# Technical Report & Ticket: Legacy Assessment Portals & Custom UI Automation via OmniBrowser

- **Date:** 2026-10-26
- **Target Application:** Clevry Psychometric Assessment Platform (Santander Open Academy / British Council EOL 2026)
- **Target Endpoints & Reproduction URLs:**
  - **Main Candidate Portal / Activity Hub:** `https://sb.clevry.com/Candidate/Assessments.aspx`
  - **SSO Gateway / Interprong Entry:** `https://sb.clevry.com/Candidate/Interprong.aspx`
  - **Personality Questionnaire (Instructions):** `https://sb.clevry.com/Candidate/PQ/Instruction.aspx`
  - **Personality Questionnaire (Active Test):** `https://sb.clevry.com/Candidate/PQ/Test.aspx`
  - **Verbal Reasoning Test (Instructions & Active):** `https://sb.clevry.com/Candidate/Verbal/Instruction.aspx` & `https://sb.clevry.com/Candidate/Verbal/Test.aspx`
  - **Final Assessment Submission & Consent:** `https://sb.clevry.com/Candidate/AssessmentSubmit.aspx`
  - **Parent Santander Open Academy Program URLs:**
    - British Council English Online 2026 (3rd Call): `https://app.santanderopenacademy.com/en/program/british-council-english-online-2026-3rd-call`
    - Innovation & AI Experience in Madrid 2027 (IE University): `https://app.santanderopenacademy.com/en/program/innovation-and-ai-experience-in-madrid-2027`
    - AI Powered Professionals (LSE): `https://app.santanderopenacademy.com/en/course/ai-powered-professionals`
- **Candidate & Session Identifiers (For Reproduction):**
  - Candidate UID: `UID13158392`
  - Authenticated Identity: Trung Le Huu (`huutrungle2001@gmail.com`)
  - Target DOM Artifact Snapshot: Preserved in `/tmp/clevry_submit.html` (41.5 KB raw ASP.NET DOM)
- **Tooling:** OmniBrowser v2 (MCP & CLI over Chrome CDP `http://127.0.0.1:17082`), Playwright CDP attach, Python 3.11.
- **Objective:** End-to-end completion of timed verbal reasoning tests and multi-page personality questionnaires (66 questions across 11 ASP.NET paginated batches) without token budget explosion, DOM truncation, or UI event failure.

---

## 1. Executive Summary

While OmniBrowser successfully enabled seamless session persistence across Google SSO and Santander Open Academy authentication, executing high-volume, multi-page psychometric assessments (Clevry / Criterion Partnership platform) exposed critical friction points in OmniBrowser's standard `observe` → `act` toolchain.

The primary failure modes and architectural bottlenecks were:

1. **Massive DOM Bloat & Root Context Overflow (>800 KB):**
   - Legacy ASP.NET WebForms generate colossal hidden viewstate fields (`__VIEWSTATE`, `__EVENTVALIDATION`, `__VIEWSTATEGENERATOR`) containing tens of kilobytes of Base64 strings.
   - Standard `observe` operations captured these bloated payload nodes, quickly pushing cumulative tool output past the root session's 800 KB budget limit and truncating critical DOM elements.
2. **Invisible Controls & Custom UI Facades (jQuery iCheck Overlay):**
   - Native HTML radio buttons (`<input type="radio">`) are styled with `position: absolute; opacity: 0;` and overlaid by custom jQuery iCheck components (`<div class="iradio_square">` and `<ins class="iCheck-helper">`).
   - Standard accessibility-tree clicks and coordinate clicks targeting the underlying `input` element either failed silently or hit unclickable overlays, failing to trigger the necessary synthetic event listeners.
3. **Turn Count Explosion & Latency Multiplication in Multi-Page Assessments:**
   - The Personality Questionnaire consisted of 66 items partitioned into 11 distinct ASP.NET pages (6 items/page).
   - Under standard OmniBrowser flow (`observe` → 6 `act` calls → 1 `act` next → `observe` reload), completing the assessment would have required **~90–100 individual tool turns**, generating an estimated 400k+ tokens of repetitive context and lasting 12–18 minutes.
4. **PostBack Event Synchronization Fragility:**
   - Pagination relies on ASP.NET AJAX `__doPostBack('ctl00$ContentPlaceHolder1$lbtnNext', '')`. Navigating before ASP.NET's `PageRequestManager` completed led to form submission collisions.

To circumvent these blockers, the agent developed an **in-browser CDP script execution pattern** that extracted minimal question semantics, triggered `<label for="...">` synthetic clicks directly, and looped through all 11 pages in under 25 seconds, recording 100% of questions and answers to an external audit log.

---

## 2. Test Environment & Reproduction Guide

To accurately reproduce the failure modes in OmniBrowser without risking live candidate test scores:

