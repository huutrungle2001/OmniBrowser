# Review: review-task-001_mcp_server_foundation

RECORD_TYPE: REVIEW
RECORD_ID: review-task-001_mcp_server_foundation
STATUS: BLOCKED
ATTEMPT: 3
CREATED_AT: 2026-10-05T23:05:00Z
UPDATED_AT: 2026-10-05T23:42:00Z
FROM: reviewer
TO: orchestrator, implementer
BASE_COMMIT: e16ea3956f7f75cb1a758b37f9991f043289a901
REVIEWED_COMMIT: e8c80a906fd60f51ab3d0a5c111ea294617d9028

## Scope Audited

- Reviewed Commit: `e8c80a906fd60f51ab3d0a5c111ea294617d9028` (Attempt 3)
- Base Commit: `e16ea3956f7f75cb1a758b37f9991f043289a901`
- Inspected Files:
  - `src/browser_core/mcp_server.py`
  - `src/browser_core/page_manager.py`
  - `tests/test_mcp_server.py`
  - `scripts/cdp_controller.py`
  - `src/browser_core/contracts.py`
- Reviewer Engine: ChatGPT Web (GPT-5.6 / GPT-6 Sol Thinking High) via Chrome CDP session `omni_review` (`https://chatgpt.com/c/6ac41d2a-57b8-83eb-83b0-7cafff9379fe`)

## Findings

Adversarial audit completed via ChatGPT Web Oracle for Attempt 3.

### Successfully Verified Components
1. **Asyncio / Playwright Deadlock Safety**: **PASS**. Dedicated single-thread worker pattern preserves greenlet thread affinity; 10-call concurrent async test verified without race conditions.
2. **Scanner Idempotency**: **PASS**. Scanner registration re-bootstraps frames without accumulating duplicate CDP sessions.
3. **Restored Primitive Timeout Defaults**: **PASS**. `browser_run_code` default `timeout_ms: int | None = None` correctly forwards `None`, preserving historical 2,000 ms read / 10,000 ms write primitive defaults.
4. **Application-Level 15KB Postcondition**: **PASS**. Multi-tier progressive trimming guarantees inner application observe JSON $\le 15$ KB.

---

### Unresolved Invariants & Architectural Blockers

#### 1. `browser_act` Hover/Scroll Contract Parity & Revision Attribute Error
- **Root Cause**: `_op_act` attempts to read `state.revision` from `state = manager._frame_state(...)`. In `src/browser_core/page_manager.py`, `_FrameState` is defined with only `(token, epoch)` and has **no `revision` attribute**.
- **Impact**: Reading `state.revision` raises `AttributeError`, which is swallowed by `except Exception: pass`, silently falling back to hardcoded `rev = 1`.
- **Contract Mismatch**: The established `ActResult.state_delta` is `{"revision": self.revision, "document_epoch": self.document_epoch}` and includes `action_duration_ms`. The MCP implementation returns `"state_delta": {}`, `"delta": {}`, and omits `action_duration_ms`, violating 100% backward compatibility.

#### 2. Lease Context Lookup Mechanism (`_browser_context_id` Non-Existence)
- **Root Cause**: In `PageManager.connect()`, context matching inspects `getattr(ctx, "_browser_context_id", None) == self.browser_context_id`. Playwright Python's `BrowserContext` implementation does not expose `_browser_context_id`.
- **Impact**: While the fail-closed check prevents silent fallback to `contexts[0]`, every valid non-null lease context ID will fail to match and raise `RuntimeError`. A real CDP-to-Playwright context mapping and a positive multi-context test (creating 2 contexts and verifying isolation) are required.

#### 3. Total Wire-Level JSON-RPC Transport Budget
- **Observation**: `structured_output=False` successfully prevents duplicate structured content. However, the test only checks `len(res.content[0].text.encode("utf-8"))`. When serialized into `CallToolResult` and enveloped in JSON-RPC 2.0 with string escaping, a 15.25 KB observe JSON expands beyond 15 KB on the wire. A safety margin (e.g. 13.5 KB content limit) is required to guarantee total wire payload $\le 15$ KB.

#### 4. MCP SDK Generation & Protocol Specification
- **Observation**: Module still falls back to legacy `FastMCP` (`mcp>=1.0.0`) and tests only the legacy `initialize()` lifecycle. Pinning to `mcp>=2.0.0` or providing dedicated modern `2026-07-28` protocol verification is required to guarantee modern specification compliance.

---

## Independent Verification

| Check | Command Executed by Reviewer | Exit Code | Outcome |
| :--- | :--- | :---: | :--- |
| MCP Test Suite | `pytest tests/test_mcp_server.py -v` | 0 | 10/10 PASSED |
| Full Regression Suite | `pytest tests/ -v` | 0 | 145/145 PASSED |
| Whitespace Hygiene | `git diff --check e16ea39..e8c80a9` | 0 | 0 warnings (Clean) |
| ChatGPT Web Audit | `./scripts/oracle extract --session omni_review` | 0 | Detailed Adversarial Audit Extracted |
| Schema Linter | `python3 scripts/lint_communication_records.py .agents/communication/reviews/review-task-001_mcp_server_foundation.md` | 0 | PASSED |

## Decision Rationale

Under Rule 5 (*No Infinite Multi-Agent Debates* in `AGENTS.md`):
> "Maximum 3 revision cycles per task. If a task is not approved after 3 attempts, mark `STATUS: BLOCKED` and escalate to the human operator."

Because Attempt 3 concludes the third revision cycle with contract parity, context ID mapping, and transport budget issues remaining unresolved, the review is formally marked **`STATUS: BLOCKED`** and escalated to the human operator / orchestrator for guidance.