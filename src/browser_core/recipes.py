"""Procedural memory and adaptive recipe execution.

Recipes are intentionally small JSON documents.  They provide a fast path for
known workflows while returning an explicit Level 1 observe/act fallback when
the page no longer matches the recorded procedure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import json
import os
from pathlib import Path
import hashlib
import re
import shlex
import sqlite3
import threading
import time
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse, urlsplit, urlunsplit
from uuid import uuid4

from playwright.sync_api import Page

from .contracts import DOMNodeRef
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


class AnchorAmbiguous(ValueError):
    """A mutating recipe step resolved to more than one live element."""


class InvalidExecutableArtifact(ValueError):
    """The provided file or artifact is not an executable recipe or candidate."""


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
    target: Any = None
    value: Any = None
    expect: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 5000
    selector: str | None = None

    def __post_init__(self) -> None:
        if self.target is None and self.selector is not None:
            self.target = self.selector

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecipeStep":
        action = str(data.get("action", data.get("op", ""))).strip().lower()
        if not action:
            raise ValueError("Recipe step requires action")
        target = data.get("target", data.get("selector", data.get("ref")))
        expect = data.get("expect", {})
        if expect is None:
            expect = {}
        if not isinstance(expect, dict):
            raise ValueError("Recipe step expect must be an object")
        return cls(action=action, target=target,
                   value=data.get("value", data.get("text", data.get("script"))),
                   expect=dict(expect), timeout_ms=int(data.get("timeout_ms", 5000)))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"action": self.action}
        if self.target is not None:
            result["target"] = self.target
        if self.value is not None:
            result["value"] = self.value
        if self.expect:
            result["expect"] = self.expect
        if self.timeout_ms != 5000:
            result["timeout_ms"] = self.timeout_ms
        return result

    @property
    def op(self) -> str:
        return self.action


@dataclass(slots=True)
class Recipe:
    id: str
    name: str
    description: str = ""
    domain_pattern: str | list[str] = "*"
    steps: list[RecipeStep] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.metadata = _defaults(self.metadata)
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
        return cls(id=str(data["id"]), name=str(data["name"]),
                   description=str(data.get("description", "")),
                   domain_pattern=data["domain_pattern"], steps=list(data["steps"]),
                   validation=dict(data.get("validation", {})), metadata=dict(data.get("metadata", {})))

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
        return {"kind": kind, "schema_version": 1, "id": self.id, "name": self.name, "description": self.description,
                "domain_pattern": self.domain_pattern,
                "steps": [step.to_dict() for step in self.steps],
                "validation": self.validation, "metadata": self.metadata}

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent) + "\n"

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


class RecipeStore:
    """Loads recipes from a directory and records only non-sensitive health data."""

    def __init__(self, root: str | Path = "recipes"):
        self.root = Path(root)
        self._recipes: dict[str, Recipe] = {}

    def load(self) -> list[Recipe]:
        self._recipes = {}
        if not self.root.exists():
            return []
        for path in sorted(self.root.rglob("*.json")):
            if "sitemaps" in path.parts:
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
        return self._recipes.get(recipe_id)

    get_recipe = get

    def match(self, url: str) -> list[Recipe]:
        return self.list(url)

    find_matching = match

    def save(self, recipe: Recipe, domain: str | None = None) -> Path:
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
        path.write_text(recipe.to_json(), encoding="utf-8")
        self._recipes[recipe.id] = recipe
        return path

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

    def record_success(self, recipe: Recipe) -> None:
        recipe.metadata["success_count"] = int(recipe.metadata.get("success_count", 0)) + 1
        recipe.metadata["last_failure_reason"] = None

    def record_failure(self, recipe: Recipe, *, step_index: int, reason: str, target: str | None) -> None:
        ledger = recipe.metadata.setdefault("failure_ledger", [])
        entry = {"step_index": step_index, "reason": _safe_reason(reason), "target": _safe_target(target)}
        ledger.append(entry)
        recipe.metadata["failure_ledger"] = ledger[-20:]
        recipe.metadata["failure_count"] = int(recipe.metadata.get("failure_count", 0)) + 1
        recipe.metadata["last_failure_reason"] = entry["reason"]


@dataclass(slots=True)
class RecipeExecutionResult:
    ok: bool
    recipe_id: str
    completed_steps: int
    fallback_required: bool = False
    message: str = ""
    failure: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "recipe_id": self.recipe_id, "completed_steps": self.completed_steps,
                "fallback_required": self.fallback_required, "message": self.message, "failure": self.failure}

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

    def execute(self, recipe: Recipe | str, page: Page, params: Mapping[str, Any] | None = None,
                *, manager: PageManager | None = None) -> RecipeExecutionResult:
        if isinstance(recipe, str):
            if self.store is None:
                raise ValueError("RecipeStore is required when recipe is an id")
            found = self.store.get(recipe)
            if found is None:
                raise KeyError(f"Unknown recipe: {recipe}")
            recipe = found
        params = params or {}
        try:
            if not recipe.matches_url(page.url):
                raise ValueError(f"recipe domain does not match active URL: {page.url}")
            for key in _TEMPLATE.findall(json.dumps(recipe.to_dict())):
                if key not in params:
                    raise ValueError(f"missing recipe parameter: {key}")
            page_manager = manager or (self.store and getattr(self.store, "manager", None)) or manager_for_page(page)
            for index, step in enumerate(recipe.steps):
                target = _substitute(step.target, params)
                value = _substitute(step.value, params)
                expect = _substitute(step.expect, params)
                timeout = min(max(int(step.timeout_ms), 1), 30000)
                self._execute_step(page, page_manager, step.action, target, value, expect, timeout)
            resolved_validation = _substitute(recipe.validation, params)
            self._validate(resolved_validation, page, page_manager)
        except Exception as error:
            index = locals().get("index", 0)
            target = locals().get("target", None)
            reason = _safe_reason(str(error))
            failure = {"step_index": index, "reason": reason, "target": _safe_target(target)}
            if self.store:
                self.store.record_failure(recipe, step_index=index, reason=reason, target=target)
            return RecipeExecutionResult(False, recipe.id, index, True,
                                         "Recipe fast path failed; fallback to Level 1 observe() / act() and re-scan the page.", failure)
        recipe.metadata["last_failure_reason"] = None
        if self.store:
            self.store.record_success(recipe)
        else:
            recipe.metadata["success_count"] = int(recipe.metadata.get("success_count", 0)) + 1
        return RecipeExecutionResult(True, recipe.id, len(recipe.steps), False, "Recipe completed successfully.")

    run = execute

    @staticmethod
    def _execute_step(page: Page, manager: PageManager | None, action: str, target: Any, value: Any,
                      expect: dict[str, Any], timeout: int) -> None:
        if action == "eval":
            page.evaluate(str(value))
            return
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
                act(page, native, ref, None if value is None else str(value), {**expect, "timeout_ms": timeout}, manager=manager)
                return
        initial_url = page.url
        locator = AnchorCompiler.resolve(page, target, mutating=action in {"click", "fill", "select", "upload"})
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
        # Calculate confidence
        if isinstance(r.domain_pattern, str) and u_domain in r.domain_pattern:
            confidence = 0.95
        elif isinstance(r.domain_pattern, list) and any(u_domain in p for p in r.domain_pattern):
            confidence = 0.95
        elif r.domain_pattern == "*":
            confidence = 0.80
        else:
            confidence = 0.90

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
            "command": cmd,
            "argv": argv,
            "source": "curated",
            "required_params": params,
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
            meta = c.get("metadata", {})
            confidence = float(meta.get("confidence", 0.88 if meta.get("source") == "flight_recorder" else 0.85))
            if meta.get("status") == "promoted":
                confidence = 0.95

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
                "command": cmd,
                "argv": argv,
                "source": "learned",
                "required_params": params,
            })
            seen_ids.add(cid)
    except Exception:
        pass

    return sorted(suggestions, key=lambda item: item["confidence"], reverse=True)
