"""Capture once per target, render once per document, page and bind exact refs."""
from ..browser.frames import FrameScope
from ..browser.targets import bind
from ..errors import OmniError
from ..reading.capture import capture
from ..reading.pagination import paginate, stale_cursor


def observe(page, tab_id, max_bytes, pretty=False, timeout=10000, cursor=None):
    from playwright.sync_api import Error
    try:
        with FrameScope(page, timeout) as scope:
            source, candidates, metadata = capture(scope, tab_id)
            result, selected = paginate(source, candidates, metadata, tab_id, max_bytes, pretty, cursor)
            bound, refs = {'links': [], 'controls': []}, {}
            for frame in scope.frames:
                chosen = [c for c in selected if c[3]['frame_id'] == frame.id]
                if not chosen:
                    continue
                for (backend, _, kind, item), ref in zip(chosen, bind(scope, frame, chosen, tab_id)):
                    if ref is None:
                        result['coverage'][kind + '_omitted'] += 1
                        result['coverage']['ref_bindings_failed'] += 1
                        result['truncated'] = True
                    refs[(frame.id, backend)] = ref
            for backend, _, kind, item in selected:
                ref = refs.get((item['frame_id'], backend))
                if ref is not None:
                    bound[kind].append({'ref': ref, **item})
            result.update(bound)
            scope.check()
            return result
    except OmniError as exc:
        if cursor and exc.code == 'STALE_REF':
            raise stale_cursor() from None
        raise
    except Error:
        raise OmniError('UNAVAILABLE_TARGET', 'Frame capture unavailable; observe again.', 4) from None
