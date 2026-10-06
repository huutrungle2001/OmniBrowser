"""Model Context Protocol (MCP) server implementation for OmniBrowser.

Exposes OmniBrowser's progressive capabilities (DOM observation, semantic action dispatch,
execution recipes, tab isolation, and targeted visual inspection) over standard JSON-RPC 2.0 stdio transport.
"""

from __future__ import annotations

import concurrent.futures
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
FastMCP = MCPServer

from .contracts import (
    ActionTimeoutError,
    DOMNodeRef,
    ExitCode,
    LeaseExpiredError,
    LeaseNotFoundError,
    OmniBrowserError,
    ObserveResult,
    ObservedNode,
    StaleRefError,
    StateDelta,
    TargetNotFoundError,
)
from .engine import act
from .page_manager import PageManager, _reset_color_scheme
from .primitives import inspect as inspect_primitive, runBrowserCode
from .recipes import RecipeEngine, RecipeStore

DEFAULT_CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:17082")
MAX_OBSERVE_BUDGET_BYTES = 13500  # 13.5 KB content budget ensuring total wire JSON <= 15 KB (15,360 bytes)


class BrowserWorker:
    """Pins Playwright session to a single dedicated worker thread to guarantee greenlet thread-safety."""

    def __init__(
        self,
        cdp_url: str = DEFAULT_CDP_URL,
        *,
        test_mode: bool = False,
        lease_id: str | None = None,
        fencing_token: int | None = None,
    ):
        self.cdp_url = cdp_url
        self.test_mode = test_mode
        self.lease_id = lease_id
        self.fencing_token = fencing_token
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._manager: PageManager | None = None
        self._active_tab_id: str | None = None

    def run(self, fn, *args, **kwargs) -> Any:
        future = self._executor.submit(fn, *args, **kwargs)
        return future.result()

    def get_manager(self) -> PageManager:
        if self.test_mode:
            parsed = urlparse(self.cdp_url)
            if parsed.port == 17082:
                raise ValueError("SAFETY INVARIANT VIOLATION: Test mode refuses connection to port 17082")

        cdp_url = self.cdp_url
        context_id = None
        if self.lease_id:
            from browser_core.broker.session_router import SessionRouter
            router = SessionRouter()
            lease = router.lease_manager.validate_lease(self.lease_id, self.fencing_token)
            cdp_url = lease.cdp_url
            context_id = lease.browser_context_id

        if self._manager is None or not self._manager.browser or not self._manager.browser.is_connected():
            self._manager = PageManager(
                cdp_url,
                test_mode=self.test_mode,
                browser_context_id=context_id,
            ).connect()
        return self._manager

    def validate_lease_health(self) -> None:
        """Enforces Invariant 8 (Lease Ownership Invariant) on every operation."""
        if self.lease_id:
            from browser_core.broker.session_router import SessionRouter
            router = SessionRouter()
            try:
                router.lease_manager.validate_lease(self.lease_id, self.fencing_token)
            except (LeaseExpiredError, LeaseNotFoundError) as err:
                raise ToolError(f"Lease {self.lease_id} is expired or invalid: {err}") from err

    def shutdown(self) -> None:
        def _close():
            if self._manager is not None:
                try:
                    self._manager.close()
                except Exception:
                    pass
                self._manager = None

        try:
            self._executor.submit(_close).result(timeout=5.0)
        except Exception:
            pass
        self._executor.shutdown(wait=False)


def _get_page(manager: PageManager, tab_id: str | None = None):
    if not manager.context or not manager.browser or not manager.browser.is_connected():
        manager.connect()
    pages = manager.context.pages
    if not pages:
        page = manager.context.new_page()
        _reset_color_scheme(page)
        return page

    if tab_id is None:
        page = pages[0]
        page.bring_to_front()
        _reset_color_scheme(page)
        return page

    try:
        idx = int(tab_id)
        if 0 <= idx < len(pages):
            page = pages[idx]
            page.bring_to_front()
            _reset_color_scheme(page)
            return page
    except ValueError:
        pass

    tab_lower = tab_id.lower()
    for page in pages:
        if tab_id in page.url or tab_lower in (page.title() or "").lower():
            page.bring_to_front()
            _reset_color_scheme(page)
            return page

    page = pages[0]
    page.bring_to_front()
    _reset_color_scheme(page)
    return page


