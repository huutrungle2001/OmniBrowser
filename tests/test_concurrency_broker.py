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
    snapshot_file = vault._get_identity_dir("github-user") / "v1.json"
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
    assert "Invalid fencing token" in str(exc_info.value)

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
    assert "currently locked by lease" in str(exc_info.value)

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


def test_exact_fencing_token_validation():
    """Verifies that both stale (<) and fabricated future (>) fencing tokens are strictly rejected."""
    manager = LeaseManager()
    lease = manager.create_lease(
        agent_id="agent-fencing",
        project_id="test-fencing",
        execution_class=ExecutionClass.CLASS_S,
        cdp_url="http://127.0.0.1:9999",
    )
    current_token = lease.fencing_token

    # 1. Exact match passes
    assert manager.validate_lease(lease.lease_id, fencing_token=current_token).lease_id == lease.lease_id

    # 2. Stale token rejected (< current_token)
    with pytest.raises(LeaseExpiredError) as exc_stale:
        manager.validate_lease(lease.lease_id, fencing_token=current_token - 1)
    assert f"Invalid fencing token #{current_token - 1} (expected: #{current_token})" in str(exc_stale.value)

    # 3. Fabricated future token rejected (> current_token)
    with pytest.raises(LeaseExpiredError) as exc_future:
        manager.validate_lease(lease.lease_id, fencing_token=current_token + 99)
    assert f"Invalid fencing token #{current_token + 99} (expected: #{current_token})" in str(exc_future.value)

    # 4. After renewal, old token rejected, new token accepted
    renewed = manager.renew_lease(lease.lease_id)
    assert renewed.fencing_token > current_token
    with pytest.raises(LeaseExpiredError):
        manager.validate_lease(lease.lease_id, fencing_token=current_token)
    assert manager.validate_lease(lease.lease_id, fencing_token=renewed.fencing_token).lease_id == lease.lease_id


def test_shared_and_exclusive_identity_locks():
    """Verifies that multiple shared leases coexist, exclusive is blocked until all shared release, and vice versa."""
    manager = LeaseManager()

    # 1. First shared lease succeeds
    l_sh1 = manager.create_lease(
        agent_id="agent-1",
        project_id="test",
        execution_class=ExecutionClass.CLASS_A,
        cdp_url="http://127.0.0.1:9999",
        auth_identity="alice@example.com",
        exclusive_identity=False,
    )
    assert manager.is_identity_locked("alice@example.com") is True

    # 2. Second shared lease also succeeds
    l_sh2 = manager.create_lease(
        agent_id="agent-2",
        project_id="test",
        execution_class=ExecutionClass.CLASS_A,
        cdp_url="http://127.0.0.1:9999",
        auth_identity="alice@example.com",
        exclusive_identity=False,
    )

    # 3. Exclusive lease request is rejected while shared leases are active
    with pytest.raises(IdentityConflictError) as exc_info:
        manager.create_lease(
            agent_id="agent-3",
            project_id="test",
            execution_class=ExecutionClass.CLASS_A,
            cdp_url="http://127.0.0.1:9999",
            auth_identity="alice@example.com",
            exclusive_identity=True,
        )
    assert "currently locked by lease" in str(exc_info.value)

    # 4. Releasing l_sh1 still leaves identity locked by l_sh2
    manager.revoke_lease(l_sh1.lease_id)
    assert manager.is_identity_locked("alice@example.com") is True

    # 5. Exclusive request still rejected
    with pytest.raises(IdentityConflictError):
        manager.create_lease(
            agent_id="agent-3",
            project_id="test",
            execution_class=ExecutionClass.CLASS_A,
            cdp_url="http://127.0.0.1:9999",
            auth_identity="alice@example.com",
            exclusive_identity=True,
        )

    # 6. Releasing l_sh2 unlocks the identity
    manager.revoke_lease(l_sh2.lease_id)
    assert manager.is_identity_locked("alice@example.com") is False

    # 7. Exclusive lease now succeeds
    l_ex = manager.create_lease(
        agent_id="agent-3",
        project_id="test",
        execution_class=ExecutionClass.CLASS_A,
        cdp_url="http://127.0.0.1:9999",
        auth_identity="alice@example.com",
        exclusive_identity=True,
    )
    assert manager.is_identity_locked("alice@example.com") is True

    # 8. Both shared and exclusive requests are rejected while exclusive is active
    with pytest.raises(IdentityConflictError):
        manager.create_lease(
            agent_id="agent-4",
            project_id="test",
            execution_class=ExecutionClass.CLASS_A,
            cdp_url="http://127.0.0.1:9999",
            auth_identity="alice@example.com",
            exclusive_identity=False,
        )

    with pytest.raises(IdentityConflictError):
        manager.create_lease(
            agent_id="agent-5",
            project_id="test",
            execution_class=ExecutionClass.CLASS_A,
            cdp_url="http://127.0.0.1:9999",
            auth_identity="alice@example.com",
            exclusive_identity=True,
        )

    # 9. Clean release
    manager.revoke_lease(l_ex.lease_id)
    assert manager.is_identity_locked("alice@example.com") is False


