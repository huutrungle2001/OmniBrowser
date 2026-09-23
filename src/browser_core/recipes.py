"""Procedural memory and adaptive recipe execution.

Recipes are intentionally small JSON documents.  They provide a fast path for
known workflows while returning an explicit Level 1 observe/act fallback when
the page no longer matches the recorded procedure.
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass as _dataclass, field

if sys.version_info < (3, 10):
    def dataclass(*args, **kwargs):
        kwargs.pop("slots", None)
        return _dataclass(*args, **kwargs)
else:
    dataclass = _dataclass
import fcntl
import fnmatch
import json
import os
from pathlib import Path
import hashlib
import math
import re
import shlex
import sqlite3
import threading
import time
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse, urlsplit, urlunsplit
from uuid import uuid4

from playwright.sync_api import Page

from .contracts import (
    AnchorAmbiguous,
    AnchorBundle,
    AnchorCandidate,
    AnchorNotFound,
    CASConflictError,
    DOMNodeRef,
    ExecutionOutcome,
    ForbiddenAnchorError,
    HealthStats,
    LifecycleRecord,
    MatcherSpec,
    PreconditionFailedError,
    PromotionPolicy,
    QuarantinedRecipeError,
    RecipeLifecycleState,
    RepairCandidate,
    RiskClass,
    RiskGateError,
    SafetySpec,
    SemanticAnchor,
    StaleRepairError,
    UnknownSideEffectError,
)
from .engine import act
from .page_manager import PageManager, manager_for_page


_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET_KEY = re.compile(r"(?i)(password|passwd|passcode|token|secret|api[_-]?key|authorization|bearer|auth|credential|private[_-]?key|recovery[_-]?code)")
_OTP_KEY = re.compile(r"(?i)(otp|one.?time|verification.?code|cvv|ssn|cookie|session|2fa|mfa)")
_EMAIL_KEY = re.compile(r"(?i)(email|e-mail|mail)")
_PHONE_KEY = re.compile(r"(?i)(phone|mobile|tel)")
_NAME_KEY = re.compile(r"(?i)(first.?name|last.?name|full.?name|username|user|login)")
_SEARCH_KEY = re.compile(r"(?i)(search|query|keyword|filter|find)")
_FRAGILE_SELECTOR = re.compile(r"(?i)(:nth-(?:child|of-type)\(|^/|//)")
_IRREVERSIBLE_REGEX = re.compile(
    r"(?:\b|_)(delete|remove|destroy|terminate|purge|drop|wipe|revoke|pay|payment|checkout|buy|purchase|charge|transfer|send|publish|deploy|place\s*order|reset|disable|book)(?:\b|_)",
    re.I,
)
_PERSISTENT_REGEX = re.compile(
    r"(?:\b|_)(submit|save|update|create|confirm|apply|commit|sign\s*in|login|register|enroll|post|insert|approve|continue|ok)(?:\b|_)",
    re.I,
)
_NAV_REGEX = re.compile(r"\b(tab|nav|link|menu|expand|collapse|filter|page|next|prev|breadcrumb|accordion)\b", re.I)


class InvalidExecutableArtifact(ValueError):
    """The provided file or artifact is not an executable recipe or candidate."""


def classify_action_risk(
    action: str,
    target: Any = None,
    value: Any = None,
    explicit_risk: str | None = None,
) -> str:
    """Classify an action into Risk Classes R0-R4 with strict precedence to explicit risk metadata."""
    if explicit_risk:
        clean_exp = str(explicit_risk).strip().upper()
        if clean_exp in {
            RiskClass.R0_READONLY,
            RiskClass.R1_REVERSIBLE_NAV,
            RiskClass.R2_LOCAL_MUTABLE,
            RiskClass.R3_PERSISTENT_MUTATION,
            RiskClass.R4_IRREVERSIBLE,
        }:
            return clean_exp

    action_lower = str(action).strip().lower()
    target_str = str(target or "").lower()
    value_str = str(value or "").lower()

    if _IRREVERSIBLE_REGEX.search(target_str) or _IRREVERSIBLE_REGEX.search(value_str) or _IRREVERSIBLE_REGEX.search(action_lower):
        return RiskClass.R4_IRREVERSIBLE

    if _PERSISTENT_REGEX.search(target_str) or _PERSISTENT_REGEX.search(value_str) or _PERSISTENT_REGEX.search(action_lower):
        return RiskClass.R3_PERSISTENT_MUTATION

    if action_lower in {"observe", "inspect", "wait_for"}:
        return RiskClass.R0_READONLY

    if action_lower == "click":
        if _NAV_REGEX.search(target_str):
            return RiskClass.R1_REVERSIBLE_NAV
        return RiskClass.R2_LOCAL_MUTABLE

    if action_lower in {"fill", "select", "upload", "press"}:
        return RiskClass.R2_LOCAL_MUTABLE

    if action_lower == "eval":
        if _IRREVERSIBLE_REGEX.search(value_str):
            return RiskClass.R4_IRREVERSIBLE
        if _PERSISTENT_REGEX.search(value_str):
            return RiskClass.R3_PERSISTENT_MUTATION
        return RiskClass.R0_READONLY

    # Fail-closed for unknown actions: do NOT default to R2!
    return RiskClass.R3_PERSISTENT_MUTATION


def get_recipe_risk(recipe: Any) -> str:
    """Determine overall risk class of a recipe as the maximum risk of its steps, respecting explicit metadata."""
    risk_order = [
        RiskClass.R0_READONLY,
        RiskClass.R1_REVERSIBLE_NAV,
        RiskClass.R2_LOCAL_MUTABLE,
        RiskClass.R3_PERSISTENT_MUTATION,
        RiskClass.R4_IRREVERSIBLE,
    ]
    explicit_max = None
    if isinstance(recipe, dict):
        safety_dict = recipe.get("safety", {})
        if isinstance(safety_dict, dict) and safety_dict.get("max_risk"):
            explicit_max = safety_dict.get("max_risk")
    elif getattr(recipe, "safety", None) and getattr(recipe.safety, "max_risk", None):
        explicit_max = recipe.safety.max_risk

    steps = getattr(recipe, "steps", []) if not isinstance(recipe, dict) else recipe.get("steps", [])
    highest = RiskClass.R0_READONLY
    for step in steps:
        action = getattr(step, "action", "") or (step.get("action", step.get("op", "")) if isinstance(step, dict) else "")
        target = getattr(step, "target", None) or (step.get("target") if isinstance(step, dict) else None)
        value = getattr(step, "value", None) or (step.get("value") if isinstance(step, dict) else None)
        step_explicit = getattr(step, "risk_class", None) or (step.get("risk_class", step.get("risk")) if isinstance(step, dict) else None)
        risk = classify_action_risk(action, target, value, explicit_risk=step_explicit)
        if risk_order.index(risk) > risk_order.index(highest):
            highest = risk

    if explicit_max and explicit_max in risk_order:
        if risk_order.index(explicit_max) > risk_order.index(highest):
            highest = explicit_max

    return highest




def _find_anchor_in_tree(anchor: dict[str, Any], tree_nodes: Any) -> bool:
    """Check if an anchor definition matches at least one element in tree_nodes."""
    if not anchor or not isinstance(anchor, dict):
        return True
    if isinstance(tree_nodes, dict):
        nodes = tree_nodes.get("tree", tree_nodes.get("elements", []))
    elif isinstance(tree_nodes, list):
        nodes = tree_nodes
    else:
        nodes = []

    exp_role = str(anchor.get("role", "")).strip().lower()
    exp_name = str(anchor.get("name", "")).strip().lower()
    exp_text = str(anchor.get("text_contains", "")).strip().lower()
    exp_tag = str(anchor.get("tag", "")).strip().lower()

    for node in nodes:
        n_dict = asdict(node) if hasattr(node, "__dataclass_fields__") else (dict(node) if isinstance(node, (dict, Mapping)) else {})
        n_role = str(n_dict.get("role", "")).strip().lower()
        n_name = str(n_dict.get("name", "")).strip().lower()
        n_value = str(n_dict.get("value", "")).strip().lower()
        n_tag = str(n_dict.get("tag", "")).strip().lower()

        if exp_role and exp_role != n_role:
            continue
        if exp_tag and exp_tag != n_tag:
            continue
        if exp_name:
            if exp_name not in n_name and n_name not in exp_name:
                continue
        if exp_text:
            if exp_text not in n_name and exp_text not in n_value:
                continue
        return True
    return False


def extract_semantic_fingerprint(tree_nodes: Any) -> set[tuple[str, str]]:
    """Extract normalized (role, name) pairs for stable structural elements."""
    if isinstance(tree_nodes, dict):
        nodes = tree_nodes.get("tree", tree_nodes.get("elements", []))
    elif isinstance(tree_nodes, list):
        nodes = tree_nodes
    else:
        nodes = []

    fp = set()
    for node in nodes:
        n_dict = asdict(node) if hasattr(node, "__dataclass_fields__") else (dict(node) if isinstance(node, (dict, Mapping)) else {})
        role = str(n_dict.get("role", "")).strip().lower()
        name = str(n_dict.get("name", "")).strip().lower()
        if name and role in {"heading", "button", "link", "textbox", "combobox", "dialog", "navigation", "main", "tab"}:
            fp.add((role, name))
    return fp


def calculate_semantic_similarity(fp1: set[tuple[str, str]], fp2: set[tuple[str, str]]) -> float:
    """Compute Jaccard similarity between two semantic element fingerprints."""
    if not fp1 and not fp2:
        return 1.0
    union = fp1 | fp2
    if not union:
        return 1.0
    intersection = fp1 & fp2
    return round(len(intersection) / len(union), 3)


def match_page_state(recipe: Any, page_url: str, tree_nodes: Any) -> dict[str, Any]:
    """Evaluate composite matching: required anchors, forbidden anchors, route, and semantic similarity."""
    matcher = getattr(recipe, "matcher", None) or MatcherSpec()

    # 1. Hard veto: Forbidden anchors
    for fa in getattr(matcher, "forbidden_anchors", []):
        if _find_anchor_in_tree(fa, tree_nodes):
            name = fa.get("name") or fa.get("role") or str(fa)
            return {
                "matched": False,
                "score": 0.0,
                "reason": f"forbidden_anchor_present: {name}",
                "semantic_similarity": 0.0,
                "route_similarity": 0.0,
            }

    # 2. Hard precondition: Required anchors
    missing_required = []
    for ra in getattr(matcher, "required_anchors", []):
        if not _find_anchor_in_tree(ra, tree_nodes):
            missing_required.append(ra)

    if missing_required:
        return {
            "matched": False,
            "score": 0.0,
            "reason": f"missing_required_anchors: {len(missing_required)} missing",
            "missing": missing_required,
            "semantic_similarity": 0.0,
            "route_similarity": 0.0,
        }

    # 3. Route similarity
    route_matched = recipe.matches_url(page_url) if hasattr(recipe, "matches_url") else False
    route_sim = 1.0 if route_matched else 0.0

    # 4. Semantic similarity
    live_fp = extract_semantic_fingerprint(tree_nodes)
    recorded_fp = set()
    raw_fp = getattr(matcher, "semantic_fingerprint", {}) or {}
    if isinstance(raw_fp, dict):
        fp_raw = raw_fp.get("elements", [])
        recorded_fp = {tuple(x) for x in fp_raw}

    if recorded_fp:
        sem_sim = calculate_semantic_similarity(recorded_fp, live_fp)
    else:
        sem_sim = 1.0 if route_matched else 0.5

    # 5. Anchor coverage
    anchor_cov = 1.0 if not missing_required else 0.0

    # 6. Health trust prior
    health_score = getattr(getattr(recipe, "health", None), "health_score", 1.0)

    # 7. Composite score: 0.30*route + 0.40*sem + 0.20*anchor + 0.10*health
    composite_score = round(0.30 * route_sim + 0.40 * sem_sim + 0.20 * anchor_cov + 0.10 * health_score, 3)

    min_sim = getattr(matcher, "min_similarity", 0.70)
    matched = bool((composite_score >= min_sim) and (route_matched or sem_sim >= 0.70))
    reason = None
    if not matched:
        if composite_score < min_sim:
            reason = f"similarity_below_threshold: {composite_score} < {min_sim}"
        elif not route_matched and sem_sim < 0.70:
            reason = f"route_mismatch_and_low_similarity: route={route_sim}, sem={sem_sim}"
        else:
            reason = "matching_criteria_not_satisfied"

    return {
        "matched": matched,
        "score": composite_score,
        "reason": reason,
        "semantic_similarity": sem_sim,
        "route_similarity": route_sim,
        "anchor_coverage": anchor_cov,
        "health_score": health_score,
    }


def _find_matching_node(anchor: dict[str, Any], nodes: Any) -> dict[str, Any] | None:
    """Find and return dictionary representation of node matching an anchor, or None."""
    if not anchor or not isinstance(anchor, dict):
        return {}
    if isinstance(nodes, dict):
        node_list = nodes.get("tree", nodes.get("elements", []))
    elif isinstance(nodes, list):
        node_list = nodes
    else:
        node_list = []

    exp_role = str(anchor.get("role", "")).strip().lower()
    exp_name = str(anchor.get("name", "")).strip().lower()
    exp_text = str(anchor.get("text_contains", "")).strip().lower()
    exp_tag = str(anchor.get("tag", "")).strip().lower()

    for node in node_list:
        n_dict = asdict(node) if hasattr(node, "__dataclass_fields__") else (dict(node) if isinstance(node, (dict, Mapping)) else {})
        n_role = str(n_dict.get("role", "")).strip().lower()
        n_name = str(n_dict.get("name", "")).strip().lower()
        n_value = str(n_dict.get("value", "")).strip().lower()
        n_tag = str(n_dict.get("tag", "")).strip().lower()

        if exp_role and exp_role != n_role:
            continue
        if exp_tag and exp_tag != n_tag:
            continue
        if exp_name and (exp_name not in n_name and n_name not in exp_name):
            continue
        if exp_text and (exp_text not in n_name and exp_text not in n_value):
            continue
        return n_dict
    return None


def verify_transition_reconciliation(
    postconditions: list[dict[str, Any]],
    baseline_nodes: Any,
    current_nodes: Any,
    initial_url: str,
    current_url: str,
) -> bool:
    """
    Verify that postconditions represent a confirmed state transition
    rather than a pre-existing state predicate present prior to mutation.
    """
    if not postconditions:
        return False

    matched_current: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for post in postconditions:
        curr_node = _find_matching_node(post, current_nodes)
        if curr_node is None:
            return False
        matched_current.append((post, curr_node))

    # Evidence of state transition:
    # 1. URL changed
    if initial_url and current_url and _safe_url(initial_url) != _safe_url(current_url):
        return True

    # 2. Check each postcondition against baseline_nodes
    for post, curr_match in matched_current:
        base_match = _find_matching_node(post, baseline_nodes)
        if base_match is None:
            # Anchor newly materialized as a consequence of the action!
            return True
        # Anchor existed in baseline; did its observable state/value change?
        curr_val = curr_match.get("value", "")
        base_val = base_match.get("value", "")
        curr_name = curr_match.get("name", "")
        base_name = base_match.get("name", "")
        if (curr_val and curr_val != base_val) or (curr_name and curr_name != base_name):
            return True

    # If all postcondition anchors were already present in baseline and did not transition,
    # reconciliation cannot prove mutation occurred.
    return False




def _memory_root(root: str | Path | None = None) -> Path:
    """Keep learned data outside the reviewed, source-controlled recipe tree."""
    return Path(root or os.environ.get("OMNIBROWSER_MEMORY_ROOT") or Path.home() / ".omnibrowser" / "memory" / "v1")


def _safe_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        port_part = f":{parsed.port}" if parsed.port else ""
        netloc = f"{host}{port_part}" if host else ""
        # Strictly strip userinfo (user:pass@), query values, and fragments
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except Exception:
        return ""


def _domain_from_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        if parsed.scheme == "about":
            return f"about:{parsed.path or 'blank'}"
        host = (parsed.hostname or "").rstrip(".").lower()
        if not host:
            domain = parsed.netloc or parsed.path
            if "@" in domain:
                domain = domain.split("@")[-1]
            if ":" in domain:
                domain = domain.split(":")[0]
            host = domain.strip().lower()
        return host or "generic"
    except Exception:
        return "generic"


def _normalize_path(url: str) -> str:
    try:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            return "_root"
        normalized = re.sub(r"[^\w.-]", "_", path)
        return normalized[:100]
    except Exception:
        return "_root"


def _safe_json(value: Any, *, name: str = "") -> Any:
    """Create a persistable projection; raw typed input is deliberately excluded."""
    if isinstance(value, Mapping):
        return {str(key): _safe_json(item, name=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item, name=name) for item in value]
    return sanitize_value(value, sensitive_name=name)


def _defaults(metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = dict(metadata or {})
    result.setdefault("version", "1")
    result.setdefault("success_count", 0)
    result.setdefault("failure_count", 0)
    result.setdefault("last_failure_reason", None)
    result.setdefault("failure_ledger", [])
    return result


def _substitute(value: Any, params: Mapping[str, Any]) -> Any:
    """Substitute templates without coercing non-string JSON values."""
    if isinstance(value, str):
        return _TEMPLATE.sub(lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), value)
    if isinstance(value, list):
        return [_substitute(item, params) for item in value]
    if isinstance(value, AnchorBundle):
        new_candidates = []
        for c in value.candidates:
            new_c = AnchorCandidate(
                kind=c.kind,
                selector=_substitute(c.selector, params) if c.selector else None,
                role=_substitute(c.role, params) if c.role else None,
                name=_substitute(c.name, params) if c.name else None,
                score=c.score,
            )
            new_candidates.append(new_c)
        return AnchorBundle(candidates=new_candidates, fallback_action=value.fallback_action)
    if isinstance(value, dict):
        if set(value) >= {"$ref"}:
            ref = str(value["$ref"])
            if ref in params:
                return params[ref]
        return {key: _substitute(item, params) for key, item in value.items()}
    return value


@dataclass(slots=True)
class RecipeStep:
    action: str
    target: str | AnchorBundle | dict[str, Any] | None = None
    value: Any = None
    expect: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 5000
    selector: str | None = None
    risk_class: str | None = None

    def __post_init__(self) -> None:
        if self.target is None and self.selector is not None:
            self.target = self.selector
        elif isinstance(self.target, dict) and ("candidates" in self.target or "kind" in self.target):
            self.target = AnchorBundle.from_dict(self.target)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecipeStep":
        action = str(data.get("action", data.get("op", ""))).strip().lower()
        if not action:
            raise ValueError("Recipe step requires action")
        target = data.get("target", data.get("selector", data.get("ref")))
        if isinstance(target, dict) and ("candidates" in target or "kind" in target):
            target = AnchorBundle.from_dict(target)
        elif isinstance(target, AnchorBundle):
            target = target
        expect = data.get("expect", {})
        if expect is None:
            expect = {}
        if not isinstance(expect, dict):
            raise ValueError("Recipe step expect must be an object")
        risk_class = data.get("risk_class", data.get("risk"))
        return cls(action=action, target=target,
                   value=data.get("value", data.get("text", data.get("script"))),
                   expect=dict(expect), timeout_ms=int(data.get("timeout_ms", 5000)),
                   risk_class=str(risk_class).strip().upper() if risk_class else None)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"action": self.action}
        if self.target is not None:
            if hasattr(self.target, "to_dict"):
                result["target"] = self.target.to_dict()
            else:
                result["target"] = self.target
        if self.value is not None:
            result["value"] = self.value
        if self.expect:
            result["expect"] = self.expect
        if self.timeout_ms != 5000:
            result["timeout_ms"] = self.timeout_ms
        if self.risk_class:
            result["risk_class"] = self.risk_class
        return result

    @property
    def op(self) -> str:
        return self.action

    @property
    def risk(self) -> str:
        return self.risk_class or classify_action_risk(self.action, self.target, self.value)



@dataclass(slots=True)
class Recipe:
    id: str
    name: str
    description: str = ""
    domain_pattern: str | list[str] = "*"
    steps: list[RecipeStep] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    matcher: MatcherSpec = field(default_factory=MatcherSpec)
    preconditions: list[dict[str, Any]] = field(default_factory=list)
    postconditions: list[dict[str, Any]] = field(default_factory=list)
    safety: SafetySpec = field(default_factory=SafetySpec)
    health: HealthStats = field(default_factory=HealthStats)
    lifecycle: LifecycleRecord = field(default_factory=LifecycleRecord)
    action_type: str = ""

    def __post_init__(self) -> None:
        self.metadata = _defaults(self.metadata)
        if isinstance(self.matcher, dict):
            self.matcher = MatcherSpec(
                required_anchors=list(self.matcher.get("required_anchors", [])),
                forbidden_anchors=list(self.matcher.get("forbidden_anchors", [])),
                semantic_fingerprint=dict(self.matcher.get("semantic_fingerprint", {})),
                min_similarity=float(self.matcher.get("min_similarity", 0.70)),
            )
        if isinstance(self.safety, dict):
            self.safety = SafetySpec(
                max_risk=str(self.safety.get("max_risk", "R2")),
                allow_r4=bool(self.safety.get("allow_r4", False)),
                unknown_effect_policy=str(self.safety.get("unknown_effect_policy", "reconcile")),
            )
        if isinstance(self.health, dict):
            self.health = HealthStats(
                executions=int(self.health.get("executions", 0)),
                successes=int(self.health.get("successes", 0)),
                failures=int(self.health.get("failures", 0)),
                precondition_failures=int(self.health.get("precondition_failures", 0)),
                forbidden_anchor_failures=int(self.health.get("forbidden_anchor_failures", 0)),
                unknown_side_effects=int(self.health.get("unknown_side_effects", 0)),
                health_score=float(self.health.get("health_score", 1.0)),
                semantic_similarity_ewma=float(self.health.get("semantic_similarity_ewma", 1.0)),
            )
        if isinstance(self.lifecycle, dict):
            self.lifecycle = LifecycleRecord.from_dict(self.lifecycle)
        elif not isinstance(self.lifecycle, LifecycleRecord):
            self.lifecycle = LifecycleRecord()
        self.steps = [step if isinstance(step, RecipeStep) else RecipeStep.from_dict(step) for step in self.steps]
        self.validate()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Recipe":
        kind = data.get("kind")
        if kind == "omnibrowser.semantic_sitemap" or "interactive_elements" in data:
            raise InvalidExecutableArtifact("Sitemaps cannot be executed as recipes")
        if kind is not None and kind not in {"omnibrowser.recipe", "omnibrowser.recipe_candidate"}:
            raise InvalidExecutableArtifact(f"Invalid executable artifact kind: {kind!r}. Expected recipe or candidate.")

        required = ("id", "name", "domain_pattern", "steps")
        missing = [key for key in required if key not in data]
        if missing:
            raise ValueError(f"Recipe missing required field(s): {', '.join(missing)}")

        matcher_raw = data.get("matcher") or {}
        if isinstance(matcher_raw, MatcherSpec):
            matcher = matcher_raw
        elif isinstance(matcher_raw, dict):
            matcher = MatcherSpec(
                required_anchors=list(matcher_raw.get("required_anchors", [])),
                forbidden_anchors=list(matcher_raw.get("forbidden_anchors", [])),
                semantic_fingerprint=dict(matcher_raw.get("semantic_fingerprint", {})),
                min_similarity=float(matcher_raw.get("min_similarity", 0.70)),
            )
        else:
            matcher = MatcherSpec()

        safety_raw = data.get("safety") or {}
        if isinstance(safety_raw, SafetySpec):
            safety = safety_raw
        elif isinstance(safety_raw, dict):
            safety = SafetySpec(
                max_risk=str(safety_raw.get("max_risk", "R2")),
                allow_r4=bool(safety_raw.get("allow_r4", False)),
                unknown_effect_policy=str(safety_raw.get("unknown_effect_policy", "reconcile")),
            )
        else:
            safety = SafetySpec()

        health_raw = data.get("health") or {}
        if isinstance(health_raw, HealthStats):
            health = health_raw
        elif isinstance(health_raw, dict):
            health = HealthStats(
                executions=int(health_raw.get("executions", 0)),
                successes=int(health_raw.get("successes", 0)),
                failures=int(health_raw.get("failures", 0)),
                precondition_failures=int(health_raw.get("precondition_failures", 0)),
                forbidden_anchor_failures=int(health_raw.get("forbidden_anchor_failures", 0)),
                unknown_side_effects=int(health_raw.get("unknown_side_effects", 0)),
                health_score=float(health_raw.get("health_score", 1.0)),
                semantic_similarity_ewma=float(health_raw.get("semantic_similarity_ewma", 1.0)),
            )
        else:
            health = HealthStats()

        lifecycle_raw = data.get("lifecycle") or {}
        if isinstance(lifecycle_raw, LifecycleRecord):
            lifecycle = lifecycle_raw
        elif isinstance(lifecycle_raw, dict):
            lifecycle = LifecycleRecord.from_dict(lifecycle_raw)
        else:
            lifecycle = LifecycleRecord()

        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            domain_pattern=data["domain_pattern"],
            steps=list(data["steps"]),
            validation=dict(data.get("validation", {})),
            metadata=dict(data.get("metadata", {})),
            matcher=matcher,
            preconditions=list(data.get("preconditions", [])),
            postconditions=list(data.get("postconditions", [])),
            safety=safety,
            health=health,
            lifecycle=lifecycle,
            action_type=str(data.get("action_type", "")),
        )

    @classmethod
    def from_json(cls, payload: str | bytes | Path) -> "Recipe":
        if isinstance(payload, Path):
            payload = payload.read_bytes()
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError("Recipe JSON must contain an object")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        kind = "omnibrowser.recipe_candidate" if (self.metadata.get("automatic") or self.metadata.get("status") == "draft") else "omnibrowser.recipe"
        res = {
            "kind": kind,
            "schema_version": 2,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "domain_pattern": self.domain_pattern,
            "steps": [step.to_dict() for step in self.steps],
            "validation": self.validation,
            "metadata": self.metadata,
            "matcher": self.matcher.to_dict(),
            "preconditions": self.preconditions,
            "postconditions": self.postconditions,
            "safety": self.safety.to_dict(),
            "health": self.health.to_dict(),
            "lifecycle": self.lifecycle.to_dict(),
        }
        if self.action_type:
            res["action_type"] = self.action_type
        return res


    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent) + "\n"

    def match(self, page_url: str, tree_nodes: Any = None) -> dict[str, Any]:
        return match_page_state(self, page_url, tree_nodes)

    @property
    def max_risk(self) -> str:
        return get_recipe_risk(self)

    def validate(self) -> None:
        if not self.id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", self.id):
            raise ValueError("Recipe id must contain only letters, numbers, _, ., or -")
        if not self.steps:
            raise ValueError("Recipe must contain at least one step")
        valid_actions = {"click", "fill", "select", "wait_for", "eval", "upload"}
        automatic = bool(self.metadata.get("automatic") or self.metadata.get("source") == "learned")
        for index, step in enumerate(self.steps):
            if step.action not in valid_actions:
                raise ValueError(f"Unsupported recipe action at step {index}: {step.action!r}")
            if step.action != "eval" and not step.target:
                raise ValueError(f"Recipe step {index} requires target")
            if step.action in {"fill", "select", "eval", "upload"} and step.value is None:
                raise ValueError(f"Recipe step {index} requires value")
            if automatic and step.action == "eval":
                raise ValueError("Automatic recipes may not contain eval steps")
        if not isinstance(self.validation, dict):
            raise ValueError("Recipe validation must be an object")

    def matches_url(self, url: str) -> bool:
        patterns = self.domain_pattern if isinstance(self.domain_pattern, list) else [self.domain_pattern]
        for pattern in patterns:
            pattern = str(pattern)
            if pattern.startswith("re:"):
                try:
                    if re.search(pattern[3:], url):
                        return True
                except re.error:
                    continue
            elif len(pattern) >= 2 and pattern.startswith("/") and pattern.rfind("/") > 0:
                end = pattern.rfind("/")
                try:
                    if re.search(pattern[1:end], url, re.I if "i" in pattern[end + 1:] else 0):
                        return True
                except re.error:
                    continue
            elif fnmatch.fnmatchcase(url, pattern) or fnmatch.fnmatchcase(urlparse(url).netloc, pattern):
                return True
            else:
                # Bare patterns are useful as either a domain fragment or a regex.
                try:
                    if re.search(pattern, url):
                        return True
                except re.error:
                    pass
        return False


def compute_recipe_content_digest(recipe: Recipe) -> str:
    """Deterministic SHA256 hex digest of executable recipe content."""
    action_type = getattr(recipe, "action_type", "")
    domain_pattern = getattr(recipe, "domain_pattern", "*")
    steps = [s.to_dict() if hasattr(s, "to_dict") else s for s in getattr(recipe, "steps", [])]
    matcher = recipe.matcher.to_dict() if hasattr(recipe.matcher, "to_dict") else getattr(recipe, "matcher", {})
    safety = recipe.safety.to_dict() if hasattr(recipe.safety, "to_dict") else getattr(recipe, "safety", {})

    payload = [
        action_type,
        domain_pattern,
        steps,
        matcher,
        safety,
    ]
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def calculate_utility_score(recipe: Recipe, now_ts: float | None = None) -> float:
    """
    Calculate multi-factor utility score U(R):
    U(R) = (Frequency * Reliability * RecomputeCost * RecencyDecay) / StorageCost
    """
    if now_ts is None:
        now_ts = time.time()

    executions = getattr(recipe.health, "executions", 0) if hasattr(recipe, "health") else 0
    health_score = getattr(recipe.health, "health_score", 1.0) if hasattr(recipe, "health") else 1.0

    # Frequency: base 1.0 plus log-scaled executions
    frequency = 1.0 + math.log1p(max(0, executions))

    # Reliability: health score (success rate)
    reliability = max(0.01, float(health_score))

    # RecomputeCost: proportional to step complexity and risk
    step_count = max(1, len(recipe.steps))
    risk = getattr(recipe, "max_risk", get_recipe_risk(recipe))
    risk_multiplier = {
        RiskClass.R0_READONLY: 1.0,
        RiskClass.R1_REVERSIBLE_NAV: 1.2,
        RiskClass.R2_LOCAL_MUTABLE: 1.5,
        RiskClass.R3_PERSISTENT_MUTATION: 2.0,
        RiskClass.R4_IRREVERSIBLE: 3.0,
    }.get(risk, 1.5)
    recompute_cost = max(1.0, step_count * risk_multiplier)

    # RecencyDecay: exponential decay based on last execution / update / creation
    meta = getattr(recipe, "metadata", {}) or {}
    last_ts = (
        meta.get("last_executed_at")
        or meta.get("updated_at")
        or meta.get("created_at")
        or now_ts
    )
    age_seconds = max(0.0, float(now_ts) - float(last_ts))
    # 14-day half-life decay
    recency_decay = math.exp(-age_seconds / (14.0 * 86400.0))

    # StorageCost: payload size in KB (minimum 1.0 KB to normalize)
    try:
        payload_bytes = len(json.dumps(recipe.to_dict()).encode("utf-8"))
        storage_cost = max(1.0, payload_bytes / 1024.0)
    except Exception:
        storage_cost = 1.0

    raw_score = (frequency * reliability * recompute_cost * recency_decay) / storage_cost
    return round(float(raw_score), 4)


def evaluate_promotion(
    recipe: Recipe,
    policy: PromotionPolicy | None = None,
) -> str | None:
    """
    Evaluate if a recipe meets evidence criteria for advancing to a higher lifecycle state.
    Returns the new state if promotion criteria are met, or None if not eligible.
    """
    if policy is None:
        policy = PromotionPolicy()

    current_state = getattr(recipe.lifecycle, "state", RecipeLifecycleState.DRAFT) if hasattr(recipe, "lifecycle") else RecipeLifecycleState.DRAFT
    if current_state in {RecipeLifecycleState.QUARANTINED, RecipeLifecycleState.ARCHIVED, RecipeLifecycleState.SUSPECT}:
        return None

    executions = getattr(recipe.health, "executions", 0) if hasattr(recipe, "health") else 0
    successes = getattr(recipe.health, "successes", 0) if hasattr(recipe, "health") else int(recipe.metadata.get("success_count", 0))
    failures = getattr(recipe.health, "failures", 0) if hasattr(recipe, "health") else int(recipe.metadata.get("failure_count", 0))
    health_score = getattr(recipe.health, "health_score", 1.0) if hasattr(recipe, "health") else 1.0

    sessions_seen = set(getattr(recipe.lifecycle, "sessions_seen", [])) if hasattr(recipe, "lifecycle") else set()
    agents_seen = set(getattr(recipe.lifecycle, "agents_seen", [])) if hasattr(recipe, "lifecycle") else set()

    qualifying_state = current_state

    # DRAFT -> VERIFIED_LOCAL: >= 2 successful replays
    if successes >= policy.min_successes_local:
        qualifying_state = RecipeLifecycleState.VERIFIED_LOCAL

    # VERIFIED_LOCAL -> VERIFIED_SHARED: >= 5 successes across >= 3 independent sessions with 0 structural failures
    if (
        qualifying_state in {RecipeLifecycleState.VERIFIED_LOCAL, RecipeLifecycleState.VERIFIED_SHARED, RecipeLifecycleState.CURATED}
        and successes >= policy.min_successes_shared
        and len(sessions_seen) >= policy.min_independent_sessions
        and failures == 0
    ):
        qualifying_state = RecipeLifecycleState.VERIFIED_SHARED

    # VERIFIED_SHARED -> CURATED: >= 20 executions across >= 3 distinct agents with success rate >= 0.95
    if (
        qualifying_state in {RecipeLifecycleState.VERIFIED_SHARED, RecipeLifecycleState.CURATED}
        and executions >= policy.min_executions_curated
        and len(agents_seen) >= policy.min_independent_agents
        and health_score >= policy.curated_success_rate
    ):
        qualifying_state = RecipeLifecycleState.CURATED

    state_ranks = {
        RecipeLifecycleState.DRAFT: 0,
        RecipeLifecycleState.VERIFIED_LOCAL: 1,
        RecipeLifecycleState.VERIFIED_SHARED: 2,
        RecipeLifecycleState.CURATED: 3,
    }
    current_rank = state_ranks.get(current_state, -1)
    qualifying_rank = state_ranks.get(qualifying_state, -1)

    if qualifying_rank > current_rank:
        return qualifying_state
    return None


def check_policy_qualifies_for(
    recipe: Recipe,
    target_state: str,
    policy: PromotionPolicy | None = None,
) -> bool:
    """Check if recipe's current evidence satisfies the promotion policy criteria for target_state."""
    if policy is None:
        policy = PromotionPolicy()

    clean_state = str(target_state).strip().upper()
    if clean_state == RecipeLifecycleState.DRAFT:
        return True

    successes = getattr(recipe.health, "successes", 0) if hasattr(recipe, "health") else int(recipe.metadata.get("success_count", 0))
    failures = getattr(recipe.health, "failures", 0) if hasattr(recipe, "health") else int(recipe.metadata.get("failure_count", 0))
    executions = getattr(recipe.health, "executions", 0) if hasattr(recipe, "health") else 0
    health_score = getattr(recipe.health, "health_score", 1.0) if hasattr(recipe, "health") else 1.0

    sessions_seen = set(getattr(recipe.lifecycle, "sessions_seen", [])) if hasattr(recipe, "lifecycle") else set()
    agents_seen = set(getattr(recipe.lifecycle, "agents_seen", [])) if hasattr(recipe, "lifecycle") else set()

    if clean_state == RecipeLifecycleState.VERIFIED_LOCAL:
        return successes >= policy.min_successes_local
    if clean_state == RecipeLifecycleState.VERIFIED_SHARED:
        return (
            successes >= policy.min_successes_shared
            and len(sessions_seen) >= policy.min_independent_sessions
            and failures == 0
        )
    if clean_state == RecipeLifecycleState.CURATED:
        return (
            executions >= policy.min_executions_curated
            and len(agents_seen) >= policy.min_independent_agents
            and health_score >= policy.curated_success_rate
        )
    return False


