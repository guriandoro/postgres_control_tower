"""SPA static-file serving.

In production the manager image bundles the built Vite app at
``$PCT_WEB_DIST_DIR`` (``web/dist`` by convention). FastAPI mounts those
assets at ``/`` and falls back to ``index.html`` for any non-API path so
React Router's history-mode URLs (``/clusters/42``) work on hard reload.

If the directory is not configured or does not exist we keep silent —
the API still works, which is exactly what we want during ``pytest``
runs and in dev when the user runs ``npm run dev`` separately.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("pct_manager.web")


def mount_spa(app: FastAPI, dist_dir: str | None) -> None:
    if not dist_dir:
        log.info("web_dist_dir not set; SPA static serving disabled.")
        return
    root = Path(dist_dir).expanduser().resolve()
    index = root / "index.html"
    if not index.is_file():
        log.warning("web_dist_dir=%s has no index.html; SPA disabled.", root)
        return

    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/", include_in_schema=False)
    def _index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_fallback(full_path: str) -> FileResponse:
        # Anything that looks like an API/health route was already matched by
        # the included routers above; if it falls through to here it's a 404.
        if (
            full_path.startswith("api/")
            or full_path in {"healthz", "openapi.json", "docs", "redoc"}
        ):
            raise HTTPException(status_code=404)
        candidate = (root / full_path).resolve()
        # Prevent escaping the dist directory via ../ traversal.
        if root in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    log.info("SPA mounted from %s", root)