def test_reserve_before_provision_rollback():
    """Verifies that identity pre-reservation rolls back cleanly on provisioning failure."""
    manager = LeaseManager()
    temp_lease_id = "temp-lease-fail"

    # Pre-reserve
    manager.reserve_identity("bob@example.com", exclusive=True, temp_lease_id=temp_lease_id)
    assert manager.is_identity_locked("bob@example.com") is True

    # Another lease attempt fails
    with pytest.raises(IdentityConflictError):
        manager.reserve_identity("bob@example.com", exclusive=True, temp_lease_id="temp-lease-2")

    # Simulate provisioning failure -> rollback
    manager.rollback_identity("bob@example.com", temp_lease_id)
    assert manager.is_identity_locked("bob@example.com") is False

    # Now another lease can reserve cleanly
    manager.reserve_identity("bob@example.com", exclusive=True, temp_lease_id="temp-lease-success")
    assert manager.is_identity_locked("bob@example.com") is True
    manager.rollback_identity("bob@example.com", "temp-lease-success")


def test_target_registry_re_registration():
    """Verifies that re-registering an existing target under a new lease cleans up the old lease's target set."""
    registry = TargetRegistry()

    # Register under lease-1
    registry.register_target("target-shared", "ctx-1", "lease-1", "agent-1")
    assert registry.is_target_owned_by_lease("target-shared", "lease-1") is True
    assert "target-shared" in [t.target_id for t in registry.get_targets_for_lease("lease-1")]

    # Re-register under lease-2
    registry.register_target("target-shared", "ctx-1", "lease-2", "agent-2")
    assert registry.is_target_owned_by_lease("target-shared", "lease-2") is True
    assert registry.is_target_owned_by_lease("target-shared", "lease-1") is False

    # Check lease target sets
    assert "target-shared" not in [t.target_id for t in registry.get_targets_for_lease("lease-1")]
    assert "target-shared" in [t.target_id for t in registry.get_targets_for_lease("lease-2")]


def test_profile_sanctity_constructor_guards():
    """Verifies that AuthStateVault and SessionRouter refuse to mount ~/.chrome-ai-profile in constructors."""
    protected_path = Path("~/.chrome-ai-profile").expanduser()

    with pytest.raises(PermissionError) as exc_vault:
        AuthStateVault(vault_dir=protected_path)
    assert "SAFETY INVARIANT 9 VIOLATION" in str(exc_vault.value)

    with pytest.raises(PermissionError) as exc_router:
        SessionRouter(state_dir=protected_path)
    assert "SAFETY INVARIANT 9 VIOLATION" in str(exc_router.value)


