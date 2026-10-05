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

try:
    from mcp.server.mcpserver import MCPServer
    FastMCP = MCPServer
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
        MCPServer = FastMCP
    except ImportError:
        from mcp.server import Server as MCPServer
        FastMCP = MCPServer

from .contracts import (
    ActionTimeoutError,
    DOMNodeRef,
    ExitCode,
    OmniBrowserError,
    ObserveResult,
    ObservedNode,
    StaleRefError,
    TargetNotFoundError,
)
from .engine import act
from .page_manager import PageManager, _reset_color_scheme
from .primitives import inspect as inspect_primitive, runBrowserCode
from .recipes import RecipeEngine, RecipeStore

DEFAULT_CDP_URL = os.environ.get("CDP_URL", "http://127.0.0.1:17082")
MAX_OBSERVE_BUDGET_BYTES = 15 * 1024  # 15 KB JSON budget


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

        if self._manager is None or not self._manager.browser or not self._manager.browser.is_connected():
            self._manager = PageManager(self.cdp_url, test_mode=self.test_mode).connect()
        return self._manager

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
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    page.goto(url, wait_until="domcontentloaded")
    manager.install_scanner(page)
    tab_index = manager.context.pages.index(page) if manager.context and page in manager.context.pages else 0
    return {
        "ok": True,
        "url": page.url,
        "title": page.title(),
        "tab_id": str(tab_index),
    }


