# OmniBrowser v3: The Pragmatic Automation Architecture (Plan & Specification)

- **Target Version:** OmniBrowser v3.0
- **Status:** Approved Architecture & Implementation Plan
- **Author / Maintainer:** Trung Le Huu & Codex AI Partner
- **Workspace:** `/Users/huutrungle2001/Documents/OnGoing/OmniBrowser`
- **Core Directive:** Fast, Powerful, Production-Ready, Deterministic. Zero dogmatism. No over-engineering.

---

## 1. Executive Summary & Paradigm Shift

OmniBrowser v2 proved the value of attaching to existing authenticated Chrome instances via loopback CDP (`http://127.0.0.1:17082`), bypassing MFA, bot detection, and login friction. However, real-world field tests on enterprise platforms (Clevry psychometrics, Slate CRM, Symplicity CareerHub, Microsoft Forms) exposed critical failure modes stemming from **defensive purism** and **dogmatic design constraints**:

1. **The "No Arbitrary Eval" Fallacy:** Forbidding JavaScript execution forced agents into multi-turn roundtrips (up to 100 turns for a 66-question test), inflating token consumption by 400k+ tokens, taking 15+ minutes, and causing frequent session dropouts.
2. **Hidden Element Blindness:** The engine rejected clicks on modern styled inputs (`opacity: 0`, `iCheck`, `custom-control`), failing with `INVALID_ACTION` or freezing for 10.6 seconds on native dropdowns.
3. **DOM Noise & ViewState Bloat:** ASP.NET `__VIEWSTATE` and Base64 blobs routinely pushed context past 800 KB, truncating active questions and navigation buttons.
4. **Fragile Pagination:** Strict HMAC cursor signing broke on benign background DOM mutations (clocks, analytics pings), throwing `STALE_CURSOR`.

### The v3 Philosophy
We move from **Defensive Purism** to **Pragmatic Production Automation**:
- **Native when possible, Scripted when practical:** Retain clean AX-tree observation and native inputs, but add direct, deterministic in-browser JavaScript evaluation (`evaluate`).
- **Resilient Fallbacks:** When native inputs are obscured by CSS facades, automatically resolve and click the associated `<label>` or parent wrapper instead of crashing.
- **Batching over Churn:** Provide `act_batch` to execute atomic multi-step sequences (e.g. fill 6 fields + click next) in a single tool call.
- **Signal over Noise:** Strip ViewState/Base64 rác automatically and allow scoped observation via CSS selectors.

---

## 2. Target Project Structure (Clean, Lean & Modular)

The project retains a flat, logical, single-responsibility structure without heavy frameworks or unnecessary abstractions:

```text
OmniBrowser/
├── pyproject.toml              # Dependencies: playwright, httpx, markdownify, mcp
├── README.md                   # Concise documentation & command reference
├── PLAN.md                     # This specification
├── omni.py                     # Root CLI development wrapper
├── omni_mcp.py                 # Root MCP development wrapper
├── src/
│   └── omni/
│       ├── __init__.py         # Package exports (__version__ = "3.0.0")
│       ├── cli.py              # CLI argument parsing & command dispatch
│       ├── mcp_server.py       # Persistent STDIO MCP server (9 tools)
│       ├── errors.py           # Standardized, actionable error codes
│       ├── models.py           # Clean JSON serialization & payload schemas
│       ├── browser/            # Low-level CDP connection & DOM target management
│       │   ├── connection.py   # CDP attach (port 17082, no_defaults=True)
│       │   ├── tabs.py         # Tab listing, switching & lookup
│       │   ├── targets.py      # Ref binding & smart element resolution
│       │   ├── frames.py       # Out-of-process & nested iframe tree
│       │   └── refs.js         # In-browser DOM node identifier script
│       ├── reading/            # AX-Tree & DOM extraction pipeline
│       │   ├── cleaner.py      # NEW: Strips __VIEWSTATE, Base64 blobs & script rác
│       │   ├── capture.py      # Bounded AX/DOM snapshotting
│       │   ├── render.py       # Markdown rendering & control extraction
│       │   └── pagination.py   # Resilient, mutation-tolerant cursor pagination
│       └── commands/           # Command implementations (1 file = 1 responsibility)
│           ├── list_tabs.py    # omni --list-tabs
│           ├── goto.py         # omni --goto URL [--new-tab]
│           ├── observe.py      # omni --observe [--selector] [--max-bytes]
│           ├── act.py          # omni --act (smart click, fill, select, press)
│           ├── act_batch.py    # NEW: omni --act-batch (composite sequential actions)
│           ├── evaluate.py     # NEW: omni --eval "expression / script"
│           ├── scroll.py       # omni --scroll (window OR inner container ref)
│           ├── screenshot.py   # omni --screenshot (viewport or full-page)
│           └── download.py     # omni --download (cookie-authenticated GET)
├── tests/                      # Pytest integration tests
└── docs/                       # Architectural tickets & failure mode analyses
```

---

## 3. The 5 Core Architectural Upgrades

### Upgrade 1: Direct Script Execution (`evaluate` / `omni --eval`)
- **Problem:** Complex calculations, custom UI frameworks, and ASP.NET postbacks require dozens of blind clicks without script access.
- **Solution:** Add an explicit, first-class `evaluate` command to both CLI and MCP.
- **Specification:**
  - **CLI:** `omni --eval "document.querySelector('form').submit()" --tab TAB_ID`
  - **MCP:** `evaluate(tab_id, script, arg?)`
  - **Execution:** Evaluates in the tab's top-level execution context (or specified frame), returning the serialized JSON value or meaningful runtime exception.
  - **Deterministic Safety:** Does not expose arbitrary Node/OS access; runs strictly inside the browser sandbox.

