# Consultation Request: task_007_implementation_review_request

RECORD_TYPE: CONSULTATION_REQUEST
RECORD_ID: task_007_implementation_review_request
STATUS: REQUESTED
ATTEMPT: 1
CREATED_AT: 2026-09-19T10:55:00Z
UPDATED_AT: 2026-09-19T10:55:00Z
FROM: implementer
TO: oracle

## Background

Following our architectural consultation on the **Hybrid Bulkheaded Browser Pool** for OmniBrowser, we have fully implemented Milestone v2.2 (Task 007: Browser Concurrency and Resource Broker).

### Architecture Summary:
1. **Execution Classes**:
   - **Class S (Shared Multi-Context)**: Warm headless daemon, ephemeral BrowserContext (<10ms startup via raw CDP WebSocket `Target.createBrowserContext`, ~20-40MB RAM).
   - **Class I (Isolated Ephemeral Process)**: Independent Chrome process with dynamic port and temporary user-data-dir for untrusted or crash-prone tasks.
   - **Class A (Authenticated & Interactive)**: Dedicated headful or snapshot-injected runtime with exclusive identity lease (`auth_identity`).
2. **Subsystems Implemented in `src/browser_core/broker/`**:
   - `SessionRouter`: Central coordinator, state persistence (`state.json`), daemon lifecycle, and routing.
   - `LeaseManager`: Monotonic fencing tokens (`fencing_token`), lease expiration validation (`LeaseExpiredError`), and exclusive identity locks (`IdentityConflictError`).
   - `TargetRegistry`: Virtual scoped tab ownership mapping `TargetID -> BrowserContextID -> LeaseID -> AgentID`.
   - `AdmissionController`: System telemetry check (RAM, CPU load, active leases) with backpressure timeout.
   - `AuthStateVault`: Snapshot-based auth storage (`chmod 600`) enforcing strict profile sanctity (Invariant 9).
   - `LifecycleWatchdog`: Daemon RSS tracking (2GB threshold) and zero-downtime Drain Protocol.
3. **Native CDP WebSocket Client (`src/browser_core/cdp_client.py`)**:
   - Standard-library WebSocket client (no Playwright or asyncio loop dependencies), preventing event loop collisions.
4. **CLI Integration (`scripts/cdp_controller.py`)**:
   - `--lease / --session`, `--fencing-token`, `broker lease request`, `broker lease release`, `broker status`, scoped `list-tabs`.
5. **Validation Status**:
   - All 8 broker integration tests passed (`tests/test_concurrency_broker.py`).
   - All 50 repository tests passed with exit code 0 (`pytest`).
   - Zero live profile pollution (port 17082 blocked).

## Questions

We would like a deep, rigorous adversarial code and architecture review of the attached implementation files:

1. **Concurrency & Race Condition Analysis**:
   - Are there subtle race conditions in `LeaseManager` (fencing tokens, monotonic counters, identity locking) or `SessionRouter` (daemon recycling, state persistence across concurrent CLI processes)?
   - How robust is our file-based state synchronization (`state.json` via atomic rename `.tmp_state_... -> state.json`) when multiple CLI processes execute commands concurrently?

2. **CDP Context Lifecycle & Resource Leaks**:
   - In Class S, we create contexts using `Target.createBrowserContext` and dispose them using `Target.disposeBrowserContext`.
   - Are there any residual V8 memory allocations, lingering service workers, network cache build-ups, or unclosed targets that could escape disposal in long-running headless daemons?

3. **Failure Domains & Resilience**:
   - Invariant 10 mandates separation between `BrowserContext` (state), `Process Boundary` (crash), and `Account Lease` (identity).
   - If the shared Class S Chrome daemon crashes unexpectedly (SIGKILL / OOM) while 5 agents hold active leases, how gracefully does `SessionRouter` detect and recover on the next lease request or CLI invocation?
   - How can we make the recovery even more seamless?

4. **Identity & Profile Sanctity**:
   - Invariant 9 prohibits concurrent mounting of `~/.chrome-ai-profile`.
   - Does `AuthStateVault` and `assert_not_protected_profile` completely prevent any accidental lock on the live profile?

5. **Milestone v2.3 Roadmap Recommendations**:
   - What high-leverage enhancements should we plan for next (e.g., streaming DevTools protocol multiplexer, warm pool of pre-forked Class I processes, automatic auth cookie refresh)?