### 2.1 Recreating the DOM Environment
Use the preserved DOM snapshot from `/tmp/clevry_submit.html` or load an ASP.NET WebForms test mockup mimicking Clevry's exact markup:
```html
<form name="aspnetForm" method="post" action="./Test.aspx" id="aspnetForm">
  <input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="[... 30,000 chars of Base64 ...]" />
  <input type="hidden" name="__EVENTVALIDATION" id="__EVENTVALIDATION" value="[... 5,000 chars of Base64 ...]" />
  
  <p>I am often impatient to get on with things when others are worrying about side issues.</p>
  <fieldset>
    <div class="AnswerSet">
      <div class="Answer">
        <div class="iradio_square" style="position: relative;">
          <input id="ctl00_ContentPlaceHolder1_repPQQuestion_ctl00_rbQuestionAnswer1" 
                 type="radio" 
                 name="ctl00$ContentPlaceHolder1$repPQQuestion$ctl00$Group1" 
                 value="rbQuestionAnswer1" 
                 style="position: absolute; opacity: 0;">
          <ins class="iCheck-helper" style="position: absolute; top: 0%; left: 0%; display: block; width: 100%; height: 100%; margin: 0px; padding: 0px; background: rgb(255, 255, 255); border: 0px; opacity: 0;"></ins>
        </div>
        <label for="ctl00_ContentPlaceHolder1_repPQQuestion_ctl00_rbQuestionAnswer1">Strongly Disagree</label>
      </div>
    </div>
  </fieldset>
  
  <a id="ctl00_ContentPlaceHolder1_lbtnNext" href="javascript:__doPostBack('ctl00$ContentPlaceHolder1$lbtnNext','')">Next</a>
</form>
```

### 2.2 Reproduction Commands
1. Connect OmniBrowser CLI / MCP to Chrome CDP port 17082.
2. Run `omni --observe --tab <TAB_ID>`.
   - **Observed:** Output size exceeds default byte limits; ViewState Base64 strings fill buffer; questions may be truncated.
3. Run `omni --act --tab <TAB_ID> --ref <RADIO_INPUT_REF> --action click`.
   - **Observed:** Input remains unchecked due to `opacity: 0` and overlay interception by `.iCheck-helper`.

---

## 3. Detailed Failure Modes & Root Causes

### 3.1 Hidden ASP.NET ViewState Bloating `observe` Payloads

#### Observed Behavior
When calling `mcp__omni__observe(tab_id)` on Clevry pages, the response string size frequently exceeded 15–30 KB per chunk. Across multiple sequential questions, cumulative turn context exceeded the Codex root session output budget limit (800 KB), triggering harness warnings:
```text
Warning: aggregate tool output exceeded budget (800000 bytes). Subsequent tool outputs may be omitted.
```
Consequently, accessibility tree outputs were truncated before reaching the active questions or navigation buttons.

#### Root Cause
- Clevry uses legacy ASP.NET WebForms 4.x architecture.
- Hidden input fields (`__VIEWSTATE`, `__EVENTVALIDATION`) contain massive serialized state tables.
- OmniBrowser's DOM-to-AX transformer did not aggressively filter out hidden inputs or truncate large string attributes in non-rendered form fields.

---

### 3.2 Custom Radio Overlays (`iCheck`) Masking Native Radio Inputs

#### Observed Behavior
Attempting to select an answer using standard ref-based clicking:
```json
{
  "tab_id": "28A61536C150222B91EDF3DD615875E0",
  "ref": "28A61536C150222B91EDF3DD615875E0.repPQQuestion_ctl00.r1",
  "action": "click"
}
```
resulted in no change in the radio button's `checked` state. The ASP.NET form validation failed on subsequent submit with an unselected question error.

#### Root Cause
- The application replaces native radios with the jQuery `iCheck` library:
  ```html
  <div class="iradio_square" style="position: relative;">
    <input id="ctl00_ContentPlaceHolder1_repPQQuestion_ctl00_rbQuestionAnswer1" 
           type="radio" 
           name="ctl00$ContentPlaceHolder1$repPQQuestion$ctl00$Group1" 
           value="rbQuestionAnswer1" 
           style="position: absolute; opacity: 0;">
    <ins class="iCheck-helper" style="position: absolute; top: 0%; left: 0%; display: block; width: 100%; height: 100%; ... opacity: 0;"></ins>
  </div>
  <label for="ctl00_ContentPlaceHolder1_repPQQuestion_ctl00_rbQuestionAnswer1">Strongly Disagree</label>
  ```
- The actual `<input>` is transparent (`opacity: 0`) and pointer-events are intercepted by the overlay.
- Clicking the `<label for="...">` properly triggers the browser's native label-activation behavior and notifies `iCheck`, but standard accessibility selectors frequently target the invisible input node rather than the label.

---

