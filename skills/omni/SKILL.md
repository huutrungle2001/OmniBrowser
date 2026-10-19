---
name: omni
description: >-
  Read and control an attached local CDP browser through OmniBrowser's Omni MCP or CLI, including
  paged observation, native input, PNG screenshots, scrolling and HTTP downloads.
  No arbitrary JavaScript tool or browser launching.
---

Use registered MCP `omni` when available; otherwise invoke
`/Users/huutrungle2001/Documents/OnGoing/OmniBrowser/.venv/bin/omni`.
Default CDP is `http://127.0.0.1:17082`; CLI accepts `--cdp-url`, MCP uses
its server's `OMNI_CDP_URL`. Do not launch Chrome yourself.

Seven operations:

- `list_tabs`: stable IDs, titles, URLs. Select an explicit ID, never an index.
- `goto(url, tab_id=...)` or explicit `new_tab=true`: navigation only.
- `observe(tab_id, cursor?, max_bytes=15360)`: content/links/controls and exact refs.
- `act(tab_id, ref, action, ...)`: native click, fill(value), press(key), or
  select(exactly one of value/label). Select requires native HTML select.
- `download(tab_id, url, output, mkdir=false, overwrite=false)`: safe HTTP GET.
- `screenshot(tab_id, full_page=false, output?)`: actual PNG image + metadata.
- `scroll(tab_id, direction="up"|"down", amount?)`: one document wheel input;
  amount is CSS pixels, default 75% viewport capped at 800.

Follow `next_cursor` while `has_more`; this continues all three streams, not just
content. Cursor works across CLI/MCP calls in the same unchanged extracted state.
On `STALE_CURSOR`, start a fresh observe without cursor. On `STALE_REF`, observe
again and select a fresh target. Navigation, detachment/changed identity, or
512-ref retention eviction can invalidate refs. Keep needed late-page refs fresh.

Each observe JSON page is bounded (2048..65536 bytes) and binds at most 96 refs.
`truncated`/omitted counts describe that response, including earlier/later pages.
Even `has_more=false` does not prove complete site coverage: inspect processing
limits, extraction truncation and unsupported iframe/closed-shadow counts.
Main document/open Shadow DOM only; cursor state excludes editable values.

Do not automatically replay mutations after failure, timeout or cancellation:
a side effect may already have happened. Inspect before deciding what to do.
No hidden observe/screenshot follows an action. Coordinate competing clients on
the same tab; MCP serializes only its own calls.

```sh
omni --list-tabs
omni --goto https://example.com --new-tab
omni --observe --tab TAB_ID
omni --observe --tab TAB_ID --cursor CURSOR
omni --act --tab TAB_ID --ref REF --action fill --value 'text'
omni --act --tab TAB_ID --ref REF --action press --key Enter
omni --act --tab TAB_ID --ref REF --action select --label 'Blue'
omni --act --tab TAB_ID --ref REF --action click
omni --scroll --tab TAB_ID --direction down --amount 600
omni --screenshot --tab TAB_ID --output /absolute/capture.png
omni --download --tab TAB_ID --url https://example.com/notes.pdf --output /absolute/notes.pdf
```

Screenshots may expose visible secrets; request only authorized pages. Limits:
6M pixels, 8192px/side, 4 MiB PNG; oversized full-page capture is rejected, not
resized/scrolled. CLI needs output; MCP saving is optional. Files require explicit
overwrite/mkdir. Scroll does not target inner panes or prove lazy loading complete.

Downloads use cookie-scoped HTTP GET, not Save dialogs. No POST/blob, browser-only
token, partitioned-cookie or challenge fallback. Do not print cookies or filled/
selected secrets; shell arguments/history can expose CLI values.
Installed MCP/skill schemas may need reconnect/new session; never restart another
agent or assume running clients already discovered the new tools.
