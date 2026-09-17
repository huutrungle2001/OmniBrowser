# Task: task-003-unified-cli-and-production-packaging

RECORD_TYPE: TASK
RECORD_ID: task-003-unified-cli-and-production-packaging
STATUS: TASK_READY
ATTEMPT: 1
CREATED_AT: 2026-09-17T02:40:00Z
UPDATED_AT: 2026-09-17T02:40:00Z
FROM: orchestrator
TO: implementer

## Objective

Deliver Phase 3 & Phase 4 of OmniBrowser: Build the unified CLI adapter (`scripts/cdp_controller.py`), implement legacy regression and new CLI integration tests (`tests/test_cli_legacy.py`), and package the production skill into `~/.gemini/config/skills/browser-automation-cdp/`.

## Scope

Owned:
- `scripts/cdp_controller.py`
- `tests/test_cli_legacy.py`
- Production packaging & documentation synchronization to `~/.gemini/config/skills/browser-automation-cdp/`

Preserve:
- `src/browser_core/contracts.py`
- `src/browser_core/page_manager.py`
- `src/browser_core/engine.py`
- `src/browser_core/primitives.py`
- `scripts/dom_agent.js`
- All automated tests must strictly run against isolated ephemeral Chrome with zero live profile tampering.

## Acceptance Criteria

1. **Unified CLI Adapter (`scripts/cdp_controller.py`)**:
   - **100% Backward Compatibility** with existing legacy CLI commands:
     - `list-tabs`: Prints table of open tabs with index, title, URL.
     - `goto <url>`: Navigates active or matched tab to URL and waits for `domcontentloaded`.
     - `eval <expression>`: Evaluates JavaScript expression on active or matched tab and prints result.
     - `screenshot [--output FILE]`: Captures full viewport screenshot.
     - CLI flags `--cdp-url` (default: `DEFAULT_CDP_URL` / env `CDP_URL`) and `--match` must behave identically.
   - **Progressive Capability Subcommands**:
     - `observe [--scope main|viewport] [--max-elements 80] [--json]`: Runs semantic DOM scanner, outputs compact tree.
     - `act --action <click|fill|select|press> --ref <ref> [--value <val>] [--expect <json>] [--json]`: Executes atomic action with postcondition expectation.
     - `inspect <dom|ax|frame-tree> [--ref <ref>] [--depth 3]`: Outputs bounded structural/accessibility/frame diagnostics.
     - `run-code --script <string> [--mode read|write]`: Executes safe bounded JavaScript in async IIFE.
     - `visual [--ref <ref>] [--output <path>]`: Captures targeted Pillow-cropped bounding box with CAPTCHA detection.

2. **Regression & Integration Test Suite (`tests/test_cli_legacy.py`)**:
   - Verifies legacy subcommands (`list-tabs`, `goto`, `eval`, `screenshot`) on ephemeral Chrome with exit code 0.
   - Verifies new subcommands (`observe`, `act`, `inspect`, `run-code`, `visual`) invoked via subprocess on ephemeral Chrome with exit code 0.

3. **Production Packaging**:
   - Synchronize core modules (`src/browser_core/`), scripts (`scripts/cdp_controller.py`, `scripts/dom_agent.js`), and requirements into `~/.gemini/config/skills/browser-automation-cdp/`.
   - Update `~/.gemini/config/skills/browser-automation-cdp/SKILL.md` to document the Progressive Capability Pyramid and CLI usage.

4. **Multi-Agent Delegation Invariant**:
   - Implementer Lead strictly coordinates and audits; authoring delegated to worker subagents.

## Validation

1. `python3 -m pytest tests/test_cli_legacy.py -v` passes with exit code 0 on ephemeral Chrome.
2. `python3 -m pytest tests/ -v` (full 15+ test suite) passes with exit code 0.
3. `git diff --check` passes with 0 warnings.
