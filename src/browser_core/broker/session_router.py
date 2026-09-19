"""Session Router: Orchestrator for the Hybrid Bulkheaded Browser Pool.

Routes incoming agent requests to:
- Class S (Shared Multi-Context): Warm headless daemon pool, ephemeral BrowserContext (<10ms startup, ~20-40MB RAM).
- Class I (Isolated Dedicated Ephemeral): Independent Chrome process with dynamic port & temp user-data-dir.
- Class A (Authenticated & Interactive): Dedicated headful or snapshot-injected runtime with exclusive identity lease.

Coordinates AdmissionController, LeaseManager, TargetRegistry, AuthStateVault, and LifecycleWatchdog.
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
import tempfile
import threading
import time
from typing import Any
import urllib.request
import uuid

from ..cdp_client import CDPClient, get_browser_ws_url, is_cdp_alive
from ..contracts import (
    AdmissionRejectedError,
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

        # Load persisted state
        self._load_state()

    @contextmanager
    def _state_lock(self):
        """Inter-process and thread-safe lock for state load-mutate-save operations."""
        with self._lock:
            lock_fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                self._load_state()
                yield
                self._save_state()
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(lock_fd)

    def _save_state(self) -> None:
        """Persist broker state for cross-process CLI invocations."""
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
            temp_file.write_text(json.dumps(state_data, indent=2), encoding="utf-8")
            temp_file.replace(self.state_file)
        except Exception:
            pass

    def _load_state(self) -> None:
        """Load persisted broker state."""
        if not self.state_file.exists():
            return
        try:
            state_data = json.loads(self.state_file.read_text(encoding="utf-8"))
            for lid, ldict in state_data.get("leases", {}).items():
                if lid not in self.lease_manager._leases:
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
                    )
                    self.lease_manager._leases[lid] = lease

            self.lease_manager._fencing_counter = max(
                self.lease_manager._fencing_counter,
                state_data.get("fencing_counter", 1),
            )

            from .lease_manager import IdentityLockRecord
            for k, v in state_data.get("active_identities", {}).items():
                if k not in self.lease_manager._active_identities:
                    self.lease_manager._active_identities[k] = IdentityLockRecord.from_dict(v)

            for tdict in state_data.get("targets", []):
                self.target_registry.register_target(
                    target_id=tdict["target_id"],
                    browser_context_id=tdict.get("browser_context_id"),
                    lease_id=tdict["lease_id"],
                    agent_id=tdict["agent_id"],
                    url=tdict.get("url", ""),
                    title=tdict.get("title", ""),
                )

            # Reconstruct daemons pool
            daemons_data = state_data.get("class_s_daemons", {})
            # Backward compatibility for legacy single class_s_daemon
            legacy_daemon = state_data.get("class_s_daemon")
            if legacy_daemon and "class-s-default" not in daemons_data:
                daemons_data["class-s-default"] = legacy_daemon

            for did, dinfo in daemons_data.items():
                if did not in self._class_s_daemons:
                    pid = dinfo.get("pid")
                    cdp_url = dinfo.get("cdp_url")
                    if pid and cdp_url:
                        try:
                            os.kill(pid, 0)
                            if is_cdp_alive(cdp_url):
                                self._class_s_daemons[did] = {
                                    "instance_id": did,
                                    "process": None,
                                    "pid": pid,
                                    "cdp_url": cdp_url,
                                    "user_data_dir": dinfo.get("user_data_dir"),
                                    "is_draining": dinfo.get("is_draining", False),
                                }
                                self.watchdog.register_daemon(did, pid, max_rss_mb=2048)
                                if dinfo.get("is_draining"):
                                    self.watchdog.mark_draining(did, "Persisted draining state")
                        except OSError:
                            pass

            self._dedicated_processes_data.update(state_data.get("dedicated_processes", {}))
        except Exception:
            pass

    def _ensure_class_s_daemon(self) -> tuple[str, str]:
        """Starts or returns an active Class S daemon. Returns (instance_id, cdp_url)."""
        # Look for existing healthy, non-draining daemon
        for did, dinfo in list(self._class_s_daemons.items()):
            cdp_url = dinfo.get("cdp_url")
            if not dinfo.get("is_draining") and not self.watchdog.is_draining(did):
                if cdp_url and is_cdp_alive(cdp_url):
                    return did, cdp_url
                else:
                    # Stale daemon
                    self._class_s_daemons.pop(did, None)

        # Launch new daemon with unique instance_id
        instance_id = f"class-s-{uuid.uuid4().hex[:8]}"
        chrome_bin = find_chrome_binary()
        temp_dir = tempfile.mkdtemp(prefix=f"omnibrowser_{instance_id}_")
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

        active_port_file = Path(temp_dir, "DevToolsActivePort")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not active_port_file.exists():
            if process.poll() is not None:
                raise RuntimeError("Class S Chrome daemon exited prematurely.")
            time.sleep(0.05)

        if not active_port_file.exists():
            process.terminate()
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
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
            elif pid:
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(0.1)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

            temp_dir = dinfo.get("user_data_dir")
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
        temp_dir = tempfile.mkdtemp(prefix=f"omnibrowser_class_{execution_class.lower()}_")
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

        active_port_file = Path(temp_dir, "DevToolsActivePort")
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not active_port_file.exists():
            if process.poll() is not None:
                raise RuntimeError(f"Class {execution_class} Chrome process exited prematurely.")
            time.sleep(0.05)

        if not active_port_file.exists():
            process.terminate()
            raise RuntimeError(f"Class {execution_class} Chrome process failed to expose DevToolsActivePort.")

        port = int(active_port_file.read_text(encoding="utf-8").splitlines()[0])
        cdp_url = f"http://127.0.0.1:{port}"

        # Query real initial target ID from Chrome
        initial_target_id = f"target-{time.time_ns()}"
        try:
            req = urllib.request.Request(f"{cdp_url}/json/list")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            for t in targets:
                if t.get("type") == "page" and t.get("id"):
                    initial_target_id = t["id"]
                    break
        except Exception:
            pass

        return process, cdp_url, temp_dir, initial_target_id

    def request_lease(
        self,
        requirements: BrowserRequirements,
        agent_id: str,
        project_id: str,
    ) -> Lease:
        """Admit and route a lease request according to requirements with reserve-before-provision."""
        # 1. Admission control & backpressure
        self.admission.acquire_admission(
            requirements,
            get_active_lease_count_fn=self.lease_manager.get_active_lease_count,
            timeout_seconds=5.0,
        )

        with self._state_lock():
            temp_lease_id = f"lease-{uuid.uuid4().hex[:12]}"

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
                    ws_url = get_browser_ws_url(cdp_url)

                    with CDPClient(ws_url) as cdp:
                        ctx_res = cdp.request("Target.createBrowserContext")
                        context_id = ctx_res["result"]["browserContextId"]
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
                    return lease

                elif requirements.execution_class in (ExecutionClass.CLASS_I, ExecutionClass.CLASS_A):
                    requires_visual = requirements.requires_visual or requirements.execution_class == ExecutionClass.CLASS_A
                    process, cdp_url, temp_dir, real_target_id = self._spawn_dedicated_process(
                        requirements.execution_class,
                        requires_visual=requires_visual,
                    )

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
                    return lease

                else:
                    raise ValueError(f"Unknown execution class: {requirements.execution_class}")

            except Exception:
                # Rollback identity reservation on provisioning failure
                if requirements.auth_identity:
                    self.lease_manager.rollback_identity(requirements.auth_identity, temp_lease_id)
                raise

    def release_lease(self, lease_id: str) -> None:
        """Releases and tears down resources associated with a lease."""
        with self._state_lock():
            lease = self.lease_manager.revoke_lease(lease_id)
            if not lease:
                return

            # Clean up Class S browser context
            if lease.execution_class == ExecutionClass.CLASS_S and lease.browser_context_id:
                try:
                    if is_cdp_alive(lease.cdp_url):
                        ws_url = get_browser_ws_url(lease.cdp_url)
                        with CDPClient(ws_url) as cdp:
                            cdp.request("Target.disposeBrowserContext", {"browserContextId": lease.browser_context_id})
                except Exception:
                    pass

                # Check daemon drain completion for all daemons
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
                try:
                    process.terminate()
                    process.wait(timeout=3)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

            # Clean up from persisted process data
            pdata = self._dedicated_processes_data.pop(lease_id, None)
            if pdata and not proc_info:
                pid = pdata.get("pid")
                if pid:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(0.1)
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                temp_dir = pdata.get("user_data_dir")
                if temp_dir and os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)

            self.target_registry.clear_lease_targets(lease_id)

    def status(self) -> dict[str, Any]:
        """Returns comprehensive status of all broker subsystems."""
        with self._state_lock():
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
        """Tears down all active leases and daemons cleanly."""
        with self._state_lock():
            active_ids = [l.lease_id for l in self.lease_manager.get_active_leases()]
            for lid in active_ids:
                try:
                    self.release_lease(lid)
                except Exception:
                    pass

            for did in list(self._class_s_daemons.keys()):
                self._handle_daemon_recycle(did)
