# OmniBrowser: Complete Master Architectural & Implementation Plan

This document represents the definitive, full-scale technical architecture and phased execution plan for **OmniBrowser**, integrating the complete engineering specifications developed by Codex and refined through adversarial review.

---

## 1. Architectural Philosophy & The Progressive Capability Pyramid

OmniBrowser transforms rudimentary, token-bloated, and high-latency browser automation into a token-efficient, robust **Progressive Capability Architecture**. It eliminates raw HTML dumps and full-screen 4K screenshot overhead by enforcing a strict hierarchy of capabilities:

```text
                     ▲
                    / \
                   /   \     Layer 4: Targeted Visual Crop (inspectVisual)
                  / Visual\  - Scoped bbox screenshots for CAPTCHAs, canvas, complex charts.
                 /─────────\
                / Bounded   \   Layer 3: Bounded Code Execution (runBrowserCode)
               /  JS / Py    \  - Arbitrary scripts for complex SPAs, drag-and-drop.
              /───────────────\
             /  Native Action  \   Layer 2: Atomic Actions with Postconditions (act)
            /   (Click, Fill)   \  - Click/type by opaque node ref, verify state delta.
           /─────────────────────\
          /   Semantic DOM Tree   \   Layer 1: Fast Path Semantic Projection (observe)
         /      (Opaque Refs)      \  - Compact JSON (<15KB), clean accessibility tree.
        └───────────────────────────┘
```

### Core Tenets:
1. **Semantic projection = fast-path có thể thiếu (Lossy by design)**: Luôn khai báo coverage metadata (`"is_complete": false`).
2. **Raw browser code / CDP inspect = escape hatch luôn sẵn sàng**: Cho phép agent thoát khỏi semantic projection khi gặp cấu trúc DOM phức tạp hoặc SPA bất thường.
3. **Visual = escalation có mục tiêu, không phải default**: Không chụp toàn màn hình, chỉ crop đúng bounding box xung quanh element gây tranh chấp.
4. **Site Pack = accelerator lazy-loaded (Deferred to v2.0)**: Core Engine phải hoàn toàn tổng quát (generic) và độc lập trước khi mở rộng chuyên biệt hóa cho từng trang web.

---

## 2. Refined High-Cohesion File Layout (v1.0 Consensus)

Following architectural debate, the repository rejects premature micro-file fragmentation (14 files) in favor of **4 cohesive Python core modules** + **1 in-browser JavaScript scanner** + **1 backward-compatible CLI adapter**:

```text
OmniBrowser/
├── AGENTS.md                                     # Hiến pháp multi-agent, invariants, handoff matrix
├── GOAL.md                                       # Mục tiêu tối thượng & Progressive Capability Pyramid
├── PLAN.md                                       # Bản kế hoạch tổng thể & chi tiết kỹ thuật này
├── TODO.md                                       # Danh mục backlog & tracking tiến độ
├── .gitignore
├── requirements.txt                              # playwright, pillow, jsonschema, pytest
├── pytest.ini
├── scripts/
│   ├── cdp_controller.py                         # CLI adapter: 100% backward compatibility + new subcommands
│   ├── dom_agent.js                              # Page-side observer & candidate index (injected into browser)
│   ├── notify_agent.sh                           # Inter-agent handoff notification helper
│   └── lint_communication_records.py             # Schema & protocol linter
├── src/
│   └── browser_core/
│       ├── __init__.py
│       ├── contracts.py                          # Request/response dataclasses, error taxonomy, constants
│       ├── page_manager.py                       # CDP connection, tab/frame selection, epoch lifecycle, injection
│       ├── engine.py                             # observe(), act(), expectation watchers, state deltas, stale recovery
│       └── primitives.py                         # inspect(), runBrowserCode(), inspectVisual() crop
└── tests/
    ├── conftest.py
    ├── test_cli_legacy.py                        # Backward compatibility tests (goto, eval, list-tabs, screenshot)
    ├── test_observe.py                           # Semantic projection & opaque ref tests
    ├── test_actions.py                           # Atomic actions, native input, state delta tests
    ├── test_stale_recovery.py                    # Stale ref detection & fingerprint recovery
    ├── test_primitives.py                        # inspect, runBrowserCode, inspectVisual tests
    └── fixtures/
        ├── standard_form.html                    # Baseline HTML forms & validation
        ├── spa_replacement.html                  # Dynamic DOM re-rendering & stale ref test
        ├── iframe_parent.html                    # Multi-frame / iframe hierarchy test
        ├── iframe_child.html
        ├── shadow_components.html                # Open shadow DOM components
        └── canvas_ui.html                        # Canvas fallback & visual crop test
```

