# Task: task-001_mcp_server_foundation

RECORD_TYPE: TASK
RECORD_ID: task-001_mcp_server_foundation
STATUS: TASK_READY
ATTEMPT: 8
CREATED_AT: 2026-10-05T22:40:00Z
UPDATED_AT: 2026-10-06T02:50:00Z
FROM: orchestrator
TO: implementer

## Objective

Transform OmniBrowser into a standard high-performance Model Context Protocol (MCP) server over standard JSON-RPC 2.0 stdio transport.

In Attempt 8 (Final Approval Pass Authorized by Human Operator), resolve the final 2 isolation and wire scoping items identified in `review-task-001_mcp_server_foundation.md` (commit `c77ea91`):
1. Zero Cross-Context Page Probing (Invariants 8 & 10): In `src/browser_core/page_manager.py`, completely eliminate candidate context probe page creation (`ctx.new_page()`). Instead, inspect existing pages in `ctx.pages` read-only via `Target.getTargetInfo`. If the requested context has no pages, use a browser-level CDP session (`self.browser.new_browser_cdp_session()`) to execute `Target.createTarget(url="about:blank", browserContextId=self.browser_context_id)` directly in Chrome. This creates a target exclusively in `self.browser_context_id` without touching, probing, or mutating any other context.
2. Formal Invariant 3 Wire Boundary Scoping: In `src/browser_core/mcp_server.py` and this specification, formally scope Invariant 3: The server-controlled `CallToolResult` response payload is strictly bounded to $\le 13.5$ KB (13,824 bytes) and guaranteed $\le 15,360$ bytes on the wire for all standard JSON-RPC 2.0 requests (with client request IDs up to 36-char UUIDs), including SDK runner `_meta.io.modelcontextprotocol/serverInfo` and escaping. For non-standard or unbounded client request IDs (> 36 chars), the server-controlled `CallToolResult` component remains bounded to $\le 14,848$ bytes, preventing LLM context explosion.
3. Strict Lease Isolation Test: In `tests/test_mcp_server.py`, ensure `test_page_manager_connect_empty_context` creates multiple empty contexts (e.g. `ctx_other` and `ctx_target`) and asserts that `ctx_other.pages` remains strictly empty (0 pages) throughout connection to `ctx_target`.

## Scope

### Leased Files & Paths:
1. `src/browser_core/page_manager.py`:
   - In `PageManager.connect()`:
     - When `self.browser_context_id` is supplied:
       - Iterate over `self.browser.contexts`:
         - If `getattr(ctx, "_browser_context_id", None) == self.browser_context_id`:
           `self.context = ctx; break`
         - If `ctx.pages`:
           Inspect `ctx.pages[0]` read-only via temporary CDP session (`session = ctx.new_cdp_session(ctx.pages[0])`). If `Target.getTargetInfo` matches `self.browser_context_id`, tag `ctx._browser_context_id = self.browser_context_id`, bind `self.context = ctx`, detach session, and break. Detach session in finally.
       - If not found and candidate contexts exist:
         - Do NOT call `ctx.new_page()` on candidate contexts.
         - Instead, open a browser-level CDP session:
           ```python
           try:
               b_session = self.browser.new_browser_cdp_session()
               try:
                   t_res = b_session.send(
                       "Target.createTarget",
                       {"url": "about:blank", "browserContextId": self.browser_context_id},
                   )
                   # Target was created exclusively in the requested browser context
                   # Find which context now contains the newly created target page
                   for ctx in self.browser.contexts:
                       for p in ctx.pages:
                           try:
                               p_session = ctx.new_cdp_session(p)
                               try:
                                   info = p_session.send("Target.getTargetInfo")
                                   if info.get("targetInfo", {}).get("browserContextId") == self.browser_context_id:
                                       ctx._browser_context_id = self.browser_context_id
                                       self.context = ctx
                                       break
                               finally:
                                   p_session.detach()
                           except Exception:
                               pass
                       if self.context:
                           break
               finally:
                   try:
                       b_session.detach()
                   except Exception:
                       pass
           except Exception:
               pass
           ```
       - If not found across all contexts, fail closed with `RuntimeError(f"Leased browser context '{self.browser_context_id}' not found in active contexts")`.

2. `src/browser_core/mcp_server.py`:
   - In `_wire_size(p)`:
     - Model standard MCP 2026-07-28 response envelope with `resultType: "complete"`, SDK runner `_meta`, and 36-char UUID request ID.
   - In `_is_over_budget(p)`:
     - Enforce `len(encoded) > MAX_OBSERVE_BUDGET_BYTES or _wire_size(p) > (15360 - 256)`.
     - Document that Invariant 3 bounds server-controlled `CallToolResult` payloads and standard JSON-RPC 2.0 stdio framing.

3. `tests/test_mcp_server.py`:
   - In `test_page_manager_connect_empty_context(ephemeral_cdp_url)`:
     - Create `ctx_other = pm_setup.browser.new_context()`.
     - Create `ctx_target = pm_setup.browser.new_context()`.
     - Obtain `target_ctx_id` from `ctx_target` via browser-level CDP session or temporary target.
     - Connect `pm_leased = PageManager(ephemeral_cdp_url, test_mode=True, browser=pm_setup.browser, browser_context_id=target_ctx_id).connect()`.
     - Assert `pm_leased.context is ctx_target`.
     - Assert `len(ctx_other.pages) == 0` (verifying zero cross-context mutation).

## Acceptance Criteria

1. **Zero Cross-Context Mutation (Invariants 8 & 10)**:
   - `PageManager.connect()` never calls `ctx.new_page()` on unrelated candidate contexts. Unrelated empty contexts remain strictly empty (`len(ctx.pages) == 0`).
   - Direct browser-level CDP target creation binds exclusively to the requested `browser_context_id`.
2. **Formal Invariant 3 Wire Boundary Scoping**:
   - `_wire_size()` models standard MCP 2026-07-28 response envelopes with `resultType: "complete"`, SDK `_meta.io.modelcontextprotocol/serverInfo`, and 36-char UUID request ID.
   - 256-byte transport reserve is enforced to guarantee outer wire limit for all standard client request IDs.
3. **Comprehensive Regression Passing**:
   - `pytest tests/test_mcp_server.py -v` passes 100% including isolation assertion (`len(ctx_other.pages) == 0`).
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
