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
3. **`omni_implementer`** (Codex Lead / Systems Engineer): Owns code implementation, spawns subagents for decoupled tasks, runs ephemeral Chrome tests, and commits clean results.
4. **`omni_reviewer`** (Adversarial Quality Gate): Runs in a fresh context, audits git diffs independently, tests backward compatibility, and issues binding approval.

---

## 5. Operational Protocol & Mandatory Turn Handoff

### 5.1 Communication Hygiene
- **Authoritative Mechanism**: Durable Markdown records in `.agents/communication/` and `scripts/notify_agent.sh` are the **ONLY** authoritative communication channel.
- **Zero Raw Terminal Dumps**: Communication records must use structured Markdown evidence tables summarizing command, exit code, and high-level outcome.
- **Context Resets (`/new`)**: Implementer and Reviewer sessions must execute context resets (`/new`) before picking up new tasks to prevent context poisoning.

### 5.2 Mandatory Handoff Routing Matrix
1. **Orchestrator** $\rightarrow$ `omni_implementer`:
   ```bash
   scripts/notify_agent.sh -a omni_implementer -m "Task <id> ready." -r .agents/communication/tasks/<id>.md
   ```
2. **Implementer** $\rightarrow$ `omni_reviewer`:
   ```bash
   scripts/notify_agent.sh -a omni_reviewer -m "Result for <id> ready for review." -r .agents/communication/results/<id>.md
   ```
3. **Reviewer**:
   - If `STATUS: REVISION_REQUIRED` $\rightarrow$ `omni_implementer`:
     ```bash
     scripts/notify_agent.sh -a omni_implementer -m "Revision required for <id>." -r .agents/communication/reviews/<id>.md
     ```
   - If `STATUS: APPROVED` $\rightarrow$ `omni_orchestrator` & `omni_hub`:
     ```bash
     scripts/notify_agent.sh -a omni_orchestrator -m "Task <id> approved." -r .agents/communication/reviews/<id>.md
     scripts/notify_agent.sh -a omni_hub -m "Task <id> approved." -r .agents/communication/reviews/<id>.md
     ```

### 5.3 Turn Completion Rule (No Infinite Waiting)
- Once an agent finishes sending a notification via `notify_agent.sh`, it **MUST immediately end its turn** and become inactive.
- Agents must **NEVER** poll or loop waiting for the other agent. The receiving agent will wake the sender upon completing its turn.

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
