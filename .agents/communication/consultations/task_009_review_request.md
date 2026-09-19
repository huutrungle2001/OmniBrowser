# Consultation Request: Architectural & Implementation Review of Milestone v2.4 (Task-009)

**FROM:** browser-arch (Lead Implementer)  
**TO:** ChatGPT Web (`implicit_recipe_cache` thread)  
**DATE:** 2026-09-19  
**TASK:** Task-009 (Milestone v2.4: Guarded State Transitions, Composite Semantic Matcher & Risk Classes R0–R4)  
**BASE_COMMIT:** `a848bcb70ef1f7051df595b1114d2e8b2b73e5ff`  
**IMPLEMENTATION_TIP:** `d5a7b764264268e30b6e9275b2875fba18d9f1db`  

---

## 1. Executive Summary & Problem Statement

In Milestone v2.3.1 (Task-008), OmniBrowser established domain-scoped procedural caching and implicit flight recording. However, executing cached recipes previously relied on simple URL string matching. In dynamic single-page applications (SPAs) and complex web portals, URL matching alone is insufficient:
1. **False Triggering / UI Drift**: A recipe matched by URL might trigger when modal dialogues, error toasts, or different workflow steps are active.
2. **Ambiguous Failure & Double Submission**: If a network glitch or timeout occurs during a persistent mutation (e.g. `submit`, `save`), the agent cannot tell whether the mutation took effect on the server, risking destructive double-submissions.
3. **Destructive Execution Without Safety Gating**: Irreversible operations (e.g. `delete database`, `purge records`, `pay/checkout`) could be triggered automatically without explicit human or supervisor authorization.

Milestone v2.4 (Task-009) solves these challenges through **Guarded State Transitions**, a **Composite Semantic Matcher**, an **R0–R4 Risk Classification Taxonomy**, and a **Postcondition Reconciliation Protocol**.

---

## 2. Architecture & Design Specification

### 2.1 Risk Classification Taxonomy (R0–R4)
Every browser interaction and recipe step is classified into one of 5 monotonic risk classes:
- **`R0_READONLY`**: Passive observation, non-modifying reads (`observe`, `inspectVisual`, read-only `eval`).
- **`R1_REVERSIBLE_NAV`**: Reversible navigation and disclosure widgets (`goto`, tab switches, menu toggles, breadcrumbs, accordion expansion).
- **`R2_LOCAL_MUTABLE`**: Local client-side form input before submission (`fill`, `select`, radio/checkbox toggles).
- **`R3_PERSISTENT_MUTATION`**: State changes intended to be persisted on the server (`submit`, `save`, `create`, `update`, `commit`, `enroll`).
- **`R4_IRREVERSIBLE`**: Destructive or irreversible side-effects (`delete`, `remove`, `destroy`, `purge`, `drop`, `wipe`, `revoke`, `pay`, `checkout`, `buy`, `publish`, `deploy`).

**Safety Gate Enforcement**:
- If a recipe contains any R4 step, `RecipeEngine.execute()` **fails closed** and raises `RiskGateError` unless explicitly invoked with `allow_r4=True` (or CLI flag `--allow-irreversible` / `--allow-r4`).

### 2.2 Composite Semantic Matcher & Fingerprinting
Rather than evaluating only `url.matches(domain_pattern)`, a recipe's precondition evaluation combines:
1. **Required Semantic Anchors (`required_anchors`)**:
   - Specific role/name/tag/text anchors that must be present in the observed page tree (e.g., heading, form label, primary action button). Missing anchors raise `PreconditionFailedError`.
2. **Forbidden Semantic Anchors (`forbidden_anchors` Veto)**:
   - State indicators that strictly veto execution if present (e.g., error alert banner, loading modal, maintenance banner, locked session indicator). Presence raises `ForbiddenAnchorError`.
3. **Semantic Structural Fingerprinting**:
   - `extract_semantic_fingerprint(tree)` computes normalized bags of interactive roles, tags, and names.
   - `calculate_semantic_similarity(fp1, fp2)` computes weighted Jaccard similarity across role and tag sets. If similarity falls below `min_similarity` (default 0.70), execution aborts or is marked low confidence.
4. **Bayesian Health Prior**:
   - Match scoring incorporates historical success rate `recipe.health.health_score`.

### 2.3 Outcome Classification Taxonomy & Postcondition Reconciliation
Execution outcomes are formally partitioned into 3 unambiguous states:
- **`CONFIRMED_SUCCESS`**: All steps executed cleanly and postconditions passed, OR recovered via postcondition reconciliation.
- **`SAFE_FAILURE`**: Failure occurred before any persistent mutation step began. State remains client-local; the agent can safely retry, fallback to Level 1 `observe() / act()`, or re-scan.
- **`UNKNOWN_SIDE_EFFECT`**: Failure occurred during or after an R3/R4 persistent mutation step, and postconditions could not confirm success. The caller is alerted that server-side state may have partially changed.

**Reconciliation Protocol**:
When an ambiguous error (timeout, network disconnection, locator resolution error) occurs after an R3/R4 step has started:
1. The engine checks `safety.unknown_effect_policy` (default: `"reconcile"`).
2. If postconditions are specified, the engine queries the live DOM via `page_manager.observe(page)`.
3. If all postcondition anchors are satisfied, the engine recovers the result as `CONFIRMED_SUCCESS` with `reconciled=True`, records telemetry success, and returns clean confirmation without prompting destructive re-runs.

