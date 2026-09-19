# Result: task-011-cross-agent-promotion-and-quarantine

RECORD_TYPE: RESULT
RECORD_ID: task-011-cross-agent-promotion-and-quarantine
STATUS: READY_FOR_REVIEW
ATTEMPT: 1
CREATED_AT: 2026-09-19T18:03:00Z
UPDATED_AT: 2026-09-19T18:03:00Z
FROM: implementer
TO: hub
BASE_COMMIT: 9dc47bf9effb4d16376ef08e1e6e06d125550749
IMPLEMENTATION_TIP: 2515bc4ec6ab6d8e810e2b94a8b7d08c6abc4ddb


## Summary

- Implemented Milestone v2.5.x of OmniBrowser: **Cross-Agent Promotion, Quarantine & Shared Cache Governance** based on `task-011-cross-agent-promotion-and-quarantine.md`.
- Modeled procedural memory as an active, self-governing distributed cache with evidence-based progression, fail-closed quarantine boundaries, and linearizable optimistic concurrency:
  - **Evidence-Based Promotion Lifecycle**:
    - Modeled states: `DRAFT` $\rightarrow$ `VERIFIED_LOCAL` $\rightarrow$ `VERIFIED_SHARED` $\rightarrow$ `CURATED`.
    - Enforced multi-agent evidence diversity thresholds (`sessions_seen`, `agents_seen`, `reliability` $\ge 0.95$, `structural_failures` $== 0$) via `PromotionPolicy` and `evaluate_promotion`.
    - Isolated `DRAFT` recipes from shared suggestions and workflow path planners.
  - **Automated Quarantine & Degradation Engine**:
    - Modeled degradation states: `SUSPECT` $\rightarrow$ `QUARANTINED` $\rightarrow$ `ARCHIVED`.
    - Automatic quarantine triggers: $\ge 3$ consecutive failures, any `UNKNOWN_SIDE_EFFECT` on persistent writes ($R3/R4$), or health score $< 0.60$.
    - Defense-in-depth exclusion: Quarantined recipes are excluded from `suggest_for_url()`, `StateTransitionGraph.ingest_recipe()`, `WorkflowEngine.execute()`, and `RecipeEngine.execute()`.
  - **Linearizable Compare-And-Swap (CAS)**:
    - Extended `RecipeStore.save` with inter-process file locking via `fcntl.flock(LOCK_EX)` on `self.root / ".locks" / f"{recipe.id}.lock"`.
    - Executed the entire transaction (`read authoritative revision` $\rightarrow$ `compare expected_revision` $\rightarrow$ `mutate` $\rightarrow$ `increment revision` $\rightarrow$ `update U-score` $\rightarrow$ `atomic temp write` $\rightarrow$ `os.replace`) strictly within the lock critical section, eliminating lost-update race conditions.
  - **Fail-Closed Execution Re-Validation**:
    - Both `RecipeEngine.execute()` and `WorkflowEngine.execute()` unconditionally re-validate the canonical lifecycle state against `RecipeStore.get()` prior to browser dispatch.
    - Stale plans or cached in-memory recipe objects composed before quarantine immediately raise `QuarantinedRecipeError` with 0 browser operations dispatched.
  - **Generation-Bound Evidence Resets**:
    - Implemented `compute_recipe_content_digest` (deterministic SHA256 of executable payload) and bound it to `LifecycleRecord`.
    - Semantic mutations increment `generation`, update `content_digest`, and reset all evidence counters (`sessions_seen`, `agents_seen`, `consecutive_failures`), dropping `VERIFIED_SHARED` / `CURATED` recipes back to `VERIFIED_LOCAL`.
  - **Safe Restore & Privileged Promotion Controls**:
    - `restore()` strictly resets quarantined recipes to `VERIFIED_LOCAL` (never directly to `VERIFIED_SHARED` or `CURATED`), requiring fresh cross-agent verification.
    - Ordinary promotion enforces `PromotionPolicy`; administrative overrides require `privileged=True`, explicit `actor`, and `reason`, logging immutably to `LifecycleRecord.audit_log`.
  - **Utility-Score (U-Score) Eviction & Archiving**:
    - Implemented multi-factor utility scoring: $U(R) = \frac{\text{StorageCost}}{\text{Frequency} \times \text{Reliability} \times \text{RecomputeCost} \times \text{RecencyDecay}}$.
    - `evict_lowest_utility()` non-destructively archives canonical recipes (`ACTIVE` $\rightarrow$ `ARCHIVED`) while pruning unverified transient drafts.
  - **CLI Integration**:
    - Added `recipe promote`, `recipe quarantine`, `recipe restore`, and `recipe lifecycle` subcommands to `scripts/cdp_controller.py`.
