# Task: task-012-self-healing-and-alternate-transitions

RECORD_TYPE: TASK
RECORD_ID: task-012-self-healing-and-alternate-transitions
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-19T18:05:00Z
UPDATED_AT: 2026-09-19T18:05:00Z
FROM: hub
TO: implementer

## Objective

Implement Milestone v2.6 of OmniBrowser: **Self-Healing Anchor Bundles, Deterministic Local Repair & Alternate Transition Discovery**.

In dynamic web applications, UI drift (CSS class shuffling, DOM re-structuring, test-id renames, text modifications) inevitably breaks naive single-selector automation macros. Furthermore, existing naive "self-healing" frameworks suffer from severe architectural anti-patterns:
1. Mutating canonical memory on the fly during unverified live runs without regression gating.
2. Blindly clicking `.first()` or low-confidence fuzzy elements on mutating actions ($R3/R4$), causing catastrophic side effects.
3. Inability to distinguish between minor UI drift (same state transition contract) and business-flow structural changes (new required authentication, modals, or payment gates).

OmniBrowser v2.6 solves this by introducing a strict dual-plane model: **Online Deterministic Resolution & Offline Learner**, powered by multi-candidate **Anchor Bundles** and **State-Transition Graph (STG) Alternate Route Discovery**.

Core Requirements:
1. **Scored Candidate Bundle (`AnchorBundle`)**:
   - Replace rigid single target locators with multi-candidate `AnchorBundle` containing 3–6 scored `AnchorCandidate` entries.
   - Supported candidate kinds: `test_attr`, `role_name`, `label_input`, `scoped_css`, `neighborhood`, and `text`.
   - Implement dynamic confidence scoring:
     $$\text{Confidence} = 0.30 \times U + 0.25 \times S + 0.20 \times M + 0.10 \times Q + 0.10 \times H + 0.05 \times A - P$$
     where $U$ is runtime uniqueness, $S$ stability, $M$ semantic strength, $Q$ scope quality, $H$ historical success, $A$ actionability, and $P$ dynamic penalties.
   - Enforce Uniqueness Safety Invariant:
     - `count == 0` $\rightarrow$ `AnchorNotFound` (try next candidate).
     - `count > 1` $\rightarrow$ `AnchorAmbiguous` (disqualified immediately; `.first` is strictly forbidden for mutating steps).
2. **Deterministic Online Local Repair**:
   - Walk the candidate bundle in descending confidence order.
   - Confidence Zoning:
     - $\text{Confidence} \ge 0.92$: Auto-resolve and dispatch action.
     - $0.75 \le \text{Confidence} < 0.92$: Dispatch action ONLY if pre/post-conditions match; generate a `RepairCandidate`.
     - $\text{Confidence} < 0.75$ or state contract mismatch: Escalate / abort transition.
   - **Zero Online Canonical Mutation Invariant**: Online repair must NEVER mutate the canonical recipe file in `RecipeStore`. The canonical recipe remains immutable during runtime.
3. **Repair Candidate Pipeline (`RepairCandidate`)**:
   - Whenever an alternate candidate is successfully used to heal a step, record a `RepairCandidate` event with `recipe_id`, `step_index`, `broken_candidate`, `healed_candidate`, `confidence`, `timestamp`.
   - Persist repair events via `RecipeStore.record_repair_candidate()`.
4. **Alternate Transition Discovery via STG**:
   - If a recipe or edge fails all candidates or transitions to an unexpected state, `WorkflowEngine` queries `StateTransitionGraph.find_alternate_path(current_state, target_state, excluded_edges=[failed_edge])`.
   - If an alternate route exists (e.g. bypassing a broken direct shortcut via a secondary navigation route), `WorkflowEngine` dynamically switches to the detour plan without aborting the overarching user workflow.
5. **CLI Integration**:
   - Subcommands: `recipe repair list`, `recipe repair inspect <candidate_id>`, and `recipe repair apply <candidate_id>`.
   - Real-time healing output in `recipe run` and `workflow run` (e.g. `[HEALED] Step 2: role_name fallback (conf: 0.91)`).

## Scope

### Owned:
1. `src/browser_core/contracts.py`:
   - Data models: `AnchorCandidate`, `AnchorBundle`, `RepairCandidate`, `ConfidenceScore`, `HealResult`.
2. `src/browser_core/recipes.py`:
   - Extend `RecipeStep` to support `target: str | AnchorBundle`.
   - Implement `AnchorResolver` (bundle evaluation, uniqueness checks, confidence calculation).
   - Implement `RecipeStore.record_repair_candidate()`, `get_repair_candidates()`, `apply_repair_candidate()`.
   - Update `RecipeEngine.execute()` to support transparent online healing without canonical mutation.
3. `src/browser_core/workflows.py`:
   - Extend `WorkflowEngine.execute()` to trigger STG alternate route discovery (`StateTransitionGraph.find_alternate_path()`) when an intermediate edge encounters an unrecoverable selector or execution failure.
4. `scripts/cdp_controller.py`:
   - Add `recipe repair` CLI subcommands (`list`, `inspect`, `apply`).
   - Surface healing notifications during step execution.
5. `tests/test_recipe_self_healing.py`:
   - Unit and ephemeral integration tests verifying bundle resolution, ambiguity disqualification, zero online canonical mutation, repair candidate creation, offline repair application, and STG alternate transition detours.

### Preserved:
- 100% backward compatibility: Existing recipes specifying simple string selectors or `target_role` continue to function seamlessly without modification.
- Zero Live Profile Pollution: Automated tests strictly blocked from connecting to port 17082.

## Acceptance Criteria

1. **Candidate Bundle Evaluation & Ambiguity Safety**:
   - Resolves multi-candidate bundles in descending score order.
   - If a candidate matches multiple DOM elements (`count > 1`), it is immediately rejected as ambiguous and never clicked.
2. **Zero Online Canonical Mutation**:
   - Executing a recipe with healed selector drift succeeds in the browser, but the recipe JSON on disk maintains identical content and revision.
   - A `RepairCandidate` record is generated and stored in the repair ledger.
3. **Offline Repair Promotion (`recipe repair apply`)**:
   - Applying an approved repair candidate updates the recipe in `RecipeStore` through standard CAS, incrementing `generation` and resetting evidence counters.
4. **STG Alternate Transition Detour**:
   - When an edge fails in a multi-step workflow, `WorkflowEngine` computes an alternate path avoiding the broken edge and completes the overarching workflow.
5. **Quality & Verification**:
   - Unit and integration tests pass with exit code 0.
   - Full regression suite passes with exit code 0.
   - Whitespace hygiene clean (`git diff --check`).
   - Formal review submitted to ChatGPT Web and approved.

## Validation

1. `python3 -m pytest tests/test_recipe_self_healing.py -v` passes with exit code 0.
2. Full regression suite `python3 -m pytest tests/ -v` passes with exit code 0.
3. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-012-self-healing-and-alternate-transitions.md` passes.
4. Formal review verdict from ChatGPT Web: `APPROVED`.
