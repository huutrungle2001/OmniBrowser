# Task: task-005-procedural-memory-and-adaptive-recipes

RECORD_TYPE: TASK
RECORD_ID: task-005-procedural-memory-and-adaptive-recipes
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-17T15:22:00Z
UPDATED_AT: 2026-09-17T15:22:00Z
FROM: orchestrator
TO: implementer

## Objective

Deliver Milestone v2.0 of OmniBrowser: Implement the **Procedural Memory & Adaptive Recipes Engine** (`src/browser_core/recipes.py`) inspired by the "Web Agents That Actually Learn" architecture. Enable OmniBrowser to store, match, execute, and self-heal site-specific procedural recipes (turning 10-turn exploration into 1-step exploitation), support graceful fail-closed fallback to Level 1 semantic observation, provide network traffic interception for API reverse engineering, and expose the `recipe` CLI subcommand.

## Scope

Owned:
- `src/browser_core/recipes.py`: Procedural memory module containing `Recipe`, `RecipeStep`, `RecipeStore`, execution fast-path, PII sanitization, and self-healing failure feedback ledger.
- `src/browser_core/primitives.py`: Add network traffic interception primitive (`capture_network_traffic`) to record background HTTP/API requests during tasks.
- `scripts/cdp_controller.py`: Expose unified `recipe` subcommands (`recipe list`, `recipe run`, `recipe show`) while preserving 100% backward compatibility for all existing commands.
- `recipes/`: Directory housing canonical JSON recipes (e.g. `recipes/two_factor_auth.json`, `recipes/standard_enrolment_form.json`).
- `tests/test_recipes.py`: Comprehensive test suite verifying recipe matching, execution, graceful fallback, failure feedback, and network interception on ephemeral Chrome sandboxes.

Preserve:
- `src/browser_core/contracts.py`, `src/browser_core/page_manager.py`, `src/browser_core/engine.py`.
- Zero Live Profile Tampering invariant: All automated tests must strictly run against isolated ephemeral Chrome instances; never connect to or modify `~/.chrome-ai-profile` on port 17082 during tests.
- 100% backward compatibility with legacy CLI commands (`list-tabs`, `goto`, `eval`, `screenshot`, `observe`, `act`, `inspect`, `run-code`, `visual`).

## Acceptance Criteria

1. **Recipe Schema & Procedural Store (`src/browser_core/recipes.py`)**:
   - Structured `Recipe` definition supporting:
     - `id`, `name`, `description`, `domain_pattern` (regex or glob matching URLs).
     - `steps`: List of procedural actions (`click`, `fill`, `select`, `wait_for`, `eval`) with parameterized values (e.g. `{{code}}`, `{{username}}`).
     - `validation`: Postcondition assertions (e.g. URL regex, text presence).
     - `metadata`: version, success/failure counts, last failure reason.
   - `RecipeStore` capable of scanning and loading recipes from `recipes/` directory and matching active URLs.

2. **Execution Fast-Path & Graceful Fallback**:
   - `RecipeEngine.execute(recipe, page, params)` runs procedural steps sequentially with minimal round-trips.
   - **Fail-Closed Fallback**: If any recipe step fails (DOM modified, element not found, or timeout):
     - Execution stops cleanly without uncaught exceptions or browser crash.
     - Logs structured failure feedback (step index, reason, target) into the recipe ledger.
     - Returns `fallback_required: true` and an explicit diagnostic message instructing the caller to fallback to Level 1 `observe()` / `act()`.

3. **PII-Sanitized Recipe Distillation Helper**:
   - Helper function allowing an agent to distill a recorded successful interaction into a new recipe JSON.
   - Masks sensitive values (emails, auth tokens, passwords) into template parameters (`{{param}}`).

4. **Network Traffic Interception (Level 3 Evolution)**:
   - Provide a network capture primitive in `primitives.py` using CDP `Network` domain (`Network.enable`, `Network.responseReceived`, `Network.getResponseBody`).
   - Allows capturing background JSON/REST requests during page actions, enabling agents to inspect underlying APIs.

5. **Unified CLI Integration (`scripts/cdp_controller.py`)**:
   - CLI support for:
     - `recipe list [--url <url>]`: Lists available recipes matching domain.
     - `recipe run <recipe_id> [--params '{"key": "val"}']`: Executes a recipe against active/matched tab.
     - `recipe show <recipe_id>`: Dumps recipe steps and health ledger.

6. **Test Suite & Empirical Verification (`tests/test_recipes.py`)**:
   - Verifies recipe domain matching.
   - Verifies successful fast-path execution skipping exploration turns.
   - Verifies graceful fallback and ledger recording on simulated UI drift (broken selector).
   - Verifies network interception captures JSON responses.
   - All tests pass with exit code 0 on ephemeral Chrome.

7. **Multi-Agent Delegation Invariant**:
   - Implementer Lead (`omni-hub`) strictly acts as supervisory architect and auditor. Implementation must be delegated to worker subagents.

## Validation

1. `python3 -m pytest tests/test_recipes.py -v` passes with exit code 0 on ephemeral Chrome.
2. Complete test suite `python3 -m pytest tests/ -v` (30+ tests) passes with exit code 0.
3. `git diff --check` passes with 0 warnings.
4. CLI subcommands (`recipe list`, `recipe run`, `recipe show`) verified via `--help` and integration tests.
