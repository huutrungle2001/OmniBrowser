# Task: task-002-action-engine-and-primitives

RECORD_TYPE: TASK
RECORD_ID: task-002-action-engine-and-primitives
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-17T02:00:00Z
UPDATED_AT: 2026-09-17T02:00:00Z
FROM: orchestrator
TO: implementer

## Objective

Build Phase 2 of OmniBrowser: the Action Engine (`engine.py`) and Escape Hatches / Primitives (`primitives.py`). This delivers atomic actions with postcondition expectations (`act`), bounded browser JavaScript execution (`runBrowserCode`), scoped diagnostic inspection (`inspect`), and targeted visual bounding-box cropping with Pillow (`inspectVisual`).

## Scope

Owned:
- `src/browser_core/engine.py`
- `src/browser_core/primitives.py`
- `tests/fixtures/interactive_page.html`
- `tests/test_actions.py`
- `tests/test_primitives.py`

Preserve:
- `src/browser_core/contracts.py` (extend dataclasses if necessary, preserve existing contracts)
- `src/browser_core/page_manager.py` (preserve existing connection/epoch lifecycle)
- `scripts/dom_agent.js` (preserve existing scanner)
- Never connect automated tests to live port 17082 or `~/.chrome-ai-profile`.

## Acceptance Criteria

1. **Atomic Actions with Expectations (`engine.py`)**:
   - `act(page, action="click"|"fill"|"select"|"press", ref=ref, value=val, expect=...)`:
     - Resolves opaque `ref` via `page_manager`.
     - Dispatches native Playwright actions against the resolved element.
     - Supports postcondition expectations: wait for DOM mutation revision change or URL navigation.
     - Returns structured `ActResult` containing `ok: bool`, `state_delta` (new revision, document epoch), and elapsed duration.
     - Stale ref detection: If ref document epoch is outdated, raise `StaleRefError` with clear escalation advice.

2. **Escape Hatches & Diagnostic Primitives (`primitives.py`)**:
   - `inspect(page, mode="dom"|"ax"|"frame-tree", ref=None, depth=3)`:
     - Returns bounded textual inspection of the target element or frame tree.
   - `runBrowserCode(page, script=..., mode="read"|"write")`:
     - Executes custom JavaScript in an isolated async IIFE.
     - Enforces max output size limit (<= 32 KB UTF-8 JSON) and timeout bounds (2s for read, 10s for write).
   - `inspectVisual(page, ref=None, output_path=None)`:
     - Takes a targeted screenshot cropped to the bounding box of the element with +20px padding via Pillow.
     - If CAPTCHA is detected, returns `requires_human_verification: true` without attempting bypass.

3. **Multi-Agent Delegation Invariant**:
   - The Implementer Lead (`cx/gpt-5.6-terra`) **MUST NOT write code directly on the main thread**.
   - It MUST explicitly summon worker subagents from `.agents/subagents/` via `spawn_agent`:
     - `systems_engineer` for `engine.py` and `primitives.py`.
     - `test_architect` for `interactive_page.html`, `test_actions.py`, and `test_primitives.py`.
     - `internal_reviewer` for pre-handoff diff auditing.

## Validation

1. `python3 -m pytest tests/test_actions.py tests/test_primitives.py -v` passes with exit code 0 on ephemeral Chrome.
2. `python3 -m pytest tests/ -v` (full suite including phase 1) passes with exit code 0.
3. `git diff --check` passes with 0 warnings.
