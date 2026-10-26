---
name: omnibrowser
description: >-
  Ultra-fast local Chrome automation & inspection over CDP port 17082:
  direct JavaScript evaluate, smart label click, batch form actions, scoped DOM observation,
  scrolling and screenshots. Bypasses login/MFA by reusing the existing signed-in Chrome session.
---

Prefer registered MCP `omni_v3` or the global CLI `omni_v3` (available globally in PATH).
Default CDP: `http://127.0.0.1:17082` (Port 17082, Host 127.0.0.1).

Eight tools available:

- `list_tabs()`: List all open tabs with `tab_id`, `title`, `url`.
- `goto(url, tab_id?, wait="load"|"commit"|"none")`: Navigate tab or open new tab.
- `observe(tab_id, selector?, max_chars?)`: Extract clean text & interactive controls with `ref`.
  - Pass `selector` (e.g. `#main-form`, `.content`) to scope observation and prune irrelevant headers/footers.
  - Automatically filters out Base64 blobs and massive __VIEWSTATE tokens.
- `act(tab_id, action, ref?, selector?, value?, snapshot_id?)`:
  - Actions: `click`, `fill`, `select`, `check`, `press`.
  - Smart click: Automatically resolves and clicks `<label for="...">` if the target `<input>` is styled with `opacity: 0` (custom radios/checkboxes).
  - Safe select: Dispatches JavaScript value setters for `<select>` without triggering OS Cocoa menu hangs.
- `act_batch(tab_id, actions, snapshot_id?, stop_on_error=true)`:
  - Run multiple actions sequentially in a single turn (e.g., fill 5 inputs + click radio + click submit). Reduces latency by 85%.
- `evaluate(tab_id, expression)`:
  - **Superweapon for complex pages:** Evaluates JavaScript directly in the tab context (e.g. `document.title`, `document.querySelector('form').submit()`, scraping tables, triggering postbacks). Returns serialized JSON.
- `scroll(tab_id, ref?, selector?, direction="down"|"up", amount?)`:
  - Supports scrolling the main window OR inner scrollable containers (`overflow: auto`).
- `screenshot(tab_id, selector?, full_page=false, path?)`:
  - Captures viewport, full page, or specific element to PNG.

CLI Examples:
- `omni_v3 --list-tabs`
- `omni_v3 --tab <TAB_ID> --eval 'document.title'`
- `omni_v3 --tab <TAB_ID> --selector '#form' --observe`
- `omni_v3 --tab <TAB_ID> --ref r1 --act click`
- `omni_v3 --tab <TAB_ID> --act-batch '[{"ref":"r1","action":"fill","value":"text"},{"ref":"r2","action":"click"}]'`