### Upgrade 2: Smart Action Resolution (Label Fallback & Dropdown Safety)
- **Problem:** Clicking hidden inputs (`opacity: 0`, `iCheck`) throws `INVALID_ACTION`. Clicking native `<select>` dropdowns freezes the CDP loop for 10.6s.
- **Solution in `src/omni/commands/act.py` & `browser/targets.py`:**
  1. **Label Auto-Resolution:** If the target element has `opacity: 0` or is marked unclickable:
     - Check if it is an `<input type="radio" | "checkbox">`.
     - Automatically locate its associated `<label for="id">` or immediate parent label/facade container.
     - Dispatch the native click to the label/facade element instead of failing.
  2. **Dropdown Protection:**
     - If action is `click` on an `HTMLSelectElement`: Do not dispatch native OS click. Return a descriptive error instructing to use `action="select"`, or auto-convert to `select_option`.

### Upgrade 3: Batch Action Execution (`act_batch`)
- **Problem:** Form filling and questionnaire completion require 1 turn per radio button.
- **Solution:** Introduce `act_batch` allowing atomic execution of multiple interactions in 1 roundtrip.
- **Specification:**
  - **Input Payload:**
    ```json
    {
      "tab_id": "...",
      "actions": [
        { "ref": "tab.n1", "action": "fill", "value": "Warwick" },
        { "ref": "tab.n2", "action": "fill", "value": "5838393" },
        { "ref": "tab.n3", "action": "click" },
        { "ref": "tab.n4", "action": "click", "wait_for_navigation": true }
      ]
    }
    ```
  - **Behavior:** Executes sequentially. If any step fails, halts and returns the index of the failed action and current page state. Reduces latency by 85%.

### Upgrade 4: Scoped & Sanitized Observation (`observe --selector`)
- **Problem:** Huge DOM trees overflow agent context with rác (30KB+ ViewState strings, footers, cookie banners).
- **Solution in `src/omni/reading/cleaner.py` & `observe.py`:**
  1. **Scoped Observation:** Add optional `selector` argument (e.g. `--selector "#assessment-form"`). Restricts AX/DOM traversal exclusively to that subtree.
  2. **Automatic Noise Pruning:** Automatically strip:
     - Hidden inputs with values > 100 characters (`__VIEWSTATE`, `__EVENTVALIDATION`, `__RequestVerificationToken`).
     - Inline data URIs (`data:image/...`).
     - Script and stylesheet contents.
  3. **Resilient Cursors:** Base cursor pagination on stable content offsets rather than rigid whole-document cryptographic hashes that break on harmless DOM pings.
  4. **Raised Byte Limit:** Increase default byte limit from 15 KB to 64 KB (with 128 KB max) so modern LLMs can ingest entire active form sections in 1 turn.

### Upgrade 5: Container-Aware Scrolling (`scroll --ref`)
- **Problem:** Microsoft Forms, Notion, and Gmail use inner `overflow: auto` div containers. Global window scrolling does nothing.
- **Solution in `src/omni/commands/scroll.py`:**
  - Accept an optional `ref` or `selector`:
    `omni --scroll --tab TAB_ID --ref CONTAINER_REF --direction down --amount 500`
  - Dispatches wheel/scroll events directly onto the target element's bounding box rather than assuming `window`.

---

## 4. Implementation Roadmap (Fast & Concrete)

### Step 1: Core Engine & Cleaner Module (Day 1)
- Create `src/omni/reading/cleaner.py` to filter ViewState and large hidden attributes.
- Update `src/omni/reading/render.py` to invoke the cleaner.
- Implement `src/omni/commands/evaluate.py` and hook into `cli.py` & `mcp_server.py`.

### Step 2: Smart Action Resolution & Batching (Day 2)
- Update `src/omni/commands/act.py` with label auto-resolution for `opacity: 0` inputs and select-safety.
- Create `src/omni/commands/act_batch.py` for multi-action execution.
- Add `--selector` and `--ref` parameters to `observe.py` and `scroll.py`.

### Step 3: Integration Testing & Real-World Verification (Day 3)
- Run regression tests on existing test suite (`pytest tests/`).
- Test against real-world test cases:
  1. Clevry Assessment Portal (`https://sb.clevry.com`) — verifying 0-bloat observe and 1-turn batch actions.
  2. Slate CRM / Stanford Form — verifying native select and cascading fields.
  3. Symplicity CareerHub — verifying scoped observation without card duplication.

---

## 5. Verification Metrics (Definition of Done)
1. **Zero ViewState Bloat:** Observation of legacy ASP.NET pages drops from 800 KB+ to under 15 KB clean markdown.
2. **Zero Invisibility Timeouts:** Clicking custom radio/checkbox components succeeds on the first attempt via label auto-resolution.
3. **Turn Count Reduction:** 11-page psychometric questionnaires complete in $le 5$ turns (using `act_batch`) or 1 turn (using `evaluate`), down from 90+ turns.
4. **Clean Codebase:** Zero third-party bloat added; maintains < 1,500 total lines of Python across the entire core package.

