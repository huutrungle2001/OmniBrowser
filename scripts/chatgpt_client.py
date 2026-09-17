#!/usr/bin/env python3
"""
ChatGPT Web UI Client over Playwright CDP (Port 17082 / 9222).
Enables automated:
  - Text prompts & Multi-file attachments (C++, Python, JSON, Markdown, PDFs)
  - Single-session end-to-end execution without reconnect drops
  - Fresh chat creation (--new / -n) and thread switching
  - Model switching (--model / -m)
  - Live response streaming wait and stop-button detection
  - High-fidelity Markdown extraction via native Copy Response button + clipboard
  - Full code block & Canvas artifact extraction and download
  - DALL-E / generative image auto-detection and high-res downloading
  - Account and workspace profile inspection
  - Full conversation history extraction (scroll-discovery for all turns)
"""

import os
import sys
import time
import json
import base64
import argparse
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

def resolve_cdp_url() -> str:
    if "CDP_URL" in os.environ:
        return os.environ["CDP_URL"]
    for port in [17082, 9222]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5) as resp:
                if resp.status == 200:
                    return f"http://127.0.0.1:{port}"
        except Exception:
            pass
    return "http://127.0.0.1:17082"

CDP_URL = resolve_cdp_url()
ROOT_DIR = Path(__file__).resolve().parent.parent

def get_chatgpt_page(context, target_url: str = None, new_chat: bool = False):
    """Find or open a ChatGPT page in the context, optionally starting a new chat."""
    page = None
    for pg in context.pages:
        if "chatgpt.com" in pg.url:
            if target_url and target_url in pg.url:
                page = pg
                break
            if not target_url and page is None:
                page = pg

    if page is None:
        page = context.new_page()
        page.goto(target_url or "https://chatgpt.com/", wait_until="domcontentloaded", timeout=45000)
        time.sleep(2)
        return page

    if new_chat:
        print("🆕 Starting fresh ChatGPT thread...", flush=True)
        # If already at root, nothing to do
        if page.url.rstrip("/") == "https://chatgpt.com":
            return page
        new_chat_btn = page.locator("a[data-testid='create-new-chat-button'], a:has-text('New chat')").first
        if new_chat_btn.count() > 0 and new_chat_btn.is_visible():
            try:
                new_chat_btn.click(timeout=2500, force=True)
                time.sleep(1.5)
            except Exception:
                page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        else:
            page.goto("https://chatgpt.com/", wait_until="domcontentloaded")
        time.sleep(1.5)

    return page

def select_model(page, model_name: str) -> bool:
    """Select a specific model from the model dropdown if available."""
    if not model_name:
        return True

    print(f"🔄 Selecting model: {model_name}...", flush=True)
    try:
        switcher = page.locator("button[aria-label='Switch model'], button[data-testid='model-switcher-dropdown-trigger']").first
        if switcher.count() == 0 or not switcher.is_visible():
            print(f"ℹ️ Model switcher not visible on active view (using thread default).", flush=True)
            return False

        switcher.click(force=True)
        time.sleep(0.8)

        matched = page.evaluate("""(target) => {
            const items = Array.from(document.querySelectorAll("[role='menuitem'], [role='menuitemradio'], div[data-testid*='model']"));
            for (const it of items) {
                if (it.innerText.toLowerCase().includes(target.toLowerCase())) {
                    it.click();
                    return true;
                }
            }
            return false;
        }""", model_name)

        if matched:
            time.sleep(1.0)
            print(f"✅ Switched to model: {model_name}", flush=True)
            return True
        else:
            page.keyboard.press("Escape")
            print(f"⚠️ Could not find model matching '{model_name}' in menu.", flush=True)
            return False
    except Exception as e:
        print(f"⚠️ Model switch error: {e}", file=sys.stderr)
        return False

