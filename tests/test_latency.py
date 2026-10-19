"""Opt-in reporting, also verifies commands under normal full-suite execution."""
import json
import platform
import statistics
from importlib.metadata import version
import os


def test_cli_wall_clock(cli, page, server, tmp_path, chrome):
    browser_page, tab = page
    browser_page.context.add_cookies([{"name": "session", "value": "fixture-secret", "url": server.url + "/"}])
    observed = cli("--observe", "--tab", tab)
    ref = next(c["ref"] for c in observed.data["controls"] if c["name"] == "Search label")
    commands = {
        "list-tabs": ("--list-tabs",),
        "goto": ("--goto", server.url + "/interactive.html", "--tab", tab),
        "observe": ("--observe", "--tab", tab),
        "act-fill": ("--act", "--tab", tab, "--ref", ref, "--action", "fill", "--value", "fixture"),
        "download": ("--download", "--tab", tab, "--url", server.url + "/protected/file.pdf", "--output", str(tmp_path / "benchmark.pdf"), "--overwrite")}
    measures = {}
    # Goto invalidates refs, so run it last.
    for name in ("list-tabs", "observe", "act-fill", "download", "goto"):
        values = [round(cli(*commands[name]).elapsed * 1000, 2) for _ in range(3)]
        measures[name] = {"samples_ms": values, "median_ms": round(statistics.median(values), 2)}
    if os.environ.get("OMNI_REPORT_LATENCY") == "1":
        print("\nLATENCY " + json.dumps({"platform": platform.platform(), "python": platform.python_version(),
              "playwright": version("playwright"), "httpx": version("httpx"),
              "chrome": chrome["browser"].version,
              "initial_observe_ms": round(observed.elapsed * 1000, 2), "commands": measures}))
