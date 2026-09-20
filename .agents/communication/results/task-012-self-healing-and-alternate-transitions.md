# Result: task-012-self-healing-and-alternate-transitions

RECORD_TYPE: RESULT
RECORD_ID: task-012-self-healing-and-alternate-transitions
STATUS: READY_FOR_REVIEW
ATTEMPT: 1
CREATED_AT: 2026-09-20T13:55:00Z
UPDATED_AT: 2026-09-20T13:55:00Z
FROM: implementer
TO: hub
BASE_COMMIT: 7ce937397182e960743c602fd8c56242c6aa6669
IMPLEMENTATION_TIP: 6ad796917aa8e8486ec7255c8719518f2aa4fb1c


## Summary

- Implemented Milestone v2.6 of OmniBrowser: **Self-Healing Anchor Bundles, Deterministic Local Repair & STG Alternate Route Discovery** based on `task-012-self-healing-and-alternate-transitions.md`.
- Solved procedural automation brittleness caused by UI drift without introducing unsafe anti-patterns, establishing a strict **Online Deterministic Resolver & Offline Learner** architecture:
  - **Scored Candidate Bundle (`AnchorBundle`)**:
    - Replaced rigid single locators with multi-candidate `AnchorBundle` housing scored `AnchorCandidate` items across `test_attr`, `role_name`, `label_input`, `scoped_css`, `neighborhood`, and `text`.
    - Evaluated candidates in descending prior confidence order with dynamic uniqueness checks.
    - Strict Candidate Ambiguity Disqualification: When `count > 1`, candidate is rejected immediately. Under no circumstances is `.first()` clicked on mutating steps.
  - **Zero Online Canonical Mutation Invariant**:
    - Online execution resolves drifted selectors in memory without modifying the canonical on-disk recipe in `RecipeStore`. Disk JSON, generation, revision, and content digest remain 100% byte-for-byte identical.
    - Drifts resolved via alternate candidates are captured as `RepairCandidate` events and durably persisted to `.repairs.jsonl`.
  - **Terminality of UNKNOWN_SIDE_EFFECT**:
    - In `WorkflowEngine.execute`, when an intermediate edge returns `UNKNOWN_SIDE_EFFECT` or raises `UnknownSideEffectError`, detour finding is strictly suppressed (`find_alternate_path` is never called). Execution halts immediately with 0 subsequent edges dispatched.
  - **Risk-Aware R3/R4 Candidate Fallback**:
    - Uniqueness (`count == 1`) does not authorize high-risk mutation.
    - For R3 (Irreversible State): Candidate score must be $\ge 0.80$; text-only candidates are strictly disqualified.
    - For R4 (Destructive / Financial): Candidate score must be $\ge 0.90$; text-only and generic neighborhood candidates are strictly disqualified.
  - **Detour Preserves Original Authorization Envelope**:
    - `StateTransitionGraph.find_alternate_path` accepts `max_allowed_risk` and `allow_r4`, strictly pruning candidate edges that exceed the original workflow's risk envelope.
  - **Workflow-Global History & Bounded Replan Recovery**:
    - Failed edges monotonically accumulate across replans (`failed_edges.add(failed_edge_id)`) in `excluded_edge_ids`, preventing circular execution of broken transitions.
    - Bounded recovery: Added `max_detours = 2`. If `detour_count >= max_detours` and another edge fails, execution fails closed with `WorkflowInterruptedError(exit_code=2)`.
  - **RepairCandidate Generation & Digest Binding**:
    - Extended `RepairCandidate` with `recipe_generation` and `recipe_content_digest`.
    - `RecipeStore.apply_repair_candidate` checks `repair.recipe_content_digest == recipe.lifecycle.content_digest`. If mismatched, fails closed and raises `StaleRepairError(exit_code=2)`.
  - **CLI Integration**:
    - Added `recipe repair list`, `recipe repair inspect`, and `recipe repair apply` subcommands to `scripts/cdp_controller.py`.
- **Formal ChatGPT Web Architecture Review & Approval**:
  - Initial review returned `CHANGES REQUIRED` identifying 6 specific hardening criteria.
  - All 6 criteria implemented and validated with 12 dedicated self-healing tests in `tests/test_recipe_self_healing.py`.
  - Second consultation with ChatGPT Web (GPT-5.6 Sol Thinking High) on tab `6aaeab64`: **Verdict: APPROVED** (Recorded at `.agents/communication/consultations/task_012_final_approval.md`).
- Authored 12 comprehensive unit and ephemeral integration tests in `tests/test_recipe_self_healing.py` (12/12 passed in 14.26s).
- Full repository regression suite: **133 passed, 0 failed** in 107.45s with zero live profile pollution.

