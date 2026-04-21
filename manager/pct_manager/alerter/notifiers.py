"""Notifier plug-ins (Slack + SMTP).

Each notifier subclasses :class:`Notifier` and implements ``send``. The
dispatcher iterates over a list of *enabled* notifiers and calls
``send`` once per (alert, occasion) tuple, where ``occasion`` is one of
``"opened"``, ``"renotify"`` or ``"resolved"``.

Failures are logged but never raised: a broken Slack webhook must not
take the manager down.
"""

from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Literal

import httpx

from ..config import Settings
from ..models import Alert

log = logging.getLogger("pct_manager.alerter")

Occasion = Literal["opened", "renotify", "resolved"]


# ---------- shared helpers ----------


def _format_subject(alert: Alert, occasion: Occasion) -> str:
    sev = alert.severity.upper()
    verb = {
        "opened": "OPEN",
        "renotify": "STILL OPEN",
        "resolved": "RESOLVED",
    }[occasion]
    cluster = (
        f"cluster={alert.cluster_id}" if alert.cluster_id is not None else "fleet"
    )
    return f"[PCT][{sev}] {verb}: {alert.kind} ({cluster})"


def _format_body(alert: Alert, occasion: Occasion) -> str:
    payload_json = json.dumps(alert.payload, indent=2, sort_keys=True, default=str)
    lines = [
        f"Alert id:    {alert.id}",
        f"Kind:        {alert.kind}",
        f"Severity:    {alert.severity}",
        f"Cluster id:  {alert.cluster_id}",
        f"Dedup key:   {alert.dedup_key!r}",
        f"Opened at:   {alert.opened_at.isoformat()}",
    ]
    if alert.resolved_at is not None:
        lines.append(f"Resolved at: {alert.resolved_at.isoformat()}")
    lines.append(f"Occasion:    {occasion}")
    lines.append("")
    lines.append("Payload:")
    lines.append(payload_json)
    return "\n".join(lines)


# ---------- base ----------


class Notifier:
    """Abstract notifier; subclasses implement ``send`` and ``enabled``."""

    name: str = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:  # pragma: no cover - trivial
        return False

    def send(self, alert: Alert, occasion: Occasion) -> None:
        raise NotImplementedError


# ---------- Slack ----------


class SlackNotifier(Notifier):
    """Posts a Block Kit message to a Slack incoming webhook.

    We use sync ``httpx.Client`` because the alert dispatcher itself
    runs synchronously inside an APScheduler tick — keeping the I/O
    sync avoids dragging an event loop into the scheduler thread.
    """

    name = "slack"

    @property
    def enabled(self) -> bool:
        return bool(self.settings.slack_webhook_url)

    def send(self, alert: Alert, occasion: Occasion) -> None:
        url = self.settings.slack_webhook_url
        if not url:
            return
        subject = _format_subject(alert, occasion)
        body = _format_body(alert, occasion)
        # Slack truncates >40k chars; we always stay well under.
        payload = {
            "text": subject,
            "blocks": [
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": subject[:150]},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"```\n{body[:2800]}\n```",
                    },
                },
            ],
        }
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.post(url, json=payload)
                if response.status_code >= 400:
                    log.warning(
                        "Slack notifier got HTTP %s: %s",
                        response.status_code,
                        response.text[:200],
                    )
        except Exception:  # noqa: BLE001
            log.exception("Slack notifier failed for alert %s", alert.id)


# ---------- SMTP ----------


class SMTPNotifier(Notifier):
    """Plain-text email via SMTP. STARTTLS by default."""

    name = "smtp"

    @property
    def enabled(self) -> bool:
        return bool(self.settings.smtp_host and self.settings.smtp_to)

    def send(self, alert: Alert, occasion: Occasion) -> None:
        if not self.enabled:
            return
        recipients = [
            r.strip() for r in self.settings.smtp_to.split(",") if r.strip()
        ]
        if not recipients:
            return

        msg = EmailMessage()
        msg["Subject"] = _format_subject(alert, occasion)
        msg["From"] = self.settings.smtp_from
        msg["To"] = ", ".join(recipients)
        msg.set_content(_format_body(alert, occasion))

        try:
            with smtplib.SMTP(
                self.settings.smtp_host, self.settings.smtp_port, timeout=15
            ) as smtp:
                smtp.ehlo()
                if self.settings.smtp_use_tls:
                    smtp.starttls()
                    smtp.ehlo()
                if self.settings.smtp_username:
                    smtp.login(
                        self.settings.smtp_username, self.settings.smtp_password
                    )
                smtp.send_message(msg)
        except Exception:  # noqa: BLE001
            log.exception("SMTP notifier failed for alert %s", alert.id)


def build_notifiers(settings: Settings) -> list[Notifier]:
    """Return the list of enabled notifiers, in send order."""
    candidates: list[Notifier] = [SlackNotifier(settings), SMTPNotifier(settings)]
    return [n for n in candidates if n.enabled]
