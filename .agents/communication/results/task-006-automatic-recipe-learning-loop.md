# Result: task-006-automatic-recipe-learning-loop

RECORD_TYPE: RESULT
RECORD_ID: task-006-automatic-recipe-learning-loop
STATUS: READY_FOR_REVIEW
ATTEMPT: 1
CREATED_AT: 2026-09-18T22:00:00Z
UPDATED_AT: 2026-09-18T22:00:00Z
FROM: implementer
TO: hub
BASE_COMMIT: b793b774b4488459d43823268ed83d44eafaf2dc
IMPLEMENTATION_TIP: 4a25ebe0146560521adb2e052a5f30dfd3b56772

## Summary

- Added privacy-first automatic procedural memory: `act()` records semantic actions to an explicit isolated learning run, then `learn complete` distills a local draft recipe.
- Added local SQLite WAL trace and health ledger stores plus candidate JSON drafts below `~/.omnibrowser/memory/v1` (or a test-provided memory root); canonical source-controlled `recipes/` is never modified by learning.
- Added scored anchor bundles, strict mutating-target ambiguity protection, automatic-recipe `eval` rejection, typed runtime input refs, URL query stripping, and a unified postcondition poller.
- Added backwards-compatible learning and candidate lifecycle CLI commands.

## Scope Modified

Owned files modified:
- `src/browser_core/engine.py`
- `src/browser_core/recipes.py`
- `scripts/cdp_controller.py`
- `tests/test_learning_loop.py`

Preserved:
- Legacy CLI commands and canonical `recipes/` remain unchanged.
- Tests use only temporary Chrome user-data directories and dynamically allocated CDP ports; they do not use port `17082` or a live profile.

## Validation Evidence

| Check | Command | Context | Exit | Outcome | Evidence Path |
| :--- | :--- | :--- | :---: | :--- | :--- |
| Learning loop | `python3 -m pytest tests/test_learning_loop.py -q` | Ephemeral Chrome and temporary memory root | 0 | 5/5 passed: privacy/WAL inspection, draft replay, run isolation, promotion, ambiguity, navigation race, and CLI lifecycle | none |
| Regression group A | `python3 -m pytest tests/test_actions.py tests/test_cli_legacy.py tests/test_learning_loop.py tests/test_phase1.py -q` | Ephemeral Chrome | 0 | 20/20 passed | none |
| Regression group B | `python3 -m pytest tests/test_primitives.py tests/test_recipes.py tests/test_v1_1_battle_testing.py -q` | Ephemeral Chrome and local HTTP | 0 | 22/22 passed |
| Syntax and hygiene | `python3 -m py_compile src/browser_core/*.py scripts/cdp_controller.py && git diff --check` | Local | 0 | Clean | none |
| CLI surface | `python3 scripts/cdp_controller.py learn --help && python3 scripts/cdp_controller.py recipe --help` | Local, no browser connection | 0 | `learn begin/complete` and `recipe candidates/promote/suggest` available | none |

## Limitations / Artifacts

- Learning is opt-in through an explicit `run_id`; a failed local journal append never changes the completed browser action result.
- Promotion requires `--approve` and only changes the local candidate file and ledger; promoted candidates are not silently copied into reviewed canonical recipes.
