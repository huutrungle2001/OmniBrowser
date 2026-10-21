# OmniBrowser v2

A small attach-only Chrome/Chromium helper: independent CLI and persistent STDIO
MCP, seven commands, no agent loop/broker/recipes or arbitrary eval tool. Python
3.10+; existing runtime dependencies only (Playwright, httpx, markdownify, MCP).

## Install and entries

```sh
git clone https://github.com/huutrungle2001/OmniBrowser-v2.git OmniBrowser
cd OmniBrowser
python3 -m venv .venv
.venv/bin/python -m pip install -e .
export PATH="$PWD/.venv/bin:$PATH"
export OMNI_CDP_URL=http://127.0.0.1:17082
```

Chrome must already expose loopback CDP; Omni never launches a browser/profile.
Default is port 17082; CLI accepts `--cdp-url`, MCP reads `OMNI_CDP_URL`.
Playwright 1.64+ attach uses `no_defaults=True`, leaving download policy, focus
and media defaults unchanged. Do not expose CDP to a network.

Root `python /absolute/path/OmniBrowser/omni.py`, installed `omni`, and `omni-mcp`
work independently of cwd. `--help`/`--version` do not contact Chrome.
For Python package imports from the checkout, use `PYTHONPATH=src` with safe-path
mode or run outside the root: the development entry `omni.py` otherwise shadows
the installed package in ordinary `python -c`/stdin imports.

## Commands

```sh
omni --list-tabs
omni --goto https://example.com --new-tab
omni --goto https://example.com --tab TAB_ID
omni --observe --tab TAB_ID
omni --observe --tab TAB_ID --cursor CURSOR_FROM_PREVIOUS_RESULT
omni --act --tab TAB_ID --ref REF --action click
omni --act --tab TAB_ID --ref REF --action fill --value 'text'
omni --act --tab TAB_ID --ref REF --action press --key Enter
omni --act --tab TAB_ID --ref REF --action select --value blue
omni --act --tab TAB_ID --ref REF --action select --label Blue
omni --act --tab TAB_ID --action click_at --x 320.5 --y 240
omni --scroll --tab TAB_ID --direction down --amount 600
omni --screenshot --tab TAB_ID --output /absolute/capture.png
omni --screenshot --tab TAB_ID --output /absolute/full.png --full-page
omni --download --tab TAB_ID --url https://example.com/notes.pdf --output ./notes.pdf
```

Use real returned tab IDs/refs, not indexes, text matches or guessed selectors.
Only explicit `goto --new-tab` creates a tab; no implicit focus/tab selection.
Irrelevant/ambiguous action arguments are rejected before browser interaction.
Press is native element input; select is exact value OR label on native HTML
select only (custom dropdowns return `UNSUPPORTED_ACTION`). No force/JS click,
JS value assignment, action replay, hidden observe or post-action screenshot.

CLI stdout is one JSON object, optionally `--pretty`. Errors retain
`{"ok":false,"error":{"code":"STALE_REF","message":"..."}}`.
Exit codes: 0 success; 2 invalid/unsupported/limit; 3 connection/missing tab;
4 stale/unavailable target; 5 timeout; 6 file/download/auth; 1 internal.
`--timeout` is milliseconds, default 10000 (download 120000); it is a phase
bound, not a hard wall-clock interruption of CDP capture/local parsing.
Filled/selected values and native call logs are omitted from errors/results;
shell history/arguments can still expose CLI values.

## Complete paged observation

Observe joins batch DOM/AX/DOMSnapshot captures using vendored browser-use
snapshot/visibility/clickable/paint-order logic, then clean HTML -> markdown.
It returns `content`, absolute `links`, named `controls`, `frames`, `coverage`,
`truncated`, `has_more`, and `next_cursor`. Follow the cursor to continue all
four streams (including frame metadata) until `has_more=false`/`next_cursor=null`.

Capture is best-effort, not an atomic snapshot: unrelated DOM mutations during
capture do not reject the whole observation. Document identity, URL/baseURI and
frame topology/loader checks remain; unresolved candidates and extraction limits
are reported. Signed cursors still reject meaningful changes to cleaned results
or document/frame identity, not arbitrary DOM mutation counts.

Main, nested same-origin, cross-origin and out-of-process iframe documents are
read once in stable frame-tree order. Content has `[Frame FRAME_ID]` boundaries;
every link/control carries `frame_id`. Frame metadata includes parent identity,
sanitized URL/name, read status and concise reasons for unread frames. Duplicate
sibling URLs/labels are not identities. Hidden/inert/fully clipped owners are
filtered; partial clipping and transforms are not projected into child content.
Ordinary below-viewport content is retained. Closed shadow roots remain unsupported.

Each full Omni JSON page + newline is bounded to 15360 UTF-8 bytes by default
(`--max-bytes 2048..65536`). Streams are interleaved fairly; positions in
`coverage.positions` are [start,end) offsets for content characters/items.
Only the returned page binds refs, at most 96 combined links/controls; candidate
discovery is not globally capped at 96. An item that cannot fit by itself raises
`OUTPUT_BUDGET`; names, URLs and refs are not clipped to fit.

