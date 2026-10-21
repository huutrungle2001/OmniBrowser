"""Operation-owned target sessions and exact CDP frame/default-context routing.

Frame-tree/session association adapted from browser-use (MIT; vendor UPSTREAM.md).
No URL/index matching, browser-wide attachment, or persistent session registry.
"""
from dataclasses import dataclass
import time
from urllib.parse import urlsplit, urlunsplit

from ..errors import OmniError, stale

FRAME_LIMIT = 32
DEPTH_LIMIT = 8
DISCOVERY_LIMIT = 256

OWNER_VISIBILITY = """function() {
  let rect = this.getBoundingClientRect();
  let limited = false;
  if (!this.isConnected || !rect.width || !rect.height) return {visible:false};
  for (let el = this; el; el = el.parentElement || el.getRootNode().host) {
    const s = getComputedStyle(el);
    if (el.hidden || el.inert || s.display === 'none' ||
        ['hidden', 'collapse'].includes(s.visibility) || Number(s.opacity) === 0) return {visible:false};
    limited ||= s.transform !== 'none' || s.clipPath !== 'none' || s.maskImage !== 'none';
    if (el !== this && el !== document.scrollingElement &&
        [s.overflowX, s.overflowY].some(x => ['hidden','clip','scroll','auto'].includes(x))) {
      const r = el.getBoundingClientRect();
      const previous = rect;
      rect = {
        left: s.overflowX === 'visible' ? rect.left : Math.max(rect.left, r.left),
        right: s.overflowX === 'visible' ? rect.right : Math.min(rect.right, r.right),
        top: s.overflowY === 'visible' ? rect.top : Math.max(rect.top, r.top),
        bottom: s.overflowY === 'visible' ? rect.bottom : Math.min(rect.bottom, r.bottom)
      };
      if (rect.right <= rect.left || rect.bottom <= rect.top) return {visible:false};
      limited ||= ['left','right','top','bottom'].some(k => rect[k] !== previous[k]);
    }
  }
  return {visible:true, limited};
}"""


def public_url(url):
    try:
        parts = urlsplit(url)
        if parts.scheme in {'http', 'https'} and not parts.username and not parts.password:
            return urlunsplit(parts)
        return 'about:blank' if url == 'about:blank' else '[non-HTTP document]'
    except ValueError:
        return '[unavailable URL]'


@dataclass
class Frame:
    id: str
    parent: str | None
    url: str
    name: str
    loader: str
    session: object = None
    context: int | None = None
    metadata: dict | None = None
    status: str = 'unavailable'
    reason: str = 'No live default execution context.'
    geometry_limited: bool = False

    def public(self):
        return {'frame_id': self.id, 'parent_id': self.parent,
                'url': public_url(self.url), 'name': self.name[:128],
                'status': self.status, **({'reason': self.reason} if self.reason else {}),
                **({'limitations': 'Partially clipped/transformed owner; child geometry is not projected.'} if self.geometry_limited else {})}


