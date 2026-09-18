"""Native Playwright actions with small, explicit postcondition watchers."""

from __future__ import annotations

from dataclasses import asdict
import re
import time
from typing import Any

from playwright.sync_api import Page

from .contracts import (
    ActRequest,
    ActResult,
    ActionTimeoutError,
    DOMNodeRef,
    StaleRefError,
    StateDelta,
)
from .page_manager import PageManager, manager_for_page


_ACTIONS = {"check", "click", "fill", "select", "press"}


def _get_manager(page: Page, manager: PageManager | None) -> PageManager:
    resolved = manager or manager_for_page(page)
    if resolved is None:
        raise RuntimeError("A PageManager must install the scanner before act()")
    return resolved


def _ref(value: DOMNodeRef | str) -> DOMNodeRef:
    return value if isinstance(value, DOMNodeRef) else DOMNodeRef.parse(value)


def _snapshot(manager: PageManager, page: Page, frame: Any) -> Any:
    return manager.observe(page, frame=frame, max_elements=200)


def _delta(before: Any, after: Any) -> StateDelta:
    old = {str(node.ref): node for node in before.tree}
    new = {str(node.ref): node for node in after.tree}
    changed: list[dict[str, Any]] = []
    for key in old.keys() & new.keys():
        left, right = old[key], new[key]
        if (left.role, left.name, left.bounds, left.visible, left.disabled, left.value) != (
            right.role,
            right.name,
            right.bounds,
            right.visible,
            right.disabled,
            right.value,
        ):
            changed.append({"ref": key, "before": asdict(left), "after": asdict(right)})
            changed[-1]["before"]["ref"] = key
            changed[-1]["after"]["ref"] = key
    added = [{**asdict(node), "ref": str(node.ref)} for key, node in new.items() if key not in old]
    removed = [{**asdict(node), "ref": str(node.ref)} for key, node in old.items() if key not in new]
    return StateDelta(changed=changed, added=added, removed=removed)


def _body_text(frame: Any) -> str:
    try:
        return str(frame.locator("body").inner_text(timeout=250))
    except Exception:
        return ""


def _expectation_met(expect: dict[str, Any], *, before: Any, current: Any, page: Page, manager: PageManager, frame: Any, initial_url: str) -> bool:
    if expect.get("revision_changed") or expect.get("dom_mutation") or expect.get("mutation"):
        if current.revision <= before.revision:
            return False
    if "revision" in expect and current.revision < int(expect["revision"]):
        return False
    if expect.get("url_changed") and page.url == initial_url:
        return False
    if "url" in expect and page.url != str(expect["url"]):
        return False
    if "url_matches" in expect:
        try:
            if re.search(str(expect["url_matches"]), page.url) is None:
                return False
        except re.error as error:
            raise ValueError(f"Invalid url_matches expectation: {error}") from error
    if "text_present" in expect and str(expect["text_present"]) not in _body_text(frame):
        return False
    element = expect.get("element")
    if element:
        target_ref = _ref(element.get("ref")) if isinstance(element, dict) and element.get("ref") else None
        if target_ref is None:
            return False
        handle = manager.resolve_element(page, target_ref)
        visible = bool(handle and handle.is_visible())
        if isinstance(element, dict) and element.get("enabled") is True:
            if not handle or bool(handle.is_disabled()):
                return False
        if isinstance(element, dict) and element.get("enabled") is False:
            if handle and not bool(handle.is_disabled()):
                return False
        if isinstance(element, dict) and element.get("visible") is True and not visible:
            return False
        if isinstance(element, dict) and element.get("visible") is False and visible:
            return False
        if isinstance(element, dict) and element.get("text_present"):
            if not handle or str(element["text_present"]) not in str(handle.inner_text()):
                return False
        if isinstance(element, dict) and "checked" in element:
            if not handle or bool(handle.is_checked()) != bool(element["checked"]):
                return False
    return True


