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
import re
import sqlite3
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
_SECRET_KEY = re.compile(r"(?i)(password|passwd|passcode|token|secret|api[_-]?key|authorization)")
_OTP_KEY = re.compile(r"(?i)(otp|one.?time|verification.?code|cvv|ssn|cookie|session)")
_FRAGILE_SELECTOR = re.compile(r"(?i)(:nth-(?:child|of-type)\(|^/|//)")


class AnchorAmbiguous(ValueError):
    """A mutating recipe step resolved to more than one live element."""


def _memory_root(root: str | Path | None = None) -> Path:
    """Keep learned data outside the reviewed, source-controlled recipe tree."""
    return Path(root or os.environ.get("OMNIBROWSER_MEMORY_ROOT") or Path.home() / ".omnibrowser" / "memory" / "v1")


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    # Query values frequently carry OAuth codes, session keys, and PII.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


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
        return {"id": self.id, "name": self.name, "description": self.description,
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
        valid_actions = {"click", "fill", "select", "wait_for", "eval"}
        automatic = bool(self.metadata.get("automatic") or self.metadata.get("source") == "learned")
        for index, step in enumerate(self.steps):
            if step.action not in valid_actions:
                raise ValueError(f"Unsupported recipe action at step {index}: {step.action!r}")
            if step.action != "eval" and not step.target:
                raise ValueError(f"Recipe step {index} requires target")
            if step.action in {"fill", "select", "eval"} and step.value is None:
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

    def save(self, recipe: Recipe) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{recipe.id}.json"
        path.write_text(recipe.to_json(), encoding="utf-8")
        self._recipes[recipe.id] = recipe
        return path

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
        return {"candidates": candidates[:6], "context": {"form": _safe_json(str(raw.get("form") or ""), name="form")}}

    @classmethod
    def resolve(cls, page: Page, target: Any, *, mutating: bool) -> Any:
        candidates = target.get("candidates", []) if isinstance(target, Mapping) else [{"kind": "scoped_css", "selector": str(target)}]
        ambiguous = False
        for candidate in candidates:
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

    def __init__(self, root: str | Path | None = None):
        self.root = _memory_root(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.candidates_dir = self.root / "candidates"
        self.candidates_dir.mkdir(exist_ok=True)
        self.traces_db = self.root / "traces.db"
        self.ledger_db = self.root / "ledger.db"
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
        if _SECRET_KEY.search(text) or _OTP_KEY.search(text):
            name, sensitivity = ("password", "secret") if "pass" in text else ("verification_code", "secret")
        elif "email" in text:
            name, sensitivity = "email", "pii"
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
        recipe = distill_recipe(recipe_id, str(row[0]), events, domain_pattern=urlsplit(url).netloc or "*",
                                validation=_safe_json(dict(validation or {}), name="validation"), metadata={"automatic": True, "status": "draft", "run_id": run_id})
        path = self.candidates_dir / f"{recipe_id}.json"
        path.write_text(recipe.to_json(), encoding="utf-8")
        with self._connection(self.ledger_db) as db:
            db.execute("INSERT INTO executions VALUES (?, ?, ?, ?)", (recipe_id, "distilled", time.time(), json.dumps({"run_id": run_id})))
        return {"run_id": run_id, "status": "draft", "candidate_id": recipe_id}

    def candidates(self, url: str | None = None) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.candidates_dir.glob("*.json")):
            recipe = Recipe.from_json(path)
            if url is None or recipe.matches_url(_safe_url(url)):
                result.append(recipe.to_dict())
        return result

    def promote(self, candidate_id: str, *, approve: bool = False) -> dict[str, Any]:
        if not approve:
            raise ValueError("promotion requires explicit --approve")
        path = self.candidates_dir / f"{candidate_id}.json"
        if not path.exists():
            raise ValueError(f"unknown candidate: {candidate_id}")
        recipe = Recipe.from_json(path)
        recipe.metadata["status"] = "promoted"
        recipe.metadata["promoted_at"] = int(time.time())
        path.write_text(recipe.to_json(), encoding="utf-8")
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
            suggestions.append({"candidate_id": recipe["id"], "status": metadata.get("status", "draft"), "confidence": round(score, 2), "required_params": params})
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
        locator = AnchorCompiler.resolve(page, target, mutating=action in {"click", "fill", "select"})
        if action == "click":
            locator.click(timeout=timeout)
        elif action == "fill":
            locator.fill(str(value), timeout=timeout)
        elif action == "select":
            locator.select_option(str(value), timeout=timeout)
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
