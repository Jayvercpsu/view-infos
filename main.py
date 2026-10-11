"""Vercel + local entrypoint.

Vercel's Python runtime looks for a top-level `app` (or `application` /
`handler`) in app.py, index.py, server.py, main.py, wsgi.py, asgi.py at the
project root (or under src/, app/, api/). pyproject.toml also pins this via
[tool.vercel] entrypoint = "main:app".
"""

from app.main import create_app

app = create_app()

__all__ = ["app"]
