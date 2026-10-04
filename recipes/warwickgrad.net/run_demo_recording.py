import os
import signal
import subprocess
import time
from playwright.sync_api import sync_playwright

RAW_VIDEO_PATH = "/tmp/omnibrowser_demo_raw.mov"
FINAL_VIDEO_PATH = "/Users/huutrungle2001/Documents/OnGoing/Trung-Le-Huu/portfolio/applications/warwickgrad_demo/omnibrowser_warwickgrad_demo.mp4"

def click_section_edit(page, h2_text):
    return page.evaluate("""(text) => {
        const h2 = Array.from(document.querySelectorAll('h2')).find(h => h.innerText.trim().toLowerCase().includes(text.toLowerCase()));
        if (!h2) return false;
        let container = h2.parentElement;
        for (let i = 0; i < 6; i++) {
            if (!container) break;
            const b = Array.from(container.querySelectorAll('button')).find(btn => btn.innerText.trim() === 'Edit');
            if (b) {
                b.scrollIntoView({ behavior: 'smooth', block: 'center' });
                b.click();
                return true;
            }
            container = container.parentElement;
        }
        return false;
    }""", h2_text)

def click_section_save(page, h2_text):
    res = page.evaluate("""(text) => {
        const h2s = Array.from(document.querySelectorAll('h2'));
        const h = h2s.find(el => el.innerText.trim().toLowerCase().includes(text.toLowerCase()));
        if (!h) return false;
        const nextH = h2s.find((el, i) => i > h2s.indexOf(h) && !el.innerText.trim().toLowerCase().includes(text.toLowerCase()));
        const topBound = h.getBoundingClientRect().top + window.scrollY - 20;
        const bottomBound = nextH ? (nextH.getBoundingClientRect().top + window.scrollY + 20) : 999999;

        const saveBtn = Array.from(document.querySelectorAll('button')).find(b => {
            const bTop = b.getBoundingClientRect().top + window.scrollY;
            return bTop >= topBound && bTop <= bottomBound && b.innerText.trim() === 'Save all' && b.offsetParent !== null;
        });
        if (saveBtn) {
            saveBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            saveBtn.click();
            return true;
        }
        return false;
    }""", h2_text)
    time.sleep(2.0)
    return res

def click_section_add_new(page, h2_text, index=0):
    res = page.evaluate("""({ text, targetIndex }) => {
        const h2s = Array.from(document.querySelectorAll('h2'));
        const h = h2s.find(el => el.innerText.trim().toLowerCase().includes(text.toLowerCase()));
        if (!h) return false;
        const nextH = h2s.find((el, i) => i > h2s.indexOf(h) && !el.innerText.trim().toLowerCase().includes(text.toLowerCase()));
        const topBound = h.getBoundingClientRect().top + window.scrollY - 20;
        const bottomBound = nextH ? (nextH.getBoundingClientRect().top + window.scrollY + 20) : 999999;

        const addBtns = Array.from(document.querySelectorAll('button')).filter(b => {
            const bTop = b.getBoundingClientRect().top + window.scrollY;
            return bTop >= topBound && bTop <= bottomBound && b.innerText.includes('Add New') && b.offsetParent !== null;
        });
        if (addBtns[targetIndex]) {
            addBtns[targetIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
            addBtns[targetIndex].click();
            return true;
        }
        return false;
    }""", {"text": h2_text, "targetIndex": index})
    time.sleep(0.8)
    return res

def fill_input_text(page, aria_label, value, delay=15):
    loc = page.locator(f"input[aria-label='{aria_label}'], textarea[aria-label='{aria_label}']").first
    loc.scroll_into_view_if_needed()
    loc.click()
    page.keyboard.press("Meta+a")
    time.sleep(0.05)
    page.keyboard.press("Backspace")
    time.sleep(0.05)
    loc.press_sequentially(value, delay=delay)
    time.sleep(0.2)

def set_masked_date(page, label_snippet, date_val):
    page.evaluate("""({ snippet, val }) => {
        const inp = Array.from(document.querySelectorAll('input')).find(el => (el.getAttribute('aria-label') || '').toLowerCase().includes(snippet.toLowerCase()) && el.offsetParent !== null);
        if (inp) {
            inp.scrollIntoView({ behavior: 'smooth', block: 'center' });
            const container = inp.closest('.q-field');
            if (container) {
                const clearBtn = container.querySelector('.fa-times-circle');
                if (clearBtn) clearBtn.click();
            }
            inp.focus();
            inp.value = val;
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
            inp.blur();
        }
    }""", {"snippet": label_snippet, "val": date_val})
    time.sleep(0.3)

