# OmniBrowser: Autonomous Multi-Agent Architecture, Context & Protocol Guidelines

This file provides the permanent context, engineering principles, system invariants, and multi-agent protocol guidelines for the **OmniBrowser** project.

---

## 1. Core Engineering Principles

### 1.1 Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.
- **State assumptions explicitly** — If uncertain about Chrome DevTools Protocol behavior or frame lifecycles, verify via isolated test scripts first.
- **Present multiple interpretations** — Compare DOM projection approaches vs visual escalation before writing irreversible logic.
- **Push back when warranted** — If a simpler Playwright/CDP mechanism solves the problem without extra complexity, implement the simpler one.
- **Stop when confused** — Clarify CDP protocol nuances or OOPIF iframe edge cases immediately.

### 1.2 Simplicity First
Minimum code that solves the problem. Nothing speculative.
- No features beyond what was asked.
- No premature Site-Pack abstractions in v1.0. Keep `browser_core/` strictly focused on the core 4 modules (`contracts.py`, `page_manager.py`, `engine.py`, `primitives.py`).
- If 200 lines of JavaScript or Python can be 50, rewrite it.
- **The test:** Would a senior browser systems engineer find this codebase concise, robust, and readable? If no, simplify.

### 1.3 Surgical Changes & Backward Compatibility
Touch only what you must. Clean up only your own mess.
- **Zero Breaking Changes**: Existing CLI subcommands (`list-tabs`, `goto`, `eval`, `screenshot`) must continue to accept identical arguments and produce identical output formats.
- Don't refactor unrelated utility scripts or formatting.
- Remove temporary files, test dumps, or debug logs introduced during execution.

### 1.4 Goal-Driven Execution
Define success criteria. Loop until verified.
- *Instead of "Add observe command"* $\rightarrow$ *"Write a test script navigating to a complex test HTML page, assert observe returns compact JSON with opaque refs, make it pass"*.
- *Instead of "Fix stale node ref"* $\rightarrow$ *"Write a test triggering DOM mutation after navigation, verify StaleRefError triggers automatic fast re-scan, make it pass"*.
- For multi-step tasks, state a brief plan:
  1. `[Step]` $\rightarrow$ verify: `[check]`
  2. `[Step]` $\rightarrow$ verify: `[check]`
  3. `[Step]` $\rightarrow$ verify: `[check]`

---

## 2. Project Profile & Architectural Mission

- **Project Name:** `OmniBrowser`
- **Primary Objective:** Build a high-performance, token-efficient Progressive Capability browser automation engine and agent skill using native Chrome DevTools Protocol (CDP) and Playwright. Transform slow, token-bloated raw HTML / full-screen screenshot interactions into structured semantic DOM projections (`observe`), native action execution with postconditions (`act`), bounded browser code execution (`runBrowserCode`), and targeted crop visual escalation (`inspectVisual`).
- **Target Distribution Path:** Deployable and symlink-compatible with `~/.gemini/config/skills/browser-automation-cdp/`.
- **Primary Languages:** Python 3.10+, Modern JavaScript (ES2022+ injected in-browser).
- **Key Directory Structure:**
  - `src/browser_core/`: Core Python library modules (`contracts.py`, `page_manager.py`, `engine.py`, `primitives.py`).
  - `scripts/`: CLI controller (`cdp_controller.py`), DOM scanner (`dom_agent.js`), notification helper (`notify_agent.sh`), linter (`lint_communication_records.py`).
  - `tests/`: Unit tests, mock HTML fixtures, ephemeral integration test runners.
  - `.agents/`: Agent declarations (`agents/`) and durable communication records (`communication/`).

---

## 3. Unbreakable System Invariants

1. **Zero Live Profile Pollution (CRITICAL SAFETY INVARIANT)**:
   - Automated tests, CI runners, and developer test scripts **MUST NEVER** connect to or modify the user's live Chrome profile (`~/.chrome-ai-profile` on port 17082).
   - All tests must launch isolated ephemeral Chrome instances with `--user-data-dir=$(mktemp -d)` and dynamically allocated debugging ports.
2. **100% Backward Compatibility**:
   - Existing workflows relying on `cdp_controller.py list-tabs`, `goto`, `eval`, and `screenshot` must never break.
3. **Token & Payload Budget Constraint**:
   - `observe` semantic DOM trees must strictly filter invisible/redundant elements and enforce size bounds ($\le 15\text{ KB}$ JSON payload) to prevent LLM context explosion.