def _op_navigate(worker: BrowserWorker, url: str, tab_id: str | None = None) -> dict[str, Any]:
    worker.validate_lease_health()
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as error:
        raise ToolError(f"Navigation to {url} failed: {error}") from error
    manager.install_scanner(page)
    tab_index = manager.context.pages.index(page) if manager.context and page in manager.context.pages else 0
    return {
        "ok": True,
        "url": page.url,
        "title": page.title(),
        "tab_id": str(tab_index),
    }


def _op_observe(worker: BrowserWorker, tab_id: str | None = None, match: str | None = None) -> dict[str, Any]:
    worker.validate_lease_health()
    manager = worker.get_manager()
    target_tab = tab_id or match
    page = _get_page(manager, target_tab)
    if page not in manager._sessions:
        manager.install_scanner(page)
    try:
        obs = manager.observe(page)
    except Exception as error:
        raise ToolError(f"DOM observation failed: {error}") from error

    tree_nodes = [
        {
            "ref": str(n.ref),
            "role": n.role,
            "name": n.name,
            "bounds": list(n.bounds),
            "visible": n.visible,
            "interactive": n.interactive,
            "disabled": n.disabled,
            "value": n.value,
            "occluded": getattr(n, "occluded", False),
        }
        for n in obs.tree
    ]

    payload: dict[str, Any] = {
        "ok": True,
        "version": getattr(obs, "version", "1"),
        "page_url": obs.page_url,
        "frame_id": obs.frame_id,
        "document_epoch": obs.document_epoch,
        "revision": obs.revision,
        "tree": tree_nodes,
        "coverage": {
            "is_complete": obs.coverage.is_complete,
            "included": obs.coverage.included,
            "omitted": obs.coverage.omitted,
        },
        "suggested_recipes": getattr(obs, "suggested_recipes", []),
    }

    def _wire_size(p: dict) -> int:
        """Models standard MCP 2026-07-28 JSON-RPC 2.0 stdio response envelope.

        Guarantees Invariant 3 wire-level budget (<= 15,360 bytes / 15 KB)
        accounting for a standard 36-character UUID client request ID,
        SDK runner _meta serverInfo header, tool result envelope formatting,
        and JSON text payload.
        """
        raw_text = json.dumps(p, ensure_ascii=False)
        wrapper = {
            "jsonrpc": "2.0",
            "id": "00000000-0000-0000-0000-000000000000",
            "result": {
                "resultType": "complete",
                "_meta": {
                    "io.modelcontextprotocol/serverInfo": {
                        "name": "omnibrowser",
                        "version": "1.0.0",
                    }
                },
                "content": [{"type": "text", "text": raw_text}],
                "isError": False,
            },
        }
        return len(json.dumps(wrapper, ensure_ascii=False).encode("utf-8"))

    def _is_over_budget(p: dict) -> bool:
        """Applies Invariant 3 content budget and a 256-byte transport headroom reserve.

        Formal Invariant 3 Wire Scoping:
        Guarantees that the server-controlled CallToolResult payload remains <= 13.5 KB
        (13,824 bytes), ensuring the total serialized JSON-RPC 2.0 stdio response packet
        never exceeds 15,360 bytes (15 KB) for standard requests with client request IDs
        up to 36-character UUIDs. For unbounded or non-standard client request IDs
        (> 36 chars), the server-controlled CallToolResult payload remains strictly
        bounded to <= 14,848 bytes, preventing LLM context explosion.
        """
        encoded = json.dumps(p, ensure_ascii=False).encode("utf-8")
        return len(encoded) > MAX_OBSERVE_BUDGET_BYTES or _wire_size(p) > (15360 - 256)

    # 1. Truncate tree nodes if over budget
    while _is_over_budget(payload) and len(tree_nodes) > 0:
        cut = max(0, len(tree_nodes) * 4 // 5)
        if cut >= len(tree_nodes):
            cut = len(tree_nodes) - 1
        tree_nodes = tree_nodes[:cut]
        payload["tree"] = tree_nodes
        payload["coverage"]["is_complete"] = False
        payload["coverage"]["truncated"] = True

    # 2. If metadata alone exceeds budget (e.g. huge data: URL or long strings), truncate metadata
    if _is_over_budget(payload):
        payload["suggested_recipes"] = []

    while _is_over_budget(payload) and len(payload["page_url"]) > 60:
        excess = max(
            len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) - MAX_OBSERVE_BUDGET_BYTES,
            _wire_size(payload) - (15360 - 256),
        )
        cut_len = max(50, len(payload["page_url"]) - excess - 20)
        payload["page_url"] = payload["page_url"][:cut_len] + "...[truncated]"

    # 3. Absolute Unconditional Hard Postcondition fallback
    if _is_over_budget(payload):
        payload = {
            "ok": True,
            "version": "1",
            "page_url": payload["page_url"][:80] + "...[truncated]",
            "frame_id": str(payload.get("frame_id", ""))[:32],
            "document_epoch": str(payload.get("document_epoch", ""))[:32],
            "revision": payload.get("revision", 0),
            "tree": [],
            "coverage": {"is_complete": False, "truncated": True},
            "suggested_recipes": [],
        }

    # Hard invariant assertions: strictly guaranteed <= MAX_OBSERVE_BUDGET_BYTES and <= 15360 wire
    raw_encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    wire_bytes = _wire_size(payload)
    assert len(raw_encoded) <= MAX_OBSERVE_BUDGET_BYTES, f"Payload limit exceeded: {len(raw_encoded)} > {MAX_OBSERVE_BUDGET_BYTES}"
    assert wire_bytes <= 15360, f"Outer wire budget exceeded: {wire_bytes} > 15360"
    return payload