def attach_files(page, file_paths: list) -> bool:
    """Attach one or more files to the ChatGPT composer."""
    if not file_paths:
        return True

    resolved_paths = []
    for fp in file_paths:
        p = Path(fp).resolve()
        if not p.exists():
            print(f"⚠️ Warning: Attachment file not found: {fp}", file=sys.stderr)
            continue
        resolved_paths.append(str(p))

    if not resolved_paths:
        return False

    print(f"📎 Attaching {len(resolved_paths)} file(s) to ChatGPT: {[Path(p).name for p in resolved_paths]}...", flush=True)
    file_input = page.locator("input[type='file']").first
    if file_input.count() == 0:
        print("❌ Could not locate file input element in ChatGPT DOM.", file=sys.stderr)
        return False

    file_input.set_input_files(resolved_paths)
    time.sleep(1.0)
    for _ in range(25):
        uploading = page.evaluate("""() => {
            const sendBtn = document.querySelector("button[data-testid='send-button']");
            const isDisabled = sendBtn && sendBtn.hasAttribute("disabled");
            const progress = document.querySelector("[role='progressbar'], .animate-spin");
            return Boolean(isDisabled || progress);
        }""")
        if not uploading:
            break
        time.sleep(0.4)
    print("✅ Files attached successfully!", flush=True)
    return True

