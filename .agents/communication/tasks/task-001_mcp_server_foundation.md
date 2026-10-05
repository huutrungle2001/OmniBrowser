# Task: task-001_mcp_server_foundation

RECORD_TYPE: TASK
RECORD_ID: task-001_mcp_server_foundation
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-10-05T22:40:00Z
UPDATED_AT: 2026-10-05T22:40:00Z
FROM: orchestrator
TO: implementer

## Objective

Transform OmniBrowser into a standard high-performance Model Context Protocol (MCP) server. Expose OmniBrowser's core capabilities (DOM observation, semantic action dispatch, execution recipes, tab isolation, and targeted visual inspection) over standard JSON-RPC 2.0 stdio transport.

Additionally, resolve existing test failures to establish a 100% clean baseline, and enhance `dom_agent.js` with element occlusion checks inspired by `browser-use` (`document.elementFromPoint`) to prevent clicking covered elements.

## Scope

### Leased Files & Paths:
1. `src/browser_core/mcp_server.py`:
   - Implement MCP server exposing standard stdio transport using `mcp.server.mcpserver.MCPServer` (MCP SDK 2.x) with fallback/compatibility for FastMCP.
   - Expose the 8 core browser tools:
     1. `browser_navigate(url: str, tab_id: str | None = None)`: Navigate active or specified tab to URL.
     2. `browser_observe(tab_id: str | None = None, match: str | None = None)`: Extract compact semantic DOM tree ($\le 15$ KB budget) with opaque ref IDs.
     3. `browser_act(action: str, ref: str | None = None, value: str | None = None, tab_id: str | None = None)`: Execute native user action (`click`, `fill`, `select`, `press`, `scroll`, `hover`).
     4. `browser_screenshot(path: str | None = None, tab_id: str | None = None)`: Capture full or viewport screenshot.
     5. `browser_recipe_run(recipe_id: str, params: dict | None = None, tab_id: str | None = None)`: Execute parameterized procedural workflow recipe.
     6. `browser_run_code(code: str, mode: str = "read", tab_id: str | None = None)`: Bounded browser JS evaluation with strict output constraints.
     7. `browser_list_tabs()`: List all open tabs and active targets.
     8. `browser_inspect(mode: str = "dom", tab_id: str | None = None)`: Inspect accessibility tree, frame hierarchy, or DOM bounds.

2. `scripts/cdp_controller.py`:
   - Add `--mcp` CLI argument to launch the stdio MCP server (`python scripts/cdp_controller.py --mcp`).
   - Preserve 100% backward compatibility for all existing subcommands (`list-tabs`, `goto`, `eval`, `screenshot`, `observe`, `act`, `recipe`, `workflow`).

3. `scripts/dom_agent.js`:
   - Port occlusion & elementFromPoint checking from `browser-use`:
     - Verify element visibility and determine if element center `(x, y)` is occluded by overlays/modals using `document.elementFromPoint(x, y)`.
     - Flag occluded elements or adjust interaction target coordinates accordingly.

4. Baseline Test Fixes:
   - Fix `PYTHONPATH` / `sys.path` bootstrapping in `tests/test_concurrency_broker.py` so tests can run without manual `PYTHONPATH=.`.
   - Fix unsupported recipe action `'layout'` in `recipes/app.powerbi.com/powerbi_web_upload_and_build.json` and `src/browser_core/recipes.py` (`valid_actions`). Ensure `tests/test_recipes.py` passes 100%.

5. `tests/test_mcp_server.py`:
   - Author comprehensive end-to-end MCP client test suite testing tool discovery, call execution of all 8 tools, error reporting, and tab lifecycle.

## Acceptance Criteria

1. **Protocol Compliance**:
   - MCP server strictly adheres to Model Context Protocol specification (JSON-RPC 2.0 stdio).
   - FastMCP / MCPServer exposes tools with clear descriptions, typed schema arguments, and structured return formats.
2. **Zero Live Profile Pollution (CRITICAL INVARIANT)**:
   - Tests and MCP server in test mode must NEVER touch `~/.chrome-ai-profile` on port 17082. All test executions use ephemeral isolated profiles.
3. **Token & Payload Budget Constraint**:
   - `browser_observe` output strictly limits semantic DOM tree to $\le 15$ KB JSON payload.
4. **Occlusion Detection**:
   - `dom_agent.js` accurately identifies elements covered by higher z-index layers using `elementFromPoint`.
5. **Full Test Suite Passing**:
   - `python3 -m pytest tests/ -v` exits with code 0 (100% passing across all unit, integration, and MCP tests).
6. **Result Record & Notification**:
   - Author `.agents/communication/results/task-001_mcp_server_foundation.md`.
   - Notify `omni_reviewer` via `scripts/send_prompt.sh -s omni_reviewer`.

## Validation

1. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-001_mcp_server_foundation.md` exits 0.
2. `pytest tests/test_recipes.py` passes with 0 failures.
3. `pytest tests/test_concurrency_broker.py` passes with 0 failures.
4. `pytest tests/test_mcp_server.py` passes with 0 failures.
5. Full regression: `pytest tests/ -v` passes 100%.
