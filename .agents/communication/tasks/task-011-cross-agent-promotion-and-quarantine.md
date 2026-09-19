# Task: task-011-cross-agent-promotion-and-quarantine

RECORD_TYPE: TASK
RECORD_ID: task-011-cross-agent-promotion-and-quarantine
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-19T16:48:00Z
UPDATED_AT: 2026-09-19T16:48:00Z
FROM: hub
TO: implementer

## Objective

Implement Milestone v2.5.x of OmniBrowser: **Cross-Agent Promotion, Quarantine & Shared Cache Governance**.

In a distributed multi-agent system where multiple subagents and background sessions concurrently navigate, record, and execute browser recipes, procedural memory cannot remain a passive static store. Blind execution of unverified draft traces across agents risks widespread failures, while concurrent telemetry writes can overwrite updates. Furthermore, stale or broken recipes must be automatically quarantined before they degrade agent performance.

Core Requirements:
1. **Evidence-Based Promotion Lifecycle**:
   - Model the full recipe lifecycle states: `DRAFT` $\rightarrow$ `VERIFIED_LOCAL` $\rightarrow$ `VERIFIED_SHARED` $\rightarrow$ `CURATED`.
   - Implement evidence thresholds requiring independent sessions and agents (not mere repetition counts in a single session) to graduate recipes:
     - `DRAFT` $\rightarrow$ `VERIFIED_LOCAL`: $\ge 2$ successful replays.
     - `VERIFIED_LOCAL` $\rightarrow$ `VERIFIED_SHARED`: $\ge 5$ successes across $\ge 3$ independent sessions with 0 structural failures.
     - `VERIFIED_SHARED` $\rightarrow$ `CURATED`: $\ge 20$ executions across $\ge 3$ distinct agents with success rate $\ge 0.95$.
   - Unpromoted `DRAFT` recipes are strictly restricted to local inspection/testing and NEVER suggested for shared cross-agent fast-path execution.
2. **Automated Degradation & Quarantine Engine**:
   - Lifecycle degradation states: `SUSPECT` $\rightarrow$ `QUARANTINED` $\rightarrow$ `ARCHIVED`.
   - Automatic triggers for quarantine:
     - $\ge 3$ consecutive execution failures.
     - Any single `UNKNOWN_SIDE_EFFECT` on a persistent mutation (R3/R4).
     - Health score falling below threshold ($< 0.60$).
   - Quarantine invariant: Quarantined recipes are immediately excluded from `suggest_for_url()`, `observe()`, and `WorkflowComposer` graph path planning.
3. **Optimistic Concurrency & Revision CAS (Compare-And-Swap)**:
   - Recipes track `revision: int` and `generation: int`.
   - Concurrent updates to telemetry, health stats, and lifecycle status must verify monotonic revisions. Conflicts must trigger merge reconciliation rather than destructive overwrites.
4. **Utility-Score (U-Score) Eviction & Archiving**:
   - Implement multi-factor utility scoring:
     $U(R) = \frac{\text{Frequency} \times \text{Reliability} \times \text{RecomputeCost} \times \text{RecencyDecay}}{\text{StorageCost}}$
   - Low-utility drafts and transient flight recordings are aggressively pruned/evicted under budget constraints, while canonical recipes are gracefully archived instead of permanently deleted.
5. **CLI Integration**:
   - Subcommands: `recipe promote`, `recipe quarantine`, `recipe restore`, and `recipe lifecycle`.
   - Expose lifecycle status in `recipe list`, `recipe show`, and `observe` suggestions.

## Scope

### Owned:
1. `src/browser_core/contracts.py`:
   - Data models for `RecipeLifecycleState` (`DRAFT`, `VERIFIED_LOCAL`, `VERIFIED_SHARED`, `CURATED`, `SUSPECT`, `QUARANTINED`, `ARCHIVED`), `LifecycleRecord`, `PromotionPolicy`, `QuarantineTrigger`, and `UtilityScore`.
2. `src/browser_core/recipes.py`:
   - Extend `Recipe` and `RecipeStore` with lifecycle governance, revision tracking (CAS), promotion evaluation, quarantine enforcement, and U-score calculation.
   - Filter `suggest_for_url` and `match_page_state` to exclude unverified drafts and quarantined recipes from active suggestions.
3. `src/browser_core/workflows.py`:
   - Update `StateTransitionGraph.ingest_recipe` and `WorkflowComposer.plan` to ignore quarantined and suspect transition edges.
4. `scripts/cdp_controller.py`:
   - CLI subcommands for lifecycle inspection, manual promote, and manual quarantine/restore.
5. `tests/test_recipe_governance.py`:
   - Unit and ephemeral integration tests verifying promotion criteria, automatic quarantine upon failures/unknown effects, CAS concurrency, graph exclusion, and U-score eviction.

### Preserved:
- 100% backward compatibility with all existing CLI commands (`goto`, `eval`, `list-tabs`, `observe`, `act`, `recipe list`, `recipe run`, `workflow plan`, `workflow run`).
- Zero Live Profile Pollution: Automated tests strictly blocked from connecting to port 17082.

## Acceptance Criteria

1. **Lifecycle Progression & Access Control**:
   - `DRAFT` recipes are only visible locally and cannot be suggested or executed by shared agents.
   - Promotion strictly validates session and agent independence requirements before advancing state.
2. **Automated Quarantine Invariant**:
   - A recipe encountering $\ge 3$ consecutive failures or an `UNKNOWN_SIDE_EFFECT` is immediately moved to `QUARANTINED`.
   - Quarantined recipes are completely excluded from `suggest_for_url`, `observe` outputs, and workflow composition.
3. **CAS Concurrency Safety**:
   - Concurrent updates with stale revisions fail or merge without data loss.
4. **CLI Lifecycle Commands**:
   - `recipe lifecycle <recipe_id>` outputs detailed lifecycle progression, telemetry evidence, and quarantine status.
   - `recipe promote <recipe_id>` and `recipe quarantine <recipe_id>` allow explicit administrative governance.
5. **Quality & Verification**:
   - Unit and integration tests pass with exit code 0.
   - Full regression suite passes with exit code 0.
   - Whitespace hygiene clean (`git diff --check`).
   - Formal review submitted to ChatGPT Web and approved.

## Validation

1. `python3 -m pytest tests/test_recipe_governance.py -v` passes with exit code 0.
2. Full regression suite `python3 -m pytest tests/ -v` passes with exit code 0.
3. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-011-cross-agent-promotion-and-quarantine.md` passes.
4. Formal review verdict from ChatGPT Web: `APPROVED`.
