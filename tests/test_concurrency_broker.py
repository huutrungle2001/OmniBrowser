"""
Unit and integration tests for OmniBrowser Concurrency Broker (Milestone v2.2).
Verifies:
- Scoped tabs & target isolation across concurrent leases
- Fencing token protection & lease expiration (LeaseExpiredError)
- Execution Class routing (Class S, Class I, Class A)
- Auth State Vault permissions (chmod 600) and profile sanctity (Invariant 9)
- Admission controller backpressure
- Lifecycle watchdog drain protocol
- Zero Live Profile Pollution invariant
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import time
import pytest

from browser_core.broker import (
    AdmissionController,
    AuthStateVault,
    DaemonState,
    LeaseManager,
    LifecycleWatchdog,
    SessionRouter,
    TargetRegistry,
)
from browser_core.contracts import (
    AdmissionRejectedError,
    BrowserRequirements,
    ExecutionClass,
    IdentityConflictError,
    LeaseExpiredError,
    LeaseNotFoundError,
)


@pytest.fixture
def temp_broker_dir():
    temp_dir = tempfile.mkdtemp(prefix="omnibrowser_test_broker_")
    yield temp_dir
    import shutil
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_auth_state_vault_permissions_and_sanctity(temp_broker_dir):
    """Verifies AuthStateVault enforces chmod 600 and Invariant 9 (Profile Sanctity)."""
    vault = AuthStateVault(vault_dir=Path(temp_broker_dir) / "vault")
    
    # 1. Save snapshot and verify file permissions (0600)
    version = vault.save_snapshot(
        identity="github-user",
        cookies=[{"name": "session", "value": "test-cookie-123", "domain": "github.com"}],
        local_storage={"theme": "dark"},
    )
    assert version == "v1"

    snapshot = vault.get_snapshot("github-user", "v1")
    assert snapshot is not None
    assert snapshot["identity"] == "github-user"
    assert snapshot["cookies"][0]["value"] == "test-cookie-123"

    # Check file mode
    snapshot_file = Path(temp_broker_dir) / "vault" / "github-user" / "v1.json"
    file_stat = snapshot_file.stat()
    assert stat.S_IMODE(file_stat.st_mode) == 0o600

    # 2. Verify Invariant 9: protected profile cannot be used as runtime directory
    with pytest.raises(PermissionError) as exc_info:
        vault.assert_not_protected_profile(Path("~/.chrome-ai-profile").expanduser())
    assert "SAFETY INVARIANT 9 VIOLATION" in str(exc_info.value)


def test_admission_controller():
    """Verifies admission controller backpressure and queue limits."""
    admission = AdmissionController(
        max_concurrent_leases=2,
        min_available_memory_mb=500,
        max_cpu_load_ratio=100.0,  # High threshold for test stability
    )

    req_s = BrowserRequirements(execution_class=ExecutionClass.CLASS_S)
    req_i = BrowserRequirements(execution_class=ExecutionClass.CLASS_I)

    # Admitted when under limit
    admitted, _ = admission.check_admission(req_s, current_active_leases=0)
    assert admitted is True

    # Rejected when max_concurrent_leases reached
    admitted, reason = admission.check_admission(req_s, current_active_leases=2)
    assert admitted is False
    assert "Maximum concurrent lease limit" in reason

    # Acquire admission with timeout
    with pytest.raises(AdmissionRejectedError):
        admission.acquire_admission(req_i, get_active_lease_count_fn=lambda: 2, timeout_seconds=0.3)


def test_target_registry_isolation():
    """Verifies that targets are strictly mapped to their owning lease."""
    registry = TargetRegistry()

    # Register target for Lease A
    t1 = registry.register_target("target-1", "ctx-1", "lease-a", "agent-1", url="https://example.com/1")
    t2 = registry.register_target("target-2", "ctx-1", "lease-a", "agent-1", url="https://example.com/2")

    # Register target for Lease B
    t3 = registry.register_target("target-3", "ctx-2", "lease-b", "agent-2", url="https://example.com/3")

    # Verify ownership
    assert registry.is_target_owned_by_lease("target-1", "lease-a") is True
    assert registry.is_target_owned_by_lease("target-1", "lease-b") is False
    assert registry.is_target_owned_by_lease("target-3", "lease-b") is True

    # Verify lease-scoped listing
    lease_a_targets = registry.get_targets_for_lease("lease-a")
    assert len(lease_a_targets) == 2
    assert {t.target_id for t in lease_a_targets} == {"target-1", "target-2"}

    lease_b_targets = registry.get_targets_for_lease("lease-b")
    assert len(lease_b_targets) == 1
    assert lease_b_targets[0].target_id == "target-3"

    # Unregister
    registry.unregister_target("target-1")
    assert len(registry.get_targets_for_lease("lease-a")) == 1


def test_lease_manager_lifecycle_and_fencing():
    """Verifies lease issuance, expiration, revocation, and monotonic fencing tokens."""
    manager = LeaseManager()

    # Create lease with 1 second timeout
    lease = manager.create_lease(
        agent_id="agent-1",
        project_id="test-proj",
        execution_class=ExecutionClass.CLASS_S,
        cdp_url="http://127.0.0.1:9999",
        timeout_seconds=1.0,
    )
    assert lease.lease_id.startswith("lease-")
    assert lease.fencing_token == 1
    assert lease.is_active is True

    # Valid lease check
    validated = manager.validate_lease(lease.lease_id, fencing_token=1)
    assert validated.lease_id == lease.lease_id

    # Stale fencing token check
    with pytest.raises(LeaseExpiredError) as exc_info:
        manager.validate_lease(lease.lease_id, fencing_token=0)
    assert "Stale fencing token" in str(exc_info.value)

    # Renewal increments fencing token
    old_expires_at = lease.expires_at
    renewed = manager.renew_lease(lease.lease_id, additional_seconds=10.0)
    assert renewed.fencing_token == 2
    assert renewed.expires_at > old_expires_at

    # Old fencing token #1 now rejected
    with pytest.raises(LeaseExpiredError):
        manager.validate_lease(lease.lease_id, fencing_token=1)

    # Revocation
    manager.revoke_lease(lease.lease_id)
    with pytest.raises(LeaseExpiredError) as exc_info:
        manager.validate_lease(lease.lease_id)
    assert "no longer active" in str(exc_info.value)

    # Nonexistent lease
    with pytest.raises(LeaseNotFoundError):
        manager.validate_lease("lease-nonexistent")


def test_lease_manager_identity_concurrency_lock():
    """Verifies exclusive identity lease enforcement (Invariant 10)."""
    manager = LeaseManager()

    # Agent 1 acquires exclusive lease for "user@gmail.com"
    lease1 = manager.create_lease(
        agent_id="agent-1",
        project_id="test-proj",
        execution_class=ExecutionClass.CLASS_A,
        cdp_url="http://127.0.0.1:9999",
        auth_identity="user@gmail.com",
        exclusive_identity=True,
    )
    assert lease1.auth_identity == "user@gmail.com"
    assert manager.is_identity_locked("user@gmail.com") is True

    # Agent 2 attempts to acquire lease for the same identity -> IdentityConflictError
    with pytest.raises(IdentityConflictError) as exc_info:
        manager.create_lease(
            agent_id="agent-2",
            project_id="test-proj",
            execution_class=ExecutionClass.CLASS_A,
            cdp_url="http://127.0.0.1:9999",
            auth_identity="user@gmail.com",
            exclusive_identity=True,
        )
    assert "currently locked exclusively" in str(exc_info.value)

    # Releasing lease 1 unlocks identity
    manager.revoke_lease(lease1.lease_id)
    assert manager.is_identity_locked("user@gmail.com") is False

    # Now Agent 2 can acquire it
    lease2 = manager.create_lease(
        agent_id="agent-2",
        project_id="test-proj",
        execution_class=ExecutionClass.CLASS_A,
        cdp_url="http://127.0.0.1:9999",
        auth_identity="user@gmail.com",
        exclusive_identity=True,
    )
    assert lease2.auth_identity == "user@gmail.com"


def test_lifecycle_watchdog_and_drain_protocol():
    """Verifies crash tracking and Drain Protocol state transitions."""
    recycled_daemons = []
    watchdog = LifecycleWatchdog(on_recycle_callback=lambda did: recycled_daemons.append(did))

    # Register daemon
    record = watchdog.register_daemon("daemon-1", pid=1234, max_rss_mb=100)
    assert record.state == DaemonState.HEALTHY

    # Memory check triggers draining
    is_draining = watchdog.check_memory_and_drain("daemon-1", get_rss_fn=lambda pid: 150)
    assert is_draining is True
    assert watchdog.is_draining("daemon-1") is True

    # While leases are active, does not recycle
    assert watchdog.check_drain_completion("daemon-1", active_leases_on_daemon=2) is False
    assert len(recycled_daemons) == 0

    # When active leases drop to 0, recycles
    assert watchdog.check_drain_completion("daemon-1", active_leases_on_daemon=0) is True
    assert "daemon-1" in recycled_daemons


@pytest.mark.timeout(30)
def test_session_router_class_s_and_i_integration(temp_broker_dir):
    """Integration test: verifies Class S fast context and Class I process isolation."""
    router = SessionRouter(
        vault_dir=Path(temp_broker_dir) / "vault",
        state_dir=Path(temp_broker_dir) / "broker",
    )
    try:
        # 1. Request Class S lease (Shared Multi-Context)
        t_start = time.monotonic()
        lease_s1 = router.request_lease(
            BrowserRequirements(execution_class=ExecutionClass.CLASS_S),
            agent_id="agent-s1",
            project_id="proj-1",
        )
        duration_ms = (time.monotonic() - t_start) * 1000
        assert lease_s1.execution_class == ExecutionClass.CLASS_S
        assert lease_s1.browser_context_id is not None
        assert lease_s1.is_active is True

        # Second Class S lease should be warm and fast
        t_start = time.monotonic()
        lease_s2 = router.request_lease(
            BrowserRequirements(execution_class=ExecutionClass.CLASS_S),
            agent_id="agent-s2",
            project_id="proj-1",
        )
        s2_duration_ms = (time.monotonic() - t_start) * 1000
        assert s2_duration_ms < 500.0  # Context creation is fast (< 500ms)

        # Verify they have distinct contexts
        assert lease_s1.browser_context_id != lease_s2.browser_context_id
        assert lease_s1.lease_id != lease_s2.lease_id

        # Verify target registry isolation
        s1_targets = router.target_registry.get_targets_for_lease(lease_s1.lease_id)
        s2_targets = router.target_registry.get_targets_for_lease(lease_s2.lease_id)
        assert len(s1_targets) >= 1
        assert len(s2_targets) >= 1
        assert s1_targets[0].target_id != s2_targets[0].target_id

        # 2. Release Class S leases
        router.release_lease(lease_s1.lease_id)
        router.release_lease(lease_s2.lease_id)

        # 3. Request Class I lease (Dedicated Process)
        lease_i = router.request_lease(
            BrowserRequirements(execution_class=ExecutionClass.CLASS_I),
            agent_id="agent-i",
            project_id="proj-2",
        )
        assert lease_i.execution_class == ExecutionClass.CLASS_I
        assert lease_i.process_pid is not None
        assert lease_i.user_data_dir is not None
        assert Path(lease_i.user_data_dir).exists()

        # Release Class I lease and verify process and temp dir are cleaned up
        temp_dir = lease_i.user_data_dir
        router.release_lease(lease_i.lease_id)
        assert not Path(temp_dir).exists()

    finally:
        router.close()


def test_cli_broker_and_scoped_tabs(capsys, monkeypatch, temp_broker_dir):
    """Verifies CLI broker subcommands and --lease scoped operations."""
    import json
    from scripts.cdp_controller import main

    monkeypatch.setenv("OMNIBROWSER_BROKER_DIR", str(Path(temp_broker_dir) / "broker"))
    monkeypatch.setenv("OMNIBROWSER_VAULT_DIR", str(Path(temp_broker_dir) / "vault"))

    # 1. broker status
    rc = main(["broker", "status"])
    assert rc is None or rc == 0
    captured = capsys.readouterr()
    status_data = json.loads(captured.out.strip())
    assert "telemetry" in status_data
    assert "active_leases_count" in status_data
    assert status_data["active_leases_count"] == 0

    # 2. broker lease request
    rc = main(["broker", "lease", "request", "--class", "S", "--agent-id", "test-agent"])
    assert rc is None or rc == 0
    captured = capsys.readouterr()
    lease_data = json.loads(captured.out.strip())
    lease_id = lease_data["lease_id"]
    assert lease_id.startswith("lease-")
    assert lease_data["execution_class"] == "S"

    # 3. list-tabs with valid lease
    rc = main(["--lease", lease_id, "list-tabs"])
    assert rc is None or rc == 0
    captured = capsys.readouterr()
    assert lease_id in captured.out
    assert "Total open tabs:" in captured.out

    # 4. release lease
    rc = main(["broker", "lease", "release", lease_id])
    assert rc is None or rc == 0
    captured = capsys.readouterr()
    release_data = json.loads(captured.out.strip())
    assert release_data["released"] == lease_id

    # 5. list-tabs with revoked lease -> ExitCode.POLICY_BLOCKED (5)
    rc = main(["--lease", lease_id, "list-tabs"])
    assert rc == 5
    captured = capsys.readouterr()
    assert "is no longer active" in captured.err
