# Technical Report & Ticket: Authenticated Data Scraping via OmniBrowser

- **Date:** 2026-10-25
- **Target Application:** Symplicity CareerHub — myAdvantage (University of Warwick Student Opportunity)
- **Target URL:** `https://myadvantage.warwick.ac.uk/s/studentopportunity/events/bookings?page=1`
- **Tooling:** OmniBrowser v2 (MCP & CLI over CDP `http://127.0.0.1:17082`), Python 3.11, PyYAML.
- **Objective:** High-fidelity structured extraction of 18 booked university events into strict YAML and JSON schemas (`events.yaml`, `events.json`).

---

## 1. Executive Summary

While OmniBrowser proved invaluable by allowing instant reuse of an existing, authenticated SSO/Shibboleth Chrome session (completely bypassing multi-factor authentication, bot detection, and ephemeral session cookies), using OmniBrowser for **structured, multi-attribute data extraction** revealed fundamental impedance mismatches between its **UI-action design** and the requirements of **high-fidelity data scraping**.

The primary friction points and pitfalls encountered were:

1. **Semantic Flattening & Attribute Misattribution (The "ENDORSED" Badge Pitfall):** OmniBrowser's linear accessibility text stream strips container hierarchy, causing badges and metadata located at the end of a card container to appear linearly adjacent to the *next* card container. Without visual or DOM inspection, an agent will misattribute badges (e.g. assigning the purple `ENDORSED` badge of Card 2 to Card 3).
2. **Text Concatenation Across Adjacent Nodes:** Inline headers and metadata elements rendered adjacent in the DOM are flattened into concatenated strings without whitespace delimiters (e.g. `Using AI for CVs and applicationsCareers workshops`), requiring complex string deconstruction.
3. **Dual DOM Trees & Silent Pagination Duplication:** Modern responsive web applications (like Symplicity CareerHub) maintain parallel DOM branches for desktop versus mobile/screen-reader viewports. OmniBrowser's `observe` traverses both, resulting in an exact 100% duplicate list of all 18 events across chunk pagination.
4. **Opacity of CSS-Driven Media (Background Images):** Thumbnail artwork rendered via CSS (`style="background-image: url(...)"`) is completely invisible in OmniBrowser's `observe` text, links, and controls streams.
5. **Non-Breaking Whitespace (`\u00a0`) Escapes:** Timestamp ranges use Unicode non-breaking spaces (`\u00a0`), which pollute downstream serialization (causing backslash-escapes in YAML outputs like `1:00\_PM`).
6. **Multi-Cursor Pagination Overhead:** Full-page observation required multiple round-trips with `has_more` and `next_cursor`, consuming significant token budget and wall-clock latency for a relatively small dataset of 18 cards.

---

## 2. Detailed Failure Modes, Root Causes & Empirical Evidence

### 2.1 The "ENDORSED" Attribute Misattribution Pitfall

#### Observed Behavior
When retrieving page content via `mcp__omni__observe`, the linear text stream around Events #2 and #3 appeared as:
```text
Games HubParties & socials
- You are booked
27OCT
Tue 27 Oct 2026, 3:00 PM - 5:00 PM
The Nook, University of Warwick
Together at Warwick
Save
ENDORSED
A career in quant finance (for penultimate, final year PhD & post doc STEM students, with pizza!)Careers workshops
- You are booked
27OCT
Tue 27 Oct 2026, 4:00 PM - 5:00 PM
Mathematical Sciences - MB0.07, University of Warwick
- Organisational awareness
- Self-awareness
Pizza & refreshments & networking with iSAM representatives
Save
```
A linear parser or LLM evaluating this text block will naturally parse `ENDORSED` as a leading badge for **Event #3** (*"A career in quant finance"*).

#### Root Cause (DOM Hierarchy Inspection)
Inspecting the actual DOM nodes via CDP revealed:
```html
<div class="entity-card-container"> <!-- Card 2: Games Hub -->
  <div class="entity-card-img" style="..."></div>
  <div class="entity-card-body-container">
    <div class="entity-card-body">
      <a class="entity-card-title" ...>Games Hub</a>
      <span class="text-muted">Parties & socials</span>
      ...
      <div class="event-card-summary text-muted">Together at Warwick</div>
    </div>
  </div>
  <div class="entity-card-actions">
    <button title="Save">...</button>
    <div class="entity-card-endorsements">
      <div class="_container_1y2au_1" style="...">
        <!-- Purple star shield icon -->
        <div class="_endorsedWrapper_1y2au_28">
          <div class="_endorsed_1y2au_28">ENDORSED</div>
        </div>
      </div>
    </div>
  </div>
</div> <!-- End of Card 2 -->

<div class="entity-card-container"> <!-- Card 3: Quant Finance -->
  ...
```
The `ENDORSED` badge is located inside `entity-card-actions` at the bottom of the Card 2 container. Because OmniBrowser strips all boundary tags (`</div>`), the trailing action item of Card 2 directly precedes the header of Card 3 in the text stream.

#### Workaround Applied
We performed a screenshot capture via `mcp__omni__screenshot` and scoped DOM query to confirm that the badge visually and structurally belonged to **Games Hub**, preventing a critical epistemic error in the resulting database.

---

### 2.2 Text Concatenation Without Whitespace Delimiters

#### Observed Behavior
In the `observe` text output:
- `Using AI for CVs and applicationsCareers workshops`
- `Games HubParties & socials`
- `TIMS 2 Critical ThinkingWMG Community, Social and Wellbeing`
- `How I went from zero experience to become a data engineerCareers workshops`

