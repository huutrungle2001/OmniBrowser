import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import httpx
import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fixture_server", ROOT / "tests/fixtures/protected_file_server.py")
fixture_server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_server)


def require_isolated_endpoint(args, endpoint):
    assert int(endpoint.rsplit(":", 1)[1]) != 17082
    for index, arg in enumerate(args):
        if arg == "--cdp-url":
            assert index + 1 < len(args) and args[index + 1] == endpoint, "Unsafe test CDP override"
        if arg.startswith("--cdp-url="):
            assert arg.split("=", 1)[1] == endpoint, "Unsafe test CDP override"


@pytest.fixture(scope="session")
def server():
    instance = fixture_server.make_server()
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    instance.url = f"http://127.0.0.1:{instance.server_port}"
    yield instance
    instance.shutdown()
    instance.server_close()
    thread.join(timeout=5)


@pytest.fixture(scope="session")
def chrome(tmp_path_factory):
    # No endpoint/profile overrides: this fixture owns both and refuses the live port.
    executable = (shutil.which("google-chrome") or shutil.which("chromium") or
                  shutil.which("chromium-browser") or
                  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if not Path(executable).is_file():
        pytest.fail("Chrome is required; no live-browser fallback is permitted.")
    profile = tmp_path_factory.mktemp("omni-isolated-chrome")
    assert profile.is_dir() and "omni-isolated-chrome" in profile.name
    process = subprocess.Popen([executable, f"--user-data-dir={profile}",
        "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
        "--headless=new", "--no-first-run", "--no-default-browser-check",
        "--disable-background-networking", "--disable-component-update", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        port_file = profile / "DevToolsActivePort"
        deadline = time.monotonic() + 15
        while not port_file.exists():
            if process.poll() is not None or time.monotonic() > deadline:
                pytest.fail("Isolated Chrome failed to start.")
            time.sleep(.03)
        port = int(port_file.read_text().splitlines()[0])
        assert port != 17082, "Refusing the live browser port"
        url = f"http://127.0.0.1:{port}"
        assert httpx.get(url + "/json/version", trust_env=False).status_code == 200
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(url)
            yield {"url": url, "browser": browser, "process": process, "profile": profile}
            browser.close()
    finally:
        # Only terminate the process created with this fixture's temporary profile.
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def page(chrome, server):
    page = chrome["browser"].contexts[0].new_page()
    page.goto(server.url + "/interactive.html", wait_until="domcontentloaded")
    session = page.context.new_cdp_session(page)
    tab = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
    session.detach()
    yield page, tab
    if not page.is_closed():
        page.close()


@pytest.fixture
def cli(chrome):
    def invoke(*args, expected=0, installed=False, cwd=None):
        require_isolated_endpoint(args, chrome["url"])
        env = os.environ.copy()
        env["OMNI_CDP_URL"] = chrome["url"]
        command = [str(Path(sys.executable).parent / "omni")] if installed else [sys.executable, str(ROOT / "omni.py")]
        started = time.perf_counter()
        result = subprocess.run(command + list(args), capture_output=True, text=True,
                                env=env, cwd=cwd or ROOT, timeout=30)
        result.elapsed = time.perf_counter() - started
        assert result.returncode == expected, (result.returncode, result.stdout, result.stderr)
        assert result.stderr == "", result.stderr
        result.data = json.loads(result.stdout)
        return result
    return invoke
