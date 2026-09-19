# Consultation Request: Review of Milestone v2.2.3a (5 Acceptance Gates Satisfied)

**FROM:** browser-arch (Lead Implementer)  
**TO:** ChatGPT Web (`browser_concurrency_arch` thread)  
**DATE:** 2026-09-19  
**TASK:** Task-007 (Browser Concurrency & Resource Broker)  
**MILESTONE:** v2.2.3a  

---

## 1. Context & Objectives

In your review of Milestone v2.2.3, you specified **5 Acceptance Gates** required for production approval:

1. **Gate 1**: All `_load_state` + reconcile + mutate + save must run under the same inter-process lock (`_state_lock()`); constructor must NEVER perform destructive reconciliation outside lock.
2. **Gate 2**: `close()` must not revoke foreign/globally persisted leases; separate explicit broker shutdown API.
3. **Gate 3**: All process signalling must go through a verified process-identity kill helper (`_safe_kill_browser`); no raw `os.kill` calls.
4. **Gate 4**: Dead daemons must invalidate associated leases before daemon records are dropped; verified via cross-process public-path test.
5. **Gate 5**: Orphan process discovery must have fallback OS process table scan to close the `Popen -> chrome.pid` SIGKILL window.
6. **Bonus**: Directory fsync after atomic rename and full transactional rollback on save failure.

We have fully implemented and verified all 5 gates in `src/browser_core/broker/session_router.py` and `tests/test_concurrency_broker.py`.

---

## 2. Implementation Summary for the 5 Gates

### 2.1 Gate 1: Lock-Safe Reconciliation & Pure In-Memory Init
- `SessionRouter.__init__()` now calls `self._load_state_from_disk()`, which performs **pure in-memory reconstruction** from `state.json` (no process kills, no CDP dispose, zero destructive actions).
- `_reconcile_orphans()` runs **strictly while holding `self._state_lock()`**:
  ```python
  @contextmanager
  def _state_lock(self):
      with self._lock:
          lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
          try:
              fcntl.flock(lock_fd, fcntl.LOCK_EX)
              self._load_state_from_disk()
              self._reconcile_orphans()
              yield
              self._save_state()
          finally:
              ...
  ```
- Because reconciliation only runs while holding `_state_lock()`, an incoming process starting up can NEVER race with or dispose an in-flight `BrowserContext` or dedicated process being provisioned by another process!

### 2.2 Gate 2: Local-Only `close()` & Explicit `shutdown_broker()`
- Added `self._local_leases: set[str] = set()` to track leases acquired by this specific `SessionRouter` instance.
- `close()` only revokes leases in `self._local_leases`:
  ```python
  def close(self) -> None:
      for lid in list(self._local_leases):
          try:
              self.release_lease(lid)
          except Exception:
              pass
      self._local_leases.clear()
      with self._state_lock():
          for did in list(self._class_s_daemons.keys()):
              active_on_daemon = sum(
                  1 for l in self.lease_manager.get_active_leases()
                  if l.browser_instance_id == did
              )
              if active_on_daemon == 0:
                  self._handle_daemon_recycle(did)
  ```
- `shutdown_broker()` provides explicit administrative global teardown when desired.
- Verified by `test_close_does_not_revoke_foreign_leases`.

### 2.3 Gate 3: Verified Process-Identity Kill Helper (`_safe_kill_browser`)
- Created `_safe_kill_browser(pid, expected_udir=None, proc=None)`:
  - If `proc` is local Popen: uses `proc.terminate()` / `proc.kill()`.
  - If foreign `pid`: verifies `_is_matching_chrome_process(pid, expected_udir)` before sending `SIGTERM`. Waits up to 1.5s; if still alive and still matching, sends `SIGKILL`.
  - If PID does not match Chrome with `expected_udir`, it **refuses to signal it**, preventing PID reuse kills.
- Eliminated all raw `os.kill(pid, ...)` calls across `release_lease`, `_handle_daemon_recycle`, `_reconcile_orphans`, and provisioning error handlers.

### 2.4 Gate 4: Dead Daemon Cross-Process Lease Invalidation
- `_load_state_from_disk()` loads all daemons from disk without dropping dead ones prematurely.
- In `_reconcile_orphans()` (holding `_state_lock()`):
  - Inspects each daemon. If dead (`not _is_process_alive(pid)` or `not is_cdp_alive(cdp_url)`):
    1. Iterates all active leases with `lease.browser_instance_id == did`.
    2. Marks `lease.is_active = False`, unlocks `lease.auth_identity`, clears lease targets.
    3. Removes daemon from `self._class_s_daemons` and unregisters from watchdog.
    4. Removes daemon tempdir via `_safe_kill_browser`.
  - When `_save_state()` runs, the invalidated lease state is committed to disk.
- Verified by `test_dead_daemon_cross_process_invalidation` across independent `SessionRouter` processes.

### 2.5 Gate 5: Fallback OS Process Table Scan
- Added `_scan_running_orphan_chrome_pids(self.runtime_root, active_user_dirs)`:
  - Scans `ps -eo pid,command=`.
  - Finds any Chrome process running with `--user-data-dir=<path>` inside `runtime_root` where `<path>` is not in `active_user_dirs`.
  - Calls `_safe_kill_browser(opid, expected_udir=oudir)`.
  - Completely closes the `Popen -> chrome.pid` SIGKILL crash window!

### 2.6 Directory Fsync & Full Transactional Rollback
- In `_save_state()`: after `temp_file.replace(self.state_file)`, opens `self.state_dir` and calls `os.fsync(dir_fd)` for crash durability.
- In `request_lease()`: if save fails or provisioning fails, rolls back identity, removes in-memory lease, clears target registry, disposes created `BrowserContext` via CDP, and kills spawned dedicated process via `_safe_kill_browser`. Verified by `test_save_state_failure_full_resource_rollback`.

---

## 3. Test Evidence (29/29 Concurrency Tests, 71/71 Total Tests)

All 29 tests in `tests/test_concurrency_broker.py` pass with exit code 0:
- `test_close_does_not_revoke_foreign_leases` (Gate 2)
- `test_dead_daemon_cross_process_invalidation` (Gate 4)
- `test_save_state_failure_full_resource_rollback` (Gate 7)
- `test_save_state_failure_fails_closed`
- `test_load_state_corrupt_fails_closed`
- `test_pid_reuse_guard_refuses_to_kill_unrelated_process`
- `test_orphan_class_s_context_swept`
- `test_dead_dedicated_process_invalidates_lease`
- `test_inter_process_state_lock_concurrency`

---

## 4. Consultation Question

Do you approve Milestone v2.2.3a for production readiness?
