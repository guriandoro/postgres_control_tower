"""Tiny CLI for one-off ops like creating a user. Most lifecycle management
will eventually move to the UI; this exists so the prototype is usable from
a terminal."""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from .auth import hash_password
from .db import SessionLocal
from .models import User


def _create_user(args: argparse.Namespace) -> int:
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == args.email)) is not None:
            print(f"User {args.email} already exists.", file=sys.stderr)
            return 1
        db.add(
            User(
                email=args.email,
                password_hash=hash_password(args.password),
                role=args.role,
            )
        )
        db.commit()
        print(f"Created {args.role} user: {args.email}")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="pct-manager")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_user = sub.add_parser("create-user", help="Create a UI user")
    p_user.add_argument("--email", required=True)
    p_user.add_argument("--password", required=True)
    p_user.add_argument("--role", choices=["viewer", "admin"], default="viewer")
    p_user.set_defaults(func=_create_user)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
