"""FastAPI-Einstieg. Ingress entfernt das Pfad-Präfix, die App läuft daher unter /."""

from __future__ import annotations

import html

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse

from app import API_VERSION, __version__

api = APIRouter(prefix="/api/v1")


@api.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@api.get("/version")
async def version() -> dict:
    """Versions-Handshake für Bridge und Card (siehe Konzept, API-Vertrag)."""
    return {"app_version": __version__, "api_version": API_VERSION}


INDEX_HTML = """<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>rationPlaner</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1c1c1c; background: #fafafa; }}
  @media (prefers-color-scheme: dark) {{ body {{ color: #e6e6e6; background: #1c1c1c; }} }}
  code {{ font-size: .9em; }}
</style>
</head>
<body>
<h1>rationPlaner</h1>
<p>App v{version}, API v{api_version}. Die Web-UI folgt in Phase 1.</p>
<p>Status: <span id="status">prüfe …</span></p>
<p>Ingress-Pfad: <code>{ingress_path}</code></p>
<script>
  // Relativ, damit es hinter Ingress funktioniert.
  fetch("api/v1/health").then(r => r.json())
    .then(d => document.getElementById("status").textContent = d.status)
    .catch(() => document.getElementById("status").textContent = "nicht erreichbar");
</script>
</body>
</html>
"""


def create_app() -> FastAPI:
    app = FastAPI(title="rationPlaner", version=__version__)
    app.include_router(api)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request) -> str:
        """Platzhalterseite. Ingress entfernt das Präfix, liefert es aber als Header mit."""
        return INDEX_HTML.format(
            version=__version__,
            api_version=API_VERSION,
            ingress_path=html.escape(request.headers.get("X-Ingress-Path", "-")),
        )

    return app


app = create_app()