class FrameScope:
    def __init__(self, page, timeout):
        self.page = page
        self.deadline = time.monotonic() + timeout / 1000
        self.sessions = []
        self.contexts = {}
        self.frames = []
        self.discovery_limited = False

    def remaining(self):
        ms = int((self.deadline - time.monotonic()) * 1000)
        if ms <= 0:
            raise OmniError('TIMEOUT', 'Operation timed out; inspect before retrying.', 5)
        return ms

    def send(self, session, method, params=None):
        self.remaining()
        result = session.send(method, params or {})
        self.remaining()
        return result

    def evaluate(self, frame, expression, objects=False):
        response = self.send(frame.session, 'Runtime.evaluate', {
            'expression': expression, 'contextId': frame.context,
            'returnByValue': not objects, 'objectGroup': 'omni-operation',
            'timeout': self.remaining(),
        })
        if response.get('exceptionDetails'):
            raise stale()
        return response['result'] if objects else response['result'].get('value')

    def _attach(self, owner):
        session = self.page.context.new_cdp_session(owner)
        self.sessions.append(session)
        contexts = self.contexts[session] = {}
        def created(event):
            context = event['context']
            aux = context.get('auxData', {})
            if aux.get('isDefault') and aux.get('frameId'):
                contexts[aux['frameId']] = context['id']
        session.on('Runtime.executionContextCreated', created)
        session.on('Runtime.executionContextsCleared', lambda _: contexts.clear())
        session.on('Runtime.executionContextDestroyed', lambda e: contexts.update(
            {fid: None for fid, cid in contexts.items() if cid == e['executionContextId']}))
        self.send(session, 'Runtime.enable')
        return session

    def discover(self):
        from playwright.sync_api import Error
        main = self._attach(self.page)
        trees = [(main, self.send(main, 'Page.getFrameTree')['frameTree'])]
        # new_cdp_session(Frame) is supported for OOPIFs only. Same-process
        # frames deliberately use their default context on the parent target.
        owners = self.page.frames
        self.total = len(owners)
        self.discovery_limited = len(owners) > DISCOVERY_LIMIT
        for owner in owners[:DISCOVERY_LIMIT]:
            self.remaining()
            if owner == self.page.main_frame:
                continue
            try:
                session = self._attach(owner)
            except Error:
                continue
            trees.append((session, self.send(session, 'Page.getFrameTree')['frameTree']))
            if len(self.sessions) >= FRAME_LIMIT:
                self.discovery_limited = True
                break
        records, order = {}, []
        for session, tree in trees:
            pending = [(tree, None)]
            while pending and len(records) < DISCOVERY_LIMIT:
                node, parent = pending.pop()
                raw = node['frame']
                fid = raw['id']
                if fid not in records:
                    records[fid] = Frame(fid, raw.get('parentId', parent), raw.get('url', ''),
                                         raw.get('name', ''), raw.get('loaderId', ''))
                    order.append(fid)
                frame = records[fid]
                context = self.contexts[session].get(fid)
                if context is not None:
                    frame.session, frame.context = session, context
                    frame.loader = raw.get('loaderId', '')
                pending.extend((child, fid) for child in reversed(node.get('childFrames', [])))
            self.discovery_limited |= bool(pending)
        root = trees[0][1]['frame']['id']
        pending = [(root, 0)]
        while pending:
            fid, depth = pending.pop()
            frame = records[fid]
            if len(self.frames) >= FRAME_LIMIT or depth > DEPTH_LIMIT:
                frame.status, frame.reason = 'limited', 'Frame count or nesting limit.'
            elif frame.context is not None:
                frame.status, frame.reason = 'pending', ''
            self.frames.append(frame)
            pending.extend((child, depth + 1) for child in reversed(order) if records[child].parent == fid)
        self.main = self.frames[0]
        return self

    def check(self):
        from .targets import check_document
        for frame in self.frames:
            if frame.metadata:
                check_document(self, frame)
        if len(self.page.frames) != self.total:
            raise stale()
        current = []
        for session in self.sessions:
            tree = self.send(session, 'Page.getFrameTree')['frameTree']
            pending = [(tree, None)]
            while pending:
                node, parent = pending.pop()
                raw = node['frame']
                current.append((raw['id'], raw.get('parentId', parent), raw.get('loaderId', '')))
                pending.extend((child, raw['id']) for child in node.get('childFrames', []))
                if len(current) > DISCOVERY_LIMIT * FRAME_LIMIT:
                    raise stale()
        if any((f.id, f.parent, f.loader) not in current for f in self.frames):
            raise stale()
        if not self.discovery_limited and {fid for fid, _, _ in current} != {f.id for f in self.frames}:
            raise stale()

    def close(self):
        from playwright.sync_api import Error
        for session in reversed(self.sessions):
            try:
                session.send('Runtime.releaseObjectGroup', {'objectGroup': 'omni-operation'})
            except Error:
                pass
            try:
                session.detach()
            except Error:
                pass

    def __enter__(self):
        try:
            return self.discover()
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args):
        self.close()
