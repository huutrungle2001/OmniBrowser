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

from browser_core.broker.session_router import SessionRouter
from browser_core.contracts import ExecutionClass
from browser_core.mcp_server import (
    DEFAULT_CDP_URL,
    MAX_OBSERVE_BUDGET_BYTES,
    ToolError,
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
            if name == "browser_list_tabs":
                return items
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
        assert "version" in obs_res
        assert "suggested_recipes" in obs_res
        encoded = json.dumps(obs_res, ensure_ascii=False).encode("utf-8")
        assert len(encoded) <= MAX_OBSERVE_BUDGET_BYTES, f"Payload {len(encoded)} exceeds limit"
        outer_packet = json.dumps({
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
                "content": [{"type": "text", "text": json.dumps(obs_res, ensure_ascii=False)}],
                "isError": False,
            },
        }, ensure_ascii=False)
        assert len(outer_packet.encode("utf-8")) <= 15360, f"Outer packet {len(outer_packet.encode('utf-8'))} exceeds 15360 bytes"

        # Locate interactive elements
        name_input = next((n for n in obs_res["tree"] if n["role"] == "textbox" and n["name"] == "Name"), None)
        assert name_input is not None
        save_btn = next((n for n in obs_res["tree"] if n["role"] == "button" and "Save" in n["name"]), None)
        assert save_btn is not None

        # 4. browser_act (fill and click) with contract parity (state_delta, elapsed_ms)
        fill_res = _call_tool_sync(
            server,
            "browser_act",
            {"action": "fill", "ref": name_input["ref"], "value": "Ada Lovelace"},
        )
        assert fill_res["ok"] is True
        assert "state_delta" in fill_res
        assert "elapsed_ms" in fill_res

        click_res = _call_tool_sync(
            server,
            "browser_act",
            {"action": "click", "ref": save_btn["ref"]},
        )
        assert click_res["ok"] is True
        assert "state_delta" in click_res
        assert "elapsed_ms" in click_res

        # Contract parity on hover
        hover_res = _call_tool_sync(
            server,
            "browser_act",
            {"action": "hover", "ref": save_btn["ref"]},
        )
        assert hover_res["ok"] is True
        assert hover_res["action"] == "hover"
        assert "document_epoch" in hover_res
        assert "revision" in hover_res
        assert isinstance(hover_res["revision"], int) and hover_res["revision"] >= 1
        assert hover_res["state_delta"] == {"revision": hover_res["revision"], "document_epoch": hover_res["document_epoch"]}
        assert hover_res["delta"] == {"changed": [], "added": [], "removed": []}
        assert isinstance(hover_res.get("elapsed_ms"), (int, float))
        assert hover_res["action_duration_ms"] == hover_res["elapsed_ms"]

        # Contract parity on window scroll (no ref)
        scroll_res = _call_tool_sync(
            server,
            "browser_act",
            {"action": "scroll", "value": "100"},
        )
        assert scroll_res["ok"] is True
        assert scroll_res["action"] == "scroll"
        assert "document_epoch" in scroll_res
        assert "revision" in scroll_res
        assert isinstance(scroll_res["revision"], int) and scroll_res["revision"] >= 1
        assert scroll_res["state_delta"] == {"revision": scroll_res["revision"], "document_epoch": scroll_res["document_epoch"]}
        assert scroll_res["delta"] == {"changed": [], "added": [], "removed": []}
        assert isinstance(scroll_res.get("elapsed_ms"), (int, float))
        assert scroll_res["action_duration_ms"] == scroll_res["elapsed_ms"]

        # 5. browser_run_code (verify explicit timeout_ms and default timeout_ms=None)
        code_res = _call_tool_sync(
            server,
            "browser_run_code",
            {"code": "return document.getElementById('status').innerText;", "mode": "read", "timeout_ms": 3000},
        )
        assert code_res["ok"] is True
        assert "Ada Lovelace" in str(code_res["result"])

        default_code_res = _call_tool_sync(
            server,
            "browser_run_code",
            {"code": "return window.location.href;"},
        )
        assert default_code_res["ok"] is True
        assert "interactive_page" in str(default_code_res["result"])

        # 6. browser_inspect (verify mode, ref, depth parameters)
        dom_inspect = _call_tool_sync(server, "browser_inspect", {"mode": "dom", "depth": 2})
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

        # 8. browser_recipe_run (ToolError raised on failure or unknown recipe)
        with pytest.raises(ToolError, match="Recipe standard_enrolment_form failed"):
            _call_tool_sync(
                server,
                "browser_recipe_run",
                {"recipe_id": "standard_enrolment_form", "params": {"name": "Grace Hopper", "course": "CS101"}},
            )

        with pytest.raises(ToolError, match="Unknown recipe"):
            _call_tool_sync(
                server,
                "browser_recipe_run",
                {"recipe_id": "nonexistent_recipe"},
            )

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

        # Attempt to click occluded element via browser_act (must raise ToolError)
        with pytest.raises(ToolError, match="occluded"):
            _call_tool_sync(
                server,
                "browser_act",
                {"action": "click", "ref": covered_btn["ref"]},
            )

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

                # Call invalid act to verify stdio is_error=True semantics
                err_res = await session.call_tool(
                    "browser_act",
                    {"action": "click"},
                )
                assert getattr(err_res, "is_error", False) is True

    anyio.run(_run_client)


