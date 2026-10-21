# OmniBrowser ref-stability correction

Status: approved narrow implementation plan; no code change is included in this document.

## Objective

Fix the general `STALE_REF` false positives observed on reactive forms and search
inputs. A ref must remain usable when the exact DOM node, document, frame and
stored identity are still valid, even if the page temporarily writes and removes
an observed attribute during one UI update.

This is a ref-lifecycle correction, not a Cerebral Valley workaround. Do not add
domain names, React checks, selector fallbacks, positional lookup, action replay,
or automatic retargeting.

## Evidence to preserve

The failure was reproduced live on three unrelated surfaces of
`cerebralvalley.ai`:

- `/profile/create`: filling Handle makes the old First Name and Last Name refs
  stale; the exact backend node IDs and stable HTML IDs remain unchanged.
- `/e/meta-ai-developer-hackathon/apply`: filling LinkedIn makes the old GitHub
  and location refs stale; textareas retain their refs.
- `/search`: filling the search input invalidates its old ref.

CDP events show the page temporarily sets `name=""` and removes `name` on the
same input nodes. The final identity tuple is unchanged and no target removal
was observed. The current observer nevertheless executes:

```js
if (detached || changed) state.drop(ref)
```

`changed` is true for any watched attribute mutation; it does not compare the
final value with the stored identity. This proves an over-eager invalidation
policy, not a locator or backend-node mapping failure.

`STALE_CURSOR` is a separate pagination/observation-consistency issue. Do not
loosen cursor validation or combine its fix with this ref patch.

## Contract after the patch

Keep these guarantees:

- A ref resolves only to the exact tab, frame, current document and DOM node
  originally bound.
- `state.identity(node)` must still equal the identity captured at binding.
- `isConnected`, `ownerDocument`, `docId`, `frameId`, `tabId`, `baseURI`, URL and
  navigation checks remain enforced.
- A real node replacement, document/frame navigation or identity change remains
  `STALE_REF`.
- No similar node, selector, text match or sibling may be substituted.
- Native action visibility/enabled checks and sanitized errors remain unchanged.
- CLI and MCP continue sharing the same command implementation and ref format.

The contract is exact-node continuity plus the tracked identity tuple. It cannot
promise that an application has not reused the same DOM node for a different
business object, nor eliminate asynchronous check-to-action races.

## Minimal implementation

### `src/omni/browser/refs.js`

1. Remove the unconditional `changed` attribute branch from the observer. A
   mutation record alone must not drop a ref.
2. Keep `state.prune()` as the authoritative current-state check. It drops refs
   whose node is disconnected, belongs to another document, or whose current
   identity differs from the stored identity.
3. Keep child-list/removal invalidation at the level required by the existing
   exact-node contract. Do not reintroduce unconditional invalidation for
   `attributes` or `characterData`. If removal tracking is retained, it must not
   turn a transient attribute update into `STALE_REF`.
4. Keep the 512-entry bound, pagehide cleanup, document-local registry and
   open-shadow-root handling.
5. In `state.bind`, before reusing a WeakMap ref, ensure its map entry still
   exists, its stored backend ID is the candidate backend ID, and its current
   identity still matches. Otherwise drop the stale entry and allocate a fresh
   ref. Do not recover an old ref by guessing.

Do not change the identity tuple casually. Any change to identity attributes must
be justified against the existing action contract; `href`, `role`, `type`, `id`,
`name`, `formaction`, `contenteditable` and form action remain meaningful.

### `src/omni/browser/targets.py`

Keep `RESOLVE`, document checks and exact frame traversal intact. Only make a
small reuse/binding adjustment if required by the `refs.js` guard. Do not add
URL/text matching, revision counters, global page generations or action replay.

### Other modules

- Do not change `commands/act.py` retry semantics.
- Do not change `reading/pagination.py` or cursor signing for this task.
- Do not change the public seven-tool API, CLI flags, MCP schemas or ref format.
- Update comments/docstrings only where they describe the corrected policy.

## Direct verification (no test-suite expansion)

Use the existing authorized Chrome session and harmless form state. Do not submit
the hackathon application, accept terms, change account data, or touch another
agent's tab.

1. On `/profile/create`, observe Handle, First Name and Last Name once. Fill all
   three using the original refs without observing between actions. The transient
   `name` writes must not make later refs stale.
2. On the Apply form, observe LinkedIn, GitHub and location once. Fill them with
   the original refs, then verify the values by a fresh read. Do not click Submit.
3. Repeat the same check on the search input and one textarea as a control.
4. Confirm genuine stale behavior still works using an existing harmless local
   verification page or navigation: a navigated document, disconnected target,
   replacement node, or persistent identity change must reject the old ref.
5. Confirm CLI and a fresh STDIO MCP process can use fresh refs, and that no
   action is replayed after an error or timeout.

Report exact observed outcomes, including any limitation around remove/reinsert
history. Do not create a large test suite, fixtures directory, benchmark, or
temporary compatibility wrapper.

## Scope and handoff

The expected patch is small and confined primarily to `refs.js`, with a narrowly
justified `targets.py` guard if needed. Preserve all unrelated dirty worktree
changes. Do not commit or modify external projects. At completion, report changed
files, direct browser results, remaining limitations, and whether MCP clients
need only reconnecting to load the new Python code.
