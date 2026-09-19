"""Small, versioned data contracts shared by OmniBrowser core modules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum
import re
from typing import Any


_REF_PATTERN = re.compile(r"^f(?P<frame>[A-Za-z0-9_-]+)\.d(?P<epoch>[A-Za-z0-9_-]+)\.n(?P<node>[A-Za-z0-9_-]+)$")


class ExitCode(IntEnum):
    INVALID_INPUT = 2
    CDP_UNAVAILABLE = 3
    TARGET_NOT_FOUND = 4
    POLICY_BLOCKED = 5
    ACTION_TIMEOUT = 6
    INTERNAL_ERROR = 7


class OmniBrowserError(Exception):
    """Base exception for errors the CLI can map to a stable exit code."""

    exit_code = ExitCode.INTERNAL_ERROR


class StaleRefError(OmniBrowserError):
    exit_code = ExitCode.TARGET_NOT_FOUND


class TargetNotFoundError(OmniBrowserError):
    exit_code = ExitCode.TARGET_NOT_FOUND


class ActionTimeoutError(OmniBrowserError):
    exit_code = ExitCode.ACTION_TIMEOUT


class LeaseExpiredError(OmniBrowserError):
    """Raised when an operation is attempted with an expired or revoked lease."""
    exit_code = ExitCode.POLICY_BLOCKED


class LeaseNotFoundError(OmniBrowserError):
    """Raised when a requested lease ID does not exist."""
    exit_code = ExitCode.TARGET_NOT_FOUND


class IdentityConflictError(OmniBrowserError):
    """Raised when an exclusive identity lease is already held by another agent."""
    exit_code = ExitCode.POLICY_BLOCKED


class AdmissionRejectedError(OmniBrowserError):
    """Raised when admission controller rejects request due to memory/CPU backpressure."""
    exit_code = ExitCode.POLICY_BLOCKED


class BrokerStateUnavailableError(OmniBrowserError):
    """Raised when broker control-plane persistence fails or state file cannot be loaded."""
    exit_code = ExitCode.INTERNAL_ERROR


@dataclass(frozen=True, slots=True)
class DOMNodeRef:
    """An opaque reference valid only for one frame document epoch."""

    frame: str
    epoch: str
    node: str

    @classmethod
    def parse(cls, value: str) -> "DOMNodeRef":
        match = _REF_PATTERN.fullmatch(value)
        if not match:
            raise ValueError(f"Invalid DOM node ref: {value!r}")
        return cls(**match.groupdict())

    def __str__(self) -> str:
        return f"f{self.frame}.d{self.epoch}.n{self.node}"


@dataclass(slots=True)
class ObservedNode:
    ref: DOMNodeRef
    role: str
    name: str
    bounds: tuple[int, int, int, int]
    visible: bool
    interactive: bool
    disabled: bool = False
    value: str | None = None


@dataclass(slots=True)
class Coverage:
    is_complete: bool = False
    included: list[str] = field(default_factory=lambda: ["visible-interactive"])
    omitted: list[str] = field(default_factory=lambda: ["offscreen-content", "canvas"])


@dataclass(slots=True)
class ObserveResult:
    page_url: str
    frame_id: str
    document_epoch: str
    revision: int
    tree: list[ObservedNode]
    coverage: Coverage = field(default_factory=Coverage)
    version: str = "1"
    ok: bool = True
    suggested_recipes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["tree"] = [
            {**asdict(node), "ref": str(node.ref)} for node in self.tree
        ]
        return result


@dataclass(slots=True)
class StateDelta:
    changed: list[dict[str, Any]] = field(default_factory=list)
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ActRequest:
    op: str
    ref: DOMNodeRef
    value: str | None = None
    expect: dict[str, Any] | None = None


@dataclass(slots=True)
class ActResult:
    revision: int
    delta: StateDelta = field(default_factory=StateDelta)
    ok: bool = True
    document_epoch: str | None = None
    action_duration_ms: int = 0

    @property
    def state_delta(self) -> dict[str, Any]:
        """Compact state metadata kept alongside the detailed DOM delta."""
        return {
            "revision": self.revision,
            "document_epoch": self.document_epoch,
        }

    @property
    def elapsed_ms(self) -> int:
        return self.action_duration_ms

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["delta"] = asdict(self.delta)
        result["state_delta"] = self.state_delta
        result["elapsed_ms"] = self.action_duration_ms
        return result


class ExecutionClass:
    CLASS_S = "S"  # Shared Multi-Context (warm daemon, headless=new, ephemeral context)
    CLASS_I = "I"  # Isolated Dedicated Ephemeral (independent Chrome process, temp dir)
    CLASS_A = "A"  # Authenticated & Interactive (dedicated headful, exclusive identity lease)


@dataclass(slots=True)
class BrowserRequirements:
    execution_class: str = ExecutionClass.CLASS_S
    auth_identity: str | None = None
    requires_visual: bool = False
    untrusted_site: bool = False
    timeout_seconds: float = 300.0
    exclusive_identity: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Lease:
    lease_id: str
    agent_id: str
    project_id: str
    execution_class: str
    fencing_token: int
    cdp_url: str
    browser_context_id: str | None = None
    owned_target_ids: list[str] = field(default_factory=list)
    auth_identity: str | None = None
    expires_at: float = 0.0
    is_active: bool = True
    process_pid: int | None = None
    user_data_dir: str | None = None
    browser_instance_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
