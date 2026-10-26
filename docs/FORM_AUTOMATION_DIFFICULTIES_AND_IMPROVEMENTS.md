# Technical Difficulties & Friction Points in Form Automation via OmniBrowser

- **Date:** 2026-10-25
- **Target Application:** Technolutions Slate CRM — Knight-Hennessy Scholars Information Session Registration (Stanford University / University of Warwick)
- **Target URL:** `https://apply.knight-hennessy.stanford.edu/register/?id=e42b308c-4429-4100-9991-b65ee41b68c0`
- **Tooling:** OmniBrowser v2 (MCP & CLI), Playwright CDP attach to existing Chrome instance (port 17082), Python 3.10+.
- **Objective:** Automated, non-submitting population of a multi-section registration form with cascading dropdowns, autocomplete text inputs, and dynamic conditional questions.

---

## Executive Summary

While OmniBrowser successfully completed the end-to-end population and verification of all required fields without triggering form submission, the agent encountered several significant friction points that substantially increased turn count, wall-clock latency, and execution fragility:

1. **Native `<select>` Click Timeout (10.6s freeze):** Firing an `act(action="click")` on a native HTML dropdown triggered the operating system's native Cocoa popup menu, stalling the CDP event loop until hitting OmniBrowser's default 10-second timeout.
2. **AX-Tree Opacity of Dropdown Options:** Collapsed comboboxes (`role: "combobox"`) in the Chrome Accessibility tree did not expose their child `<option>` labels or values in the `observe` control stream, forcing the agent to out-of-band download and parse the raw HTML to discover permissible values.
3. **Cursor Invalidation Churn (`STALE_CURSOR`):** The default 15 KB `max_bytes` limit required cursor pagination for a 47-control form. Background heartbeat and telemetry scripts on the page mutated minor DOM attributes between requests, immediately invalidating pagination cursors.
4. **Dynamic Cascading Fields & Asynchronous Autocomplete Overlays:** Selecting `Country = United Kingdom` replaced the `State` field with a `Region` dropdown whose options were dynamically injected by external CDN scripts, and the `Undergraduate institution` autocomplete spawned a floating overlay that obscured lower form fields until cleared.

This report documents the exact failure modes, reproduction details, workarounds applied, and concrete architectural recommendations to improve OmniBrowser v2.

---

## Detailed Failure Modes & Root Causes

### 1. Native `<select>` Click Stalling the CDP Event Loop (`TIMEOUT`)

#### Observed Behavior
When attempting to interact with a native HTML `<select>` dropdown via the standard `click` action:
```json
{
  "tab_id": "673F3149CEBCA969B05510C6B0F33E4E",
  "ref": "673F3149CEBCA969B05510C6B0F33E4E.9a34192da1f442b038c550e910733350.n42",
  "action": "click"
}
```

The call hung for **10.6 seconds of wall time** before failing with:
```json
{
  "ok": false,
  "error": {
    "code": "TIMEOUT",
    "message": "Action timed out; inspect before retrying. It was not replayed."
  }
}
```

#### Root Cause
- In Chromium, clicking a native `<select>` element dispatches a mouse event that tells the browser engine to open the operating system's modal popup menu (e.g., Cocoa popup on macOS, Win32 popup on Windows).
- Because this popup menu runs in the OS window manager loop rather than the standard web DOM, the Chrome DevTools Protocol (CDP) session is blocked waiting for user interaction or modal dismissal.
- Although OmniBrowser supports `action="select"`, agents and LLMs naturally attempt to `click` a combobox first to inspect its dropdown options if they do not know what options exist.

#### Workaround Employed
- Abandoned `click` on all elements with `role: "combobox"`.
- Used strictly `act(tab_id=..., ref=..., action="select", label="...")` or `value="..."`.

