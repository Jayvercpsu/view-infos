# view-info — Surprise site with privacy-conscious visitor analytics

A production-ready, responsive “SURPRISE! 🎉” landing page plus a password-protected
admin dashboard, written in **Python / FastAPI**, storage backed by **JSONBin.io**,
and deployable on **Vercel**.

- **Public page** `/` — animated surprise page (confetti, glassmorphism, gradients),
  fully responsive (Android, iPhone, tablet, desktop), honors `prefers-reduced-motion`.
- **Admin page** `/admin-private` — boots into a session check, shows the
  password-only login card, and on success swaps to a dashboard with a sidebar
  (Overview / Visitors nav, sign-out), protected analytics dashboard
  (totals, device/browser/location breakdowns, paginated visitor table, refresh,
  loading / empty / error states, logout).
- **Storage** — JSONBin.io via a dedicated service module with timeouts, bounded
  retries, validation, and safe error mapping.

---

## 1. Project structure

```
view-info/
├── main.py                  # Vercel + uvicorn entrypoint (exports `app`)
├── app/
│   ├── main.py              # FastAPI app factory, error handlers, static mount
│   ├── config.py            # Settings from env (server-side only)
│   ├── security.py          # bcrypt hashing, signed sessions, rate limiter, CSRF origin check
│   ├── deps.py              # DI wiring: settings, session manager, store, admin auth
│   ├── errors.py            # Domain error → safe HTTP mapping
│   ├── routers/
│   │   ├── pages.py         # / , /admin-private , /favicon.svg
│   │   ├── auth.py          # /api/auth/{login,logout,session}
│   │   └── visitors.py      # /api/track* (public) and /api/admin/* (protected)
│   └── services/
│       ├── jsonbin.py       # JSONBin.io v3 client (timeouts, retries, no secret leakage)
│       ├── analytics.py     # UA parsing, trusted-proxy IP/geo extraction, record building
│       └── visitor_store.py # CRUD + aggregation + retention pruning
├── public/                  # Static frontend (CDN-served on Vercel, bundled for the function)
│   ├── index.html           # Surprise page + privacy notice
│   ├── admin.html           # Admin login + dashboard
│   ├── favicon.svg
│   └── assets/{css,js}/
├── tests/                   # pytest suite (auth, authz, tracking, JSONBin, analytics)
├── requirements.txt         # Runtime deps (Vercel installs these)
├── pyproject.toml           # Project metadata + [tool.vercel] entrypoint
├── vercel.json              # Framework + security headers
├── .env.example             # Documented environment variables (safe to commit)
├── .gitignore / .vercelignore
└── .python-version          # 3.12 (Vercel default)
```

Business logic lives in `app/services/`; routes only parse/validate and delegate.

---

## 2. Local development

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install pytest               # or: pip install -e ".[dev]"