Cursors are signed with a document-local key and a keyed fingerprint of cleaned
extraction, exact frame/node identities, frame topology and document generations.
They expose offsets, not raw content,
credentials or editable values. Each call captures afresh; no disk registry or
observation daemon. Cursors work between CLI processes and MCP. Malformed,
wrong-tab/document, navigation or changed extracted state returns `STALE_CURSOR`
and requires a fresh observe without cursor. Title-only/irrelevant chrome text
outside the extracted main content is not itself a reason to invalidate.

`truncated` and `*_omitted` describe the CURRENT response, so they may remain
true/nonzero on the final page because earlier pages held other items.
`has_more=false` only ends recoverable pagination, not all site coverage.
Unrecoverable aggregate limits remain explicit: 50000 processed DOM nodes,
3000 paint-filter nodes, 1000000 HTML characters before markdown, 32 read frames,
8 descendant levels, 256 discovery records/attachment attempts, and acquisition
limits of 100000 snapshot nodes/8000000 string characters across target batches.
CDP batch replies arrive before their size can be checked; a single batch and
local rendering are not hard wall-clock interruptible. Overlong names are omitted.
Check `processing_limited`, `paint_filter_limited`,
`extraction_content_truncated`, `frames_unread`, `frames_unrouted`,
`frame_discovery_limited`, `frame_geometry_limited`, frame statuses/limitations
and omission/unsupported counts.
Frame metadata is paginated too; omitted metadata does not imply a successful read.

Refs resolve from exact backend node objects into a document-local Map; no Nth
element rebinding. They work across CLI/MCP, with at most 512 retained refs.
Navigation/BFCache, detachment/reattachment, identity changes (e.g. href), another
tab or eviction rejects a ref. On stale refs, observe/select again. Refs include
an opaque exact frame/document identity and resolve only to the bound node in
that document, including iframe/open Shadow DOM nodes. The 512-ref retention
limit is per document. Transient writes/removals of watched identity attributes
do not invalidate a ref when the exact node's stored identity is restored; a
different final identity still rejects it. No raw DOM/AX, editable values, hidden fields,
cookies or browser credentials are serialized. Visible page text/URLs are not
a general secret-redaction service.

## Screenshot, scroll and downloads

Screenshot is viewport PNG by default. Full-page uses bounded CDP capture beyond
the viewport, without tab activation, viewport resize or hidden scrolling.
Output is CSS-resolution PNG, limited to 6000000 pixels, 8192px per side and
4 MiB encoded-image bytes. Oversized capture returns `SCREENSHOT_LIMIT`.
CLI requires output; MCP returns actual standard ImageContent plus JSON metadata,
with optional explicit output file. Existing files/missing parents require
`--overwrite`/`--mkdir`; publication is atomic and failures leave no partial PNG.
Screenshots can reveal visible secrets; use only authorized pages.

For visual-only targets, capture a viewport screenshot and choose the point:

```sh
omni --screenshot --tab TAB_ID --output /absolute/viewport.png
omni --act --tab TAB_ID --action click_at --x 320 --y 240
```

`click_at` sends one native mouse click at finite fractional-capable coordinates
in **top-level viewport CSS pixels**, including hits inside iframes. It requires
both `x`/`y`, rejects `ref`/`value`/`label`/`key`, and checks current viewport bounds.
Ref actions require a ref and reject coordinates. Success confirms input dispatch,
not application intent. No screenshot, scroll, correction or retry is hidden inside
the operation; canvas typing, dragging, resizing and rotating are not supported.

PNG metadata includes actual image dimensions, viewport CSS size, page-CSS capture
origin/extent, page scroll, DPR, browser zoom and visual viewport scale. When
`image_to_input.supported=true`, map original image pixel coordinates using
`input_x = viewport_origin.x + image_x * css_per_image_pixel.x` (likewise y).
This uses actual PNG dimensions rather than assuming device pixels equal CSS
pixels. Account separately for any resizing by your image viewer/client.
Full-page positions are **not** directly viewport click coordinates: take a
viewport screenshot for clicking. Pinch zoom/nonzero visual viewport offsets
are explicitly unsupported for coordinate input/mapping. Normal desktop browser
zoom is accounted for through current CSS metrics, DPR and actual PNG dimensions.
A screenshot is not a lock: capture again after layout, navigation, viewport or
scroll changes. No automatic visual targeting or arbitrary JavaScript API is exposed.

Scroll sends one native wheel input to a hit-tested main-document location with
no inner scroller in its ancestry. Direction is up/down; amount is 1..10000 CSS
pixels, default 75% viewport height capped at 800 (minimum 100).
It returns before/after positions, movement, document/viewport sizes and
`at_current_bottom`. This is not a claim that lazy loading is finished or an
infinite list is complete. No inner-pane/iframe scrolling, focus switching,
implicit observe or repeated auto-scroll.

HTTP downloads preserve the existing scoped in-memory cookie jar, redirects,
streaming/byte cap (512 MiB default), HTML/PDF checks and atomic no-clobber output.
No browser Save dialog or download-policy changes. POST/blob, partitioned-cookie
auth, browser-only tokens, client certificates and challenges have no fallback.

