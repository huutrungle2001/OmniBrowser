"""Bounded batched target capture; document-local joins and explicit coverage."""
from ..browser.frames import OWNER_VISIBILITY
from ..browser.targets import initialize
from ..errors import OmniError, stale
from .render import CONTENT_LIMIT, NODE_LIMIT, PAINT_LIMIT, render_observation

SNAPSHOT_NODE_LIMIT = 100000
SNAPSHOT_STRING_LIMIT = 8000000

def owner_visible(scope, frame, parents):
    if frame.parent is None:
        return True
    parent = parents.get(frame.parent)
    if parent is None or parent.status not in {'read', 'pending'} or parent.session is None:
        return False
    owner = scope.send(parent.session, 'DOM.getFrameOwner', {'frameId': frame.id})
    remote = scope.send(parent.session, 'DOM.resolveNode', {
        'backendNodeId': owner['backendNodeId'], 'executionContextId': parent.context,
        'objectGroup': 'omni-operation',
    })['object']
    result = scope.send(parent.session, 'Runtime.callFunctionOn', {
        'objectId': remote['objectId'], 'functionDeclaration': OWNER_VISIBILITY,
        'returnByValue': True,
    })
    geometry = result.get('result', {}).get('value') or {}
    frame.geometry_limited = bool(geometry.get('limited')) or parent.geometry_limited
    return geometry.get('visible') is True


def capture(scope, tab_id):
    from playwright.sync_api import Error
    from .._vendor.browser_use_dom.enhanced_snapshot import REQUIRED_COMPUTED_STYLES

    limits = {'nodes': NODE_LIMIT, 'paint': PAINT_LIMIT, 'content': CONTENT_LIMIT}
    batches, parents, content, candidates = {}, {f.id: f for f in scope.frames}, [], []
    acquired_nodes, acquired_strings = 0, 0
    coverage = {'content_truncated': False, 'links_omitted': 0, 'controls_omitted': 0,
                'unsupported_closed_shadow_roots': 0, 'processing_limited': scope.discovery_limited,
                'paint_filter_limited': False, 'ref_binding_limit': 96, 'ref_bindings_failed': 0}
    # Install document generations before the first batch to detect replacement.
    # Capture is best-effort, not atomic; unrelated DOM changes are acceptable.
    for frame in scope.frames:
        if frame.status == 'pending':
            try:
                initialize(scope, frame, tab_id)
            except Error:
                frame.status, frame.reason = 'unavailable', 'No live document context.'
    for frame in scope.frames:
        scope.remaining()
        if frame.status != 'pending':
            continue
        if (limits['nodes'] <= 0 or limits['content'] <= 0 or
                acquired_nodes > SNAPSHOT_NODE_LIMIT or acquired_strings > SNAPSHOT_STRING_LIMIT):
            frame.status, frame.reason = 'limited', 'Aggregate node/content limit.'
            coverage['processing_limited'] = True
            continue
        try:
            if not owner_visible(scope, frame, parents):
                frame.status, frame.reason = 'skipped', 'Hidden, inert, clipped or unread frame owner.'
                continue
            if frame.session not in batches:
                dom = scope.send(frame.session, 'DOM.getDocument', {'depth': -1, 'pierce': True})['root']
                snapshot = scope.send(frame.session, 'DOMSnapshot.captureSnapshot', {
                    'computedStyles': REQUIRED_COMPUTED_STYLES,
                    'includePaintOrder': True, 'includeDOMRects': True,
                })
                acquired_nodes += sum(len(d['nodes']['backendNodeId']) for d in snapshot['documents'])
                acquired_strings += sum(len(s) for s in snapshot['strings'])
                if acquired_nodes > SNAPSHOT_NODE_LIMIT or acquired_strings > SNAPSHOT_STRING_LIMIT:
                    batches[frame.session] = None
                else:
                    documents, pending, count = {}, [dom], 0
                    while pending and count < SNAPSHOT_NODE_LIMIT:
                        node = pending.pop()
                        count += 1
                        if node['nodeType'] == 9:
                            documents[node['backendNodeId']] = node
                        pending.extend(node.get('children', []))
                        pending.extend(node.get('shadowRoots', []))
                        if node.get('contentDocument'):
                            pending.append(node['contentDocument'])
                    batches[frame.session] = (documents, snapshot) if not pending else None
            batch = batches[frame.session]
            if batch is None:
                frame.status, frame.reason = 'limited', 'Target batch exceeds acquisition limits.'
                coverage['processing_limited'] = True
                continue
            documents, snapshot = batch
            remote = scope.evaluate(frame, 'document', objects=True)
            backend = scope.send(frame.session, 'DOM.describeNode', {'objectId': remote['objectId']})['node']['backendNodeId']
            dom = documents.get(backend)
            selected = [d for d in snapshot['documents'] if d['nodes']['backendNodeId'] and d['nodes']['backendNodeId'][0] == backend]
            if dom is None or len(selected) != 1:
                raise stale()
            ax = scope.send(frame.session, 'Accessibility.getFullAXTree', {'frameId': frame.id})
            result, found = render_observation(dom, ax, {**snapshot, 'documents': selected}, frame.metadata, tab_id, limits)
            frame.status, frame.reason = 'read', ''
            content.append(f'\n\n[Frame {frame.id}]\n\n{result["content"]}')
            candidates.extend((backend_id, expected, kind, {**item, 'frame_id': frame.id})
                              for backend_id, expected, kind, item in found)
            for key, value in result['coverage'].items():
                if key != 'ref_binding_limit':
                    coverage[key] = (coverage[key] or value) if isinstance(value, bool) else coverage[key] + value
        except Error:
            if not scope.page.context.browser.is_connected() or scope.page.is_closed():
                raise OmniError('UNAVAILABLE_TARGET', 'Browser transport or tab unavailable.', 4) from None
            frame.status, frame.reason = 'unavailable', 'Frame detached or context unavailable during capture.'
        except OmniError as exc:
            if exc.code != 'STALE_REF' or frame is scope.main:
                raise
            frame.status, frame.reason = 'unavailable', 'Frame document changed during capture.'
    main = scope.main.metadata
    if main is None:
        raise OmniError('UNAVAILABLE_TARGET', 'Main document unavailable for observation.', 4)
    unrouted = max(0, scope.total - len(scope.frames))
    coverage['processing_limited'] |= any(f.status == 'limited' for f in scope.frames)
    coverage.update(frames_discovered=len(scope.frames), frames_seen=scope.total, frames_unrouted=unrouted,
                    frames_read=sum(f.status == 'read' for f in scope.frames),
                    frames_unread=unrouted + sum(f.status != 'read' for f in scope.frames),
                    frame_discovery_limited=scope.discovery_limited,
                    frame_geometry_limited=sum(f.geometry_limited for f in scope.frames),
                    frame_geometry='Hidden/inert/fully clipped owners filtered; partial clipping/transforms are not projected into child content.')
    metadata = {**main, 'documents': [scope.total, [(f.id, f.parent, f.loader, f.metadata['id'] if f.metadata else None) for f in scope.frames]]}
    return {'tab_id': tab_id, 'url': main['url'], 'title': main['title'],
            'content': ''.join(content).lstrip(), 'links': [], 'controls': [],
            'frames': [f.public() for f in scope.frames], 'coverage': coverage,
            'truncated': bool(coverage['frames_unread'] or coverage['processing_limited'])}, candidates, metadata
