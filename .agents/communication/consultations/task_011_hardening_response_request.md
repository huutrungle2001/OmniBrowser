# Formal Hardening Evidence & Review Sign-off Request: Task-011 (Milestone v2.5.x)

Commit: `94e6564`
Scope: Cross-Agent Promotion, Quarantine & Shared Cache Governance Hardening

In direct response to your detailed review (`task_011_review_response.md`), we have satisfied all 6 acceptance criteria with comprehensive implementation and regression tests:

### 1. CAS Linearizability
- `RecipeStore.save`: Inter-process file locking via `fcntl.flock(lf.fileno(), fcntl.LOCK_EX)` on `self.root / ".locks" / f"{recipe.id}.lock"`.
- The entire transaction (`read current disk revision` -> `compare expected_revision` -> `increment revision` -> `update U-score` -> `atomic temp write` -> `atomic rename os.replace`) occurs strictly under the flock critical section.

### 2. Real Concurrency Test (`test_cas_true_multiprocess_atomicity`)
- Two concurrent workers synchronized with a barrier attempt to commit revision 2 with `expected_revision = 2` on the exact same recipe file.
- Verified: Exactly one succeeds, exactly one receives `CASConflictError`, and final disk revision is exactly 3 ($N + 1$).

### 3. Stale-Plan Quarantine Test (`test_stale_plan_execution_blocked_after_recipe_quarantine`)
- Workflow plan composed while recipe X was healthy.
- Recipe X subsequently quarantined in store.
- When old plan is executed, `WorkflowEngine.execute` re-validates the authoritative canonical recipe state from `RecipeStore.get()`, immediately raising `QuarantinedRecipeError` before dispatching any browser operations (dispatch count == 0).

### 4. Canonical Execution Check
- `RecipeEngine.execute` and `WorkflowEngine.execute` unconditionally verify canonical state against `RecipeStore`, neutralizing stale in-memory recipe instances.

### 5. Generation-Bound Evidence (`test_generation_bound_evidence_resets_on_mutation`)
- Added `compute_recipe_content_digest` (deterministic SHA256 of executable payload).
- Added `content_digest` and `audit_log` to `LifecycleRecord`.
- Upon semantic recipe mutation, `generation` increments, `content_digest` updates, and all evidence (`sessions_seen`, `agents_seen`, `consecutive_failures`) resets to zero, dropping `VERIFIED_SHARED` / `CURATED` recipes back to `VERIFIED_LOCAL`.

### 6. Safe Restore & Privileged Promotion (`test_safe_restore_and_privileged_promotion_override`)
- `restore()` strictly resets state to `VERIFIED_LOCAL` (never directly to `VERIFIED_SHARED` or `CURATED`), requiring fresh cross-session evidence.
- `promote()` enforces `PromotionPolicy`. Bypassing policy requires `privileged=True`, explicit non-empty `actor`, and `reason`, which are recorded immutably in `LifecycleRecord.audit_log`. CLI accepts `--privileged`, `--actor`, `--reason`.

### 7. Full Test Suite Validation
- `tests/test_recipe_governance.py`: **16/16 passed** in 3.66s.
- Full repository regression: **121 passed, 0 failed** in 85.31s with 0 live profile pollution.

Please review this hardening evidence and confirm your final review verdict (`APPROVED`).
