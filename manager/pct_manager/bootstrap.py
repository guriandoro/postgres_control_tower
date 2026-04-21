import logging

from sqlalchemy import select

from .auth import hash_password
from .config import settings
from .db import SessionLocal
from .models import User

log = logging.getLogger(__name__)


def bootstrap_admin() -> None:
    """If env-configured and no admin exists, create one. Idempotent."""
    email = settings.bootstrap_admin_email
    password = settings.bootstrap_admin_password
    if not (email and password):
        return

    with SessionLocal() as db:
        existing_admin = db.scalar(select(User).where(User.role == "admin"))
        if existing_admin is not None:
            return
        existing_email = db.scalar(select(User).where(User.email == email))
        if existing_email is not None:
            log.warning(
                "Bootstrap skipped: a non-admin user with email %s already exists.",
                email,
            )
            return

        user = User(email=email, password_hash=hash_password(password), role="admin")
        db.add(user)
        db.commit()
        log.warning(
            "Bootstrap admin created (email=%s). Change the password and rotate "
            "PCT_BOOTSTRAP_ADMIN_PASSWORD before exposing this instance.",
            email,
        )
