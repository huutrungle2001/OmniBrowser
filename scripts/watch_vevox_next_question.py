#!/usr/bin/env python3
"""
Simple Vevox Question Watcher
Monitors the Vevox page on Chrome CDP (17082).
Waits until a new question appears (different from current question),
prints the question details, and exits cleanly to trigger agent wakeup.
"""
import sys
import time
from playwright.sync_api import sync_playwright

CDP_URL = "http://127.0.0.1:17082"
CURRENT_QUESTION_SNIPPET = sys.argv[1] if len(sys.argv) > 1 else ""

def main():
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"ERROR: Cannot connect to Chrome CDP: {e}", file=sys.stderr)
            sys.exit(1)

        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "vevox.app" in pg.url:
                    page = pg
                    break
            if page:
                break

        if not page:
            print("ERROR: Vevox tab not found", file=sys.stderr)
            sys.exit(1)

        print(f"Watching Vevox tab (ignoring current: {CURRENT_QUESTION_SNIPPET[:40]!r})...", flush=True)

        while True:
            try:
                res = page.evaluate("""() => {
                    const qTitleEl = document.querySelector('[data-testid=question-title]');
                    const qText = qTitleEl ? qTitleEl.innerText.trim() : null;
                    const choiceEls = Array.from(document.querySelectorAll('[data-testid=question-choice]'));
                    const choices = choiceEls.map(b => b.innerText.trim().split('\\n')[0]);
                    const pollStateEl = document.querySelector('[pollstate]');
                    const pollState = pollStateEl ? pollStateEl.getAttribute('pollstate') : null;
                    return { qText, choices, pollState };
                }""")

                q = res.get("qText")
                choices = res.get("choices")
                poll_state = res.get("pollState")

                # Detect when a new question is open and has choices
                if q and choices and len(choices) > 0 and poll_state != "results":
                    if not CURRENT_QUESTION_SNIPPET or CURRENT_QUESTION_SNIPPET.lower() not in q.lower():
                        print(f"=== NEW_QUESTION_DETECTED ===", flush=True)
                        print(f"Title: {q}", flush=True)
                        print("Choices:", flush=True)
                        for c in choices:
                            print(f"  {c}", flush=True)
                        sys.exit(0)

                time.sleep(0.3)
            except KeyboardInterrupt:
                break
            except Exception:
                time.sleep(0.5)

if __name__ == "__main__":
    main()
