# Architectural & Code Review Request: Task-010 (Milestone v2.5: State-Transition Graph & Workflow Composition)

## 1. Context & Objective
Following your formal approval of Task-008 and Task-009 (Guarded State Transitions & Risk Classes R0–R4), we have implemented **Milestone v2.5: State-Transition Graph & Workflow Composition**.

The goal is to solve the brittleness of long-journey monolithic browser macros. OmniBrowser now decomposes browser journeys into directed state-transition graphs ($S_i \xrightarrow{R} S_j$), dynamically discovers optimal (lowest-cost / lowest-risk) execution paths using Dijkstra's algorithm, and executes composed multi-edge workflows under strict fail-closed safety invariants.

## 2. Core Modules & Implementation Details

### A. Data Contracts (`src/browser_core/contracts.py`)
- `SemanticStateNode`: Identifies distinct application states by `domain`, `route_pattern`, `required_anchors`, and `semantic_fingerprint`.
- `TransitionEdge`: Directed recipe transition between states, tagged with `recipe_id`, `risk_class` ($R0$–$R4$), `cost`, and `required_params`.
- `WorkflowPlan`: Composed execution plan containing ordered `edges`, `cumulative_risk` (maximum risk rank across all traversed edges), and `estimated_steps`.
- `WorkflowExecutionResult`: Comprehensive execution telemetry reporting `ok`, `completed_edges`, `total_edges`, `current_state`, `cumulative_risk`, `outcome` (`CONFIRMED_SUCCESS`, `SAFE_FAILURE`, `UNKNOWN_SIDE_EFFECT`), and `edge_results`.
- `WorkflowInterruptedError`: Typed exception raised when execution halts on an edge failure.

### B. Graph & Composition Engine (`src/browser_core/workflows.py`)
1. **`StateTransitionGraph`**:
   - Ingests registered recipes from `RecipeStore`.
   - Synthesizes / maps semantic state nodes from recipe domain patterns, preconditions, and postconditions.
   - Computes edge transition costs based on risk weighting:
     $\text{Cost} = 1.0 + \text{RiskCostWeight}(R) + 0.2 \times \text{StepCount}$.
   - `resolve_live_state(page_url, tree_nodes)`: Resolves live DOM observation to the best matching `SemanticStateNode` via route pattern and `MatcherSpec`.
2. **`WorkflowComposer`**:
   - Computes lowest-cost execution paths from `start_state` to `goal_state` using Dijkstra's algorithm.
   - Prunes candidate edges exceeding `max_allowed_risk` (e.g. preventing R4 or R3 edges when restricted to R2).
   - Computes `cumulative_risk` across the chosen path.
3. **`WorkflowEngine`**:
   - **Composition Safety Invariant 1 (R4 Gate)**: Inspects plan-level and edge-level risk. If any edge requires R4, execution is blocked with `RiskGateError` unless `allow_r4=True` is explicitly authorized.
   - **Composition Safety Invariant 2 (JIT Persistent-Write Barrier)**: Delegates each edge to `RecipeEngine`, which enforces pre-dispatch modal checks and live region verification prior to any mutating step.
   - **Composition Safety Invariant 3 (Immediate Halt on UNKNOWN_SIDE_EFFECT)**: If an intermediate edge experiences unpredictable DOM drift or post-mutation failure, execution immediately halts with `outcome=UNKNOWN_SIDE_EFFECT`. Subsequent edges are NEVER executed.
   - **Composition Safety Invariant 4 (Post-Edge State Transition Verification)**: After edge $k$ completes, live page observation verifies that target state $S_{k}$ satisfies its `route_pattern` and `required_anchors`. Edge $k+1$ is only dispatched if the transition is confirmed.

### C. CLI Integration (`scripts/cdp_controller.py`)
- `cdp_controller.py workflow graph [--domain <domain>]`: Dumps graph topology in compact JSON.
- `cdp_controller.py workflow plan --from <start> --to <goal> [--max-risk <R0-R4>]`: Computes lowest-cost path with cumulative risk.
- `cdp_controller.py workflow run <plan.json> [--allow-irreversible] [--params <json>]`: Dispatches composed execution against live browser tab with full per-edge telemetry.

## 3. Validation & Testing Evidence
- **Test Suite**: `tests/test_workflow_composition.py` (9/9 tests pass in 1.61s):
  1. `test_workflow_contracts`: Data serialization & backward compatibility aliases (`WorkflowSpec`, `WorkflowStep`).
  2. `test_graph_ingestion_and_edge_costs`: Store ingestion and risk-cost weighting.
  3. `test_resolve_live_state`: Live DOM tree resolution to semantic state nodes.
  4. `test_workflow_composer_dijkstra`: Multi-hop path finding, risk pruning, and cumulative risk tracking.
  5. `test_workflow_engine_r4_gating`: Gating R4 execution behind explicit authorization.
  6. `test_workflow_engine_unknown_side_effect_halts_immediately`: Composition safety invariant verifying subsequent edges are never called upon unknown side effect.
  7. `test_workflow_engine_post_edge_state_verification_failure`: Halting on missing target state anchors.
  8. `test_workflow_ephemeral_browser_integration`: End-to-end multi-step interactive workflow against isolated ephemeral Chrome sandbox.
  9. `test_workflow_cli_commands`: CLI graph and plan execution.
- **Full Repository Regression Suite**:
  - `python3 -m pytest tests/`: **102 passed, 0 failed** across all modules.
- **Safety Invariant**: Zero connection to port 17082 in automated tests.
- **Whitespace Hygiene**: `git diff --check` passes with exit code 0.
- **Git Commit**: `ffd90db` (`feat(workflows): implement state-transition graph and workflow composition (Milestone v2.5)`).

## 4. Review Scope & Gates
Please review the architectural design and code diff for Milestone v2.5 against our safety invariants:
1. Is the composition model mathematically and operationally sound?
2. Does the workflow engine guarantee that no subsequent destructive actions can be triggered if an intermediate edge encounters an `UNKNOWN_SIDE_EFFECT` or state mismatch?
3. Are the R0–R4 safety gates and JIT write barriers fully preserved under workflow composition?

Please render your formal review verdict (`APPROVED` or `CHANGES REQUIRED` with specific criteria).
