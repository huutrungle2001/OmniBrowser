# Review: review-task-001_mcp_server_foundation

RECORD_TYPE: REVIEW
RECORD_ID: review-task-001_mcp_server_foundation
STATUS: BLOCKED
ATTEMPT: 7
CREATED_AT: 2026-10-05T23:05:00Z
UPDATED_AT: 2026-10-06T02:47:00Z
FROM: reviewer
TO: orchestrator, implementer
BASE_COMMIT: e16ea3956f7f75cb1a758b37f9991f043289a901
REVIEWED_COMMIT: 0c41a63b564ddc25099ae08abcdeb3a64af9a84d

## Scope Audited

- Reviewed Commit: `0c41a63b564ddc25099ae08abcdeb3a64af9a84d` (Attempt 7)
- Base Commit: `e16ea3956f7f75cb1a758b37f9991f043289a901`
- Inspected Files:
  - `src/browser_core/mcp_server.py`
  - `src/browser_core/page_manager.py`
  - `tests/test_mcp_server.py`
  - `scripts/dom_agent.js`
- Reviewer Engine: ChatGPT Web (GPT-5.6 / GPT-6 Sol Thinking High) via Chrome CDP session `omni_review` (`https://chatgpt.com/c/6ac41d2a-57b8-83eb-83b0-7cafff9379fe`)

## Findings

Adversarial audit completed via ChatGPT Web Oracle for Attempt 7 (`0c41a63`).
Overall Verdict from ChatGPT Web: **`STATUS: CHANGES_REQUESTED`**.

| Area | Verdict | Details |
| :--- | :---: | :--- |
| MCP 2026-07-28 / SDK v2 compliance | **PASS** | Cleanly targets MCP Python SDK v2 using `MCPServer`, `ToolError`, and `structured_output=False`. Modern `resultType: "complete"` and SDK `_meta` format modeled. |
| Asyncio / Playwright deadlock safety | **PASS** | Synchronous Playwright instances pinned strictly to dedicated `BrowserWorker` single-thread executor; zero cross-thread greenlet context switching. |
| Iframe-ref action compatibility | **PASS** | Ref-based actions resolve target frame and evaluate occlusion/revision against `target_eval`. Hover/scroll return full `StateDelta` shape with matching revision and epoch. |
| Authentic CDP context matching | **PASS with isolation blocker** | Genuine CDP `browserContextId` resolution works, but discovery probes empty non-matching contexts by creating temporary pages, which triggers observable side-effects across lease boundaries. |
| Inner observe payload <=13.5KB | **PASS** | DOM tree nodes, URLs, and recipes progressively trimmed below 13.5 KB. |
| Entire JSON-RPC packet <=15,360 bytes | **FAIL** | Fixed 256-byte transport reserve does not guarantee total packet <=15,360 bytes against arbitrary-length client request IDs (which MCP protocol leaves unbounded). |
| 100% backward/isolation compatibility | **FAIL** | Blocked on cross-context page probing and unbounded request-ID wire invariant scoping. |

---

### Confirmed Improvements & Approvals

1. **MCP 2026-07-28 & SDK v2 Compliance**: **PASS**. Adheres to MCP Python SDK v2 `MCPServer`, `ToolError`, dual-era `server/discover` and `initialize` compatibility, and models the modern response envelope (`resultType: "complete"` and `_meta.io.modelcontextprotocol/serverInfo`).
2. **Playwright Thread Affinity & Deadlock Safety**: **PASS**. Single `ThreadPoolExecutor(max_workers=1)` per `BrowserWorker` eliminates cross-thread greenlet hazards.
3. **Iframe-Ref Action Parity**: **PASS**. Target frame evaluation ensures iframe refs return the child frame's authoritative epoch and revision with `StateDelta` contract shape.

---

### Unresolved Architectural & Specification Blockers

#### 1. Cross-Context Probe Creation During Empty Context Resolution (Invariant 8 & 10)
- **Issue**: In `PageManager.connect()` (`src/browser_core/page_manager.py`), context discovery iterates over all `ctx in self.browser.contexts`. When it encounters an empty context, it creates a temporary page `p = ctx.new_page()` to inspect its `browserContextId` via CDP.
- **Violation**: In a shared browser instance hosting multiple leased contexts, this probes and temporarily mutates contexts belonging to other agents/leases. Page creation/destruction emits CDP target events and mutates context state across lease boundaries.
- **Remediation**: Use a browser-level CDP session (`self.browser.new_browser_cdp_session()`) to query target IDs without creating pages in candidate contexts, or obtain architectural sign-off on context discovery semantics.

#### 2. Wire Budget Boundary Invariant Scoping (Invariant 3)
- **Issue**: `_wire_size()` models standard UUID request IDs (36 chars) and `_is_over_budget()` reserves 256 bytes of headroom. However, the MCP specification defines `RequestId = string | number` with no maximum string length. A client sending a >1000-character request ID will cause the total JSON-RPC response to exceed 15,360 bytes.
- **Violation**: The server cannot truthfully assert that *every* valid JSON-RPC packet is `<= 15,360` bytes without enforcing a request-ID length limit or explicitly defining the invariant.
- **Remediation**: Clarify Invariant 3 in the task specification: formally scope the `<= 15 KB` boundary to the *server-controlled `CallToolResult` response payload with transport headroom for standard request IDs (<= 36 chars)*.

---

## Independent Verification

| Check | Command Executed by Reviewer | Exit Code | Outcome |
| :--- | :--- | :---: | :--- |
| MCP Test Suite | `pytest tests/test_mcp_server.py -v` | 0 | 13/13 PASSED |
| Subsystem Regression | `pytest tests/test_workflow_composition.py tests/test_v1_1_battle_testing.py -v` | 0 | 21/21 PASSED |
| Whitespace Hygiene | `git diff --check e16ea39..0c41a63` | 0 | 0 warnings (Clean) |
| ChatGPT Web Audit | `./scripts/oracle extract --session omni_review` | 0 | Detailed Adversarial Audit Extracted (`.tmp/chatgpt_review_attempt7.md`) |
| Schema Linter | `python3 scripts/lint_communication_records.py .agents/communication/reviews/review-task-001_mcp_server_foundation.md` | 0 | PASSED |

## Decision Rationale

Under Rule 5 (*No Infinite Multi-Agent Debates* in `AGENTS.md`):
"Maximum 3 revision cycles per task. If a task is not approved after 3 attempts, mark `STATUS: BLOCKED` and escalate to the human operator."

Attempt 7 has resolved modern MCP envelope formatting, iframe action parity, and empty context discovery. However, two architectural nuances remain:
1. Context discovery creates temporary probe pages across candidate contexts, affecting lease boundary isolation.
2. Invariant 3 wire budget model assumes standard request IDs (<= 36 chars) with 256-byte headroom, which requires formal task spec alignment regarding unbounded client request IDs.

Status is recorded as **`STATUS: BLOCKED`** per Rule 5, escalating to the Human Operator and Orchestrator for architectural disposition.