def test_auth_state_vault_atomic_and_numeric_sort(temp_broker_dir):
    """Verifies numeric version sorting (v1, v2, ... v12) and atomic latest.json pointer."""
    vault = AuthStateVault(vault_dir=Path(temp_broker_dir) / "vault")

    # Create 12 snapshots to test v10, v11, v12 sorting over v2, v3
    for i in range(1, 13):
        ver = vault.save_snapshot(
            identity="test-user",
            cookies=[{"name": "token", "value": f"val-{i}", "domain": "example.com"}],
            local_storage={"counter": str(i)},
        )
        assert ver == f"v{i}"

    # Verify list_snapshots is in numeric order
    snapshots = vault.list_snapshots("test-user")
    expected = [f"v{i}" for i in range(1, 13)]
    assert [s["version"] for s in snapshots] == expected

    # Verify latest snapshot matches v12
    latest = vault.get_latest_snapshot("test-user")
    assert latest is not None
    assert latest["cookies"][0]["value"] == "val-12"
    assert latest["local_storage"]["counter"] == "12"

    # Verify latest.json pointer file
    latest_file = vault._get_identity_dir("test-user") / "latest.json"
    assert latest_file.exists()
    assert stat.S_IMODE(latest_file.stat().st_mode) == 0o600


def _mp_worker(state_dir: str, vault_dir: str, counter_file: str, barrier, iterations: int):
    barrier.wait()
    router = SessionRouter(vault_dir=vault_dir, state_dir=state_dir)
    for _ in range(iterations):
        with router._state_lock():
            val = int(Path(counter_file).read_text(encoding="utf-8").strip() or "0")
            time.sleep(0.001)
            Path(counter_file).write_text(str(val + 1), encoding="utf-8")


def test_inter_process_state_lock_concurrency(temp_broker_dir):
    """Verifies that independent OS processes acquiring _state_lock on state.lock maintain serializability."""
    import multiprocessing

    counter_file = Path(temp_broker_dir) / "counter.txt"
    counter_file.write_text("0", encoding="utf-8")

    num_processes = 4
    iterations = 15
    barrier = multiprocessing.Barrier(num_processes)

    processes = []
    for _ in range(num_processes):
        p = multiprocessing.Process(
            target=_mp_worker,
            args=(
                str(Path(temp_broker_dir) / "broker"),
                str(Path(temp_broker_dir) / "vault"),
                str(counter_file),
                barrier,
                iterations,
            ),
        )
        processes.append(p)
        p.start()

    for p in processes:
        p.join(timeout=15)
        assert p.exitcode == 0

    final_val = int(counter_file.read_text(encoding="utf-8").strip())
    assert final_val == num_processes * iterations


def test_revoked_lease_not_resurrected(temp_broker_dir):
    """Verifies Critical #1 fix: status() on a stale in-memory router does not resurrect a revoked lease on disk."""
    broker_dir = Path(temp_broker_dir) / "broker"
    vault_dir = Path(temp_broker_dir) / "vault"

    r1 = SessionRouter(vault_dir=vault_dir, state_dir=broker_dir)
    r2 = SessionRouter(vault_dir=vault_dir, state_dir=broker_dir)

    # 1. R1 creates Lease 1
    lease = r1.request_lease(
        BrowserRequirements(execution_class=ExecutionClass.CLASS_S),
        agent_id="agent-1",
        project_id="test",
    )
    assert lease.is_active is True

    # 2. R2 revokes Lease 1 on disk
    r2.release_lease(lease.lease_id)
    assert r2.lease_manager.get_active_lease_count() == 0

    # 3. R1 calls status() - with authoritative reload, R1 must NOT resurrect Lease 1 on disk
    status_data = r1.status()
    assert status_data["active_leases_count"] == 0

    # 4. Verify R2 also still sees 0 active leases
    status_r2 = r2.status()
    assert status_r2["active_leases_count"] == 0

    r1.close()
    r2.close()


