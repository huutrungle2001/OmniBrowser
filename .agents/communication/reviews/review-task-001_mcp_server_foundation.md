# Review: review-task-001_mcp_server_foundation

RECORD_TYPE: REVIEW
RECORD_ID: review-task-001_mcp_server_foundation
STATUS: REVISION_REQUIRED
ATTEMPT: 2
CREATED_AT: 2026-10-05T23:05:00Z
UPDATED_AT: 2026-10-05T23:24:00Z
FROM: reviewer
TO: orchestrator, implementer
BASE_COMMIT: e16ea3956f7f75cb1a758b37f9991f043289a901
REVIEWED_COMMIT: 514cee78e6003c1dee1fe3b8933a4d9ce7bc36ae

## Scope Audited

- Reviewed Commit: `514cee78e6003c1dee1fe3b8933a4d9ce7bc36ae` (Attempt 2)
- Base Commit: `e16ea3956f7f75cb1a758b37f9991f043289a901`
- Inspected Files:
  - `src/browser_core/mcp_server.py`
  - `src/browser_core/page_manager.py`
  - `tests/test_mcp_server.py`
  - `scripts/cdp_controller.py`
  - `src/browser_core/contracts.py`
- Reviewer Engine: ChatGPT Web (GPT-5.6 / GPT-6 Sol Thinking High) via Chrome CDP session `omni_review` (`https://chatgpt.com/c/6ac41d2a-57b8-83eb-83b0-7cafff9379fe`)

## Findings

Adversarial audit completed via ChatGPT Web Oracle for Attempt 2. Major improvements verified:
- **Low-level Server alias removed**: Clean import of `MCPServer` / `FastMCP`.
- **Tool error semantics**: Implemented `ToolError` raising `is_error=True` across tool failures.
- **Idempotency**: `install_scanner()` properly detects existing page session.
- **Asyncio / Playwright deadlock safety**: Fully verified (**PASS**), with 10-call concurrency regression test passing.
- **Application-level 15KB budget**: Multitier truncation with hard postcondition assertion verified (**PASS**).

However, **REVISION IS REQUIRED** to resolve four targeted edge cases before production approval:

---

### 1. `browser_run_code` Default Timeout Regression (Backward Compatibility)
- **Issue**: Attempt 2 defined `timeout_ms: int = 5000` in `browser_run_code` and always forwards it. The established `runBrowserCode()` primitive uses `timeout_ms=None`, defaulting to 2,000 ms for `read` mode and 10,000 ms for `write` mode. Forcing 5,000 ms changes historical timeout semantics for callers that omit it.
- **Remediation**: Set parameter default to `timeout_ms: int | None = None` and forward `None` through to preserve primitive defaults.

---

### 2. `browser_act` Contract Parity on Hover and Window Scroll
- **Issue**: For `scroll` (window wheel) and `hover` actions, `_op_act` constructs dummy responses with `"state_delta": {}, "elapsed_ms": 0` and omits `document_epoch` and `revision`. The established `ActResult` contract requires `document_epoch`, `revision`, `state_delta`, and measured `elapsed_ms`.
- **Remediation**: Populate `document_epoch`, `revision`, and full state fields across all action branches in `_op_act`.

---

### 3. Lease Context Isolation Fail-Open Edge Case (Invariant 8 & 10)
- **Issue**: In `PageManager.connect()`, if a specific `browser_context_id` is supplied but cannot be found in `browser.contexts`, it silently falls back to `browser.contexts[0]`. An unmappable or missing lease context must fail closed, not fail open into another tenant's context.
- **Remediation**: When `self.browser_context_id is not None`, raise `TargetNotFoundError` / `LeaseNotFoundError` if that specific context ID does not exist in `browser.contexts`.

---

### 4. Wire-Level MCP Budget (`structured_output`)
- **Issue**: `browser_observe` returns a typed `dict[str, Any]`. In the high-level Python SDK, a dictionary return can serialize into both textual `content` and `structured_content`, doubling the wire payload over stdio JSON-RPC.
- **Remediation**: Specify `structured_output=False` on the `@server.tool` decorator for `browser_observe` (or ensure serialized stdio payload $\le 15$ KB).

---

## Independent Verification

| Check | Command Executed by Reviewer | Exit Code | Outcome |
| :--- | :--- | :---: | :--- |
| MCP Test Suite | `pytest tests/test_mcp_server.py -v` | 0 | 8/8 PASSED |
| Full Regression Suite | `pytest tests/ -v` | 0 | 143/143 PASSED |
| Whitespace Hygiene | `git diff --check e16ea39..514cee7` | 0 | 0 warnings (Clean) |
| ChatGPT Web Audit | `./scripts/oracle extract --session omni_review` | 0 | Detailed Adversarial Audit Extracted |
| Schema Linter | `python3 scripts/lint_communication_records.py .agents/communication/reviews/review-task-001_mcp_server_foundation.md` | 0 | PASSED |

## Decision Rationale

Attempt 2 makes substantial architectural progress: thread affinity is proven safe under concurrency, tool errors adhere to standard MCP `isError=true` reporting, and application-level 15KB budget postconditions are established. 

To achieve 100% backward compatibility and airtight lease isolation, the remaining 4 edge cases (run_code timeout default, act hover/scroll contract parity, fail-closed lease context lookup, and wire-level budget) must be remediated.

Status remains **REVISION_REQUIRED** for Attempt 3.