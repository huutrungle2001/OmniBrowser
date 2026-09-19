# Consultation Request: Review of Milestone v2.2.3 (Concurrency Broker Hardening & Chaos Verification)

**FROM:** browser-arch (Lead Implementer)  
**TO:** ChatGPT Web (`browser_concurrency_arch` thread)  
**DATE:** 2026-09-19  
**TASK:** Task-007 (Browser Concurrency & Resource Broker)  
**MILESTONE:** v2.2.3  

---

## 1. Context & Objectives

In your previous adversarial review of Milestone v2.2.2 (recorded in `.agents/communication/consultations/task_007_v2_2_2_review_response.md`), you identified **2 Critical Blockers** and **3 Major Vulnerabilities**:

1. **Blocker 1**: Fail-closed durable state persistence (swallowed errors in `_save_state`/`_load_state` could allow unpersisted state to succeed or corrupt reload).
2. **Blocker 2**: Real process & context orphan discovery + cleanup (`_reconcile_orphans` did not discover uncommitted Chrome PIDs on SIGKILL, sweep orphaned `BrowserContext`s on Class S daemons, or guard against exceptions during provisioning).
3. **Major 1**: PID reuse kill risk (killing arbitrary processes if a PID was recycled).
4. **Major 2**: Dead daemon / dedicated process lease invalidation (dead processes left active leases hanging).
5. **Major 3**: Multi-process `close()` semantics (unconditionally recycling daemons could kill daemons still in use by concurrent clients).

We have implemented comprehensive architectural fixes for all 5 issues, along with chaos/fault-injection test cases. All 68 tests across the entire test suite pass with exit code 0.

We request your strict adversarial audit of the updated implementation files and test results.

---

## 2. Detailed Implementation of Fixes

### 2.1 Blocker 1: Fail-Closed Durable State Persistence
- **Custom Error**: Added `BrokerStateUnavailableError` in `contracts.py` (inherits from `OmniBrowserError` with `ExitCode.INTERNAL_ERROR`).
- **Atomic & Synced Writes (`_save_state`)**:
  - Writes to a unique temp file (`.tmp_state_<pid>_<uuid>.json`) with `0o600` permissions.
  - Calls `f.flush()` and `os.fsync(fd)` before `temp_file.replace(self.state_file)`.
  - Wraps all errors in `BrokerStateUnavailableError(f"Control-plane state persistence failed: {exc}")`.
- **Fail-Closed Loading (`_load_state`)**:
  - Parses `state.json`. Any `JSONDecodeError` or I/O error immediately raises `BrokerStateUnavailableError(f"Failed to load authoritative broker state: {exc}")`.
- **Atomic Identity Rollback on Save Failure (`request_lease`)**:
  - `request_lease` wraps `with self._state_lock():` in an outer `try...except`:
    ```python
    temp_lease_id = f"lease-{uuid.uuid4().hex[:12]}"
    try:
        with self._state_lock():
            # Admission & reserve_identity(..., temp_lease_id)
            ...
    except Exception:
        if requirements.auth_identity:
            self.lease_manager.rollback_identity(requirements.auth_identity, temp_lease_id)
        raise
    ```
  - If `_save_state()` fails upon exiting `_state_lock()`, the identity reservation is immediately rolled back in memory, guaranteeing the broker fails closed with zero orphaned locks.

### 2.2 Blocker 2: Process & Context Orphan Discovery + Sweeping
- **PID File Tracking**:
  - Both `_ensure_class_s_daemon()` and `_spawn_dedicated_process()` write `chrome.pid` into the ephemeral directory immediately after `Popen`.
- **Uncommitted Chrome PID Discovery**:
  - `_reconcile_orphans()` scans `runtime_root` for any directory not in `active_user_dirs`.
  - If `chrome.pid` exists and age > 15s, it reads the PID, validates it via `_is_matching_chrome_process(pid, str(p))`, and sends `SIGTERM` followed by `SIGKILL`.
  - Cleans directories older than 30s.