---

## 3. Data Contracts & Communication Protocol

### 3.1 Standard Output Envelope
Mọi lệnh mới trả JSON có version ra `stdout`; diagnostic ngắn ra `stderr`:

```json
{
  "version": "1",
  "ok": true,
  "page": {
    "tab_id": "tab-2",
    "frame_id": "f0",
    "url": "https://example.test/form",
    "document_epoch": "d9",
    "revision": "r42"
  }
}
```

### 3.2 Machine-Readable Error Contract
Mọi lỗi đều có mã định danh chuẩn và gợi ý hành động tiếp theo:

```json
{
  "version": "1",
  "ok": false,
  "error": {
    "code": "STALE_REF",
    "message": "The element was replaced after a SPA render.",
    "recoverable": true,
    "suggested_next": "observe-or-inspect"
  }
}
```

### 3.3 Exit Code Standards:
- `0`: Action / Inspection thành công.
- `2`: Input / CLI contract không hợp lệ.
- `3`: CDP / Browser unavailable.
- `4`: Target / Ref không tìm thấy hoặc stale không recover được.
- `5`: Policy / Approval block.
- `6`: Timeout / Postcondition failed.
- `7`: Internal / Controller fault.

---

## 4. Deep Technical Module Specifications

### Module 1: Semantic Interaction Projector (`dom_agent.js` & `page_manager.py`)

#### Injection & Lifecycle:
- `page_manager.py` kết nối CDP qua Playwright tới debugging port (17082 cho production, port ngẫu nhiên cho tests).
- Inject `dom_agent.js` vào mọi document mới thông qua `Page.addScriptToEvaluateOnNewDocument`.
- Quản lý lifecycle: `page`, `frame`, `documentEpoch`, `revision`.
- Tự động invalidate handles khi navigation, detach frame, hoặc reload.
- Tuyệt đối **không ghi `data-*` vào DOM thật** của trang web để tránh xung đột với framework JS của ứng dụng (React/Vue/Svelte).

#### In-Page State Management (`dom_agent.js`):
```javascript
WeakMap<Element, nodeToken>   // Ánh xạ DOM element sang token định danh nội bộ
documentEpoch                  // Token đổi mỗi khi page load/navigate
revision                       // Số đếm tăng mỗi khi MutationObserver phát hiện DOM thay đổi
MutationObserver               // Đánh dấu dirty region, không serialize toàn bộ DOM tại mỗi mutation
candidateCache                 // Cache các interactive elements hợp lệ
```

#### Candidate Selection Strategy:
- **Giữ lại (Interactive):**
  - Native controls: `button`, `a[href]`, `input`, `textarea`, `select`, `option`.
  - ARIA roles: `button`, `checkbox`, `textbox`, `combobox`, `tab`, `menuitem`, `switch`, `slider`, `link`.
  - Functional elements: `[contenteditable]`, element có `tabindex >= 0`, `dialog`, `form`, active toast/alert.
  - Elements nằm trong Open Shadow Root và Attach-able Iframes.
- **Loại bỏ (Noise Filtering):**
  - `script`, `style`, SVG icons/decorations, analytics pixels, ads, hidden templates.
  - Invisible elements: `display:none`, `visibility:hidden`, `aria-hidden="true"`, `inert`, `hidden`.
  - Duplicated responsive layouts và boilerplate ngoài viewport scope.

