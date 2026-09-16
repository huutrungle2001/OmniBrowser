# OmniBrowser: Task Backlog & Progress Tracker

## Active Milestone: Phase 1 (Core Foundation & Semantic Scanner)

- [ ] Task 001: Implement `src/browser_core/contracts.py` (Data schemas, error taxonomy, constants)
- [ ] Task 002: Implement `scripts/dom_agent.js` (DOM candidate scanner, opaque refs, visibility filter)
- [ ] Task 003: Implement `src/browser_core/page_manager.py` (CDP connection, target attach, epoch lifecycle)
- [ ] Task 004: Phase 1 Integration Test with HTML fixture

---

## Backlog: Phase 2 (Engine & Primitives)

- [ ] Task 005: Implement `src/browser_core/engine.py` (`observe`, `act`, postcondition watchers, state delta)
- [ ] Task 006: Implement `src/browser_core/primitives.py` (`inspectVisual`, `runBrowserCode`)
- [ ] Task 007: Phase 2 Integration Test on ephemeral Chrome instance

---

## Backlog: Phase 3 (CLI Integration, Backward Compatibility & Skill Packaging)

- [ ] Task 008: Update `scripts/cdp_controller.py` with unified subcommand dispatch
- [ ] Task 009: Comprehensive regression test for legacy commands (`goto`, `eval`, `list-tabs`, `screenshot`)
- [ ] Task 010: Deploy and sync to `~/.gemini/config/skills/browser-automation-cdp/` + update `SKILL.md`