def select_quasar_option(page, label_snippet, search_query, option_match=None):
    if option_match is None:
        option_match = search_query
    page.evaluate("""({ snippet, query }) => {
        const inputs = Array.from(document.querySelectorAll('input'));
        const input = inputs.find(el => {
            const l = el.getAttribute('aria-label') || '';
            return l.toLowerCase().includes(snippet.toLowerCase()) && el.offsetParent !== null;
        });
        if (input) {
            input.scrollIntoView({ behavior: 'smooth', block: 'center' });
            input.focus();
            input.value = query;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }""", {"snippet": label_snippet, "query": search_query})
    time.sleep(0.8)
    page.evaluate("""(match) => {
        const items = Array.from(document.querySelectorAll('.q-menu .q-item, .q-virtual-scroll__content .q-item, .q-item__label'));
        const target = items.find(el => el.innerText.trim().toLowerCase().includes(match.toLowerCase()));
        if (target) {
            target.click();
        }
    }""", option_match)
    time.sleep(0.5)

def main():
    print("1. Bringing Google Chrome to front...")
    subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'])
    time.sleep(1.5)

    if os.path.exists(RAW_VIDEO_PATH):
        try:
            os.remove(RAW_VIDEO_PATH)
        except OSError:
            pass

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:17082")
        context = browser.contexts[0]
        
        target_page = None
        for page in context.pages:
            if "warwickgrad.net" in page.url or "about:blank" in page.url:
                target_page = page
                break
        if not target_page:
            target_page = context.new_page()

        print("2. Starting from clean empty tab (about:blank)...")
        target_page.goto("about:blank")
        target_page.bring_to_front()
        time.sleep(1.0)

        # Elevate Chrome right before recording
        subprocess.run(["osascript", "-e", 'tell application "Google Chrome" to activate'])
        time.sleep(0.5)

        print("3. Starting screen recording via screencapture (-k -C)...")
        record_proc = subprocess.Popen(["screencapture", "-v", "-k", "-C", RAW_VIDEO_PATH])
        print(f"Recording process PID: {record_proc.pid}")
        time.sleep(2.5)  # Visual intro on blank tab

        try:
            print("4. Navigating to WarwickGrad profile...")
            target_page.goto("https://www.warwickgrad.net/app/community/profile", wait_until="networkidle", timeout=30000)
            time.sleep(2.5)

            # ==========================================
            # SECTION 1: PERSONAL HEADLINE
            # ==========================================
            print("5. Automating Section 1: Personal Headline...")
            click_section_edit(target_page, "personal headline")
            time.sleep(0.6)
            fill_input_text(target_page, "Headline", "MSc Applied Artificial Intelligence at University of Warwick | AI & Full-Stack Engineer", delay=12)
            click_section_save(target_page, "personal headline")

            # ==========================================
            # SECTION 2: LIFE SINCE WARWICK (BIO)
            # ==========================================
            print("6. Automating Section 2: Life since Warwick (Bio)...")
            click_section_edit(target_page, "Life since Warwick")
            time.sleep(0.6)
            bio_text = ("MSc student in Applied Artificial Intelligence at the University of Warwick (WMG Excellence Scholarship recipient). "
                        "Background in combinatorial optimization, mathematical modeling, and full-stack software development. "
                        "Previously published peer-reviewed research on vehicle-drone synchronization with Springer (CITA 2025), "
                        "won 1st prize in the global Solana Consumer Hackathon, and achieved two Bronze Medals in the Vietnam National Mathematical Olympiad. "
                        "Passionate about operational research, agentic workflows, and collaborating on high-impact AI engineering projects.")
            fill_input_text(target_page, "Life since Warwick", bio_text, delay=6)
            click_section_save(target_page, "Life since Warwick")

            # ==========================================
            # SECTION 3: PERSONAL DETAILS
            # ==========================================
            print("7. Automating Section 3: Personal details...")
            click_section_edit(target_page, "Personal details")
            time.sleep(0.6)
            fill_input_text(target_page, "Title", "Mr", delay=20)
            fill_input_text(target_page, "Middle Name(s)", "Huu", delay=20)
            fill_input_text(target_page, "Preferred name", "Trung", delay=20)
            set_masked_date(target_page, "Date of Birth", "10/08/2001")
            select_quasar_option(target_page, "Sex", "Male", "Male")
            select_quasar_option(target_page, "Nationality", "Viet", "Vietnamese")
            click_section_save(target_page, "Personal details")

            # ==========================================
            # SECTION 4: EDUCATION DETAILS
            # ==========================================
            print("8. Automating Section 4: Education details...")
            click_section_edit(target_page, "Education details")
            time.sleep(0.6)
            
            # Official Education
            fill_input_text(target_page, "Department", "Warwick Manufacturing Group (WMG)", delay=10)
            fill_input_text(target_page, "Course", "Applied Artificial Intelligence", delay=10)
            fill_input_text(target_page, "Degree", "Master of Science (MSc)", delay=10)
            fill_input_text(target_page, "Year Started", "2026", delay=15)
            fill_input_text(target_page, "Year Graduated", "2027", delay=15)

            # Other Education (Phenikaa)
            fill_input_text(target_page, "Institution", "Phenikaa University", delay=10)
            target_page.evaluate("""() => {
                const setVal = (label, index, val) => {
                    const inputs = Array.from(document.querySelectorAll('input')).filter(el => el.getAttribute('aria-label') === label && el.offsetParent !== null);
                    if (inputs[index]) {
                        inputs[index].focus();
                        inputs[index].value = val;
                        inputs[index].dispatchEvent(new Event('input', { bubbles: true }));
                    }
                };
                setVal('Degree', 1, 'Bachelor of Information Technology (B.IT)');
                setVal('Course', 1, 'Artificial Intelligence and Data Science');
                setVal('Year Started', 1, '2019');
                setVal('Year Graduated', 1, '2025');
                setVal('City', 0, 'Hanoi');
            }""")
            time.sleep(0.4)
            select_quasar_option(target_page, "Country", "Viet", "Viet Nam")
            click_section_save(target_page, "Education details")

            # ==========================================
            # SECTION 5: PROFESSIONAL HISTORY
            # ==========================================
            print("9. Automating Section 5: Professional history...")
            click_section_edit(target_page, "Professional history")
            time.sleep(0.6)
            fill_input_text(target_page, "Position", "Software Engineer", delay=12)
            fill_input_text(target_page, "Organisation", "Toshiba Software Development Vietnam (TSDV)", delay=10)
            fill_input_text(target_page, "Department", "Software Engineering", delay=12)
            set_masked_date(target_page, "Start Date", "01/01/2023")
            set_masked_date(target_page, "End Date", "01/01/2024")
            fill_input_text(target_page, "City", "Hanoi", delay=15)
            select_quasar_option(target_page, "Country", "Viet", "Viet Nam")
            desc_text = ("Engineered and maintained a large-scale enterprise newspaper-composition system in C++. "
                         "Diagnosed nested pointer networks, complex data structures, and memory leaks across the shared UI component library.")
            fill_input_text(target_page, "Description", desc_text, delay=6)
            click_section_save(target_page, "Professional history")

            # ==========================================
            # SECTION 6: CONTACT DETAILS
            # ==========================================
            print("10. Automating Section 6: Contact details...")
            click_section_edit(target_page, "Contact details")
            time.sleep(0.6)
            fill_input_text(target_page, "Mobile Number", "+44 7455 344678", delay=15)
            fill_input_text(target_page, "Website", "https://github.com/huutrungle2001", delay=10)
            fill_input_text(target_page, "LinkedIn", "https://linkedin.com/in/huutrungle2001", delay=10)
            click_section_save(target_page, "Contact details")

            # ==========================================
            # SECTION 7: ADDRESS
            # ==========================================
            print("11. Automating Section 7: Address...")
            click_section_edit(target_page, "Address")
            time.sleep(0.6)
            fill_input_text(target_page, "Address Line 1", "Room A-806-A, Arundel House", delay=12)
            fill_input_text(target_page, "Address Line 2", "Whitefriars Lane", delay=12)
            fill_input_text(target_page, "City", "Coventry", delay=15)
            fill_input_text(target_page, "Postcode", "CV1 2NA", delay=15)
            select_quasar_option(target_page, "Country (type the first 3 letters", "United Kingdom", "United Kingdom")
            click_section_save(target_page, "Address")

            # ==========================================
            # SECTION 8: OTHER CAREERS SUPPORT (TOGGLES)
            # ==========================================
            print("12. Automating Section 8: Other careers support...")
            click_section_edit(target_page, "Other careers support")
            time.sleep(0.6)
            target_page.evaluate("""() => {
                const toggles = Array.from(document.querySelectorAll('.q-toggle')).filter(el => el.offsetParent !== null);
                for (const t of toggles) {
                    const text = t.innerText.trim();
                    if (text.includes('Sharing my career story') || 
                        text.includes('Supporting Student enterprise activity') || 
                        text.includes('Advise prospective Students')) {
                        if (t.getAttribute('aria-checked') === 'false') {
                            t.click();
                        }
                    }
                }
            }""")
            time.sleep(0.4)
            click_section_save(target_page, "Other careers support")

            # ==========================================
            # GRAND FINALE: SMOOTH SCROLL BACK TO TOP
            # ==========================================
            print("13. Grand Finale: Smooth scrolling to top to present completed profile...")
            target_page.evaluate("window.scrollTo({ top: 0, behavior: 'smooth' })")
            time.sleep(5.0)

        finally:
            print("14. Stopping screen recording...")
            record_proc.send_signal(signal.SIGINT)
            try:
                record_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                record_proc.kill()
            time.sleep(1.5)

    print("15. Transcoding video to MP4...")
    if os.path.exists(RAW_VIDEO_PATH):
        cmd = [
            "ffmpeg", "-y",
            "-i", RAW_VIDEO_PATH,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-crf", "18",
            FINAL_VIDEO_PATH
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print(f"SUCCESS! Demo video generated at:\n{FINAL_VIDEO_PATH}")
            size_mb = os.path.getsize(FINAL_VIDEO_PATH) / (1024 * 1024)
            print(f"Video file size: {size_mb:.2f} MB")
        else:
            print(f"FFmpeg error: {res.stderr}")
    else:
        print("Error: RAW_VIDEO_PATH not found.")

if __name__ == "__main__":
    main()
