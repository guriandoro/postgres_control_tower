"""Pytest fixtures for the manager.

The manager's models lean on Postgres-specific types (JSONB), so the
unit suite needs a real Postgres. Set ``PCT_TEST_DATABASE_URL`` to a
throwaway database before running these tests; otherwise everything
in ``manager/tests`` is skipped.

Example::

    PCT_TEST_DATABASE_URL=postgresql+psycopg://pct:pct@localhost:5432/pct_test \
        pytest manager/tests
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

_TEST_DSN = os.environ.get("PCT_TEST_DATABASE_URL", "")

if not _TEST_DSN:
    pytest.skip(
        "PCT_TEST_DATABASE_URL not set — manager integration tests skipped.",
        allow_module_level=True,
    )

# Point the manager at the test DB *before* importing it so
# ``settings.database_url`` and the module-level engine pick it up.
os.environ["PCT_DATABASE_URL"] = _TEST_DSN
os.environ.setdefault("PCT_JWT_SECRET", "test-jwt-secret-do-not-use-in-prod")
os.environ.setdefault("PCT_BOOTSTRAP_ADMIN_EMAIL", "")
os.environ.setdefault("PCT_BOOTSTRAP_ADMIN_PASSWORD", "")


@pytest.fixture(scope="session")
def engine():
    """Engine for the test DB. The schema is created via Alembic once
    per session so the ``pct.job_artifacts`` table (added in 0008)
    actually exists."""
    eng = create_engine(_TEST_DSN, future=True)
    # Run alembic upgrade head — same path the manager uses at startup.
    from alembic import command
    from alembic.config import Config as AlembicConfig

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = AlembicConfig(os.path.join(repo_root, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", _TEST_DSN)
    cfg.set_main_option("script_location", os.path.join(repo_root, "alembic"))
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture()
def db_session(engine) -> Iterator[Session]:
    """Per-test session with all pct/logs tables truncated up front."""
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    with SessionLocal() as s:
        # Wipe tables we touch. Order matters for FKs; CASCADE keeps it terse.
        s.execute(
            text(
                "TRUNCATE TABLE pct.job_artifacts, pct.jobs, "
                "pct.agents, pct.clusters RESTART IDENTITY CASCADE"
            )
        )
        s.commit()
        yield s


@pytest.fixture()
def client(db_session) -> Iterator[TestClient]:
    """FastAPI TestClient wired to the same DB session for hand-off."""
    from pct_manager.db import get_db
    from pct_manager.main import app

    def _override_get_db() -> Iterator[Session]:
        # Re-use the test session so the test can introspect changes.
        # We don't close it here — the db_session fixture owns lifecycle.
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)
