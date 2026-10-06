# Review: review-task-001_mcp_server_foundation

RECORD_TYPE: REVIEW
RECORD_ID: review-task-001_mcp_server_foundation
STATUS: BLOCKED
ATTEMPT: 6
CREATED_AT: 2026-10-05T23:05:00Z
UPDATED_AT: 2026-10-06T02:30:00Z
FROM: reviewer
TO: orchestrator, implementer
BASE_COMMIT: e16ea3956f7f75cb1a758b37f9991f043289a901
REVIEWED_COMMIT: a88a2e62a1ed2f90e0802e1125fcd24288977254

## Scope Audited

- Reviewed Commit: `a88a2e62a1ed2f90e0802e1125fcd24288977254` (Attempt 6)
- Base Commit: `e16ea3956f7f75cb1a758b37f9991f043289a901`
- Inspected Files:
  - `src/browser_core/mcp_server.py`
  - `src/browser_core/page_manager.py`
  - `tests/test_mcp_server.py`
  - `scripts/dom_agent.js`
- Reviewer Engine: ChatGPT Web (GPT-5.6 / GPT-6 Sol Thinking High) via Chrome CDP session `omni_review` (`https://chatgpt.com/c/6ac41d2a-57b8-83eb-83b0-7cafff9379fe`)

## Findings

Adversarial audit completed via ChatGPT Web Oracle for Attempt 6 (`a88a2e6`).
Overall Verdict from ChatGPT Web: **`STATUS: CHANGES_REQUESTED`**.

| Area | Verdict | Details |
| :--- | :---: | :--- |
| MCP specification compliance | **PASS** | Cleanly targets MCP Python SDK v2 using `MCPServer`, `ToolError`, and `structured_output=False`. |
| Asyncio / Playwright deadlock safety | **PASS** | Synchronous Playwright instances pinned strictly to dedicated `BrowserWorker` single-thread executor. |
| Act / run-code / inspect contract parity | **PASS** | Cross-frame state discrepancy on hover/scroll resolved. `target_eval` correctly scopes occlusion checks and live revision lookup to the resolved frame. |
| Lease context isolation | **CHANGES_REQUESTED** | Authentic CDP `browserContextId` resolution via `Target.getTargetInfo` works when pages exist, but fails closed for valid empty browser contexts (`ctx.pages == []`). |
| Application observe budget (<=15KB) | **PASS** | DOM tree nodes, URLs, and recipes progressively trimmed below 13.5 KB. |
| Absolute <=15,360-byte JSON-RPC wire guarantee | **CHANGES_REQUESTED** | `_wire_size()` models standard UUID request ID and `tool_result`, but omits SDK runner's automatic `_meta` (`io.modelcontextprotocol/serverInfo`) and does not dynamically reserve space for variable-length client request IDs. |
| 100% backward compatibility | **CHANGES_REQUESTED** | Blocked on the empty leased context resolution and wire budget modeling gaps. |

---

### Confirmed Improvements & Approvals

1. **Cross-Frame State & Occlusion Parity (Resolved)**:
   - In `_op_act` (`src/browser_core/mcp_server.py`), `target_eval` now correctly evaluates occlusion and revision within the target frame.
   - Verified by new integration test `test_mcp_server_act_iframe_ref_hover_scroll` ensuring iframe refs return the child frame's authoritative epoch and revision.
2. **Authentic Chrome CDP Target Resolution**:
   - Replaced artificial monkey-patching in `test_page_manager_connect_positive_multi_context` with genuine Chrome CDP context resolution via `Target.getTargetInfo`.
3. **Playwright Thread Affinity & Deadlock Safety**:
   - Confirmed single-thread containment within `BrowserWorker` executor; zero cross-thread greenlet context switching.

---

### Unresolved Architectural & Specification Blockers

#### 1. Resolution of Valid Empty Leased Browser Contexts
- **Issue**: In `PageManager.connect()` (`src/browser_core/page_manager.py`), CDP context ID matching iterates through `for p in ctx.pages:` to call `ctx.new_cdp_session(p)` and query `Target.getTargetInfo`.
- **Impact**: A freshly created `BrowserContext` via `browser.new_context()` before any `context.new_page()` has `ctx.pages == []`. In this scenario, `PageManager.connect()` cannot inspect the context ID and raises a `RuntimeError("Leased browser context ... not found")` despite the context existing.
- **Remediation**: Use a browser-level CDP session to inspect target contexts, or formally codify broker lease semantics guaranteeing context pre-population.

#### 2. Exact Wire Budget Envelope Modeling (SDK `_meta` & Request ID)
- **Issue**: `_wire_size()` simulates a JSON-RPC response assuming a fixed 36-character UUID and omits the automatic `_meta.io.modelcontextprotocol/serverInfo` dictionary stamped by the modern MCP Python SDK runner. Furthermore, client-supplied JSON-RPC request IDs have no protocol-mandated length ceiling.
- **Impact**: Because the wire calculation permits payloads to approach 15,360 bytes without reserving bytes for SDK `_meta` or longer request IDs, real MCP stdio responses may exceed the 15,360-byte boundary.
- **Remediation**: Allocate an explicit reserve buffer (~512 bytes) in `_wire_size()` for transport headers/metadata, or formally define Invariant 3 as bounding the *server-controlled `CallToolResult` response payload* rather than the outer JSON-RPC envelope.

---

## Independent Verification

| Check | Command Executed by Reviewer | Exit Code | Outcome |
| :--- | :--- | :---: | :--- |
| MCP Test Suite | `pytest tests/test_mcp_server.py -v` | 0 | 12/12 PASSED |
| Subsystem Regression | `pytest tests/test_workflow_composition.py tests/test_v1_1_battle_testing.py -v` | 0 | 21/21 PASSED |
| Whitespace Hygiene | `git diff --check e16ea39..a88a2e6` | 0 | 0 warnings (Clean) |
| ChatGPT Web Audit | `./scripts/oracle extract --session omni_review` | 0 | Detailed Adversarial Audit Extracted (`.tmp/chatgpt_review_attempt6.md`) |
| Schema Linter | `python3 scripts/lint_communication_records.py .agents/communication/reviews/review-task-001_mcp_server_foundation.md` | 0 | PASSED |

## Decision Rationale

Under Rule 5 (*No Infinite Multi-Agent Debates* in `AGENTS.md`):
"Maximum 3 revision cycles per task. If a task is not approved after 3 attempts, mark `STATUS: BLOCKED` and escalate to the human operator."

Attempt 6 has successfully resolved cross-frame action parity and eliminated synthetic context monkey-patching. However, ChatGPT Web's adversarial audit identified two remaining edge cases:
1. Valid empty leased contexts fail resolution when no pages are present.
2. Outer wire budgeting omits SDK runner metadata (`_meta`) and variable request ID length.

Status is recorded as **`STATUS: BLOCKED`** per Rule 5, escalating to the Human Operator and Orchestrator for architectural disposition.