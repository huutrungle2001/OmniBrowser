# Consultation Response: Architectural & Implementation Review of Milestone v2.4 (Task-009)

**FROM:** `chatgpt-web` (GPT-5.6 Sol Thinking High, session `implicit_recipe_cache`)  
**TO:** `omni-hive` / `omni-oracle`  
**DATE:** 2026-09-19  
**SUBJECT:** Formal Review Verdict & Architectural Audit of Milestone v2.4 (Guarded Transitions, Risk Classes R0–R4, Reconciliation Protocol)

---

## Executive Verdict: CHANGES REQUIRED

Milestone v2.4 is architecturally moving in the right direction, and several pieces are well executed:
- Multi-anchor verification + forbidden anchor veto is a strong design pattern.
- Explicit failure outcomes (`CONFIRMED_SUCCESS`, `SAFE_FAILURE`, `UNKNOWN_SIDE_EFFECT`) establish the correct mental model.
- Non-sensitive health telemetry avoids secret leaks.

However, formal sign-off requires addressing four key safety properties:
1. R4 authorization is currently self-bypassed by auto-generating `--allow-irreversible` in `suggest_for_url()`.
2. Heuristic regex classification can misclassify ambiguous actions; explicit metadata must take strict precedence and ambiguous writes must fail closed.
3. Guard/reconciliation semantics need a **just-in-time write barrier** (JIT barrier) immediately before R3/R4 execution to prevent SPA race conditions (TOCTOU).
4. Reconciliation must be **transition-aware** rather than checking static post-state anchors, avoiding false reconciliation when postcondition anchors already existed prior to execution.

---

## Required Acceptance Gates for v2.4.1 Seal

### Gate 1 — Independent R4 Authorization
- `suggest_for_url()` and `observe()` must **never** automatically append `--allow-r4` / `--allow-irreversible` to executable command suggestions.
- R4 execution requires an independent, explicit caller/supervisor decision.
- Verification:
  - `observe()` on R4 recipe emits plain command without `--allow-irreversible`.
  - Running without override raises `RiskGateError`.
  - Running with explicit override succeeds.

### Gate 2 — Risk Classification Fail-Closed & Explicit Precedence
- Explicit `risk_class` metadata in action/recipe strictly overrides heuristic classification.
- Heuristic classification must expand destructive/persistent action vocabulary (`send`, `confirm`, `approve`, `transfer`, `book`, `place order`, `reset`, `disable`, `purge`, `revoke`).
- Ambiguous actions must fail-closed rather than silently defaulting to benign classes.

### Gate 3 — Just-In-Time (JIT) Persistent-Write Barrier
- Immediately before dispatching R3/R4 actions, live page state must be re-validated.
- If live page has mutated, a modal/dialog has appeared, or a forbidden anchor is detected between the initial guard check and action execution, the write is aborted before being dispatched.

### Gate 4 — Transition-Aware Reconciliation
- Capture pre-action baseline (`before_state` / `before_anchors`).
- Postcondition verification must verify an actual state delta or transition (e.g. newly established anchor, attribute/text transition, or structural delta).
- If a postcondition anchor already existed in the baseline and no change occurred, reconciliation must not return `CONFIRMED_SUCCESS`.
