"""Target Registry: Virtual Scoped Tab & Target Ownership Management.

Enforces Invariant 8 (Lease Ownership):
No command executed without valid Lease; no cross-lease Target access.
Maps: TargetID -> BrowserContextID -> LeaseID -> AgentID.
Eliminates global active tab collisions by scoping tab enumeration and action routing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import threading
from typing import Any


@dataclass(slots=True)
class TargetRecord:
    target_id: str
    browser_context_id: str | None
    lease_id: str
    agent_id: str
    url: str = ""
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TargetRegistry:
    """Thread-safe registry mapping browser targets to their owning lease and agent."""

    def __init__(self):
        self._lock = threading.RLock()
        self._targets: dict[str, TargetRecord] = {}
        self._lease_targets: dict[str, set[str]] = {}

    def register_target(
        self,
        target_id: str,
        browser_context_id: str | None,
        lease_id: str,
        agent_id: str,
        url: str = "",
        title: str = "",
    ) -> TargetRecord:
        with self._lock:
            # Clean up old reverse mapping if re-registering target under a different lease
            old_record = self._targets.get(target_id)
            if old_record and old_record.lease_id != lease_id:
                old_set = self._lease_targets.get(old_record.lease_id)
                if old_set:
                    old_set.discard(target_id)
                    if not old_set:
                        del self._lease_targets[old_record.lease_id]

            record = TargetRecord(
                target_id=target_id,
                browser_context_id=browser_context_id,
                lease_id=lease_id,
                agent_id=agent_id,
                url=url,
                title=title,
            )
            self._targets[target_id] = record
            if lease_id not in self._lease_targets:
                self._lease_targets[lease_id] = set()
            self._lease_targets[lease_id].add(target_id)
            return record

    def unregister_target(self, target_id: str) -> TargetRecord | None:
        with self._lock:
            record = self._targets.pop(target_id, None)
            if record and record.lease_id in self._lease_targets:
                self._lease_targets[record.lease_id].discard(target_id)
                if not self._lease_targets[record.lease_id]:
                    del self._lease_targets[record.lease_id]
            return record

    def update_target(self, target_id: str, url: str | None = None, title: str | None = None) -> None:
        with self._lock:
            record = self._targets.get(target_id)
            if record:
                if url is not None:
                    record.url = url
                if title is not None:
                    record.title = title

    def get_target(self, target_id: str) -> TargetRecord | None:
        with self._lock:
            return self._targets.get(target_id)

    def get_targets_for_lease(self, lease_id: str) -> list[TargetRecord]:
        with self._lock:
            target_ids = self._lease_targets.get(lease_id, set())
            return [self._targets[tid] for tid in target_ids if tid in self._targets]

    def is_target_owned_by_lease(self, target_id: str, lease_id: str) -> bool:
        with self._lock:
            record = self._targets.get(target_id)
            return bool(record and record.lease_id == lease_id)

    def clear_lease_targets(self, lease_id: str) -> list[str]:
        with self._lock:
            target_ids = list(self._lease_targets.pop(lease_id, set()))
            for tid in target_ids:
                self._targets.pop(tid, None)
            return target_ids

    def get_all_records(self) -> list[TargetRecord]:
        with self._lock:
            return list(self._targets.values())
