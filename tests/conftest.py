"""
Pytest configuration and safety fixtures for OmniBrowser test suite.
Enforces the critical safety invariant: Zero Live Profile Pollution.
"""

import http.server
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time

import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture
def ephemeral_user_data_dir():
    """Provides an isolated ephemeral user data directory for Chrome."""
    temp_dir = tempfile.mkdtemp(prefix="omnibrowser_test_profile_")
    yield temp_dir
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(autouse=True)
def guard_live_profile(monkeypatch):
    """Autouse fixture ensuring no test inadvertently targets the user live profile port."""
    if os.environ.get("CDP_PORT") == "17082" or ":17082" in os.environ.get("CDP_URL", ""):
        pytest.fail(
            "SAFETY INVARIANT VIOLATION: CDP_PORT is set to 17082. "
            "Automated tests must never connect to live user Chrome profile."
        )
    original_connect = socket.socket.connect

    def guarded_connect(sock, address):
        if isinstance(address, tuple) and len(address) >= 2 and address[1] == 17082:
            raise AssertionError("SAFETY INVARIANT VIOLATION: tests may not connect to port 17082")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    yield


_CACHED_CHROME_BINARY: str | None = None


def _get_chrome_binary() -> str | None:
    global _CACHED_CHROME_BINARY
    if _CACHED_CHROME_BINARY and Path(_CACHED_CHROME_BINARY).exists():
        return _CACHED_CHROME_BINARY
    candidates = [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    for candidate in candidates:
        if candidate.exists():
            _CACHED_CHROME_BINARY = str(candidate)
            return _CACHED_CHROME_BINARY
    try:
        with sync_playwright() as playwright:
            _CACHED_CHROME_BINARY = str(playwright.chromium.executable_path)
            return _CACHED_CHROME_BINARY
    except Exception:
        pass
    return None


@pytest.fixture
def ephemeral_cdp_url(ephemeral_user_data_dir):
    """Launch a disposable Chromium and expose only its dynamically assigned CDP URL."""
    chrome_binary = _get_chrome_binary()
    if chrome_binary is None:
        pytest.fail("No Chromium binary is available for the isolated CDP integration test.")

    process = subprocess.Popen(
        [
            str(chrome_binary),
            "--headless=new",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            f"--user-data-dir={ephemeral_user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    active_port_file = Path(ephemeral_user_data_dir, "DevToolsActivePort")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not active_port_file.exists():
        if process.poll() is not None:
            pytest.fail("Ephemeral Chromium exited before exposing CDP.")
        time.sleep(0.05)
    if not active_port_file.exists():
        process.terminate()
        pytest.fail("Ephemeral Chromium did not create DevToolsActivePort.")

    port = int(active_port_file.read_text(encoding="utf-8").splitlines()[0])
    assert port != 17082
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def fixture_server():
    fixture_dir = Path(__file__).parent / "fixtures"
    handler = lambda *args, **kwargs: http.server.SimpleHTTPRequestHandler(
        *args, directory=str(fixture_dir), **kwargs
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
