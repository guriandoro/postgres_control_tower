import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .bootstrap import bootstrap_admin
from .config import settings
from .routes import agents as agents_routes
from .routes import alerts as alerts_routes
from .routes import auth as auth_routes
from .routes import clusters as clusters_routes
from .routes import jobs as jobs_routes
from .routes import logs as logs_routes
from .routes import schedules as schedules_routes
from .scheduler import build_scheduler, run_startup_jobs
from .web import mount_spa

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pct_manager")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Postgres Control Tower manager v%s starting", __version__)
    try:
        bootstrap_admin()
    except Exception:  # noqa: BLE001
        log.exception("Bootstrap admin step failed; continuing startup.")

    run_startup_jobs()
    scheduler = build_scheduler()
    scheduler.start()
    log.info("APScheduler started with jobs: %s",
             [j.id for j in scheduler.get_jobs()])

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        log.info("Postgres Control Tower manager shutting down")


app = FastAPI(
    title="Postgres Control Tower — Manager",
    version=__version__,
    lifespan=lifespan,
)

# Allow the Vite dev server (5173) to call /api/v1/* during development.
# In prod the SPA is served from this same origin so the list is harmless.
_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(auth_routes.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(agents_routes.router, prefix="/api/v1/agents", tags=["agents"])
app.include_router(clusters_routes.router, prefix="/api/v1/clusters", tags=["clusters"])
app.include_router(jobs_routes.router, prefix="/api/v1/jobs", tags=["jobs"])
app.include_router(
    schedules_routes.router, prefix="/api/v1/schedules", tags=["schedules"]
)
app.include_router(logs_routes.router, prefix="/api/v1/logs", tags=["logs"])
app.include_router(alerts_routes.router, prefix="/api/v1/alerts", tags=["alerts"])


@app.get("/healthz", tags=["meta"])
def healthz() -> dict[str, str | bool]:
    return {"ok": True, "version": __version__}


# SPA mount must come last so its catch-all doesn't shadow API routes.
mount_spa(app, settings.web_dist_dir)
