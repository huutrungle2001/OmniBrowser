"""
Session Router & Execution Class Manager for OmniBrowser.
Coordinates lease requests, browser pools, process boundaries, and scoped execution.

Execution Classes:
- Class S (Shared Daemon, Scoped Context): Multi-tenant, lightweight contexts for read-only/ephemeral tasks.
- Class I (Isolated Ephemeral Process): Dedicated Chrome process for long-running, heavyweight, or untrusted tasks.
- Class A (Account-Bound Process): Dedicated Chrome process for privileged, authenticated account tasks with exclusive leasing.

Invariants Enforced:
- Lease Ownership Invariant (Invariant 8): Valid lease required, cross-lease target access rejected.
- Profile Sanctity Invariant (Invariant 9): ~/.chrome-ai-profile is never mounted as runtime profile.
- Failure Domain Separation (Invariant 10): Process crashes and memory leaks are contained.
- Fail-Closed Durable State & Strict Concurrency: Inter-process flock, atomic writes with fsync, PID reuse protection, full transactional rollback.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any
import uuid

from browser_core.cdp_client import CDPClient, get_browser_ws_url, is_cdp_alive
from browser_core.contracts import (
    AdmissionRejectedError,
    BrokerStateUnavailableError,
    BrowserRequirements,
    ExecutionClass,
    IdentityConflictError,
    Lease,
    LeaseExpiredError,
    LeaseNotFoundError,
)
from .admission_controller import AdmissionController
from .auth_state_vault import AuthStateVault
from .lease_manager import LeaseManager
from .lifecycle_watchdog import LifecycleWatchdog
from .target_registry import TargetRegistry


def _is_matching_chrome_process(pid: int, expected_udir: str | None = None) -> bool:
    """Verifies that a PID is an actual Chrome process and matches expected user_data_dir."""
    try:
        res = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if res.returncode != 0 or not res.stdout:
            return False
        cmd = res.stdout.strip()
        first_token = cmd.split()[0].lower() if cmd else ""
        is_chrome = "chrome" in first_token or "chromium" in first_token or "chrome" in cmd.lower()
        if not is_chrome:
            return False

        if expected_udir is not None:
            expected_flag = f"--user-data-dir={expected_udir}"
            return expected_flag in cmd or expected_udir in cmd
        return True
    except Exception:
        return False


def _is_process_alive(pid: int, popen_obj: subprocess.Popen | None = None) -> bool:
    """Checks if a process is truly alive, handling child zombies and cross-process zombies."""
    if popen_obj is not None:
        return popen_obj.poll() is None
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid != 0:
            return False
    except (ChildProcessError, OSError):
        pass

    try:
        os.kill(pid, 0)
    except OSError:
        return False

    try:
        res = subprocess.run(
            ["ps", "-p", str(pid), "-o", "state="],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        if res.returncode != 0 or not res.stdout:
            return False
        if "Z" in res.stdout.strip():
            return False
    except Exception:
        pass

    return True


def _safe_kill_browser(
    pid: int | None,
    expected_udir: str | None = None,
    proc: subprocess.Popen | None = None,
) -> bool:
    """Safely terminates and kills a browser process with strict PID-reuse and process-identity verification."""
    if proc is not None:
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1)
                return True
        except Exception:
            pass
        return False

    if pid is None or pid <= 1:
        return False

    # Check process identity before sending signals to prevent PID reuse kills
    if not _is_matching_chrome_process(pid, expected_udir):
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        # Give process time to shut down cleanly
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            if not _is_process_alive(pid):
                return True
            time.sleep(0.05)

        # Force kill if still alive and still matches
        if _is_matching_chrome_process(pid, expected_udir):
            os.kill(pid, signal.SIGKILL)
        return True
    except OSError:
        return False


def _scan_running_orphan_chrome_pids(runtime_root: Path, active_user_dirs: set[Path]) -> list[tuple[int, str]]:
    """Scans OS process table for Chrome processes with --user-data-dir in runtime_root not in active_user_dirs."""
    orphans = []
    try:
        res = subprocess.run(
            ["ps", "-eo", "pid,command="],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode != 0 or not res.stdout:
            return orphans
        root_str = str(runtime_root.resolve())
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            cmd = parts[1]
            if "chrome" not in cmd.lower() and "chromium" not in cmd.lower():
                continue
            if "--user-data-dir=" in cmd:
                for token in cmd.split():
                    if token.startswith("--user-data-dir="):
                        udir_str = token.split("=", 1)[1].strip()
                        udir_path = Path(udir_str).resolve()
                        try:
                            if udir_path.is_relative_to(runtime_root.resolve()) and udir_path != runtime_root.resolve():
                                if udir_path not in active_user_dirs:
                                    orphans.append((pid, str(udir_path)))
                        except (ValueError, AttributeError):
                            if str(udir_path).startswith(root_str) and str(udir_path) != root_str:
                                if udir_path not in active_user_dirs:
                                    orphans.append((pid, str(udir_path)))
    except Exception:
        pass
    return orphans


def find_chrome_binary() -> str:
    """Find local Chrome or Chromium executable."""
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)

    # Check Playwright cache directories
    pw_cache = Path.home() / "Library/Caches/ms-playwright"
    if pw_cache.exists():
        for p in pw_cache.glob("**/Chromium.app/Contents/MacOS/Chromium"):
            if p.exists():
                return str(p)
        for p in pw_cache.glob("**/chrome-linux/chrome"):
            if p.exists():
                return str(p)

    which = shutil.which("google-chrome") or shutil.which("chromium")
    if which:
        return which
    raise RuntimeError("No Chrome/Chromium binary found.")


def get_default_broker_dir() -> Path:
    env_dir = os.environ.get("OMNIBROWSER_BROKER_DIR")
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return Path.home() / ".omnibrowser" / "broker"


class SessionRouter:
    """Central broker coordinating lease requests, browser pools, and scoped execution."""

    def __init__(
        self,
        vault_dir: Path | str | None = None,
        state_dir: Path | str | None = None,
        max_concurrent_leases: int = 10,
        min_available_memory_mb: int = 1024,
    ):
        self._lock = threading.RLock()
        self.vault = AuthStateVault(vault_dir)
        self.state_dir = Path(state_dir).expanduser().resolve() if state_dir else get_default_broker_dir()
        # Enforce Invariant 9 on state directory
        self.vault.assert_not_protected_profile(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "state.json"
        self.lock_file = self.state_dir / "state.lock"

        # Dedicated trusted runtime root for ephemeral browser user-data-dirs
        self.runtime_root = self.state_dir / "runtime"
        self.vault.assert_not_protected_profile(self.runtime_root)
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.runtime_root, 0o700)
        except OSError:
            pass

        self.admission = AdmissionController(
            max_concurrent_leases=max_concurrent_leases,
            min_available_memory_mb=min_available_memory_mb,
        )
        self.target_registry = TargetRegistry()
        self.lease_manager = LeaseManager()
        self.watchdog = LifecycleWatchdog(on_recycle_callback=self._handle_daemon_recycle)

        # Class S daemon pool: instance_id -> dict
        # { "instance_id": str, "process": Popen | None, "cdp_url": str, "user_data_dir": str, "is_draining": bool }
        self._class_s_daemons: dict[str, dict[str, Any]] = {}

        # Class I & A process tracking: lease_id -> (subprocess.Popen, temp_dir)
        self._dedicated_processes: dict[str, tuple[subprocess.Popen, str]] = {}
        self._dedicated_processes_data: dict[str, dict[str, Any]] = {}

        # Local leases created by this SessionRouter instance
        self._local_leases: set[str] = set()

        # Pure in-memory reconstruction from disk (NO destructive actions outside lock)
        self._load_state_from_disk()

    @contextmanager
    def _state_lock(self):
        """Inter-process and thread-safe lock for state load-mutate-save operations."""
        with self._lock:
            lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                self._load_state_from_disk()
                self._reconcile_orphans()
                yield
                self._save_state()
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(lock_fd)

    def _save_state(self) -> None:
        """Persist broker state for cross-process CLI invocations (Fail-closed & Durable)."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            daemons_data = {}
            for did, dinfo in self._class_s_daemons.items():
                proc = dinfo.get("process")
                daemons_data[did] = {
                    "instance_id": did,
                    "pid": proc.pid if proc and proc.poll() is None else dinfo.get("pid"),
                    "cdp_url": dinfo.get("cdp_url"),
                    "user_data_dir": dinfo.get("user_data_dir"),
                    "is_draining": dinfo.get("is_draining", False),
                }

            dedicated_data = dict(self._dedicated_processes_data)
            for lid, (proc, udir) in self._dedicated_processes.items():
                if proc.poll() is None:
                    dedicated_data[lid] = {"pid": proc.pid, "user_data_dir": udir}

            state_data = {
                "leases": {
                    lid: lease.to_dict()
                    for lid, lease in self.lease_manager._leases.items()
                },
                "fencing_counter": self.lease_manager._fencing_counter,
                "active_identities": {
                    k: v.to_dict() for k, v in self.lease_manager._active_identities.items()
                },
                "class_s_daemons": daemons_data,
                "dedicated_processes": dedicated_data,
                "targets": [t.to_dict() for t in self.target_registry.get_all_records()],
            }
            temp_file = self.state_dir / f".tmp_state_{os.getpid()}_{uuid.uuid4().hex}.json"
            fd = os.open(temp_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
                f.flush()
                os.fsync(fd)
            temp_file.replace(self.state_file)

            # Strict crash durability: fsync parent directory
            try:
                dir_flags = os.O_RDONLY
                if hasattr(os, "O_DIRECTORY"):
                    dir_flags |= os.O_DIRECTORY
                dir_fd = os.open(str(self.state_dir), dir_flags)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
        except Exception as exc:
            raise BrokerStateUnavailableError(f"Control-plane state persistence failed: {exc}") from exc

    def _reconcile_orphans(self) -> None:
        """Scans and reaps orphaned Chrome processes, dead lease processes, and orphaned BrowserContexts.
        MUST ONLY be called while holding self._state_lock().
        """
        active_user_dirs = set()
        active_pids = set()
        active_class_s_contexts = set()

        for l in self.lease_manager.get_active_leases():
            if l.user_data_dir:
                active_user_dirs.add(Path(l.user_data_dir).resolve())
            if l.process_pid:
                active_pids.add(l.process_pid)
            if l.browser_context_id:
                active_class_s_contexts.add(l.browser_context_id)

        for did, dinfo in list(self._class_s_daemons.items()):
            udir = dinfo.get("user_data_dir")
            if udir:
                active_user_dirs.add(Path(udir).resolve())
            pid = dinfo.get("pid")
            if pid:
                active_pids.add(pid)

        # 1. Scan runtime_root for unreferenced directories & kill uncommitted orphan Chrome PIDs
        now = time.time()
        if self.runtime_root.exists():
            for p in self.runtime_root.iterdir():
                if p.is_dir() and p.resolve() not in active_user_dirs:
                    try:
                        mtime = p.stat().st_mtime
                        pid_file = p / "chrome.pid"
                        if pid_file.exists():
                            try:
                                orphan_pid = int(pid_file.read_text(encoding="utf-8").strip())
                                if now - mtime > 15.0:
                                    _safe_kill_browser(orphan_pid, expected_udir=str(p))
                            except (ValueError, OSError):
                                pass

                        if now - mtime > 30.0:
                            shutil.rmtree(p, ignore_errors=True)
                    except OSError:
                        pass

        # Fallback OS process scan to close the Popen -> chrome.pid SIGKILL race
        orphan_pids = _scan_running_orphan_chrome_pids(self.runtime_root, active_user_dirs)
        for opid, oudir in orphan_pids:
            _safe_kill_browser(opid, expected_udir=oudir)

        # 2. Check active dedicated leases for dead processes (detect dead dedicated Chrome)
        for lid, lease in list(self.lease_manager._leases.items()):
            if lease.is_active and lease.execution_class in (ExecutionClass.CLASS_I, ExecutionClass.CLASS_A):
                if lease.process_pid:
                    proc_info = self._dedicated_processes.get(lid)
                    popen_obj = proc_info[0] if proc_info else None
                    if not _is_matching_chrome_process(lease.process_pid, lease.user_data_dir) or not _is_process_alive(lease.process_pid, popen_obj):
                        # Dead dedicated process!
                        lease.is_active = False
                        if lease.auth_identity:
                            self.lease_manager.rollback_identity(lease.auth_identity, lid)
                        self.target_registry.clear_lease_targets(lid)

        # 3. Check live Class S daemons & sweep orphaned BrowserContexts
        for did, dinfo in list(self._class_s_daemons.items()):
            pid = dinfo.get("pid")
            cdp_url = dinfo.get("cdp_url")
            popen_obj = dinfo.get("process")
            udir = dinfo.get("user_data_dir")
            daemon_dead = False
            if pid:
                if not _is_matching_chrome_process(pid, udir) or not _is_process_alive(pid, popen_obj):
                    daemon_dead = True
            if not cdp_url or not is_cdp_alive(cdp_url):
                daemon_dead = True

            if daemon_dead:
                # Invalidate all leases on this dead daemon BEFORE removing daemon
                for lid, lease in list(self.lease_manager._leases.items()):
                    if lease.is_active and lease.browser_instance_id == did:
                        lease.is_active = False
                        if lease.auth_identity:
                            self.lease_manager.rollback_identity(lease.auth_identity, lid)
                        self.target_registry.clear_lease_targets(lid)
                self._class_s_daemons.pop(did, None)
                self.watchdog.unregister_daemon(did)
                if pid:
                    _safe_kill_browser(pid, expected_udir=udir, proc=popen_obj)
                if udir and os.path.exists(udir):
                    shutil.rmtree(udir, ignore_errors=True)
            else:
                # Sweep orphan BrowserContexts on live daemon
                try:
                    ws_url = get_browser_ws_url(cdp_url)
                    with CDPClient(ws_url) as cdp:
                        bc_res = cdp.request("Target.getBrowserContexts")
                        context_ids = bc_res.get("result", {}).get("browserContextIds", [])
                        for ctx_id in context_ids:
                            if ctx_id not in active_class_s_contexts:
                                cdp.request("Target.disposeBrowserContext", {"browserContextId": ctx_id})
                except Exception:
                    pass

        # 4. Reconcile dead or unreferenced dedicated processes from _dedicated_processes_data
        for lid, pdata in list(self._dedicated_processes_data.items()):
            lease = self.lease_manager.get_lease(lid)
            if not lease or not lease.is_active:
                pid = pdata.get("pid")
                udir = pdata.get("user_data_dir")
                if pid and udir:
                    _safe_kill_browser(pid, expected_udir=udir)
                if udir and os.path.exists(udir):
                    shutil.rmtree(udir, ignore_errors=True)
                self._dedicated_processes_data.pop(lid, None)
                self._dedicated_processes.pop(lid, None)

    def _refresh_daemon_health(self) -> None:
        """Inspects Class S daemons RSS and triggers drain protocol on high memory."""
        for did, dinfo in list(self._class_s_daemons.items()):
            pid = dinfo.get("pid")
            if pid:
                is_draining = self.watchdog.check_memory_and_drain(did)
                if is_draining:
                    dinfo["is_draining"] = True

    def _load_state_from_disk(self) -> None:
        """Pure in-memory reconstruction from disk (Authoritative, Fail-closed, NO destructive actions)."""
        if not self.state_file.exists():
            return
        try:
            state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
            new_leases = {}
            for lid, ldict in state_data.get("leases", {}).items():
                lease = Lease(
                    lease_id=ldict["lease_id"],
                    agent_id=ldict["agent_id"],
                    project_id=ldict["project_id"],
                    execution_class=ldict["execution_class"],
                    fencing_token=ldict["fencing_token"],
                    cdp_url=ldict["cdp_url"],
                    browser_context_id=ldict.get("browser_context_id"),
                    owned_target_ids=ldict.get("owned_target_ids", []),
                    auth_identity=ldict.get("auth_identity"),
                    expires_at=ldict.get("expires_at", 0.0),
                    is_active=ldict.get("is_active", True),
                    process_pid=ldict.get("process_pid"),
                    user_data_dir=ldict.get("user_data_dir"),
                    browser_instance_id=ldict.get("browser_instance_id"),
                )
                new_leases[lid] = lease

            # Authoritative swap (prevents resurrecting revoked leases)
            self.lease_manager._leases = new_leases
            self.lease_manager._fencing_counter = max(
                self.lease_manager._fencing_counter,
                state_data.get("fencing_counter", 1),
            )

            from .lease_manager import IdentityLockRecord
            new_identities = {}
            for k, v in state_data.get("active_identities", {}).items():
                new_identities[k] = IdentityLockRecord.from_dict(v)
            self.lease_manager._active_identities = new_identities

            # Reconstruct targets atomically
            self.target_registry._targets.clear()
            self.target_registry._lease_targets.clear()
            for tdict in state_data.get("targets", []):
                self.target_registry.register_target(
                    target_id=tdict["target_id"],
                    browser_context_id=tdict.get("browser_context_id"),
                    lease_id=tdict["lease_id"],
                    agent_id=tdict["agent_id"],
                    url=tdict.get("url", ""),
                    title=tdict.get("title", ""),
                )

            # Reconstruct daemons pool without dropping dead ones yet (reconciler handles invalidation)
            new_daemons = {}
            daemons_data = state_data.get("class_s_daemons", {})
            for did, dinfo in daemons_data.items():
                existing_proc = self._class_s_daemons.get(did, {}).get("process")
                new_daemons[did] = {
                    "instance_id": did,
                    "process": existing_proc,
                    "pid": dinfo.get("pid"),
                    "cdp_url": dinfo.get("cdp_url"),
                    "user_data_dir": dinfo.get("user_data_dir"),
                    "is_draining": dinfo.get("is_draining", False),
                }
            self._class_s_daemons = new_daemons

            self._dedicated_processes_data = dict(state_data.get("dedicated_processes", {}))
        except Exception as exc:
            raise BrokerStateUnavailableError(f"Failed to load authoritative broker state: {exc}") from exc

    def _ensure_class_s_daemon(self) -> tuple[str, str]:
        """Starts or returns an active Class S daemon. Returns (instance_id, cdp_url)."""
        self._refresh_daemon_health()

        # Look for existing healthy, non-draining daemon
        for did, dinfo in list(self._class_s_daemons.items()):
            cdp_url = dinfo.get("cdp_url")
            if not dinfo.get("is_draining") and not self.watchdog.is_draining(did):
                if cdp_url and is_cdp_alive(cdp_url):
                    return did, cdp_url
                else:
                    self._class_s_daemons.pop(did, None)

        # Launch new daemon with unique instance_id in trusted runtime root
        instance_id = f"class-s-{uuid.uuid4().hex[:8]}"
        chrome_bin = find_chrome_binary()
        temp_dir = tempfile.mkdtemp(prefix=f"omnibrowser_{instance_id}_", dir=self.runtime_root)
        self.vault.assert_not_protected_profile(temp_dir)

        cmd = [
            chrome_bin,
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={temp_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "about:blank",
        ]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Record PID immediately for orphan recovery
        (Path(temp_dir) / "chrome.pid").write_text(str(process.pid), encoding="utf-8")

        active_port_file = Path(temp_dir, "DevToolsActivePort")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not active_port_file.exists():
            if process.poll() is not None:
                raise RuntimeError("Class S Chrome daemon exited prematurely.")
            time.sleep(0.05)

        if not active_port_file.exists():
            _safe_kill_browser(process.pid, temp_dir, proc=process)
            raise RuntimeError("Class S Chrome daemon failed to expose DevToolsActivePort.")

        port = int(active_port_file.read_text(encoding="utf-8").splitlines()[0])
        cdp_url = f"http://127.0.0.1:{port}"

        self._class_s_daemons[instance_id] = {
            "instance_id": instance_id,
            "process": process,
            "pid": process.pid,
            "cdp_url": cdp_url,
            "user_data_dir": temp_dir,
            "is_draining": False,
        }

        self.watchdog.register_daemon(instance_id, process.pid, max_rss_mb=2048)
        return instance_id, cdp_url

    def _handle_daemon_recycle(self, daemon_id: str) -> None:
        """Recycles a drained daemon once all active leases have completed."""
        dinfo = self._class_s_daemons.pop(daemon_id, None)
        if dinfo:
            process = dinfo.get("process")
            pid = dinfo.get("pid")
            temp_dir = dinfo.get("user_data_dir")
            _safe_kill_browser(pid, temp_dir, proc=process)
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            self.watchdog.unregister_daemon(daemon_id)

    def _spawn_dedicated_process(
        self,
        execution_class: str,
        requires_visual: bool,
    ) -> tuple[subprocess.Popen, str, str, str]:
        """Spawns an isolated ephemeral Chrome process. Returns (process, cdp_url, temp_dir, initial_target_id)."""
        chrome_bin = find_chrome_binary()
        temp_dir = tempfile.mkdtemp(prefix=f"omnibrowser_class_{execution_class.lower()}_", dir=self.runtime_root)
        self.vault.assert_not_protected_profile(temp_dir)

        cmd = [
            chrome_bin,
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={temp_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ]
        if not requires_visual:
            cmd.insert(1, "--headless=new")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Record PID immediately for orphan recovery
        (Path(temp_dir) / "chrome.pid").write_text(str(process.pid), encoding="utf-8")

        try:
            active_port_file = Path(temp_dir, "DevToolsActivePort")
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not active_port_file.exists():
                if process.poll() is not None:
                    raise RuntimeError(f"Class {execution_class} Chrome process exited prematurely.")
                time.sleep(0.05)

            if not active_port_file.exists():
                raise RuntimeError(f"Class {execution_class} Chrome process failed to expose DevToolsActivePort.")

            port = int(active_port_file.read_text(encoding="utf-8").splitlines()[0])
            cdp_url = f"http://127.0.0.1:{port}"

            # Deterministically create initial target via CDP (zero timing races or synthetic fallback)
            ws_url = get_browser_ws_url(cdp_url)
            with CDPClient(ws_url) as cdp:
                t_res = cdp.request("Target.createTarget", {"url": "about:blank"})
                real_target_id = t_res["result"]["targetId"]

            return process, cdp_url, temp_dir, real_target_id
        except Exception:
            _safe_kill_browser(process.pid, temp_dir, proc=process)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def request_lease(
        self,
        requirements: BrowserRequirements,
        agent_id: str,
        project_id: str,
    ) -> Lease:
        """Admit and route a lease request according to requirements with reserve-before-provision and full transactional rollback."""
        deadline = time.monotonic() + requirements.timeout_seconds
        last_reason = "Capacity exhausted"

        # Loop with backpressure to acquire admission and provision under state lock (prevent TOCTOU)
        while True:
            temp_lease_id = f"lease-{uuid.uuid4().hex[:12]}"
            created_context_id: str | None = None
            created_cdp_url: str | None = None
            spawned_proc_info: tuple[subprocess.Popen, str] | None = None

            try:
                with self._state_lock():
                    admitted, reason = self.admission.check_admission(
                        requirements,
                        current_active_leases=self.lease_manager.get_active_lease_count(),
                    )
                    if admitted:
                        # 2. Reserve identity before creating Chrome resources (prevent resource leaks)
                        if requirements.auth_identity:
                            self.lease_manager.reserve_identity(
                                requirements.auth_identity,
                                requirements.exclusive_identity,
                                temp_lease_id,
                            )

                        try:
                            # 3. Route and provision based on execution class
                            if requirements.execution_class == ExecutionClass.CLASS_S:
                                instance_id, cdp_url = self._ensure_class_s_daemon()
                                created_cdp_url = cdp_url
                                ws_url = get_browser_ws_url(cdp_url)

                                with CDPClient(ws_url) as cdp:
                                    ctx_res = cdp.request("Target.createBrowserContext")
                                    context_id = ctx_res["result"]["browserContextId"]
                                    created_context_id = context_id
                                    t_res = cdp.request("Target.createTarget", {"url": "about:blank", "browserContextId": context_id})
                                    target_id = t_res["result"]["targetId"]

                                dinfo = self._class_s_daemons[instance_id]
                                lease = self.lease_manager.create_lease(
                                    agent_id=agent_id,
                                    project_id=project_id,
                                    execution_class=ExecutionClass.CLASS_S,
                                    cdp_url=cdp_url,
                                    timeout_seconds=requirements.timeout_seconds,
                                    auth_identity=requirements.auth_identity,
                                    exclusive_identity=requirements.exclusive_identity,
                                    browser_context_id=context_id,
                                    process_pid=dinfo.get("pid"),
                                    user_data_dir=dinfo.get("user_data_dir"),
                                    pre_reserved_lease_id=temp_lease_id,
                                    browser_instance_id=instance_id,
                                )

                                # Register initial target
                                self.target_registry.register_target(
                                    target_id=target_id,
                                    browser_context_id=context_id,
                                    lease_id=lease.lease_id,
                                    agent_id=agent_id,
                                    url="about:blank",
                                    title="about:blank",
                                )
                                lease.owned_target_ids.append(target_id)
                                self._local_leases.add(lease.lease_id)
                                return lease

                            elif requirements.execution_class in (ExecutionClass.CLASS_I, ExecutionClass.CLASS_A):
                                requires_visual = requirements.requires_visual or requirements.execution_class == ExecutionClass.CLASS_A
                                process, cdp_url, temp_dir, real_target_id = self._spawn_dedicated_process(
                                    requirements.execution_class,
                                    requires_visual=requires_visual,
                                )
                                spawned_proc_info = (process, temp_dir)

                                lease = self.lease_manager.create_lease(
                                    agent_id=agent_id,
                                    project_id=project_id,
                                    execution_class=requirements.execution_class,
                                    cdp_url=cdp_url,
                                    timeout_seconds=requirements.timeout_seconds,
                                    auth_identity=requirements.auth_identity,
                                    exclusive_identity=requirements.exclusive_identity,
                                    process_pid=process.pid,
                                    user_data_dir=temp_dir,
                                    pre_reserved_lease_id=temp_lease_id,
                                    browser_instance_id=f"dedicated-{process.pid}",
                                )

                                self._dedicated_processes[lease.lease_id] = (process, temp_dir)
                                self._dedicated_processes_data[lease.lease_id] = {"pid": process.pid, "user_data_dir": temp_dir}

                                # Register real target
                                self.target_registry.register_target(
                                    target_id=real_target_id,
                                    browser_context_id=None,
                                    lease_id=lease.lease_id,
                                    agent_id=agent_id,
                                    url="about:blank",
                                    title="about:blank",
                                )
                                lease.owned_target_ids.append(real_target_id)
                                self._local_leases.add(lease.lease_id)
                                return lease

                            else:
                                raise ValueError(f"Unknown execution class: {requirements.execution_class}")

                        except Exception:
                            # Rollback identity reservation on provisioning failure
                            if requirements.auth_identity:
                                self.lease_manager.rollback_identity(requirements.auth_identity, temp_lease_id)
                            raise
                    else:
                        last_reason = reason
            except Exception:
                # Full transactional rollback on any failure (including save failure)
                if requirements.auth_identity:
                    self.lease_manager.rollback_identity(requirements.auth_identity, temp_lease_id)
                self.lease_manager._leases.pop(temp_lease_id, None)
                self.target_registry.clear_lease_targets(temp_lease_id)
                self._local_leases.discard(temp_lease_id)
                self._dedicated_processes.pop(temp_lease_id, None)
                self._dedicated_processes_data.pop(temp_lease_id, None)
                if created_context_id and created_cdp_url and is_cdp_alive(created_cdp_url):
                    try:
                        ws_url = get_browser_ws_url(created_cdp_url)
                        with CDPClient(ws_url) as cdp:
                            cdp.request("Target.disposeBrowserContext", {"browserContextId": created_context_id})
                    except Exception:
                        pass
                if spawned_proc_info:
                    proc, udir = spawned_proc_info
                    _safe_kill_browser(proc.pid, udir, proc=proc)
                    if os.path.exists(udir):
                        shutil.rmtree(udir, ignore_errors=True)
                raise

            if time.monotonic() >= deadline:
                raise AdmissionRejectedError(f"Timed out waiting for admission: {last_reason}")
            time.sleep(0.1)

    def release_lease(self, lease_id: str) -> None:
        """Releases and tears down resources associated with a lease."""
        with self._state_lock():
            lease = self.lease_manager.revoke_lease(lease_id)
            if not lease:
                return

            self._local_leases.discard(lease_id)

            # Clean up Class S browser context
            if lease.execution_class == ExecutionClass.CLASS_S and lease.browser_context_id:
                try:
                    if is_cdp_alive(lease.cdp_url):
                        ws_url = get_browser_ws_url(lease.cdp_url)
                        with CDPClient(ws_url) as cdp:
                            cdp.request("Target.disposeBrowserContext", {"browserContextId": lease.browser_context_id})
                except Exception:
                    pass

                # Check daemon drain completion by browser_instance_id or cdp_url
                daemon_id = lease.browser_instance_id
                if daemon_id and daemon_id in self._class_s_daemons:
                    active_on_daemon = sum(
                        1 for l in self.lease_manager.get_active_leases()
                        if l.browser_instance_id == daemon_id
                    )
                    self.watchdog.check_drain_completion(daemon_id, active_on_daemon)
                else:
                    for did, dinfo in list(self._class_s_daemons.items()):
                        if dinfo.get("cdp_url") == lease.cdp_url:
                            active_on_daemon = sum(
                                1 for l in self.lease_manager.get_active_leases()
                                if l.execution_class == ExecutionClass.CLASS_S and l.cdp_url == lease.cdp_url
                            )
                            self.watchdog.check_drain_completion(did, active_on_daemon)

            # Clean up Class I / A dedicated processes (in-memory or cross-process)
            proc_info = self._dedicated_processes.pop(lease_id, None)
            if proc_info:
                process, temp_dir = proc_info
                _safe_kill_browser(process.pid, temp_dir, proc=process)
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

            # Clean up from persisted process data
            pdata = self._dedicated_processes_data.pop(lease_id, None)
            if pdata and not proc_info:
                pid = pdata.get("pid")
                temp_dir = pdata.get("user_data_dir")
                if pid and temp_dir:
                    _safe_kill_browser(pid, temp_dir)
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

            self.target_registry.clear_lease_targets(lease_id)

    def status(self) -> dict[str, Any]:
        """Returns comprehensive status of all broker subsystems."""
        with self._state_lock():
            self._refresh_daemon_health()
            active_leases = self.lease_manager.get_active_leases()
            return {
                "telemetry": self.admission.get_system_telemetry(),
                "active_leases_count": len(active_leases),
                "active_leases": [l.to_dict() for l in active_leases],
                "daemons": self.watchdog.get_status(),
                "registered_targets_count": len(self.target_registry.get_all_records()),
                "vault_snapshots_count": len(self.vault.list_snapshots()),
            }

    def close(self) -> None:
        """Cleanly releases only locally acquired leases for this SessionRouter instance."""
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

    def shutdown_broker(self) -> None:
        """Global administrative shutdown: releases ALL active leases and recycles all daemons."""
        with self._state_lock():
            active_ids = [l.lease_id for l in self.lease_manager.get_active_leases()]
            for lid in active_ids:
                try:
                    self.release_lease(lid)
                except Exception:
                    pass
            self._local_leases.clear()
            for did in list(self._class_s_daemons.keys()):
                self._handle_daemon_recycle(did)
