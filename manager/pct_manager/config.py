from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Manager runtime configuration. All env vars are prefixed with ``PCT_``."""

    model_config = SettingsConfigDict(
        env_prefix="PCT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+psycopg://pct:pct@localhost:5432/pct",
        description="SQLAlchemy URL for the manager's Postgres.",
    )

    jwt_secret: str = Field(
        default="change-me-to-a-long-random-string",
        description="HS256 signing secret for UI session JWTs.",
    )
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    enrollment_token: str = Field(
        default="change-me-enrollment-token",
        description=(
            "Pre-shared secret an agent must present to /api/v1/agents/register "
            "before it is issued its own per-agent bearer token."
        ),
    )

    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None

    log_retention_days: int = 14

    web_dist_dir: str | None = Field(
        default=None,
        description=(
            "Filesystem path to the built Vite app (web/dist). When set, "
            "the manager serves it as a SPA at '/'. Leave unset in tests."
        ),
    )
    cors_allow_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description=(
            "Comma-separated origins allowed by CORS. Used during dev when "
            "the Vite server (5173) talks to the manager (8080). In prod "
            "the SPA is served from the same origin so this is ignored."
        ),
    )

    # --- Alerting (P7) ---
    # Rule engine cadence in seconds. The thresholds (60s WAL lag warn /
    # 5m crit, 2s clock skew, 3 transitions / 10m for flapping) are
    # deliberately hardcoded in alerter/rules.py — operators tune them
    # by editing code, not by twiddling env vars (PLAN §6).
    alert_eval_interval: int = 60
    # Re-notify the same OPEN alert every N seconds at most. 6h by
    # default; ack the alert in the UI to silence sooner.
    alert_renotify_seconds: int = 6 * 3600
    # Backup schedules tick — how often to scan ``pct.backup_schedules``
    # for due rows. 60s matches the cron resolution; lowering it doesn't
    # buy precision because cron itself is minute-grained.
    schedule_tick_interval: int = 60
    # Storage runway forecast cadence and lookback window.
    forecast_interval_seconds: int = 300
    forecast_window_days: int = 7
    # Optional per-fleet bytes cap used as the runway "full" line. Set
    # to 0 to disable days-to-target estimation; per-cluster overrides
    # land in pct.storage_forecast.target_bytes (v2 feature).
    forecast_target_bytes: int = 0

    # Slack incoming webhook. Empty disables the Slack notifier.
    slack_webhook_url: str = ""

    # --- Job artifacts (pt-stalk bundles, future diagnostic uploads) ---
    # Filesystem directory where the manager stores binary artifacts
    # uploaded by agents (e.g. pt-stalk tarballs). Files land under
    # ``<artifacts_dir>/<job_id>/<artifact_id>-<filename>``. The path is
    # created on first upload; in production, mount a dedicated volume
    # here so artifacts survive container rebuilds.
    artifacts_dir: str = "/var/lib/pct-manager/artifacts"
    # Hard ceiling for any single artifact upload, in bytes. The agent
    # will refuse to upload anything larger; the route enforces the same
    # cap server-side. 200 MiB is enough for a 30-iteration pt-stalk run
    # on a busy DB; bump it if you collect hour-long snapshots.
    max_artifact_bytes: int = 200 * 1024 * 1024

    # SMTP — empty smtp_host disables the SMTP notifier.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from: str = "pct@localhost"
    # Comma-separated list of recipient addresses.
    smtp_to: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
