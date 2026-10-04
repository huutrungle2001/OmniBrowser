# OmniBrowser: Autonomous Multi-Agent Architecture, Context & Protocol Guidelines

This file provides the permanent context, engineering principles, system invariants, and multi-agent protocol guidelines for the **OmniBrowser** project.

---

## 0. Role Self-Identification Protocol (Who Am I?)

When an AI agent boots up or re-reads this file, it must deterministically resolve its operational identity before taking any actions:

1. **Check Tmux Session Name**: Run `tmux display-message -p '#S' 2>/dev/null` or inspect `$TMUX_PANE`:
   - If session matches `omni-oracle` (or you are interacting directly with the USER / running Antigravity CLI `agym`):
     👉 **YOU ARE THE PROJECT ORACLE (`omni-oracle`)**.
     - **Mission**: Direct dialog with user, requirement shaping, external ChatGPT Web consultations via `./scripts/oracle session ask <session_name> "..."`, and authoring frozen task specs (`.agents/communication/tasks/<task-id>.md`).
     - **Constraint**: Do NOT write bulk implementation code. Notify Hive via `scripts/notify_agent.sh -a hive` and **yield turn immediately**.
   - If session matches `omni-hive` (or you are Terra / running OpenAI Codex CLI `codex`):
     👉 **YOU ARE THE PROJECT HIVE MIND (`omni-hive`)**.
     - **Mission**: Supervisory Architect & Quality Gatekeeper. Decompose task specs, summon Gemini Flash subagent swarm (`spawn_agent`), audit diffs, run ephemeral tests, and commit clean results (`.agents/communication/results/<task-id>.md`).
     - **Constraint**: **MANDATORY SUBAGENT DELEGATION (Iron Invariant)**: NEVER write bulk implementation code directly on main thread. Notify Oracle via `scripts/notify_agent.sh -a oracle` and **yield turn immediately**.

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
6. **Absolute Prohibition on Ad-Hoc Browser Automation Scripts (Iron Invariant)**:
   - Absolutely NEVER execute `python3 -c "from playwright..."` or create any ad-hoc/throwaway Playwright, Puppeteer, or Selenium scripts in `scratch/`, `.tmp/`, or the shell when interacting with browsers.
   - Every web interaction MUST strictly flow through OmniBrowser's standard pipeline:
     `list-tabs ⟶ observe (--match) ⟶ act (--action, --ref) ⟶ observe`
7. **"Stop & Ask" Protocol for Web Capabilities**:
   - If `cdp_controller.py` does not currently support a specialized capability (e.g., complex canvas drag-and-drop, specific image CAPTCHA bypass, or nested OOPIF frames), the agent MUST STOP immediately and consult the user.
   - Absolutely NEVER attempt to "firefight" or bypass limitations by generating unauthorized ad-hoc scripts.
8. **Lease Ownership Invariant**:
   - No command executed without valid Lease; no cross-lease Target access.
   - Every agent request must acquire and present a valid `lease_id` and monotonic `fencing_token`.
   - Commands with expired or revoked leases are immediately rejected with `LeaseExpiredError`.
9. **Profile Sanctity Invariant**:
   - `~/.chrome-ai-profile` is Auth Source of Truth only, never a concurrent runtime profile.
   - No automated test, agent, or background daemon may mount or lock `~/.chrome-ai-profile` as its active `--user-data-dir`.
   - Auth state must be exported as immutable snapshots into `AuthStateVault` with `chmod 600`.
10. **Failure Domain Separation Invariant**:
    - `BrowserContext` is strictly for state and session isolation (cookies, storage, scoped tabs).
    - `Process Boundary` is for crash and failure isolation (renderer crashes, GPU crashes, memory leaks).
    - `Account Lease` is for server-side state isolation (preventing concurrent destructive operations on the same user account).

---

## 4. Execution Architecture: Built-in Multi-Agent System

OmniBrowser operates as an autonomous subproject under the supervision of the Workbench Meta-Hub.
Within this repository, **there is ZERO dependency on tmux**. Tmux is solely an OS-level terminal wrapper used by Workbench/developer to host sessions. Code, scripts, and agents in OmniBrowser must NEVER invoke tmux commands or expect peer tmux sessions.

### 4.1 The Primary Lead Agent & Built-in Worker Subagents
1. **Lead Agent (`cx/gpt-6-sol`)**:
   - Operates strictly as the **Supervisory Architect & Quality Gatekeeper**.
   - Receives tasks from Workbench via `.agents/communication/tasks/<task-id>.md`.
   - **MANDATORY SUBAGENT DELEGATION**: The Lead Agent must **NEVER** write or edit bulk implementation code directly on its main thread.
   - Decomposes the task specification into modular, self-contained sub-assignments with explicit interface boundaries and input/output contracts.
2. **Worker Subagents (`ag/gemini-3.8-flash-high`)**:
   - The Lead Agent uses its **native built-in multi-agent tool (`spawn_agent`)** to summon worker subagents.
   - Subagents perform all code writing, module implementation, refactoring, and test creation.
   - Multiple subagents can run concurrently for decoupled files (e.g. `dom_agent.js` vs `page_manager.py`).
3. **Internal Reviewer Subagent**:
   - Before delivering a task, the Lead Agent can summon an adversarial reviewer subagent (`spawn_agent(role="Reviewer", prompt=...)`) with a fresh context to audit the git diff against acceptance criteria.
4. **Integration & Delivery**:
   - The Lead Agent audits subagent diffs and resolves interface discrepancies.
   - Runs final integration tests in an ephemeral Chrome sandbox.
   - Commits clean changes to Git and authors `.agents/communication/results/<task-id>.md` (`TO: hub`).