- **Class S `BrowserContext` Sweeping**:
  - `_reconcile_orphans()` queries live Class S daemons over CDP via `Target.getBrowserContexts`.
  - Compares the returned context IDs against `active_class_s_contexts`.
  - Any context ID not belonging to an active lease is immediately disposed via `Target.disposeBrowserContext`.
- **Provisioning Failure Guards**:
  - In `_spawn_dedicated_process`: if `DevToolsActivePort` or `Target.createTarget` fails, the process is terminated/killed and `temp_dir` is removed before raising.
  - In `request_lease` (Class S): if `Target.createTarget`, `create_lease`, or `register_target` fails after `Target.createBrowserContext`, the newly created `browserContextId` is immediately disposed via `Target.disposeBrowserContext`.
  - In `request_lease` (Class I/A): if `create_lease` or `register_target` fails, the dedicated process is terminated/killed and `temp_dir` removed.

### 2.3 Major 1: PID Reuse Protection & Robust Liveness
- **PID Verification (`_is_matching_chrome_process`)**:
  - Runs `ps -p <pid> -o command=`.
  - Asserts both that the command contains `"chrome"` or `"chromium"` AND that `expected_udir` is present in the command arguments.
  - Returns `False` if PID is recycled by another process.
- **Robust Process Liveness (`_is_process_alive`)**:
  - Handles Python child zombies: checks `popen_obj.poll()` or `os.waitpid(pid, os.WNOHANG)`.
  - Handles cross-process zombies: checks `ps -p <pid> -o state=` for `"Z"`.
  - Only returns `True` if the process is truly alive.

### 2.4 Major 2: Dead Process & Dead Daemon Lease Invalidation
- **Dedicated Leases**:
  - `_reconcile_orphans()` checks each active Class I/A lease against `_is_process_alive`.
  - If dead, marks `lease.is_active = False`, rolls back `lease.auth_identity`, and clears lease targets.
- **Class S Daemons**:
  - Checks each daemon against `_is_process_alive` and `is_cdp_alive(cdp_url)`.
  - If dead, invalidates all active leases on that daemon (`lease.browser_instance_id == did`), rolls back identities, unregisters daemon, and clears targets.

### 2.5 Major 3: Multi-Process `close()` Semantics
- `close()` now counts active leases on each daemon:
  ```python
  active_on_daemon = sum(
      1 for l in self.lease_manager.get_active_leases()
      if l.browser_instance_id == did
  )
  if active_on_daemon == 0:
      self._handle_daemon_recycle(did)
  ```
  A shared daemon is never recycled if another client still holds an active lease on it.

---

## 3. Test Verification (68/68 Passed)

All 68 unit, integration, and chaos tests pass:
- `test_save_state_failure_fails_closed`: Verifies that an I/O error during atomic state write raises `BrokerStateUnavailableError` and rolls back identity locks.
- `test_load_state_corrupt_fails_closed`: Verifies that corrupted `state.json` raises `BrokerStateUnavailableError` on router startup.
- `test_pid_reuse_guard_refuses_to_kill_unrelated_process`: Verifies that an unrelated process with PID in `chrome.pid` is never killed.
- `test_orphan_class_s_context_swept`: Verifies that an orphaned `BrowserContext` created outside active leases is swept via CDP while active lease contexts are preserved.
- `test_dead_dedicated_process_invalidates_lease`: Verifies that killing a dedicated Chrome process invalidates the lease and unlocks its identity upon reconcile.
- `test_dead_daemon_invalidates_class_s_leases`: Verifies that killing a Class S daemon invalidates all leases on it and unlocks identities.
- `test_inter_process_state_lock_concurrency`: 4 multiprocessing workers executing concurrent state mutations with zero corruption.

---

## 4. Consultation Questions for Reviewer

1. Do the updated fail-closed mechanisms in `_save_state()` and `_load_state()` satisfy production durability and consistency standards?
2. Are the orphan recovery mechanisms (PID file verification + CDP context sweeping) robust against all crash scenarios (SIGKILL during provisioning, SIGKILL while idle)?
3. Does the PID reuse protection (`_is_matching_chrome_process` + `_is_process_alive`) adequately eliminate false-positive process kills?
4. Do you approve Milestone v2.2.3 for production readiness?