#### Recommended Architectural Fix
- In `src/omnibrowser/actions.py`, inspect the underlying DOM tag before executing a `click`.
- If the target node is an HTML `<select>`, OmniBrowser should:
  1. **Option A (Guardrail):** Immediately reject the action with a clear, fast error without waiting for the 10s timeout:
     ```json
     {
       "ok": false,
       "error": {
         "code": "NATIVE_SELECT_CLICK_BLOCKED",
         "message": "Target is a native <select> element. Clicking native selects opens a blocking OS popup menu. Use action='select' with 'label' or 'value' instead."
       }
     }
     ```
  2. **Option B (Transparent Handling):** Intercept the click and prevent default popup rendering, or automatically return the list of available `<option>` items.

---

### 2. Invisibility of Collapsed `<select>` Options in the Accessibility Tree

#### Observed Behavior
When observing the page, native HTML dropdowns appeared as:
```json
{
  "ref": "673F3149CEBCA969B05510C6B0F33E4E.9a34192da1f442b038c550e910733350.n42",
  "role": "combobox",
  "name": "When do you plan to apply to graduate school?*",
  "disabled": false,
  "expanded": false,
  "frame_id": "673F3149CEBCA969B05510C6B0F33E4E"
}
```

No child options, text values, or permitted keys were exposed anywhere in the control list.

#### Impact
- The agent is informed that a field requires selection, but cannot determine what valid choices exist (e.g., is the choice `"1-2 years"`, `"2027"`, `"Next year"`, or a UUID?).
- Attempting `action="select"` with an incorrect label results in `INVALID_ARGUMENT` / `UNSUPPORTED_ACTION`.
- As a consequence, the agent was forced to take a heavy out-of-band detour:
  1. Call `omni --download` to fetch the full 125 KB HTML document to `/tmp/khs_form.html`.
  2. Write and execute an external Python script using BeautifulSoup to parse all `<select>` and `<option>` tags to map form field IDs to their option values.

#### Recommended Architectural Fix
- When building the accessibility node representation for `role: "combobox"` or native `<select>`, OmniBrowser should query and attach an `options` array:
  ```json
  {
    "ref": "...",
    "role": "combobox",
    "name": "When do you plan to apply to graduate school?*",
    "options": [
      {"value": "f4dc2135-f82c-4e1d-a364-01434cede04f", "label": "This year"},
      {"value": "cb98823e-8886-4768-b19b-1c84b93d4c42", "label": "1-2 years"},
      {"value": "e026afa7-2aab-4861-9e12-4f992dca9689", "label": "3+ years"}
    ]
  }
  ```
- If the options list is extremely large (e.g., a 245-country dropdown), OmniBrowser can truncate the list or provide a count with sample values: `"options_count": 245, "options_sample": ["Afghanistan", "Albania", ...]`.
- This single addition would eliminate 90% of out-of-band HTML parsing scripts during web form automation.

---

### 3. Cursor Invalidation Churn (`STALE_CURSOR`) on Active Pages

#### Observed Behavior
On the initial observe call, OmniBrowser truncated the control stream due to the default 15 KB buffer limit:
```json
{
  "truncated": true,
  "has_more": true,
  "next_cursor": "WzE3MTUsMjQsMjcsMSwyXX2l2Zxw1zgipczMgeRulzc1GjGNdGkC5aNmsVAeAP__"
}
```

Immediately calling `observe` with that exact cursor returned:
```json
{
  "ok": false,
  "error": {
    "code": "STALE_CURSOR",
    "message": "Cursor is invalid or page content changed; start a fresh observe without cursor."
  }
}
```

