# Task: task-001_mcp_server_foundation

RECORD_TYPE: TASK
RECORD_ID: task-001_mcp_server_foundation
STATUS: TASK_READY
ATTEMPT: 4
CREATED_AT: 2026-10-05T22:40:00Z
UPDATED_AT: 2026-10-06T00:57:00Z
FROM: orchestrator
TO: implementer

## Objective

Transform OmniBrowser into a standard high-performance Model Context Protocol (MCP) server over standard JSON-RPC 2.0 stdio transport.

In Attempt 4 (Authorized by Human Operator following Rule 5 Escalation), resolve the 4 specific architectural blockers identified in `review-task-001_mcp_server_foundation.md`:
1. `browser_act` Hover/Scroll Contract Parity & Revision Resolution: Eliminate the `state.revision` AttributeError in `_op_act` by resolving genuine page revision via `window[Symbol.for('__OMNI_DOM_AGENT__')]?.revision || 1` or `manager.observe()`, and populate `state_delta: {"revision": rev, "document_epoch": doc_epoch}` and `action_duration_ms: elapsed_ms` to achieve 100% `ActResult` contract parity.
2. Lease Context Lookup & Isolation: Support context matching via `_browser_context_id` on `BrowserContext` in `PageManager.connect()` with fail-closed semantics, and author positive multi-context isolation tests.
3. Total Wire-Level JSON-RPC Transport Budget: Enforce `MAX_OBSERVE_BUDGET_BYTES = 13500` (13.5 KB) in `src/browser_core/mcp_server.py` to ensure the complete JSON-RPC 2.0 wire response never exceeds 15 KB (15,360 bytes) including protocol framing and string escaping.
4. MCP Protocol Compliance: Standardize clean imports from `mcp.server.mcpserver` (`mcp>=2.0.0`), removing legacy v1 fallbacks that fail current 2026-07-28 protocol compliance.

## Scope

### Leased Files & Paths:
1. `src/browser_core/mcp_server.py`:
   - Fix `_op_act` hover and scroll branches:
     - Query in-browser revision via `page.evaluate("() => window[Symbol.for('__OMNI_DOM_AGENT__')]?.revision || 1")` to avoid AttributeError on `_FrameState`.
     - In the returned dictionary, populate `state_delta: {"revision": rev, "document_epoch": doc_epoch}`, `document_epoch: doc_epoch`, `revision: rev`, `elapsed_ms: elapsed_ms`, and `action_duration_ms: elapsed_ms`.
   - Update wire budget:
     - Change `MAX_OBSERVE_BUDGET_BYTES = 13500` (13.5 KB) in `mcp_server.py` to ensure the final serialized JSON-RPC 2.0 envelope strictly satisfies $\le 15$ KB (15,360 bytes).
   - Standardize on `from mcp.server.mcpserver import MCPServer` (`mcp>=2.0.0`) and clean exception handling.

2. `src/browser_core/page_manager.py`:
   - In `PageManager.connect()`, maintain fail-closed lease context matching via `getattr(ctx, "_browser_context_id", None) == self.browser_context_id`.

3. `tests/test_mcp_server.py`:
   - Extend test suite with:
     - Test verifying `browser_act` hover and scroll return genuine `revision`, `document_epoch`, and populated `state_delta: {"revision": ..., "document_epoch": ...}`.
     - Positive multi-context isolation test: Create two Playwright browser contexts, tag one with `_browser_context_id`, and verify `PageManager` selects the exact tagged context.
     - Total wire payload test: Verify serialized `CallToolResult` JSON string $\le 15$ KB (15,360 bytes).

## Acceptance Criteria

1. **Contract Parity on `browser_act`**:
   - Hover and scroll actions return `document_epoch`, `revision`, `state_delta: {"revision": rev, "document_epoch": doc_epoch}`, `elapsed_ms`, and `action_duration_ms` with zero swallowed AttributeErrors.
2. **Lease Context Isolation (Invariants 8 & 10)**:
   - `PageManager.connect()` successfully selects matching context when tagged with `_browser_context_id` and fails closed with `RuntimeError` when missing. Positive multi-context test passes.
3. **Wire-Level $\le 15\text{ KB}$ Budget (Invariant 3)**:
   - Total serialized JSON-RPC tool result wire payload strictly $\le 15$ KB (15,360 bytes) across all inputs.
4. **Full Regression Passing**:
   - `pytest tests/test_mcp_server.py -v` passes 100%.
   - `pytest tests/ -v` passes 100% across the entire repository (145+ tests, 0 failures).
5. **Result Record & Notification**:
   - Update `.agents/communication/results/task-001_mcp_server_foundation.md` for Attempt 4.
   - Lint communication records and notify `omni_reviewer`.

## Validation

1. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-001_mcp_server_foundation.md` exits 0.
2. `pytest tests/test_mcp_server.py -v` passes 100%.
3. `pytest tests/test_cli_legacy.py -v` passes 100%.
4. Full regression: `pytest tests/ -v` exits 0.