cp .env.example .env             # then edit values (never commit .env)
uvicorn main:app --reload
# open http://127.0.0.1:8000/ and http://127.0.0.1:8000/admin-private
```

`.env` is loaded automatically (python-dotenv) when present. On Vercel, environment
variables come from the project settings — no `.env` file is needed.

### Run the tests

```bash
python -m pytest tests/ -q
```

The suite covers: password hashing, login success/failure, generic errors, rate
limiting, session cookies, logout, authorization on **every** admin endpoint,
tampered cookies, visitor collection (trusted headers, anonymization, opt-in GPS,
opt-out deletion), pagination/filters/stats, retention pruning, missing-credential
error handling, and the JSONBin client (mocked transport: auth errors, retries,
timeouts, malformed payloads, secret-leak prevention).

---

## 3. Create the JSONBin

1. Create a free account at <https://jsonbin.io> and open **API Keys** → copy your
   **X-Master-Key**.
2. Create a bin (either way):

   **Dashboard:** Create Bin → paste the initial document → Save:

   ```json
   { "schema_version": 1, "updated_at": null, "visitors": [] }
   ```

   **API:**

   ```bash
   curl -X POST "https://api.jsonbin.io/v3/b" \
     -H "Content-Type: application/json" \
     -H "X-Master-Key: YOUR_MASTER_KEY" \
     -d '{"schema_version":1,"updated_at":null,"visitors":[]}'
   ```

   The response's `metadata.id` is your `JSONBIN_BIN_ID`. Keep the bin **private**.

> ⚠️ **JSONBin limitations (by design of this provider):**
> - **No transactions/locks.** Writes are read-modify-write of the whole document;
>   concurrent serverless invocations can race and lose an update. This app keeps
>   the window small (read → append → write, capped record count, retention trim),
>   but for serious traffic move to a real datastore (Postgres, Upstash, Vercel KV…).
> - **Rate limits** are per account/plan (HTTP 429 is mapped to a safe 503 response
>   with bounded retries).
> - **Whole-document writes** — the retention window (`VISITOR_RETENTION_DAYS`)
>   and a 5 000-record cap keep the document small.

---

## 4. Environment variables (exact list for Vercel)

| Variable | Required | Purpose |
|---|---|---|
| `ADMIN_PASSWORD_HASH` | ✅ (prod) | SHA-256-prehashed **bcrypt** hash of the admin password. |
| `ADMIN_PASSWORD` | optional | Dev-only convenience; used only when the hash is unset. |
| `SESSION_SECRET` | ✅ (prod) | Long random string signing session cookies (32+ chars). |
| `JSONBIN_BIN_ID` | ✅ for analytics | The bin id from JSONBin. |
| `JSONBIN_MASTER_KEY` | ✅ for analytics | Your JSONBin X-Master-Key. |
| `JSONBIN_ACCESS_KEY` | optional | Scoped access key, if you prefer one over the master key. |
| `ENV` | ✅ (prod: `production`) | Enables production hardening (docs off, cookie policy). |
| `COOKIE_SECURE` | optional | `auto` (default; Secure on HTTPS/Vercel), `true`, `false`. |
| `SESSION_TTL_HOURS` | optional | Admin session lifetime (default `8`). |
| `VISITOR_RETENTION_DAYS` | optional | Auto-prune visits older than N days (default `90`). |
| `ANONYMIZE_IP` | optional | `true` stores IPs masked as `a.b.c.0/24` (default `false`). |
| `LOGIN_RATE_LIMIT_ATTEMPTS` / `LOGIN_RATE_LIMIT_WINDOW_SECONDS` | optional | Default `5` / `300`. |
| `TRACK_RATE_LIMIT_ATTEMPTS` / `TRACK_RATE_LIMIT_WINDOW_SECONDS` | optional | Default `30` / `60`. |
| `JSONBIN_TIMEOUT_SECONDS` | optional | Default `10`. |
| `EXTRA_ALLOWED_ORIGINS` | optional | Extra origins allowed for state-changing requests. |

Generate the values:

```bash
# Password hash (SHA-256 pre-hash + bcrypt cost 12) — matches .env.example sample for admin111322?
python -c "import bcrypt,hashlib;print(bcrypt.hashpw(hashlib.sha256(b'YOUR-PASSWORD').digest(),bcrypt.gensalt(12)).decode())"

# Session secret
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

> Note: paste `ADMIN_PASSWORD_HASH` **literally** in the Vercel dashboard (it contains
> `$` characters — the dashboard does not expand them). `.env.example` already contains
> a working hash for the intended demo password `admin111322?`.

Secrets are **never** sent to the browser, embedded in HTML/JS, returned by any API,
or logged. `/api/health` only reports *whether* storage is configured.

---

## 5. Deploy to Vercel

Verified against the current Vercel Python runtime (framework presets, 2026):
Vercel detects FastAPI from the dependencies, loads `main:app` (declared in
`pyproject.toml` → `[tool.vercel] entrypoint`), and serves the whole app as one
function; files in `public/` are served from the CDN. Python 3.12 is pinned via
`.python-version`.

### Option A — Vercel CLI

```bash
npm i -g vercel
vercel login
vercel                      # first deploy (preview)
# Add env vars:
vercel env add ADMIN_PASSWORD_HASH
vercel env add SESSION_SECRET
vercel env add JSONBIN_BIN_ID
vercel env add JSONBIN_MASTER_KEY
vercel env add ENV production
# Promote:
vercel --prod
```

### Option B — Git integration

1. Push this folder to GitHub/GitLab/Bitbucket (only this folder as the repo root).
2. On <https://vercel.com/new> → **Import** the repo. Vercel auto-detects **FastAPI**.
3. **Project → Settings → Environment Variables** → add every variable from §4
   (Production *and* Preview).
4. Deploy. Subsequent pushes redeploy automatically.

### Verify the deployment

```bash
curl https://YOUR-DOMAIN/api/health          # jsonbin_configured: true
curl https://YOUR-DOMAIN/                    # SURPRISE page
curl -i https://YOUR-DOMAIN/api/admin/stats  # 401 without a session cookie
```

Then log in at `/admin-private`, open the public page once, and confirm the visit
appears in the dashboard.

---

## 6. How the security works

- **Password auth only** — bcrypt (SHA-256 pre-hash, cost 12) against
  `ADMIN_PASSWORD_HASH`; generic “Invalid password.” on failure; constant-time-ish
  verification on both success and failure paths.
