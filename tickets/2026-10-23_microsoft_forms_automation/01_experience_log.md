# 01 — Experience Log: Step-by-Step Execution

This document records the chronological sequence of events, agent decisions, and tool interactions during the automated form filling of the Microsoft Forms application on 2026-10-23.

---

### Phase 1: Tab Discovery & Form Inspection

1. **Tab Discovery:**
   - Ran `list_tabs` on OmniBrowser.
   - Target tab identified:
     - `tab_id`: `F7B26F7BC7838C13CA35CE563F713950`
     - `title`: `TeamWork Spring 2027 Application Form`
     - `url`: `https://forms.cloud.microsoft/Pages/ResponsePage.aspx?id=vc-6Ce9HZUSSZTVG8ur2vJXTPrUjGsNNmJ7yZpJTuY5UQ0RaMkg4WEQ5VVhIOVRSU0szUlhRWkpPTyQlQCN0PWcu`

2. **Initial Observation:**
   - Executed `observe(tab_id="F7B26F7BC7838C13CA35CE563F713950")`.
   - Result:
     - First 36 controls returned.
     - Response contained: `"controls_omitted": 312`, `"has_more": true`, and a `next_cursor` token.
     - Controls only spanned Questions 1 to 3 (Forename, Surname, Warwick ID).
   - **Finding:** A single `observe` call without pagination only reveals the top fraction of a 25-question form.

---

### Phase 2: Control Mapping & Pagination

1. **Pagination Churn:**
   - To map which refs corresponded to Questions 4 through 25, multiple sequential `observe` calls following `next_cursor` were required.
   - In total, 7 successive cursor calls were needed to exhaust all 389 accessibility controls on the page.
   - An ad-hoc Python script (`dump_controls.py`) was written to paginate through the full list and dump it to JSON.

2. **Control Analysis:**
   - Text inputs (Questions 1–5, 8, 11, 12, 13, 15, 22, 24, 25) were registered as `role: "textbox"`.
   - Radio options (Questions 6, 7, 9, 10, 16, 17, 19, 21, 23) were exposed primarily as `role: "button"` with the option label as `name`, alongside sibling `LabelText` nodes.
   - Question 14 (Ranking list) presented a composite list of 7 items, each containing multiple sibling `button` and `option` nodes.

---

### Phase 3: Field Population (Questions 1 to 13)

1. **Standard Text Inputs (Questions 1–5, 8):**
   - Executed `act(ref=..., action="fill", value=...)` for:
     - Q1 (Forename: `Trung`)
     - Q2 (Surname: `Le`)
     - Q3 (Warwick ID: `5838393`)
     - Q4 (Email: `Trung.Le@warwick.ac.uk`)
     - Q5 (Course: `MSc Applied Artificial Intelligence`)
     - Q8 (Nationality: `Vietnam`)
   - All standard fills completed synchronously and accurately.

2. **Radio Group Selections (Questions 6, 7, 9, 10):**
   - Executed `act(ref=..., action="click")` on the button nodes corresponding to:
     - Q6: `Post Graduate Taught`
     - Q7: `Full Time`
     - Q9: `I am an international student`
     - Q10: `No`
   - Clicks toggled the underlying radio inputs without issue.

3. **Conditional Question Behavior (Question 11):**
   - Even after selecting "No" on Question 10 ("Have you previously taken part?"), Question 11 ("Which year did you take part?") remained in the DOM with `required: true`.
   - Filled `N/A` into Question 11 to satisfy form validation.

4. **Multi-Line Textareas (Questions 12 & 13):**
   - Successfully filled Question 12 (goals) and Question 13 (skills) using `act(action="fill")`.

---

### Phase 4: Ranking Question Interaction (Question 14)

1. **Scrolling Failure:**
   - Attempted to scroll down to view Question 14 using `scroll(direction="down", amount=1500)`.
   - **Error:** `UNSUPPORTED_SCROLL: No unobstructed main-document wheel target; inner panes/iframes are unsupported.`
   - The agent had to click on an element known to belong to Question 14 to force the browser's native `scrollIntoView`.

2. **Transient Reordering Buttons:**
   - When inspecting the ranking items, "Move option up" and "Move option down" buttons were initially absent from the accessibility tree.
   - Clicking an item (e.g., "Technology and AI") focused it, causing the UI to mount:
     - Button `Move option up`
     - Button `Move option down`
   - These buttons were only discoverable by running a fresh `observe` after focusing the item.

3. **Reordering Execution:**
   - To place "Technology and AI" into slot #1 (from slot #6), it had to be moved up 5 times.
   - Each click on `Move option up` reordered the DOM list.
   - A Python loop script (`move_tech_up.py`) was executed to repeatedly observe fresh refs and click `Move option up`.
   - The same process was repeated for "Research and Innovation" to move it from slot #5 to slot #2.

---

### Phase 5: Lower Form Fields & Final Verification (Questions 15 to 25)

1. **Populating Questions 15 to 25:**
   - Q15 (Order explanation): Filled text via `act(fill)`.
   - Q16 (Availability 10h/wk): Clicked "Yes".
   - Q17 (Compulsory 2-hr session): Clicked "Yes I will be available to attend...".
   - Q18 (Reason if unavailable): Left blank (available).
   - Q19 (Other employment/holidays): Clicked "No".
   - Q20 (Details): Left blank (N/A).
   - Q21 (Computer/internet access): Clicked "Yes".
   - Q22 (Country during project): Filled `UK`.
   - Q23 (Warwick-only team preference): Clicked "I would be happy in either type of team".
   - Q24 (Widening Participation UK postcode): Filled `I'm from Vietnam, no valid UK postcode`.
   - Q25 (Caring responsibilities): Filled 44-word statement.

2. **Navigating the Lower Viewport:**
   - Because `scroll` was unavailable, navigation to lower sections was achieved by clicking on lower input refs or focusing inputs and dispatching `PageDown` keyboard events (`act(action="press", key="PageDown")`).
   - Attempting to click older refs that were no longer in the DOM or whose identifiers shifted resulted in `STALE_REF` and `TIMEOUT`, requiring fresh observation passes.

3. **Visual Verification:**
   - Because `screenshot(full_page=true)` did not capture beyond the initial 756px viewport, the agent navigated through 5 distinct viewport sections (top, upper-middle, ranking, lower-middle, bottom).
   - Captured viewport screenshots at each position and inspected them with an image reader to verify that all 25 questions contained the intended selections.
   - The "Submit" button was intentionally left untouched.