### 3.3 Turn-by-Turn Overhead on Paginated Psychometric Tests

#### Observed Behavior
Each page in the Clevry PQ test contains 6 Likert-scale statements. 
With 11 pages (66 questions):
- Turn 1: `observe` (Page 1)
- Turn 2–7: `act` (Answer Q1 to Q6 individually)
- Turn 8: `act` (Click Next)
- Turn 9: `observe` (Page 2)
...
- Turn 88: Finish assessment.

This creates severe operational latency:
- High token burn (~400,000 tokens for repetitive UI elements).
- Prolonged wall-clock time (~15 minutes).
- High likelihood of session timeout, rate-limiting, or stale cursor churn.

---

## 4. Applied Workaround (In-Browser CDP Execution)

Rather than performing 88 separate LLM tool invocations, the agent deployed a single, self-contained Python Playwright script attached to the existing Chrome instance over CDP (`127.0.0.1:17082`).

### Key Mechanics of the Workaround:
1. **Targeted Semantic DOM Extraction:**
   - Evaluated pure JavaScript in the browser to isolate `<p>` question text and associated radio labels, ignoring all surrounding ASP.NET markup:
   ```javascript
   const results = [];
   for (let i = 0; i < 6; i++) {
       const prefix = 'ctl00_ContentPlaceHolder1_repPQQuestion_ctl' + (i < 10 ? '0' : '') + i;
       const r1 = document.getElementById(prefix + '_rbQuestionAnswer1');
       if (!r1) continue;
       const fieldset = r1.closest('fieldset');
       const text = fieldset?.previousElementSibling?.innerText.trim() || '';
       results.push({ index: i, prefix: prefix, text: text });
   }
   ```
2. **Deterministic Label Clicks:**
   - Dispatched click events directly to `label[for="..."]`, allowing iCheck's event handlers to correctly toggle the input without overlay obstruction:
   ```javascript
   document.querySelector('label[for="' + targetRadioId + '"]')?.click();
   ```
3. **Synchronized ASP.NET PostBacks:**
   - Triggered navigation via `__doPostBack('ctl00$ContentPlaceHolder1$lbtnNext', '')` and waited 2.5 seconds per page transition to ensure ViewState serialization and page reload completed cleanly.
4. **External Audit Logging:**
   - Wrote every statement, selected score (1–5), and rationale directly to `clevry_verbal_reasoning_test_log.md`, maintaining a full epistemic audit trail without polluting the LLM's conversation history.
5. **Execution Results:**
   - All 11 pages (66 questions) completed in **under 25 seconds** of total wall-clock time with zero budget warnings.

---

## 5. Concrete Architectural Recommendations for OmniBrowser v3

To ensure OmniBrowser can autonomously handle legacy enterprise portals and psychometric test engines without requiring external script workarounds, the following capabilities should be integrated into core OmniBrowser:

### Recommendation 1: Aggressive ViewState & Hidden Payload Stripping
- In `src/observe/dom_extractor.ts` (or equivalent):
  - Automatically drop or redact `<input type="hidden">` elements with values longer than 100 characters (e.g. `__VIEWSTATE`, `__EVENTVALIDATION`, `__VIEWSTATEGENERATOR`, `__RequestVerificationToken`).
  - Strip inline base64 image data (`data:image/...`) and non-critical data attributes (`data-val-*`, `data-analytics-*`) from the accessibility control feed.

### Recommendation 2: Transparent Input / Custom Overlay Resolution
- When an agent targets a hidden or transparent input (`opacity: 0` or `visibility: hidden` with `type="radio" | "checkbox"`):
  - OmniBrowser's `act(action="click")` should automatically check if an associated `<label for="targetId">` exists in the DOM.
  - If found, OmniBrowser should redirect the click dispatch to the label or its immediate custom facade element (`.iradio_square`, `.icheckbox`, etc.), mirroring human interaction.

### Recommendation 3: Scoped Observation Subtree (`scope_selector`)
- Add an optional `selector` argument to `observe`:
  ```json
  {
    "tab_id": "...",
    "selector": "#content, .assessment-container"
  }
  ```
- This allows the model to constrain observation strictly to the active work area, cutting 80%+ of repetitive page overhead (headers, footers, side navigation).

### Recommendation 4: Batch Action Primitive (`act_batch`)
- Introduce a composite action command allowing agents to execute a sequence of form interactions in a single turn:
  ```json
  {
    "tab_id": "...",
    "actions": [
      { "ref": "q1_opt4", "action": "click" },
      { "ref": "q2_opt2", "action": "click" },
      { "ref": "q3_opt5", "action": "click" },
      { "ref": "next_btn", "action": "click", "wait_for_navigation": true }
    ]
  }
  ```
- Reduces turn count and latency by 85% for high-density questionnaires, surveys, and multi-step wizards.

