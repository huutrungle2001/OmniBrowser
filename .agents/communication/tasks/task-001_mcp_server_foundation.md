# Task: task-001_mcp_server_foundation

RECORD_TYPE: TASK
RECORD_ID: task-001_mcp_server_foundation
STATUS: TASK_READY
ATTEMPT: 7
CREATED_AT: 2026-10-05T22:40:00Z
UPDATED_AT: 2026-10-06T02:35:00Z
FROM: orchestrator
TO: implementer

## Objective

Transform OmniBrowser into a standard high-performance Model Context Protocol (MCP) server over standard JSON-RPC 2.0 stdio transport.

In Attempt 7 (Final Sign-Off Pass Authorized by Human Operator), resolve the final 2 edge cases identified in `review-task-001_mcp_server_foundation.md` (commit `6f1194b`):
1. Empty Leased Browser Context Resolution: In `src/browser_core/page_manager.py`, resolve `self.browser_context_id` even when candidate browser contexts have zero pages (`ctx.pages == []`) by creating a temporary probe page (`p = ctx.new_page()`) to query `Target.getTargetInfo`. If the context matches, tag `ctx._browser_context_id = self.browser_context_id`, retain `p`, and bind `self.context = ctx`; if not, close `p.close()` and inspect the next context.
2. Authentic 2026 SDK Envelope Modeling & Transport Reserve Buffer: In `src/browser_core/mcp_server.py`, update `_wire_size()` to simulate the exact modern MCP 2026-07-28 response envelope with `resultType: "complete"`, SDK runner `_meta: {"io.modelcontextprotocol/serverInfo": {"name": "omnibrowser", "version": "1.0.0"}}`, and 36-char UUID request ID. In `_is_over_budget()`, allocate a 256-byte transport headroom reserve (`_wire_size(p) > (15360 - 256)`).
3. Empty Context Positive Regression Test: In `tests/test_mcp_server.py`, add `test_page_manager_connect_empty_context` verifying that an authentic CDP browser context containing zero pages resolves cleanly.

## Scope

### Leased Files & Paths:
1. `src/browser_core/page_manager.py`:
   - In `PageManager.connect()`:
     - When `self.browser_context_id` is supplied:
       - Iterate over `self.browser.contexts`.
       - Check `getattr(ctx, "_browser_context_id", None) == self.browser_context_id`.
       - If not yet matched, inspect context's CDP context ID:
         - If `ctx.pages`: use `p = ctx.pages[0]`, created_probe = False.
         - If `not ctx.pages`: create probe page `p = ctx.new_page()`, created_probe = True.
         - Attach temporary CDP session `session = ctx.new_cdp_session(p)`:
           ```python
           try:
               info = session.send("Target.getTargetInfo")
               ctx_id = info.get("targetInfo", {}).get("browserContextId")
               if ctx_id == self.browser_context_id:
                   ctx._browser_context_id = self.browser_context_id
                   self.context = ctx
                   created_probe = False  # retain page for subsequent operations
                   break
           finally:
               try:
                   session.detach()
               except Exception:
                   pass
               if created_probe:
                   try:
                       p.close()
                   except Exception:
                       pass
           ```
       - If not found across all contexts, fail closed with `RuntimeError(f"Leased browser context '{self.browser_context_id}' not found in active contexts")`.

2. `src/browser_core/mcp_server.py`:
   - In `_wire_size(p)`:
     - Update simulated envelope to include MCP 2026-07-28 `resultType: "complete"`, SDK runner `_meta`, and 36-char UUID request ID:
       ```python
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
       ```
   - In `_is_over_budget(p)`:
     - Apply a 256-byte transport reserve: `return len(encoded) > MAX_OBSERVE_BUDGET_BYTES or _wire_size(p) > (15360 - 256)`.
     - Update docstring formally defining Invariant 3 as bounding the server-controlled `CallToolResult` response payload and transport-modeled outer packet.

3. `tests/test_mcp_server.py`:
   - Add `test_page_manager_connect_empty_context(ephemeral_cdp_url)`:
     - Connect `pm_setup = PageManager(ephemeral_cdp_url, test_mode=True).connect()`.
     - Create `ctx_empty = pm_setup.browser.new_context()`. Ensure `ctx_empty.pages == []`.
     - Create a probe page on `ctx_empty` temporarily to discover its authentic CDP `browserContextId`, then close that probe page so `ctx_empty.pages == []` again.
     - Connect `pm_leased = PageManager(ephemeral_cdp_url, test_mode=True, browser=pm_setup.browser, browser_context_id=real_empty_id).connect()`.
     - Assert `pm_leased.context is ctx_empty`.
   - Update wire size assertions in `test_mcp_server_observe_hard_15kb_boundary` and `test_mcp_server_core_tools_execution` to test the updated 2026 envelope with `resultType: "complete"` and `_meta`.

## Acceptance Criteria

1. **Empty Leased Context Resolution**:
   - `PageManager.connect()` resolves leased browser contexts via real Chrome CDP `Target.getTargetInfo` whether the context contains existing pages or is currently empty (`ctx.pages == []`).
2. **Accurate 2026 Wire Budget Envelope & Reserve**:
   - `_wire_size()` models standard MCP 2026-07-28 response envelopes including `resultType: "complete"`, SDK `_meta.io.modelcontextprotocol/serverInfo`, and 36-char UUID request ID.
   - 256-byte transport reserve is enforced to ensure standard and variable-length client request IDs cannot breach the 15,360-byte wire limit.
3. **Comprehensive Regression Passing**:
   - `pytest tests/test_mcp_server.py -v` passes 100% including new empty context and updated envelope tests.
   - `pytest tests/ -v` passes 100% across the entire repository (148+ tests, 0 failures).
   - Zero live profile pollution: tests never connect to port 17082.
4. **Clean Handoff**:
   - Result record updated and committed.
   - `python3 scripts/lint_communication_records.py` passes with exit code 0.

## Validation

1. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-001_mcp_server_foundation.md` exits 0.
2. `pytest tests/test_mcp_server.py -v` passes 100%.
3. `pytest tests/test_cli_legacy.py -v` passes 100%.
4. Full regression: `pytest tests/ -v` exits 0.
