"""HTML page routes: the public surprise page and the admin entry point."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, HTMLResponse

router = APIRouter(tags=["pages"])

# project_root/public — bundled with the function on Vercel (see .vercelignore).
PUBLIC_DIR = Path(__file__).resolve().parents[2] / "public"


def _page_response(filename: str) -> HTMLResponse:
    path = PUBLIC_DIR / filename
    if not path.is_file():
        return HTMLResponse(
            "<!doctype html><html><body><h1>Page missing</h1>"
            f"<p>Expected bundled file <code>{filename}</code>.</p></body></html>",
            status_code=500,
        )
    return HTMLResponse(
        path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/", include_in_schema=False)
async def home(request: Request) -> HTMLResponse:
    return _page_response("index.html")


@router.get("/admin-private", include_in_schema=False)
async def admin_page(request: Request) -> HTMLResponse:
    return _page_response("admin.html")


@router.get("/favicon.svg", include_in_schema=False)
@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    path = PUBLIC_DIR / "favicon.svg"
    if path.is_file():
        return FileResponse(path, media_type="image/svg+xml")
    return HTMLResponse(status_code=204)
