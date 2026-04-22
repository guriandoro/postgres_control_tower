"""Tiny in-process state shared between collectors and the heartbeat.

Collectors that learn the local node's role (Patroni REST snapshot, WAL
``pg_is_in_recovery()`` probe) update :class:`AgentRuntimeState`. The
heartbeat loop reads the latest value on every tick so the manager
always has an accurate ``agents.role`` even when the dedicated
``patroni_state`` ingest hasn't landed yet.

Source priority is encoded in :meth:`AgentRuntimeState.update_role`:
"patroni" beats "wal" beats default. Patroni reflects the *cluster's*
view (who actually holds the leader lock), which is more authoritative
than ``pg_is_in_recovery`` on a single node — e.g. during a brief
network partition a former leader still answers "false" to
``pg_is_in_recovery`` even though Patroni has already elected someone
else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Final

# Higher = stronger signal. Both collectors run on similar cadences, so
# without ranking they would alternately overwrite each other.
_SOURCE_RANK: Final[dict[str, int]] = {
    "default": 0,
    "wal": 1,
    "patroni": 2,
}


@dataclass
class AgentRuntimeState:
    """Mutable, single-process state. Not persisted; rebuilt every restart."""

    role: str = "unknown"
    role_source: str = "default"
    role_updated_at: datetime | None = None
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def update_role(self, role: str, source: str) -> None:
        """Set ``role`` if ``source`` is at least as strong as the current
        source, OR if the existing value is older than 5 minutes (so a
        stalled high-priority collector can't pin a stale value forever).
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            current_rank = _SOURCE_RANK.get(self.role_source, 0)
            new_rank = _SOURCE_RANK.get(source, 0)
            stale = (
                self.role_updated_at is None
                or (now - self.role_updated_at).total_seconds() > 300
            )
            if new_rank >= current_rank or stale:
                self.role = role
                self.role_source = source
                self.role_updated_at = now

    def snapshot_role(self) -> str:
        with self._lock:
            return self.role
