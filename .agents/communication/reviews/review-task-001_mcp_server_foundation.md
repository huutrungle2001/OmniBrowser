# Review: review-task-001_mcp_server_foundation

RECORD_TYPE: REVIEW
RECORD_ID: review-task-001_mcp_server_foundation
STATUS: REVISION_REQUIRED
ATTEMPT: 1
CREATED_AT: 2026-10-05T23:05:00Z
UPDATED_AT: 2026-10-05T23:05:00Z
FROM: reviewer
TO: orchestrator, implementer
BASE_COMMIT: e16ea3956f7f75cb1a758b37f9991f043289a901
REVIEWED_COMMIT: 63574b0dfd54d27b9e2521c28925c2612f7571a9

## Scope Audited

- Reviewed Commit: `63574b0dfd54d27b9e2521c28925c2612f7571a9`
- Base Commit: `e16ea3956f7f75cb1a758b37f9991f043289a901`
- Inspected Files:
  - `src/browser_core/mcp_server.py`
  - `scripts/cdp_controller.py`
  - `scripts/dom_agent.js`
  - `src/browser_core/contracts.py`
  - `src/browser_core/page_manager.py`
  - `src/browser_core/primitives.py`
  - `src/browser_core/recipes.py`
  - `tests/test_mcp_server.py`
- Reviewer Engine: ChatGPT Web (GPT-5.6 / GPT-6 Sol Thinking High) via Chrome CDP session `omni_review` (`https://chatgpt.com/c/6ac41d2a-57b8-83eb-83b0-7cafff9379fe`)

## Findings

Adversarial audit completed via ChatGPT Web Oracle. While the core threading architecture (`BrowserWorker` pinning Playwright to a single dedicated thread) is fundamentally sound, **REVISION IS REQUIRED** due to:
1. Non-guaranteed 15KB hard budget bound on edge cases,
2. Broken Lease isolation on the MCP path,
3. MCP specification compliance and error semantics gaps (`isError` flag),
4. Legacy contract discrepancies.

---

### 1. MCP Specification Compliance — CHANGES REQUESTED
- **Invalid Low-Level SDK Fallback**:
  `src/browser_core/mcp_server.py` attempts to fallback to `from mcp.server import Server as MCPServer`. Low-level `Server` is a fundamentally different API that does not provide `@server.tool(...)`. The high-level `MCPServer` / `FastMCP` decorator interface cannot run on raw `Server`. This fallback advertises compatibility it cannot fulfill; it should be removed or properly implemented.
- **Tool Execution Failure Semantics**:
  Failed tool executions (e.g., occluded click, recipe lookup/execution errors) return `{"ok": false, ...}` rather than raising a tool error or using MCP `isError=true` semantics. Clients relying on `CallToolResult.isError` will treat failures as successful tool calls.
- **Modern 2026-07-28 MCP Protocol Handshake**:
  `tests/test_mcp_server.py` exercises the legacy `session.initialize()` flow. A test should also verify the modern stateless connection path.

---

### 2. Asyncio / Playwright Event Loop Deadlock Safety — PASS (Hardening Recommended)
- **Verdict**: **PASS**.
  The dedicated `ThreadPoolExecutor(max_workers=1)` pattern properly preserves Playwright greenlet thread affinity. MCP AnyIO worker thread dispatches to `BrowserWorker`, avoiding asyncio ↔ Playwright deadlocks.
- **Hardening Recommendations**:
  - Make `install_scanner()` idempotent to avoid accumulating duplicate CDP sessions and `framenavigated` callbacks across navigations.
  - Add concurrency / overlapping tool call regression tests to verify queueing behavior under load.

---

### 3. <=15KB Budget & Backward Compatibility — CHANGES REQUESTED
- **15KB Hard Bound Violation on Long Metadata**:
  The truncation loop in `src/browser_core/mcp_server.py` only runs while `len(tree_nodes) > 1`. If `page_url` or non-tree metadata is unusually long, or when `len(tree_nodes) <= 1`, an oversized payload will escape without truncation. An unconditional hard postcondition must enforce `serialized_size <= MAX_OBSERVE_BUDGET_BYTES` (including metadata truncation if necessary).
- **Lease Isolation Regression**:
  CLI forwards `--lease` and `--fencing-token` to `run_mcp_server()`, but `BrowserWorker.get_manager()` ignores both: it connects to the raw CDP URL without obtaining the leased session or scoping to `browser_context_id`. Invariant 8 ("Lease Ownership Invariant") is violated on the MCP entry point.
- **Result Contract Parity**:
  - `ObserveResult`: `version` and `suggested_recipes` are omitted in MCP `browser_observe`.
  - `ActResult`: `state_delta` and `elapsed_ms` are omitted in MCP `browser_act`.
  - Primitive arguments: `inspect` omits `ref` and `depth`; `run_code` omits `timeout_ms`.

## Independent Verification

| Check | Command Executed by Reviewer | Exit Code | Outcome |
| :--- | :--- | :---: | :--- |
| MCP Test Suite | `pytest tests/test_mcp_server.py -v` | 0 | 5/5 PASSED |
| Legacy CLI Suite | `pytest tests/test_cli_legacy.py -v` | 0 | 9/9 PASSED |
| Whitespace Hygiene | `git diff --check e16ea39..63574b0` | 0 | 0 warnings (Clean) |
| ChatGPT Web Audit | `./scripts/oracle extract --session omni_review` | 0 | Detailed 10KB Adversarial Audit Extracted |
| Schema Linter | `python3 scripts/lint_communication_records.py .agents/communication/reviews/review-task-001_mcp_server_foundation.md` | 0 | PASSED |

## Decision Rationale

Task `task-001_mcp_server_foundation` achieves solid architectural foundations with its thread-pinned browser worker and passing ephemeral tests. However, before merging into production, the security invariant for lease context isolation must be restored, the 15KB payload limit must be enforced as an absolute postcondition, and MCP tool failure semantics must adhere to standard `isError=true` reporting.

Status is set to **REVISION_REQUIRED** pending these specific remediations.