"""Lightweight native Chrome DevTools Protocol (CDP) WebSocket client.

Implements pure-Python CDP communication using the standard library (socket, struct, json),
avoiding external event loop conflicts with Playwright or asyncio.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
from typing import Any
import urllib.parse
import urllib.request


def is_cdp_alive(cdp_url: str, timeout: float = 0.5) -> bool:
    """Checks whether the Chrome CDP HTTP endpoint is responsive."""
    try:
        req = urllib.request.Request(f"{cdp_url.rstrip('/')}/json/version")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_browser_ws_url(cdp_url: str, timeout: float = 2.0) -> str:
    """Fetches the browser-level WebSocket debugger URL from Chrome's /json/version endpoint."""
    req = urllib.request.Request(f"{cdp_url.rstrip('/')}/json/version")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ws_url = data.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError(f"Chrome at {cdp_url} did not expose webSocketDebuggerUrl")
    return ws_url


class CDPClient:
    """Synchronous, standard-library WebSocket client for CDP commands."""

    def __init__(self, ws_url: str, timeout: float = 10.0):
        self.ws_url = ws_url
        self.timeout = timeout
        parsed = urllib.parse.urlsplit(ws_url)
        if parsed.scheme not in {"ws", "wss"}:
            raise ValueError(f"Invalid WebSocket scheme: {parsed.scheme}")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        host = parsed.hostname or "127.0.0.1"

        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._socket.settimeout(timeout)
        self._recv_buffer = bytearray()
        self._closed = False
        self._next_id = 0

        # Perform WebSocket handshake
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        host_header = host
        if ":" in host_header:
            host_header = f"[{host_header}]"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self._socket.sendall(request)
        headers = self._read_until(b"\r\n\r\n")
        if not headers.startswith(b"HTTP/1.1 101"):
            self._socket.close()
            raise RuntimeError(f"CDP WebSocket handshake failed: {headers[:100]!r}")

    def _read_until(self, marker: bytes) -> bytes:
        while True:
            idx = self._recv_buffer.find(marker)
            if idx >= 0:
                end = idx + len(marker)
                data = bytes(self._recv_buffer[:end])
                del self._recv_buffer[:end]
                return data
            chunk = self._socket.recv(4096)
            if not chunk:
                raise ConnectionError("CDP connection closed during read_until")
            self._recv_buffer.extend(chunk)

    def _recv_exact(self, size: int) -> bytes:
        while len(self._recv_buffer) < size:
            chunk = self._socket.recv(size - len(self._recv_buffer))
            if not chunk:
                raise ConnectionError("CDP connection closed during recv_exact")
            self._recv_buffer.extend(chunk)
        data = bytes(self._recv_buffer[:size])
        del self._recv_buffer[:size]
        return data

    def _send_frame(self, payload: bytes, opcode: int = 1) -> None:
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length < 65536:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
        self._socket.sendall(header + mask + masked)

    def _read_frame(self) -> tuple[int, bytes]:
        first, second = self._recv_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = bytearray(self._recv_exact(length))
        if mask:
            payload = bytearray(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, bytes(payload)

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._next_id += 1
        req_id = self._next_id
        msg = json.dumps({"id": req_id, "method": method, "params": params or {}}).encode("utf-8")
        self._send_frame(msg)

        while True:
            opcode, payload = self._read_frame()
            if opcode == 9:  # Ping
                self._send_frame(payload, opcode=10)  # Pong
                continue
            if opcode == 8:  # Close
                self.close()
                raise ConnectionError("CDP connection closed by remote peer")
            if opcode in (1, 2):  # Text or binary
                response = json.loads(payload.decode("utf-8"))
                if response.get("id") == req_id:
                    if "error" in response:
                        raise RuntimeError(f"CDP error: {response['error']}")
                    return response

    def close(self) -> None:
        if not self._closed:
            try:
                self._send_frame(b"", opcode=8)
            except OSError:
                pass
            finally:
                self._closed = True
        try:
            self._socket.close()
        except OSError:
            pass

    def __enter__(self) -> CDPClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()