- **Sessions** — stateless signed tokens (`itsdangerous`) in an **HttpOnly**,
  **SameSite=Lax**, **Secure-in-production** cookie. Serverless platforms have no
  shared durable memory, so sessions are signed rather than stored; a stolen cookie
  is valid until TTL, logout, or `SESSION_SECRET` rotation (documented trade-off).
  A **password fingerprint is embedded in every token**, so changing
  `ADMIN_PASSWORD_HASH` immediately invalidates all previously issued sessions.
  `/api/auth/logout` clears the cookie.
- **Rate limiting** — in-memory sliding window per client IP on `/api/auth/login`
  (and a lighter one on `/api/track`). A successful login clears that client's
  counter so real users never inherit a stale lockout. On serverless this is
  *per warm instance*; it blunts bursts but is not a global limit — a shared store
  (e.g. Upstash Redis) would be needed for a hard global cap.
- **CSRF** — SameSite=Lax cookies plus an Origin check on every state-changing
  endpoint.
- **Authorization** — every `/api/admin/*` route requires a valid session
  (`require_admin` dependency); the visitor-facing tracking endpoints never accept
  client-supplied IPs.
- **Trusted proxy headers (Vercel)** — client IP is taken only from `x-real-ip`
  and location only from `x-vercel-ip-country` / `-country-region` / `-city`, all of
  which Vercel's proxy **overwrites** per request (documented Vercel behavior).
  `X-Forwarded-For` is deliberately **not** trusted. Outside Vercel
  (`TRUST_PROXY_HEADERS` off by default) the socket peer address is used instead.
- **Headers** — CSP, HSTS, `X-Frame-Options: DENY`, `nosniff`, Referrer-Policy,
  Permissions-Policy (set in `vercel.json` for production, and applied by an
  ASGI middleware in `app/main.py` so local/dev/proxy deployments match).
  `/admin-private`, `/api/admin/*`, and `/api/auth/*` also send
  `Cache-Control: no-store` and `X-Robots-Tag: noindex` so session-adjacent
  responses are never cached or indexed.

## 7. Privacy model

- The public page shows a **privacy notice** describing exactly what is collected
  and why: UTC timestamp, IP address, approximate (IP-based) country/city, browser,
  OS, device category, and referrer.
- **No GPS** unless the visitor explicitly opts in through the browser's
  `navigator.geolocation` permission prompt (coordinates are rounded to 3 decimals).
- **Opt-out:** none on the public page by design (the earlier “Delete my visit
  record” control was removed); visits are deletable by the admin from the
  dashboard table, or by deleting the whole JSONBin.
- **Retention:** records older than `VISITOR_RETENTION_DAYS` (default 90) are
  trimmed automatically on every write; an admin purge endpoint exists too.
- No contacts, SMS, photos, microphone/camera, or any other personal data is ever
  accessed. A `Permissions-Policy` header blocks those powerful features anyway.

---

## 8. API summary

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | public | Surprise page |
| GET | `/admin-private` | public | Admin login/dashboard page |
| GET | `/api/health` | public | Config status (no secrets) |
| POST | `/api/track` | public | Record a visit → `{visit_id}` |
| POST | `/api/track/geo` | visit id | Attach opt-in precise location |
| POST | `/api/track/delete` | visit id | Opt-out: delete own visit |
| POST | `/api/auth/login` | — | Password login → session cookie |
| POST | `/api/auth/logout` | cookie | Clear session |
| GET | `/api/auth/session` | cookie | `{authenticated, auth_configured}` |
| GET | `/api/admin/stats` | ✅ admin | Aggregates |
| GET | `/api/admin/visitors` | ✅ admin | Paginated list (`page`, `page_size`, `device`, `country`) |
| DELETE | `/api/admin/visitors/{id}` | ✅ admin | Delete one visit |
| POST | `/api/admin/visitors/purge` | ✅ admin | Purge records older than `?days=` |

---

## 9. Honest notes / unverified assumptions

- **Not deployed from here.** The build (imports, dependency resolution, tests) and
  all routing behavior are verified locally, and the structure matches Vercel's
  documented Python-runtime conventions (framework preset, `main:app` entrypoint,
  `public/` static assets, `requirements.txt`/`pyproject.toml` deps, `.python-version`).
  An actual `vercel --prod` deployment from your account has **not** been run in
  this environment — confirm with the verification curls in §5 after deploying.
- **JSONBin concurrency** — lost updates under concurrent writes are possible by
  design of the provider (see §3). Fine for low/medium traffic; migrate to a real
  datastore if visits matter at scale.
- **Geo headers** — Vercel documents `x-vercel-ip-*` as available on all plans, but
  values can be coarse/absent depending on the client network; the dashboard shows
  them as “approximate” for that reason.
- **Rate limiting** is per-instance on serverless (documented above).
- **Sessions are stateless** (signed, not server-stored) — rotate `SESSION_SECRET`
  to force-logout everyone.
# view-infos