## Scope Modified

Owned files created/modified:
- `src/browser_core/contracts.py`:
  - Added `AnchorCandidate`, `AnchorBundle`, `RepairCandidate` (with `recipe_generation` and `recipe_content_digest`), `StaleRepairError`, `AnchorNotFound`, `AnchorAmbiguous`.
  - Added `R0`..`R4` aliases to `RiskClass` and properties to `TransitionEdge` and `WorkflowPlan`.
- `src/browser_core/recipes.py`:
  - Extended `RecipeStep` to support `AnchorBundle`.
  - Implemented `AnchorResolver` with risk-aware R3/R4 confidence thresholds and ambiguity disqualification.
  - Implemented `RecipeEngine.execute()` online healing and repair candidate emission.
  - Implemented `RecipeStore` repair ledger (`record_repair_candidate`, `list_repair_candidates`, `apply_repair_candidate` with stale digest verification).
- `src/browser_core/workflows.py`:
  - Implemented `StateTransitionGraph.find_alternate_path()` with risk-envelope pruning and edge exclusion.
  - Updated `WorkflowEngine.execute()` with terminal `UNKNOWN_SIDE_EFFECT` halting, global `failed_edges` accumulation, and bounded `max_detours = 2`.
- `scripts/cdp_controller.py`:
  - Added `recipe repair` CLI subcommands (`list`, `inspect`, `apply`).
- `tests/test_recipe_self_healing.py`:
  - Authored 12 comprehensive unit, integration, and negative regression tests.
- `.agents/communication/consultations/`:
  - Created `task_012_review_request.md`, `task_012_review_response.md`, `task_012_hardening_response_request.md`, and `task_012_final_approval.md`.

Preserved:
- 100% backward compatibility: Existing recipes with string selectors or dict targets function identically.
- Zero Live Profile Pollution: Automated test runners strictly blocked from connecting to port 17082.

## Validation Evidence

| Check | Command | Context | Exit | Outcome | Evidence Path |
| :--- | :--- | :--- | :---: | :--- | :--- |
| Task-012 Self-Healing Suite | `python3 -m pytest tests/test_recipe_self_healing.py -v` | Ephemeral sandbox | 0 | 12/12 passed | `tests/test_recipe_self_healing.py` |
| Full Repository Regression | `python3 -m pytest tests/ -v` | Ephemeral sandbox | 0 | 133/133 passed | `tests/` |
| Whitespace Hygiene | `git diff --check` | Repository worktree | 0 | Clean | Worktree root |
| Semantic Record Linter | `python3 scripts/lint_communication_records.py .agents/communication/results/task-012-self-healing-and-alternate-transitions.md` | Protocol validator | 0 | Validated | `scripts/lint_communication_records.py` |
| ChatGPT Web Review (Turn 1) | `chatgpt-oracle` (Tab `6aaeab64`) | GPT-5.6 Sol Thinking High | 0 | CHANGES REQUIRED (6 criteria) | `.agents/communication/consultations/task_012_review_response.md` |
| ChatGPT Web Review (Turn 2) | `chatgpt-oracle` (Tab `6aaeab64`) | GPT-5.6 Sol Thinking High | 0 | APPROVED (All criteria PASS) | `.agents/communication/consultations/task_012_final_approval.md` |
| UNKNOWN_SIDE_EFFECT Terminal Test | `pytest tests/test_recipe_self_healing.py -k test_unknown_side_effect_never_detours` | Mock browser sandbox | 0 | 1 passed, 0 dispatch | `tests/test_recipe_self_healing.py` |
| Risk-Aware Fallback Test | `pytest tests/test_recipe_self_healing.py -k test_r3_r4_low_confidence_candidate_disqualified` | Mock browser sandbox | 0 | 1 passed | `tests/test_recipe_self_healing.py` |
| Detour Risk Envelope Test | `pytest tests/test_recipe_self_healing.py -k test_detour_preserves_risk_envelope` | Graph path planner | 0 | 1 passed | `tests/test_recipe_self_healing.py` |
| Bounded Detour & History Test | `pytest tests/test_recipe_self_healing.py -k test_detour_bounded_and_accumulates_failed_edges` | Workflow engine | 0 | 1 passed | `tests/test_recipe_self_healing.py` |
| Stale Repair Rejection Test | `pytest tests/test_recipe_self_healing.py -k test_stale_repair_candidate_rejected` | Recipe store CAS | 0 | 1 passed | `tests/test_recipe_self_healing.py` |
| Safety Invariant Check | `pytest -k guard_live_profile` | conftest socket guard | 0 | 0 live profile access | `tests/conftest.py` |