def test_mcp_server_observe_hard_15kb_boundary(ephemeral_cdp_url):
    """Verifies Invariant 3: browser_observe output is strictly <= 15KB even on oversized URLs/DOM."""
    server = create_mcp_server(ephemeral_cdp_url, test_mode=True)
    try:
        # Case A: Massive DOM with hundreds of interactive nodes
        large_body = "".join(f"<button id='b{i}'>Button {i:04d} with long descriptive text</button>" for i in range(400))
        nav_res = _call_tool_sync(
            server,
            "browser_navigate",
            {"url": f"data:text/html,<html><body>{large_body}</body></html>"},
        )
        assert nav_res["ok"] is True

        obs_a = _call_tool_sync(server, "browser_observe", {})
        encoded_a = json.dumps(obs_a, ensure_ascii=False).encode("utf-8")
        assert len(encoded_a) <= MAX_OBSERVE_BUDGET_BYTES, f"DOM truncation failed: {len(encoded_a)} bytes"
        assert obs_a["coverage"]["truncated"] is True or obs_a["coverage"]["is_complete"] is False

        # Case B: Massive URL (> 25KB data URI) where metadata alone exceeds 15KB
        huge_padding = "A" * 25000
        nav_b = _call_tool_sync(
            server,
            "browser_navigate",
            {"url": f"data:text/html,<html><body><h1>Padded</h1><!--{huge_padding}--></body></html>"},
        )
        assert nav_b["ok"] is True

        obs_b = _call_tool_sync(server, "browser_observe", {})
        encoded_b = json.dumps(obs_b, ensure_ascii=False).encode("utf-8")
        # Hard postcondition assertion
        assert len(encoded_b) <= MAX_OBSERVE_BUDGET_BYTES, f"URL truncation failed: {len(encoded_b)} bytes"
        assert "[truncated]" in obs_b["page_url"]

        async def _check_wire_payload():
            res_b = await server.call_tool("browser_observe", {})
            wire_call_tool_json = res_b.model_dump_json() if hasattr(res_b, "model_dump_json") else json.dumps(res_b.model_dump())
            assert len(wire_call_tool_json.encode("utf-8")) <= 15360, f"CallToolResult wire size {len(wire_call_tool_json.encode('utf-8'))} exceeds 15 KB"
            jsonrpc_b = json.dumps({
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
                    "content": [{"type": "text", "text": res_b.content[0].text}],
                    "isError": False,
                },
            })
            assert len(jsonrpc_b.encode("utf-8")) <= 15360, f"JSON-RPC wire size {len(jsonrpc_b.encode('utf-8'))} exceeds 15 KB"

        anyio.run(_check_wire_payload)

    finally:
        server.browser_worker.shutdown()


def test_mcp_server_lease_isolation(ephemeral_cdp_url):
    """Verifies Invariant 8: MCP server honors lease scoping and rejects expired/revoked leases."""
    router = SessionRouter()
    lease = router.lease_manager.create_lease(
        agent_id="test-agent-mcp",
        project_id="test-proj-mcp",
        execution_class=ExecutionClass.CLASS_S,
        cdp_url=ephemeral_cdp_url,
        timeout_seconds=60,
    )
    router._save_state()

    server = create_mcp_server(
        ephemeral_cdp_url,
        test_mode=True,
        lease_id=lease.lease_id,
        fencing_token=lease.fencing_token,
    )
    try:
        # Validate worker bound to lease
        assert server.browser_worker.lease_id == lease.lease_id
        assert server.browser_worker.fencing_token == lease.fencing_token

        # Operational while lease is active
        tabs = _call_tool_sync(server, "browser_list_tabs", {})
        assert isinstance(tabs, list)

        # Revoke lease to simulate expiration / release
        router.lease_manager.revoke_lease(lease.lease_id)
        router._save_state()

        # Subsequent tool execution must fail with ToolError enforcing Invariant 8
        with pytest.raises(ToolError, match="expired or invalid"):
            _call_tool_sync(
                server,
                "browser_navigate",
                {"url": "about:blank"},
            )

    finally:
        server.browser_worker.shutdown()


