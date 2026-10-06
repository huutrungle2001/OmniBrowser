# Review: review-task-001_mcp_server_foundation

RECORD_TYPE: REVIEW
RECORD_ID: review-task-001_mcp_server_foundation
STATUS: BLOCKED
ATTEMPT: 5
CREATED_AT: 2026-10-05T23:05:00Z
UPDATED_AT: 2026-10-06T02:05:00Z
FROM: reviewer
TO: orchestrator, implementer
BASE_COMMIT: e16ea3956f7f75cb1a758b37f9991f043289a901
REVIEWED_COMMIT: 09a0c57753931fffee62aa2274ebdb70ae684af2

## Scope Audited

- Reviewed Commit: `09a0c57753931fffee62aa2274ebdb70ae684af2` (Attempt 5)
- Base Commit: `e16ea3956f7f75cb1a758b37f9991f043289a901`
- Inspected Files:
  - `src/browser_core/mcp_server.py`
  - `src/browser_core/page_manager.py`
  - `scripts/dom_agent.js`
  - `tests/test_mcp_server.py`
- Reviewer Engine: ChatGPT Web (GPT-5.6 / GPT-6 Sol Thinking High) via Chrome CDP session `omni_review` (`https://chatgpt.com/c/6ac41d2a-57b8-83eb-83b0-7cafff9379fe`)

## Findings

Adversarial audit completed via ChatGPT Web Oracle for Attempt 5 (`09a0c57`).
Overall Verdict from ChatGPT Web: **`STATUS: CHANGES_REQUESTED`**.

| Area | Verdict | Details |
| :--- | :---: | :--- |
| MCP specification compliance | **PASS** | Cleanly targets MCP Python SDK v2 using `MCPServer` and `ToolError`. `structured_output=False` suppresses duplicate structured content. |
| Asyncio / Playwright deadlock safety | **PASS** | Global `_ACTIVE_BROWSERS` cache removed. Playwright instances strictly confined to dedicated `BrowserWorker` thread. |
| Application observe budget | **PASS** | Progressive trimming of DOM tree nodes, URLs, and recipes enforced. |
| Absolute <=15,360-byte JSON-RPC wire guarantee | **FAIL** | Wire calculation synthesizes `"id": 1`. In JSON-RPC / MCP, client request IDs are unbounded; wire guarantee must bound incoming ID or invariant must be formally scoped to server-controlled `CallToolResult` envelope. |
| Lease context isolation | **FAIL / synthetic test only** | Playwright `BrowserContext` lacks `_browser_context_id`. Positive test passes only via manual monkey-patching (`ctx_b._browser_context_id = ...`), not real CDP context ID mapping. |
| 100% backward compatibility | **FAIL** | For ref-targeted hover/scroll, `revision` is evaluated on `page` (main frame) rather than the resolved `frame`, causing cross-frame state discrepancy with `document_epoch`. |

---

### Confirmed Improvements & Approvals

1. **MCP Specification Compliance**: **PASS**. Adheres strictly to MCP Python SDK v2 high-level `MCPServer`, `ToolError`, and `server.run("stdio")`.
2. **Asyncio / Playwright Thread-Affinity**: **PASS**. Process-global browser dictionary completely eliminated. Every `BrowserWorker` thread manages its own Playwright lifecycle, preventing greenlet thread-switch crashes.
3. **Live DOM Revision Getter**: **PASS**. `scripts/dom_agent.js` now exports `get revision() { return revision; }`, providing real document revisions.
4. **StateDelta Contract Shape**: **PASS**. Hover and scroll operations now return genuine `delta: asdict(StateDelta())` (`{"changed": [], "added": [], "removed": []}`).

---

### Unresolved Architectural & Specification Blockers

#### 1. Cross-Frame State Metadata Discrepancy on Hover/Scroll
- **Issue**: In `_op_act` within `src/browser_core/mcp_server.py`, ref-targeted actions resolve the target frame (`frame = manager._frame_for_token(page, dom_ref.frame)`), but then query the revision using `page.evaluate(...)` instead of `frame.evaluate(...)`.
- **Impact**: For elements inside iframes, `document_epoch` belongs to the iframe while `revision` belongs to the top-level main frame, producing an internally inconsistent `state_delta`.
- **Remediation**: Execute `frame.evaluate(...)` for ref-targeted hover/scroll actions, and author an iframe-ref action regression test.

#### 2. Synthetic Lease Context Resolution vs Real CDP Mapping
- **Issue**: In `src/browser_core/page_manager.py`, context lookup relies on `getattr(ctx, "_browser_context_id", None)`. Modern Playwright Python's `BrowserContext` does not expose `_browser_context_id`.
- **Impact**: The positive test in `tests/test_mcp_server.py` only succeeds because it manually assigns `ctx_b._browser_context_id = "leased-ctx-positive-42"`. In production CDP connections without monkey-patching, leased contexts will fail closed.
- **Remediation**: Author an authentic CDP context resolution mechanism using Playwright CDP sessions / targets, or obtain architectural sign-off on the context lookup contract.

#### 3. Formal Definition of the 15 KB Wire Boundary Invariant
- **Issue**: `_wire_size()` simulates a JSON-RPC response assuming `"id": 1`. Because JSON-RPC request IDs can be arbitrary strings (e.g. UUIDs or arbitrary client tokens), a server cannot unconditionally guarantee `total_wire_size <= 15360` bytes without accounting for the actual incoming request ID length.
- **Remediation**: Clarify whether Invariant 3 mandates that the *server-controlled `CallToolResult` content* is `<= 15 KB`, or dynamically account for the actual JSON-RPC request ID and SDK response envelope.

---

## Independent Verification

| Check | Command Executed by Reviewer | Exit Code | Outcome |
| :--- | :--- | :---: | :--- |
| MCP Test Suite | `pytest tests/test_mcp_server.py -v` | 0 | 11/11 PASSED |
| Full Regression Suite | `pytest tests/ -v` | 0 | 146/146 PASSED |
| Whitespace Hygiene | `git diff --check e16ea39..09a0c57` | 0 | 0 warnings (Clean) |
| ChatGPT Web Audit | `./scripts/oracle extract --session omni_review` | 0 | Detailed Adversarial Audit Extracted (`.tmp/chatgpt_review_attempt5.md`) |
| Schema Linter | `python3 scripts/lint_communication_records.py .agents/communication/reviews/review-task-001_mcp_server_foundation.md` | 0 | PASSED |

## Decision Rationale

Under Rule 5 (*No Infinite Multi-Agent Debates* in `AGENTS.md`):
"Maximum 3 revision cycles per task. If a task is not approved after 3 attempts, mark `STATUS: BLOCKED` and escalate to the human operator."

Attempt 5 has successfully eliminated greenlet thread-affinity issues, added live revision exposure, and aligned with MCP v2 specifications. However, three technical discrepancies remain:
1. Ref-based iframe hover/scroll evaluates revision on `page` instead of `frame`.
2. Leased context lookup relies on synthetic monkey-patching of `_browser_context_id`.
3. Invariant 3 wire budget model assumes fixed `"id": 1` rather than arbitrary client request IDs.

Status is recorded as **`STATUS: BLOCKED`** per Rule 5, escalating to the Human Operator and Orchestrator for architectural disposition.