def _op_act(
    worker: BrowserWorker,
    action: str,
    ref: str | None = None,
    value: str | None = None,
    tab_id: str | None = None,
) -> dict[str, Any]:
    worker.validate_lease_health()
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    if page not in manager._sessions:
        manager.install_scanner(page)
    action_lower = action.lower().strip()

    if ref:
        try:
            dom_ref = DOMNodeRef.parse(ref)
        except Exception as error:
            raise ToolError(f"Invalid DOM ref '{ref}': {error}") from error

        handle = manager.resolve_element(page, dom_ref)
        if handle is None:
            raise ToolError(f"Stale ref {ref}; call browser_observe to refresh refs")

        frame = None
        try:
            frame = manager._frame_for_token(page, dom_ref.frame)
        except Exception:
            pass
        target_eval = frame if frame is not None else page

        # Occlusion detection: Avoid clicking covered elements
        if action_lower == "click":
            try:
                is_occ = target_eval.evaluate(
                    "el => window[Symbol.for('__OMNI_DOM_AGENT__')]?.isOccluded?.(el)",
                    handle,
                )
                if is_occ:
                    raise ToolError(
                        f"Element {ref} is occluded by another element/modal; cannot click covered target."
                    )
            except ToolError:
                raise
            except Exception:
                pass

        if action_lower in {"hover", "scroll"}:
            start_time = time.monotonic()
            doc_epoch = "d1"
            rev = 0
            if frame is not None:
                try:
                    state = manager._frame_state(frame)
                    doc_epoch = f"d{state.epoch}"
                except Exception:
                    pass
            try:
                rev = int(target_eval.evaluate("() => window[Symbol.for('__OMNI_DOM_AGENT__')]?.revision ?? 0"))
            except Exception:
                pass

            if action_lower == "hover":
                try:
                    handle.hover()
                    elapsed_ms = round((time.monotonic() - start_time) * 1000)
                    return {
                        "ok": True,
                        "action": "hover",
                        "ref": ref,
                        "delta": asdict(StateDelta()),
                        "state_delta": {"revision": rev, "document_epoch": doc_epoch},
                        "document_epoch": doc_epoch,
                        "revision": rev,
                        "elapsed_ms": elapsed_ms,
                        "action_duration_ms": elapsed_ms,
                    }
                except Exception as error:
                    raise ToolError(f"Hover action failed on {ref}: {error}") from error
            elif action_lower == "scroll":
                try:
                    handle.scroll_into_view_if_needed()
                    elapsed_ms = round((time.monotonic() - start_time) * 1000)
                    return {
                        "ok": True,
                        "action": "scroll",
                        "ref": ref,
                        "delta": asdict(StateDelta()),
                        "state_delta": {"revision": rev, "document_epoch": doc_epoch},
                        "document_epoch": doc_epoch,
                        "revision": rev,
                        "elapsed_ms": elapsed_ms,
                        "action_duration_ms": elapsed_ms,
                    }
                except Exception as error:
                    raise ToolError(f"Scroll action failed on {ref}: {error}") from error

    if action_lower == "scroll" and not ref:
        start_time = time.monotonic()
        try:
            delta_y = int(value) if (value and value.lstrip("-").isdigit()) else (500 if value != "up" else -500)
            page.mouse.wheel(0, delta_y)
            elapsed_ms = round((time.monotonic() - start_time) * 1000)
            doc_epoch = "d1"
            rev = 0
            try:
                state = manager._frame_state(page.main_frame)
                doc_epoch = f"d{state.epoch}"
            except Exception:
                pass
            try:
                rev = int(page.evaluate("() => window[Symbol.for('__OMNI_DOM_AGENT__')]?.revision ?? 0"))
            except Exception:
                pass
            return {
                "ok": True,
                "action": "scroll",
                "delta_y": delta_y,
                "delta": asdict(StateDelta()),
                "state_delta": {"revision": rev, "document_epoch": doc_epoch},
                "document_epoch": doc_epoch,
                "revision": rev,
                "elapsed_ms": elapsed_ms,
                "action_duration_ms": elapsed_ms,
            }
        except Exception as error:
            raise ToolError(f"Window scroll failed: {error}") from error

    if not ref:
        raise ToolError(f"Action {action} requires ref")

    try:
        res = act(page, action_lower, ref, value=value, manager=manager)
    except (OmniBrowserError, StaleRefError, ActionTimeoutError, ValueError) as error:
        raise ToolError(f"Action {action_lower} failed: {error}") from error
    except Exception as error:
        raise ToolError(f"Action {action_lower} failed with unexpected error: {error}") from error

    return {
        "ok": res.ok,
        "action": action_lower,
        "ref": ref,
        "delta": asdict(res.delta) if hasattr(res.delta, "__dataclass_fields__") else res.delta,
        "state_delta": res.state_delta,
        "document_epoch": res.document_epoch,
        "revision": res.revision,
        "elapsed_ms": res.action_duration_ms,
        "action_duration_ms": res.action_duration_ms,
    }


