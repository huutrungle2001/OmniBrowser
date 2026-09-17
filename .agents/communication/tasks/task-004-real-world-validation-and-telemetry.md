# Task: task-004-real-world-validation-and-telemetry

RECORD_TYPE: TASK
RECORD_ID: task-004-real-world-validation-and-telemetry
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-17T12:48:00Z
UPDATED_AT: 2026-09-17T12:48:00Z
FROM: orchestrator
TO: implementer

## Objective

Execute Milestone v1.1 of OmniBrowser: Conduct real-world battle-testing of the Progressive Capability Pyramid across three distinct workflow archetypes (Semantic Form, Dynamic SPA, and Complex Canvas/Blocker), capture empirical telemetry metrics (latency p50/p95, observation token/byte payload, first-action success rate, escalation breakdown), and author the v1.1 telemetry benchmark report.

## Scope

Owned:
- `tests/test_v1_1_battle_testing.py`: Comprehensive test suite verifying the 3 workflow archetypes against isolated fixtures/sandboxes.
- `docs/v1_1_telemetry_report.md`: Quantitative telemetry and benchmark report documenting performance metrics and empirical findings.
- Any necessary edge-case fixes in `src/browser_core/` or `scripts/dom_agent.js` if real-world testing surfaces DOM quirks (e.g. shadow DOM nesting or dynamic iframe event propagation).

Preserve:
- `src/browser_core/contracts.py` stability and backwards compatibility.
- Zero Live Profile Tampering invariant: All automated tests must run against isolated ephemeral Chrome instances; never connect to or modify `~/.chrome-ai-profile` during test runs.
- 100% backward compatibility for legacy CLI commands (`list-tabs`, `goto`, `eval`, `screenshot`).

## Acceptance Criteria

1. **Workflow Archetype 1 (Semantic Form Submission & Validation)**:
   - Automated test demonstrating multi-field form completion (`fill`, `select`, `check`), postcondition expectation evaluation (`expect`), form validation handling, and submission verification.

2. **Workflow Archetype 2 (Dynamic SPA & Real-Time Mutations)**:
   - Automated test demonstrating interaction with dynamic single-page applications where elements update without full page reload.
   - Verification of `StateDelta` capturing added/changed elements and fingerprint-based `stale-ref` recovery when DOM nodes mutate during client-side rendering.

3. **Workflow Archetype 3 (Complex UI / Canvas / Visual Fallback)**:
   - Automated test demonstrating graceful escalation to `runBrowserCode` (bounded JS execution) and `inspectVisual` (targeted Pillow crop) when interacting with Canvas-based or non-semantic components.
   - Confirmation of CAPTCHA/blocker safety behavior: flagging `REQUIRES_HUMAN_VERIFICATION` without attempting unsafe automated bypasses.

4. **Telemetry & Empirical Benchmark Report (`docs/v1_1_telemetry_report.md`)**:
   - Deliver an empirical telemetry report recording:
     - Controller latency: p50 (<400ms target) and p95 (<900ms target).
     - Observation payload size: bytes and tokens per turn comparing legacy raw HTML (100KB-500KB) vs OmniBrowser semantic projection (<15KB).
     - Token reduction percentage (>80% savings).
     - First-action success rate (>90% target).
     - Escalation breakdown (percentage of turns resolved at Level 1 `observe`/`act` vs Level 3 `run-code` vs Level 4 `visual`).

5. **Multi-Agent Delegation Invariant**:
   - Implementer Lead (`omni-hub`) strictly acts as supervisory architect and auditor. Code implementation must be delegated to worker subagents.

## Validation

1. `python3 -m pytest tests/test_v1_1_battle_testing.py -v` passes with exit code 0 on ephemeral Chrome.
2. Full test suite `python3 -m pytest tests/ -v` (20+ tests) passes with exit code 0.
3. `docs/v1_1_telemetry_report.md` contains verified empirical measurements.
4. `git diff --check` passes with zero warnings.
