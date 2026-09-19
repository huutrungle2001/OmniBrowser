"""OmniBrowser Concurrency Broker Subsystem.

Provides:
- SessionRouter: Central orchestrator for the Hybrid Bulkheaded Browser Pool.
- LeaseManager: Lifecycle, fencing tokens, and identity concurrency locks.
- TargetRegistry: Virtual scoped tabs and target ownership management.
- AdmissionController: System telemetry and resource backpressure.
- AuthStateVault: Single-Writer / Multi-Reader snapshot storage (chmod 600).
- LifecycleWatchdog: Health monitoring and Drain Protocol.
"""

from .admission_controller import AdmissionController
from .auth_state_vault import AuthStateVault
from .lease_manager import LeaseManager
from .lifecycle_watchdog import DaemonState, LifecycleWatchdog
from .session_router import SessionRouter, find_chrome_binary
from .target_registry import TargetRecord, TargetRegistry

__all__ = [
    "AdmissionController",
    "AuthStateVault",
    "DaemonState",
    "LeaseManager",
    "LifecycleWatchdog",
    "SessionRouter",
    "TargetRecord",
    "TargetRegistry",
    "find_chrome_binary",
]