4. **Deterministic Sandbox Grounding**:
   - LLM code reviews cannot substitute for real execution. Implementation handoff requires exit-code 0 from actual integration tests against running Chrome instances.
5. **No Infinite Multi-Agent Debates**:
   - Maximum 3 revision cycles per task. If a task is not approved after 3 attempts, mark `STATUS: BLOCKED` and escalate to the human operator.

---

## 4. Multi-Agent System Architecture & The 4 Roles

OmniBrowser operates under a strictly coordinated, 4-agent workflow:

```text
                                [omni_hub]
                 (Master Coordinator & Operator Interface)
                                    │
                                    ▼
                          [omni_orchestrator]
                     (Task Architect & Contract Gate)
                                    │
                         tasks/<id>.md (TASK_READY)
                                    │
                                    ▼
                         [omni_implementer]
             (Codex Lead: Core Implementation & Chrome Tests)
                                    │
                        results/<id>.md (READY_FOR_REVIEW)
                                    │
                                    ▼
                           [omni_reviewer]
                 (Adversarial Audit & Regression Gate)
                                    │
            ┌───────────────────────┴───────────────────────┐
            ▼                                               ▼
         APPROVED                                   REVISION_REQUIRED
            │                                               │
   [omni_orchestrator & hub]                                [omni_implementer]
```

### Agent Roster:
1. **`omni_hub`** (Interactive Master Window): Human operator communication, milestone supervision, global progress monitoring.
2. **`omni_orchestrator`** (Task Architect): Decomposes `PLAN.md` into falsifiable `.agents/communication/tasks/<task-id>.md` specifications with owned/preserved file scopes.
3. **`omni_implementer`** (Codex Lead / Supervisory Tech Lead): Operates strictly in a Supervisory & Coordination role. MUST ALWAYS summon worker subagents (`ag/gemini-3.8-flash-high` via `spawn_agent`) to perform code writing, editing, and test authoring. Direct bulk coding on the main thread is strictly forbidden.
4. **`omni_reviewer`** (Adversarial Quality Gate): Runs in a fresh context, audits git diffs independently, tests backward compatibility, and issues binding approval.

### 4.1 Mandatory Subagent Delegation Invariant (Supervisor-Only Rule)
- **Zero Direct Coding on Main Thread**: The Implementer Lead (`omni-hub` / `omni_implementer` running `cx/gpt-5.6-terra`) is strictly an **Architect, Supervisor, and Integration Gatekeeper**. It must **NEVER** write or edit implementation code, modules, or test suites directly on its main thread.
- **Mandatory Subagent Delegation**: For **EVERY** implementation task, `omni_implementer` **MUST**:
  1. Decompose the task specification into discrete, modular sub-assignments with explicit interface boundaries and input/output contracts.
  2. Summon worker subagents (`ag/gemini-3.8-flash-high` via `spawn_agent`) to write the code, implement the modules, and create test files.
  3. Supervise subagent outputs, audit their diffs, and verify adherence to the agreed contracts.
  4. Execute final sandbox integration tests on ephemeral Chrome.
  5. Author the Result record, commit clean changes, and dispatch handoff to Reviewer.
- **Rationale**: Enforces dual-tier token economics (preserving `gpt-5.6-terra` high reasoning for planning/audit while utilizing fast, unlimited `gemini-3.8-flash` threads for code production), eliminates context pollution on the lead agent, and guarantees strict separation of concerns.

---

## 5. Operational Protocol & Mandatory Turn Handoff

### 5.1 Communication Hygiene & Canonical Roles
- **Canonical Roles**: Communication records strictly use canonical roles (`hub`, `orchestrator`, `implementer`, `reviewer`). Transport mapping to physical tmux panes (e.g. `omni-hub`, `omni_reviewer`) is handled decoupled by `scripts/notify_agent.sh`.
- **Authoritative Mechanism**: Durable Markdown records in `.agents/communication/` committed to Git are the **ONLY** source of truth. Tmux notifications are best-effort asynchronous wake-ups.
- **Zero Raw Terminal Dumps**: Communication records must use structured Markdown evidence tables summarizing command, exit code, and high-level outcome.
- **Context Resets (`/new`)**: Reviewer sessions must execute context resets (`/new`) to ensure clean-room, adversarial verification without inheriting implementer assumptions.

