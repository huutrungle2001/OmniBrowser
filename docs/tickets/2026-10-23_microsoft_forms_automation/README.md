# Experience Report: Microsoft Forms Automation via OmniBrowser

- **Date:** 2026-10-23
- **Target Application:** Microsoft Forms ("TeamWork Spring 2027 Application Form")
- **Context:** Automated population and verification of a 25-question student application form using OmniBrowser CLI (`omni`) and MCP over Chrome CDP.
- **Nature of Document:** Empirical incident/experience report documenting observed behaviors, unsupported page characteristics, and the workarounds employed to complete the task.

---

## Executive Summary

Filling a production Microsoft Forms page with 25 diverse questions (text fields, radio groups, multi-line answers, dynamic ranking lists) succeeded completely. However, several inherent page architectural patterns exposed limitations in OmniBrowser's current toolset:

1. **Inner-Pane Scrolling:** Microsoft Forms wraps questions inside an internal scrollable container rather than scrolling the main `window`. OmniBrowser's `scroll` tool halted with `UNSUPPORTED_SCROLL`.
2. **Ref Invalidation Churn:** Dynamic DOM mutations (such as reordering items in a ranking question) immediately invalidated existing node refs, triggering `STALE_REF`.
3. **Control Tree Pagination Overhead:** The page contained 389 accessibility nodes. With OmniBrowser's 15 KB chunking limit, retrieving the full control list required 6 to 7 serial cursor roundtrips per observe cycle.
4. **Transient UI Elements:** Key interactive controls (e.g., "Move option up" / "Move option down" buttons) only mount to the accessibility tree when their parent item is actively focused.
5. **Full-Page Screenshot Clamping:** `screenshot(full_page=true)` clamped to the viewport height (756px) because the document root did not expand with the inner container's scroll height.

The task was completed successfully by employing synthetic scrolling (focusing lower inputs and dispatching `PageDown` key events), writing client-side pagination scripts, and using focus-observe-act cycles for transient controls.

---

## Report Directory Structure

- [`01_experience_log.md`](./01_experience_log.md): Chronological, step-by-step account of the automation session.
- [`02_unsupported_areas.md`](./02_unsupported_areas.md): Detailed breakdown of the unsupported browser/DOM patterns and exact error codes returned.
- [`03_workarounds_used.md`](./03_workarounds_used.md): Specific techniques and helper scripts used to bypass limitations.
- [`evidence/`](./evidence/): Raw error payloads and reproduction scripts used during execution.