def _op_screenshot(worker: BrowserWorker, path: str | None = None, tab_id: str | None = None) -> dict[str, Any]:
    worker.validate_lease_health()
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    out_path = path or str(Path(tempfile.gettempdir()) / f"omnibrowser_screenshot_{uuid4().hex[:8]}.png")
    try:
        page.screenshot(path=out_path)
    except Exception as error:
        raise ToolError(f"Screenshot capture failed: {error}") from error
    return {
        "ok": True,
        "path": str(out_path),
        "url": page.url,
    }


def _op_recipe_run(
    worker: BrowserWorker,
    recipe_id: str,
    params: dict | None = None,
    tab_id: str | None = None,
) -> dict[str, Any]:
    worker.validate_lease_health()
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    store = RecipeStore()
    engine = RecipeEngine(store)
    try:
        res = engine.execute(recipe_id, page, params or {})
    except KeyError:
        raise ToolError(f"Unknown recipe: {recipe_id}")
    except Exception as error:
        raise ToolError(f"Recipe execution {recipe_id} failed: {error}") from error

    if not res.ok:
        step_idx = res.failure.get("step_index", res.completed_steps) if res.failure else res.completed_steps
        raise ToolError(f"Recipe {recipe_id} failed at step {step_idx}: {res.message}")

    return {
        "ok": res.ok,
        "recipe_id": recipe_id,
        "completed_steps": res.completed_steps,
        "fallback_required": res.fallback_required,
        "message": res.message,
    }


def _op_run_code(
    worker: BrowserWorker,
    code: str,
    mode: str = "read",
    timeout_ms: int | None = None,
    tab_id: str | None = None,
) -> dict[str, Any]:
    worker.validate_lease_health()
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    try:
        result = runBrowserCode(page, script=code, mode=mode, timeout_ms=timeout_ms)
    except Exception as error:
        raise ToolError(f"runBrowserCode failed: {error}") from error
    return {
        "ok": True,
        "result": result,
    }


def _op_list_tabs(worker: BrowserWorker) -> list[dict[str, Any]]:
    worker.validate_lease_health()
    manager = worker.get_manager()
    if not manager.context or not manager.browser or not manager.browser.is_connected():
        manager.connect()

    tabs = []
    # If scoped to a lease, list targets registered for that lease
    if worker.lease_id:
        from browser_core.broker.session_router import SessionRouter
        router = SessionRouter()
        lease_targets = router.target_registry.get_targets_for_lease(worker.lease_id)
        if lease_targets:
            for i, target in enumerate(lease_targets):
                tabs.append({
                    "index": i,
                    "tab_id": str(i),
                    "url": target.url or "about:blank",
                    "title": target.title or "about:blank",
                    "active": (i == 0),
                })
            return tabs

    for i, p in enumerate(manager.context.pages):
        tabs.append({
            "index": i,
            "tab_id": str(i),
            "url": p.url,
            "title": p.title(),
            "active": (i == 0),
        })
    return tabs