#### Opaque Node Ref Scheme:
```text
Ref Format: f0.d9.n186
Display ID: E12 (Chỉ dùng để hiển thị cho con người/LLM đọc lướt, không dùng làm identity action)
```
- `f0`: Frame token.
- `d9`: Document epoch token.
- `n186`: Node token duy nhất trong document.
- Fingerprint lưu kèm: `role`, `accessible_name`, `label_text`, `form_owner`, `landmark_path`, `geometry_rect`.

#### `observe()` Output Schema:
```json
{
  "version": "1",
  "ok": true,
  "page": { "tab_id": "tab-1", "frame_id": "f0", "url": "https://warwick.ac.uk/enrolment", "revision": "r42" },
  "coverage": {
    "is_complete": false,
    "included": ["visible-interactive", "active-dialog", "validation-messages"],
    "omitted": ["offscreen-content", "canvas", "unexposed-closed-shadow-root"]
  },
  "tree": [
    {
      "ref": "f0.d9.n186",
      "display_id": "E1",
      "role": "textbox",
      "name": "Student ID",
      "value": "",
      "bounds": [120, 240, 280, 40],
      "clickable": true
    },
    {
      "ref": "f0.d9.n210",
      "display_id": "E2",
      "role": "button",
      "name": "Continue",
      "disabled": false,
      "bounds": [120, 300, 150, 44],
      "clickable": true
    }
  ]
}
```

---

### Module 2: Atomic Action & Postcondition Engine (`engine.py`)

#### Action Execution Lifecycle:
1. **Resolve Ref**: Tra cứu node ref `f0.d9.n186` trong document/frame hiện hành.
2. **Pre-action Checks**: Xác minh element còn attached, visible, không bị overlay che khuất (occlusion).
3. **Scroll into View**: Cuộn target vào viewport nếu nằm ngoài tầm nhìn.
4. **Native Input Dispatch**: Sử dụng CDP native mouse/keyboard events (`Input.dispatchMouseEvent`, `Input.dispatchKeyEvent`).
5. **Attach Expectations Watcher**: Kích hoạt bộ lắng nghe postcondition *trước* khi bắn event để không bị lỡ micro-mutations.
6. **Evaluate Delta**: So sánh trạng thái DOM trước/sau và trả về `StateDelta`.

#### Expectation Watchers Contract:
Không dùng `time.sleep()` cố định. Hỗ trợ polling watcher với timeout tối đa:
```json
{
  "op": "click",
  "target": { "ref": "f0.d9.n210" },
  "expect": {
    "url_matches": "/step2",
    "element": { "ref": "f0.d9.n305", "visible": true },
    "text_present": "Enrolment Stage 2",
    "timeout_ms": 8000
  }
}
```

#### State Delta Return:
Chỉ trả về những gì đã thay đổi, không trả lại nguyên trang (tiết kiệm token tối đa):
```json
{
  "ok": true,
  "revision": "r43",
  "action_duration_ms": 142,
  "delta": {
    "changed": [
      { "ref": "f0.d9.n186", "value": "2104928" }
    ],
    "added": [
      { "ref": "f0.d9.n305", "role": "heading", "name": "Enrolment Stage 2" }
    ],
    "removed": [
      { "ref": "f0.d9.n210" }
    ]
  }
}
```

#### Stale-Ref Recovery Algorithm:
Khi nhận `ref` không còn trong DOM:
1. Thử resolve trực tiếp trong frame hiện tại.
2. Nếu miss, tìm kiếm theo Fingerprint: so khớp `role` + `accessible_name` + `parent_landmark`.
3. Nếu tìm thấy duy nhất 1 candidate khớp: thực hiện action và trả warning `auto_recovered: true`.
4. Nếu tìm thấy nhiều candidate mơ hồ hoặc document epoch đã đổi do chuyển trang: fail-closed với `STALE_REF`, trả gợi ý `observe`.

---

### Module 3: Escape Hatches & Visual Escalation (`primitives.py`)

