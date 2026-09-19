"""Lease Manager: Lifecycle, Fencing Tokens, and Identity Concurrency Locks.

Enforces:
- Invariant 8: No command executed without valid Lease.
- Invariant 10: Account Lease for server-side state isolation.
Fencing tokens prevent race conditions and ghost actions from expired or revoked sessions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
import uuid
from typing import Any

from ..contracts import (
    IdentityConflictError,
    Lease,
    LeaseExpiredError,
    LeaseNotFoundError,
)


@dataclass
class IdentityLockRecord:
    exclusive_lease_id: str | None = None
    shared_lease_ids: set[str] = field(default_factory=set)
    reserved_at: dict[str, float] = field(default_factory=dict)

    def is_locked_for_exclusive(self) -> bool:
        return self.exclusive_lease_id is not None or len(self.shared_lease_ids) > 0

    def is_locked_for_shared(self) -> bool:
        return self.exclusive_lease_id is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exclusive_lease_id": self.exclusive_lease_id,
            "shared_lease_ids": list(self.shared_lease_ids),
            "reserved_at": self.reserved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | list[Any]) -> IdentityLockRecord:
        if isinstance(data, list):
            # Legacy format [lease_id, exclusive_bool]
            lease_id, exclusive = data[0], bool(data[1])
            if exclusive:
                return cls(exclusive_lease_id=lease_id)
            return cls(shared_lease_ids={lease_id} if lease_id else set())
        return cls(
            exclusive_lease_id=data.get("exclusive_lease_id"),
            shared_lease_ids=set(data.get("shared_lease_ids", [])),
            reserved_at=data.get("reserved_at", {}),
        )


class LeaseManager:
    """Thread-safe lease coordinator managing lease lifecycles, fencing tokens, and identity locks."""

    def __init__(self):
        self._lock = threading.RLock()
        self._leases: dict[str, Lease] = {}
        self._fencing_counter = 1
        # Map: auth_identity -> IdentityLockRecord
        self._active_identities: dict[str, IdentityLockRecord] = {}

    def _is_lease_active(self, lease_id: str, record: IdentityLockRecord, now: float) -> bool:
        """Checks if a lease is active or has a valid pending reservation (< 60s)."""
        lease = self._leases.get(lease_id)
        if lease is not None:
            return lease.is_active and now <= lease.expires_at
        return (now - record.reserved_at.get(lease_id, 0)) < 60.0

    def reserve_identity(self, auth_identity: str, exclusive: bool, temp_lease_id: str) -> None:
        """Pre-reserves an identity before creating browser resources, preventing resource leaks on conflict."""
        with self._lock:
            now = time.time()
            record = self._active_identities.get(auth_identity)
            if record:
                # Purge expired leases from record
                if record.exclusive_lease_id and not self._is_lease_active(record.exclusive_lease_id, record, now):
                    record.exclusive_lease_id = None
                active_shared = set()
                for lid in record.shared_lease_ids:
                    if self._is_lease_active(lid, record, now):
                        active_shared.add(lid)
                record.shared_lease_ids = active_shared

                if exclusive and record.is_locked_for_exclusive():
                    holder = record.exclusive_lease_id or next(iter(record.shared_lease_ids), "unknown")
                    raise IdentityConflictError(
                        f"Identity {auth_identity!r} is currently locked by lease {holder}."
                    )
                if not exclusive and record.is_locked_for_shared():
                    raise IdentityConflictError(
                        f"Identity {auth_identity!r} is currently locked exclusively by lease {record.exclusive_lease_id}."
                    )
            else:
                record = IdentityLockRecord()
                self._active_identities[auth_identity] = record

            record.reserved_at[temp_lease_id] = now
            if exclusive:
                record.exclusive_lease_id = temp_lease_id
            else:
                record.shared_lease_ids.add(temp_lease_id)

    def rollback_identity(self, auth_identity: str, temp_lease_id: str) -> None:
        """Rolls back an identity pre-reservation if resource provisioning fails."""
        with self._lock:
            record = self._active_identities.get(auth_identity)
            if not record:
                return
            record.reserved_at.pop(temp_lease_id, None)
            if record.exclusive_lease_id == temp_lease_id:
                record.exclusive_lease_id = None
            record.shared_lease_ids.discard(temp_lease_id)
            if record.exclusive_lease_id is None and not record.shared_lease_ids:
                del self._active_identities[auth_identity]

    def create_lease(
        self,
        agent_id: str,
        project_id: str,
        execution_class: str,
        cdp_url: str,
        timeout_seconds: float = 300.0,
        auth_identity: str | None = None,
        exclusive_identity: bool = True,
        browser_context_id: str | None = None,
        process_pid: int | None = None,
        user_data_dir: str | None = None,
        pre_reserved_lease_id: str | None = None,
    ) -> Lease:
        with self._lock:
            lease_id = pre_reserved_lease_id or f"lease-{uuid.uuid4().hex[:12]}"
            fencing_token = self._fencing_counter
            self._fencing_counter += 1

            now = time.time()
            expires_at = now + timeout_seconds

            # If not pre-reserved, reserve identity now
            if auth_identity and not pre_reserved_lease_id:
                self.reserve_identity(auth_identity, exclusive_identity, lease_id)

            lease = Lease(
                lease_id=lease_id,
                agent_id=agent_id,
                project_id=project_id,
                execution_class=execution_class,
                fencing_token=fencing_token,
                cdp_url=cdp_url,
                browser_context_id=browser_context_id,
                owned_target_ids=[],
                auth_identity=auth_identity,
                expires_at=expires_at,
                is_active=True,
                process_pid=process_pid,
                user_data_dir=user_data_dir,
            )

            self._leases[lease_id] = lease
            return lease

    def validate_lease(self, lease_id: str, fencing_token: int | None = None) -> Lease:
        """Validates that a lease exists, is active, has not expired, and matches fencing token."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease:
                raise LeaseNotFoundError(f"Lease {lease_id!r} not found")

            if not lease.is_active:
                raise LeaseExpiredError(f"Lease {lease_id!r} is no longer active (revoked or closed)")

            now = time.time()
            if now > lease.expires_at:
                lease.is_active = False
                if lease.auth_identity:
                    self.rollback_identity(lease.auth_identity, lease_id)
                raise LeaseExpiredError(
                    f"Lease {lease_id!r} expired {int(now - lease.expires_at)}s ago"
                )

            # Strict fencing validation: token must match exactly when provided
            if fencing_token is not None and fencing_token != lease.fencing_token:
                raise LeaseExpiredError(
                    f"Invalid fencing token #{fencing_token} (expected: #{lease.fencing_token}) for lease {lease_id!r}"
                )

            return lease

    def revoke_lease(self, lease_id: str, reason: str = "Revoked") -> Lease:
        """Revokes an active lease immediately."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease:
                raise LeaseNotFoundError(f"Lease {lease_id!r} not found")

            lease.is_active = False
            if lease.auth_identity:
                self.rollback_identity(lease.auth_identity, lease_id)

            return lease

    def renew_lease(self, lease_id: str, additional_seconds: float = 300.0) -> Lease:
        """Extends lease expiration and increments its fencing token."""
        with self._lock:
            lease = self.validate_lease(lease_id)
            lease.expires_at += additional_seconds
            lease.fencing_token = self._fencing_counter
            self._fencing_counter += 1
            return lease

    def get_lease(self, lease_id: str) -> Lease | None:
        with self._lock:
            return self._leases.get(lease_id)

    def get_active_leases(self) -> list[Lease]:
        with self._lock:
            now = time.time()
            active = []
            for lease in self._leases.values():
                if lease.is_active and now <= lease.expires_at:
                    active.append(lease)
                elif lease.is_active and now > lease.expires_at:
                    lease.is_active = False
                    if lease.auth_identity:
                        self.rollback_identity(lease.auth_identity, lease.lease_id)
            return active

    def get_active_lease_count(self) -> int:
        return len(self.get_active_leases())

    def is_identity_locked(self, auth_identity: str) -> bool:
        with self._lock:
            now = time.time()
            record = self._active_identities.get(auth_identity)
            if not record:
                return False

            # Check exclusive
            if record.exclusive_lease_id:
                if self._is_lease_active(record.exclusive_lease_id, record, now):
                    return True
                record.exclusive_lease_id = None

            # Check shared
            active_shared = set()
            for lid in record.shared_lease_ids:
                if self._is_lease_active(lid, record, now):
                    active_shared.add(lid)
            record.shared_lease_ids = active_shared

            if not record.exclusive_lease_id and not record.shared_lease_ids:
                del self._active_identities[auth_identity]
                return False
            return True
