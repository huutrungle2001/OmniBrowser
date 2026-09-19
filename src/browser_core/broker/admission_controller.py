"""Admission Controller: Backpressure and resource-aware scheduling.

Applies backpressure based on macOS system telemetry (RAM pressure, CPU load)
and active lease limits, preventing memory exhaustion on developer workstations.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

from ..contracts import AdmissionRejectedError, BrowserRequirements, ExecutionClass


class AdmissionController:
    """Controls whether new browser leases can be admitted based on system capacity."""

    def __init__(
        self,
        max_concurrent_leases: int = 10,
        min_available_memory_mb: int = 1024,
        max_cpu_load_ratio: float = 2.5,
    ):
        self.max_concurrent_leases = max_concurrent_leases
        self.min_available_memory_mb = min_available_memory_mb
        self.max_cpu_load_ratio = max_cpu_load_ratio

    def get_system_telemetry(self) -> dict[str, Any]:
        """Collects memory and CPU metrics from macOS / Linux."""
        available_mb = self._get_available_memory_mb()
        cpu_count = os.cpu_count() or 4
        try:
            load_1, load_5, load_15 = os.getloadavg()
            cpu_load_ratio = load_1 / cpu_count
        except (OSError, AttributeError):
            load_1 = 0.0
            cpu_load_ratio = 0.0

        return {
            "available_memory_mb": available_mb,
            "cpu_count": cpu_count,
            "load_1m": load_1,
            "cpu_load_ratio": round(cpu_load_ratio, 2),
            "memory_pressure_high": available_mb < self.min_available_memory_mb,
            "cpu_saturation_high": cpu_load_ratio > self.max_cpu_load_ratio,
        }

    def _get_available_memory_mb(self) -> int:
        """Get available memory in MB. Uses psutil if present, otherwise platform commands."""
        try:
            import psutil
            return int(psutil.virtual_memory().available / (1024 * 1024))
        except ImportError:
            pass

        # macOS fallback using vm_stat
        if sys.platform == "darwin":
            try:
                output = subprocess.check_output(["vm_stat"], text=True, timeout=2)
                pages_free = 0
                pages_speculative = 0
                page_size = 4096
                for line in output.splitlines():
                    if "Pages free:" in line:
                        pages_free = int(line.split(":")[1].strip().rstrip("."))
                    elif "Pages speculative:" in line:
                        pages_speculative = int(line.split(":")[1].strip().rstrip("."))
                    elif "page size of" in line:
                        parts = line.split("page size of")
                        if len(parts) > 1:
                            page_size = int(parts[1].split("bytes")[0].strip())
                free_mb = int((pages_free + pages_speculative) * page_size / (1024 * 1024))
                return max(free_mb, 2048)  # Generous lower bound fallback
            except Exception:
                return 4096

        # Linux fallback using /proc/meminfo
        if sys.platform.startswith("linux"):
            try:
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemAvailable:"):
                            return int(line.split()[1]) // 1024
            except Exception:
                pass

        return 4096

    def check_admission(
        self,
        requirements: BrowserRequirements,
        current_active_leases: int,
    ) -> tuple[bool, str]:
        """Returns (is_admitted, reason)."""
        if current_active_leases >= self.max_concurrent_leases:
            return False, f"Maximum concurrent lease limit ({self.max_concurrent_leases}) reached"

        telemetry = self.get_system_telemetry()

        # Class S (Shared Multi-Context) is lightweight (~20-40MB), allow unless extreme pressure
        if requirements.execution_class == ExecutionClass.CLASS_S:
            if telemetry["available_memory_mb"] < (self.min_available_memory_mb // 2):
                return False, f"Critical memory pressure: {telemetry['available_memory_mb']}MB available"
            return True, "Admitted (Class S lightweight context)"

        # Class I and A spawn dedicated processes (~300-600MB+), check strict limits
        if telemetry["memory_pressure_high"]:
            return False, (
                f"Memory pressure: {telemetry['available_memory_mb']}MB available < "
                f"{self.min_available_memory_mb}MB threshold"
            )

        if telemetry["cpu_saturation_high"]:
            return False, (
                f"CPU load saturation: ratio {telemetry['cpu_load_ratio']} > "
                f"{self.max_cpu_load_ratio} threshold"
            )

        return True, "Admitted (Resource check passed)"

    def acquire_admission(
        self,
        requirements: BrowserRequirements,
        get_active_lease_count_fn,
        timeout_seconds: float = 5.0,
    ) -> None:
        """Blocks until admission is granted or raises AdmissionRejectedError upon timeout."""
        deadline = time.monotonic() + timeout_seconds
        last_reason = ""
        while time.monotonic() < deadline:
            admitted, reason = self.check_admission(requirements, get_active_lease_count_fn())
            if admitted:
                return
            last_reason = reason
            time.sleep(0.2)

        raise AdmissionRejectedError(f"Admission rejected: {last_reason} (timed out after {timeout_seconds}s)")