def extract_and_download_images(page, out_dir: Path) -> list:
    """Detect generated images in the latest assistant response and download them."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    images_info = page.evaluate("""() => {
        const assistantTurns = document.querySelectorAll("[data-message-author-role='assistant'], article");
        const lastTurn = assistantTurns.length > 0 ? assistantTurns[assistantTurns.length - 1] : document.body;
        const imgs = Array.from(lastTurn.querySelectorAll("img"));
        return imgs.map((img, idx) => ({
            index: idx,
            src: img.src,
            alt: img.alt || "",
            width: img.naturalWidth || img.width || 0,
            height: img.naturalHeight || img.height || 0
        })).filter(img => img.src && (
            img.src.includes("oaiusercontent") || 
            img.src.includes("dalle") || 
            img.src.startsWith("blob:") || 
            (img.alt && img.alt.toLowerCase().includes("generated")) ||
            (img.width > 200 && img.height > 200 && !img.src.includes("favicon"))
        ));
    }""")

    if not images_info:
        return []

    print(f"🎨 Detected {len(images_info)} generated image(s) in response. Downloading...", flush=True)
    saved_images = []
    for img in images_info:
        src = img.get("src", "")
        if not src:
            continue
        try:
            b64_data = page.evaluate("""async (url) => {
                const res = await fetch(url);
                const blob = await res.blob();
                return new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onloadend = () => {
                        const result = reader.result;
                        resolve(result.includes(',') ? result.split(',')[1] : result);
                    };
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });
            }""", src)

            if b64_data:
                raw_bytes = base64.b64decode(b64_data)
                ext = "png"
                if "jpeg" in src or "jpg" in src:
                    ext = "jpg"
                elif "webp" in src:
                    ext = "webp"
                fn = out_dir / f"chatgpt_image_{int(time.time())}_{img['index']:02d}.{ext}"
                with open(fn, "wb") as f:
                    f.write(raw_bytes)
                print(f"  🖼️ Saved: {fn} ({len(raw_bytes):,} bytes)", flush=True)
                saved_images.append(str(fn))
        except Exception as e:
            print(f"⚠️ Error downloading image {img.get('index')}: {e}", file=sys.stderr)

    return saved_images

def extract_answer_data(page) -> dict:
    """Extract Markdown text, code blocks, and canvas artifacts from the latest assistant turn."""
    markdown_text = ""
    try:
        copy_btn = page.locator("[data-testid='copy-turn-action-button']").last
        if copy_btn.count() > 0:
            copy_btn.click(timeout=2000, force=True)
            time.sleep(0.3)
            markdown_text = page.evaluate("() => navigator.clipboard.readText()")
    except Exception:
        pass

    dom_data = page.evaluate("""() => {
        const assistantTurns = document.querySelectorAll("[data-message-author-role='assistant'], article");
        const lastTurn = assistantTurns.length > 0 ? assistantTurns[assistantTurns.length - 1] : null;
        const textContent = lastTurn ? lastTurn.innerText : "";

        const codeBlocks = [];
        if (lastTurn) {
            const preElements = lastTurn.querySelectorAll("pre");
            preElements.forEach((pre, idx) => {
                const codeEl = pre.querySelector("code");
                const langClass = codeEl ? codeEl.className : "";
                const langMatch = langClass.match(/language-([a-zA-Z0-9_-]+)/);
                const lang = langMatch ? langMatch[1] : "text";

                const headerEl = pre.querySelector("div.flex.items-center, span.text-xs");
                const headerText = headerEl ? headerEl.innerText.trim() : "";

                const codeText = codeEl ? codeEl.innerText : pre.innerText;
                codeBlocks.push({
                    index: idx,
                    lang: lang,
                    header: headerText,
                    code: codeText
                });
            });
        }

        const canvasEl = document.querySelector("div.cm-content, div.view-lines");
        let canvasCode = "";
        if (canvasEl && canvasEl.innerText) {
            canvasCode = canvasEl.innerText;
        }

        return {
            dom_text: textContent,
            code_blocks: codeBlocks,
            canvas_code: canvasCode
        };
    }""")

    final_text = markdown_text if markdown_text.strip() else dom_data.get("dom_text", "")
    code_blocks = dom_data.get("code_blocks", [])
    canvas_code = dom_data.get("canvas_code", "")

    primary_code = canvas_code
    if not primary_code and code_blocks:
        sorted_blocks = sorted(code_blocks, key=lambda b: len(b.get("code", "")), reverse=True)
        primary_code = sorted_blocks[0].get("code", "")

    return {
        "text": final_text,
        "markdown": final_text,
        "code": primary_code,
        "code_blocks": code_blocks,
        "canvas_code": canvas_code
    }

def execute_chat(
    prompt_text: str = "",
    file_paths: list = None,
    thread_url: str = None,
    new_chat: bool = False,
    model: str = None,
    timeout: int = 180,
    poll_interval: float = 1.5,
    out_path: str = None,
    out_dir: str = None,
    save_md: str = None,
    images_dir: str = None
) -> dict:
    """Single-session end-to-end prompt submission, streaming wait, and extraction."""
    print(f"🚀 Connecting to Chrome CDP on {CDP_URL}...", flush=True)
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP_URL)
        except Exception as e:
            print(f"❌ Error connecting to CDP: {e}", file=sys.stderr)
            return {"text": "", "error": str(e)}

        context = browser.contexts[0]
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = get_chatgpt_page(context, target_url=thread_url, new_chat=new_chat)
        print(f"📄 Active ChatGPT Tab: {page.url} ({page.title()})", flush=True)

        # 1. Model switch if requested
        if model:
            select_model(page, model)

        # 2. Attach files if provided
        if file_paths:
            attach_files(page, file_paths)

        # 3. Fill composer
        if prompt_text:
            composer = page.locator("#prompt-textarea, div.ProseMirror").first
            if composer.count() > 0:
                try:
                    composer.click(force=True)
                    time.sleep(0.3)
                    composer.fill(prompt_text)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"⚠️ Direct fill warning: {e}", flush=True)

        # Record initial assistant turns
        initial_turns = page.evaluate("""() => {
            return document.querySelectorAll("[data-message-author-role='assistant']").length;
        }""")

        # 4. Click send or press Enter (verifying composer clearance)
        send_btn = page.locator("#composer-submit-button, button[data-testid='send-button'], button[aria-label*='Send'], button[aria-label*='Gửi']").first
        
        # Wait up to 6s for send button to be enabled
        for _ in range(20):
            is_disabled = page.evaluate("""() => {
                const b = document.querySelector("#composer-submit-button, button[data-testid='send-button']");
                return !b || b.hasAttribute('disabled') || b.getAttribute('aria-disabled') === 'true';
            }""")
            if not is_disabled:
                break
            time.sleep(0.3)

        # Submit and verify composer clearance
        for attempt in range(3):
            if send_btn.count() > 0 and send_btn.is_visible():
                try:
                    send_btn.click(timeout=2000)
                except Exception:
                    page.keyboard.press("Enter")
            else:
                page.keyboard.press("Enter")

            time.sleep(0.8)
            remaining_text = page.evaluate("""() => {
                const el = document.querySelector("#prompt-textarea, div.ProseMirror");
                return el ? el.innerText.trim() : "";
            }""")
            if not remaining_text:
                break
            time.sleep(0.5)

        print("🚀 Prompt submitted! Waiting for ChatGPT generation...", flush=True)

        # 5. Phase 1: Wait for generation to start (up to 15s)
        start_wait = time.time()
        while time.time() - start_wait < 15.0:
            gen_state = page.evaluate("""() => {
                const stopBtn = document.querySelector("button[data-testid='stop-button'], button[aria-label*='Stop']");
                const thinking = document.querySelector("div.result-thinking, .animate-pulse");
                const turns = document.querySelectorAll("[data-message-author-role='assistant']").length;
                return {
                    active: Boolean(stopBtn || thinking),
                    turns: turns
                };
            }""")
            if gen_state["active"] or gen_state["turns"] > initial_turns:
                break
            time.sleep(0.4)

        # Phase 2: Wait for generation to complete
        start_gen = time.time()
        while time.time() - start_gen < timeout:
            is_generating = page.evaluate("""() => {
                const stopBtn = document.querySelector("button[data-testid='stop-button'], button[aria-label*='Stop']");
                const thinking = document.querySelector("div.result-thinking, .animate-pulse");
                return Boolean(stopBtn || thinking);
            }""")

            if not is_generating:
                time.sleep(1.0)
                break
            time.sleep(poll_interval)
            print("  ... thinking / streaming response ...", flush=True)

        # 6. Extract response data
        res = extract_answer_data(page)

        # 7. Extract generated images if any
        img_target_dir = Path(images_dir or out_dir or (ROOT_DIR / ".tmp" / "chatgpt_images"))
        saved_imgs = extract_and_download_images(page, img_target_dir)
        res["images"] = saved_imgs

        # 8. Save artifacts & log
        save_artifacts(res, out_path=out_path, out_dir=out_dir, save_md=save_md)
        log_consultation(prompt_text or "", file_paths or [], page.url, res)

        return res

def fetch_history(out_dir: str = ".tmp", thread_url: str = None) -> list:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    print(f"📜 Fetching full conversation history to {out_path}...", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        page = get_chatgpt_page(context, thread_url)

        # Scroll to top first
        page.evaluate("""() => {
            window.scrollTo(0, 0);
            const containers = Array.from(document.querySelectorAll("div.overflow-y-auto, main, div[class*='scroll']"));
            containers.forEach(c => { c.scrollTop = 0; });
        }""")
        time.sleep(1.0)

        # Incrementally scroll down in steps to discover all turns without unmounting
        collected_turns = {}
        for step in range(25):
            current_turns = page.evaluate("""() => {
                const turns = Array.from(document.querySelectorAll("[data-message-author-role]"));
                return turns.map((t, idx) => ({
                    role: t.getAttribute("data-message-author-role"),
                    text: t.innerText
                }));
            }""")

            for t in current_turns:
                txt = t["text"].strip()
                if not txt:
                    continue
                key = (t["role"], txt[:100])
                if key not in collected_turns:
                    collected_turns[key] = t["text"]

            page.evaluate("""() => {
                window.scrollBy(0, 1000);
                const containers = Array.from(document.querySelectorAll("div.overflow-y-auto, main, div[class*='scroll']"));
                containers.forEach(c => { c.scrollTop += 1000; });
            }""")
            time.sleep(0.2)

        assistant_turns = [v for k, v in collected_turns.items() if k[0] == "assistant"]
        print(f"🔍 Discovered {len(assistant_turns)} assistant answers in thread.", flush=True)

        for i, ans in enumerate(assistant_turns):
            fn = out_path / f"chatgpt_answer_{i+1:02d}.md"
            with open(fn, "w", encoding="utf-8") as f:
                f.write(ans)
            print(f"  ✅ Saved Answer {i+1:02d} ({len(ans):,} chars) -> {fn}")

        # Save full raw conversation transcript
        transcript_path = out_path / "full_conversation_transcript.md"
        with open(transcript_path, "w", encoding="utf-8") as f:
            for (role, _), text in collected_turns.items():
                f.write(f"# [{'USER' if role == 'user' else 'ASSISTANT'}]\n\n{text}\n\n---\n\n")
        print(f"  📄 Complete conversation transcript ({len(collected_turns)} turns) -> {transcript_path}")

        return assistant_turns

def get_account_info() -> dict:
    """Inspect current logged-in ChatGPT account profile and workspace."""
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        page = get_chatgpt_page(context)
        prof_btn = page.locator("[data-testid='accounts-profile-button']").first
        info = {
            "url": page.url,
            "title": page.title(),
            "profile_name": "",
            "workspace": "",
            "logged_in": False
        }
        if prof_btn.count() > 0:
            info["logged_in"] = True
            raw_text = prof_btn.inner_text().strip()
            lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
            if not lines:
                try:
                    prof_btn.click(force=True)
                    time.sleep(0.4)
                    menu_text = page.evaluate("""() => {
                        const m = document.querySelector("[role='menu']");
                        return m ? m.innerText : "";
                    }""")
                    page.keyboard.press("Escape")
                    lines = [l.strip() for l in menu_text.split("\n") if l.strip()]
                except Exception:
                    pass

            if len(lines) >= 2:
                info["profile_name"] = lines[0]
                info["workspace"] = lines[1]
            elif len(lines) == 1:
                info["profile_name"] = lines[0]
            else:
                aria = prof_btn.get_attribute("aria-label") or ""
                info["profile_name"] = aria.replace(", open profile menu", "")
        return info

def log_consultation(prompt_text: str, file_paths: list, thread_url: str, res: dict):
    log_file = ROOT_DIR / "results" / "chatgpt_consultations.jsonl"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "thread_url": thread_url,
        "files_attached": [Path(p).name for p in file_paths] if file_paths else [],
        "prompt_length": len(prompt_text),
        "prompt_preview": prompt_text[:150].replace("\n", " "),
        "response_chars": len(res.get("text", "")),
        "code_chars": len(res.get("code", "")),
        "num_code_blocks": len(res.get("code_blocks", [])),
        "images_downloaded": len(res.get("images", []))
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"📊 Logged consultation to: {log_file}")

def save_artifacts(res: dict, out_path: str = None, out_dir: str = None, save_md: str = None):
    if save_md and res.get("text"):
        p = Path(save_md)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(res["text"])
        print(f"📄 Full response saved to: {p}")

    if out_path and res.get("code"):
        p = Path(out_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(res["code"])
        print(f"💾 Primary code block saved to: {p} ({len(res['code']):,} chars)")

    if out_dir and (res.get("code_blocks") or res.get("canvas_code")):
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        if res.get("canvas_code"):
            with open(d / "canvas_artifact.txt", "w", encoding="utf-8") as f:
                f.write(res["canvas_code"])
            print(f"📦 Saved canvas artifact to: {d / 'canvas_artifact.txt'}")

        for block in res.get("code_blocks", []):
            idx = block.get("index", 0)
            lang = block.get("lang", "txt")
            ext = {"cpp": "cpp", "c++": "cpp", "python": "py", "py": "py", "json": "json", "bash": "sh", "sh": "sh"}.get(lang, "txt")
            filename = f"block_{idx:02d}.{ext}"
            with open(d / filename, "w", encoding="utf-8") as f:
                f.write(block.get("code", ""))
            print(f"📦 Saved code block {idx} ({lang}) to: {d / filename}")

def main():
    parser = argparse.ArgumentParser(description="Interact with ChatGPT Web UI via Playwright CDP (Attach files, prompt, extract, download, history, accounts)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # chat / prompt
    p_chat = subparsers.add_parser("chat", help="Send a message and optional files, and wait for reply")
    p_chat.add_argument("message", type=str, nargs="?", default="", help="Prompt message text")
    p_chat.add_argument("--files", "-f", nargs="+", default=[], help="File path(s) to attach (e.g. -f code.cpp data.json)")
    p_chat.add_argument("--new", "-n", action="store_true", help="Start a new chat before sending prompt")
    p_chat.add_argument("--model", "-m", type=str, default=None, help="Select model (e.g. 'gpt-4o', '5.6 Sol', 'o3-mini')")
    p_chat.add_argument("--thread", type=str, default=None, help="Specific thread URL")
    p_chat.add_argument("--timeout", type=int, default=180, help="Max wait time in seconds")
    p_chat.add_argument("--out", "-o", type=str, default=None, help="File path to save primary extracted code block")
    p_chat.add_argument("--out-dir", type=str, default=None, help="Directory to save all code blocks & artifacts")
    p_chat.add_argument("--save-md", type=str, default=None, help="File path to save full response markdown")
    p_chat.add_argument("--images-dir", type=str, default=None, help="Directory to save generated images")

    # new chat
    p_new = subparsers.add_parser("new-chat", help="Start a fresh conversation thread")
    p_new.add_argument("--model", "-m", type=str, default=None, help="Select model for new chat")

    # extract only
    p_extract = subparsers.add_parser("extract", help="Extract latest response/code from active tab")
    p_extract.add_argument("--out", "-o", type=str, default=None, help="File path to save primary extracted code block")
    p_extract.add_argument("--out-dir", type=str, default=None, help="Directory to save all code blocks & artifacts")
    p_extract.add_argument("--save-md", type=str, default=None, help="File path to save full response markdown")
    p_extract.add_argument("--images-dir", type=str, default=None, help="Directory to save generated images")

    # download images
    p_images = subparsers.add_parser("download-images", help="Download all generated images from current tab")
    p_images.add_argument("--out-dir", "-d", type=str, default=".tmp/chatgpt_images", help="Directory to save images")

    # account info
    p_account = subparsers.add_parser("account", help="Check current ChatGPT login & workspace profile")

    # history extraction
    p_history = subparsers.add_parser("history", help="Extract full history of all turns in conversation")
    p_history.add_argument("--out-dir", "-d", type=str, default=".tmp", help="Directory to save conversation files")
    p_history.add_argument("--thread", type=str, default=None, help="Specific thread URL")

    args = parser.parse_args()

    if args.command == "account":
        acc = get_account_info()
        print(f"👤 Account Profile: {acc['profile_name']}")
        print(f"🏢 Workspace/Plan: {acc['workspace']}")
        print(f"🔗 Active Tab: {acc['url']}")
        print(f"🔐 Logged In: {acc['logged_in']}")
        return

    if args.command == "new-chat":
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = get_chatgpt_page(context, new_chat=True)
            if args.model:
                select_model(page, args.model)
            print(f"✅ Ready on new chat: {page.url}")
        return

    if args.command == "download-images":
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            page = get_chatgpt_page(context)
            imgs = extract_and_download_images(page, Path(args.out_dir))
            print(f"🖼️ Total downloaded images: {len(imgs)}")
        return

    if args.command == "history":
        fetch_history(out_dir=args.out_dir, thread_url=args.thread)
        return

    if args.command == "extract":
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(CDP_URL)
            context = browser.contexts[0]
            context.grant_permissions(["clipboard-read", "clipboard-write"])
            page = get_chatgpt_page(context)
            res = extract_answer_data(page)
            img_target_dir = Path(args.images_dir or args.out_dir or (ROOT_DIR / ".tmp" / "chatgpt_images"))
            saved_imgs = extract_and_download_images(page, img_target_dir)
            res["images"] = saved_imgs

            print("\n" + "=" * 80)
            print("🤖 LATEST CHATGPT RESPONSE:")
            print("=" * 80)
            print(res.get("text", "")[:2000])
            if len(res.get("text", "")) > 2000:
                print(f"\n... [{len(res.get('text', '')) - 2000} more characters truncated] ...")
            print("=" * 80)

            save_artifacts(res, out_path=args.out, out_dir=args.out_dir, save_md=args.save_md)
            log_consultation("extract", [], page.url or "", res)
        return

    if args.command == "chat":
        if not args.message and not args.files:
            print("❌ Error: Must provide either a message or at least one file to attach.", file=sys.stderr)
            sys.exit(1)

        res = execute_chat(
            prompt_text=args.message,
            file_paths=args.files,
            thread_url=args.thread,
            new_chat=args.new,
            model=args.model,
            timeout=args.timeout,
            out_path=args.out,
            out_dir=args.out_dir,
            save_md=args.save_md,
            images_dir=args.images_dir
        )

        print("\n" + "=" * 80)
        print("🤖 CHATGPT RESPONSE:")
        print("=" * 80)
        print(res.get("text", "")[:2000])
        if len(res.get("text", "")) > 2000:
            print(f"\n... [{len(res.get('text', '')) - 2000} more characters truncated] ...")
        print("=" * 80)

if __name__ == "__main__":
    main()
