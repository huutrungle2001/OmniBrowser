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
It returns `content`, absolute `links`, named `controls`, `coverage`,
`truncated`, `has_more`, and `next_cursor`. Follow the cursor to continue all
three streams until `has_more=false`/`next_cursor=null`.

Each full Omni JSON page + newline is bounded to 15360 UTF-8 bytes by default
(`--max-bytes 2048..65536`). Streams are interleaved fairly; positions in
`coverage.positions` are [start,end) offsets for content characters/items.
Only the returned page binds refs, at most 96 combined links/controls; candidate
discovery is not globally capped at 96. An item that cannot fit by itself raises
`OUTPUT_BUDGET`; names, URLs and refs are not clipped to fit.

Cursors are signed with a document-local key and a keyed fingerprint of cleaned
extraction and exact node identities. They expose offsets, not raw content,
credentials or editable values. Each call captures afresh; no disk registry or
observation daemon. Cursors work between CLI processes and MCP. Malformed,
wrong-tab/document, navigation or changed extracted state returns `STALE_CURSOR`
and requires a fresh observe without cursor. Title-only/irrelevant chrome text
outside the extracted main content is not itself a reason to invalidate.

`truncated` and `*_omitted` describe the CURRENT response, so they may remain
true/nonzero on the final page because earlier pages held other items.
`has_more=false` only ends recoverable pagination, not all site coverage.
Unrecoverable limits remain explicit: 50000 processed DOM nodes,
3000 paint-filter nodes, 1000000 HTML characters before markdown, overlong
name fallbacks omitted, and unsupported iframe/closed-shadow content.
Check `processing_limited`, `paint_filter_limited`,
`extraction_content_truncated`, omission counts and unsupported counts.

Refs resolve from exact backend node objects into a document-local Map; no Nth
element rebinding. They work across CLI/MCP, with at most 512 retained refs.
Navigation/BFCache, detachment/reattachment, identity changes (e.g. href), another
tab or eviction rejects a ref. On stale refs, observe/select again. Main
document/open Shadow DOM only; no raw DOM/AX, editable values, hidden fields,
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
Install the bundled skill by linking `skills/omni` into `~/.codex/skills/omni`.
See [skills/omni/SKILL.md](skills/omni/SKILL.md). The product name is
OmniBrowser; CLI `omni` and MCP registration `omni` remain unchanged.

Each top-level operation is implemented once in `src/omni/commands/*.py`.
Entries stay tiny; CLI/MCP share validation and dispatch. `reader.py`,
`pagination.py`, `refs.js`, `tabs.py` and `cdp.py` are bounded shared helpers.
Tiny old-module compatibility imports remain. Vendor MIT/provenance is in
[src/omni/_vendor/browser_use_dom/UPSTREAM.md](src/omni/_vendor/browser_use_dom/UPSTREAM.md).

## Verification

This revision was exercised directly through actual CLI/MCP on a harmless temporary local demo
in a newly-created tab of existing Chrome. No pytest, automated suite, mocks,
benchmark or new fixture project was added/run; existing tests remain unchanged.
No Moodle/account/agent-fetch tab was used. Existing fixture-based checks are
included under `tests/`; local handoff notes and screenshots are not published.