#### Root Cause
In the HTML structure:
```html
<a class="entity-card-title" href="...">Using AI for CVs and applications</a>
<span class="text-muted">Careers workshops</span>
```
The `<a>` element and `<span class="text-muted">` have no whitespace between them in raw markup. Because both are inline or inline-block elements, OmniBrowser's text extractor flattens them directly into one contiguous token sequence without introducing a newline or space.

#### Workaround Applied
Cross-referencing the exact `text` property from `observe.links` (which captures the exact link text from the anchor tag) and stripping that prefix from the combined line to reliably isolate the category.

---

### 2.3 Responsive Dual-Tree Duplication

#### Observed Behavior
Concatenating the full content across pagination cursors resulted in:
- Events 1 through 18 listed sequentially in Chunks 1 and 2.
- Immediately following Event 18 (`WMG Movie Showing: Comedy`), Events 1 through 18 appeared **a second time** in the content stream.

#### Root Cause
CareerHub renders two parallel DOM structures for accessibility and responsive layouts:
1. Primary desktop card grid/list.
2. Alternate responsive list with distinct CSS classes or hidden media query wrappers.
Because both DOM branches exist in the accessibility tree and were not set to `display: none` (using off-screen transforms or media queries that Chromium still computes for accessibility), OmniBrowser visited both containers sequentially.

#### Workaround Applied
Deduplicating cards using the unique entity ID in the URL (`/students/events/detail/<id>`) and bounding extraction to the primary container.

---

### 2.4 Invisible Media: CSS Background Images

#### Observed Behavior
Card thumbnails were missing from both the `observe` content text and the `observe.links` stream.

#### Root Cause
Modern web cards frequently load visual assets as CSS background images:
```html
<div class="entity-card-img" style="background-image: url('/services/students/events/file?key=3%2Ftest.jpg'); background-size: cover;"></div>
```
OmniBrowser only collects standard `<img>` tags and `<a>` links. Background image URLs are completely omitted from the tool output.

#### Workaround Applied
Direct CDP extraction of the computed style / inline style attribute on `.entity-card-img`.

---

## 3. Structural Comparison: UI Automation vs. Data Scraping

| Feature / Need | OmniBrowser v2 (Current) | Data Scraping Requirement |
| :--- | :--- | :--- |
| **Primary Focus** | User-action simulation (`click`, `fill`, `press`) | High-fidelity, structured object extraction |
| **DOM Hierarchy** | Flattened text stream; boundaries lost | Preserved parent-child containment (`container -> item`) |
| **CSS Classes & Styles** | Stripped (except for role/expanded states) | Crucial for status (`label-success`), media (`background-image`) |
| **Throughput & Latency**| Multi-roundtrip cursor streaming (15 KB limit) | Single-call atomic JSON/YAML extraction |
| **Session State** | ✅ Native CDP connection retains login/SSO | ✅ Native CDP connection retains login/SSO |

---

## 4. Architectural Proposals for Future OmniBrowser Improvement

To make OmniBrowser an effective tool for both UI interaction and data scraping, we propose the following concrete feature enhancements:

### Proposal 1: Scoped Observation Tool (`observe_scoped`)
Allow `observe` to target a specific container ref or CSS selector rather than the entire document:
```typescript
mcp__omni__observe({
  tab_id: string,
  scope_ref?: string,       // e.g. "DC84...n25" (parent container ref)
  scope_selector?: string   // e.g. ".entity-card-container"
})
```
*Impact:* Eliminates chunk churn, keeps child attributes strictly bounded to their parent container, and prevents cross-card text bleeding.

### Proposal 2: Structured Scraper Primitive (`mcp__omni__extract`)
Provide a purpose-built declarative extraction tool that operates safely within the tab without requiring full-page text flattening:
```typescript
mcp__omni__extract({
  tab_id: string,
  selector: string,         // e.g. ".entity-card-container"
  schema: {
    title: { selector: ".entity-card-title", attr: "text" },
    url: { selector: ".entity-card-title", attr: "href" },
    category: { selector: ".text-muted.entity-card-mb-5", attr: "text" },
    status: { selector: ".label", attr: "text" },
    image_url: { selector: ".entity-card-img", attr: "css:background-image" },
    skills: { selector: ".entity-card-unordered-list li", multiple: true, attr: "text" }
  }
})
```
*Impact:* Yields zero-token-waste, structured, validated JSON directly in 1 round-trip (< 200ms) while preserving the authenticated session.

### Proposal 3: Inter-Node Boundary Delimitation
In the internal accessibility tree-to-text conversion:
- Automatically inject a single space or newline between adjacent block/inline nodes if no trailing/leading whitespace exists between them.
- Ensure container boundaries (like cards or list items) emit a standardized separator (e.g. `--- [Item Boundary] ---`) to prevent linear attribute bleeding.

### Proposal 4: Media & Image Asset Capture
Extend the `observe` asset collector to detect elements with non-empty `background-image` or CSS mask sources and report them in a new `media` or `images` stream alongside `links` and `controls`.

---

## 5. Artifacts Generated from this Investigation

- Extracted Data: `/Users/huutrungle2001/Documents/OnGoing/Trung-Le-Huu/events.yaml`
- Extracted JSON: `/Users/huutrungle2001/Documents/OnGoing/Trung-Le-Huu/events.json`
- Target System: Warwick Student Opportunity myAdvantage portal.

