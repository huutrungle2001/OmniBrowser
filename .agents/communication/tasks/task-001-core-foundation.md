# Task: task-001-core-foundation

RECORD_TYPE: TASK
RECORD_ID: task-001-core-foundation
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-17T00:53:00Z
UPDATED_AT: 2026-09-17T00:53:00Z
FROM: omni_orchestrator
TO: omni_implementer

## Objective

Establish the foundational data contracts (`contracts.py`), the in-browser semantic DOM scanner (`dom_agent.js`), and the CDP page connection manager (`page_manager.py`). This forms the core engine for Phase 1 of OmniBrowser, enabling compact semantic projections with opaque element refs (`f0.d9.n186`) while strictly enforcing safety invariants.

## Scope

Owned:
- `src/browser_core/contracts.py`
- `scripts/dom_agent.js`
- `src/browser_core/page_manager.py`
- `tests/fixtures/test_page.html`
- `tests/test_phase1.py`

Preserve:
- Do not modify or interact with the live profile `~/.chrome-ai-profile`.
- Ensure zero breaking changes for existing CLI subcommands.

## Acceptance Criteria

- [ ] Criterion 1 (Contracts): `contracts.py` defines typed models for `DOMNodeRef`, `ObserveResult`, `ActRequest`, `ActResult`, `StateDelta`, and standard exception hierarchy (`StaleRefError`, `TargetNotFoundError`, `ActionTimeoutError`).
- [ ] Criterion 2 (DOM Scanner): `dom_agent.js` executes in-page, scans interactive elements, generates stable opaque refs (`f0.d9.n186`), captures bounds/visibility/interactivity, and produces compact JSON (<15KB on standard pages).
- [ ] Criterion 3 (Page Manager): `page_manager.py` manages CDP connections via Playwright, supports multi-target/iframe attachment, tracks epoch tokens across navigations, and executes isolated ephemeral Chrome sessions for tests.
- [ ] Criterion 4 (Verification): `tests/test_phase1.py` navigates an ephemeral Chrome instance to `tests/fixtures/test_page.html`, asserts successful DOM scan, validates opaque ref resolution, and exits 0.

## Validation

- `python3 -m pytest tests/test_phase1.py -v`
- `git diff --check`