def _op_inspect(
    worker: BrowserWorker,
    mode: str = "dom",
    ref: str | None = None,
    depth: int = 3,
    tab_id: str | None = None,
) -> dict[str, Any]:
    worker.validate_lease_health()
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    try:
        content = inspect_primitive(manager, page, mode=mode, ref=ref, depth=depth)
    except Exception as error:
        raise ToolError(f"Inspect mode {mode} failed: {error}") from error
    return {
        "ok": True,
        "mode": mode,
        "content": content,
    }


def create_mcp_server(
    cdp_url: str = DEFAULT_CDP_URL,
    *,
    lease_id: str | None = None,
    fencing_token: int | None = None,
    test_mode: bool = False,
    server_name: str = "omnibrowser",
) -> MCPServer:
    """Instantiate and configure the FastMCP / MCPServer with all 8 core tools."""
    server = MCPServer(server_name)
    worker = BrowserWorker(
        cdp_url=cdp_url,
        test_mode=test_mode,
        lease_id=lease_id,
        fencing_token=fencing_token,
    )
    server.browser_worker = worker

    @server.tool(name="browser_navigate", description="Navigate active or specified tab to URL.", structured_output=False)
    def browser_navigate(url: str, tab_id: str | None = None) -> dict[str, Any]:
        """Navigate active or specified tab to URL."""
        return worker.run(_op_navigate, worker, url, tab_id)

    @server.tool(name="browser_observe", description="Extract compact semantic DOM tree (<= 15 KB budget) with opaque ref IDs.", structured_output=False)
    def browser_observe(tab_id: str | None = None, match: str | None = None) -> dict[str, Any]:
        """Extract compact semantic DOM tree (<= 15 KB budget) with opaque ref IDs."""
        return worker.run(_op_observe, worker, tab_id, match)

    @server.tool(name="browser_act", description="Execute native user action (click, fill, select, press, scroll, hover).", structured_output=False)
    def browser_act(
        action: str,
        ref: str | None = None,
        value: str | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute native user action (click, fill, select, press, scroll, hover)."""
        return worker.run(_op_act, worker, action, ref, value, tab_id)

    @server.tool(name="browser_screenshot", description="Capture full or viewport screenshot.", structured_output=False)
    def browser_screenshot(path: str | None = None, tab_id: str | None = None) -> dict[str, Any]:
        """Capture full or viewport screenshot."""
        return worker.run(_op_screenshot, worker, path, tab_id)

    @server.tool(name="browser_recipe_run", description="Execute parameterized procedural workflow recipe.", structured_output=False)
    def browser_recipe_run(
        recipe_id: str,
        params: dict | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute parameterized procedural workflow recipe."""
        return worker.run(_op_recipe_run, worker, recipe_id, params, tab_id)

    @server.tool(name="browser_run_code", description="Bounded browser JS evaluation with strict output constraints.", structured_output=False)
    def browser_run_code(
        code: str,
        mode: str = "read",
        timeout_ms: int | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        """Bounded browser JS evaluation with strict output constraints."""
        return worker.run(_op_run_code, worker, code, mode, timeout_ms, tab_id)

    @server.tool(name="browser_list_tabs", description="List all open tabs and active targets.", structured_output=False)
    def browser_list_tabs() -> list[dict[str, Any]]:
        """List all open tabs and active targets."""
        return worker.run(_op_list_tabs, worker)

    @server.tool(name="browser_inspect", description="Inspect accessibility tree, frame hierarchy, or DOM bounds.", structured_output=False)
    def browser_inspect(
        mode: str = "dom",
        ref: str | None = None,
        depth: int = 3,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        """Inspect accessibility tree, frame hierarchy, or DOM bounds."""
        return worker.run(_op_inspect, worker, mode, ref, depth, tab_id)

    return server


def run_mcp_server(
    cdp_url: str = DEFAULT_CDP_URL,
    *,
    lease_id: str | None = None,
    fencing_token: int | None = None,
    test_mode: bool = False,
) -> int:
    """Run the stdio MCP server."""
    server = create_mcp_server(
        cdp_url=cdp_url,
        lease_id=lease_id,
        fencing_token=fencing_token,
        test_mode=test_mode,
    )
    try:
        server.run("stdio")
    finally:
        if hasattr(server, "browser_worker"):
            server.browser_worker.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_mcp_server())