def check_quarantine_triggers(
    recipe: Recipe,
    last_outcome: str | None = None,
) -> tuple[bool, str | None]:
    """
    Check automatic degradation and quarantine triggers:
    1. >= 3 consecutive execution failures
    2. Any single UNKNOWN_SIDE_EFFECT on persistent mutation (R3/R4)
    3. Health score degradation below 0.60
    """
    lifecycle = getattr(recipe, "lifecycle", None)
    consecutive_failures = getattr(lifecycle, "consecutive_failures", 0) if lifecycle else 0

    # Trigger 1: >= 3 consecutive failures
    if consecutive_failures >= 3:
        return True, f"consecutive_failures_exceeded: {consecutive_failures} >= 3"

    # Trigger 2: UNKNOWN_SIDE_EFFECT on persistent mutation (R3/R4)
    if last_outcome == ExecutionOutcome.UNKNOWN_SIDE_EFFECT:
        recipe_risk = getattr(recipe, "max_risk", get_recipe_risk(recipe))
        if recipe_risk in {RiskClass.R3_PERSISTENT_MUTATION, RiskClass.R4_IRREVERSIBLE}:
            return True, f"unknown_side_effect_on_persistent_mutation: risk={recipe_risk}"

    # Trigger 3: Health score degradation below threshold (< 0.60)
    health = getattr(recipe, "health", None)
    if health and health.health_score < 0.60:
        if health.executions >= 3 or (health.executions == 0 and health.health_score != 1.0):
            return True, f"health_score_degraded: {health.health_score} < 0.60"

    return False, None


