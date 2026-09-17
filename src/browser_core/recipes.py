"""Procedural memory and adaptive recipe execution.

Recipes are intentionally small JSON documents.  They provide a fast path for
known workflows while returning an explicit Level 1 observe/act fallback when
the page no longer matches the recorded procedure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import fnmatch
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

from playwright.sync_api import Page

from .contracts import DOMNodeRef
from .engine import act
from .page_manager import PageManager, manager_for_page


_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SECRET_KEY = re.compile(r"(?i)(password|passwd|passcode|token|secret|api[_-]?key|authorization)")


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
        return {key: _substitute(item, params) for key, item in value.items()}
    return value


@dataclass(slots=True)
class RecipeStep:
    action: str
    target: str | None = None
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
        return cls(action=action, target=str(target) if target is not None else None,
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
        for index, step in enumerate(self.steps):
            if step.action not in valid_actions:
                raise ValueError(f"Unsupported recipe action at step {index}: {step.action!r}")
            if step.action != "eval" and not step.target:
                raise ValueError(f"Recipe step {index} requires target")
            if step.action in {"fill", "select", "eval"} and step.value is None:
                raise ValueError(f"Recipe step {index} requires value")
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
    if _SECRET_KEY.search(sensitive_name):
        return "{{password}}" if re.search(r"pass|secret", sensitive_name, re.I) else "{{token}}"
    result = _EMAIL.sub("{{email}}", value)
    result = _BEARER.sub(r"\1{{token}}", result)
    result = _JWT.sub("{{token}}", result)
    return result


def distill_recipe(recipe_id: str, name: str, steps: Sequence[Mapping[str, Any] | RecipeStep], *,
                   domain_pattern: str | list[str] = "*", description: str = "",
                   validation: Mapping[str, Any] | None = None, metadata: Mapping[str, Any] | None = None) -> Recipe:
    """Turn a successful interaction trace into a recipe without retaining secrets."""
    sanitized_steps: list[RecipeStep] = []
    for raw in steps:
        step = raw if isinstance(raw, RecipeStep) else RecipeStep.from_dict(raw)
        target = sanitize_value(step.target, sensitive_name="target")
        value = sanitize_value(step.value, sensitive_name=target or "")
        sanitized_steps.append(RecipeStep(step.action, target, value,
                                          _substitute(sanitize_value(step.expect), {}), step.timeout_ms))
    return Recipe(recipe_id, name, description, domain_pattern, sanitized_steps,
                  dict(validation or {}), _defaults(metadata))


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
        locator = page.locator(str(target)).first
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
        RecipeEngine._check_expectation(page, locator, expect)

    @staticmethod
    def _check_expectation(page: Page, locator: Any, expect: Mapping[str, Any]) -> None:
        if "url" in expect and page.url != str(expect["url"]):
            raise ValueError(f"expected URL {expect['url']!r}, got {page.url!r}")
        url_pattern = expect.get("url_matches", expect.get("url_regex"))
        if url_pattern is not None and re.search(str(url_pattern), page.url) is None:
            raise ValueError(f"URL does not match {url_pattern!r}")
        if "text_present" in expect:
            body_text = page.locator("body").inner_text(timeout=500)
            if str(expect["text_present"]) not in body_text:
                raise ValueError(f"expected text not present: {expect['text_present']!r}")
        if "text" in expect and str(expect["text"]) not in page.locator("body").inner_text(timeout=500):
            raise ValueError(f"expected text not present: {expect['text']!r}")
        if "visible" in expect and bool(locator.is_visible()) != bool(expect["visible"]):
            raise ValueError("visibility postcondition failed")

    @staticmethod
    def _validate(validation: Mapping[str, Any], page: Page, manager: PageManager | None) -> None:
        del manager
        if not validation:
            return
        RecipeEngine._check_expectation(page, page.locator("body"), validation)


# Friendly aliases used by callers that describe this operation as mining.
distill = distill_recipe