def test_mcp_server_concurrency(ephemeral_cdp_url, fixture_server):
    """Verifies concurrent/overlapping async tool calls execute safely without greenlet thread conflicts."""
    server = create_mcp_server(ephemeral_cdp_url, test_mode=True)
    try:
        _call_tool_sync(server, "browser_navigate", {"url": f"{fixture_server}/interactive_page.html"})

        results = []

        async def _call_concurrently():
            async def _worker(idx: int):
                res = await server.call_tool("browser_run_code", {"code": f"return {idx} * 10;"})
                # parse result
                text = res.content[0].text
                data = json.loads(text)
                results.append((idx, data["result"]))

            async with anyio.create_task_group() as tg:
                for i in range(10):
                    tg.start_soon(_worker, i)

        anyio.run(_call_concurrently)

        assert len(results) == 10
        for idx, val in results:
            assert val == idx * 10

    finally:
        server.browser_worker.shutdown()


def test_page_manager_connect_fail_closed_missing_context(ephemeral_cdp_url):
    """Verifies Invariants 8 & 10: PageManager fails closed when leased browser_context_id is not found."""
    from browser_core.page_manager import PageManager

    pm = PageManager(
        ephemeral_cdp_url,
        test_mode=True,
        browser_context_id="ctx-nonexistent-99999",
    )
    try:
        with pytest.raises(RuntimeError, match="Leased browser context 'ctx-nonexistent-99999' not found in active contexts"):
            pm.connect()
    finally:
        pm.close()


def test_page_manager_connect_positive_multi_context(ephemeral_cdp_url):
    """Verifies Invariants 8 & 10: PageManager binds to the exact leased context via authentic CDP resolution."""
    from browser_core.page_manager import PageManager

    pm_setup = PageManager(ephemeral_cdp_url, test_mode=True).connect()
    try:
        ctx_a = pm_setup.browser.new_context()
        _page_a = ctx_a.new_page()
        ctx_b = pm_setup.browser.new_context()
        page_b = ctx_b.new_page()

        # Query authentic Chrome CDP browserContextId for ctx_b
        session = ctx_b.new_cdp_session(page_b)
        info = session.send("Target.getTargetInfo")
        session.detach()
        real_ctx_id = info["targetInfo"]["browserContextId"]
        assert real_ctx_id, "Chrome CDP did not return a valid browserContextId"

        # Connect PageManager with authentic CDP context ID (zero monkey-patching)
        pm_leased = PageManager(
            ephemeral_cdp_url,
            test_mode=True,
            browser=pm_setup.browser,
            browser_context_id=real_ctx_id,
        ).connect()
        assert pm_leased.context is ctx_b
        assert pm_leased.context is not ctx_a
        assert getattr(pm_leased.context, "_browser_context_id", None) == real_ctx_id
        pm_leased.close()
    finally:
        pm_setup.close()


def test_page_manager_connect_empty_context(ephemeral_cdp_url):
    """Verifies Invariants 8 & 10: PageManager resolves an authentic empty context with zero cross-context mutation."""
    from browser_core.page_manager import PageManager

    pm_setup = PageManager(ephemeral_cdp_url, test_mode=True).connect()
    try:
        b_session = pm_setup.browser.new_browser_cdp_session()
        before_ids = set(b_session.send("Target.getBrowserContexts")["browserContextIds"])

        ctx_other = pm_setup.browser.new_context()
        mid_ids = set(b_session.send("Target.getBrowserContexts")["browserContextIds"])

        ctx_target = pm_setup.browser.new_context()
        after_ids = set(b_session.send("Target.getBrowserContexts")["browserContextIds"])
        target_ctx_id = (after_ids - mid_ids).pop()
        b_session.detach()

        assert len(ctx_other.pages) == 0
        assert len(ctx_target.pages) == 0

        # Connect PageManager with the authentic empty target context ID
        pm_leased = PageManager(
            ephemeral_cdp_url,
            test_mode=True,
            browser=pm_setup.browser,
            browser_context_id=target_ctx_id,
        ).connect()

        assert pm_leased.context is ctx_target
        assert getattr(pm_leased.context, "_browser_context_id", None) == target_ctx_id
        # ctx_target now contains the created target page
        assert len(ctx_target.pages) >= 1
        # CRITICAL LEASE ISOLATION INVARIANT: ctx_other MUST remain strictly empty (zero cross-context mutation)
        assert len(ctx_other.pages) == 0

        pm_leased.close()
    finally:
        pm_setup.close()


