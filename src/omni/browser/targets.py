"""Document-local exact bindings, shared across independent CLI/MCP connections."""
from importlib.resources import files
import json
import re

from ..errors import OmniError, stale

RESOLVE = """({ref, tabId, frameId, docId}) => {
  const s = window.__omni_document__;
  const entry = s?.nodes.get(ref);
  if (!s?.live || s.document !== document || s.tabId !== tabId ||
      s.frameId !== frameId || s.id !== docId || !entry ||
      !entry.node.isConnected || entry.node.ownerDocument !== document ||
      s.identity(entry.node) !== entry.identity) return null;
  return entry.node;
}"""


def initialize(scope, frame, tab_id):
    script = files('omni.browser').joinpath('refs.js').read_text(encoding='utf-8')
    frame.metadata = scope.evaluate(frame, f'({script})({json.dumps(dict(tabId=tab_id, frameId=frame.id))})')
    if not frame.metadata:
        raise stale()
    return frame.metadata


def check_document(scope, frame):
    meta = frame.metadata
    expression = """(meta => { const s = window.__omni_document__;
      return !!s?.live && s.document === document && s.id === meta.id &&
        location.href === meta.url && document.baseURI === meta.baseURI;
      })""" + '(' + json.dumps(meta) + ')'
    if not scope.evaluate(frame, expression):
        raise stale()


def bind(scope, frame, candidates, tab_id):
    from playwright.sync_api import Error
    resolved = []
    for backend, _, _, _ in candidates:
        try:
            remote = scope.send(frame.session, 'DOM.resolveNode', {
                'backendNodeId': backend, 'executionContextId': frame.context,
                'objectGroup': 'omni-operation',
            })['object']
            resolved.append({'objectId': remote['objectId']})
        except Error:
            resolved.append({'value': None})
    window = scope.evaluate(frame, 'window', objects=True)['objectId']
    response = scope.send(frame.session, 'Runtime.callFunctionOn', {
        'objectId': window,
        'functionDeclaration': """function(meta, entries, ...nodes) {
          const s = window.__omni_document__;
          if (!s?.live || s.tabId !== meta.tabId || s.id !== meta.id) return [];
          s.prune();
          return nodes.map((node, i) => s.bind(node, entries[i][1], entries[i][0], meta.id, meta.baseURI));
        }""",
        'arguments': [{'value': {**frame.metadata, 'tabId': tab_id}},
                      {'value': [[c[0], c[1]] for c in candidates]}, *resolved],
        'returnByValue': True,
    })
    refs = response.get('result', {}).get('value')
    if response.get('exceptionDetails') or not isinstance(refs, list) or len(refs) != len(candidates):
        raise stale()
    return refs


def resolve(page, tab_id, ref, deadline):
    """Find the exact live document by its opaque frame/document identity.

    Playwright owns native input/session routing after resolution; no durable
    remote object IDs, URL matching or frame-position selectors are involved.
    """
    from playwright.sync_api import Error
    match = re.fullmatch(r'([A-Fa-f0-9]{32})\.([a-f0-9]{32})\.n[1-9][0-9]*', ref)
    if not match:
        raise stale()
    frame_id, doc_id = match.groups()
    from .frames import DISCOVERY_LIMIT, DEPTH_LIMIT, OWNER_VISIBILITY
    for frame in page.frames[:DISCOVERY_LIMIT]:
        deadline()
        try:
            handle = frame.evaluate_handle(RESOLVE, dict(ref=ref, tabId=tab_id, frameId=frame_id, docId=doc_id))
            if handle.as_element() is not None:
                try:
                    ancestor, depth = frame, 0
                    while ancestor.parent_frame is not None:
                        deadline()
                        depth += 1
                        if depth > DEPTH_LIMIT:
                            raise stale()
                        owner = ancestor.frame_element()
                        try:
                            geometry = owner.evaluate('el => (' + OWNER_VISIBILITY + ').call(el)')
                            if not geometry.get('visible'):
                                raise OmniError('INVALID_ACTION', 'Frame owner is hidden, inert or clipped; inspect the page.', 4)
                        finally:
                            owner.dispose()
                        ancestor = ancestor.parent_frame
                except BaseException:
                    handle.dispose()
                    raise
                return handle
            handle.dispose()
        except Error:
            if page.is_closed() or not page.context.browser.is_connected():
                raise OmniError('UNAVAILABLE_TARGET', 'Browser transport or tab unavailable.', 4) from None
            if not frame.is_detached():
                raise OmniError('UNAVAILABLE_TARGET', 'Frame context unavailable; inspect before retrying.', 4) from None
    raise stale()
