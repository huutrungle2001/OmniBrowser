# Formal Hardening Evidence & Review Sign-off Request: Task-012 (Milestone v2.6)

Commit: `107e3ce`
Scope: Self-Healing Anchor Bundles, Deterministic Local Repair & STG Alternate Route Discovery Hardening

In direct response to your architectural review (`task_012_review_response.md`), we have satisfied all 6 required acceptance criteria with comprehensive implementations and regression tests:

### 1. `UNKNOWN_SIDE_EFFECT` is Strictly Terminal
- `WorkflowEngine.execute`: When an edge returns `ExecutionOutcome.UNKNOWN_SIDE_EFFECT` or raises `UnknownSideEffectError`, detour route finding is strictly suppressed (`find_alternate_path` is never called).
- Execution halts immediately with 0 detours and 0 subsequent edges dispatched.
- Verified in `test_unknown_side_effect_never_detours`: browser dispatch count remains 0 for all subsequent steps.

### 2. R3/R4 Risk-Aware Candidate Fallback
- `AnchorResolver.resolve`: Accept `risk_class: RiskClass | str`. Enforces that `count == 1` establishes DOM uniqueness, NOT authorization to mutate high-risk targets.
- For R3 (Irreversible State): Candidate score must be $\ge 0.80$; text-only candidates (`candidate.kind == "text"`) are strictly disqualified.
- For R4 (Destructive / Financial): Candidate score must be $\ge 0.90$; text-only and generic neighborhood candidates are strictly disqualified.
- Verified in `test_r3_r4_low_confidence_candidate_disqualified`.

### 3. Detour Preserves Authorization Risk Envelope
- `StateTransitionGraph.find_alternate_path`: Accepts `max_allowed_risk: RiskClass` and `allow_r4: bool = False`. Dijkstra prunes any candidate edge exceeding `max_allowed_risk` or requiring R4 when unauthorized.
- `WorkflowEngine.execute`: Detour path planning strictly enforces original workflow's risk envelope.
- Verified in `test_detour_preserves_risk_envelope`: An R1 workflow refuses to detour through shorter R3/R4 edges.

### 4. Workflow-Global Replanning History & Bounded Recovery
- `WorkflowEngine.execute`: Maintains `failed_edges: set[str]` that accumulates across replans and is passed to `excluded_edge_ids`, preventing circular execution of broken transitions.
- Added parameter `max_detours: int = 2`. If `detour_count >= max_detours` and another edge fails, execution fails closed with `WorkflowInterruptedError(exit_code=2)`.
- Verified in `test_detour_bounded_and_accumulates_failed_edges`.

### 5. `RepairCandidate` Generation & Content Digest Binding
- `RepairCandidate`: Extended with `recipe_generation: int = 1` and `recipe_content_digest: str | None = None`.
- `RecipeEngine.execute`: Binds current recipe generation and executable content digest when capturing repair candidates.
- `RecipeStore.apply_repair_candidate`: Verifies `repair.recipe_content_digest == recipe.lifecycle.content_digest`. If mismatched, fails closed and raises `StaleRepairError(exit_code=2)`.
- Verified in `test_stale_repair_candidate_rejected`.

### 6. Full Test Suite Validation
- `tests/test_recipe_self_healing.py`: **12/12 passed** in 14.26s.
- Full repository regression: **133 passed, 0 failed** in 107.45s with 0 live profile pollution.

Please review this hardening evidence and confirm your final review verdict (`APPROVED`).
