# Task: task-001_mcp_server_foundation

RECORD_TYPE: TASK
RECORD_ID: task-001_mcp_server_foundation
STATUS: TASK_READY
ATTEMPT: 3
CREATED_AT: 2026-10-05T22:40:00Z
UPDATED_AT: 2026-10-05T23:25:00Z
FROM: orchestrator
TO: implementer

## Objective

Transform OmniBrowser into a standard high-performance Model Context Protocol (MCP) server. Expose OmniBrowser's core capabilities (DOM observation, semantic action dispatch, execution recipes, tab isolation, and targeted visual inspection) over standard JSON-RPC 2.0 stdio transport.

In Attempt 3, address the 4 specific adversarial review findings from Attempt 2 (`review-task-001_mcp_server_foundation.md`):
1. Backward compatibility: Restore historical `runBrowserCode` timeout defaults in `browser_run_code` (`timeout_ms: int | None = None`).
2. Contract parity: Populate `document_epoch`, `revision`, `state_delta`, and measured `elapsed_ms` for hover and window scroll in `_op_act`.
3. Invariant 8 & 10 (Lease Isolation): Fail closed in `PageManager.connect()` when `browser_context_id` is not found, rather than falling back to `contexts[0]`.
4. Invariant 3 (Wire-Level Budget): Set `structured_output=False` on `@server.tool` for `browser_observe` so the MCP SDK does not duplicate payload over JSON-RPC wire.

## Scope

### Leased Files & Paths:
1. `src/browser_core/mcp_server.py`:
   - `browser_run_code`: Change `timeout_ms: int = 5000` to `timeout_ms: int | None = None` in both the tool definition and `_op_run_code`. Forward `None` directly into `runBrowserCode(..., timeout_ms=timeout_ms)` so internal defaults (2,000 ms for read, 10,000 ms for write) are preserved.
   - `browser_act`: In `_op_act`, populate `document_epoch`, `revision`, `state_delta`, and measured `elapsed_ms` across all action branches:
     - For `hover`: Measure execution time, extract current page revision/document epoch from `manager`, and return complete `ActResult` dictionary.
     - For `scroll` with ref: Measure execution time, extract page revision/document epoch, and return complete `ActResult` dictionary.
     - For `scroll` window wheel (no ref): Measure execution time, extract page revision/document epoch, and return complete dictionary with `document_epoch`, `revision`, `state_delta: {}`, and `elapsed_ms`.
   - Wire-Level Budget (`structured_output`): Set `structured_output=False` on the `@server.tool` decorator for `browser_observe` (and optionally all browser tools) so `CallToolResult` emits only `content` (text) and keeps `structured_content=None`, avoiding wire payload doubling.
   - Standardize clean imports from `mcp.server.mcpserver` (`mcp>=2.0.0`).

2. `src/browser_core/page_manager.py`:
   - Fail closed on missing lease context: In `PageManager.connect()`, if `self.browser_context_id is not None`, locate the matching context in `self.browser.contexts`. If no match is found, raise `RuntimeError(f"Leased browser context '{self.browser_context_id}' not found in active contexts")` rather than falling back to `self.browser.contexts[0]`.

3. `tests/test_mcp_server.py`:
   - Extend test suite with:
     - Regression test verifying `PageManager.connect()` raises `RuntimeError` when given a non-existent `browser_context_id`.
     - Test verifying `browser_run_code` default `timeout_ms=None` propagates correctly to `runBrowserCode`.
     - Test verifying `browser_act` hover and window scroll return `document_epoch`, `revision`, `state_delta`, and `elapsed_ms`.
     - Test verifying `browser_observe` tool returns `structured_content=None` on `CallToolResult` and total payload $\le 15$ KB.

## Acceptance Criteria

1. **Fail-Closed Lease Isolation (Invariants 8 & 10)**:
   - `PageManager.connect()` fails closed with `RuntimeError` when an explicit `browser_context_id` cannot be found.
2. **Backward-Compatible Timeout**:
   - `browser_run_code(timeout_ms=None)` preserves `runBrowserCode` historical defaults (2,000 ms read / 10,000 ms write).
3. **Contract Parity on Hover and Window Scroll**:
   - `browser_act` returns `document_epoch`, `revision`, `state_delta`, and measured `elapsed_ms` across all action types.
4. **Wire-Level $\le 15\text{ KB}$ Budget (Invariant 3)**:
   - `browser_observe` specifies `structured_output=False` on tool registration; wire JSON payload strictly satisfies $\le 15$ KB limit.
5. **Full Regression Passing**:
   - `pytest tests/test_mcp_server.py -v` passes 100%.
   - Full regression suite `pytest tests/ -v` passes 100% with exit code 0.
6. **Result Record & Notification**:
   - Author updated `.agents/communication/results/task-001_mcp_server_foundation.md` for Attempt 3.
   - Notify `omni_reviewer` via `scripts/send_prompt.sh -s omni_reviewer`.

## Validation

1. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-001_mcp_server_foundation.md` exits 0.
2. `pytest tests/test_mcp_server.py -v` passes 100%.
3. `pytest tests/test_cli_legacy.py -v` passes 100%.
4. Full regression: `pytest tests/ -v` exits 0.