- **Formal ChatGPT Web Architecture Review & Approval**:
  - Initial review returned `CHANGES REQUIRED` identifying 6 hardening criteria.
  - All 6 criteria fully satisfied and validated with 16 dedicated unit/integration tests in `tests/test_recipe_governance.py`.
  - Second consultation with ChatGPT Web (GPT-5.6 Sol Thinking High) on tab `6aaeab64`: **Verdict: APPROVED** (Recorded at `.agents/communication/consultations/task_011_final_approval.md`).
- Authored 16 unit, integration, and real concurrency tests in `tests/test_recipe_governance.py` (16/16 passed in 3.66s).
- Full repository regression suite: **121 passed, 0 failed** in 85.31s with zero live profile pollution.

## Scope Modified

Owned files created/modified:
- `src/browser_core/contracts.py`:
  - Added `RecipeLifecycleState`, `LifecycleRecord` (with `content_digest` and `audit_log`), `PromotionPolicy`, `QuarantineTrigger`, and `UtilityScore`.
- `src/browser_core/recipes.py`:
  - Added `compute_recipe_content_digest()`, `evaluate_promotion()`, `calculate_utility_score()`.
  - Implemented `RecipeStore.save()` with flock-protected CAS, `promote()`, `quarantine()`, `restore()`, `evict_lowest_utility()`.
  - Updated `RecipeEngine.execute()` to verify canonical quarantine state before dispatch.
- `src/browser_core/workflows.py`:
  - Updated `StateTransitionGraph.ingest_recipe()` to prune quarantined/suspect/archived recipes.
  - Updated `WorkflowEngine.execute()` to verify canonical quarantine state before dispatching edges.
- `scripts/cdp_controller.py`:
  - Added `recipe lifecycle`, `recipe promote`, `recipe quarantine`, `recipe restore` CLI commands.
  - Optimized tab discovery and page lookup with non-blocking HTTP `/json/list` fallback.
- `tests/test_recipe_governance.py`:
  - Authored 16 comprehensive tests including `test_cas_true_multiprocess_atomicity`, `test_stale_plan_execution_blocked_after_recipe_quarantine`, `test_generation_bound_evidence_resets_on_mutation`, and `test_safe_restore_and_privileged_promotion_override`.
- `.agents/communication/consultations/`:
  - Created `task_011_review_request.md`, `task_011_review_response.md`, `task_011_hardening_response_request.md`, and `task_011_final_approval.md`.

Preserved:
- 100% backward compatibility: Existing commands (`goto`, `eval`, `list-tabs`, `observe`, `act`, `recipe list`, `recipe run`, `workflow plan`, `workflow run`) pass identically.
- Zero Live Profile Pollution: Automated test runners strictly blocked from connecting to port 17082.

## Validation Evidence

| Check | Command | Context | Exit | Outcome | Evidence Path |
| :--- | :--- | :--- | :---: | :--- | :--- |
| Task-011 Governance Suite | `python3 -m pytest tests/test_recipe_governance.py -v` | Ephemeral sandbox | 0 | 16/16 passed | `tests/test_recipe_governance.py` |
| Full Repository Regression | `python3 -m pytest tests/ -v` | Ephemeral sandbox | 0 | 121/121 passed | `tests/` |
| Whitespace Hygiene | `git diff --check` | Repository worktree | 0 | Clean | Worktree root |
| Semantic Record Linter | `python3 scripts/lint_communication_records.py .agents/communication/results/task-011-cross-agent-promotion-and-quarantine.md` | Protocol validator | 0 | Validated | `scripts/lint_communication_records.py` |
| ChatGPT Web Review (Turn 1) | `chatgpt-oracle` (Tab `6aaeab64`) | GPT-5.6 Sol Thinking High | 0 | CHANGES REQUIRED (6 criteria) | `.agents/communication/consultations/task_011_review_response.md` |
| ChatGPT Web Review (Turn 2) | `chatgpt-oracle` (Tab `6aaeab64`) | GPT-5.6 Sol Thinking High | 0 | APPROVED (All criteria PASS) | `.agents/communication/consultations/task_011_final_approval.md` |
| Concurrency Atomicity Test | `pytest tests/test_recipe_governance.py -k test_cas_true_multiprocess_atomicity` | Multi-worker barrier | 0 | 1 passed | `tests/test_recipe_governance.py` |
| Stale Plan Quarantine Test | `pytest tests/test_recipe_governance.py -k test_stale_plan_execution_blocked` | Ephemeral mock browser | 0 | 1 passed, 0 dispatch | `tests/test_recipe_governance.py` |
| Safety Invariant Check | `pytest -k guard_live_profile` | conftest socket guard | 0 | 0 live profile access | `tests/conftest.py` |
