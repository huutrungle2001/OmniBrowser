"""Lease Manager: Lifecycle, Fencing Tokens, and Identity Concurrency Locks.

Enforces:
- Invariant 8: No command executed without valid Lease.
- Invariant 10: Account Lease for server-side state isolation.
Fencing tokens prevent race conditions and ghost actions from expired or revoked sessions.
"""

from __future__ import annotations

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


class LeaseManager:
    """Thread-safe lease coordinator managing lease lifecycles, fencing tokens, and identity locks."""

    def __init__(self):
        self._lock = threading.RLock()
        self._leases: dict[str, Lease] = {}
        self._fencing_counter = 1
        # Map: auth_identity -> (lease_id, exclusive)
        self._active_identities: dict[str, tuple[str, bool]] = {}

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
    ) -> Lease:
        with self._lock:
            # Check identity lock if auth_identity is requested
            if auth_identity:
                if auth_identity in self._active_identities:
                    existing_lease_id, is_exclusive = self._active_identities[auth_identity]
                    if is_exclusive or exclusive_identity:
                        raise IdentityConflictError(
                            f"Identity {auth_identity!r} is currently locked exclusively by lease {existing_lease_id}"
                        )
                self._active_identities[auth_identity] = ("", exclusive_identity)

            lease_id = f"lease-{uuid.uuid4().hex[:12]}"
            fencing_token = self._fencing_counter
            self._fencing_counter += 1

            now = time.time()
            expires_at = now + timeout_seconds

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
            if auth_identity:
                self._active_identities[auth_identity] = (lease_id, exclusive_identity)

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
                if lease.auth_identity and self._active_identities.get(lease.auth_identity, (None,))[0] == lease_id:
                    del self._active_identities[lease.auth_identity]
                raise LeaseExpiredError(
                    f"Lease {lease_id!r} expired {int(now - lease.expires_at)}s ago"
                )

            if fencing_token is not None and fencing_token < lease.fencing_token:
                raise LeaseExpiredError(
                    f"Stale fencing token {fencing_token} (current: {lease.fencing_token}) for lease {lease_id!r}"
                )

            return lease

    def revoke_lease(self, lease_id: str, reason: str = "Revoked") -> Lease:
        """Revokes an active lease immediately."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if not lease:
                raise LeaseNotFoundError(f"Lease {lease_id!r} not found")

            lease.is_active = False
            if lease.auth_identity and self._active_identities.get(lease.auth_identity, (None,))[0] == lease_id:
                del self._active_identities[lease.auth_identity]

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
                    if lease.auth_identity and self._active_identities.get(lease.auth_identity, (None,))[0] == lease.lease_id:
                        del self._active_identities[lease.auth_identity]
            return active

    def get_active_lease_count(self) -> int:
        return len(self.get_active_leases())

    def is_identity_locked(self, auth_identity: str) -> bool:
        with self._lock:
            if auth_identity not in self._active_identities:
                return False
            lease_id, _ = self._active_identities[auth_identity]
            lease = self._leases.get(lease_id)
            if not lease or not lease.is_active or time.time() > lease.expires_at:
                del self._active_identities[auth_identity]
                return False
            return True
