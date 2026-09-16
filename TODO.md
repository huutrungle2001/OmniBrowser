# OmniBrowser: Master Backlog & Task Tracking

## Phase 1: Core Foundation & Semantic Scanner
- [ ] **Task 001**: Implement `src/browser_core/contracts.py` (Dataclasses, error taxonomy, exit codes)
- [ ] **Task 002**: Implement `scripts/dom_agent.js` (BFS traversal, WeakMap, opaque ref `f0.d9.n186`, candidate filtering)
- [ ] **Task 003**: Implement `src/browser_core/page_manager.py` (Playwright CDP connection, multi-target attach, epoch token, script injection)
- [ ] **Task 004**: Create HTML test fixtures (`tests/fixtures/standard_form.html`, `spa_replacement.html`, `iframe_parent.html`)
- [ ] **Task 005**: Author and verify Phase 1 test suite (`tests/test_observe.py`) on ephemeral Chrome instance

---

## Phase 2: Action Engine & Escape Hatches
- [ ] **Task 006**: Implement `src/browser_core/engine.py` (`act()`, native input dispatch, expectation watchers, state delta, stale-ref recovery)
- [ ] **Task 007**: Implement `src/browser_core/primitives.py` (`inspect()`, bounded `runBrowserCode()`, targeted `inspectVisual()` crop via Pillow)
- [ ] **Task 008**: Create interactive fixtures (`tests/fixtures/shadow_components.html`, `canvas_ui.html`)
- [ ] **Task 009**: Author and verify Phase 2 test suite (`tests/test_actions.py`, `tests/test_stale_recovery.py`, `tests/test_primitives.py`)

---

## Phase 3: Unified CLI, Backward Compatibility & Benchmarking
- [ ] **Task 010**: Implement `scripts/cdp_controller.py` (Subcommand router, 100% legacy compatibility for `list-tabs`, `goto`, `eval`, `screenshot`)
- [ ] **Task 011**: Author regression test suite (`tests/test_cli_legacy.py`)
- [ ] **Task 012**: Author benchmark suite comparing legacy raw HTML/screenshot vs `observe`/`act` (latency & token usage)

---

## Phase 4: Skill Packaging & Production Deployment
- [ ] **Task 013**: Deploy and sync code to `~/.gemini/config/skills/browser-automation-cdp/`
- [ ] **Task 014**: Update `~/.gemini/config/skills/browser-automation-cdp/SKILL.md` with complete API reference & usage instructions
- [ ] **Task 015**: Independent audit and sign-off by `omni_reviewer` (`STATUS: APPROVED`)