### 5.2 Task Immutability & Spec Drift Prevention
- **Frozen After Notification**: Once a task record (`tasks/<id>.md`) is committed and notified with `STATUS: TASK_READY`, its Acceptance Criteria and Scope are **frozen**.
- **No In-Place Spec Mutations**: If requirements, technical design, or constraints evolve during implementation, do **NOT** silently edit the existing task file. Instead:
  - Increment `ATTEMPT: 2` (or author a new task version with `SUPERSEDES: <previous-task-id>`).
  - Document the rationale in the task revision.
  - Implementer and Reviewer must align on the identical task revision to prevent **Spec Drift**.

### 5.3 Two-Lane Workflow
To avoid bureaucratic overhead while maintaining absolute safety on critical paths:
1. **Standard Lane (Core & Safety-Critical)**:
   - *Applies to:* Core engine, CDP bindings, DOM agent, contracts, security/profile sandboxing, CLI backward compatibility.
   - *Workflow:* Full 4-step cycle: `Task` $\rightarrow$ `Implementation` $\rightarrow$ `Result (with commit SHA)` $\rightarrow$ `Independent Review (fresh context)`.
2. **Trivial Lane (Low-Risk / Cosmetic)**:
   - *Applies to:* Documentation fixes, comments, typo corrections, minor test fixture text not altering contracts.
   - *Workflow:* Lightweight cycle: `Task` $\rightarrow$ `Implementation + Result commit` $\rightarrow$ Quick reviewer verification without multi-round ceremony.

### 5.4 Mandatory Handoff Routing Matrix
1. **Orchestrator** $\rightarrow$ `implementer`:
   ```bash
   scripts/notify_agent.sh -a implementer -m "Task <id> ready." -r .agents/communication/tasks/<id>.md
   ```
2. **Implementer** $\rightarrow$ `reviewer`:
   ```bash
   scripts/notify_agent.sh -a reviewer -m "Result for <id> ready for review." -r .agents/communication/results/<id>.md
   ```
3. **Reviewer**:
   - If `STATUS: REVISION_REQUIRED` $\rightarrow$ `implementer`:
     ```bash
     scripts/notify_agent.sh -a implementer -m "Revision required for <id>." -r .agents/communication/reviews/<id>.md
     ```
   - If `STATUS: APPROVED` $\rightarrow$ `orchestrator` & `hub`:
     ```bash
     scripts/notify_agent.sh -a orchestrator -m "Task <id> approved." -r .agents/communication/reviews/<id>.md
     scripts/notify_agent.sh -a hub -m "Task <id> approved." -r .agents/communication/reviews/<id>.md
     ```

### 5.5 Turn Completion Rule (No Infinite Waiting)
- Once an agent finishes sending a notification via `notify_agent.sh`, it **MUST immediately end its turn** and become inactive.
- Agents must **NEVER** poll or loop waiting for the other agent. The receiving agent will wake the sender upon completing its turn.

### 5.6 Semantic Linter Enforcement
Before every handoff, `scripts/notify_agent.sh` automatically invokes `scripts/lint_communication_records.py --handoff <record> --target <target>`:
- **Git Commit Verifiability**: All `BASE_COMMIT`, `IMPLEMENTATION_TIP`, and `REVIEWED_COMMIT` hashes must exist in Git history (`git cat-file -e`).
- **Clean Worktree**: When handing off a Result, the repository worktree must be 100% clean (no untracked or modified files).
- **Mandatory Sections**: Required sections (Objective, Scope, Acceptance Criteria, Validation, Evidence) are fatal errors if omitted.
- **Literal Key Dispatch**: `notify_agent.sh` uses `tmux send-keys -l` to prevent shell/vim escape corruption.

---

## 6. Definition of Done (DoD)

A task in OmniBrowser is complete only when:
1. Objective and acceptance criteria are 100% satisfied.
2. All unit and ephemeral integration tests pass with exit code 0.
3. Legacy CLI commands (`goto`, `list-tabs`, `eval`, `screenshot`) pass backward-compatibility verification.
4. No test touched `~/.chrome-ai-profile`.
5. Code passes formatting and whitespace checks (`git diff --check`).
6. Implementation result record is committed to git.
7. Independent Reviewer commits `STATUS: APPROVED`.
8. Git working tree is clean and ready for the next task.
