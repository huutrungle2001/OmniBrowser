"""Session Router: Orchestrator for the Hybrid Bulkheaded Browser Pool.

Routes incoming agent requests to:
- Class S (Shared Multi-Context): Warm headless daemon, ephemeral BrowserContext (<50ms startup, ~20-40MB RAM).
- Class I (Isolated Dedicated Ephemeral): Independent Chrome process with dynamic port & temp user-data-dir.
- Class A (Authenticated & Interactive): Dedicated headful or snapshot-injected runtime with exclusive identity lease.

Coordinates AdmissionController, LeaseManager, TargetRegistry, AuthStateVault, and LifecycleWatchdog.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any

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
        self.state_file = self.state_dir / "state.json"
        self.admission = AdmissionController(
            max_concurrent_leases=max_concurrent_leases,
            min_available_memory_mb=min_available_memory_mb,
        )
        self.target_registry = TargetRegistry()
        self.lease_manager = LeaseManager()
        self.watchdog = LifecycleWatchdog(on_recycle_callback=self._handle_daemon_recycle)

        # Class S daemon state
        self._class_s_process: subprocess.Popen | None = None
        self._class_s_dir: str | None = None
        self._class_s_cdp_url: str | None = None

        # Class I & A process tracking: lease_id -> (subprocess.Popen, temp_dir)
        self._dedicated_processes: dict[str, tuple[subprocess.Popen, str]] = {}

        # Load persisted state
        self._load_state()

    def _save_state(self) -> None:
        """Persist broker state for cross-process CLI invocations."""
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            state_data = {
                "leases": {
                    lid: lease.to_dict()
                    for lid, lease in self.lease_manager._leases.items()
                },
                "fencing_counter": self.lease_manager._fencing_counter,
                "active_identities": {
                    k: [v[0], v[1]] for k, v in self.lease_manager._active_identities.items()
                },
                "class_s_daemon": {
                    "pid": self._class_s_process.pid if self._class_s_process and self._class_s_process.poll() is None else None,
                    "cdp_url": self._class_s_cdp_url,
                    "user_data_dir": self._class_s_dir,
                } if self._class_s_cdp_url else None,
                "dedicated_processes": {
                    lid: {"pid": proc.pid, "user_data_dir": udir}
                    for lid, (proc, udir) in self._dedicated_processes.items()
                    if proc.poll() is None
                },
                "targets": [t.to_dict() for t in self.target_registry.get_all_records()],
            }
            temp_file = self.state_dir / f".tmp_state_{os.getpid()}_{time.time_ns()}.json"
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

            for k, v in state_data.get("active_identities", {}).items():
                if k not in self.lease_manager._active_identities:
                    self.lease_manager._active_identities[k] = (v[0], v[1])

            for tdict in state_data.get("targets", []):
                self.target_registry.register_target(
                    target_id=tdict["target_id"],
                    browser_context_id=tdict.get("browser_context_id"),
                    lease_id=tdict["lease_id"],
                    agent_id=tdict["agent_id"],
                    url=tdict.get("url", ""),
                    title=tdict.get("title", ""),
                )

            s_daemon = state_data.get("class_s_daemon")
            if s_daemon and s_daemon.get("cdp_url") and s_daemon.get("pid"):
                pid = s_daemon["pid"]
                try:
                    os.kill(pid, 0)
                    cdp_url = s_daemon["cdp_url"]
                    if is_cdp_alive(cdp_url):
                        self._class_s_cdp_url = cdp_url
                        self._class_s_dir = s_daemon.get("user_data_dir")
                except OSError:
                    pass
        except Exception:
            pass

    def _ensure_class_s_daemon(self) -> str:
        """Starts or returns the warm Class S headless daemon CDP URL."""
        with self._lock:
            if self._class_s_cdp_url and is_cdp_alive(self._class_s_cdp_url):
                if not self.watchdog.is_draining("class-s-daemon"):
                    return self._class_s_cdp_url

            # Launch warm daemon
            chrome_bin = find_chrome_binary()
            self._class_s_dir = tempfile.mkdtemp(prefix="omnibrowser_class_s_")
            self.vault.assert_not_protected_profile(self._class_s_dir)

            cmd = [
                chrome_bin,
                "--headless=new",
                "--remote-debugging-address=127.0.0.1",
                "--remote-debugging-port=0",
                f"--user-data-dir={self._class_s_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-background-timer-throttling",
                "--disable-renderer-backgrounding",
                "about:blank",
            ]
            self._class_s_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Wait for DevToolsActivePort
            active_port_file = Path(self._class_s_dir, "DevToolsActivePort")
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and not active_port_file.exists():
                if self._class_s_process.poll() is not None:
                    raise RuntimeError("Class S Chrome daemon exited prematurely.")
                time.sleep(0.05)

            if not active_port_file.exists():
                self._class_s_process.terminate()
                raise RuntimeError("Class S Chrome daemon failed to expose DevToolsActivePort.")

            port = int(active_port_file.read_text(encoding="utf-8").splitlines()[0])
            self._class_s_cdp_url = f"http://127.0.0.1:{port}"

            # Register with watchdog
            self.watchdog.register_daemon("class-s-daemon", self._class_s_process.pid, max_rss_mb=2048)
            self._save_state()
            return self._class_s_cdp_url

    def _handle_daemon_recycle(self, daemon_id: str) -> None:
        """Recycles a drained daemon once all active leases have completed."""
        with self._lock:
            if daemon_id == "class-s-daemon":
                if self._class_s_process:
                    try:
                        self._class_s_process.terminate()
                        self._class_s_process.wait(timeout=3)
                    except Exception:
                        try:
                            self._class_s_process.kill()
                        except Exception:
                            pass
                    self._class_s_process = None
                if self._class_s_dir and os.path.exists(self._class_s_dir):
                    shutil.rmtree(self._class_s_dir, ignore_errors=True)
                    self._class_s_dir = None
                self._class_s_cdp_url = None
                self.watchdog.unregister_daemon("class-s-daemon")

    def _spawn_dedicated_process(
        self,
        execution_class: str,
        requires_visual: bool,
    ) -> tuple[subprocess.Popen, str, str]:
        """Spawns an isolated ephemeral Chrome process. Returns (process, cdp_url, temp_dir)."""
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
        return process, cdp_url, temp_dir

    def request_lease(
        self,
        requirements: BrowserRequirements,
        agent_id: str,
        project_id: str,
    ) -> Lease:
        """Admit and route a lease request according to requirements."""
        # 1. Admission control & backpressure
        self.admission.acquire_admission(
            requirements,
            get_active_lease_count_fn=self.lease_manager.get_active_lease_count,
            timeout_seconds=5.0,
        )

        with self._lock:
            # Route based on execution class
            if requirements.execution_class == ExecutionClass.CLASS_S:
                cdp_url = self._ensure_class_s_daemon()
                ws_url = get_browser_ws_url(cdp_url)

                with CDPClient(ws_url) as cdp:
                    ctx_res = cdp.request("Target.createBrowserContext")
                    context_id = ctx_res["result"]["browserContextId"]
                    t_res = cdp.request("Target.createTarget", {"url": "about:blank", "browserContextId": context_id})
                    target_id = t_res["result"]["targetId"]

                lease = self.lease_manager.create_lease(
                    agent_id=agent_id,
                    project_id=project_id,
                    execution_class=ExecutionClass.CLASS_S,
                    cdp_url=cdp_url,
                    timeout_seconds=requirements.timeout_seconds,
                    auth_identity=requirements.auth_identity,
                    exclusive_identity=requirements.exclusive_identity,
                    browser_context_id=context_id,
                    process_pid=self._class_s_process.pid if self._class_s_process else None,
                    user_data_dir=self._class_s_dir,
                )

                # Register initial page target
                self.target_registry.register_target(
                    target_id=target_id,
                    browser_context_id=context_id,
                    lease_id=lease.lease_id,
                    agent_id=agent_id,
                    url="about:blank",
                    title="about:blank",
                )
                lease.owned_target_ids.append(target_id)
                self._save_state()
                return lease

            elif requirements.execution_class in (ExecutionClass.CLASS_I, ExecutionClass.CLASS_A):
                requires_visual = requirements.requires_visual or requirements.execution_class == ExecutionClass.CLASS_A
                process, cdp_url, temp_dir = self._spawn_dedicated_process(
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
                )

                self._dedicated_processes[lease.lease_id] = (process, temp_dir)

                # Register initial target
                target_id = f"target-{time.time_ns()}"
                self.target_registry.register_target(
                    target_id=target_id,
                    browser_context_id=None,
                    lease_id=lease.lease_id,
                    agent_id=agent_id,
                    url="about:blank",
                    title="about:blank",
                )
                lease.owned_target_ids.append(target_id)
                self._save_state()
                return lease

            else:
                raise ValueError(f"Unknown execution class: {requirements.execution_class}")

    def release_lease(self, lease_id: str) -> None:
        """Releases and tears down resources associated with a lease."""
        with self._lock:
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

                # Check daemon drain completion
                active_s_leases = sum(
                    1 for l in self.lease_manager.get_active_leases()
                    if l.execution_class == ExecutionClass.CLASS_S
                )
                self.watchdog.check_drain_completion("class-s-daemon", active_s_leases)

            # Clean up Class I / A dedicated processes
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

            self.target_registry.clear_lease_targets(lease_id)
            self._save_state()

    def status(self) -> dict[str, Any]:
        """Returns comprehensive status of all broker subsystems."""
        with self._lock:
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
        with self._lock:
            active_ids = [l.lease_id for l in self.lease_manager.get_active_leases()]
            for lid in active_ids:
                try:
                    self.release_lease(lid)
                except Exception:
                    pass

            self._handle_daemon_recycle("class-s-daemon")
            self._save_state()
