# Task: task-001_mcp_server_foundation

RECORD_TYPE: TASK
RECORD_ID: task-001_mcp_server_foundation
STATUS: TASK_READY
ATTEMPT: 2
CREATED_AT: 2026-10-05T22:40:00Z
UPDATED_AT: 2026-10-05T23:08:00Z
FROM: orchestrator
TO: implementer

## Objective

Transform OmniBrowser into a standard high-performance Model Context Protocol (MCP) server. Expose OmniBrowser's core capabilities (DOM observation, semantic action dispatch, execution recipes, tab isolation, and targeted visual inspection) over standard JSON-RPC 2.0 stdio transport.

In Attempt 2, address the critical adversarial review findings from `review-task-001_mcp_server_foundation.md` (Lease Context Isolation, Hard 15KB postcondition, MCP error semantics, fallback cleanup, contract field parity, and concurrency tests).

## Scope

### Leased Files & Paths:
1. `src/browser_core/mcp_server.py`:
   - High-level MCP server exposing stdio transport using `mcp.server.mcpserver.MCPServer` (MCP SDK 2.x) with clean FastMCP compatibility fallback. Remove invalid low-level `mcp.server.Server` fallback.
   - Enforce **Lease Context Isolation (Invariant 8)**: When `--lease` and `--fencing-token` are passed, `BrowserWorker.get_manager()` must resolve the leased target context via `LeaseManager` rather than connecting blindly to the global default CDP context.
   - Enforce **Unconditional Hard 15KB Postcondition (Invariant 3)**:
     Ensure serialized JSON payload of `browser_observe` never exceeds 15 KB (`assert len(encoded) <= MAX_OBSERVE_BUDGET_BYTES`), truncating metadata (`page_url`, frame tokens) if tree reduction alone is insufficient.
   - Enforce **MCP Tool Error Semantics**: Tool failures (occluded click, invalid ref, action timeout, recipe failure) must raise `ToolError` / set `is_error=True` on `CallToolResult` instead of returning error-shaped success dictionaries.
   - Restore **Contract Field Parity**:
     - `browser_observe`: Include `version` and `suggested_recipes`.
     - `browser_act`: Include `state_delta` and `elapsed_ms`.
     - `browser_inspect`: Support optional `ref` and `depth` parameters.
     - `browser_run_code`: Support optional `timeout_ms` parameter.
   - Expose the 8 core browser tools with complete signatures:
     1. `browser_navigate(url: str, tab_id: str | None = None)`
     2. `browser_observe(tab_id: str | None = None, match: str | None = None)`
     3. `browser_act(action: str, ref: str | None = None, value: str | None = None, tab_id: str | None = None)`
     4. `browser_screenshot(path: str | None = None, tab_id: str | None = None)`
     5. `browser_recipe_run(recipe_id: str, params: dict | None = None, tab_id: str | None = None)`
     6. `browser_run_code(code: str, mode: str = "read", timeout_ms: int = 5000, tab_id: str | None = None)`
     7. `browser_list_tabs()`
     8. `browser_inspect(mode: str = "dom", ref: str | None = None, depth: int = 3, tab_id: str | None = None)`

2. `scripts/cdp_controller.py`:
   - Maintain `--mcp` CLI argument with full lease and fencing token propagation.
   - Preserve 100% backward compatibility for all existing subcommands.

3. `scripts/dom_agent.js`:
   - Keep element occlusion checking with `document.elementFromPoint(cx, cy)` preventing clicks on covered elements.

4. `tests/test_mcp_server.py`:
   - Extend test suite with:
     - Boundary tests for 15KB hard postcondition (unusually long URL, single large element, multibyte UTF-8).
     - Test asserting `is_error=True` when a tool execution fails.
     - Concurrency test with overlapping tool calls verifying thread-safe execution in `BrowserWorker`.
     - Lease scoping test verifying leased context isolation.

## Acceptance Criteria

1. **Protocol Compliance & Error Semantics**:
   - MCP server strictly adheres to JSON-RPC 2.0 stdio protocol.
   - Failed tool executions set `is_error=True`.
   - Unsupported low-level Server fallback removed.
2. **Lease Isolation Invariant (Invariant 8)**:
   - When `--lease` is supplied, MCP tools operate within the leased target context.
3. **Hard 15KB Budget Postcondition (Invariant 3)**:
   - `browser_observe` output unconditionally guaranteed $\le 15$ KB JSON payload under all pathological inputs.
4. **Contract Parity**:
   - `browser_observe`, `browser_act`, `browser_inspect`, and `browser_run_code` include all expected schema fields and optional parameters.
5. **Full Regression Passing**:
   - `python3 -m pytest tests/ -v` passes 100% with exit code 0.
6. **Result Record & Notification**:
   - Author updated `.agents/communication/results/task-001_mcp_server_foundation.md`.
   - Notify `omni_reviewer` via `scripts/send_prompt.sh -s omni_reviewer`.

## Validation

1. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-001_mcp_server_foundation.md` exits 0.
2. `pytest tests/test_mcp_server.py -v` passes 100%.
3. `pytest tests/test_cli_legacy.py -v` passes 100%.
4. Full regression: `pytest tests/ -v` exits 0.