def _op_observe(worker: BrowserWorker, tab_id: str | None = None, match: str | None = None) -> dict[str, Any]:
    manager = worker.get_manager()
    target_tab = tab_id or match
    page = _get_page(manager, target_tab)
    if page not in manager._sessions:
        manager.install_scanner(page)
    obs = manager.observe(page)
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
    }

    # Strict Token & Payload Budget Constraint (<= 15 KB JSON)
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    while len(encoded) > MAX_OBSERVE_BUDGET_BYTES and len(tree_nodes) > 1:
        cut = max(1, len(tree_nodes) * 4 // 5)
        if cut >= len(tree_nodes):
            cut = len(tree_nodes) - 1
        tree_nodes = tree_nodes[:cut]
        payload["tree"] = tree_nodes
        payload["coverage"]["is_complete"] = False
        payload["coverage"]["truncated"] = True
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    return payload


def _op_act(
    worker: BrowserWorker,
    action: str,
    ref: str | None = None,
    value: str | None = None,
    tab_id: str | None = None,
) -> dict[str, Any]:
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    if page not in manager._sessions:
        manager.install_scanner(page)
    action_lower = action.lower().strip()

    if ref:
        dom_ref = DOMNodeRef.parse(ref)
        handle = manager.resolve_element(page, dom_ref)
        if handle is None:
            raise StaleRefError(f"Stale ref {ref}; call browser_observe to refresh refs")

        # Occlusion detection: Avoid clicking covered elements
        if action_lower == "click":
            try:
                is_occ = page.evaluate("el => window[Symbol.for('__OMNI_DOM_AGENT__')]?.isOccluded?.(el)", handle)
                if is_occ:
                    return {
                        "ok": False,
                        "error": f"Element {ref} is occluded by another element/modal; cannot click covered target.",
                        "occluded": True,
                    }
            except Exception:
                pass

        if action_lower == "hover":
            handle.hover()
            return {"ok": True, "action": "hover", "ref": ref}
        elif action_lower == "scroll":
            handle.scroll_into_view_if_needed()
            return {"ok": True, "action": "scroll", "ref": ref}

    if action_lower == "scroll" and not ref:
        delta_y = int(value) if (value and value.lstrip("-").isdigit()) else (500 if value != "up" else -500)
        page.mouse.wheel(0, delta_y)
        return {"ok": True, "action": "scroll", "delta_y": delta_y}

    if not ref:
        raise ValueError(f"Action {action} requires ref")

    res = act(page, action_lower, ref, value=value, manager=manager)
    return {
        "ok": res.ok,
        "action": action_lower,
        "ref": ref,
        "delta": asdict(res.delta) if hasattr(res.delta, "__dataclass_fields__") else res.delta,
        "document_epoch": res.document_epoch,
        "revision": res.revision,
    }


def _op_screenshot(worker: BrowserWorker, path: str | None = None, tab_id: str | None = None) -> dict[str, Any]:
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    out_path = path or str(Path(tempfile.gettempdir()) / f"omnibrowser_screenshot_{uuid4().hex[:8]}.png")
    page.screenshot(path=out_path)
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
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    store = RecipeStore()
    engine = RecipeEngine(store)
    try:
        res = engine.execute(recipe_id, page, params or {})
        return {
            "ok": res.ok,
            "recipe_id": recipe_id,
            "step_index": res.step_index,
            "fallback_required": res.fallback_required,
            "diagnostic": res.diagnostic,
        }
    except KeyError:
        return {
            "ok": False,
            "recipe_id": recipe_id,
            "error": f"Unknown recipe: {recipe_id}",
            "fallback_required": True,
        }
    except Exception as error:
        return {
            "ok": False,
            "recipe_id": recipe_id,
            "error": str(error),
            "fallback_required": True,
        }


def _op_run_code(
    worker: BrowserWorker,
    code: str,
    mode: str = "read",
    tab_id: str | None = None,
) -> dict[str, Any]:
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    result = runBrowserCode(page, script=code, mode=mode)
    return {
        "ok": True,
        "result": result,
    }


def _op_list_tabs(worker: BrowserWorker) -> list[dict[str, Any]]:
    manager = worker.get_manager()
    if not manager.context or not manager.browser or not manager.browser.is_connected():
        manager.connect()
    tabs = []
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
    tab_id: str | None = None,
) -> dict[str, Any]:
    manager = worker.get_manager()
    page = _get_page(manager, tab_id)
    content = inspect_primitive(manager, page, mode=mode)
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

    @server.tool(name="browser_navigate", description="Navigate active or specified tab to URL.")
    def browser_navigate(url: str, tab_id: str | None = None) -> dict[str, Any]:
        """Navigate active or specified tab to URL."""
        return worker.run(_op_navigate, worker, url, tab_id)

    @server.tool(name="browser_observe", description="Extract compact semantic DOM tree (<= 15 KB budget) with opaque ref IDs.")
    def browser_observe(tab_id: str | None = None, match: str | None = None) -> dict[str, Any]:
        """Extract compact semantic DOM tree (<= 15 KB budget) with opaque ref IDs."""
        return worker.run(_op_observe, worker, tab_id, match)

    @server.tool(name="browser_act", description="Execute native user action (click, fill, select, press, scroll, hover).")
    def browser_act(
        action: str,
        ref: str | None = None,
        value: str | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute native user action (click, fill, select, press, scroll, hover)."""
        return worker.run(_op_act, worker, action, ref, value, tab_id)

    @server.tool(name="browser_screenshot", description="Capture full or viewport screenshot.")
    def browser_screenshot(path: str | None = None, tab_id: str | None = None) -> dict[str, Any]:
        """Capture full or viewport screenshot."""
        return worker.run(_op_screenshot, worker, path, tab_id)

    @server.tool(name="browser_recipe_run", description="Execute parameterized procedural workflow recipe.")
    def browser_recipe_run(
        recipe_id: str,
        params: dict | None = None,
        tab_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute parameterized procedural workflow recipe."""
        return worker.run(_op_recipe_run, worker, recipe_id, params, tab_id)

    @server.tool(name="browser_run_code", description="Bounded browser JS evaluation with strict output constraints.")
    def browser_run_code(code: str, mode: str = "read", tab_id: str | None = None) -> dict[str, Any]:
        """Bounded browser JS evaluation with strict output constraints."""
        return worker.run(_op_run_code, worker, code, mode, tab_id)

    @server.tool(name="browser_list_tabs", description="List all open tabs and active targets.")
    def browser_list_tabs() -> list[dict[str, Any]]:
        """List all open tabs and active targets."""
        return worker.run(_op_list_tabs, worker)

    @server.tool(name="browser_inspect", description="Inspect accessibility tree, frame hierarchy, or DOM bounds.")
    def browser_inspect(mode: str = "dom", tab_id: str | None = None) -> dict[str, Any]:
        """Inspect accessibility tree, frame hierarchy, or DOM bounds."""
        return worker.run(_op_inspect, worker, mode, tab_id)

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
