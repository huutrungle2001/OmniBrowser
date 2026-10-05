"""Comprehensive end-to-end tests for the OmniBrowser MCP Server.

Verifies:
- Standard Model Context Protocol (MCP) JSON-RPC 2.0 stdio compliance
- Registration and typed schemas of all 8 core tools
- Execution of all 8 tools against an ephemeral Chrome CDP instance
- Zero Live Profile Pollution safety invariant
- Token & Payload Budget Constraint (<= 15 KB JSON observe payload)
- Occlusion detection & elementFromPoint checking preventing covered clicks
- Error handling and stale ref detection
- Subprocess stdio client integration
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import time

import anyio
import pytest

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from browser_core.mcp_server import (
    DEFAULT_CDP_URL,
    MAX_OBSERVE_BUDGET_BYTES,
    create_mcp_server,
)


def _call_tool_sync(server, name: str, arguments: dict):
    """Helper to synchronously invoke server.call_tool via anyio."""
    async def _runner():
        res = await server.call_tool(name, arguments)
        if (
            hasattr(res, "structured_content")
            and isinstance(res.structured_content, dict)
            and "result" in res.structured_content
            and isinstance(res.structured_content["result"], list)
        ):
            return res.structured_content["result"]

        if hasattr(res, "content") and res.content:
            items = []
            for item in res.content:
                if hasattr(item, "text"):
                    try:
                        items.append(json.loads(item.text))
                    except Exception:
                        items.append(item.text)
            if len(items) == 1:
                return items[0]
            if len(items) > 1:
                return items

        if hasattr(res, "structured_content") and res.structured_content is not None:
            return res.structured_content
        return res

    return anyio.run(_runner)


def _list_tools_sync(server):
    """Helper to synchronously list tools via anyio."""
    async def _runner():
        return await server.list_tools()

    return anyio.run(_runner)


def test_mcp_server_tool_registration():
    """Verifies all 8 core browser tools are registered with schemas and descriptions."""
    server = create_mcp_server("http://127.0.0.1:9999", test_mode=True)
    try:
        tools = _list_tools_sync(server)
        tool_names = {t.name for t in tools}
        expected_tools = {
            "browser_navigate",
            "browser_observe",
            "browser_act",
            "browser_screenshot",
            "browser_recipe_run",
            "browser_run_code",
            "browser_list_tabs",
            "browser_inspect",
        }
        assert expected_tools.issubset(tool_names), f"Missing tools: {expected_tools - tool_names}"
        for tool in tools:
            assert tool.description is not None
            assert len(tool.description) > 0
    finally:
        server.browser_worker.shutdown()


def test_mcp_server_safety_invariant_blocks_port_17082():
    """Verifies the critical safety invariant: test mode refuses port 17082."""
    server = create_mcp_server("http://127.0.0.1:17082", test_mode=True)
    try:
        with pytest.raises(ValueError, match="SAFETY INVARIANT VIOLATION"):
            server.browser_worker.get_manager()
    finally:
        server.browser_worker.shutdown()


def test_mcp_server_core_tools_execution(ephemeral_cdp_url, fixture_server):
    """Verifies execution of all 8 tools against an ephemeral Chrome instance."""
    server = create_mcp_server(ephemeral_cdp_url, test_mode=True)
    try:
        # 1. browser_list_tabs
        tabs = _call_tool_sync(server, "browser_list_tabs", {})
        assert isinstance(tabs, list)
        assert len(tabs) >= 1

        # 2. browser_navigate
        target_url = f"{fixture_server}/interactive_page.html"
        nav_res = _call_tool_sync(server, "browser_navigate", {"url": target_url})
        assert nav_res["ok"] is True
        assert "interactive_page.html" in nav_res["url"]
        assert nav_res["title"] == "OmniBrowser interactive fixture"

        # 3. browser_observe & Budget Constraint (<= 15 KB)
        obs_res = _call_tool_sync(server, "browser_observe", {})
        assert obs_res["ok"] is True
        assert "tree" in obs_res
        encoded = json.dumps(obs_res, ensure_ascii=False).encode("utf-8")
        assert len(encoded) <= MAX_OBSERVE_BUDGET_BYTES, f"Payload {len(encoded)} exceeds 15 KB limit"

        # Locate interactive elements
        name_input = next((n for n in obs_res["tree"] if n["role"] == "textbox" and n["name"] == "Name"), None)
        assert name_input is not None
        save_btn = next((n for n in obs_res["tree"] if n["role"] == "button" and "Save" in n["name"]), None)
        assert save_btn is not None

        # 4. browser_act (fill and click)
        fill_res = _call_tool_sync(
            server,
            "browser_act",
            {"action": "fill", "ref": name_input["ref"], "value": "Ada Lovelace"},
        )
        assert fill_res["ok"] is True

        click_res = _call_tool_sync(
            server,
            "browser_act",
            {"action": "click", "ref": save_btn["ref"]},
        )
        assert click_res["ok"] is True

        # 5. browser_run_code (verify DOM updated)
        code_res = _call_tool_sync(
            server,
            "browser_run_code",
            {"code": "return document.getElementById('status').innerText;", "mode": "read"},
        )
        assert code_res["ok"] is True
        assert "Ada Lovelace" in str(code_res["result"])

        # 6. browser_inspect
        dom_inspect = _call_tool_sync(server, "browser_inspect", {"mode": "dom"})
        assert dom_inspect["ok"] is True
        assert "<body" in dom_inspect["content"].lower()

        tree_inspect = _call_tool_sync(server, "browser_inspect", {"mode": "frame-tree"})
        assert tree_inspect["ok"] is True
        assert "interactive_page" in tree_inspect["content"]

        # 7. browser_screenshot
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            shot_res = _call_tool_sync(server, "browser_screenshot", {"path": tmp_path})
            assert shot_res["ok"] is True
            assert Path(tmp_path).exists()
            assert Path(tmp_path).stat().st_size > 0
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # 8. browser_recipe_run
        recipe_res = _call_tool_sync(
            server,
            "browser_recipe_run",
            {"recipe_id": "standard_enrolment_form", "params": {"name": "Grace Hopper", "course": "CS101"}},
        )
        assert "recipe_id" in recipe_res
        assert "fallback_required" in recipe_res

        unknown_recipe = _call_tool_sync(
            server,
            "browser_recipe_run",
            {"recipe_id": "nonexistent_recipe"},
        )
        assert unknown_recipe["ok"] is False
        assert "Unknown recipe" in unknown_recipe.get("error", "")

    finally:
        server.browser_worker.shutdown()


def test_mcp_server_occlusion_detection(ephemeral_cdp_url, fixture_server):
    """Verifies that dom_agent.js elementFromPoint checking flags occluded elements and prevents clicking."""
    server = create_mcp_server(ephemeral_cdp_url, test_mode=True)
    try:
        target_url = f"{fixture_server}/interactive_page.html"
        _call_tool_sync(server, "browser_navigate", {"url": target_url})

        # Observe baseline before overlay
        obs_before = _call_tool_sync(server, "browser_observe", {})
        save_btn = next((n for n in obs_before["tree"] if n["role"] == "button" and "Save" in n["name"]), None)
        assert save_btn is not None
        assert save_btn.get("occluded") is False
        assert save_btn.get("interactive") is True

        # Inject high z-index blocking modal overlay covering the viewport
        _call_tool_sync(
            server,
            "browser_run_code",
            {
                "code": """
                const modal = document.createElement('div');
                modal.id = 'blocking-modal-overlay';
                modal.style.position = 'fixed';
                modal.style.top = '0';
                modal.style.left = '0';
                modal.style.width = '100vw';
                modal.style.height = '100vh';
                modal.style.backgroundColor = 'rgba(0, 0, 0, 0.8)';
                modal.style.zIndex = '999999';
                document.body.appendChild(modal);
                return true;
                """,
                "mode": "write",
            },
        )

        # Re-observe with overlay present
        obs_after = _call_tool_sync(server, "browser_observe", {})
        covered_btn = next((n for n in obs_after["tree"] if n["role"] == "button" and "Save" in n["name"]), None)
        assert covered_btn is not None
        # Occlusion check must flag the covered element
        assert covered_btn.get("occluded") is True
        assert covered_btn.get("interactive") is False

        # Attempt to click occluded element via browser_act
        click_res = _call_tool_sync(
            server,
            "browser_act",
            {"action": "click", "ref": covered_btn["ref"]},
        )
        assert click_res["ok"] is False
        assert click_res.get("occluded") is True or "occluded" in click_res.get("error", "").lower()

    finally:
        server.browser_worker.shutdown()


def test_mcp_server_stdio_subprocess(ephemeral_cdp_url, fixture_server):
    """End-to-end test verifying JSON-RPC 2.0 stdio communication via ClientSession."""
    repo_root = Path(__file__).resolve().parents[1]
    controller_script = repo_root / "scripts" / "cdp_controller.py"

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(controller_script), "--mcp", f"--cdp-url={ephemeral_cdp_url}"],
        env=dict(os.environ, PYTHONPATH=str(repo_root / "src")),
    )

    async def _run_client():
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools_res = await session.list_tools()
                names = {t.name for t in tools_res.tools}
                assert "browser_navigate" in names
                assert "browser_observe" in names
                assert "browser_act" in names
                assert "browser_list_tabs" in names

                # Call browser_navigate over JSON-RPC 2.0 stdio
                nav_res = await session.call_tool(
                    "browser_navigate",
                    {"url": f"{fixture_server}/interactive_page.html"},
                )
                assert not getattr(nav_res, "is_error", False)
                for content in nav_res.content:
                    if hasattr(content, "text"):
                        data = json.loads(content.text)
                        assert data.get("ok") is True

    anyio.run(_run_client)
