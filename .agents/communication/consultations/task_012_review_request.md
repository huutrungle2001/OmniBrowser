# Architectural & Code Review Request: Task-012 (Milestone v2.6: Self-Healing Anchor Bundles & Alternate Transition Discovery)

Commit: `8fd4ae2`
Scope: Self-Healing Anchor Bundles, Deterministic Local Repair & STG Alternate Route Discovery

## 1. Context & Objective
Following your formal approval of Task-011 (Milestone v2.5.x: Cross-Agent Promotion, Quarantine & Shared Cache Governance, commit `94e6564`), we have implemented **Milestone v2.6: Self-Healing Anchor Bundles, Deterministic Local Repair & Alternate Transition Discovery**.

In dynamic web applications, UI drift (CSS class changes, attribute updates, accessible name variations) frequently breaks single-locator automation recipes. Existing self-healing automation systems frequently introduce dangerous anti-patterns:
1. Online in-place mutation of canonical recipes during unverified live executions.
2. Indiscriminate `.first()` clicking or low-confidence fuzzy element matching on mutating actions ($R3/R4$), causing catastrophic side effects.
3. Conflating minor DOM drift with structural business-flow changes.

To solve this, OmniBrowser v2.6 introduces an **Online Deterministic Resolver & Offline Learner** architecture powered by multi-candidate **Anchor Bundles** and **State-Transition Graph (STG) Detour Routing**.

---

## 2. Core Architectural Implementation

### 2.1 Scored Candidate Bundle (`AnchorBundle`)
Instead of brittle single locators, `RecipeStep.target` supports `AnchorBundle`, housing 3–6 scored `AnchorCandidate` entries:
- **Supported Kinds**: `test_attr`, `role_name`, `label_input`, `scoped_css`, `neighborhood`, `text`.
- **Confidence Scoring Prior**:
  - Unique `data-testid`/`data-qa`: 0.95
  - Unique `role` + accessible name: 0.91
  - `label` + input `name`/`type`: 0.91
  - Proved stable ID / scoped CSS: 0.84
  - Neighborhood anchor: 0.70
  - Text-only: 0.60
- **Candidate Bundle Ambiguity Safety Invariant**:
  When evaluating candidates:
  - `count == 0`: Not found $\rightarrow$ advance to next candidate in bundle.
  - `count > 1`: Ambiguous $\rightarrow$ **strictly disqualified**. Under no circumstances is `.first()` clicked on mutating steps!
  - `count == 1`: Unambiguous match $\rightarrow$ candidate selected for dispatch.

### 2.2 Deterministic Online Local Repair & Zero Canonical Mutation
- The `AnchorResolver` walks the candidate bundle in descending score order.
- When selector drift occurs (e.g. `data-testid` renamed or absent), the engine transparently resolves the action via a secondary candidate (e.g. `role_name`).
- **Zero Online Canonical Mutation Invariant**:
  - Online execution **never mutates** the canonical recipe file in `RecipeStore`. The on-disk recipe retains identical revision, generation, content, and file digest.
  - Instead, the engine captures a `RepairCandidate` event:
    `{id, recipe_id, step_index, broken_candidate, healed_candidate, confidence, context, timestamp}`
  - The `RepairCandidate` is appended to the store's durable repair ledger (`.repairs.jsonl`).

### 2.3 Offline Repair Promotion (`recipe repair apply`)
- Canonical memory changes occur strictly via offline administrative governance.
- Running `recipe repair apply <repair_id>` updates the recipe step so that the healed candidate becomes the primary anchor (`candidates[0]`), increments `generation`, records to `audit_log`, resets promotion evidence to `VERIFIED_LOCAL`, and persists through CAS linearizability.

### 2.4 STG Alternate Route Discovery & Detours
- When an edge fails unrecoverably (e.g. all candidates in an anchor bundle fail, or an unknown side-effect occurs), `WorkflowEngine` queries `StateTransitionGraph.find_alternate_path(current_state, goal_state, excluded_edge_ids={failed_edge})`.
- If an alternate route exists (e.g. bypassing a broken direct shortcut via a secondary navigation route), `WorkflowEngine` dynamically switches to the detour plan without aborting the overarching user workflow.

---

## 3. Validation & Test Evidence

- **Unit & Ephemeral Integration Tests (`tests/test_recipe_self_healing.py`)**:
  - `test_anchor_bundle_primary_success`: Primary candidate matches unique element and executes without repair logging.
  - `test_anchor_bundle_heals_drift_via_secondary_candidate`: Drift healed via fallback candidate; `RepairCandidate` recorded.
  - `test_anchor_ambiguity_disqualified`: Ambiguous candidates (`count > 1`) disqualified immediately.
  - `test_zero_online_canonical_mutation`: Recipe on disk verified byte-for-byte and revision-for-revision identical after online healing.
  - `test_offline_repair_application`: `apply_repair_candidate()` promotes healed anchor, increments generation, and resets evidence.
  - `test_workflow_stg_alternate_detour`: Broken edge bypassed via Dijkstra alternate route; workflow completed successfully.
  - `test_anchor_resolver_edge_cases`: Edge case validation for labels, text, and `AnchorNotFound` exhaustion.
  - **Result**: **7/7 passed** in 11.62s.
- **Full Repository Regression**:
  - **128 passed, 0 failed** in 113.62s with zero live profile pollution.
- **Whitespace & Protocol Linter**:
  - `git diff --check`: Clean.
  - `scripts/lint_communication_records.py`: All records valid.

---

## 4. Questions for the Oracle

1. Does the dual-plane separation between online deterministic resolution (with zero canonical mutation) and offline repair promotion provide a fail-closed, tamper-resistant boundary for procedural memory?
2. Is the candidate ambiguity disqualification rule (`count > 1` strictly skipped) sufficient to prevent accidental side effects during UI drift?
3. Does the STG alternate transition detour mechanism maintain workflow correctness without risking circular state loops?
4. Please render your formal review verdict (`APPROVED` or `CHANGES REQUIRED` with specific criteria).