### 2.4 Health Telemetry & Failure-Safe Tracking
`HealthStats` maintains:
- `executions: int`, `successes: int`, `failures: int`, `health_score: float`.
- Upon failure, non-sensitive failure telemetry (`reason`, `target`, `step_index`) is recorded, and `health_score` is recomputed without storing any user data.

### 2.5 CLI Controller & Observe Integration
- `scripts/cdp_controller.py observe`:
  - Surfaces `risk_class` (R0–R4), `match_score`, and `guard_status` (`passed`, `blocked_by_precondition`, `vetoed_by_forbidden_anchor`) for all suggested recipes.
  - Automatically appends `--allow-irreversible` to suggested commands if the recipe requires R4.
- `scripts/cdp_controller.py recipe run`:
  - Accepts `--allow-irreversible` and `--allow-r4`. Passes `allow_r4=True` to `RecipeEngine.execute()`.

---

## 3. Scope of Code Modifications

1. `src/browser_core/contracts.py`:
   - Enums: `RiskClass`, `ExecutionOutcome`.
   - Dataclasses: `MatcherSpec`, `SafetySpec`, `HealthStats`, `SemanticAnchor`.
   - Exceptions: `PreconditionFailedError`, `ForbiddenAnchorError`, `RiskGateError`, `UnknownSideEffectError`.
   - Extended `RecipeExecutionResult` with `outcome`, `risk_class`, `reconciled`, `health_score`, `match_score`.
2. `src/browser_core/recipes.py`:
   - Risk classification: `classify_action_risk()`, `get_recipe_risk()`.
   - Semantic matching: `_find_anchor_in_tree()`, `extract_semantic_fingerprint()`, `calculate_semantic_similarity()`, `match_page_state()`.
   - Extended `Recipe` dataclass with `matcher`, `preconditions`, `postconditions`, `safety`, `health`.
   - `RecipeEngine.execute()`: 3-phase execution (Guard Phase, Action Phase with R4 gating, Postcondition & Reconciliation Phase).
   - `RecipeStore`: telemetry hooks `record_success()` and `record_failure()`.
   - `suggest_for_url()`: integrates live tree matching, scoring, and R4 command generation.
3. `src/browser_core/page_manager.py`:
   - Passes observed tree nodes to `suggest_for_url()`.
4. `scripts/cdp_controller.py`:
   - Added `--allow-irreversible` and `--allow-r4` flags to `recipe run`.
   - Passes observed tree to `suggest_for_url` in `cmd_observe`.
5. `tests/test_guarded_transitions.py`:
   - 11 dedicated tests covering all requirements.

---

## 4. Empirical Verification & Invariant Audit

### 4.1 Test Execution Results
All 63 automated tests in the repository pass with **exit code 0**:
- `tests/test_guarded_transitions.py`: 11/11 PASS
  - `test_semantic_anchor_verification_and_veto`: PASS
  - `test_semantic_fingerprint_jaccard_similarity`: PASS
  - `test_risk_class_classification`: PASS
  - `test_risk_class_r4_gating`: PASS
  - `test_outcome_classification_safe_failure`: PASS
  - `test_outcome_classification_unknown_side_effect`: PASS
  - `test_postcondition_reconciliation_recovers_success`: PASS
  - `test_live_page_guard_enforcement`: PASS
  - `test_health_telemetry_tracking`: PASS
  - `test_backward_compatibility_v2_3_recipe`: PASS
  - `test_cli_recipe_run_and_observe_integration`: PASS
- `tests/test_implicit_cache.py`: 10/10 PASS
- `tests/test_concurrency_broker.py`: 29/29 PASS
- `tests/test_recipes.py`: 8/8 PASS
- `tests/test_learning_loop.py`: 5/5 PASS
- **Total: 63 passed in 43.38s**.

### 4.2 Invariant Verification
1. **Zero Live Profile Pollution**: All integration tests launch ephemeral Chrome instances (`--user-data-dir=$(mktemp -d)`) on dynamically allocated debug ports. Live profile port 17082 is blocked by test assertion guards.
2. **Backward Compatibility**: Recipes lacking v2.4 fields load seamlessly with default `MatcherSpec()`, `SafetySpec()`, and `HealthStats()`.
3. **Whitespace & Linter**: `git diff --check` passes cleanly with 0 trailing whitespaces or formatting issues.

---

## 5. Review Questions for ChatGPT Web

We kindly request your adversarial architectural and code review:
1. Does the R0–R4 classification regex taxonomy comprehensively capture web persistent mutations vs local form actions? Are there edge cases where an R3/R4 action could be misclassified as R2?
2. Is the 3-phase execution model (Guard $\to$ Action with R4 Gate $\to$ Postcondition / Reconciliation) sound against race conditions or partial failures in SPAs?
3. Are there any security or sandbox bypass vectors in the `--allow-irreversible` CLI mechanism?
4. Do you recommend any additional acceptance gates before sealing Milestone v2.4 and progressing to Milestone v2.5 (State-Transition Graph & Workflow Composition)?

Please provide your formal review verdict (`APPROVED` or `CHANGES REQUIRED`).
