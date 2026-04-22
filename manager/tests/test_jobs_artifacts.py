"""Integration tests for the job-artifact upload + download surface.

These tests exercise the three new endpoints introduced for
``pt_stalk_collect``:

* ``POST /api/v1/agents/jobs/{id}/artifact`` — agent multipart upload
* ``GET  /api/v1/jobs/{id}/artifacts`` — UI list
* ``GET  /api/v1/jobs/{id}/artifacts/{aid}/download`` — UI download

Auth is faked via FastAPI dependency overrides because both ``Agent``
and ``User`` lookups are otherwise expensive and orthogonal to what
we're testing here. The on-disk artifacts directory is pinned to a
``tmp_path`` so the tests don't pollute ``/var/lib/pct-manager``.

Skip semantics live in ``conftest.py`` — the whole module is skipped
when ``PCT_TEST_DATABASE_URL`` is not set.
"""
from __future__ import annotations

import io
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


@pytest.fixture()
def cluster_and_agents(db_session: Session):
    """Insert a cluster + two agents; the second is the "other" agent
    we use to verify the ownership check on uploads."""
    from pct_manager.models import Agent, Cluster

    cluster = Cluster(name="test-cluster", kind="standalone")
    db_session.add(cluster)
    db_session.flush()

    a1 = Agent(
        cluster_id=cluster.id,
        hostname="host-a",
        role="primary",
        token_hash="h" * 64,
    )
    a2 = Agent(
        cluster_id=cluster.id,
        hostname="host-b",
        role="replica",
        token_hash="i" * 64,
    )
    db_session.add_all([a1, a2])
    db_session.commit()
    return cluster, a1, a2


@pytest.fixture()
def job_for(db_session: Session):
    """Factory for a ``pct.jobs`` row in arbitrary status, owned by a
    given agent."""
    from pct_manager.models import Job

    def _make(agent_id: int, status: str = "running") -> Job:
        job = Job(
            agent_id=agent_id,
            kind="pt_stalk_collect",
            params={"run_time_seconds": 5, "iterations": 1},
            status=status,
        )
        db_session.add(job)
        db_session.commit()
        return job

    return _make


@pytest.fixture()
def patch_artifacts_dir(tmp_path, monkeypatch):
    """Pin the manager's ``artifacts_dir`` to a tmp_path so uploads
    land somewhere we can inspect + clean up automatically."""
    from pct_manager.config import settings as mgr_settings

    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    monkeypatch.setattr(mgr_settings, "artifacts_dir", str(artifacts_dir))
    return artifacts_dir


@pytest.fixture()
def auth_overrides(client: TestClient, db_session: Session, cluster_and_agents):
    """Override the auth deps so we can pretend to be a specific agent
    or any admin user without bothering with JWT/bearer tokens."""
    from pct_manager import auth as auth_module
    from pct_manager.main import app
    from pct_manager.models import User

    _, a1, _ = cluster_and_agents

    # Real admin user so require_admin / get_current_user work too.
    user = User(
        email="admin@example.com",
        password_hash="$2b$12$abcdefghijklmnopqrstuv",
        role="admin",
    )
    db_session.add(user)
    db_session.commit()

    state = {"agent_id": a1.id, "user_id": user.id}

    def _current_agent() -> object:
        from pct_manager.models import Agent

        return db_session.get(Agent, state["agent_id"])

    def _current_user() -> User:
        return db_session.get(User, state["user_id"])

    app.dependency_overrides[auth_module.get_current_agent] = _current_agent
    app.dependency_overrides[auth_module.get_current_user] = _current_user
    yield state
    app.dependency_overrides.pop(auth_module.get_current_agent, None)
    app.dependency_overrides.pop(auth_module.get_current_user, None)


def _upload(
    client: TestClient,
    job_id: int,
    *,
    body: bytes,
    filename: str = "bundle.tgz",
):
    return client.post(
        f"/api/v1/agents/jobs/{job_id}/artifact",
        files={"file": (filename, io.BytesIO(body), "application/gzip")},
        data={"filename": filename, "content_type": "application/gzip"},
    )


def test_upload_creates_row_and_file(
    client, db_session, cluster_and_agents, job_for,
    patch_artifacts_dir, auth_overrides,
):
    _, a1, _ = cluster_and_agents
    job = job_for(a1.id, status="running")

    body = b"fake tarball" * 100
    r = _upload(client, job.id, body=body)
    assert r.status_code == 201, r.text

    payload = r.json()
    assert payload["job_id"] == job.id
    assert payload["filename"] == "bundle.tgz"
    assert payload["size_bytes"] == len(body)
    assert len(payload["sha256"]) == 64

    # File ended up under <artifacts_dir>/<job_id>/.
    job_dir = patch_artifacts_dir / str(job.id)
    files = list(job_dir.iterdir())
    assert len(files) == 1
    assert files[0].read_bytes() == body


def test_upload_rejects_other_agents_job(
    client, cluster_and_agents, job_for, patch_artifacts_dir, auth_overrides,
):
    _, _a1, a2 = cluster_and_agents
    # Job belongs to the OTHER agent.
    job = job_for(a2.id, status="running")
    r = _upload(client, job.id, body=b"x")
    assert r.status_code == 403


def test_upload_rejects_pending_job(
    client, cluster_and_agents, job_for, patch_artifacts_dir, auth_overrides,
):
    _, a1, _ = cluster_and_agents
    job = job_for(a1.id, status="pending")
    r = _upload(client, job.id, body=b"x")
    assert r.status_code == 409


def test_upload_rejects_unsafe_filename(
    client, cluster_and_agents, job_for, patch_artifacts_dir, auth_overrides,
):
    _, a1, _ = cluster_and_agents
    job = job_for(a1.id, status="running")
    r = _upload(client, job.id, body=b"x", filename="../etc/passwd")
    assert r.status_code == 400


def test_upload_enforces_size_cap(
    client, cluster_and_agents, job_for, patch_artifacts_dir, auth_overrides,
    monkeypatch,
):
    from pct_manager.config import settings as mgr_settings

    monkeypatch.setattr(mgr_settings, "max_artifact_bytes", 16)
    _, a1, _ = cluster_and_agents
    job = job_for(a1.id, status="running")
    r = _upload(client, job.id, body=b"x" * 32)
    assert r.status_code == 413


def test_list_and_download(
    client, cluster_and_agents, job_for, patch_artifacts_dir, auth_overrides,
):
    _, a1, _ = cluster_and_agents
    job = job_for(a1.id, status="succeeded")
    body = b"contents-for-download"
    upload = _upload(client, job.id, body=body)
    assert upload.status_code == 201
    artifact_id = upload.json()["id"]

    listed = client.get(f"/api/v1/jobs/{job.id}/artifacts")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == artifact_id

    dl = client.get(f"/api/v1/jobs/{job.id}/artifacts/{artifact_id}/download")
    assert dl.status_code == 200
    assert dl.content == body
    assert "attachment" in dl.headers.get("content-disposition", "")
