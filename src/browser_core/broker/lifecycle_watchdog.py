"""Lifecycle Watchdog: Health Monitoring, Crash Containment, and Drain Protocol.

Monitors daemon and instance health, detects crashes via CDP events, and implements
the Drain Protocol to recycle memory-bloated daemons without disrupting active sessions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import threading
import time
from typing import Any, Callable


class DaemonState(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DRAINING = "DRAINING"
    RECYCLED = "RECYCLED"


@dataclass
class DaemonRecord:
    daemon_id: str
    pid: int
    state: DaemonState = DaemonState.HEALTHY
    created_at: float = 0.0
    crashed_targets_count: int = 0
    max_rss_mb: int = 2048
    drain_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["state"] = self.state.value
        return result


class LifecycleWatchdog:
    """Monitors browser daemons and orchestrates safe Drain & Recycle protocols."""

    def __init__(self, on_recycle_callback: Callable[[str], None] | None = None):
        self._lock = threading.RLock()
        self._daemons: dict[str, DaemonRecord] = {}
        self._on_recycle = on_recycle_callback

    def register_daemon(self, daemon_id: str, pid: int, max_rss_mb: int = 2048) -> DaemonRecord:
        with self._lock:
            record = DaemonRecord(
                daemon_id=daemon_id,
                pid=pid,
                state=DaemonState.HEALTHY,
                created_at=time.time(),
                max_rss_mb=max_rss_mb,
            )
            self._daemons[daemon_id] = record
            return record

    def unregister_daemon(self, daemon_id: str) -> DaemonRecord | None:
        with self._lock:
            return self._daemons.pop(daemon_id, None)

    def mark_draining(self, daemon_id: str, reason: str = "Memory threshold reached") -> None:
        with self._lock:
            record = self._daemons.get(daemon_id)
            if record and record.state != DaemonState.RECYCLED:
                record.state = DaemonState.DRAINING
                record.drain_reason = reason

    def is_draining(self, daemon_id: str) -> bool:
        with self._lock:
            record = self._daemons.get(daemon_id)
            return bool(record and record.state == DaemonState.DRAINING)

    def is_healthy(self, daemon_id: str) -> bool:
        with self._lock:
            record = self._daemons.get(daemon_id)
            return bool(record and record.state == DaemonState.HEALTHY)

    def record_target_crash(self, daemon_id: str, target_id: str) -> None:
        with self._lock:
            record = self._daemons.get(daemon_id)
            if record:
                record.crashed_targets_count += 1
                if record.crashed_targets_count >= 3:
                    record.state = DaemonState.DRAINING
                    record.drain_reason = f"Exceeded crash threshold ({record.crashed_targets_count} crashes)"

    def check_memory_and_drain(self, daemon_id: str, get_rss_fn: Callable[[int], int] | None = None) -> bool:
        """Checks if daemon RSS exceeds threshold; if so, sets to DRAINING. Returns True if draining."""
        with self._lock:
            record = self._daemons.get(daemon_id)
            if not record or record.state in (DaemonState.DRAINING, DaemonState.RECYCLED):
                return bool(record and record.state == DaemonState.DRAINING)

            rss_mb = self._get_pid_rss_mb(record.pid) if get_rss_fn is None else get_rss_fn(record.pid)
            if rss_mb > record.max_rss_mb:
                record.state = DaemonState.DRAINING
                record.drain_reason = f"RSS memory {rss_mb}MB exceeded limit {record.max_rss_mb}MB"
                return True
            return False

    def check_drain_completion(self, daemon_id: str, active_leases_on_daemon: int) -> bool:
        """If daemon is DRAINING and active leases reached 0, triggers recycle callback."""
        with self._lock:
            record = self._daemons.get(daemon_id)
            if not record or record.state != DaemonState.DRAINING:
                return False

            if active_leases_on_daemon == 0:
                record.state = DaemonState.RECYCLED
                if self._on_recycle:
                    try:
                        self._on_recycle(daemon_id)
                    except Exception:
                        pass
                return True
            return False

    def _get_pid_rss_mb(self, pid: int) -> int:
        try:
            import psutil
            process = psutil.Process(pid)
            return int(process.memory_info().rss / (1024 * 1024))
        except Exception:
            return 0

    def get_status(self, daemon_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if daemon_id:
                record = self._daemons.get(daemon_id)
                return record.to_dict() if record else {}
            return {did: rec.to_dict() for did, rec in self._daemons.items()}
