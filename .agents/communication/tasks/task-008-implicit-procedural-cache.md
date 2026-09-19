# Task: task-008-implicit-procedural-cache

RECORD_TYPE: TASK
RECORD_ID: task-008-implicit-procedural-cache
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-19T12:53:00Z
UPDATED_AT: 2026-09-19T12:53:00Z
FROM: hub
TO: browser-arch

## Objective

Implement Milestone v2.3 of OmniBrowser: **Implicit Flight Recorder & Domain-Scoped Procedural Cache**.

Transform Level 2 Procedural Memory from an explicit, manual opt-in workflow (`learn begin`, `--learn-run`, `learn complete`, `recipe promote`) into an **autonomous, zero-effort background flight recorder and auto-suggest cache**. Agents performing daily tasks (`trunglh-oracle`, `omni-oracle`, etc.) should automatically contribute to and benefit from procedural memory without needing to manage `run_id`s or remember explicit learning CLI invocations.

## Scope

### Owned:
1. `src/browser_core/recipes.py` & `src/browser_core/engine.py`:
   - **Domain-Scoped Recipe Hierarchy**:
     - Organize recipes and site maps hierarchically by domain:
       - `recipes/<domain>/`: Curated, versioned recipes for the domain (e.g. `recipes/portal.unitemps.com/login_flow.json`).
       - `recipes/<domain>/sitemaps/`: Cached semantic site maps for visited paths (interactive inputs, buttons, navigation links).
       - Local learned memory partitioned by domain: `~/.omnibrowser/memory/v1/<domain>/candidates/`.
   - **Implicit Session Flight Recorder**:
     - Automatically record actions dispatched through `act()` on an active lease/tab without requiring `--learn-run`.
     - Maintain an in-memory / ephemeral flight journal per session.
     - Auto-detect workflow completion / progression boundaries:
       - Meaningful URL navigation transitions (e.g. from `/Account/SignOn` to `/members/candidate/...`).
       - Successful form submissions or multi-step action sequences.
     - Automatically distill recorded sequences into draft candidates with full safety sanitization (`{"$ref": "password"}` for credentials; never persist raw secrets or PII).
   - **Auto-Query & Recipe Matcher**:
     - Function `suggest_for_url(url: str) -> list[dict]`: Match current URL against domain recipes and return ranked candidates with confidence scores.
   - **Site Map Auto-Cache**:
     - On page load / initial `observe`, cache lightweight semantic projections (element roles, names, selectors) into `recipes/<domain>/sitemaps/<normalized_path>.json`.

2. `scripts/cdp_controller.py`:
   - **Auto-Suggestion on `observe`**:
     - When `observe` is called, automatically query matching recipes for the target page.
     - If matches exist, include a `suggested_recipes` block in the JSON output containing:
       `recipe_id`, `name`, `confidence`, and the ready-to-run CLI invocation (`recipe run <id> [--params ...]`).
   - **Domain-Aware Recipe CLI**:
     - Support `recipe list [--domain <domain>]`.
     - Support `recipe run <recipe_id_or_path>`.
     - Deprecate requirement for `--learn-run` while keeping backward compatibility for explicit runs.

3. `tests/test_implicit_cache.py`:
   - Comprehensive unit and integration test suite:
     - Verify domain-scoped directory partitioning.
     - Verify implicit flight recording captures actions without `--learn-run`.
     - Verify auto-distillation strips raw passwords into `{"$ref": "password"}`.
     - Verify `observe` output automatically includes `suggested_recipes` when a matching recipe exists for the domain.
     - Verify site map caching on page observation.

### Preserve:
- Existing Level 1 (Primitives), Level 2 (Explicit Recipes & Distillation), Level 3 (Visual & Spatial Reasoning) interfaces and behavior.
- Invariant 9 (Profile Sanctity: live profiles are never mutated during tests).
- All 29 concurrency broker tests in `tests/test_concurrency_broker.py` must continue to pass with 0 errors.

## Acceptance Criteria

1. **Zero-Effort Recording**:
   - Calling `act()` sequence without `--learn-run` automatically records events in the session flight journal.
   - Successful workflow transition triggers automatic distillation of a sanitized draft recipe.
2. **Domain-Scoped Hierarchy**:
   - Recipes and site maps are cleanly partitioned under `<domain>/` subdirectories rather than a single flat directory.
3. **Auto-Suggestion in `observe`**:
   - When observing a URL that has an existing recipe under its domain, `observe` returns `suggested_recipes` with confidence score >= 0.8 and ready-to-run CLI string.
4. **Security & PII Sanitization**:
   - Passwords, verification codes, and bearer tokens are strictly parameterized as `{"$ref": ...}` and never saved in plain text.
5. **Test Verification**:
   - All new tests in `tests/test_implicit_cache.py` pass with exit code 0.
   - Full regression suite (`test_concurrency_broker.py`, `test_actions.py`, `test_recipes.py`, `test_learning_loop.py`) passes with exit code 0.
6. **Clean Worktree**:
   - Zero linter or whitespace warnings (`git diff --check`).
