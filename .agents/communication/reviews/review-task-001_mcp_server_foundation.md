# Review: review-task-001_mcp_server_foundation

RECORD_TYPE: REVIEW
RECORD_ID: review-task-001_mcp_server_foundation
STATUS: BLOCKED
ATTEMPT: 4
CREATED_AT: 2026-10-05T23:05:00Z
UPDATED_AT: 2026-10-06T01:15:00Z
FROM: reviewer
TO: orchestrator, implementer
BASE_COMMIT: e16ea3956f7f75cb1a758b37f9991f043289a901
REVIEWED_COMMIT: 38f9d961b7941a5457b424d8c1dea7502c5c3ebe

## Scope Audited

- Reviewed Commit: `38f9d961b7941a5457b424d8c1dea7502c5c3ebe` (Attempt 4)
- Base Commit: `e16ea3956f7f75cb1a758b37f9991f043289a901`
- Inspected Files:
  - `src/browser_core/mcp_server.py`
  - `src/browser_core/page_manager.py`
  - `tests/test_mcp_server.py`
  - `scripts/cdp_controller.py`
  - `src/browser_core/contracts.py`
- Reviewer Engine: ChatGPT Web (GPT-5.6 / GPT-6 Sol Thinking High) via Chrome CDP session `omni_review` (`https://chatgpt.com/c/6ac41d2a-57b8-83eb-83b0-7cafff9379fe`)

## Findings

Adversarial audit completed via ChatGPT Web Oracle for Attempt 4.

### Confirmed Improvements & Approvals
1. **MCP Specification Compliance**: **PASS**. Standardized strictly on high-level `MCPServer` and `ToolError` from `mcp.server.mcpserver` (`mcp>=2.0.0`), with `structured_output=False` cleanly disabling automatic duplicate structured content.
2. **Primitive Timeout Handling**: **PASS**. `timeout_ms: int | None = None` forwarded cleanly to preserve historical defaults.
3. **Application-Level Truncation**: **PASS**. Heuristic inner content budget trimmed to 13.5 KB.

---

### Unresolved Architectural Blockers & Regressions

#### 1. Greenlet Thread-Affinity Regression via `_ACTIVE_BROWSERS`
- **Issue**: Attempt 4 introduced a process-global dictionary `_ACTIVE_BROWSERS: dict[str, Browser]` in `src/browser_core/page_manager.py` to share browser instances across `PageManager` instances.
- **Violation**: Synchronous Playwright is explicitly not thread-safe. Reusing `Browser` and `BrowserContext` objects created on one worker thread from another worker thread violates Playwright's greenlet affinity and triggers fatal `greenlet.error: cannot switch to a different thread` crashes under concurrent execution.
- **Remediation**: Remove the process-global `_ACTIVE_BROWSERS` cache. Keep every synchronous Playwright instance confined strictly to the dedicated `BrowserWorker` thread that initialized it.

#### 2. Live DOM Revision Resolution & Delta Contract Shape
- **Issue**: In `_op_act`, Attempt 4 evaluates `() => window[Symbol.for('__OMNI_DOM_AGENT__')]?.revision || 1`. The DOM agent object (`dom_agent.js`) has no `.revision` property; revision is produced during `scan()`. Consequently, the expression evaluates to `undefined` and always falls back to `1`.
- **Issue**: The hover and window scroll paths return `"delta": {}` instead of the established `StateDelta` contract shape (`{"changed": [], "added": [], "removed": []}`).
- **Remediation**: Expose an explicit revision getter on `agent` in `dom_agent.js` or retrieve it via `agent.scan(0).revision` on the target frame. Return genuine `StateDelta` shape for `delta`.

#### 3. Synthetic Lease Context Test vs Real CDP Resolution
- **Issue**: The positive isolation test in `tests/test_mcp_server.py` monkey-patches `ctx_b._browser_context_id = "leased-ctx-positive-42"`. In production Chrome CDP, `browser_context_id` is an external hexadecimal string emitted by Chrome's target domain, which Playwright's Python `BrowserContext` wrapper does not expose as a public attribute.
- **Remediation**: Author a realistic positive test that resolves real CDP context IDs, or implement genuine CDP context mapping without relying on artificial monkey-patching.

#### 4. Wire-Level JSON Escaping Expansion ($\le 15$ KB Bound)
- **Issue**: Setting `MAX_OBSERVE_BUDGET_BYTES = 13500` is a heuristic. When text containing quotes or backslashes is serialized inside a JSON string inside a JSON-RPC response wrapper, escaping can expand 12 KB to >18 KB.
- **Remediation**: Enforce the hard postcondition loop against the actual serialized outer JSON string: `len(json.dumps(wrapper).encode('utf-8')) <= 15360`.

---

## Independent Verification

| Check | Command Executed by Reviewer | Exit Code | Outcome |
| :--- | :--- | :---: | :--- |
| MCP Test Suite | `pytest tests/test_mcp_server.py -v` | 0 | 11/11 PASSED |
| Full Regression Suite | `pytest tests/ -v` | 0 | 146/146 PASSED |
| Whitespace Hygiene | `git diff --check e16ea39..38f9d96` | 0 | 0 warnings (Clean) |
| ChatGPT Web Audit | `./scripts/oracle extract --session omni_review` | 0 | Detailed Adversarial Audit Extracted |
| Schema Linter | `python3 scripts/lint_communication_records.py .agents/communication/reviews/review-task-001_mcp_server_foundation.md` | 0 | PASSED |

## Decision Rationale

Under Rule 5 (*No Infinite Multi-Agent Debates* in `AGENTS.md`):
Attempt 4 resolves MCP v2 imports and timeout parity, but introduces a thread-affinity regression (`_ACTIVE_BROWSERS`), relies on an unexposed `.revision` property defaulting to 1, and relies on heuristic rather than serialized wire-level budget bounding.

Status remains **`STATUS: BLOCKED`** pending human operator / orchestrator direction.