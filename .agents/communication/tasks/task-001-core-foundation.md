# Task: task-001-core-foundation

RECORD_TYPE: TASK
RECORD_ID: task-001-core-foundation
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-17T00:53:00Z
UPDATED_AT: 2026-09-17T01:25:00Z
FROM: omni_orchestrator
TO: omni_implementer

## Objective

Establish the foundational data contracts (`contracts.py`), the in-browser semantic DOM candidate scanner with reverse ref resolution (`dom_agent.js`), the Playwright CDP connection and epoch manager (`page_manager.py`), and a strictly sandboxed ephemeral Chrome test harness (`conftest.py`). This forms the robust core engine for Phase 1 of OmniBrowser, enabling compact semantic projections with bidirectional opaque element refs (`f<frame>.d<epoch>.n<node>`).

## Scope

Owned:
- `src/browser_core/contracts.py`
- `scripts/dom_agent.js`
- `src/browser_core/page_manager.py`
- `tests/conftest.py`
- `tests/fixtures/test_page.html`
- `tests/test_phase1.py`

Preserve:
- Do not modify or connect to the user live profile `~/.chrome-ai-profile` (hard-block port 17082 in all test runners).
- Raw CDP OOPIF cross-site attachment is explicitly deferred to Phase 2; Phase 1 focuses on top-level page lifecycle, frame inventory via `page.frames`, and same-origin document injection.

## Acceptance Criteria

- [ ] Criterion 1 (Contracts): `contracts.py` defines typed models for `DOMNodeRef` (`f<frame>.d<epoch>.n<node>`), `ObservedNode`, `ObserveResult`, `ActRequest`, `ActResult`, and standard exception hierarchy (`StaleRefError`, `TargetNotFoundError`, `ActionTimeoutError`).
- [ ] Criterion 2 (DOM Scanner & Bidirectional Resolver): `dom_agent.js` executes in-page with idempotent sentinel (`window[Symbol.for("__OMNI_DOM_AGENT__")]`), scans interactive candidates, generates stable opaque refs, provides a reverse resolver (`ref -> WeakRef<Element>`), and produces compact UTF-8 JSON (<15KB, filtering large select option bloat).
- [ ] Criterion 3 (Page Manager): `page_manager.py` manages Playwright CDP connections, tracks frame tokens and document epochs across navigations, injects `dom_agent.js` via `Page.addScriptToEvaluateOnNewDocument` + runtime evaluation bootstrap, and deterministically invalidates stale refs on navigation.
- [ ] Criterion 4 (Test Harness & Verification): `tests/conftest.py` spawns an ephemeral Chrome process with `--remote-debugging-port=0`, reads `DevToolsActivePort`, serves `tests/fixtures/test_page.html` via local HTTP, hard-fails on any connection to port 17082, and `tests/test_phase1.py` passes with exit code 0.

## Validation

- `python3 -m pytest tests/test_phase1.py -v`
- `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-001-core-foundation.md`
- `git diff --check`
