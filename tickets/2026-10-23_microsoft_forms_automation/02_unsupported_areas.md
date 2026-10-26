# 02 — Unsupported Areas and Limitations Encountered

This document details the specific technical limitations, unsupported DOM patterns, and error codes encountered when running OmniBrowser against Microsoft Forms.

---

### 1. Main-Document Wheel Scrolling vs. Inner Pane Containers (`UNSUPPORTED_SCROLL`)

#### Observed Behavior
Calling `scroll(tab_id=..., direction="down", amount=1500)` resulted in immediate tool failure:
```json
{
  "ok": false,
  "error": {
    "code": "UNSUPPORTED_SCROLL",
    "message": "No unobstructed main-document wheel target; inner panes/iframes are unsupported."
  }
}
```

#### Technical Context
- Modern single-page web applications (Microsoft Forms, Microsoft Teams, Jira, Notion, Linear) frequently use fixed-height top-level layouts:
  ```css
  html, body {
    height: 100%;
    overflow: hidden;
  }
  .form-scroll-container {
    height: 100vh;
    overflow-y: auto;
  }
  ```
- OmniBrowser's `scroll` tool checks for an unobstructed wheel target on the top-level document window. When scrolling is sequestered inside an internal container `<div>`, OmniBrowser refuses the wheel dispatch entirely rather than targeting the scrollable container.

---

### 2. Clamped Full-Page Screenshot on Internal Scroll Containers

#### Observed Behavior
Calling `screenshot(tab_id=..., full_page=true)` returned:
```json
{
  "ok": true,
  "width": 1352,
  "height": 756,
  "full_page": true,
  "viewport": {"width": 1352, "height": 756},
  "capture": {"space": "page CSS pixels", "x": 0, "y": 0, "width": 1352, "height": 756}
}
```
The captured image showed only the top 756 pixels (Questions 1 to 3), cutting off the remaining 22 questions.

#### Technical Context
- CDP full-page screenshot capture calculates page height from `document.documentElement.scrollHeight` or `document.body.scrollHeight`.
- Because the top-level `body` height was clamped to 100% viewport height, the full-page screenshot captured only the viewport dimensions, missing all scrollable content contained within inner panes.

---

### 3. Ephemeral Ref Invalidation (`STALE_REF`) on Dynamic DOM Mutations

#### Observed Behavior
Whenever an action caused a localized re-render or list reordering, subsequent actions referencing previously observed nodes failed:
```json
{
  "ok": false,
  "error": {
    "code": "STALE_REF",
    "message": "Run --observe again before acting."
  }
}
```

#### Technical Context
- OmniBrowser assigns ephemeral, hash-based node identifiers (e.g., `F7B26F7BC7838C13CA35CE563F713950.d78531a950d0e360eb5a60efd23e4993.n208`).
- In dynamic React/Fluent UI components, selecting a radio button or reordering a list item triggers React virtual-DOM reconciliation, replacing underlying DOM elements and altering accessibility tree indices.
- Once a single mutation occurred, all previously dumped refs for adjacent or downstream controls became invalid, forcing full re-observation cycles.

---

### 4. Absence of Control Tree Filtering (Pagination Overhead)

#### Observed Behavior
A single `observe` call on Microsoft Forms returns at most ~36 to 70 controls before hitting the 15 KB (`max_bytes=15360`) or 96-binding limit. Exhausting the entire form required 7 sequential cursor pagination roundtrips:
- Chunk 1: 36 controls
- Chunk 2: 72 controls (total 108)
- Chunk 3: 70 controls (total 178)
- Chunk 4: 64 controls (total 242)
- Chunk 5: 68 controls (total 310)
- Chunk 6: 69 controls (total 379)
- Chunk 7: 10 controls (total 389)

#### Technical Context
- The accessibility tree included dozens of non-input decorative elements:
  - 25+ "Immersive Reader" icon buttons (`name='Immersive Reader'`)
  - Redundant `LabelText` nodes accompanying every radio option
  - Empty container buttons (`name=''`)
  - Image placeholder nodes
- Because `observe` has no filter flags (e.g., filter by role, visibility, or interactive-only), the agent spent multiple roundtrips ingesting non-actionable elements.

---

### 5. Inability to Target Transient / State-Dependent UI Controls

#### Observed Behavior
In the ranking question (Question 14):
- The reordering controls (`Move option up`, `Move option down`) were completely absent from the initial accessibility tree dump.
- They only mounted into the DOM after the specific ranking item received active focus.
- Attempting to target or move items without an intermediate focus click and re-observation was impossible.
