# Task: task-001_mcp_server_foundation

RECORD_TYPE: TASK
RECORD_ID: task-001_mcp_server_foundation
STATUS: TASK_READY
ATTEMPT: 5
CREATED_AT: 2026-10-05T22:40:00Z
UPDATED_AT: 2026-10-06T01:45:00Z
FROM: orchestrator
TO: implementer

## Objective

Transform OmniBrowser into a standard high-performance Model Context Protocol (MCP) server over standard JSON-RPC 2.0 stdio transport.

In Attempt 5 (Final Remediation Pass Authorized by Human Operator), resolve the final 4 architectural blockers identified in `review-task-001_mcp_server_foundation.md`:
1. Greenlet Thread Affinity: Remove process-global `_ACTIVE_BROWSERS` cache from `src/browser_core/page_manager.py` to preserve Playwright synchronous thread affinity.
2. Live DOM Revision Getter: Add `get revision() { return revision; }` on `agent` in `scripts/dom_agent.js` and return `delta: {"changed": [], "added": [], "removed": []}` (`StateDelta`) in `_op_act`.
3. Outer JSON-RPC Wire Payload Bound: In `src/browser_core/mcp_server.py`, verify that the fully wrapped and serialized JSON-RPC 2.0 response packet is strictly $\le 15$ KB (15,360 bytes).
4. Clean Multi-Context Test: Update `test_page_manager_connect_positive_multi_context` to test multi-context isolation cleanly without cross-thread global caches.

## Scope

### Leased Files & Paths:
1. `src/browser_core/page_manager.py`:
   - Remove `_ACTIVE_BROWSERS` global dictionary completely.
   - Keep `browser: Browser | None = None` parameter in `PageManager.__init__`.
   - In `PageManager.connect()`, start Playwright and connect over CDP strictly on the caller's thread when `self.browser is None`. Maintain fail-closed `RuntimeError` when `self.browser_context_id` is not found in `self.browser.contexts`.
   - In `PageManager.close()`, close `self.browser` only if owned by this instance (`self._playwright is not None`).

2. `scripts/dom_agent.js`:
   - In `agent` object definition, add:
     ```javascript
     get revision() {
       return revision;
     },
     ```
     so `window[Symbol.for('__OMNI_DOM_AGENT__')]?.revision` returns the authoritative in-browser revision.

3. `src/browser_core/mcp_server.py`:
   - In `_op_act`, return `delta: asdict(StateDelta())` (`{"changed": [], "added": [], "removed": []}`) and `state_delta: {"revision": rev, "document_epoch": doc_epoch}` on hover and window scroll to achieve 100% `ActResult` contract parity.
   - In `_op_observe`, ensure the postcondition guarantees the serialized outer JSON-RPC response strictly satisfies $\le 15$ KB (15,360 bytes):
     ```python
     def _wire_size(p: dict) -> int:
         raw_text = json.dumps(p, ensure_ascii=False)
         wrapper = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": raw_text}], "is_error": False}}
         return len(json.dumps(wrapper, ensure_ascii=False).encode("utf-8"))
     ```
     Ensure `_wire_size(payload) <= 15360` before returning.

4. `tests/test_mcp_server.py`:
   - Update `test_page_manager_connect_positive_multi_context` to pass `browser=pm_setup.browser` directly, testing positive multi-context binding within the same thread.
   - Assert `delta` in hover and scroll responses contains `{"changed": [], "added": [], "removed": []}`.
   - Assert outer JSON-RPC packet size $\le 15360$ bytes.

## Acceptance Criteria

1. **Thread Affinity Invariant**:
   - `_ACTIVE_BROWSERS` process-global cache completely removed from `src/browser_core/page_manager.py`. All Playwright operations confined strictly to initiating thread.
2. **Authoritative Revision & StateDelta Shape**:
   - `window[Symbol.for('__OMNI_DOM_AGENT__')].revision` returns live integer revision.
   - `_op_act` hover and scroll responses return `delta` matching `StateDelta` shape (`{"changed": [], "added": [], "removed": []}`) and `state_delta: {"revision": rev, "document_epoch": doc_epoch}`.
3. **Hard Outer Wire Budget Invariant (Invariant 3)**:
   - Serialized JSON-RPC 2.0 tool result packet $\le 15$ KB (15,360 bytes) across all inputs.
4. **Full Regression Passing**:
   - `pytest tests/test_mcp_server.py -v` passes 100%.
   - `pytest tests/ -v` passes 100% across the entire repository (146+ tests, 0 failures).
5. **Result Record & Notification**:
   - Update `.agents/communication/results/task-001_mcp_server_foundation.md` for Attempt 5.
   - Lint communication records and notify `omni_reviewer`.

## Validation

1. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-001_mcp_server_foundation.md` exits 0.
2. `pytest tests/test_mcp_server.py -v` passes 100%.
3. `pytest tests/test_cli_legacy.py -v` passes 100%.
4. Full regression: `pytest tests/ -v` exits 0.
