# Task: task-010-state-transition-graph-and-workflow-composition

RECORD_TYPE: TASK
RECORD_ID: task-010-state-transition-graph-and-workflow-composition
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-19T15:00:00Z
UPDATED_AT: 2026-09-19T15:00:00Z
FROM: hub
TO: implementer

## Objective

Implement Milestone v2.5 of OmniBrowser: **State-Transition Graph (STG) & Workflow Composition**.

Monolithic long-journey recipes are brittle and dangerous in web automation: if UI shifts at step 15 of 20, the entire macro fails and retry semantics risk duplicate persistent side effects. Instead, browser automation should decompose long flows into modular, guarded state transitions ($S_i \xrightarrow{R} S_j$) and compose them dynamically into verifiable execution graphs.

Core Requirements:
1. **Semantic State Nodes ($S$)**: Model browser states as semantic nodes defined by normalized domain, route pattern, and required/fingerprint anchors.
2. **Guarded Transition Edges ($E$)**: Represent recipes as directed edges between states with associated risk class ($R0$–$R4$), precondition guards, and postcondition transitions.
3. **State-Transition Graph Engine (`StateTransitionGraph`)**:
   - Maintain a domain-partitioned graph of states and transitions.
   - Automatically ingest registered recipes and distilled flight logs into the transition graph.
   - Path-finding algorithm (Dijkstra / BFS / A*) to discover optimal execution plans from current page state to target goal state.
4. **Composition Safety Invariant (ChatGPT Mandate)**:
   - A composed workflow must **never** weaken or bypass the per-edge $R0$–$R4$ safety guards, JIT write barriers, or reconciliation protocols established in Milestone v2.4.
   - Any workflow containing an R4 edge requires explicit caller/supervisor permission (`allow_r4=True`).
   - If an edge produces `UNKNOWN_SIDE_EFFECT`, workflow execution immediately halts with `UnknownSideEffectError` (no blind progression).
   - Each edge must verify its postcondition and state transition before the subsequent edge is allowed to execute.
5. **Workflow Execution Engine & CLI**:
   - CLI subcommands: `cdp_controller.py workflow plan` and `cdp_controller.py workflow run`.
   - Structural reporting of composed workflow execution (steps completed, edge transitions verified, cumulative risk).

## Scope

### Owned:
1. `src/browser_core/contracts.py`:
   - Data models for `WorkflowSpec`, `WorkflowStep`, `WorkflowExecutionResult`, `SemanticStateNode`, and `TransitionEdge`.
2. `src/browser_core/workflows.py`:
   - Graph representation (`StateTransitionGraph`).
   - State identification and node resolution from live DOM tree.
   - Path-finding and composition planner (`WorkflowComposer`).
   - Execution runner (`WorkflowEngine`) with edge-by-edge guarded transitions and fail-closed halting.
3. `scripts/cdp_controller.py`:
   - Add CLI subparser for `workflow` (`plan`, `run`, `graph`).
4. `tests/test_workflow_composition.py`:
   - Comprehensive unit and ephemeral integration tests covering state graph discovery, multi-edge composition, cumulative risk calculation, JIT barrier preservation, and fail-closed edge interruption.

### Preserved:
- 100% backward compatibility for all existing commands (`observe`, `act`, `recipe list`, `recipe run`).
- Zero live profile pollution: All tests must run against isolated ephemeral Chrome instances (port 17082 strictly blocked).

## Acceptance Criteria

1. **Semantic State Modeling**:
   - `SemanticStateNode` uniquely identifies distinct application states (e.g. `login_page`, `dashboard`, `settings`, `checkout_step_1`).
   - Resolves live DOM observation to the best matching state node based on route and anchor fingerprints.
2. **Graph Composition & Path Finding**:
   - Given a start state and target goal state, finds the shortest/lowest-risk path through available recipes.
   - Computes cumulative workflow risk (maximum risk across all traversed edges).
3. **Strict Safety Preservation Across Workflow Edges**:
   - If any edge in the planned path is R4, the workflow cannot execute without explicit `--allow-irreversible` / `allow_r4=True`.
   - The JIT Persistent-Write Barrier executes immediately before any mutating R3/R4 edge in the workflow.
   - If an edge fails with `UNKNOWN_SIDE_EFFECT`, subsequent edges are NEVER executed.
   - State transition verification runs after each edge: edge $k+1$ is only dispatched if the page successfully transitioned to state $S_{k+1}$.
4. **CLI Integration**:
   - `workflow plan --from <url> --to <target_state>` outputs structured JSON plan with edges and cumulative risk.
   - `workflow run <plan_or_file>` executes the sequence against active browser tab with detailed per-edge outcome telemetry.
5. **Quality & Verification**:
   - Integration tests pass with exit code 0 against ephemeral Chrome sandbox.
   - Whitespace hygiene: `git diff --check` passes with exit code 0.
   - Formal review submitted to ChatGPT Web and approved.

## Validation

1. `python3 -m pytest tests/test_workflow_composition.py -v` passes with exit code 0.
2. Full regression suite `python3 -m pytest tests/ -v` passes with exit code 0.
3. `python3 scripts/lint_communication_records.py .agents/communication/tasks/task-010-state-transition-graph-and-workflow-composition.md` passes.
4. Formal review verdict from ChatGPT Web: `APPROVED`.