def test_auth_state_vault_no_identity_collision(temp_broker_dir):
    """Verifies Critical #3 fix: alice@example.com and aliceexample.com never collide or share snapshots."""
    vault = AuthStateVault(vault_dir=Path(temp_broker_dir) / "vault")

    v_a = vault.save_snapshot(
        identity="alice@example.com",
        cookies=[{"name": "token", "value": "secret-alice-email", "domain": "example.com"}],
    )
    v_b = vault.save_snapshot(
        identity="aliceexample.com",
        cookies=[{"name": "token", "value": "secret-alice-no-at", "domain": "example.com"}],
    )

    snap_a = vault.get_latest_snapshot("alice@example.com")
    snap_b = vault.get_latest_snapshot("aliceexample.com")

    assert snap_a is not None
    assert snap_b is not None
    assert snap_a["cookies"][0]["value"] == "secret-alice-email"
    assert snap_b["cookies"][0]["value"] == "secret-alice-no-at"

    # Distinct directory paths
    dir_a = vault._get_identity_dir("alice@example.com")
    dir_b = vault._get_identity_dir("aliceexample.com")
    assert dir_a != dir_b


def test_auth_state_vault_version_gap_no_overwrite(temp_broker_dir):
    """Verifies that gaps in version numbers (v1, v3) do not cause v3 to be overwritten."""
    vault = AuthStateVault(vault_dir=Path(temp_broker_dir) / "vault")
    ident = "gap-user"
    ident_dir = vault._get_identity_dir(ident)

    # Save v1
    vault.save_snapshot(ident, cookies=[{"name": "v", "value": "1"}])
    # Manually create v3.json (simulating gap where v2 was deleted)
    v3_file = ident_dir / "v3.json"
    import json
    v3_file.write_text(json.dumps({"identity": ident, "version": "v3", "cookies": [{"name": "v", "value": "3"}]}), encoding="utf-8")

    # Save next snapshot -> must be v4, NOT v3!
    v_next = vault.save_snapshot(ident, cookies=[{"name": "v", "value": "4"}])
    assert v_next == "v4"

    # Verify v3 content was not overwritten
    v3_data = json.loads(v3_file.read_text(encoding="utf-8"))
    assert v3_data["cookies"][0]["value"] == "3"


def test_admission_capacity_inside_lock(temp_broker_dir):
    """Verifies that admission capacity limit is strictly enforced inside state lock."""
    router = SessionRouter(
        vault_dir=Path(temp_broker_dir) / "vault",
        state_dir=Path(temp_broker_dir) / "broker",
        max_concurrent_leases=1,
    )
    try:
        # Lease 1 succeeds
        l1 = router.request_lease(
            BrowserRequirements(execution_class=ExecutionClass.CLASS_S, timeout_seconds=1.0),
            agent_id="a1",
            project_id="p1",
        )
        assert l1.is_active is True

        # Lease 2 with short timeout must fail
        with pytest.raises(AdmissionRejectedError) as exc_info:
            router.request_lease(
                BrowserRequirements(execution_class=ExecutionClass.CLASS_S, timeout_seconds=0.2),
                agent_id="a2",
                project_id="p1",
            )
        assert "Timed out waiting for admission" in str(exc_info.value)
    finally:
        router.close()


def test_orphan_reconciler(temp_broker_dir):
    """Verifies Critical #2 fix: orphaned runtime directories and dead processes are reaped."""
    router = SessionRouter(
        vault_dir=Path(temp_broker_dir) / "vault",
        state_dir=Path(temp_broker_dir) / "broker",
    )
    try:
        dummy_orphan = router.runtime_root / "omnibrowser_orphan_dummy"
        dummy_orphan.mkdir(parents=True, exist_ok=True)
        # Set old mtime (> 70s ago)
        old_time = time.time() - 75.0
        os.utime(dummy_orphan, (old_time, old_time))

        assert dummy_orphan.exists()

        # Reconcile orphans
        router._reconcile_orphans()

        assert not dummy_orphan.exists()
    finally:
        router.close()


