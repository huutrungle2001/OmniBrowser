# Task: task-009-guarded-state-transitions-and-risk-classes

RECORD_TYPE: TASK
RECORD_ID: task-009-guarded-state-transitions-and-risk-classes
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-19T14:40:00Z
UPDATED_AT: 2026-09-19T14:40:00Z
FROM: hub
TO: implementer

## Objective

Implement Milestone v2.4 of OmniBrowser: **Guarded State Transitions, Composite Semantic Matcher & Risk Classes**.

Upgrade Level 2 Procedural Cache from a simple URL-pattern matcher into a **Guarded Executable Cache Entry Engine**:
1. Replace Boolean URL matching with **Composite Semantic Matching & Anchor Verification** (hard preconditions, required anchors, forbidden anchors, semantic fingerprint similarity).
2. Implement **Risk Classes (R0–R4)** with strict gating on high-risk operations (R3 persistent mutations, R4 irreversible/external actions like deletion/payment).
3. Introduce **Outcome Taxonomy & UNKNOWN_SIDE_EFFECT Reconciliation**: Distinguish between `CONFIRMED_SUCCESS`, `SAFE_FAILURE`, and `UNKNOWN_SIDE_EFFECT` to prevent destructive blind retries.
4. Track **Recipe Health Telemetry** (execution count, success rate, health score) to enable empirical cache reliability.

## Scope

### Owned:
1. `src/browser_core/contracts.py`:
   - Define `RiskClass` enum (`R0_READONLY`, `R1_REVERSIBLE_NAV`, `R2_LOCAL_MUTABLE`, `R3_PERSISTENT_MUTATION`, `R4_IRREVERSIBLE`).
   - Define `ExecutionOutcome` enum (`CONFIRMED_SUCCESS`, `SAFE_FAILURE`, `UNKNOWN_SIDE_EFFECT`).
   - Define `SemanticAnchor`, `MatcherSpec`, `SafetySpec`, `HealthStats` dataclasses.
   - Extend `Recipe` data model with matcher anchors, pre/postconditions, safety spec, and health stats.
   - Extend `RecipeRunResult` with outcome classification, risk class, and reconciliation status.

2. `src/browser_core/recipes.py`:
   - **Composite Semantic Matcher**:
     - `match_page(page_url, observed_tree, recipe) -> MatchResult`:
       - Check `hard_preconditions` & `required_anchors`: All required anchors must be present.
       - Check `forbidden_anchors`: If any forbidden anchor is present (e.g. "Session expired", "Confirm Delete"), match immediately fails (`score = 0.0`).
       - Compute `semantic_similarity`: Jaccard similarity between observed element signatures and recipe recorded anchors.
       - Composite score formula: `0.30 * route_sim + 0.40 * semantic_sim + 0.20 * anchor_coverage + 0.10 * trust_score`.
   - **Guarded Execution Engine**:
     - Pre-execution Guard Phase: Evaluates preconditions and anchors against live DOM. Fails closed with `PreconditionFailedError` if hard guards fail.
     - Risk-gated Action Phase: Rejects actions exceeding permitted risk level unless `--allow-irreversible` is explicitly set.
     - Post-execution Verification Phase: Checks postconditions and classifies outcome into `CONFIRMED_SUCCESS`, `SAFE_FAILURE`, or `UNKNOWN_SIDE_EFFECT`.
   - **Reconciliation Protocol**:
     - If an action fails during or after an R3/R4 step, triggers `reconcile_outcome(manager, page, recipe)` to inspect current DOM and ascertain whether the mutation occurred before raising an error.
   - **Health & Telemetry**:
     - `record_execution(recipe_id, outcome, similarity)`: Updates execution count, successes, failures, and rolling health score.

3. `scripts/cdp_controller.py`:
   - Update `recipe run`:
     - Add `--allow-irreversible` / `--allow-r4` flag for R4 operations.
     - Support guarded execution with automatic precondition checking and reconciliation on unknown side effects.
     - Output structured JSON result with `outcome`, `risk_class`, and `health_score`.
   - Update `observe`:
     - Include `match_score`, `risk_class`, and `guard_status` in `suggested_recipes`.

4. `tests/test_guarded_transitions.py`:
   - Comprehensive unit and integration test suite:
     - Verify required anchors match and forbidden anchors strictly veto matches.
     - Verify semantic similarity scoring on DOM variations.
     - Verify Risk Class gating: R0-R2 succeed, R3 guarded, R4 blocked unless explicitly allowed.
     - Verify outcome classification: `SAFE_FAILURE` vs `CONFIRMED_SUCCESS` vs `UNKNOWN_SIDE_EFFECT`.
     - Verify reconciliation flow on ambiguous mutation failures.
     - Verify backward compatibility for v2.3 recipes (defaulting to safe assumptions).

### Preserve:
- 100% backward compatibility for existing recipes and CLI subcommands.
- Iron Invariant 1 (Zero live profile pollution: port 17082 untouched; isolated ephemeral directories only).
- All 52 existing tests in `tests/test_implicit_cache.py`, `tests/test_concurrency_broker.py`, `tests/test_recipes.py`, `tests/test_learning_loop.py` must remain green.

## Acceptance Criteria

1. **Semantic Matcher & Anchor Verification**:
   - Matching a page against a recipe evaluates required anchors, forbidden anchors, and semantic similarity.
   - If a forbidden anchor is detected on the page, the recipe is immediately rejected (`score = 0.0`).
2. **Risk Classes Gating (R0–R4)**:
   - Actions classified into R0–R4 based on operation and target metadata.
   - Executing an R4 recipe without `--allow-irreversible` fails closed with `RiskGateError`.
3. **Guarded Execution & Outcome Classification**:
   - `execute_recipe` runs Guard Phase -> Action Phase -> Postcondition Phase.
   - Outcomes are unambiguously classified into `CONFIRMED_SUCCESS`, `SAFE_FAILURE`, or `UNKNOWN_SIDE_EFFECT`.
4. **Ambiguity Reconciliation**:
   - An action failure occurring after a persistent mutation step triggers page re-observation and reconciliation before declaring status.
5. **Health Telemetry**:
   - Recipe store updates execution counts and health scores upon completion.
6. **Test Verification & Worktree Hygiene**:
   - All tests in `tests/test_guarded_transitions.py` pass with exit code 0.
   - Full regression suite (52+ tests) passes with exit code 0.
   - `git diff --check` clean with 0 whitespace issues.

## Validation

- `pytest tests/test_guarded_transitions.py -v`: verify semantic matcher, anchor checks, risk gating, and reconciliation.
- `pytest tests/test_implicit_cache.py tests/test_concurrency_broker.py tests/test_recipes.py tests/test_learning_loop.py -v`: ensure zero regressions.
- Ephemeral Chrome verification on dynamic ports without touching live profile port 17082.

