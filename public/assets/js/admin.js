/* Admin dashboard: session-aware login, sidebar shell, stats, paginated table. */
(function () {
  "use strict";

  var state = {
    authenticated: false,
    page: 1,
    pageSize: 10,
    device: "",
    country: "",
    pages: 1,
    total: 0
  };

  var els = {};

  function $(sel) {
    return document.querySelector(sel);
  }

  function esc(value) {
    var div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function setFormStatus(message, kind) {
    if (!els.formStatus) return;
    els.formStatus.textContent = message || "";
    els.formStatus.setAttribute("data-state", kind || "");
  }

  /* boot = checking session, login = signed out, dashboard = signed in.
     Exactly one view is visible at a time ([hidden] + !important in CSS). */
  function showView(name) {
    els.bootView.hidden = name !== "boot";
    els.loginView.hidden = name !== "login";
    els.dashboardView.hidden = name !== "dashboard";
    if (name === "login") {
      closeSidebar();
      window.setTimeout(function () {
        els.passwordInput && els.passwordInput.focus();
      }, 60);
    }
    if (name === "dashboard") {
      window.scrollTo(0, 0);
    }
  }

  /* ---------- sidebar drawer ---------- */

  function openSidebar() {
    els.sidebar.classList.add("open");
    els.overlay.hidden = false;
    els.menuBtn.setAttribute("aria-expanded", "true");
    els.menuBtn.setAttribute("aria-label", "Close navigation");
  }

  function closeSidebar() {
    els.sidebar.classList.remove("open");
    if (els.overlay) els.overlay.hidden = true;
    if (els.menuBtn) {
      els.menuBtn.setAttribute("aria-expanded", "false");
      els.menuBtn.setAttribute("aria-label", "Open navigation");
    }
  }

  function toggleSidebar() {
    if (els.sidebar.classList.contains("open")) closeSidebar();
    else openSidebar();
  }

  function setActiveSection(id) {
    var links = els.sidebar.querySelectorAll(".side-link");
    for (var i = 0; i < links.length; i += 1) {
      links[i].classList.toggle("active", links[i].getAttribute("data-section") === id);
    }
  }

  function setLoading(button, loading) {
    if (!button) return;
    if (loading) {
      button.dataset.label = button.textContent;
      button.disabled = true;
      button.innerHTML = '<span class="spinner" aria-hidden="true"></span> Working…';
    } else {
      button.disabled = false;
      if (button.dataset.label) button.textContent = button.dataset.label;
    }
  }

  /* ---------- auth ---------- */

  function handleAuthError(status, data) {
    if (status === 401) {
      setFormStatus("Invalid password.", "error");
    } else if (status === 429) {
      var retry = "";
      if (data && data.detail && data.detail.headers) retry = data.detail.headers["Retry-After"];
      setFormStatus("Too many attempts. Try again in " + (retry || "a few") + " seconds.", "error");
    } else if (status === 503) {
      var msg = (data && data.detail && data.detail.message) || "Admin login is not configured.";
      setFormStatus(msg, "error");
    } else {
      setFormStatus("Something went wrong. Please try again.", "error");
    }
  }

  function login(event) {
    event.preventDefault();
    var password = els.passwordInput.value;
    if (!password) {
      setFormStatus("Enter the admin password.", "error");
      els.passwordInput.focus();
      return;
    }
    setFormStatus("Signing in…", "success");
    setLoading(els.loginBtn, true);
    fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: password })
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { status: res.status, data: data, ok: res.ok };
        });
      })
      .then(function (result) {
        setLoading(els.loginBtn, false);
        if (result.ok) {
          els.passwordInput.value = "";
          setFormStatus("", "");
          state.authenticated = true;
          showView("dashboard");
          setActiveSection("overview");
          refreshAll();
        } else {
          handleAuthError(result.status, result.data);
        }
      })
      .catch(function () {
        setLoading(els.loginBtn, false);
        setFormStatus("Network error. Please try again.", "error");
      });
  }

  function logout() {
    fetch("/api/auth/logout", { method: "POST" })
      .catch(function () { /* best effort; cookie cleared on next load */ })
      .finally(function () {
        state.authenticated = false;
        showView("login");
        setFormStatus("Signed out.", "success");
      });
  }

  /* ---------- stats ---------- */

  function renderStatCards(stats) {
    els.statTotal.textContent = stats.total_visits;
    els.statToday.textContent = stats.visits_today;
    els.statWeek.textContent = stats.visits_7d;
    els.statCountries.textContent = Object.keys(stats.countries || {}).length;
    els.retentionNote.textContent = "Retention: " + stats.retention_days + " days · Updated: " +
      (stats.updated_at || "never");
  }

  function renderBars(container, data) {
    container.innerHTML = "";
    var entries = Object.entries(data || {});
    if (!entries.length) {
      var empty = document.createElement("p");
      empty.textContent = "No data yet.";
      empty.style.color = "var(--text-2)";
      container.appendChild(empty);
      return;
    }
    var max = entries.reduce(function (acc, entry) { return Math.max(acc, entry[1]); }, 1);
    entries.forEach(function (entry) {
      var row = document.createElement("li");
      row.className = "bar-row";
      var pct = Math.round((entry[1] / max) * 100);
      row.innerHTML =
        '<span class="bar-label">' + esc(entry[0]) + "</span>" +
        '<span class="bar-count">' + entry[1] + "</span>" +
        '<span class="bar-track"><span class="bar-fill" style="width:' + pct + '%"></span></span>';
      container.appendChild(row);
    });
  }

  function renderStats(stats) {
    renderStatCards(stats);
    renderBars(els.deviceBars, stats.devices);
    renderBars(els.browserBars, stats.browsers);
    renderBars(els.countryBars, stats.countries);
  }

  /* ---------- table ---------- */

  function deviceIcon(device) {
    if (device === "mobile") return "📱";
    if (device === "tablet") return "▭";
    if (device === "desktop") return "🖥";
    return "❔";
  }

  function formatTs(ts) {
    if (!ts) return "—";
    var d = new Date(ts);
    if (isNaN(d.getTime())) return esc(ts);
    return d.toISOString().replace("T", " ").slice(0, 19) + "Z";
  }

  function renderTable(payload) {
    state.page = payload.page;
    state.pages = payload.pages;
    state.total = payload.total;
    var rows = "";
    (payload.items || []).forEach(function (item) {
      var loc = [item.city, item.country].filter(Boolean).join(", ") || "—";
      rows +=
        "<tr>" +
        "<td>" + formatTs(item.ts) + "</td>" +
        '<td><span class="device-pill">' + deviceIcon(item.device) + " " + esc(item.device || "unknown") + "</span></td>" +
        "<td>" + esc(item.browser || "—") + "</td>" +
        "<td>" + esc(item.os || "—") + "</td>" +
        "<td>" + esc(loc) + "</td>" +
        '<td class="muted">' + esc(item.ip || "—") + "</td>" +
        '<td class="muted">' + esc(item.referrer || "—") + "</td>" +
        '<td><button class="btn btn-ghost btn-sm" data-delete="' + esc(item.id) + '">Delete</button></td>' +
        "</tr>";
    });
    els.tbody.innerHTML = rows;
    var empty = payload.total === 0;
    els.emptyState.hidden = !empty;
    els.errorState.hidden = true;
    els.tableScroll.hidden = empty;
    els.pageInfo.textContent = empty
      ? "No visits recorded yet."
      : "Page " + payload.page + " of " + payload.pages + " · " + payload.total + " visits";
    els.prevPage.disabled = payload.page <= 1;
    els.nextPage.disabled = payload.page >= payload.pages;
  }

  function renderError(message) {
    els.tableScroll.hidden = true;
    els.emptyState.hidden = true;
    els.errorState.hidden = false;
    els.errorMessage.textContent = message;
    els.pageInfo.textContent = "—";
  }

  function expiredSession() {
    state.authenticated = false;
    showView("login");
    setFormStatus("Session expired. Please sign in again.", "error");
  }

  function loadVisitors() {
    els.tableState.hidden = false;
    els.tableState.innerHTML = "";
    for (var i = 0; i < 5; i += 1) {
      var sk = document.createElement("div");
      sk.className = "skeleton-row";
      els.tableState.appendChild(sk);
    }
    els.tableScroll.hidden = true;
    els.emptyState.hidden = true;
    els.errorState.hidden = true;

    var params = new URLSearchParams({
      page: String(state.page),
      page_size: String(state.pageSize)
    });
    if (state.device) params.set("device", state.device);
    if (state.country) params.set("country", state.country);

    return fetch("/api/admin/visitors?" + params.toString())
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
      })
      .then(function (result) {
        els.tableState.hidden = true;
        if (result.ok) {
          renderTable(result.data);
        } else if (result.status === 401) {
          expiredSession();
        } else {
          var msg = (result.data && result.data.detail && result.data.detail.message) ||
            "Could not load visitors.";
          renderError(msg);
        }
      })
      .catch(function () {
        els.tableState.hidden = true;
        renderError("Network error while loading visitors.");
      });
  }

  function loadStats() {
    return fetch("/api/admin/stats")
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; });
      })
      .then(function (result) {
        if (result.ok) {
          renderStats(result.data);
        } else if (result.status === 401) {
          expiredSession();
        } else {
          var msg = (result.data && result.data.detail && result.data.detail.message) ||
            "Could not load stats.";
          els.retentionNote.textContent = msg;
        }
      })
      .catch(function () {
        els.retentionNote.textContent = "Network error while loading stats.";
      });
  }

  function refreshAll() {
    els.refreshBtn && setLoading(els.refreshBtn, true);
    Promise.all([loadStats(), loadVisitors()]).finally(function () {
      els.refreshBtn && setLoading(els.refreshBtn, false);
    });
  }

  function deleteVisit(id, button) {
    setLoading(button, true);
    fetch("/api/admin/visitors/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function (res) { return { ok: res.ok }; })
      .then(function (result) {
        if (result.ok) {
          loadVisitors();
          loadStats();
        } else {
          setLoading(button, false);
          renderError("Could not delete that visit.");
        }
      })
      .catch(function () {
        setLoading(button, false);
        renderError("Network error while deleting.");
      });
  }

  function checkSession() {
    return fetch("/api/auth/session")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        state.authenticated = !!(data && data.authenticated);
        if (state.authenticated) {
          showView("dashboard");
          setActiveSection("overview");
          refreshAll();
        } else {
          showView("login");
          if (!data || !data.auth_configured) {
            setFormStatus("Admin login is not configured on this deployment.", "error");
          }
        }
      })
      .catch(function () {
        showView("login");
        setFormStatus("Could not reach the server.", "error");
      });
  }

  function init() {
    els.bootView = $("#boot-view");
    els.loginView = $("#login-view");
    els.dashboardView = $("#dashboard-view");
    els.loginForm = $("#login-form");
    els.passwordInput = $("#admin-password");
    els.loginBtn = $("#login-btn");
    els.logoutBtn = $("#logout-btn");
    els.refreshBtn = $("#refresh-btn");
    els.formStatus = $("#form-status");
    els.statTotal = $("#stat-total");
    els.statToday = $("#stat-today");
    els.statWeek = $("#stat-week");
    els.statCountries = $("#stat-countries");
    els.retentionNote = $("#retention-note");
    els.deviceBars = $("#device-bars");
    els.browserBars = $("#browser-bars");
    els.countryBars = $("#country-bars");
    els.tbody = $("#visitor-rows");
    els.tableScroll = $("#table-scroll");
    els.tableState = $("#table-state");
    els.emptyState = $("#empty-state");
    els.errorState = $("#error-state");
    els.errorMessage = $("#error-message");
    els.pageInfo = $("#page-info");
    els.prevPage = $("#prev-page");
    els.nextPage = $("#next-page");
    els.deviceFilter = $("#filter-device");
    els.countryFilter = $("#filter-country");
    els.pageSizeSel = $("#page-size");
    els.retryBtn = $("#retry-btn");
    els.sidebar = $("#sidebar");
    els.overlay = $("#sidebar-overlay");
    els.menuBtn = $("#menu-btn");

    els.loginForm.addEventListener("submit", login);
    els.logoutBtn.addEventListener("click", logout);
    els.refreshBtn.addEventListener("click", refreshAll);
    els.retryBtn.addEventListener("click", refreshAll);
    els.menuBtn.addEventListener("click", toggleSidebar);
    els.overlay.addEventListener("click", closeSidebar);
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeSidebar();
    });

    var sideLinks = els.sidebar.querySelectorAll(".side-link");
    for (var i = 0; i < sideLinks.length; i += 1) {
      sideLinks[i].addEventListener("click", function (event) {
        setActiveSection(event.currentTarget.getAttribute("data-section"));
        closeSidebar();
      });
    }

    els.prevPage.addEventListener("click", function () {
      if (state.page > 1) {
        state.page -= 1;
        loadVisitors();
      }
    });
    els.nextPage.addEventListener("click", function () {
      if (state.page < state.pages) {
        state.page += 1;
        loadVisitors();
      }
    });
    els.deviceFilter.addEventListener("change", function () {
      state.device = els.deviceFilter.value;
      state.page = 1;
      loadVisitors();
    });
    els.countryFilter.addEventListener("change", function () {
      state.country = els.countryFilter.value.trim().toUpperCase();
      state.page = 1;
      loadVisitors();
    });
    els.pageSizeSel.addEventListener("change", function () {
      state.pageSize = parseInt(els.pageSizeSel.value, 10) || 10;
      state.page = 1;
      loadVisitors();
    });
    els.tbody.addEventListener("click", function (event) {
      var btn = event.target.closest("[data-delete]");
      if (!btn) return;
      if (!window.confirm("Delete this visit record?")) return;
      deleteVisit(btn.getAttribute("data-delete"), btn);
    });

    showView("boot");
    checkSession();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