def act(
    page: Page,
    action: str,
    ref: DOMNodeRef | str,
    value: str | None = None,
    expect: dict[str, Any] | None = None,
    *,
    manager: PageManager | None = None,
    learn_run: str | None = None,
    memory_root: str | None = None,
) -> ActResult:
    """Execute one native action and optionally wait for its postcondition."""
    started = time.monotonic()
    if action not in _ACTIONS:
        raise ValueError(f"Unsupported action {action!r}; expected one of {sorted(_ACTIONS)}")
    dom_ref = _ref(ref)
    page_manager = _get_manager(page, manager)
    try:
        frame = page_manager._frame_for_token(page, dom_ref.frame)
        state = page_manager._frame_state(frame)
    except Exception as error:
        raise StaleRefError(f"Stale ref {dom_ref}: frame is gone; call observe() and retry") from error
    if dom_ref.epoch != str(state.epoch):
        raise StaleRefError(
            f"Stale ref {dom_ref}: document epoch is now d{state.epoch}; call observe() to refresh refs"
        )
    before = _snapshot(page_manager, page, frame)
    handle = page_manager.resolve_element(page, dom_ref)
    if handle is None:
        raise StaleRefError(f"Stale ref {dom_ref}: node is detached; call observe() to refresh refs")

    timeout_ms = min(max(int((expect or {}).get("timeout_ms", 5000)), 1), 10000)
    # A durable anchor must be derived while the opaque ref is still valid.
    anchor: dict[str, Any] | None = None
    if learn_run:
        try:
            from .recipes import AnchorCompiler
            anchor = AnchorCompiler.compile_handle(handle)
        except Exception:
            # Journal failure must never change the result of a user action.
            anchor = {"candidates": [], "context": {"opaque_ref": str(dom_ref)}}
    # This snapshot is intentionally immediately before dispatch.  Capturing it
    # from inside the polling loop misses fast navigations.
    initial_url = page.url
    try:
        if action == "click":
            handle.click(timeout=timeout_ms)
        elif action == "check":
            handle.check(timeout=timeout_ms)
        elif action == "fill":
            if value is None:
                raise ValueError("fill requires value")
            handle.fill(value, timeout=timeout_ms)
        elif action == "select":
            if value is None:
                raise ValueError("select requires value")
            handle.select_option(value, timeout=timeout_ms)
        elif action == "press":
            if value is None:
                raise ValueError("press requires value")
            handle.press(value, timeout=timeout_ms)
    except Exception as error:
        if "detached" in str(error).lower() or "not found" in str(error).lower():
            raise StaleRefError(f"Stale ref {dom_ref}: node detached; call observe() and retry") from error
        raise ActionTimeoutError(f"Action {action} on {dom_ref} failed: {error}") from error

    expect = expect or {}
    if expect:
        from .recipes import PostconditionWatcher

        def expectation() -> bool:
            current = _snapshot(page_manager, page, frame)
            return _expectation_met(expect, before=before, current=current, page=page, manager=page_manager,
                                    frame=frame, initial_url=initial_url)

        try:
            PostconditionWatcher.poll(expectation, timeout_ms, lambda: page.wait_for_timeout(50))
        except ValueError as error:
            raise ActionTimeoutError(f"Expectation timed out after {timeout_ms}ms for action {action}") from error
    after = _snapshot(page_manager, page, frame)
    elapsed = round((time.monotonic() - started) * 1000)
    result = ActResult(
        revision=after.revision,
        delta=_delta(before, after),
        ok=True,
        document_epoch=after.document_epoch,
        action_duration_ms=elapsed,
    )
    if learn_run and anchor is not None:
        try:
            from .recipes import LearningMemory
            LearningMemory(memory_root).append_action(
                learn_run, action=action, anchor=anchor, value=value,
                expect=expect, url=initial_url, duration_ms=elapsed,
            )
        except Exception:
            # Learning is advisory.  A transient local DB issue must not make
            # the browser action fail after it has already succeeded.
            pass
    return result


class ActionEngine:
    """Convenience object for callers that already own a PageManager."""

    def __init__(self, manager: PageManager):
        self.manager = manager

    def act(self, page: Page, action: str, ref: DOMNodeRef | str, value: str | None = None, expect: dict[str, Any] | None = None, *, learn_run: str | None = None, memory_root: str | None = None) -> ActResult:
        return act(page, action, ref, value, expect, manager=self.manager, learn_run=learn_run, memory_root=memory_root)