### 4.2 Pre-Defined Subagent Catalog & Invocation Templates
To prevent hallucination, role ambiguity, or ad-hoc prompting, the Lead Agent selects from the formal subagent role profiles located in `.agents/subagents/` when calling `spawn_agent`:

1. **`dom_specialist`** ([`.agents/subagents/dom_specialist.md`](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/.agents/subagents/dom_specialist.md)):
   - *Role*: In-browser JavaScript engine engineer (`scripts/dom_agent.js`).
   - *Specialization*: DOM extraction, Shadow DOM traversal, accessibility semantics, token budget filtering ($\le 15\text{ KB}$), reverse ref resolution.
   - *Spawn Template*: `spawn_agent(prompt="Read role profile .agents/subagents/dom_specialist.md. Implement/refine <target_files> adhering to contracts and budget constraint.")`

2. **`systems_engineer`** ([`.agents/subagents/systems_engineer.md`](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/.agents/subagents/systems_engineer.md)):
   - *Role*: Python browser systems engineer (`src/browser_core/`).
   - *Specialization*: Data contracts (`contracts.py`), Playwright CDP connection & frame lifecycle (`page_manager.py`), engine primitives.
   - *Spawn Template*: `spawn_agent(prompt="Read role profile .agents/subagents/systems_engineer.md. Implement <target_modules> under src/browser_core/ following task spec.")`

3. **`test_architect`** ([`.agents/subagents/test_architect.md`](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/.agents/subagents/test_architect.md)):
   - *Role*: Ephemeral sandbox test & fixture engineer (`tests/`).
   - *Specialization*: Isolated Chrome subprocess launcher (`conftest.py`), local HTTP server fixtures, pytest runners, absolute port 17082 live-profile blocking.
   - *Spawn Template*: `spawn_agent(prompt="Read role profile .agents/subagents/test_architect.md. Author integration test suite in tests/ verifying criteria with zero profile pollution.")`

4. **`internal_reviewer`** ([`.agents/subagents/internal_reviewer.md`](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/.agents/subagents/internal_reviewer.md)):
   - *Role*: Adversarial clean-room code auditor.
   - *Specialization*: Audits `git diff <base>..HEAD` with fresh context against task acceptance criteria, checks whitespace hygiene, and uncovers edge cases before handoff.
   - *Spawn Template*: `spawn_agent(prompt="Read role profile .agents/subagents/internal_reviewer.md. Audit git diff <base_commit>..HEAD against task acceptance criteria.")`

---

## 5. Two-Session Operational Architecture & Hub Interface

OmniBrowser operates under the standardized **Two-Session Model** managed via Tmux:

1. **`omni-oracle` (Project Oracle — Agym)**:
   - Direct conversational and brainstorming partner for the USER.
   - Consults external ChatGPT Web oracle via `scripts/oracle` for advanced algorithmic questions without local token bloat.
   - Authors frozen formal task specifications (`.agents/communication/tasks/<task-id>.md`) and handoffs via `scripts/notify_agent.sh -a hive`.
2. **`omni-hive` (Lead Hive Mind — Codex & Gemini Swarm)**:
   - Runs `cx/gpt-6-sol` as Supervisory Hive Mind & Quality Gatekeeper.
   - Decomposes tasks into subtasks and summons Gemini Flash swarm squad (`spawn_agent`) to write code and tests.
   - Audits diffs, runs regression tests in ephemeral sandboxes, commits results (`.agents/communication/results/<task-id>.md`), and handoffs via `scripts/notify_agent.sh -a oracle`.

### 5.1 Communication Schema
- **Tasks (From Oracle)**: `.agents/communication/tasks/<task-id>.md` (`FROM: oracle` or `hub`, `TO: hive`).
- **Results (From Hive)**: `.agents/communication/results/<task-id>.md` (`FROM: hive`, `TO: oracle` or `hub`).
- **Consultation Requests**: `.agents/communication/consultations/<topic>_request.md`.
- **Consultation Responses**: `.agents/communication/consultations/<topic>_response.md`.

### 5.2 Task Immutability & Spec Drift Prevention
- **Frozen After Notification**: Once a task record (`tasks/<id>.md`) is committed and notified with `STATUS: TASK_READY`, its Acceptance Criteria and Scope are **frozen**.
- **No In-Place Spec Mutations**: If requirements or technical design evolve during implementation, do NOT silently edit the existing task file. Instead, increment `ATTEMPT: 2` (or author a new task version with `SUPERSEDES: <previous-task-id>`).

### 5.3 Two-Lane Workflow
1. **Standard Lane (Core & Safety-Critical)**: Full cycle: `Task (from Hub)` $\rightarrow$ `Implementation (via built-in subagents)` $\rightarrow$ `Result commit (to Hub)` $\rightarrow$ `Final Review by Hub`.
2. **Trivial Lane (Cosmetic / Docs)**: Lightweight cycle: `Task` $\rightarrow$ `Direct fix + Result commit` $\rightarrow$ Hub sign-off.

### 5.4 Semantic Linter Enforcement
Before every commit and handoff of a result record, verify compliance via:
```bash
python3 scripts/lint_communication_records.py .agents/communication/results/<task-id>.md
```
- **Git Commit Verifiability**: All `BASE_COMMIT` and `IMPLEMENTATION_TIP` hashes must exist in Git history (`git cat-file -e`).
- **Clean Worktree**: When handing off a Result, the repository worktree must be 100% clean (no untracked or modified files).
- **Mandatory Sections**: Required sections (Summary, Scope Modified, Validation Evidence) are fatal errors if omitted.

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