#### `inspect`:
Hỗ trợ kiểm tra chi tiết có ranh giới:
- `dom`: Bounded subtree với độ sâu và số ký tự text giới hạn.
- `ax`: Accessibility tree partial của riêng ref/subtree đó.
- `frame-tree`: Liệt kê cây frame, URL, origin, và attach status.

#### `runBrowserCode`:
- Chạy script JavaScript tùy biến trong context của page/frame.
- Bọc trong `async IIFE` an toàn.
- Output bắt buộc là JSON serializable, giới hạn tối đa **32 KB** output.
- Timeout mặc định: 2 giây cho chế độ `read`, 10 giây cho chế độ `write`.
- Tuyệt đối không dump raw storage/cookies nếu không có yêu cầu bảo mật cụ thể.

#### `inspectVisual`:
- Chụp ảnh màn hình có mục tiêu (Scoped Screenshot).
- Clip ảnh đúng bounding box của element/container được chỉ định + padding 20px.
- Nếu cần annotate: dùng Pillow vẽ nhãn ID lên ảnh phụ, giữ nguyên ảnh gốc.
- **Quy tắc CAPTCHA / 2FA**: Khi phát hiện CAPTCHA, chỉ chụp ảnh bằng chứng và báo `REQUIRES_HUMAN_VERIFICATION`. Tuyệt đối không tự động giải hay bypass.

---

### Module 4: CLI Adapter & Backward Compatibility (`cdp_controller.py`)

#### Legacy Subcommands (Bắt buộc giữ nguyên 100%):
- `list-tabs`: Liệt kê các tab đang mở.
- `goto <url>`: Điều hướng tab tới URL.
- `eval <expression>`: Đánh giá biểu thức JS (đánh dấu legacy trong doc nhưng giữ nguyên code).
- `screenshot [--output FILE]`: Chụp toàn màn hình.

#### New Subcommands:
- `observe [--scope main|viewport] [--query "..."] [--max-elements 80] [--json]`
- `act --action <click|fill|select|press> --ref <ref> [--value <val>] [--expect <json>] [--json]`
- `inspect <dom|ax|frame-tree> [--ref <ref>] [--depth 3]`
- `run-code --script <file_or_string> [--frame <f_id>] [--mode read|write]`
- `visual [--ref <ref>] [--annotate] [--output <path>]`

---

## 5. Phased Implementation Roadmap & Verification Gates

### Phase 1: Core Foundation & Semantic Scanner
**Deliverables**:
1. `src/browser_core/contracts.py`: Dataclasses, schemas, error types, exit codes.
2. `scripts/dom_agent.js`: Page-side DOM scanner with BFS traversal, WeakMap, opaque ref generation, and filtering.
3. `src/browser_core/page_manager.py`: Playwright CDP connection manager, multi-target attach, script injection, and epoch lifecycle.
4. Test fixtures: `tests/fixtures/standard_form.html`, `spa_replacement.html`, `iframe_parent.html`.

**Validation Gate (Gate 1)**:
- Chạy ephemeral Chrome test suite: `python3 -m pytest tests/test_observe.py -v`.
- Assert DOM projection trả về JSON < 15KB trên standard form fixture.
- Assert opaque refs phân giải chính xác các trường input và button.
- `git diff --check` sạch 100%.

---

### Phase 2: Action Engine & Escape Hatches
**Deliverables**:
1. `src/browser_core/engine.py`: `act()` implementation, native event dispatch, expectation watchers, state delta calculator, stale-ref recovery.
2. `src/browser_core/primitives.py`: `inspect()`, `runBrowserCode()`, `inspectVisual()` targeted crop with Pillow.
3. Interactive fixtures: `tests/fixtures/shadow_components.html`, `canvas_ui.html`.

**Validation Gate (Gate 2)**:
- Chạy test suite: `python3 -m pytest tests/test_actions.py tests/test_stale_recovery.py tests/test_primitives.py -v`.
- Assert click và fill form thành công, state delta phản ánh đúng trường dữ liệu vừa nhập.
- Assert SPA re-render kích hoạt fingerprint recovery thành công.
- Assert `inspectVisual` tạo ảnh crop đúng kích thước bounding box.