## MCP, skill and source layout

Exactly seven tools: `list_tabs`, `goto`, `observe`, `act`, `download`,
`screenshot`, `scroll`. MCP uses snake_case arguments (`tab_id`, `full_page`,
`max_bytes`, `cursor`, etc.). For example:
`act(tab_id, ref, action="press", key="Enter")`;
`act(tab_id, action="click_at", x=320, y=240)`;
`observe(tab_id, cursor=next_cursor)`; `screenshot(tab_id)`.
Results/errors share CLI objects in text + structuredContent; screenshot adds a
real PNG ImageContent. Observe's byte budget excludes MCP protocol/duplicate
representation overhead.

Add an MCP registration to `~/.codex/config.toml`, replacing the example paths
with your checkout's absolute paths:

```toml
[mcp_servers.omni]
command = "/absolute/path/OmniBrowser/.venv/bin/python"
args = ["/absolute/path/OmniBrowser/omni_mcp.py"]
tool_timeout_sec = 180
[mcp_servers.omni.env]
OMNI_CDP_URL = "http://127.0.0.1:17082"
```

One dedicated thread owns sync Playwright, attaches lazily and serializes MCP
calls. CLI is independent with per-call connections. Broken transport is dropped
for a later call; no replay. Shutdown disconnects, not Chrome/user tabs.
Other clients need agent coordination. Already-running clients may need MCP
reconnect/new session for changed schemas; no other agent/session was restarted.
Install the bundled skill by linking `skills/omnibrowser` into `~/.codex/skills/omnibrowser`.
See [skills/omnibrowser/SKILL.md](skills/omnibrowser/SKILL.md). The product name is
OmniBrowser; CLI `omni` and MCP registration `omni` remain unchanged.

Each top-level operation is implemented once in `src/omni/commands/*.py`.
Entries stay tiny; CLI/MCP share validation and dispatch. Canonical layout:

```text
omni.py, omni_mcp.py            thin development entries
src/omni/
  cli.py, mcp_server.py         validation/dispatch, persistent STDIO owner
  errors.py, models.py
  browser/                     connection.py, tabs.py, frames.py, targets.py, refs.js
  reading/                     capture.py, render.py, pagination.py
  commands/                    list_tabs.py, goto.py, observe.py, act.py,
                               download.py, screenshot.py, scroll.py
  _vendor/browser_use_dom/      small existing MIT DOM subset
```

There is no backward-compatibility layer. Former package-root `cdp`, `tabs`,
`reader`, `pagination`, `refs.js`, `observe`, `actions`, `download` and `scanner.js`
are removed. Python callers must import the canonical modules above; tab command
convenience exports are gone. Old refs/cursors require a fresh observation.
Non-editable wheels include `omni/browser/refs.js`. Vendor MIT/provenance is in
[src/omni/_vendor/browser_use_dom/UPSTREAM.md](src/omni/_vendor/browser_use_dom/UPSTREAM.md).

## Verification

This revision was exercised directly through actual CLI/MCP on a harmless temporary local demo
in a newly-created tab of existing Chrome. No pytest, automated suite, mocks,
benchmark or new fixture project was added/run; existing test callers received
canonical import, registry/resource and changed observation-schema maintenance only.
No Moodle/account/agent-fetch tab was used. Existing fixture-based checks are
included under `tests/`; local handoff notes and screenshots are not published.

Direct checks on 2026-10-21, Chrome 155, existing authorized session:

- Same-origin siblings, nested documents, cross-origin OOPIF and a nested OOPIF
  read/action checks passed. Separate CDP `type: iframe` targets confirmed actual
  OOPIFs; duplicate URLs/labels changed only their selected sibling.
- CLI → MCP and MCP → CLI refs worked; native click/fill/press and native select
  by label/value passed. Navigation, detachment, node reinsertion and restored
  identity mutations rejected old refs. Changed child state/navigation rejected cursors.
- Alternating real CLI/MCP continuation recovered 230 links, 233 controls
  (including three extension groups), three frame records and both final content
  rows in 35 pages at a 4096-byte budget; largest actual response was 3944 bytes.
  Sibling candidates appeared on the first page; the global cap returned 96 refs.
- Hidden/inert/fully clipped owners were skipped; transformed owners were marked
  geometry-limited. A 37-frame page reported 32 reads and five explicit limits.
- Viewport screenshots guided native main/OOPIF canvas clicks, including after
  main-document scrolling. Missing/conflicting/non-finite/bool/out-of-bounds
  coordinates were rejected; MCP rejection checks left the click count unchanged.
  Full-page mapping was marked unsupported for direct viewport input.
- Seven-tool STDIO listing, actual MCP PNG ImageContent, help/version and an
  outside-checkout non-editable wheel/resource check passed; legacy paths were
  absent from the wheel. No other MCP clients were restarted.

Display verification used DPR 2 at 100% desktop browser zoom, including fractional
scroll offsets. Other DPR/browser zoom modes were not directly verified; pinch
zoom/offset visual viewports are explicitly unsupported, not silently corrected.
