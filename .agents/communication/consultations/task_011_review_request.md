# Architectural & Code Review Request: Task-011 (Milestone v2.5.x: Cross-Agent Promotion, Quarantine & Shared Cache Governance)

## 1. Context & Objective
Following your formal approval of Task-010 (Milestone v2.5: State-Transition Graph & Workflow Composition), we have implemented **Milestone v2.5.x: Cross-Agent Promotion, Quarantine & Shared Cache Governance**.

In a multi-agent environment where multiple agents concurrently explore, record, and execute browser recipes, procedural cache cannot remain an ungoverned static store. Raw drafts must not pollute shared fast paths, broken recipes must be quarantined immediately to prevent cascading agent failures, concurrent writes must be protected by optimistic concurrency (CAS), and storage must be managed via multi-factor utility scoring (U-Score).

## 2. Core Modules & Implementation Details

### A. Data Contracts (`src/browser_core/contracts.py`)
- `RecipeLifecycleState`: `DRAFT`, `VERIFIED_LOCAL`, `VERIFIED_SHARED`, `CURATED`, `SUSPECT`, `QUARANTINED`, `ARCHIVED`.
- `LifecycleRecord`: Tracks `state`, monotonic `revision`, `generation`, `sessions_seen`, `agents_seen`, `consecutive_failures`, `quarantine_reason`, `quarantined_at`, and `utility_score`.
- `PromotionPolicy`: Defines evidence thresholds requiring independent evidence:
  - `DRAFT` $\rightarrow$ `VERIFIED_LOCAL`: $\ge 2$ successful replays.
  - `VERIFIED_LOCAL` $\rightarrow$ `VERIFIED_SHARED`: $\ge 5$ successes across $\ge 3$ independent sessions with 0 structural failures.
  - `VERIFIED_SHARED` $\rightarrow$ `CURATED`: $\ge 20$ executions across $\ge 3$ distinct agents with success rate $\ge 0.95$.
- `QuarantinedRecipeError`: Typed exception raised when execution is attempted on a quarantined recipe.
- `CASConflictError`: Typed exception raised when optimistic concurrency detects revision collision.

### B. Procedural Engine Governance (`src/browser_core/recipes.py`)
1. **Evidence-Based Promotion (`evaluate_promotion`)**:
   - Strictly verifies unique sessions (`sessions_seen`) and distinct agents (`agents_seen`) before promoting recipes. Single-session repetitions cannot promote a recipe to shared status.
2. **Automated Quarantine Engine (`check_quarantine_triggers`)**:
   - Triggers automatic quarantine on:
     - $\ge 3$ consecutive execution failures.
     - Any single `UNKNOWN_SIDE_EFFECT` on a persistent mutation (R3/R4).
     - Health score degradation below 0.60 (after at least 3 executions).
3. **Execution & Suggestion Exclusion Invariant**:
   - `RecipeEngine.execute()` blocks execution of quarantined recipes with `QuarantinedRecipeError`.
   - `suggest_for_url()` strictly filters out all recipes in `QUARANTINED` or `ARCHIVED` state.
   - Unpromoted `DRAFT` recipes from learning memory are restricted from shared execution suggestions.
4. **Optimistic Concurrency & Revision CAS (`RecipeStore.save`)**:
   - Updates support `expected_revision`. If disk revision differs, `CASConflictError` is raised.
   - Monotonically increments revision and atomically writes via temporary file rename.
5. **Utility-Score (U-Score) Eviction & Archiving**:
   - Multi-factor utility formula:
     $U(R) = \frac{\text{Frequency} \times \text{Reliability} \times \text{RecomputeCost} \times \text{RecencyDecay}}{\text{StorageCost}}$
   - `RecipeStore.evict_low_utility(threshold=0.5, archive=True)` archives stale recipes instead of permanent deletion.
6. **Administrative Operations**:
   - Added `RecipeStore.quarantine(recipe_id, reason)`, `restore(recipe_id)`, `promote(recipe_id, target_state)`.

### C. Workflow Graph Safety (`src/browser_core/workflows.py`)
- `StateTransitionGraph.ingest_recipe` automatically excludes recipes in `QUARANTINED`, `SUSPECT`, or `ARCHIVED` states, ensuring Dijkstra path planning never routes workflows through quarantined edges.

### D. CLI Integration (`scripts/cdp_controller.py`)
- `cdp_controller.py recipe lifecycle <recipe_id>`: Dumps lifecycle progression, sessions, agents, and health.
- `cdp_controller.py recipe quarantine <recipe_id> [--reason <reason>]`: Administrative quarantine.
- `cdp_controller.py recipe restore <recipe_id>`: Restores recipe to active local service.
- `cdp_controller.py recipe promote <recipe_id> [--target-state <state>]`: Manual/policy promotion.

## 3. Validation & Testing Evidence
- **Test Suite**: `tests/test_recipe_governance.py` (12/12 passed in 3.27s):
  1. `test_lifecycle_record_and_policy_serialization`: Serialization & default values.
  2. `test_evidence_based_promotion_draft_to_local_to_shared_to_curated`: Independent session and agent promotion thresholds.
  3. `test_quarantine_trigger_on_three_consecutive_failures`: Auto-quarantine on failure streak.
  4. `test_quarantine_trigger_on_unknown_side_effect_persistent_mutation`: Instant quarantine on UNKNOWN_SIDE_EFFECT for R3/R4.
  5. `test_quarantine_trigger_on_health_score_degradation`: Auto-quarantine when health $< 0.60$.
  6. `test_quarantined_recipe_excluded_from_suggestions`: Verification that quarantined recipes are never suggested.
  7. `test_quarantined_recipe_excluded_from_state_transition_graph`: Graph pruning of quarantined edges.
  8. `test_quarantined_recipe_execution_raises_quarantined_error`: Fail-closed execution block.
  9. `test_recipe_restore_re_enables_execution`: Restoration lifecycle path.
  10. `test_cas_optimistic_concurrency_conflict_prevention`: CAS conflict detection and race prevention.
  11. `test_utility_score_calculation_and_eviction`: U-Score evaluation and non-destructive archiving.
  12. `test_cli_lifecycle_quarantine_and_promote`: CLI commands end-to-end.
- **Full Repository Regression Suite**:
  - `python3 -m pytest tests/ -v`: **117 passed, 0 failed** across all test suites in 77.19s.
- **Safety Invariant**: Zero connection to live profile port 17082.
- **Whitespace Hygiene**: `git diff --check` passes with exit code 0.
- **Git Commit**: `49ff210` (`feat(recipes): implement cross-agent promotion, quarantine, and shared cache governance (Milestone v2.5.x)`).

## 4. Review Scope & Questions
Please review the design, code diff, and testing evidence against our multi-agent safety invariants:
1. Does the evidence-based promotion lifecycle properly isolate unverified drafts and require sufficient independent verification before shared consumption?
2. Does the automatic quarantine mechanism guarantee immediate fail-closed protection against cascading failures across agents?
3. Are the CAS optimistic concurrency controls and U-Score archival sound for distributed multi-agent operations?

Please render your formal review verdict (`APPROVED` or `CHANGES REQUIRED` with specific criteria).
