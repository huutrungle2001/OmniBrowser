# Task: task-007-browser-concurrency-and-resource-broker

RECORD_TYPE: TASK
RECORD_ID: task-007-browser-concurrency-and-resource-broker
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-19T10:19:00Z
UPDATED_AT: 2026-09-19T10:19:00Z
FROM: hub
TO: browser-arch

## Objective

Implement Milestone v2.2 of OmniBrowser: **Hybrid Bulkheaded Browser Pool & Concurrency Broker**, resolving browser resource contention, tab collisions, and crash blast radius across multi-agent workflows in Workbench subprojects.

Incorporate all architectural conclusions and invariants from `.agents/communication/consultations/browser_concurrency_architecture.md` (formulated by `browser-arch` and verified with ChatGPT Web GPT-5.6 Sol Thinking High).

## Scope

### Owned:
1. `src/browser_core/broker/`:
   - `session_router.py`: Route agent requests to appropriate execution class:
     - **Class S (Shared Multi-Context):** Warm headless Chrome daemon (`--headless=new`) via `Target.createBrowserContext`. Fast startup (< 50ms), low RAM (~20–40MB).
     - **Class I (Isolated Dedicated Ephemeral):** Independent Chrome process with `--user-data-dir=$(mktemp -d)` and dynamic port for heavy WebGL/Canvas/untrusted pages.
     - **Class A (Authenticated & Interactive):** Dedicated headful Chrome or context with Auth Snapshot and **Exclusive Identity Lease**.
   - `lease_manager.py`:
     - Issue `lease_id` and monotonic `fencing_token`.
     - Reject stale commands if lease expired or revoked.
     - Enforce `auth_identity_lease` for mutating operations on the same user account.
   - `target_registry.py`:
     - Map `TargetID -> BrowserContextID -> LeaseID -> AgentID`.
     - Filter tab operations strictly within the calling agent's lease.
   - `admission_controller.py`:
     - Queue requests and apply backpressure based on macOS system telemetry (RAM pressure, CPU load).
   - `auth_state_vault.py`:
     - Single-Writer / Multi-Reader snapshot storage (`chmod 600`).
     - Never allow concurrent direct access or lock on `~/.chrome-ai-profile`.
   - `lifecycle_watchdog.py`:
     - Event-driven CDP monitoring (`Target.targetCrashed`, `Inspector.detached`).
     - Implement **Drain Protocol** for clean daemon recycling when memory threshold is reached.

2. `scripts/cdp_controller.py`:
   - Add `--session <id>` / `--lease <id>` CLI arguments.
   - Ensure `list-tabs`, `observe`, `act`, `goto`, `screenshot` are strictly scoped to the active lease.
   - Support broker CLI subcommands:
     - `broker lease request [--class S|I|A] [--auth <account>] [--timeout <sec>]`
     - `broker lease release <lease_id>`
     - `broker status`

3. `AGENTS.md` (OmniBrowser):
   - Formally document Invariants 8, 9, 10:
     - **Invariant 8 (Lease Ownership):** No command executed without valid Lease; no cross-lease Target access.
     - **Invariant 9 (Profile Sanctity):** `~/.chrome-ai-profile` is Auth Source of Truth only, never concurrent runtime.
     - **Invariant 10 (Failure Domain Separation):** BrowserContext for state, Process for crash containment, Account Lease for server-side state.

4. `tests/test_concurrency_broker.py`:
   - Unit and integration tests for multi-context creation, tab isolation, fencing token rejection, crash containment, and lease lifecycle.

### Preserve:
- Existing Level 1 (Primitives), Level 2 (Recipes & Procedural Memory), Level 3 (Visual & Spatial Reasoning) interfaces and behavior.
- 100% backward compatibility for standalone single-session commands.
- Zero Live Profile Tampering invariant: All automated test suites must use isolated ephemeral profiles or Class S ephemeral contexts; never touch live user data during tests.

## Acceptance Criteria

1. **Isolation & Scoped Tabs**:
   - Two concurrent agents running with different `lease_id`s see only their own tabs via `list-tabs`.
   - Actions by Agent A do not switch OS focus or interrupt Agent B.
2. **Fencing Token Protection**:
   - An expired or revoked lease immediately rejects subsequent CDP commands with an explicit `LeaseExpiredError`.
3. **Execution Class Routing**:
   - Class S requests spawn in < 100ms using `Target.createBrowserContext`.
   - Class I requests spawn in independent Chrome processes with isolated temp directories and dynamic ports.
   - Class A enforces single-agent mutual exclusion per user identity.
4. **Test Suite Verification**:
   - All new tests in `tests/test_concurrency_broker.py` pass with exit code 0.
   - All existing regression tests (`test_contracts.py`, `test_engine_actions.py`, `test_learning_loop.py`) continue to pass with exit code 0.
5. **Clean Worktree & Durable Records**:
   - All changes committed with zero linter warnings.
   - Result record created upon verification.
