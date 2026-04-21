"""Agent CLI.

Subcommands (P1):
    pct-agent register   one-shot: enroll with manager, persist agent token
    pct-agent run        start the agent process (HTTP server + collectors)

P1 ships only ``register`` end-to-end. ``run`` is a placeholder that boots
the local diagnostic FastAPI on 127.0.0.1; collectors land in P3/P4.
"""

from __future__ import annotations

import argparse
import socket
import sys

import httpx
import uvicorn

from . import __version__
from .config import AgentState, load_settings


def _cmd_register(args: argparse.Namespace) -> int:
    settings = load_settings()

    enrollment_token = args.enrollment_token or settings.enrollment_token
    cluster_name = args.cluster_name or settings.cluster_name
    cluster_kind = args.cluster_kind or settings.cluster_kind
    hostname = args.hostname or settings.hostname or socket.gethostname()
    manager_url = args.manager_url or settings.manager_url

    if not enrollment_token or not cluster_name:
        print(
            "register requires --enrollment-token and --cluster-name "
            "(or PCT_AGENT_ENROLLMENT_TOKEN / PCT_AGENT_CLUSTER_NAME).",
            file=sys.stderr,
        )
        return 2

    payload = {
        "enrollment_token": enrollment_token,
        "cluster_name": cluster_name,
        "cluster_kind": cluster_kind,
        "hostname": hostname,
    }
    url = f"{manager_url.rstrip('/')}/api/v1/agents/register"
    print(f"Registering with {url} as host={hostname} cluster={cluster_name}...")

    resp = httpx.post(url, json=payload, timeout=30.0)
    if resp.status_code != 201:
        print(f"Registration failed: HTTP {resp.status_code} {resp.text}", file=sys.stderr)
        return 1

    body = resp.json()
    state = AgentState(settings.state_path)
    state.save(
        {
            "manager_url": manager_url,
            "agent_id": body["agent_id"],
            "cluster_id": body["cluster_id"],
            "agent_token": body["agent_token"],
            "hostname": hostname,
        }
    )
    print(f"Registered. agent_id={body['agent_id']}; token persisted to {settings.state_path}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from .main import app

    settings = load_settings()
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="info",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="pct-agent")
    parser.add_argument("--version", action="version", version=f"pct-agent {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="Enroll with the manager")
    p_reg.add_argument("--manager-url")
    p_reg.add_argument("--enrollment-token")
    p_reg.add_argument("--cluster-name")
    p_reg.add_argument("--cluster-kind", choices=["standalone", "patroni"])
    p_reg.add_argument("--hostname")
    p_reg.set_defaults(func=_cmd_register)

    p_run = sub.add_parser("run", help="Run the agent process")
    p_run.set_defaults(func=_cmd_run)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