class RecipeStore:
    """Loads recipes from a directory and records only non-sensitive health data."""

    def __init__(self, root: str | Path = "recipes"):
        self.root = Path(root)
        self.repair_log = self.root / ".repairs.jsonl"
        self._recipes: dict[str, Recipe] = {}

    def load(self) -> list[Recipe]:
        self._recipes = {}
        if not self.root.exists():
            return []
        for path in sorted(self.root.rglob("*.json")):
            if "sitemaps" in path.parts or ".locks" in path.parts or path.name.startswith("."):
                continue
            try:
                recipe = Recipe.from_json(path.read_bytes())
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ValueError(f"Invalid recipe file {path}: {error}") from error
            if recipe.id in self._recipes:
                raise ValueError(f"Duplicate recipe id: {recipe.id}")
            self._recipes[recipe.id] = recipe
        return list(self._recipes.values())

    load_all = load

    def all(self) -> list[Recipe]:
        if not self._recipes:
            self.load()
        return list(self._recipes.values())

    def list(self, url: str | None = None) -> list[Recipe]:
        recipes = self.all()
        return [recipe for recipe in recipes if url is None or recipe.matches_url(url)]

    def get(self, recipe_id: str) -> Recipe | None:
        if not self._recipes:
            self.load()
        if self.root.exists():
            for p in self.root.rglob(f"{recipe_id}.json"):
                if p.is_file() and "sitemaps" not in p.parts and ".locks" not in p.parts and not p.name.startswith("."):
                    try:
                        rec = Recipe.from_json(p.read_bytes())
                        self._recipes[rec.id] = rec
                        return rec
                    except Exception:
                        pass
                    break
        return self._recipes.get(recipe_id)

    get_recipe = get

    def match(self, url: str) -> list[Recipe]:
        return self.list(url)

    find_matching = match

    def save(
        self,
        recipe: Recipe,
        domain: str | None = None,
        expected_revision: int | None = None,
    ) -> Path:
        if not hasattr(recipe, "lifecycle") or recipe.lifecycle is None:
            recipe.lifecycle = LifecycleRecord()

        target_path = None
        if self.root.exists():
            for p in self.root.rglob(f"{recipe.id}.json"):
                if p.is_file() and "sitemaps" not in p.parts and ".locks" not in p.parts and not p.name.startswith("."):
                    target_path = p
                    break

        if target_path is not None:
            path = target_path
        else:
            target_domain = domain
            if not target_domain:
                if isinstance(recipe.domain_pattern, str) and "*" not in recipe.domain_pattern and "/" not in recipe.domain_pattern and ":" not in recipe.domain_pattern:
                    target_domain = recipe.domain_pattern
                elif isinstance(recipe.domain_pattern, list) and len(recipe.domain_pattern) == 1 and "*" not in recipe.domain_pattern[0]:
                    target_domain = recipe.domain_pattern[0]
            if target_domain:
                save_dir = self.root / target_domain
            else:
                save_dir = self.root
            save_dir.mkdir(parents=True, exist_ok=True)
            path = save_dir / f"{recipe.id}.json"

        # Inter-process lock using lockfile
        locks_dir = self.root / ".locks"
        locks_dir.mkdir(parents=True, exist_ok=True)
        lock_path = locks_dir / f"{recipe.id}.lock"

        with open(lock_path, "a+") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                # 1. Read authoritative disk revision and content digest if path exists
                disk_rev = None
                disk_digest = None
                if path.exists():
                    try:
                        existing_data = json.loads(path.read_text(encoding="utf-8"))
                        existing_lc = existing_data.get("lifecycle", {})
                        disk_rev = int(existing_lc.get("revision", 1))
                        disk_digest = existing_lc.get("content_digest")
                    except Exception:
                        disk_rev = None
                        disk_digest = None

                # 2. Check content mutation
                current_digest = compute_recipe_content_digest(recipe)
                base_digest = recipe.lifecycle.content_digest if recipe.lifecycle.content_digest is not None else disk_digest
                if base_digest is not None and base_digest != current_digest:
                    recipe.lifecycle.generation += 1
                    recipe.lifecycle.content_digest = current_digest
                    recipe.lifecycle.sessions_seen = []
                    recipe.lifecycle.agents_seen = []
                    recipe.lifecycle.consecutive_failures = 0
                    if recipe.lifecycle.state in (RecipeLifecycleState.VERIFIED_SHARED, RecipeLifecycleState.CURATED):
                        recipe.lifecycle.state = RecipeLifecycleState.VERIFIED_LOCAL
                else:
                    recipe.lifecycle.content_digest = current_digest

                # 3. Optimistic Concurrency Control (CAS check)
                if expected_revision is not None:
                    if disk_rev is None or disk_rev != expected_revision:
                        raise CASConflictError(
                            f"CAS conflict saving recipe '{recipe.id}': expected revision {expected_revision}, but found revision {disk_rev} on disk"
                        )

                # 4. Update revision
                if disk_rev is not None:
                    recipe.lifecycle.revision = disk_rev + 1
                else:
                    recipe.lifecycle.revision += 1

                # 5. Update utility score
                recipe.lifecycle.utility_score = calculate_utility_score(recipe)

                # 6. Write to atomic temp file and os.replace over destination path
                path.parent.mkdir(parents=True, exist_ok=True)
                temp_path = path.parent / f".tmp_{uuid4().hex}_{recipe.id}.json"
                temp_path.write_text(recipe.to_json(), encoding="utf-8")
                os.replace(temp_path, path)

                # 7. Update in-memory cache
                self._recipes[recipe.id] = recipe
                return path
            finally:
                try:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass

    def quarantine(self, recipe_id: str, reason: str = "administrative_quarantine") -> Recipe:
        recipe = self.get(recipe_id)
        if recipe is None:
            raise ValueError(f"Recipe not found: {recipe_id}")
        if hasattr(recipe, "lifecycle"):
            recipe.lifecycle.state = RecipeLifecycleState.QUARANTINED
            recipe.lifecycle.quarantine_reason = str(reason)
            recipe.lifecycle.quarantined_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.save(recipe)
        return recipe

    def restore(self, recipe_id: str) -> Recipe:
        recipe = self.get(recipe_id)
        if recipe is None:
            raise ValueError(f"Recipe not found: {recipe_id}")
        if not hasattr(recipe, "lifecycle") or recipe.lifecycle is None:
            recipe.lifecycle = LifecycleRecord()
        recipe.lifecycle.state = RecipeLifecycleState.VERIFIED_LOCAL
        recipe.lifecycle.consecutive_failures = 0
        recipe.lifecycle.quarantine_reason = None
        recipe.lifecycle.quarantined_at = None
        if hasattr(recipe, "metadata"):
            recipe.metadata["failure_count"] = 0
            recipe.metadata["last_failure_reason"] = None
        if hasattr(recipe, "health"):
            recipe.health.failures = 0
            recipe.health.precondition_failures = 0
            recipe.health.forbidden_anchor_failures = 0
            recipe.health.unknown_side_effects = 0
        self.save(recipe)
        return recipe

    def promote(
        self,
        recipe_id: str,
        target_state: str | None = None,
        policy: PromotionPolicy | None = None,
        *,
        privileged: bool = False,
        actor: str | None = None,
        reason: str | None = None,
    ) -> Recipe:
        recipe = self.get(recipe_id)
        if recipe is None:
            raise ValueError(f"Recipe not found: {recipe_id}")
        if not hasattr(recipe, "lifecycle") or recipe.lifecycle is None:
            recipe.lifecycle = LifecycleRecord()

        if target_state is not None:
            clean_state = str(target_state).strip().upper()
            valid_states = {
                RecipeLifecycleState.DRAFT,
                RecipeLifecycleState.VERIFIED_LOCAL,
                RecipeLifecycleState.VERIFIED_SHARED,
                RecipeLifecycleState.CURATED,
                RecipeLifecycleState.SUSPECT,
                RecipeLifecycleState.QUARANTINED,
                RecipeLifecycleState.ARCHIVED,
            }
            if clean_state not in valid_states:
                raise ValueError(f"Invalid target lifecycle state: {target_state}")

            qualifies = check_policy_qualifies_for(recipe, clean_state, policy=policy)
            if qualifies:
                recipe.lifecycle.state = clean_state
            else:
                if privileged:
                    if not actor or not str(actor).strip() or not reason or not str(reason).strip():
                        raise ValueError("Privileged promotion requires non-empty actor and reason")
                    prev_state = recipe.lifecycle.state
                    entry = {
                        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "actor": str(actor).strip(),
                        "reason": str(reason).strip(),
                        "from_state": prev_state,
                        "to_state": clean_state,
                        "revision": recipe.lifecycle.revision,
                    }
                    recipe.lifecycle.audit_log.append(entry)
                    recipe.lifecycle.state = clean_state
                else:
                    raise PermissionError(
                        f"Target state '{target_state}' requires policy evidence or explicit privileged=True override"
                    )
        else:
            next_state = evaluate_promotion(recipe, policy=policy)
            if not next_state:
                raise ValueError(
                    f"Recipe '{recipe_id}' does not satisfy promotion criteria from state '{recipe.lifecycle.state}'"
                )
            recipe.lifecycle.state = next_state
        self.save(recipe)
        return recipe

    def evict_low_utility(self, threshold: float = 0.5, archive: bool = True) -> list[str]:
        evicted = []
        for recipe in list(self.all()):
            u_score = calculate_utility_score(recipe)
            if hasattr(recipe, "lifecycle"):
                recipe.lifecycle.utility_score = u_score
            if u_score < threshold:
                if archive:
                    if hasattr(recipe, "lifecycle"):
                        recipe.lifecycle.state = RecipeLifecycleState.ARCHIVED
                    self.save(recipe)
                else:
                    self.delete(recipe.id)
                evicted.append(recipe.id)
        return evicted

    def delete(self, recipe_id: str) -> bool:
        self._recipes.pop(recipe_id, None)
        deleted = False
        if self.root.exists():
            for p in self.root.rglob(f"{recipe_id}.json"):
                if p.is_file():
                    p.unlink()
                    deleted = True
        return deleted

    def save_sitemap(self, url: str, observed_nodes: list[Any]) -> Path:
        domain = _domain_from_url(url)
        norm_path = _normalize_path(url)
        sitemaps_dir = self.root / domain / "sitemaps"
        sitemaps_dir.mkdir(parents=True, exist_ok=True)
        sitemap_file = sitemaps_dir / f"{norm_path}.json"
        interactive_items = []
        for node in observed_nodes:
            is_interactive = getattr(node, "interactive", None)
            if is_interactive is None and isinstance(node, dict):
                is_interactive = node.get("interactive", False)
            if is_interactive:
                role = getattr(node, "role", None) or (node.get("role") if isinstance(node, dict) else "")
                name = getattr(node, "name", None) or (node.get("name") if isinstance(node, dict) else "")
                ref = str(getattr(node, "ref", None) or (node.get("ref") if isinstance(node, dict) else ""))
                raw_val = getattr(node, "value", None) or (node.get("value") if isinstance(node, dict) else None)
                value = sanitize_value(raw_val, sensitive_name=name) if raw_val is not None else None
                interactive_items.append({
                    "role": role,
                    "name": name,
                    "ref": ref,
                    "value": value,
                })
        data = {
            "kind": "omnibrowser.semantic_sitemap",
            "schema_version": 1,
            "url": _safe_url(url),
            "domain": domain,
            "path": urlparse(url).path,
            "normalized_path": norm_path,
            "updated_at": time.time(),
            "interactive_elements": interactive_items,
        }
        temp_file = sitemaps_dir / f".tmp_{uuid4().hex[:8]}_{norm_path}.json"
        temp_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_file.replace(sitemap_file)
        return sitemap_file

    def get_sitemap(self, url: str) -> dict[str, Any] | None:
        domain = _domain_from_url(url)
        norm_path = _normalize_path(url)
        sitemap_file = self.root / domain / "sitemaps" / f"{norm_path}.json"
        if sitemap_file.exists():
            try:
                return json.loads(sitemap_file.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def record_success(
        self,
        recipe: Recipe,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        recipe.metadata["success_count"] = int(recipe.metadata.get("success_count", 0)) + 1
        recipe.metadata["last_failure_reason"] = None
        recipe.metadata["last_executed_at"] = time.time()
        if hasattr(recipe, "health"):
            recipe.health.executions += 1
            recipe.health.successes += 1
            recipe.health.health_score = round(recipe.health.successes / recipe.health.executions, 3)

        if hasattr(recipe, "lifecycle"):
            recipe.lifecycle.consecutive_failures = 0
            if session_id and session_id not in recipe.lifecycle.sessions_seen:
                recipe.lifecycle.sessions_seen.append(session_id)
            if agent_id and agent_id not in recipe.lifecycle.agents_seen:
                recipe.lifecycle.agents_seen.append(agent_id)

            promoted = evaluate_promotion(recipe)
            if promoted:
                recipe.lifecycle.state = promoted

            recipe.lifecycle.utility_score = calculate_utility_score(recipe)

    def record_failure(
        self,
        recipe: Recipe,
        *,
        step_index: int,
        reason: str,
        target: str | None,
        outcome: str | None = None,
    ) -> None:
        if "RiskGateError" in str(reason) or "allow_r4" in str(reason):
            return
        ledger = recipe.metadata.setdefault("failure_ledger", [])
        entry = {"step_index": step_index, "reason": _safe_reason(reason), "target": _safe_target(target)}
        ledger.append(entry)
        recipe.metadata["failure_ledger"] = ledger[-20:]
        recipe.metadata["failure_count"] = int(recipe.metadata.get("failure_count", 0)) + 1
        recipe.metadata["last_failure_reason"] = entry["reason"]
        recipe.metadata["last_executed_at"] = time.time()
        if hasattr(recipe, "health"):
            recipe.health.executions += 1
            recipe.health.failures += 1
            recipe.health.health_score = round(recipe.health.successes / recipe.health.executions, 3)

        if hasattr(recipe, "lifecycle"):
            recipe.lifecycle.consecutive_failures += 1
            triggered, q_reason = check_quarantine_triggers(recipe, last_outcome=outcome)
            if triggered:
                recipe.lifecycle.state = RecipeLifecycleState.QUARANTINED
                recipe.lifecycle.quarantine_reason = q_reason
                recipe.lifecycle.quarantined_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            recipe.lifecycle.utility_score = calculate_utility_score(recipe)

    def record_repair_candidate(self, repair: RepairCandidate) -> None:
        """Persist a dynamic healing event to the append-only repair log."""
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.repair_log, "a", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                payload = repair.to_dict() if hasattr(repair, "to_dict") else dict(repair)
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def list_repair_candidates(self, recipe_id: str | None = None) -> list[RepairCandidate]:
        """List recorded repair candidates, optionally filtered by recipe_id."""
        if not self.repair_log.exists():
            return []
        candidates: list[RepairCandidate] = []
        with open(self.repair_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    cand = RepairCandidate.from_dict(data)
                    if recipe_id is None or cand.recipe_id == recipe_id:
                        candidates.append(cand)
                except Exception:
                    continue
        return candidates

    get_repair_candidates = list_repair_candidates

    def apply_repair_candidate(
        self,
        repair_id: str,
        actor: str = "offline_learner",
        reason: str = "self_healing_promotion",
    ) -> Recipe:
        """Apply an approved repair candidate, promoting the healed candidate and incrementing generation."""
        candidates = self.list_repair_candidates()
        matched = [c for c in candidates if c.id == repair_id]
        if not matched:
            raise ValueError(f"Repair candidate not found: {repair_id}")
        repair = matched[-1]

        recipe = self.get(repair.recipe_id)
        if recipe is None:
            raise ValueError(f"Recipe not found: {repair.recipe_id}")

        # Verification of generation / content digest binding
        current_digest = recipe.lifecycle.content_digest if (hasattr(recipe, "lifecycle") and recipe.lifecycle is not None) else None
        if current_digest is None:
            current_digest = compute_recipe_content_digest(recipe)

        if repair.recipe_content_digest is None or repair.recipe_content_digest != current_digest:
            raise StaleRepairError(
                f"Stale repair candidate '{repair_id}': candidate digest '{repair.recipe_content_digest}' "
                f"does not match canonical recipe digest '{current_digest}' (generation {getattr(recipe.lifecycle, 'generation', 1)})."
            )

        if repair.step_index >= len(recipe.steps):
            raise IndexError(
                f"Repair step index {repair.step_index} out of range (recipe has {len(recipe.steps)} steps)"
            )

        step = recipe.steps[repair.step_index]
        target = step.target
        healed_cand = AnchorCandidate.from_dict(repair.healed_candidate)
        h_dict = healed_cand.to_dict()

        if isinstance(target, AnchorBundle):
            new_candidates = [healed_cand]
            for c in target.candidates:
                c_dict = c.to_dict() if hasattr(c, "to_dict") else dict(c)
                if c_dict.get("kind") == h_dict.get("kind") and (
                    c_dict.get("selector") == h_dict.get("selector")
                    and c_dict.get("role") == h_dict.get("role")
                    and c_dict.get("name") == h_dict.get("name")
                ):
                    continue
                new_candidates.append(c)
            step.target = AnchorBundle(candidates=new_candidates, fallback_action=target.fallback_action)
        elif isinstance(target, dict) and "candidates" in target:
            bundle = AnchorBundle.from_dict(target)
            new_candidates = [healed_cand]
            for c in bundle.candidates:
                c_dict = c.to_dict() if hasattr(c, "to_dict") else dict(c)
                if c_dict.get("kind") == h_dict.get("kind") and (
                    c_dict.get("selector") == h_dict.get("selector")
                    and c_dict.get("role") == h_dict.get("role")
                    and c_dict.get("name") == h_dict.get("name")
                ):
                    continue
                new_candidates.append(c)
            step.target = AnchorBundle(candidates=new_candidates, fallback_action=bundle.fallback_action)
        else:
            broken_cand = (
                AnchorCandidate.from_dict(repair.broken_candidate)
                if repair.broken_candidate
                else AnchorCandidate(kind="scoped_css", selector=str(target))
            )
            step.target = AnchorBundle(candidates=[healed_cand, broken_cand])

        if hasattr(recipe, "lifecycle") and recipe.lifecycle is not None:
            recipe.lifecycle.audit_log.append({
                "action": "apply_repair",
                "repair_id": repair_id,
                "actor": actor,
                "reason": reason,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })

        self.save(recipe)
        return recipe



@dataclass(slots=True)
class RecipeExecutionResult:
    ok: bool
    recipe_id: str
    completed_steps: int
    fallback_required: bool = False
    message: str = ""
    failure: dict[str, Any] | None = None
    outcome: str = ExecutionOutcome.CONFIRMED_SUCCESS
    risk_class: str = RiskClass.R0_READONLY
    reconciled: bool = False
    health_score: float = 1.0
    match_score: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "recipe_id": self.recipe_id, "completed_steps": self.completed_steps,
                "fallback_required": self.fallback_required, "message": self.message, "failure": self.failure,
                "outcome": self.outcome, "risk_class": self.risk_class, "reconciled": self.reconciled,
                "health_score": self.health_score, "match_score": self.match_score}

    def __getitem__(self, key: str) -> Any:
        return self.to_dict()[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)


def _safe_reason(reason: str) -> str:
    return sanitize_value(str(reason), sensitive_name="error")


def _safe_target(target: str | None) -> str | None:
    return sanitize_value(target, sensitive_name="target") if target else target


def sanitize_value(value: Any, *, sensitive_name: str = "") -> Any:
    """Remove common PII/secrets from values before recipe persistence."""
    if isinstance(value, dict):
        return {str(key): sanitize_value(item, sensitive_name=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_value(item, sensitive_name=sensitive_name) for item in value]
    if not isinstance(value, str):
        return value
    if _SECRET_KEY.search(sensitive_name) or _OTP_KEY.search(sensitive_name):
        return "{{password}}" if re.search(r"pass|secret", sensitive_name, re.I) else "{{token}}"
    result = _EMAIL.sub("{{email}}", value)
    result = _BEARER.sub(r"\1{{token}}", result)
    result = _JWT.sub("{{token}}", result)
    return result


class AnchorCompiler:
    """Compile a live element into a small, ordered locator bundle.

    Opaque refs and ElementHandles are document-local.  This descriptor is the
    durable identity used by learned recipes and intentionally contains no HTML.
    """

    _PRIORS = {"test_attr": 0.95, "role_name": 0.91, "label_input": 0.91,
               "scoped_css": 0.78, "neighborhood": 0.70}

    @classmethod
    def compile_handle(cls, handle: Any) -> dict[str, Any]:
        raw = handle.evaluate("""element => ({
            tag: element.tagName.toLowerCase(), role: element.getAttribute('role'),
            name: element.getAttribute('aria-label') || element.getAttribute('name') || '',
            id: element.id || '', testid: element.getAttribute('data-testid') ||
              element.getAttribute('data-qa') || element.getAttribute('data-cy') || '',
            label: element.labels && element.labels[0] ? element.labels[0].innerText : '',
            type: element.getAttribute('type') || '',
            form: element.closest('form') ? (element.closest('form').getAttribute('name') || element.closest('form').getAttribute('action') || '') : ''
        })""")
        candidates: list[dict[str, Any]] = []
        testid = str(raw.get("testid") or "")
        if testid:
            candidates.append({"kind": "test_attr", "selector": f'[data-testid="{testid}"]', "score": cls._PRIORS["test_attr"]})
        role = str(raw.get("role") or ("button" if raw.get("tag") == "button" else "textbox" if raw.get("tag") in {"input", "textarea"} else ""))
        name = str(raw.get("name") or raw.get("label") or "")
        if role and name:
            candidates.append({"kind": "role_name", "role": role, "name": _safe_json(name, name="anchor_name"), "score": cls._PRIORS["role_name"]})
        label = str(raw.get("label") or "")
        if label and raw.get("tag") in {"input", "textarea", "select"}:
            candidates.append({"kind": "label_input", "label": _safe_json(label, name="label"), "score": cls._PRIORS["label_input"]})
        element_id = str(raw.get("id") or "")
        if element_id and not re.search(r"(?:^|[-_])\d{4,}(?:$|[-_])", element_id):
            candidates.append({"kind": "scoped_css", "selector": f'#{element_id}', "score": 0.90})
        field_name = str(raw.get("name") or "")
        if field_name:
            candidates.append({"kind": "scoped_css", "selector": f'{raw["tag"]}[name="{field_name}"]', "score": cls._PRIORS["scoped_css"]})
        return {
            "candidates": candidates[:6],
            "context": {
                "form": _safe_json(str(raw.get("form") or ""), name="form"),
                "type": _safe_json(str(raw.get("type") or ""), name="type"),
                "tag": _safe_json(str(raw.get("tag") or ""), name="tag"),
            }
        }

    @classmethod
    def resolve(cls, page: Page, target: Any, *, mutating: bool) -> Any:
        ordinal = None
        if isinstance(target, Mapping):
            ordinal = target.get("ordinal")
            if "selector" in target and not target.get("candidates"):
                candidates = [{"kind": "scoped_css", "selector": str(target["selector"])}]
            else:
                candidates = target.get("candidates", [])
        else:
            candidates = [{"kind": "scoped_css", "selector": str(target)}]
        ambiguous = False
        for candidate in candidates:
            cand_ordinal = candidate.get("ordinal", ordinal)
            kind = candidate.get("kind")
            if kind in {"test_attr", "scoped_css"}:
                selector = str(candidate.get("selector", ""))
                if _FRAGILE_SELECTOR.search(selector):
                    continue
                locator = page.locator(selector)
            elif kind == "role_name":
                locator = page.get_by_role(str(candidate["role"]), name=str(candidate["name"]))
            elif kind == "label_input":
                locator = page.get_by_label(str(candidate["label"]))
            else:
                continue
            count = locator.count()
            if count == 1:
                return locator
            if count > 1 and cand_ordinal is not None:
                if cand_ordinal in ("last", -1):
                    return locator.last
                if cand_ordinal in ("first", 0):
                    return locator.first
                if isinstance(cand_ordinal, int):
                    return locator.nth(cand_ordinal)
            ambiguous = ambiguous or count > 1
        if ambiguous and mutating:
            raise AnchorAmbiguous("Recipe anchor matched multiple elements; explicit ordinal intent is required")
        raise ValueError("Recipe anchor did not resolve to exactly one element")


class AnchorResolver:
    """Resolves multi-candidate AnchorBundles with dynamic scoring and ambiguity safety."""

    @classmethod
    def resolve(
        cls,
        page: Page,
        target: str | AnchorBundle | dict[str, Any],
        mutating: bool = True,
        risk_class: RiskClass | str = RiskClass.R1,
    ) -> tuple[Any, AnchorCandidate | None, float]:
        clean_risk = str(getattr(risk_class, "value", risk_class)).strip().upper()
        is_r4 = clean_risk in {RiskClass.R4_IRREVERSIBLE, "R4"}
        is_r3 = clean_risk in {RiskClass.R3_PERSISTENT_MUTATION, "R3"}

        if isinstance(target, str):
            locator = page.locator(target)
            count = locator.count()
            if count == 0:
                raise AnchorNotFound(f"Target selector not found: {target}")
            if count > 1:
                raise AnchorAmbiguous(f"Target selector is ambiguous ({count} matches): {target}")
            return locator, None, 1.0

        if isinstance(target, dict) and not isinstance(target, AnchorBundle):
            if "candidates" in target:
                bundle = AnchorBundle.from_dict(target)
            elif "selector" in target:
                bundle = AnchorBundle(candidates=[
                    AnchorCandidate(
                        kind=str(target.get("kind", "scoped_css")),
                        selector=target["selector"],
                        score=float(target.get("score", 0.80)),
                    )
                ])
            elif "role" in target and "name" in target:
                bundle = AnchorBundle(candidates=[
                    AnchorCandidate(
                        kind="role_name",
                        role=target["role"],
                        name=target["name"],
                        score=float(target.get("score", 0.90)),
                    )
                ])
            else:
                bundle = AnchorBundle.from_dict(target)
        elif isinstance(target, AnchorBundle):
            bundle = target
        else:
            raise AnchorNotFound(f"Unsupported target type: {type(target)}")

        candidates = sorted(bundle.candidates, key=lambda c: getattr(c, "score", 0.80), reverse=True)
        for candidate in candidates:
            cand_score = float(getattr(candidate, "score", 0.80))
            is_text_only = (candidate.kind == "text") or (not candidate.selector and not candidate.role and bool(candidate.name))
            is_neighborhood = (candidate.kind == "neighborhood")

            # Risk-aware candidate qualification floor (R3 / R4):
            # Count == 1 establishes uniqueness, NOT authorization to mutate high-risk targets.
            # For R3: require candidate score >= 0.80; text-only candidates are strictly disqualified.
            # For R4: require candidate score >= 0.90; text-only and generic neighborhood candidates are strictly disqualified.
            if is_r4:
                if cand_score < 0.90 or is_text_only or is_neighborhood:
                    continue
            elif is_r3:
                if cand_score < 0.80 or is_text_only:
                    continue
            try:
                kind = candidate.kind
                if kind in ("test_attr", "scoped_css", "neighborhood"):
                    if not candidate.selector:
                        continue
                    locator = page.locator(candidate.selector)
                elif kind == "role_name":
                    if not candidate.role:
                        continue
                    if candidate.name:
                        locator = page.get_by_role(candidate.role, name=candidate.name)
                    else:
                        locator = page.get_by_role(candidate.role)
                elif kind == "label_input":
                    if not candidate.name:
                        continue
                    locator = page.get_by_label(candidate.name)
                elif kind == "text":
                    if not candidate.name:
                        continue
                    locator = page.get_by_text(candidate.name, exact=True)
                else:
                    if candidate.selector:
                        locator = page.locator(candidate.selector)
                    elif candidate.name:
                        locator = page.get_by_text(candidate.name, exact=True)
                    else:
                        continue

                n = locator.count()
                if n == 0:
                    continue
                if n > 1:
                    # Disqualified as ambiguous! Never click .first() for mutating steps
                    continue
                return locator, candidate, float(candidate.score)
            except Exception:
                continue

        raise AnchorNotFound("All candidates in AnchorBundle failed or were ambiguous")



class PostconditionWatcher:
    """One polling implementation shared by opaque-ref and selector recipes."""

    @staticmethod
    def wait(page: Page, locator: Any, expect: Mapping[str, Any], timeout_ms: int, *, initial_url: str) -> None:
        if not expect:
            return
        PostconditionWatcher.poll(
            lambda: PostconditionWatcher._met(page, locator, expect, initial_url), timeout_ms,
            lambda: page.wait_for_timeout(50),
        )

    @staticmethod
    def poll(check: Any, timeout_ms: int, pause: Any) -> None:
        """Poll a predicate so every action path has identical timeout semantics."""
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            try:
                if check():
                    return
            except Exception:
                pass
            if time.monotonic() >= deadline:
                raise ValueError(f"postcondition timed out after {timeout_ms}ms")
            pause()

    @staticmethod
    def _met(page: Page, locator: Any, expect: Mapping[str, Any], initial_url: str) -> bool:
        if expect.get("url_changed") and page.url == initial_url:
            return False
        if "url" in expect and page.url != str(expect["url"]):
            return False
        pattern = expect.get("url_matches", expect.get("url_regex"))
        if pattern is not None and re.search(str(pattern), page.url) is None:
            return False
        body = None
        if "text_present" in expect or "text" in expect:
            body = page.locator("body").inner_text(timeout=500)
        if "text_present" in expect and str(expect["text_present"]) not in body:
            return False
        if "text" in expect and str(expect["text"]) not in body:
            return False
        return "visible" not in expect or bool(locator.is_visible()) == bool(expect["visible"])


class LearningMemory:
    """Durable, multi-process semantic journal for learned procedural memory."""

    def __init__(self, root: str | Path | None = None, domain: str | None = None):
        self.root = _memory_root(root)
        self.domain = domain
        self.root.mkdir(parents=True, exist_ok=True)
        if domain:
            self.domain_dir = self.root / domain
            self.candidates_dir = self.domain_dir / "candidates"
        else:
            self.domain_dir = self.root
            self.candidates_dir = self.root / "candidates"
        self.domain_dir.mkdir(parents=True, exist_ok=True)
        self.candidates_dir.mkdir(parents=True, exist_ok=True)
        self.traces_db = self.domain_dir / "traces.db"
        self.ledger_db = self.domain_dir / "ledger.db"
        self._init()

    def _connection(self, path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init(self) -> None:
        with self._connection(self.traces_db) as db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, goal TEXT NOT NULL, policy TEXT NOT NULL, agent_id TEXT, status TEXT NOT NULL, url TEXT, created_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS events (run_id TEXT NOT NULL, seq INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(run_id, seq))")
        with self._connection(self.ledger_db) as db:
            db.execute("CREATE TABLE IF NOT EXISTS executions (candidate_id TEXT, outcome TEXT NOT NULL, recorded_at REAL NOT NULL, details TEXT NOT NULL)")

    def begin(self, goal: str, *, policy: str = "suggest", agent_id: str | None = None) -> dict[str, Any]:
        if not goal.strip():
            raise ValueError("learning goal is required")
        run_id = f"lr_{uuid4().hex}"
        with self._connection(self.traces_db) as db:
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?, 'open', NULL, ?)", (run_id, _safe_json(goal, name="goal"), policy, agent_id, time.time()))
        return {"run_id": run_id, "policy": policy, "status": "open"}

    @staticmethod
    def _value_ref(action: str, value: Any, anchor: Mapping[str, Any]) -> Any:
        if value is None:
            return None
        if action not in {"fill", "select", "press"}:
            return None
        text = json.dumps(anchor).lower()
        val_str = str(value)
        if _BEARER.search(val_str) or _JWT.search(val_str) or "bearer" in text or "token" in text:
            name, sensitivity = "token", "secret"
        elif _SECRET_KEY.search(text) or "current-password" in text or "new-password" in text or "password" in text:
            name, sensitivity = "password", "secret"
        elif _OTP_KEY.search(text) or "one-time-code" in text:
            name, sensitivity = "verification_code", "secret"
        elif _EMAIL_KEY.search(text) or _EMAIL.search(val_str):
            name, sensitivity = "email", "pii"
        elif _PHONE_KEY.search(text):
            name, sensitivity = "phone", "pii"
        elif _SEARCH_KEY.search(text):
            name, sensitivity = "search_query", "runtime"
        elif _NAME_KEY.search(text):
            name, sensitivity = "username", "pii"
        else:
            name, sensitivity = "input_value", "runtime"
        return {"$ref": name, "kind": "runtime_param", "sensitivity": sensitivity, "persist_value": False}

    def append_action(self, run_id: str, *, action: str, anchor: Mapping[str, Any], value: Any = None,
                      expect: Mapping[str, Any] | None = None, url: str = "", duration_ms: int = 0) -> dict[str, Any]:
        with self._connection(self.traces_db) as db:
            row = db.execute("SELECT status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None or row[0] != "open":
                raise ValueError(f"learning run is not open: {run_id}")
            sequence = int(db.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE run_id = ?", (run_id,)).fetchone()[0])
            event = {"run_id": run_id, "seq": sequence, "action": action, "target": _safe_json(dict(anchor), name="anchor"),
                     "value": self._value_ref(action, value, anchor), "expect": _safe_json(dict(expect or {}), name="expect"),
                     "url": _safe_url(url), "duration_ms": int(duration_ms)}
            db.execute("INSERT INTO events VALUES (?, ?, ?)", (run_id, sequence, json.dumps(event, separators=(",", ":"))))
        return event

    def complete(self, run_id: str, *, success: bool, validation: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._connection(self.traces_db) as db:
            row = db.execute("SELECT goal, url FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if row is None:
                raise ValueError(f"unknown learning run: {run_id}")
            events = [json.loads(item[0]) for item in db.execute("SELECT payload FROM events WHERE run_id = ? ORDER BY seq", (run_id,))]
            db.execute("UPDATE runs SET status = ? WHERE run_id = ?", ("succeeded" if success else "failed", run_id))
        if not success:
            return {"run_id": run_id, "status": "failed", "candidate_id": None}
        if not events:
            raise ValueError("cannot distill an empty learning run")
        url = next((event["url"] for event in events if event.get("url")), "")
        recipe_id = f"learned-{uuid4().hex[:12]}"
        domain_pat = urlsplit(url).netloc or "*"
        domain = self.domain or (_domain_from_url(url) if url else None)
        recipe = distill_recipe(recipe_id, str(row[0]), events, domain_pattern=domain_pat,
                                validation=_safe_json(dict(validation or {}), name="validation"), metadata={"automatic": True, "status": "draft", "run_id": run_id})

        # Save to both local candidate dir and domain-partitioned candidate dir
        path = self.candidates_dir / f"{recipe_id}.json"
        path.write_text(recipe.to_json(), encoding="utf-8")
        if domain and (self.root / domain / "candidates") != self.candidates_dir:
            domain_cand = self.root / domain / "candidates"
            domain_cand.mkdir(parents=True, exist_ok=True)
            (domain_cand / f"{recipe_id}.json").write_text(recipe.to_json(), encoding="utf-8")

        with self._connection(self.ledger_db) as db:
            db.execute("INSERT INTO executions VALUES (?, ?, ?, ?)", (recipe_id, "distilled", time.time(), json.dumps({"run_id": run_id})))
        return {"run_id": run_id, "status": "draft", "candidate_id": recipe_id}

    def candidates(self, url: str | None = None) -> list[dict[str, Any]]:
        result = []
        seen_ids = set()
        search_dirs = [self.candidates_dir]
        if self.domain:
            search_dirs.append(self.root / self.domain / "candidates")
        if url:
            u_domain = _domain_from_url(url)
            search_dirs.append(self.root / u_domain / "candidates")
        for sub_cand in self.root.glob("*/candidates"):
            if sub_cand.is_dir():
                search_dirs.append(sub_cand)

        for cand_dir in set(search_dirs):
            if not cand_dir.exists():
                continue
            for path in sorted(cand_dir.glob("*.json")):
                if path.stem in seen_ids:
                    continue
                try:
                    recipe = Recipe.from_json(path)
                except Exception:
                    continue
                if url is None or recipe.matches_url(_safe_url(url)):
                    seen_ids.add(recipe.id)
                    result.append(recipe.to_dict())
        return result

    def promote(self, candidate_id: str, *, approve: bool = False) -> dict[str, Any]:
        if not approve:
            raise ValueError("promotion requires explicit --approve")
        # Search for candidate in self.candidates_dir and domain subdirectories
        target_path = None
        for p in self.root.rglob(f"{candidate_id}.json"):
            target_path = p
            break
        if not target_path or not target_path.exists():
            raise ValueError(f"unknown candidate: {candidate_id}")
        recipe = Recipe.from_json(target_path)
        recipe.metadata["status"] = "promoted"
        recipe.metadata["promoted_at"] = int(time.time())
        target_path.write_text(recipe.to_json(), encoding="utf-8")
        with self._connection(self.ledger_db) as db:
            db.execute("INSERT INTO executions VALUES (?, ?, ?, ?)", (candidate_id, "promoted", time.time(), "{}"))
        return {"candidate_id": candidate_id, "status": "promoted"}

    def suggest(self, url: str | None = None) -> list[dict[str, Any]]:
        suggestions = []
        for recipe in self.candidates(url):
            metadata = recipe.get("metadata", {})
            score = 0.0
            for step in recipe.get("steps", []):
                target = step.get("target", {})
                if isinstance(target, Mapping):
                    score = max(score, max((float(item.get("score", 0)) for item in target.get("candidates", [])), default=0.0))
            params = sorted({str(step.get("value", {}).get("$ref")) for step in recipe.get("steps", []) if isinstance(step.get("value"), Mapping) and step["value"].get("$ref")})
            confidence = round(score, 2) if score > 0 else (0.95 if metadata.get("status") == "promoted" else 0.88)
            suggestions.append({"candidate_id": recipe["id"], "status": metadata.get("status", "draft"), "confidence": confidence, "required_params": params})
        return sorted(suggestions, key=lambda item: item["confidence"], reverse=True)


def distill_recipe(recipe_id: str, name: str, steps: Sequence[Mapping[str, Any] | RecipeStep], *,
                   domain_pattern: str | list[str] = "*", description: str = "",
                   validation: Mapping[str, Any] | None = None, metadata: Mapping[str, Any] | None = None) -> Recipe:
    """Turn a successful interaction trace into a recipe without retaining secrets."""
    sanitized_steps: list[RecipeStep] = []
    for raw in steps:
        step = raw if isinstance(raw, RecipeStep) else RecipeStep.from_dict(raw)
        # A learned event may use the semantic journal spelling (`target`) or
        # the legacy RecipeStep spelling.  Eval is never promoted from a trace.
        if step.action == "eval":
            continue
        target = _safe_json(step.target, name="target")
        value = step.value if isinstance(step.value, Mapping) and "$ref" in step.value else sanitize_value(
            step.value, sensitive_name=json.dumps(target, default=str)
        )
        sanitized_steps.append(RecipeStep(step.action, target, value,
                                          _substitute(sanitize_value(step.expect), {}), step.timeout_ms))
    if not sanitized_steps:
        raise ValueError("learned recipe contains no permitted actions")
    return Recipe(recipe_id, name, description, domain_pattern, sanitized_steps,
                  _safe_json(dict(validation or {}), name="validation"), _defaults({**dict(metadata or {}), "automatic": True}))


class RecipeEngine:
    def __init__(self, store: RecipeStore | None = None):
        self.store = store

    def execute(
        self,
        recipe: Recipe | str,
        page: Page,
        params: Mapping[str, Any] | None = None,
        *,
        manager: PageManager | None = None,
        allow_r4: bool = False,
        session_id: str | None = None,
        agent_id: str | None = None,
    ) -> RecipeExecutionResult:
        recipe_id = recipe if isinstance(recipe, str) else getattr(recipe, "id", None)
        if self.store is not None and recipe_id:
            canonical = self.store.get(recipe_id)
            if canonical is not None and hasattr(canonical, "lifecycle") and canonical.lifecycle.state == RecipeLifecycleState.QUARANTINED:
                q_reason = getattr(canonical.lifecycle, "quarantine_reason", "quarantined")
                raise QuarantinedRecipeError(f"Cannot execute recipe '{recipe_id}': Recipe is QUARANTINED ({q_reason})")
            if isinstance(recipe, str):
                if canonical is None:
                    raise KeyError(f"Unknown recipe: {recipe_id}")
                recipe = canonical
        elif isinstance(recipe, str):
            raise ValueError("RecipeStore is required when recipe is an id")

        if hasattr(recipe, "lifecycle") and recipe.lifecycle.state == RecipeLifecycleState.QUARANTINED:
            q_reason = getattr(recipe.lifecycle, "quarantine_reason", "quarantined")
            raise QuarantinedRecipeError(f"Cannot execute recipe '{recipe.id}': Recipe is QUARANTINED ({q_reason})")

        params = params or {}
        page_manager = manager or (self.store and getattr(self.store, "manager", None)) or manager_for_page(page)

        recipe_risk = get_recipe_risk(recipe)
        safety = getattr(recipe, "safety", None) or SafetySpec()
        if recipe_risk == RiskClass.R4_IRREVERSIBLE and not allow_r4 and not getattr(safety, "allow_r4", False):
            raise RiskGateError(f"Recipe '{recipe.id}' requires R4 (irreversible/destructive) actions. Execution blocked without explicit allow_r4=True.")

        persistent_mutation_started = False
        index = 0
        target = None
        baseline_nodes: list[Any] = []
        initial_url = page.url

        try:
            if not recipe.matches_url(page.url):
                raise ValueError(f"recipe domain does not match active URL: {page.url}")

            # Guard Phase: evaluate anchors against page if manager is available
            if page_manager is not None:
                try:
                    obs = page_manager.observe(page)
                    baseline_nodes = obs.tree
                    initial_url = obs.page_url or page.url
                    match_res = match_page_state(recipe, initial_url, obs.tree)
                    if not match_res.get("matched", True):
                        reason = match_res.get("reason", "precondition_failed")
                        if "forbidden" in reason:
                            if hasattr(recipe, "health"):
                                recipe.health.forbidden_anchor_failures += 1
                            raise ForbiddenAnchorError(f"Forbidden anchor detected on page: {reason}")
                        if hasattr(recipe, "health"):
                            recipe.health.precondition_failures += 1
                        raise PreconditionFailedError(f"Precondition failed: {reason}")
                except (ForbiddenAnchorError, PreconditionFailedError):
                    raise
                except Exception:
                    pass

            for key in _TEMPLATE.findall(json.dumps(recipe.to_dict())):
                if key not in params:
                    raise ValueError(f"missing recipe parameter: {key}")

            for index, step in enumerate(recipe.steps):
                target = _substitute(step.target, params)
                value = _substitute(step.value, params)
                expect = _substitute(step.expect, params)
                timeout = min(max(int(step.timeout_ms), 1), 30000)

                step_explicit = getattr(step, "risk_class", None)
                step_risk = classify_action_risk(step.action, target, value, explicit_risk=step_explicit)

                # Individual step R4 authorization check
                if step_risk == RiskClass.R4_IRREVERSIBLE and not allow_r4 and not getattr(safety, "allow_r4", False):
                    raise RiskGateError(f"Step {index} ({step.action}) requires R4 (irreversible/destructive) actions. Execution blocked without explicit allow_r4=True.")

                if step_risk in {RiskClass.R3_PERSISTENT_MUTATION, RiskClass.R4_IRREVERSIBLE}:
                    # Gate 3: JIT Persistent-Write Barrier immediately before dispatching R3/R4
                    if page_manager is not None:
                        obs_jit = page_manager.observe(page)
                        jit_match = match_page_state(recipe, page.url, obs_jit.tree)
                        if not jit_match.get("matched", True):
                            reason = jit_match.get("reason", "jit_barrier_failed")
                            if "forbidden" in reason:
                                if hasattr(recipe, "health"):
                                    recipe.health.forbidden_anchor_failures += 1
                                raise ForbiddenAnchorError(f"JIT Write Barrier aborted: Forbidden anchor detected immediately before {step_risk} action: {reason}")
                            if hasattr(recipe, "health"):
                                recipe.health.precondition_failures += 1
                            raise PreconditionFailedError(f"JIT Write Barrier aborted: Precondition or similarity failed immediately before {step_risk} action: {reason}")

                def mark_mutation_started():
                    nonlocal persistent_mutation_started
                    if step_risk in {RiskClass.R3_PERSISTENT_MUTATION, RiskClass.R4_IRREVERSIBLE}:
                        persistent_mutation_started = True

                step_res = self._execute_step(
                    page, page_manager, step.action, target, value, expect, timeout,
                    on_action_dispatched=mark_mutation_started,
                    risk_class=step_risk,
                )
                loc, resolved_cand, cand_score = step_res if step_res is not None else (None, None, 1.0)

                is_bundle = isinstance(target, AnchorBundle) or (isinstance(target, dict) and "candidates" in target)
                bundle_obj = target if isinstance(target, AnchorBundle) else (AnchorBundle.from_dict(target) if is_bundle else None)
                if bundle_obj and bundle_obj.candidates and resolved_cand is not None:
                    primary_cand = bundle_obj.candidates[0]
                    p_dict = primary_cand.to_dict() if hasattr(primary_cand, "to_dict") else dict(primary_cand)
                    r_dict = resolved_cand.to_dict() if hasattr(resolved_cand, "to_dict") else dict(resolved_cand)
                    if p_dict != r_dict:
                        import sys
                        print(f"[HEALED] Step {index}: {resolved_cand.kind} fallback (conf: {cand_score:.2f})", file=sys.stderr)
                        rec_gen = 1
                        rec_digest = None
                        if hasattr(recipe, "lifecycle") and recipe.lifecycle is not None:
                            rec_gen = getattr(recipe.lifecycle, "generation", 1)
                            rec_digest = getattr(recipe.lifecycle, "content_digest", None)
                        if rec_digest is None and isinstance(recipe, Recipe):
                            rec_digest = compute_recipe_content_digest(recipe)

                        repair = RepairCandidate(
                            recipe_id=recipe.id if hasattr(recipe, "id") else str(recipe),
                            step_index=index,
                            broken_candidate=p_dict,
                            healed_candidate=r_dict,
                            confidence=cand_score,
                            recipe_generation=rec_gen,
                            recipe_content_digest=rec_digest,
                            context={"action": step.action, "url": page.url},
                        )
                        if self.store is not None and hasattr(self.store, "record_repair_candidate"):
                            self.store.record_repair_candidate(repair)


            # Postcondition Phase
            if getattr(recipe, "postconditions", None) and page_manager is not None:
                obs_post = page_manager.observe(page)
                for post in recipe.postconditions:
                    if not _find_anchor_in_tree(post, obs_post.tree):
                        raise RuntimeError(f"Postcondition anchor not found: {post}")

            resolved_validation = _substitute(recipe.validation, params)
            self._validate(resolved_validation, page, page_manager)

        except (ForbiddenAnchorError, PreconditionFailedError, RiskGateError, QuarantinedRecipeError):
            raise
        except Exception as error:
            reason = _safe_reason(str(error))
            failure = {"step_index": index, "reason": reason, "target": _safe_target(target)}
            reconciled = False
            outcome = ExecutionOutcome.SAFE_FAILURE

            if persistent_mutation_started:
                policy = getattr(safety, "unknown_effect_policy", "reconcile")
                if policy == "reconcile" and getattr(recipe, "postconditions", None) and page_manager is not None:
                    try:
                        obs_recon = page_manager.observe(page)
                        if verify_transition_reconciliation(
                            recipe.postconditions,
                            baseline_nodes,
                            obs_recon.tree,
                            initial_url,
                            obs_recon.page_url or page.url,
                        ):
                            # Succeeded despite the error via transition-aware verification!
                            reconciled = True
                            outcome = ExecutionOutcome.CONFIRMED_SUCCESS
                            if self.store:
                                self.store.record_success(recipe, session_id=session_id, agent_id=agent_id)
                            else:
                                recipe.metadata["success_count"] = int(recipe.metadata.get("success_count", 0)) + 1
                                recipe.metadata["last_failure_reason"] = None
                                recipe.metadata["last_executed_at"] = time.time()
                                if hasattr(recipe, "health"):
                                    recipe.health.executions += 1
                                    recipe.health.successes += 1
                                    recipe.health.health_score = round(recipe.health.successes / recipe.health.executions, 3)
                                if hasattr(recipe, "lifecycle"):
                                    recipe.lifecycle.consecutive_failures = 0
                                    if session_id and session_id not in recipe.lifecycle.sessions_seen:
                                        recipe.lifecycle.sessions_seen.append(session_id)
                                    if agent_id and agent_id not in recipe.lifecycle.agents_seen:
                                        recipe.lifecycle.agents_seen.append(agent_id)
                                    promoted = evaluate_promotion(recipe)
                                    if promoted:
                                        recipe.lifecycle.state = promoted
                                    recipe.lifecycle.utility_score = calculate_utility_score(recipe)
                            return RecipeExecutionResult(
                                True, recipe.id, index + 1, False,
                                "Recipe recovered via transition-aware postcondition reconciliation.",
                                None, outcome=outcome, risk_class=recipe_risk, reconciled=True,
                                health_score=recipe.health.health_score if hasattr(recipe, "health") else 1.0,
                            )
                    except Exception:
                        pass
                outcome = ExecutionOutcome.UNKNOWN_SIDE_EFFECT
                if hasattr(recipe, "health"):
                    recipe.health.unknown_side_effects += 1

            if self.store:
                self.store.record_failure(recipe, step_index=index, reason=reason, target=target, outcome=outcome)
            else:
                if hasattr(recipe, "lifecycle"):
                    recipe.lifecycle.consecutive_failures += 1
                    triggered, q_reason = check_quarantine_triggers(recipe, last_outcome=outcome)
                    if triggered:
                        recipe.lifecycle.state = RecipeLifecycleState.QUARANTINED
                        recipe.lifecycle.quarantine_reason = q_reason
                        recipe.lifecycle.quarantined_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    recipe.lifecycle.utility_score = calculate_utility_score(recipe)
            return RecipeExecutionResult(
                False, recipe.id, index, True,
                f"Recipe fast path failed; fallback to Level 1 observe() / act() and re-scan the page. Reason: {reason}",
                failure, outcome=outcome, risk_class=recipe_risk, reconciled=reconciled,
                health_score=recipe.health.health_score if hasattr(recipe, "health") else 1.0
            )


        recipe.metadata["last_failure_reason"] = None
        if self.store:
            self.store.record_success(recipe, session_id=session_id, agent_id=agent_id)
        else:
            recipe.metadata["success_count"] = int(recipe.metadata.get("success_count", 0)) + 1
            recipe.metadata["last_executed_at"] = time.time()
            if hasattr(recipe, "health"):
                recipe.health.executions += 1
                recipe.health.successes += 1
                recipe.health.health_score = round(recipe.health.successes / recipe.health.executions, 3)
            if hasattr(recipe, "lifecycle"):
                recipe.lifecycle.consecutive_failures = 0
                if session_id and session_id not in recipe.lifecycle.sessions_seen:
                    recipe.lifecycle.sessions_seen.append(session_id)
                if agent_id and agent_id not in recipe.lifecycle.agents_seen:
                    recipe.lifecycle.agents_seen.append(agent_id)
                promoted = evaluate_promotion(recipe)
                if promoted:
                    recipe.lifecycle.state = promoted
                recipe.lifecycle.utility_score = calculate_utility_score(recipe)

        return RecipeExecutionResult(
            True, recipe.id, len(recipe.steps), False,
            "Recipe completed successfully.",
            None, outcome=ExecutionOutcome.CONFIRMED_SUCCESS, risk_class=recipe_risk,
            health_score=recipe.health.health_score if hasattr(recipe, "health") else 1.0
        )

    run = execute

    @staticmethod
    def _execute_step(
        page: Page,
        manager: PageManager | None,
        action: str,
        target: Any,
        value: Any,
        expect: dict[str, Any],
        timeout: int,
        on_action_dispatched: Any = None,
        risk_class: RiskClass | str = RiskClass.R1,
    ) -> tuple[Any, AnchorCandidate | None, float]:
        if action == "eval":
            if on_action_dispatched:
                on_action_dispatched()
            page.evaluate(str(value))
            return None, None, 1.0
        if target is None:
            raise ValueError(f"{action} requires target")
        if manager is not None:
            try:
                ref = DOMNodeRef.parse(str(target))
            except ValueError:
                ref = None
            if ref is not None:
                native = {"click": "click", "fill": "fill", "select": "select"}.get(action)
                if native is None:
                    raise ValueError(f"Unsupported ref action: {action}")
                if on_action_dispatched:
                    on_action_dispatched()
                act(page, native, ref, None if value is None else str(value), {**expect, "timeout_ms": timeout}, manager=manager)
                return None, None, 1.0
        initial_url = page.url
        resolved_cand = None
        score = 1.0
        try:
            locator, resolved_cand, score = AnchorResolver.resolve(
                page, target, mutating=action in {"click", "fill", "select", "upload"}, risk_class=risk_class
            )
        except (AnchorNotFound, AnchorAmbiguous):
            raise
        except Exception:
            locator = AnchorCompiler.resolve(page, target, mutating=action in {"click", "fill", "select", "upload"})

        if on_action_dispatched:
            on_action_dispatched()
        if action == "click":

            try:
                locator.click(timeout=timeout)
            except Exception:
                locator.click(force=True, timeout=timeout)
        elif action == "fill":
            try:
                locator.fill(str(value), timeout=timeout)
            except Exception:
                locator.evaluate('(el, text) => { el.focus(); document.execCommand("insertText", false, text); }', str(value))
        elif action == "select":
            locator.select_option(str(value), timeout=timeout)
        elif action == "upload":
            files = value if isinstance(value, list) else [str(value)]
            locator.set_input_files(files, timeout=timeout)
        elif action == "wait_for":
            state = str(value or expect.get("state", "visible"))
            locator.wait_for(state=state, timeout=timeout)
        else:
            raise ValueError(f"Unsupported recipe action: {action}")
        PostconditionWatcher.wait(page, locator, expect, timeout, initial_url=initial_url)
        return locator, resolved_cand, score

    @staticmethod
    def _check_expectation(page: Page, locator: Any, expect: Mapping[str, Any]) -> None:
        PostconditionWatcher.wait(page, locator, expect, 5000, initial_url=page.url)

    @staticmethod
    def _validate(validation: Mapping[str, Any], page: Page, manager: PageManager | None) -> None:
        del manager
        if not validation:
            return
        RecipeEngine._check_expectation(page, page.locator("body"), validation)


# Friendly aliases used by callers that describe this operation as mining.
distill = distill_recipe


@dataclass
class FlightJournal:
    session_id: str
    domain: str
    events: list[dict[str, Any]] = field(default_factory=list)
    initial_url: str = ""
    last_url: str = ""
    started_at: float = field(default_factory=time.time)


class FlightRecorder:
    """Implicit zero-effort action flight recorder for autonomous procedural memory."""
    _active_journals: dict[str, FlightJournal] = {}
    _lock = threading.Lock()

    @classmethod
    def get_session_id(cls, page: Any) -> str:
        session_id = getattr(page, "_omnibrowser_session_id", None)
        if not session_id:
            session_id = f"session_{uuid4().hex[:8]}"
            try:
                setattr(page, "_omnibrowser_session_id", session_id)
            except Exception:
                pass
        return session_id

    @classmethod
    def get_journal(cls, page: Any) -> FlightJournal:
        session_id = cls.get_session_id(page)
        with cls._lock:
            if session_id not in cls._active_journals:
                initial_url = getattr(page, "url", "")
                domain = _domain_from_url(initial_url)
                cls._active_journals[session_id] = FlightJournal(
                    session_id=session_id,
                    domain=domain,
                    initial_url=initial_url,
                    last_url=initial_url,
                )
            return cls._active_journals[session_id]

    @classmethod
    def clear(cls) -> None:
        with cls._lock:
            cls._active_journals.clear()

    @classmethod
    def record_action(
        cls,
        page: Any,
        action: str,
        dom_ref: Any,
        handle: Any | None,
        anchor: dict[str, Any] | None,
        value: Any,
        expect: dict[str, Any] | None,
        initial_url: str,
        after_url: str,
        elapsed_ms: int,
        memory_root: str | Path | None = None,
    ) -> dict[str, Any] | None:
        journal = cls.get_journal(page)
        if not journal.initial_url:
            journal.initial_url = initial_url
            journal.domain = _domain_from_url(initial_url)

        if anchor is None and handle is not None:
            try:
                anchor = AnchorCompiler.compile_handle(handle)
            except Exception:
                anchor = {"candidates": [], "context": {"opaque_ref": str(dom_ref)}}

        sanitized_value = LearningMemory._value_ref(action, value, anchor or {})
        event = {
            "seq": len(journal.events) + 1,
            "action": action,
            "status": "success",
            "target": _safe_json(dict(anchor or {}), name="anchor"),
            "value": sanitized_value,
            "expect": _safe_json(dict(expect or {}), name="expect"),
            "url": _safe_url(initial_url),
            "after_url": _safe_url(after_url),
            "duration_ms": int(elapsed_ms),
            "timestamp": time.time(),
        }
        journal.events.append(event)
        journal.last_url = after_url

        # Check for workflow progression / completion boundary
        boundary_detected = False
        # 1. URL changed
        if _safe_url(after_url) != _safe_url(initial_url) and len(journal.events) >= 1:
            boundary_detected = True
        # 2. Form submission / multi-step sequence completion
        elif action == "click" and any(e["action"] in {"fill", "select"} for e in journal.events[:-1]):
            anchor_str = json.dumps(anchor or {}).lower()
            if any(kw in anchor_str for kw in ("submit", "login", "sign in", "continue", "save", "search", "next", "confirm")):
                boundary_detected = True
            elif expect and (expect.get("url_changed") or expect.get("revision_changed") or expect.get("dom_mutation")):
                boundary_detected = True
        # 3. Action count threshold (prevent unbounded sequence growth)
        elif len(journal.events) >= 8:
            boundary_detected = True

        if boundary_detected:
            distilled = cls.distill_journal(journal, memory_root=memory_root)
            if distilled is not None:
                # Truncate committed events only after confirmed successful persistence
                journal.events = []
                journal.initial_url = after_url
                journal.domain = _domain_from_url(after_url)
            return distilled
        return None

    @classmethod
    def distill_journal(
        cls,
        journal: FlightJournal,
        memory_root: str | Path | None = None,
    ) -> dict[str, Any] | None:
        successful_events = [e for e in journal.events if e.get("status", "success") == "success"]
        if not successful_events:
            return None
        domain = journal.domain or _domain_from_url(journal.initial_url)
        recipe_id = f"auto-{domain.replace('.', '-')}-{uuid4().hex[:8]}"
        path_name = _normalize_path(journal.initial_url)
        name = f"Auto flow for {domain} ({path_name})"

        try:
            recipe = distill_recipe(
                recipe_id=recipe_id,
                name=name,
                steps=successful_events,
                domain_pattern=domain,
                metadata={
                    "automatic": True,
                    "source": "flight_recorder",
                    "status": "draft",
                    "created_at": time.time(),
                    "confidence": 0.88,
                },
            )
            mem_root = _memory_root(memory_root)
            candidates_dir = mem_root / domain / "candidates"
            candidates_dir.mkdir(parents=True, exist_ok=True)
            candidate_path = candidates_dir / f"{recipe_id}.json"
            temp_cand = candidates_dir / f".tmp_{uuid4().hex[:8]}_{recipe_id}.json"
            temp_cand.write_text(recipe.to_json(), encoding="utf-8")
            temp_cand.replace(candidate_path)

            ledger_db = mem_root / domain / "ledger.db"
            with sqlite3.connect(ledger_db, timeout=5) as db:
                db.execute("CREATE TABLE IF NOT EXISTS executions (candidate_id TEXT, outcome TEXT NOT NULL, recorded_at REAL NOT NULL, details TEXT NOT NULL)")
                db.execute("INSERT INTO executions VALUES (?, ?, ?, ?)", (recipe_id, "implicit_distilled", time.time(), json.dumps({"session_id": journal.session_id, "steps_count": len(successful_events)})))

            return {
                "candidate_id": recipe_id,
                "recipe": recipe.to_dict(),
                "path": str(candidate_path),
            }
        except Exception:
            return None


def suggest_for_url(
    url: str,
    recipe_store: RecipeStore | None = None,
    memory_root: str | Path | None = None,
    tree: Any = None,
) -> list[dict[str, Any]]:
    """Match current URL against domain recipes and return ranked candidates with confidence scores."""
    suggestions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # 1. Curated recipes from RecipeStore
    store = recipe_store or RecipeStore()
    matching_curated = store.match(url)
    u_domain = _domain_from_url(url)
    for r in matching_curated:
        if r.id in seen_ids:
            continue
        if hasattr(r, "lifecycle") and r.lifecycle.state in {
            RecipeLifecycleState.QUARANTINED,
            RecipeLifecycleState.ARCHIVED,
        }:
            continue
        risk_class = get_recipe_risk(r)
        match_res = match_page_state(r, url, tree) if tree is not None else {"matched": True, "score": 0.95}
        if tree is not None and not match_res.get("matched", False):
            continue

        if tree is not None:
            confidence = match_res.get("score", 0.95)
            guard_status = "passed"
        else:
            if isinstance(r.domain_pattern, str) and u_domain in r.domain_pattern:
                confidence = 0.95
            elif isinstance(r.domain_pattern, list) and any(u_domain in p for p in r.domain_pattern):
                confidence = 0.95
            elif r.domain_pattern == "*":
                confidence = 0.80
            else:
                confidence = 0.90
            guard_status = "unknown"

        # Extract required parameters
        params = sorted({m.group(1) for m in _TEMPLATE.finditer(json.dumps(r.to_dict()))})
        if params:
            param_dict = {p: f"<{p}>" for p in params}
            param_str = json.dumps(param_dict)
            argv = ["python3", "scripts/cdp_controller.py", "recipe", "run", str(r.id), "--params", param_str]
            cmd = f"python3 scripts/cdp_controller.py recipe run {shlex.quote(str(r.id))} --params {shlex.quote(param_str)}"
        else:
            argv = ["python3", "scripts/cdp_controller.py", "recipe", "run", str(r.id)]
            cmd = f"python3 scripts/cdp_controller.py recipe run {shlex.quote(str(r.id))}"

        suggestions.append({
            "recipe_id": r.id,
            "name": r.name,
            "confidence": confidence,
            "match_score": confidence,
            "risk_class": risk_class,
            "guard_status": guard_status,
            "r4_authorization_required": (risk_class == RiskClass.R4_IRREVERSIBLE),
            "command": cmd,
            "argv": argv,
            "source": "curated",
            "required_params": params,
            "lifecycle_state": getattr(getattr(r, "lifecycle", None), "state", RecipeLifecycleState.DRAFT),
        })
        seen_ids.add(r.id)

    # 2. Learned recipes from LearningMemory
    try:
        mem = LearningMemory(memory_root)
        candidates = mem.candidates(url)
        for c in candidates:
            cid = c.get("id")
            if not cid or cid in seen_ids:
                continue
            lc = c.get("lifecycle", {})
            c_state = lc.get("state", RecipeLifecycleState.DRAFT) if isinstance(lc, dict) else getattr(lc, "state", RecipeLifecycleState.DRAFT)
            if c_state in {RecipeLifecycleState.QUARANTINED, RecipeLifecycleState.ARCHIVED}:
                continue
            meta = c.get("metadata", {})
            risk_class = get_recipe_risk(c)
            match_res = match_page_state(c, url, tree) if tree is not None else {"matched": True, "score": 0.88}
            if tree is not None and not match_res.get("matched", False):
                continue

            if tree is not None:
                confidence = match_res.get("score", 0.88)
                guard_status = "passed"
            else:
                confidence = float(meta.get("confidence", 0.88 if meta.get("source") == "flight_recorder" else 0.85))
                if meta.get("status") == "promoted":
                    confidence = 0.95
                guard_status = "unknown"

            params = sorted({
                str(step.get("value", {}).get("$ref"))
                for step in c.get("steps", [])
                if isinstance(step.get("value"), Mapping) and step["value"].get("$ref")
            })
            if params:
                param_dict = {p: f"<{p}>" for p in params}
                param_str = json.dumps(param_dict)
                argv = ["python3", "scripts/cdp_controller.py", "recipe", "run", str(cid), "--params", param_str]
                cmd = f"python3 scripts/cdp_controller.py recipe run {shlex.quote(str(cid))} --params {shlex.quote(param_str)}"
            else:
                argv = ["python3", "scripts/cdp_controller.py", "recipe", "run", str(cid)]
                cmd = f"python3 scripts/cdp_controller.py recipe run {shlex.quote(str(cid))}"

            suggestions.append({
                "recipe_id": cid,
                "name": c.get("name", cid),
                "confidence": round(confidence, 2),
                "match_score": round(confidence, 2),
                "risk_class": risk_class,
                "guard_status": guard_status,
                "r4_authorization_required": (risk_class == RiskClass.R4_IRREVERSIBLE),
                "command": cmd,
                "argv": argv,
                "source": "learned",
                "required_params": params,
                "lifecycle_state": c_state,
            })
            seen_ids.add(cid)
    except Exception:
        pass

    return sorted(suggestions, key=lambda item: item["confidence"], reverse=True)
