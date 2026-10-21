"""Bounded PNG capture without tab activation, resizing or implicit scrolling."""
import base64
from dataclasses import dataclass
import math
import os
from pathlib import Path
import struct
import tempfile

from ..errors import OmniError

MAX_PIXELS = 6000000
MAX_SIDE = 8192
MAX_BYTES = 4 * 1024 * 1024


@dataclass
class ScreenshotResult:
    metadata: dict
    png: bytes


def destination(output, mkdir=False, overwrite=False):
    if output is None:
        return None
    path = Path(output).absolute()
    if path.is_symlink() or path.exists() and (not overwrite or not path.is_file()):
        raise OmniError('OUTPUT_EXISTS', 'Screenshot output exists or is a symlink; use --overwrite for a regular file.', 2)
    if not path.parent.is_dir() and not mkdir:
        raise OmniError('INVALID_ARGUMENT', 'Screenshot parent must exist; use --mkdir explicitly.', 2)
    return path


def publish(path, data, mkdir, overwrite):
    temporary = None
    try:
        if mkdir:
            path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.omni-png-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
            temporary.unlink()
    except OSError:
        raise OmniError('SCREENSHOT_WRITE', 'Cannot publish screenshot; existing output was not silently overwritten.', 6) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def screenshot(page, tab_id, output=None, full_page=False, mkdir=False, overwrite=False, timeout=10000):
    from playwright.sync_api import Error
    path = destination(output, mkdir, overwrite)
    session = page.context.new_cdp_session(page)
    try:
        layout = session.send('Page.getLayoutMetrics')
        view = layout['cssVisualViewport']
        bounds = layout['cssContentSize'] if full_page else {
            'x': view['pageX'], 'y': view['pageY'], 'width': view['clientWidth'], 'height': view['clientHeight'],
        }
        width, height = math.ceil(bounds['width']), math.ceil(bounds['height'])
        if width <= 0 or height <= 0 or max(width, height) > MAX_SIDE or width * height > MAX_PIXELS:
            raise OmniError('SCREENSHOT_LIMIT', 'Capture exceeds 8192px per side or 6000000 pixels; use viewport capture.', 2)
        metrics = page.evaluate('() => ({dpr: devicePixelRatio || 1, scroll: {x: scrollX, y: scrollY}})')
        dpr = metrics['dpr']
        response = session.send('Page.captureScreenshot', {
            'format': 'png', 'fromSurface': True, 'captureBeyondViewport': full_page,
            'clip': {**bounds, 'scale': 1 / dpr},
        })
        # Limit encoded size before decoding as well as verifying actual PNG dimensions.
        if len(response['data']) > (MAX_BYTES + 2) // 3 * 4:
            raise OmniError('SCREENSHOT_LIMIT', 'PNG exceeds the 4 MiB image payload limit.', 2)
        png = base64.b64decode(response['data'], validate=True)
        if png[:8] != b'\x89PNG\r\n\x1a\n' or len(png) < 24:
            raise OmniError('SCREENSHOT', 'Browser did not return a valid PNG header.', 4)
        width, height = struct.unpack('>II', png[16:24])
        if len(png) > MAX_BYTES or width * height > MAX_PIXELS or max(width, height) > MAX_SIDE:
            raise OmniError('SCREENSHOT_LIMIT', 'Actual PNG exceeds the image size limits.', 2)
        after = session.send('Page.getLayoutMetrics')
        if any(after[k] != layout[k] for k in ('cssVisualViewport', 'cssLayoutViewport', 'cssContentSize')):
            raise OmniError('SCREENSHOT', 'Viewport or page extent changed during capture; capture again.', 4)
        normal = view.get('scale', 1) == 1 and not view.get('offsetX', 0) and not view.get('offsetY', 0)
        mapping = {
            'supported': bool(normal and not full_page),
            'input_space': 'top-level viewport', 'input_unit': 'CSS pixels',
            'css_per_image_pixel': {'x': bounds['width'] / width, 'y': bounds['height'] / height},
            'viewport_origin': {'x': bounds['x'] - view['pageX'], 'y': bounds['y'] - view['pageY']},
            'reason': ('Full-page image positions are not viewport input coordinates; use a viewport screenshot.' if full_page else
                       None if normal else 'Non-default visual viewport scaling/offset is unsupported for click_at.'),
        }
        if path is not None:
            publish(path, png, mkdir, overwrite)
        return ScreenshotResult({'ok': True, 'tab_id': tab_id, 'url': page.url,
                                 'path': str(path) if path else None, 'format': 'png',
                                 'width': width, 'height': height, 'bytes': len(png), 'full_page': full_page,
                                 'viewport': {'width': view['clientWidth'], 'height': view['clientHeight']},
                                 'capture': {'space': 'page CSS pixels', **bounds},
                                 'page_scroll': metrics['scroll'],
                                 'visual_viewport_offset': {'x': view.get('offsetX', 0), 'y': view.get('offsetY', 0)},
                                 'device_pixel_ratio': dpr, 'browser_zoom': view.get('zoom'),
                                 'visual_viewport_scale': view.get('scale', 1), 'image_to_input': mapping}, png)
    except Error:
        raise OmniError('SCREENSHOT', 'Capture failed; inspect the selected tab.', 4) from None
    finally:
        try:
            session.detach()
        except Error:
            pass
