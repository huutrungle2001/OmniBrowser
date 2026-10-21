---
name: omnibrowser
description: >-
  Read and interact with an existing local Chrome/CDP session using OmniBrowser's
  MCP or CLI: page content, controls, navigation, screenshots and file downloads.
---

Prefer registered MCP `omni`. If unavailable, use the installed `omni` CLI;
run `omni --help` for flags. Default CDP: `http://127.0.0.1:17082`.
CLI overrides with `--cdp-url`; MCP uses its server's `OMNI_CDP_URL`.

Workflow: list_tabs → observe → act/download/screenshot/scroll → observe again.

Seven tools (MCP argument names; `?` means optional):

- `list_tabs()`: tab IDs, titles and URLs.
- `goto(url, tab_id=...)` or `goto(url, new_tab=true)`: navigate or explicitly open a tab.
- `observe(tab_id, cursor?, max_bytes=15360)`: clean content, links and controls with refs.
- `act(tab_id, ref, action, ...)`: click; fill with `value`; press with `key`;
  select a native HTML dropdown with exactly one of `value` or `label`.
  Or `act(tab_id, action="click_at", x=..., y=...)`, without ref/value/label/key.
- `download(tab_id, url, output, mkdir=false, overwrite=false)`: cookie-authenticated HTTP GET.
- `screenshot(tab_id, full_page=false, output?)`: PNG; CLI requires an output path.
- `scroll(tab_id, direction="up"|"down", amount?)`: main-document scroll in CSS pixels.

Use returned `tab_id`/`ref` only; never guess indexes or selectors.
Follow `next_cursor` while `has_more` for content, links, controls AND frames.
Observe includes nested/cross-origin/OOPIF frames; controls/links own `frame_id`.
Refs select exact frame/document/nodes; never choose frames by URL/label/index.
Check unread/limited frame statuses before claiming complete coverage.
On `STALE_CURSOR`, restart observe without a cursor.
On `STALE_REF`, observe again and choose a fresh ref.
After action errors/timeouts, inspect before retrying: the action may have happened.
For canvas/visual-only targets, use a viewport screenshot then `click_at`.
Use `image_to_input` metadata: image pixels may differ from viewport CSS pixels.
Account for client image resizing. Full-page positions are not viewport input.
Capture again after layout/scroll changes; no auto-correction/retry. Pinch zoom,
offset visual viewports, canvas typing and drag operations are unsupported.

CLI: `omni --observe --tab TAB_ID`; `omni --act --tab TAB_ID --ref REF --action click`.

Do not launch Chrome or execute arbitrary JavaScript; use these tools only.
