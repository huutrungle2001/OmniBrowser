# Task: task-001_mcp_server_foundation

RECORD_TYPE: TASK
RECORD_ID: task-001_mcp_server_foundation
STATUS: TASK_READY
ATTEMPT: 6
CREATED_AT: 2026-10-05T22:40:00Z
UPDATED_AT: 2026-10-06T02:08:00Z
FROM: orchestrator
TO: implementer

## Objective

Transform OmniBrowser into a standard high-performance Model Context Protocol (MCP) server over standard JSON-RPC 2.0 stdio transport.

In Attempt 6 (Final Polish Pass Authorized by Human Operator), resolve the final 3 architectural and backward-compatibility issues identified in `review-task-001_mcp_server_foundation.md`:
1. Cross-Frame State & Occlusion Parity: For ref-targeted actions in `src/browser_core/mcp_server.py`, evaluate revision and occlusion using `(frame if frame else page).evaluate(...)` so iframe elements return the target frame's authoritative revision and epoch.
2. Authentic Chrome CDP Context Resolution: In `src/browser_core/page_manager.py`, resolve `self.browser_context_id` by inspecting active contexts and querying genuine Chrome CDP `browserContextId` via `Target.getTargetInfo` through `ctx.new_cdp_session(page)`, eliminating synthetic attribute dependence.
3. Formal Invariant 3 Wire Modeling: In `src/browser_core/mcp_server.py`, model `_wire_size()` using standard MCP v2 JSON-RPC response envelopes with 36-char UUID request IDs (`"00000000-0000-0000-0000-000000000000"`), `resultType: "tool_result"`, and `isError: False`, guaranteeing `_wire_size(payload) <= 15360` bytes. Formally document the invariant scope.
4. Iframe Action Test Coverage: In `tests/test_mcp_server.py`, author a test verifying ref-based hover/scroll targeting an iframe element, and update multi-context tests to use genuine CDP context resolution with zero monkey-patching.

## Scope

### Leased Files & Paths:
1. `src/browser_core/mcp_server.py`:
   - In `_op_act`:
     - When `ref` is provided, resolve `frame = manager._frame_for_token(page, dom_ref.frame)`.
     - Target evaluation context: `target_eval = frame if frame is not None else page`.
     - Occlusion check: execute `target_eval.evaluate("el => window[Symbol.for('__OMNI_DOM_AGENT__')]?.isOccluded?.(el)", handle)`.
     - Hover/Scroll revision: execute `int(target_eval.evaluate("() => window[Symbol.for('__OMNI_DOM_AGENT__')]?.revision || 1"))`.
   - In `_wire_size`:
     - Update simulated envelope to include MCP v2 `resultType: "tool_result"` and 36-char UUID request ID (`"00000000-0000-0000-0000-000000000000"`):
       ```python
       wrapper = {
           "jsonrpc": "2.0",
           "id": "00000000-0000-0000-0000-000000000000",
           "result": {
               "resultType": "tool_result",
               "content": [{"type": "text", "text": raw_text}],
               "isError": False,
           },
       }
       ```
     - Enforce `_wire_size(payload) <= 15360` bytes.
     - Document that Invariant 3 bounds server-controlled `CallToolResult` payloads and standard JSON-RPC 2.0 stdio framing.

2. `src/browser_core/page_manager.py`:
   - In `PageManager.connect()`:
     - When `self.browser_context_id` is supplied:
       - Iterate over `self.browser.contexts`.
       - First check if `getattr(ctx, "_browser_context_id", None) == self.browser_context_id`.
       - If not yet matched, for contexts with active pages (`ctx.pages`), attach a temporary CDP session (`session = ctx.new_cdp_session(p)`) and query `Target.getTargetInfo` to extract `info.get("targetInfo", {}).get("browserContextId")`.
       - If `browserContextId == self.browser_context_id`, tag `ctx._browser_context_id = self.browser_context_id`, assign `self.context = ctx`, and break.
       - If not found across all contexts, fail closed with `RuntimeError(f"Leased browser context '{self.browser_context_id}' not found in active contexts")`.

3. `tests/test_mcp_server.py`:
   - In `test_page_manager_connect_positive_multi_context`:
     - Remove monkey-patching (`ctx_b._browser_context_id = ...`).
     - Obtain real CDP `browserContextId` by creating a page in `ctx_b`, attaching `session = ctx_b.new_cdp_session(page_b)`, sending `Target.getTargetInfo`, and reading `info["targetInfo"]["browserContextId"]`.
     - Connect `pm_leased` with `browser_context_id=real_cdp_context_id`, and assert `pm_leased.context is ctx_b`.
   - Add `test_mcp_server_act_iframe_ref_hover_scroll`:
     - Load a page with an iframe fixture (e.g. `nested_frames.html` or similar), observe DOM to get a ref inside the iframe, perform hover/scroll, and assert `state_delta` matches the iframe's epoch and revision.
   - Update wire budget test assertions to verify envelope with UUID request ID $\le 15,360$ bytes.

## Acceptance Criteria

1. **Cross-Frame State & Occlusion Parity**:
   - Ref-targeted actions inside iframes query revision and occlusion in the target frame context (`target_eval = frame if frame is not None else page`), returning consistent `document_epoch` and `revision`.
2. **Authentic CDP Context Resolution**:
   - `PageManager.connect()` resolves leased browser contexts via real Chrome CDP `Target.getTargetInfo` without relying on synthetic monkey-patching.
   - Fail-closed behavior on missing or invalid context IDs remains strictly enforced.
3. **Formal Invariant 3 Wire Modeling**:
   - `_wire_size()` models standard MCP v2 JSON-RPC envelopes with 36-char UUID request IDs and `resultType: "tool_result"`, guaranteeing that the outer wire packet is $\le 15$ KB (15,360 bytes).
4. **Comprehensive Regression Passing**:
   - `pytest tests/test_mcp_server.py -v` passes 100% including new iframe ref action and authentic CDP context tests.
   - `pytest tests/ -v` passes 100% across the entire repository (147+ tests, 0 failures).
   - Zero live profile pollution: tests never connect to port 17082.
5. **Clean Handoff**:
   - Result record updated and committed.
   - `python3 scripts/lint_communication_records.py` passes with exit code 0.

## Validation

1. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-001_mcp_server_foundation.md` exits 0.
2. `pytest tests/test_mcp_server.py -v` passes 100%.
3. `pytest tests/test_cli_legacy.py -v` passes 100%.
4. Full regression: `pytest tests/ -v` exits 0.
