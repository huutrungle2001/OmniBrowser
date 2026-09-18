# Task: task-006-automatic-recipe-learning-loop

RECORD_TYPE: TASK
RECORD_ID: task-006-automatic-recipe-learning-loop
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-18T19:38:00Z
UPDATED_AT: 2026-09-18T19:38:00Z
FROM: orchestrator
TO: implementer

## Objective

Deliver Milestone v2.1 of OmniBrowser: Implement the **Automatic Procedural Memory & Adaptive Recipe Learning Loop (`Learn → Verify → Promote → Replay`)** incorporating expert architectural consultation from GPT-5.6 Sol High (documented in `docs/03_procedural_memory_rfc_and_expert_consultation.md`). 

Transform Level 2 from static hand-authored JSON recipes into an autonomous, privacy-safe procedural learning system where agents performing browser actions naturally accumulate resilient, parameterized recipes without manual JSON coding or token waste on subsequent runs.

## Scope

Owned:
- `src/browser_core/engine.py`:
  - Fix P0 Navigation Race Condition: Ensure `initial_url` for postcondition verification is snapshot before action execution rather than during expectation polling.
  - Integrate `SemanticActionTransaction` boundary: Emit privacy-safe action events (with explicit `run_id`, sequence, element anchor descriptors, and typed `$ref` parameters) without capturing raw PII, passwords, or raw DOM outerHTML.
- `src/browser_core/recipes.py`:
  - Fix P0 Defects:
    - Forbid `eval` action in all learned/automatic recipes to prevent arbitrary JS execution vulnerabilities.
    - Forbid ambiguous `.first` resolution on mutating actions (`click`, `fill`, `select`); raise `AnchorAmbiguous` if multiple candidates match without explicit ordinal intent.
    - Unify postcondition verification between opaque-ref actions and selector-based recipe steps into a consistent `PostconditionWatcher`.
  - `AnchorCompiler`: Construct scored candidate bundles (3–6 candidates: `test_attr` [0.95], `role_name` [0.91], `label+input` [0.91], scoped CSS [0.78], neighborhood context [0.70]) with dynamic uniqueness and stability scoring.
  - `DistillationEngine`: Parameterize dynamic inputs into typed runtime refs (`{{param}}` / `{"$ref": "runtime_param"}`), strip URL query values, and compile successful traces into draft candidates.
  - Three-Tier Storage Isolation:
    - Canonical `recipes/` in git repo: Immutable, curated, reviewed recipes only.
    - Local user memory in `~/.omnibrowser/memory/v1/`: `traces.db` (SQLite WAL), `candidates/` (draft recipes), `ledger.db` (execution telemetry & health).
    - RAM-only session buffer: Raw passwords and values are held exclusively in memory and wiped upon session close.
- `scripts/cdp_controller.py`:
  - Multi-Agent Session Isolation:
    - `learn begin --goal "<goal>" [--policy suggest]`: Returns an explicit `run_id`.
    - `act ... [--learn-run <run_id>]`: Attaches the action to the specified learning journal.
    - `learn complete <run_id> --success [--validation '<json>']`: Finalizes the run and distills draft recipe.
    - `recipe candidates [--url <url>]`: Lists distilled draft candidates.
    - `recipe promote <candidate_id> [--approve]`: Promotes verified draft to local procedural memory.
    - `recipe suggest [--url <url>]`: Suggests matching recipe with confidence score and required parameters.
- `tests/test_learning_loop.py`: Comprehensive test suite verifying recording, PII sanitization, anchor compilation, draft generation, promotion, and multi-agent run isolation on ephemeral Chrome sandboxes.
- `docs/03_procedural_memory_rfc_and_expert_consultation.md`: Retained as canonical architectural reference.

Preserve:
- `src/browser_core/contracts.py`, `src/browser_core/page_manager.py`.
- Zero Live Profile Tampering invariant: All automated tests must strictly run against isolated ephemeral Chrome instances; never connect to or modify `~/.chrome-ai-profile` on port 17082 during tests.
- 100% backward compatibility with all existing CLI commands (`list-tabs`, `goto`, `eval`, `screenshot`, `observe`, `act`, `inspect`, `run-code`, `visual`, `recipe list/run/show`).

## Acceptance Criteria

1. **P0/P1 Defect Remediation**:
   - `initial_url` in `engine.py` is captured strictly prior to action dispatch.
   - Ambiguous matches in `recipes.py` raise explicit error; `.first` is never silently executed for mutating actions.
   - Learned recipes reject any step containing `"action": "eval"`.
   - Postcondition checking is unified and respects timeout polling.

2. **Privacy Boundary & Semantic Action Journal**:
   - Action recorder intercepts at `act()` boundary.
   - Raw secrets (passwords, tokens, OTPs) and URL query values NEVER enter SQLite WAL or draft JSON files.
   - All text inputs are classified and generalized into typed parameter placeholders.

3. **Anchor Compiler & Resilient Selectors**:
   - Compiles live `ElementHandle` into candidate bundles with confidence scoring.
   - Includes 1-2 hop neighborhood context (nearest form/heading/label) for disambiguation.
   - Rejects fragile selectors (`nth-child`, absolute XPath) from promotion.

4. **Storage Model & Multi-Agent CLI Ergonomics**:
   - `learn begin` outputs a unique `run_id`.
   - Actions with `--learn-run <run_id>` append to `~/.omnibrowser/memory/v1/traces.db` without cross-agent interference.
   - `learn complete` triggers distillation and outputs candidate ID.
   - `recipe candidates` and `recipe promote` manage the draft lifecycle.
   - Canonical `recipes/` in git repository remains completely untouched by automated learning runs.

5. **Test Suite & Empirical Verification**:
   - `tests/test_learning_loop.py` covers end-to-end recording, PII masking, draft distillation, replay with anchor bundle, and CLI subcommands.
   - All tests pass with exit code 0 on ephemeral Chrome sandboxes.

6. **Multi-Agent Delegation Invariant**:
   - Implementer Lead (`omni-hub`) strictly acts as supervisory architect and quality gatekeeper. Code modifications must be delegated to built-in worker subagents (`spawn_agent`).

## Validation

1. `python3 -m pytest tests/test_learning_loop.py -v` passes with exit code 0 on ephemeral Chrome.
2. Full test suite `python3 -m pytest tests/ -v` passes with exit code 0 (zero regressions across all existing suites).
3. `git diff --check` passes with 0 warnings.
4. CLI subcommands (`learn begin`, `learn complete`, `recipe candidates`, `recipe promote`, `recipe suggest`) verified via `--help` and automated test assertions.