#### Root Cause
- Modern enterprise web forms run periodic client-side activity scripts:
  - Analytics pings (Google Analytics, Meta Pixel).
  - CSRF / session extension checks (Slate's `session.js` pinging `/ping`).
  - Input formatting/masking listeners (Grammarly extension hooks, jQuery validators).
- OmniBrowser's cursor validation detects any alteration in DOM tree state or token position and invalidates the cursor immediately to avoid inconsistent indexing.
- This creates a race condition on active pages where cursor pagination consistently fails.

#### Workaround Employed
- Avoided pagination altogether by passing a sufficiently large buffer:
  ```javascript
  tools.mcp__omni__observe({ tab_id: tabId, max_bytes: 65536 });
  ```
  This allowed all 47 controls, links, and content blocks to be returned in a single atomic response (`has_more: false`).

#### Recommended Architectural Fix
1. **Increase Default `max_bytes`:** Increase the default chunk size from 15 KB (`15360`) to 32 KB or 64 KB. 15 KB is too small for standard enterprise web forms, forcing unnecessary cursor pagination.
2. **Fuzzy / Structural Cursor Resiliency:** When validating a cursor, ignore benign DOM mutations that do not affect the accessibility control hierarchy (e.g., style changes, hidden script tags, telemetry iframe insertions).

---

### 4. Dynamic Cascading Dependencies (Slate / CRM Frameworks)

#### Observed Behavior
Enterprise CRM platforms (Technolutions Slate, Oracle Taleo, Workday, Salesforce) rely heavily on client-side reactive rendering:
1. **Cascading Address Fields:**
   - Default state: `Country` combobox followed by a `State` combobox.
   - When `Country` was set to `"United Kingdom"`, the `State` combobox was dynamically detached and replaced with a `Region` combobox (`label="Region"`).
   - The region options (`England`, `Northern Ireland`, `Scotland`, `Wales`) were not embedded in the initial HTML, but loaded via a client script (`address.js`) from an external CDN.
2. **Conditional Question Dependencies:**
   - In `How did you first hear about KHS?`, selecting `"Other"` conditionally triggers a client-side jQuery rule that injects a new required text question: `Please share how you learned about Knight-Hennessy Scholars.*`.
   - Selecting any standard option (e.g. `My college's career services office`) keeps this field hidden and non-required.
3. **Asynchronous Lookup Autocomplete Overlays:**
   - The `Undergraduate institution` field uses an async suggest service (`lookup?type=school&q=...`).
   - When filled with a school name not in the US database (e.g. `Phenikaa University`), a floating div `"No matches found."` was appended below the input, physically overlaying and obscuring the next field (`Graduation date`).

#### Workaround Employed
- Re-observed the page after mutating `Country` to obtain fresh ref IDs for the newly mounted `Region` combobox.
- Inspected the CDN script logic to ensure selected options did not trigger unwanted mandatory sub-questions.
- Dispatched a clean `act: click` on a neutral label element (`n40`) to blur the autocomplete field and dismiss the floating overlay before taking verification screenshots.

#### Recommended Architectural Fix
- Add guidance to `SKILL.md` instructing agents to:
  - Perform step-wise `observe` cycles after selecting country or category fields that frequently trigger DOM cascading.
  - Dispatch a blur / neutral click after populating autocomplete comboboxes to dismiss floating suggestion popups.

---

## Action Items for OmniBrowser Development

| Priority | Component | Issue | Proposed Improvement |
| :--- | :--- | :--- | :--- |
| **P0** | `actions.py` | Native `<select>` click triggers blocking OS popup & 10s timeout | Intercept clicks on `<select>` nodes; reject with `NATIVE_SELECT_CLICK_BLOCKED` immediately, or route to `select` logic. |
| **P0** | `observe.py` | AX-tree hides `<option>` items for comboboxes | Extract and attach `options: [...] ` metadata to `role: "combobox"` controls in the observe response. |
| **P1** | `observe.py` | `STALE_CURSOR` on pages with background telemetry | Increase default `max_bytes` from 15 KB to 64 KB; make cursor validation resilient to minor non-control DOM attribute updates. |
| **P2** | `SKILL.md` | Agent knowledge gap on comboboxes & cascading forms | Update skill prompt: explicitly warn agents *never* to click native `<select>` elements, always use `max_bytes=65536` for large forms, and re-observe after changing country/region. |