---

### Phase 3: Unified CLI, Backward Compatibility & Regression Suite
**Deliverables**:
1. `scripts/cdp_controller.py`: Dispatch subcommands mới, giữ nguyên 100% logic các lệnh legacy cũ.
2. `tests/test_cli_legacy.py`: Regression suite kiểm tra `list-tabs`, `goto`, `eval`, `screenshot`.
3. Benchmark suite so sánh: đo latency và token usage giữa phương pháp cũ (raw HTML/screenshot) vs phương pháp mới (`observe`/`act`).

**Validation Gate (Gate 3)**:
- Chạy toàn bộ test suite: `python3 -m pytest tests/ -v`.
- Chạy CLI commands thủ công trên port tạm:
  - `./scripts/cdp_controller.py list-tabs`
  - `./scripts/cdp_controller.py goto https://example.com`
  - `./scripts/cdp_controller.py observe --json`
  - `./scripts/cdp_controller.py eval "document.title"`
  - `./scripts/cdp_controller.py screenshot --output .tmp/test.png`
- Assert zero regressions trên các lệnh cũ.

---

### Phase 4: Skill Packaging & Production Deployment
**Deliverables**:
1. Đồng bộ hóa codebase sang `~/.gemini/config/skills/browser-automation-cdp/`.
2. Cập nhật `SKILL.md` của skill với đầy đủ hướng dẫn sử dụng mới, ví dụ mẫu, quy tắc escalation, và safety models.
3. Review độc lập cuối cùng từ `omni_reviewer` và đóng dấu `STATUS: APPROVED`.

---

## 6. Success Metrics & Key Performance Indicators (KPIs)

| Chỉ số | Hiện trạng (Skill cũ) | Mục tiêu OmniBrowser v1.0 | Phương pháp đo |
| :--- | :--- | :--- | :--- |
| **Payload kích thước mỗi turn** | 100 KB – 500 KB (Raw HTML) / 2 MB (PNG 4K) | **< 15 KB** (Compact JSON Tree) | Byte count của `observe` output |
| **Token tiêu thụ trung bình/task** | ~15,000 – 30,000 tokens | **< 3,000 tokens** (Giảm >80%) | Đo lượng token nạp vào LLM prompt |
| **Độ trễ phản hồi (Controller Latency)** | 2.5s – 5.0s | **< 400ms (p50), < 900ms (p95)** | Timestamp đo trong `page_manager` |
| **Tính tương thích ngược (Legacy)** | 100% | **100% (Zero regressions)** | Chạy toàn bộ test suite cũ |
| **Tỷ lệ thao tác chuẩn xác lần đầu** | ~50% (Dễ miss vì DOM biến động) | **> 90%** (Nhờ native action & postconditions) | Đếm tỷ lệ retry trên fixture suites |

---

## 7. Rollback & Failure Recovery Plan

1. **Stale Ref Failure**: Nếu tự động phục hồi ref thất bại $\rightarrow$ Controller trả về mã lỗi `STALE_REF` và yêu cầu agent gọi lại `observe` để lấy snapshot mới.
2. **Iframe / Closed Shadow DOM Blocker**: Nếu không thể duyệt DOM $\rightarrow$ Tự động leo thang sang `inspect frame-tree` hoặc `inspectVisual` container.
3. **Regression Detected**: Nếu bất kỳ lệnh cũ nào (`goto`, `list-tabs`, `eval`) bị lỗi $\rightarrow$ Hủy bỏ patch ngay lập tức thông qua Git revert và cách ly logic mới vào `browser_core/`.
4. **Live Profile Protection**: Nếu bất kỳ test script nào cố kết nối vào port `17082` mà không có cờ `--live-profile-confirmed` $\rightarrow$ Script test lập tức abort với mã lỗi an toàn (Fail-closed).
