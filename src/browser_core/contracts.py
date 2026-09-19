"""Small, versioned data contracts shared by OmniBrowser core modules."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
import re
from typing import Any, Mapping
from uuid import uuid4


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


class AnchorNotFound(OmniBrowserError, Exception):
    """Raised when an anchor or all candidates in an AnchorBundle cannot be found."""
    exit_code = ExitCode.TARGET_NOT_FOUND


class AnchorAmbiguous(OmniBrowserError, ValueError):
    """Raised when an anchor candidate matches more than one live element."""
    exit_code = ExitCode.INVALID_INPUT


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


class RiskClass:
    R0_READONLY = "R0"             # observe, inspect, non-mutating eval
    R1_REVERSIBLE_NAV = "R1"       # tab navigation, filters, accordion toggle
    R2_LOCAL_MUTABLE = "R2"        # form fills, checkboxes, drafts
    R3_PERSISTENT_MUTATION = "R3"  # form submit, save settings, create entity
    R4_IRREVERSIBLE = "R4"         # payment, deletion, publish, checkout, transfer, send


class ExecutionOutcome:
    CONFIRMED_SUCCESS = "CONFIRMED_SUCCESS"
    SAFE_FAILURE = "SAFE_FAILURE"
    UNKNOWN_SIDE_EFFECT = "UNKNOWN_SIDE_EFFECT"


class PreconditionFailedError(RuntimeError):
    """Raised when a recipe precondition or required anchor is missing."""
    pass


class ForbiddenAnchorError(RuntimeError):
    """Raised when a forbidden anchor (e.g. error dialog or conflict) is present."""
    pass


class RiskGateError(RuntimeError):
    """Raised when a recipe action exceeds the permitted risk threshold."""
    pass


class UnknownSideEffectError(RuntimeError):
    """Raised when an action fails during or after persistent mutation and side effect cannot be verified."""
    pass


@dataclass(slots=True)
class AnchorCandidate:
    kind: str
    selector: str | None = None
    role: str | None = None
    name: str | None = None
    score: float = 0.80

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "kind": self.kind,
            "score": self.score,
        }
        if self.selector is not None:
            d["selector"] = self.selector
        if self.role is not None:
            d["role"] = self.role
        if self.name is not None:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnchorCandidate":
        if isinstance(data, cls):
            return data
        kind = str(data.get("kind", "scoped_css"))
        selector = data.get("selector")
        role = data.get("role")
        name = data.get("name", data.get("label"))
        score = float(data.get("score", 0.80))
        return cls(
            kind=kind,
            selector=str(selector) if selector is not None else None,
            role=str(role) if role is not None else None,
            name=str(name) if name is not None else None,
            score=score,
        )


@dataclass(slots=True)
class AnchorBundle:
    candidates: list[AnchorCandidate] = field(default_factory=list)
    fallback_action: str = "fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidates": [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in self.candidates],
            "fallback_action": self.fallback_action,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnchorBundle":
        if isinstance(data, cls):
            return data
        candidates_raw = data.get("candidates", [])
        candidates = [AnchorCandidate.from_dict(c) for c in candidates_raw]
        fallback_action = str(data.get("fallback_action", "fail"))
        return cls(candidates=candidates, fallback_action=fallback_action)

    def __len__(self) -> int:
        return len(self.candidates)

    def __bool__(self) -> bool:
        return len(self.candidates) > 0


@dataclass(slots=True)
class RepairCandidate:
    recipe_id: str = ""
    step_index: int = 0
    broken_candidate: dict[str, Any] = field(default_factory=dict)
    healed_candidate: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    context: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "recipe_id": self.recipe_id,
            "step_index": self.step_index,
            "broken_candidate": self.broken_candidate,
            "healed_candidate": self.healed_candidate,
            "confidence": self.confidence,
            "context": self.context,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RepairCandidate":
        if isinstance(data, cls):
            return data
        return cls(
            id=str(data.get("id", uuid4().hex[:12])),
            recipe_id=str(data.get("recipe_id", "")),
            step_index=int(data.get("step_index", 0)),
            broken_candidate=dict(data.get("broken_candidate", {})),
            healed_candidate=dict(data.get("healed_candidate", {})),
            confidence=float(data.get("confidence", 0.0)),
            context=dict(data.get("context", {})),
            timestamp=str(data.get("timestamp", datetime.now(timezone.utc).isoformat())),
        )


@dataclass(slots=True)
class SemanticAnchor:
    role: str = ""
    name: str = ""
    tag: str = ""
    text_contains: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MatcherSpec:
    required_anchors: list[dict[str, Any]] = field(default_factory=list)
    forbidden_anchors: list[dict[str, Any]] = field(default_factory=list)
    semantic_fingerprint: dict[str, Any] = field(default_factory=dict)
    fingerprint_version: str = "semantic-fingerprint-v1"
    min_similarity: float = 0.70

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SafetySpec:
    max_risk: str = "R2"
    allow_r4: bool = False
    unknown_effect_policy: str = "reconcile"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HealthStats:
    executions: int = 0
    successes: int = 0
    failures: int = 0
    precondition_failures: int = 0
    forbidden_anchor_failures: int = 0
    unknown_side_effects: int = 0
    health_score: float = 1.0
    semantic_similarity_ewma: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkflowInterruptedError(RuntimeError):
    """Raised when a multi-step workflow halts due to an edge or state failure."""
    pass


@dataclass(slots=True)
class SemanticStateNode:
    state_id: str
    domain: str
    route_pattern: str = ""
    required_anchors: list[dict[str, Any]] = field(default_factory=list)
    semantic_fingerprint: dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TransitionEdge:
    edge_id: str
    from_state: str
    to_state: str
    recipe_id: str
    risk_class: str = RiskClass.R2_LOCAL_MUTABLE
    required_params: list[str] = field(default_factory=list)
    cost: float = 1.0

    @property
    def id(self) -> str:
        return self.edge_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkflowPlan:
    plan_id: str
    start_state: str
    goal_state: str
    edges: list[TransitionEdge] = field(default_factory=list)
    cumulative_risk: str = RiskClass.R0_READONLY
    estimated_steps: int = 0

    @property
    def final_state(self) -> str:
        return self.goal_state

    @property
    def target_state(self) -> str:
        return self.goal_state

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkflowExecutionResult:
    ok: bool
    plan_id: str
    completed_edges: int
    total_edges: int
    current_state: str
    cumulative_risk: str
    outcome: str = ExecutionOutcome.CONFIRMED_SUCCESS
    failure_edge: str | None = None
    message: str = ""
    edge_results: list[dict[str, Any]] = field(default_factory=list)
    detour_taken: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


WorkflowSpec = WorkflowPlan
WorkflowStep = TransitionEdge


class RecipeLifecycleState(str):
    DRAFT = "DRAFT"
    VERIFIED_LOCAL = "VERIFIED_LOCAL"
    VERIFIED_SHARED = "VERIFIED_SHARED"
    CURATED = "CURATED"
    SUSPECT = "SUSPECT"
    QUARANTINED = "QUARANTINED"
    ARCHIVED = "ARCHIVED"


class QuarantinedRecipeError(OmniBrowserError, RuntimeError):
    """Raised when an execution or suggestion request targets a quarantined recipe."""
    exit_code = ExitCode.POLICY_BLOCKED


class CASConflictError(OmniBrowserError, RuntimeError):
    """Raised when an optimistic concurrency update fails due to a revision mismatch."""
    exit_code = ExitCode.INTERNAL_ERROR


@dataclass(slots=True)
class PromotionPolicy:
    min_successes_local: int = 2
    min_successes_shared: int = 5
    min_independent_sessions: int = 3
    min_executions_curated: int = 20
    min_independent_agents: int = 3
    curated_success_rate: float = 0.95

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromotionPolicy":
        if isinstance(data, cls):
            return data
        return cls(
            min_successes_local=int(data.get("min_successes_local", 2)),
            min_successes_shared=int(data.get("min_successes_shared", 5)),
            min_independent_sessions=int(data.get("min_independent_sessions", 3)),
            min_executions_curated=int(data.get("min_executions_curated", 20)),
            min_independent_agents=int(data.get("min_independent_agents", 3)),
            curated_success_rate=float(data.get("curated_success_rate", 0.95)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class LifecycleRecord:
    state: str = RecipeLifecycleState.DRAFT
    revision: int = 1
    generation: int = 1
    sessions_seen: list[str] = field(default_factory=list)
    agents_seen: list[str] = field(default_factory=list)
    consecutive_failures: int = 0
    quarantine_reason: str | None = None
    quarantined_at: str | None = None
    utility_score: float = 1.0
    content_digest: str | None = None
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LifecycleRecord":
        if isinstance(data, cls):
            return data
        return cls(
            state=str(data.get("state", RecipeLifecycleState.DRAFT)),
            revision=int(data.get("revision", 1)),
            generation=int(data.get("generation", 1)),
            sessions_seen=list(data.get("sessions_seen", [])),
            agents_seen=list(data.get("agents_seen", [])),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            quarantine_reason=data.get("quarantine_reason"),
            quarantined_at=data.get("quarantined_at"),
            utility_score=float(data.get("utility_score", 1.0)),
            content_digest=data.get("content_digest"),
            audit_log=list(data.get("audit_log", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
