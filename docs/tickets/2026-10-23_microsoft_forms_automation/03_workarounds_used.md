# 03 — Workarounds Used During Execution

This document records the specific engineering workarounds used by the agent to circumvent the limitations outlined in `02_unsupported_areas.md` and complete the form filling.

---

### 1. Workaround for Inner-Pane Scrolling Failure

Since `scroll(direction="down")` failed with `UNSUPPORTED_SCROLL`, the agent used two alternative mechanisms to reposition the viewport:

#### Method A: Click-Driven `scrollIntoView`
- Even when off-screen, elements that exist in the DOM can be targeted via `act(ref=..., action="click")`.
- When Chrome dispatches a click event to an off-screen element, the browser engine automatically scrolls the ancestor scrollable container so the element enters the viewport.
- The agent targeted known input refs (e.g., textboxes at Question 8, 14, 21, 25) to step through the form vertically.

#### Method B: Synthetic `PageDown` Keyboard Dispatches
- When clicking an element risked changing its state, the agent focused a non-destructive element (such as an already-filled textbox) and dispatched keyboard events:
  ```json
  {
    "tab_id": "F7B26F7BC7838C13CA35CE563F713950",
    "ref": "<focused_textbox_ref>",
    "action": "press",
    "key": "PageDown"
  }
  ```
- This triggered container-level scrolling without requiring main-document wheel support.

---

### 2. Workaround for Cursor Pagination Overhead

To avoid making 7 individual interactive roundtrips each time the control hierarchy needed inspection, the agent wrote an external Python script (`dump_controls.py`) that leveraged the `omni` CLI directly:

```python
import subprocess
import json

tab_id = "F7B26F7BC7838C13CA35CE563F713950"
all_controls = []
cursor = None

while True:
    cmd = ["omni", "--observe", "--tab", tab_id]
    if cursor:
        cmd.extend(["--cursor", cursor])
    res = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(res.stdout)
    controls = data.get("controls", [])
    all_controls.extend(controls)
    if data.get("has_more") and data.get("next_cursor"):
        cursor = data.get("next_cursor")
    else:
        break

with open("controls.json", "w") as f:
    json.dump(all_controls, f, indent=2)
```

This reduced 7 separate agent-LLM conversation turns down to a single synchronous background command execution.

---

### 3. Workaround for Transient Controls & Ref Churn (List Reordering)

To reorder items in Question 14 without manual intervention for each shift:

1. **Focus Trigger:** Dispatched a click to the target ranking option (`Technology and AI`) to force Microsoft Forms to mount the transient reordering controls into the DOM.
2. **Re-Observe & Locate:** Ran a fresh `observe` to capture the newly generated ref for the button named `Move option up`.
3. **Stepwise Re-Observation Loop:** Because each click on `Move option up` altered the list order and invalidated refs, the agent ran a programmatic loop (`move_tech_up.py`):
   - At each step: re-observe the control list, search for `c["name"] == "Move option up"`, click that exact fresh ref, and pause 500ms for DOM layout stabilization.

---

### 4. Workaround for Visual-Only Target Coordinates

When testing whether direct coordinate clicking could bypass button ref invalidation:
1. Captured a viewport screenshot via `screenshot(full_page=false)`.
2. Loaded the image into Pillow (`PIL.Image`).
3. Extracted the exact pixel bounds of the active (brown-highlighted) ranking container:
   - Brown background bounds: `x=[322, 1049], y=[633, 671]`
   - Arrow region X: `[986, 1049]`
   - Up-arrow center calculated at: `(991, 649)` CSS pixels
4. Confirmed that `act(action="click_at", x=991, y=649)` accurately triggered viewport input using the screenshot's `1:1` CSS pixel mapping metadata.

---

### 5. Workaround for Incomplete Full-Page Verification

Because `full_page=true` screenshots were clamped to 756px:
- The agent sequentially navigated through five distinct vertical segments:
  1. Top: Questions 1–3 (`Forename`, `Surname`, `ID`)
  2. Upper-middle: Questions 4–7 (`Email`, `Course`, `Year`, `Study mode`)
  3. Middle: Questions 8–13 (`Nationality`, `Fee status`, `Prior participation`, `Goals`, `Skills`)
  4. Ranking: Questions 14–17 (`Project order`, `Explanation`, `Availability`, `Session`)
  5. Bottom: Questions 18–25 (`Conflicts`, `Internet`, `Country`, `Team preference`, `Postcode`, `Caring`)
- Captured a separate viewport screenshot at each position and inspected every input visually to confirm accurate data entry before concluding the task.
