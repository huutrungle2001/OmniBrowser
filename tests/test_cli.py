import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import ROOT


@pytest.mark.parametrize("args", [[], ["--observe"], ["--list-tabs", "--observe"],
    ["--goto", "file:///tmp/file", "--new-tab"], ["--act", "--tab", "x", "--ref", "x", "--action", "press"],
    ["--observe", "--tab", "x", "--max-bytes", "2047"], ["--list-tabs", "--timeout", "0"],
    ["--list-tabs", "--cdp-url", "http://example.com:9222"], ["--list-tabs", "--value", "SECRET"],
    ["--observe", "--tab", "x", "--new-tab"], ["--list-tabs", "--max-bytes", "4096"],
    ["--act", "--tab", "x", "--ref", "x", "--action", "click", "--value", "SECRET"],
    ["--download", "--tab", "x", "--url", "https://user:SECRET@example.com/a", "--output", "a.pdf"],
    ["--goto", "", "--tab", "x"]])
def test_validation_without_chrome(args):
    env = {**os.environ, "OMNI_CDP_URL": "http://127.0.0.1:1"}
    result = subprocess.run([sys.executable, str(ROOT / "omni.py"), *args], env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_ARGUMENT"
    assert "SECRET" not in result.stdout + result.stderr
    assert result.stderr == ""


def test_help_version_entry_points(tmp_path):
    for command in ([sys.executable, str(ROOT / "omni.py")], [str(Path(sys.executable).parent / "omni")]):
        for flag in ("--help", "--version"):
            result = subprocess.run(command + [flag], capture_output=True, text=True,
                                    cwd=tmp_path, env={**os.environ, "OMNI_CDP_URL": "invalid"})
            assert result.returncode == 0
            if flag == "--help":
                for action in ("--list-tabs", "--goto", "--observe", "--act", "--download"):
                    assert action in result.stdout
                assert "--eval" not in result.stdout and "--press" not in result.stdout


def test_tabs_navigation_selection(cli, chrome, page, server, tmp_path):
    original, tab = page
    first = cli("--list-tabs").data["tabs"]
    assert len(first) >= 2
    assert [t["tab_id"] for t in first] == sorted(t["tab_id"] for t in first)
    assert any(t["tab_id"] == tab and t["url"].endswith("interactive.html") for t in first)
    result = cli("--goto", server.url + "/content.html", "--tab", tab, installed=True, cwd=tmp_path).data
    assert result == {"ok": True, "tab_id": tab, "url": server.url + "/content.html", "title": "Content fixture"}
    original.wait_for_url(server.url + "/content.html")
    assert original.url.endswith("content.html") and chrome["process"].poll() is None
    cli("--observe", "--tab", "MISSING", expected=3)
    created = cli("--goto", server.url + "/endless", "--new-tab").data
    assert created["tab_id"] != tab
    original.evaluate("1")  # Dispatch events on this independent test-driver connection.
    other = next(p for p in original.context.pages if p.url.endswith("endless"))
    other.close()
    assert cli("--observe", "--tab", created["tab_id"], expected=3).data["error"]["code"] == "TAB_NOT_FOUND"
    assert original.url.endswith("content.html")


def test_connection_error():
    result = subprocess.run([sys.executable, str(ROOT / "omni.py"), "--list-tabs", "--cdp-url", "http://127.0.0.1:1"],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 3
    assert json.loads(result.stdout)["error"]["code"] == "CONNECTION"


@pytest.mark.parametrize("args,endpoint", [
    (["--cdp-url", "http://127.0.0.1:17082"], "http://127.0.0.1:54321"),
    (["--cdp-url=http://127.0.0.1:17082"], "http://127.0.0.1:54321"),
    ([], "http://127.0.0.1:17082")])
def test_test_harness_refuses_live_endpoint(args, endpoint):
    from conftest import require_isolated_endpoint
    with pytest.raises(AssertionError):
        require_isolated_endpoint(args, endpoint)


def test_attachment_does_not_change_viewport_or_other_tabs(cli, page, chrome):
    browser_page, tab = page
    browser_page.set_viewport_size({"width": 987, "height": 654})
    size = browser_page.evaluate("[innerWidth, innerHeight]")
    targets = {t["tab_id"] for t in cli("--list-tabs").data["tabs"]}
    cli("--observe", "--tab", tab)
    assert browser_page.evaluate("[innerWidth, innerHeight]") == size
    assert {t["tab_id"] for t in cli("--list-tabs").data["tabs"]} == targets
    assert chrome["process"].poll() is None and not browser_page.is_closed()


def test_wheel_contents(tmp_path, chrome, page):
    import zipfile
    result = subprocess.run([sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(tmp_path)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert "omni/scanner.js" in archive.namelist()
        entries = archive.read("omni_cli-1.0.0.dist-info/entry_points.txt").decode()
        assert "omni = omni.cli:main" in entries
    # Execute a non-editable wheel outside the checkout, including packaged scanner.
    environment = tmp_path / "wheel-env"
    subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    installed = subprocess.run([str(environment / "bin/python"), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
                               capture_output=True, text=True)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    result = subprocess.run([str(environment / "bin/omni"), "--observe", "--tab", page[1]], cwd=tmp_path,
                            env={**os.environ, "OMNI_CDP_URL": chrome["url"]}, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["controls"]
