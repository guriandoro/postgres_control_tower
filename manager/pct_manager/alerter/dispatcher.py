"""Glue between rules, alert rows, and notifiers.

One :func:`evaluate_rules` call:

  1. Runs every rule in ``rules.ALL_RULES``.
  2. For each hit, opens or updates a matching ``pct.alerts`` row keyed
     on ``(kind, cluster_id, dedup_key)`` (only **one open alert per
     key at a time** — the engine never duplicates).
  3. For any open alert whose key is no longer present in the latest
     hit set, marks it ``resolved_at = now()``.
  4. Notifies on three occasions: ``opened`` (first time the alert
     fires), ``renotify`` (after ``alert_renotify_seconds`` has passed
     since ``last_notified_at`` AND the alert is still open AND not
     acknowledged), ``resolved`` (when an open alert is closed).

This function is sync — it's invoked from APScheduler's worker thread,
which doesn't share the FastAPI event loop. ``Notifier.send`` is also
sync for the same reason.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import Alert
from .notifiers import Notifier, Occasion, build_notifiers
from .rules import ALL_RULES, RuleHit

log = logging.getLogger("pct_manager.alerter.dispatcher")


def evaluate_rules() -> dict[str, int]:
    """Run all rules once. Returns {opened, updated, resolved, notified}.

    Safe to call manually for ad-hoc evaluation; APScheduler also calls
    it on the configured ``alert_eval_interval``.
    """
    counters = {"opened": 0, "updated": 0, "resolved": 0, "notified": 0}
    notifiers = build_notifiers(settings)

    with SessionLocal() as db:
        hits: list[RuleHit] = []
        for rule_fn in ALL_RULES:
            try:
                hits.extend(rule_fn(db))
            except Exception:  # noqa: BLE001
                log.exception("Rule %s crashed; continuing", rule_fn.__name__)

        hits_by_key = {(h.kind, h.cluster_id, h.dedup_key): h for h in hits}

        # 1. Open or update alerts matching current hits.
        for key, hit in hits_by_key.items():
            kind, cluster_id, dedup_key = key
            existing = db.scalar(
                select(Alert).where(
                    Alert.kind == kind,
                    Alert.cluster_id == cluster_id,
                    Alert.dedup_key == dedup_key,
                    Alert.resolved_at.is_(None),
                )
            )
            if existing is None:
                alert = Alert(
                    kind=kind,
                    severity=hit.severity,
                    cluster_id=cluster_id,
                    dedup_key=dedup_key,
                    payload=hit.payload,
                )
                db.add(alert)
                db.flush()  # so we get .id for the notify call
                counters["opened"] += 1
                _notify(notifiers, alert, "opened", db)
                counters["notified"] += 1
            else:
                # Merge new payload + bump severity if escalated.
                existing.payload = {**existing.payload, **hit.payload}
                if _severity_rank(hit.severity) > _severity_rank(existing.severity):
                    existing.severity = hit.severity
                counters["updated"] += 1

                if _should_renotify(existing):
                    _notify(notifiers, existing, "renotify", db)
                    counters["notified"] += 1

        # 2. Resolve any open alerts no longer present in the hit set.
        open_alerts = db.scalars(
            select(Alert).where(Alert.resolved_at.is_(None))
        ).all()
        now = datetime.now(timezone.utc)
        for alert in open_alerts:
            key = (alert.kind, alert.cluster_id, alert.dedup_key)
            if key in hits_by_key:
                continue
            alert.resolved_at = now
            counters["resolved"] += 1
            _notify(notifiers, alert, "resolved", db)
            counters["notified"] += 1

        db.commit()

    log.info(
        "Alert pass: opened=%d updated=%d resolved=%d notified=%d",
        counters["opened"],
        counters["updated"],
        counters["resolved"],
        counters["notified"],
    )
    return counters


def _severity_rank(sev: str) -> int:
    return {"info": 0, "warning": 1, "critical": 2}.get(sev, 0)


def _should_renotify(alert: Alert) -> bool:
    """True if we should re-page on a still-open, unacknowledged alert."""
    if alert.acknowledged_at is not None:
        return False
    if alert.last_notified_at is None:
        # Should be impossible (set on open), but be safe.
        return True
    age = datetime.now(timezone.utc) - alert.last_notified_at
    return age >= timedelta(seconds=settings.alert_renotify_seconds)


def _notify(
    notifiers: list[Notifier],
    alert: Alert,
    occasion: Occasion,
    db: Session,
) -> None:
    for n in notifiers:
        n.send(alert, occasion)
    alert.last_notified_at = datetime.now(timezone.utc)
    # Caller commits.
    db.flush()