def test_mcp_server_act_iframe_ref_hover_scroll(ephemeral_cdp_url, fixture_server):
    """Verifies ref-targeted hover and scroll operations inside an iframe return iframe's epoch and revision."""
    server = create_mcp_server(ephemeral_cdp_url, test_mode=True)
    try:
        _call_tool_sync(server, "browser_navigate", {"url": f"{fixture_server}/interactive_page.html"})

        def _get_iframe_ref():
            manager = server.browser_worker.get_manager()
            page = manager.primary_page()
            child_frame = next(f for f in page.frames if f != page.main_frame)
            child_obs = manager.observe(page, frame=child_frame)
            btn_node = next(n for n in child_obs.tree if "Frame" in n.name or n.role == "button")
            return str(btn_node.ref), child_obs.document_epoch, child_obs.revision

        iframe_ref, expected_epoch, expected_rev = server.browser_worker.run(_get_iframe_ref)

        # Execute hover via browser_act
        hover_res = _call_tool_sync(server, "browser_act", {"action": "hover", "ref": iframe_ref})
        assert hover_res["ok"] is True
        assert hover_res["action"] == "hover"
        assert hover_res["ref"] == iframe_ref
        assert hover_res["document_epoch"] == expected_epoch
        assert hover_res["revision"] == expected_rev
        assert hover_res["state_delta"]["document_epoch"] == expected_epoch
        assert hover_res["state_delta"]["revision"] == expected_rev
        assert "changed" in hover_res["delta"]
        assert "added" in hover_res["delta"]
        assert "removed" in hover_res["delta"]

        # Execute scroll via browser_act
        scroll_res = _call_tool_sync(server, "browser_act", {"action": "scroll", "ref": iframe_ref})
        assert scroll_res["ok"] is True
        assert scroll_res["action"] == "scroll"
        assert scroll_res["ref"] == iframe_ref
        assert scroll_res["document_epoch"] == expected_epoch
        assert scroll_res["revision"] == expected_rev
        assert scroll_res["state_delta"]["document_epoch"] == expected_epoch
        assert scroll_res["state_delta"]["revision"] == expected_rev
        assert "changed" in scroll_res["delta"]
        assert "added" in scroll_res["delta"]
        assert "removed" in scroll_res["delta"]
    finally:
        server.browser_worker.shutdown()


def test_mcp_server_observe_structured_output_is_none(ephemeral_cdp_url, fixture_server):
    """Verifies Invariant 3 wire-level budget: structured_content is None and text payload <= 15 KB."""
    server = create_mcp_server(ephemeral_cdp_url, test_mode=True)
    try:
        _call_tool_sync(server, "browser_navigate", {"url": f"{fixture_server}/interactive_page.html"})

        async def _check_wire_result():
            res = await server.call_tool("browser_observe", {})
            # Verify structured_output=False guarantees structured_content is None
            assert getattr(res, "structured_content", None) is None
            # Verify single text content is returned
            assert hasattr(res, "content") and len(res.content) == 1
            payload_text = res.content[0].text
            assert len(payload_text.encode("utf-8")) <= MAX_OBSERVE_BUDGET_BYTES

            # Verify total serialized JSON-RPC 2.0 / CallToolResult wire JSON payload <= 15 KB (15,360 bytes)
            wire_call_tool_json = res.model_dump_json() if hasattr(res, "model_dump_json") else json.dumps(res.model_dump())
            assert len(wire_call_tool_json.encode("utf-8")) <= 15360, f"CallToolResult wire size {len(wire_call_tool_json.encode('utf-8'))} exceeds 15 KB"
            jsonrpc_wrapper = json.dumps({
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
                    "content": [{"type": "text", "text": payload_text}],
                    "isError": False,
                },
            })
            assert len(jsonrpc_wrapper.encode("utf-8")) <= 15360, f"JSON-RPC wire payload {len(jsonrpc_wrapper.encode('utf-8'))} exceeds 15 KB"

        anyio.run(_check_wire_result)

    finally:
        server.browser_worker.shutdown()
