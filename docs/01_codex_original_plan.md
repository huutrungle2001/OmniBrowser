Kế hoạch này nâng cấp skill hiện tại từ controller Playwright/CDP tối giản thành Progressive Capability Architecture, nhưng vẫn giữ nguyên các lệnh cũ. Hiện trạng chỉ có [SKILL.md](/Users/huutrungle2001/.gemini/config/skills/browser-automation-cdp/SKILL.md), [cdp_controller.py](/Users/huutrungle2001/.gemini/config/skills/browser-automation-cdp/scripts/cdp_controller.py) và launcher; do đó có thể tách module sạch mà không phải phá kiến trúc cũ.

Nguyên tắc triển khai:

```text
Semantic projection = fast-path có thể thiếu
Raw browser code / CDP inspect = escape hatch luôn sẵn
Visual = escalation có mục tiêu, không phải default
Site Pack = accelerator lazy-loaded, không phải source of truth
```

## 1. File layout đích

```text
browser-automation-cdp/
├── SKILL.md                                      # Sửa: API mới, escalation policy, safety
├── requirements.txt                              # playwright, pillow, jsonschema, pytest
├── pytest.ini
├── scripts/
│   ├── cdp_controller.py                         # Sửa: CLI adapter, giữ lệnh legacy
│   ├── launch_chrome.sh                          # Giữ nguyên, chỉ bổ sung health diagnostics nếu cần
│   ├── browser_core/
│   │   ├── __init__.py
│   │   ├── connection.py                         # Connect CDP, tab/frame/session selection
│   │   ├── contracts.py                          # Dataclass + JSON schema request/response
│   │   ├── errors.py                             # Stable error taxonomy / exit codes
│   │   ├── policy.py                             # Read/write/side-effect/sensitive-data policy
│   │   ├── page_registry.py                      # page, frame, document epoch, revision lifecycle
│   │   ├── projector.py                          # observe() orchestration and compact projection
│   │   ├── action_engine.py                      # act(), native input and stale-ref recovery
│   │   ├── expectations.py                       # URL/DOM/toast/dialog postcondition waiters
│   │   ├── delta.py                              # changed/added/removed state delta
│   │   ├── inspector.py                          # DOM, AX tree, frame-tree inspect
│   │   ├── code_runner.py                        # bounded runBrowserCode()
│   │   ├── visual.py                             # scoped screenshot/crop/annotation
│   │   ├── telemetry.py                          # latency, payload, retry, escalation metrics
│   │   └── site_packs/
│   │       ├── __init__.py
│   │       ├── sdk.py                            # SitePack protocol and workflow contract
│   │       ├── registry.py                       # lazy loading and URL/route matching
│   │       ├── generic_forms/
│   │       │   ├── manifest.json
│   │       │   └── pack.py
│   │       └── warwick_portal/
│   │           ├── manifest.json
│   │           ├── pack.py
│   │           └── README.md
│   └── web/
│       ├── dom_agent.js                          # injected page-side interaction index
│       └── dom_agent.test.js                     # unit tests for browser-side logic
├── tests/
│   ├── conftest.py
│   ├── test_cli_legacy.py
│   ├── test_observe.py
│   ├── test_actions.py
│   ├── test_expectations.py
│   ├── test_inspect_and_code.py
│   ├── test_visual.py
│   ├── test_iframe_shadow.py
│   ├── test_site_packs.py
│   ├── test_security_policy.py
│   └── fixtures/
│       ├── standard_form.html
│       ├── spa_replacement.html
│       ├── iframe_parent.html
│       ├── iframe_child.html
│       ├── shadow_components.html
│       ├── canvas_ui.html
│       └── cookie_banner.html
└── docs/
    ├── contracts.md
    ├── site-packs.md
    ├── safety-model.md
    └── benchmark.md
```

`warwick_portal` chỉ là pack opt-in, không chứa credentials, không chạy live trong test, và luôn fallback về Generic Core khi locator/route không khớp.

## 2. Chuẩn contract chung

Mọi lệnh mới trả JSON versioned ra `stdout`; diagnostic ngắn ra `stderr`.

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

Error phải machine-readable:

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

Exit code:

- `0`: action/inspection thành công.
- `2`: input/CLI contract không hợp lệ.
- `3`: CDP/browser unavailable.
- `4`: target/ref không tìm thấy hoặc stale không recover được.
- `5`: policy/approval block.
- `6`: timeout/postcondition failed.
- `7`: internal/controller fault.

## 3. Module 1: Semantic Interaction Projector

### Cơ chế injection

`connection.py` mở Playwright connection tới port `17082`, rồi tạo CDP session phù hợp theo tab. `page_registry.py` chịu trách nhiệm:

1. Inject `web/dom_agent.js` vào document mới qua `Page.addScriptToEvaluateOnNewDocument`.
2. Bootstrap ngay các document/frame đang mở.
3. Gắn registry theo `page`, `frame`, `documentEpoch`, `revision`.
4. Invalidate handles khi navigation, detach frame, reload hoặc renderer crash.
5. Không ghi `data-*` vào DOM thật của website.

`dom_agent.js` giữ state page-side:

```text
WeakMap<Element, nodeToken>
documentEpoch
revision
MutationObserver
interactive candidate cache
fingerprint cache
```

`MutationObserver` chỉ đánh dấu vùng bị dirty và tăng `revision`; không serialize lại toàn bộ DOM tại mỗi mutation. Projection chỉ được tính lúc agent gọi `observe`.

### Candidate selection

Giữ các nhóm sau:

- Native `button`, `a[href]`, `input`, `textarea`, `select`, `option`.
- `[role]` có action semantics: button, checkbox, textbox, combobox, tab, menuitem, switch, slider, link.
- `[contenteditable]`, element có valid `tabindex`, dialog, form, active toast/error.
- Element trong open shadow root.
- Element trong frame mà controller đã attach được.

Loại hoặc hạ ưu tiên:

- `script`, `style`, SVG decoration, analytics, ad, hidden template.
- `display:none`, `visibility:hidden`, `aria-hidden`, `inert`, `hidden`.
- Duplicated responsive subtree và boilerplate ngoài scope.
- Disabled controls, trừ khi chúng giải thích điều kiện chặn workflow.

### Ref và fingerprint

```text
ref: f0.d9.n186
short display id: E12
```

- `f0`: frame token.
- `d9`: document epoch.
- `n186`: node token trong document.
- `E12`: số hiển thị trong observation hiện tại; không được dùng làm identity action.

Fingerprint nội bộ gồm role, accessible name, label-control relation, stable allowlisted attributes, form owner, landmark path, host trail của shadow DOM và geometry phụ trợ.

`observe()` luôn khai báo bản chất lossy:

```json
{
  "coverage": {
    "is_complete": false,
    "included": ["visible-interactive", "active-dialog", "validation-messages"],
    "omitted": ["offscreen-content", "canvas", "unexposed-closed-shadow-root"]
  }
}
```

### API

```bash
cdp_controller.py observe \
  --scope main \
  --query "enrolment" \
  --max-elements 80 \
  --max-text-chars 4000 \
  --format json
```

`--since-revision r42` trả delta projection thay vì snapshot mới.

## 4. Module 2: Action và Postcondition Engine

### Action contract

```json
{
  "op": "fill",
  "target": { "ref": "f0.d9.n186" },
  "value": "12345678",
  "expect": {
    "element": {
      "ref": "f0.d9.n271",
      "enabled": true
    }
  }
}
```

Các operation MVP:

```text
click, fill, select, check, press, upload, scrollIntoView
```

### Native interaction

`action_engine.py` thực hiện theo thứ tự:

1. Resolve `ref` trong frame/document đúng.
2. Kiểm tra attached, visible, enabled, viewport geometry và occlusion.
3. Scroll target vào viewport.
4. Với click/press/fill, ưu tiên CDP native input events.
5. Với native select, dùng strategy phù hợp: keyboard/mouse trước; DOM assignment + `input`/`change` chỉ là fallback được ghi audit.
6. Cài postcondition watcher trước action để không bỏ lỡ mutation tức thì.
7. Trả state delta sau khi expect đạt hoặc timeout.

### Postcondition

`expectations.py` hỗ trợ:

```json
{
  "url_matches": "/confirmation",
  "element": { "ref": "f0.d9.n271", "enabled": true },
  "text_present": "Saved successfully",
  "dialog": "closed",
  "timeout_ms": 8000
}
```

Không dùng `sleep` mặc định. Watcher dùng navigation event, mutation revision, URL transition, dialog/toast state và element condition. `networkidle` chỉ là optional hint.

### State Delta

`delta.py` so sánh interaction state trước/sau trong scope liên quan:

```json
{
  "ok": true,
  "revision": "r43",
  "changed": [
    { "ref": "f0.d9.n186", "value": "12345678" },
    { "ref": "f0.d9.n271", "enabled": true }
  ],
  "added": [],
  "removed": []
}
```

Không trả lại whole-page observation sau mỗi action.

### Stale-ref recovery

Thứ tự recovery:

1. Resolve ref trực tiếp trong document hiện tại.
2. Re-query fingerprint trong cùng frame/document.
3. Match exact role + accessible name + form/landmark scope.
4. Match stable allowlisted locator.
5. Nếu nhiều candidate hoặc document epoch đã đổi: trả `STALE_REF`, không tự click mơ hồ.

Không auto-recover cross-navigation cho action có side effect.

## 5. Module 3: Escape Hatches

### `inspect`

```bash
cdp_controller.py inspect dom --ref f0.d9.n186 --depth 3
cdp_controller.py inspect ax --ref f0.d9.n186
cdp_controller.py inspect frame-tree
```

Các mode:

- `dom`: subtree sạch, bounded depth/text/node count.
- `ax`: partial accessibility tree cho root/ref đang xét, không dump full AX tree mặc định.
- `frame-tree`: URL, origin, frame token, attach status.
- `selector`: raw targeted query, chỉ dùng khi agent có lý do cụ thể.

### `run-code`

```bash
cdp_controller.py run-code \
  --script-file /path/to/task-script.js \
  --frame f0 \
  --return-schema /path/to/result.schema.json \
  --mode read
```

Contract:

- Script chạy trong browser frame context, bọc async IIFE.
- Chỉ trả JSON serializable và validate bằng JSON Schema.
- Default timeout 2 giây; mode write giới hạn 10 giây.
- Default output cap 32 KB; lớn hơn phải trả structured truncation.
- Không tự dump raw DOM, cookies, storage hoặc network body.
- `read` là default; `write` được audit và chịu policy side-effect.
- Persistent helper/notebook là phase sau MVP; mỗi helper phải gắn document epoch và tự invalid khi navigation.

Raw code là escape hatch cho agent, không phải một API không giới hạn toàn bộ Chrome administration surface.

## 6. Module 4: Targeted Visual Escalation

```bash
cdp_controller.py visual \
  --scope-ref f0.d9.n310 \
  --annotate-candidates \
  --output /tmp/form-region.png
```

Cơ chế:

1. `scope=viewport` hoặc `scope-ref`.
2. Lấy bounding rect, clip screenshot đúng container/viewport.
3. Giới hạn candidate overlay tối đa 20 controls gần nhất.
4. Dùng Pillow để tạo annotated copy; luôn giữ original screenshot không bị mark.
5. Trả screenshot path, dimensions, scale và mapping annotation -> `ref`.
6. Không screenshot full page mặc định.

Với CAPTCHA:

- Có thể capture evidence để nhận diện và báo trạng thái.
- Không giải, không bypass, không tự click challenge.
- Trả `REQUIRES_HUMAN_VERIFICATION`.

## 7. Module 5: Lazy-loaded Site Packs

SDK:

```python
class SitePack(Protocol):
    id: str
    version: str

    def matches(self, url: str) -> bool: ...
    def capabilities(self) -> dict[str, Workflow]: ...
    def health_check(self, core: BrowserCore) -> PackHealth: ...
    def fallback(self, reason: str) -> FallbackDecision: ...
```

`registry.py` chỉ nạp manifest/pack có domain và route phù hợp. Agent chỉ nhận capability hiện hành, không nhận toàn bộ hướng dẫn của mọi website.

Ví dụ:

```text
generic_forms:
  complete_form(fields)
  collect_validation_errors()

warwick_portal:
  inspect_enrolment_status()
  begin_enrolment()
  collect_required_fields()
```

Ràng buộc:

- Pack không chứa credentials hoặc data người dùng.
- Pack không bypass confirmation policy.
- Pack phải fail closed khi route/UI drift.
- Pack trả fallback generic `observe`, `inspect` hoặc `run-code`.
- Không test live Warwick portal trong CI; chỉ test fixture mô phỏng route/component contract.

## 8. Module 6: CLI và backward compatibility

Các lệnh cũ giữ nguyên hành vi:

```bash
list-tabs
goto URL
eval EXPRESSION
screenshot --output FILE
```

`eval` được đánh dấu legacy trong `SKILL.md`, nhưng vẫn hoạt động để không phá script có sẵn.

Lệnh mới:

```bash
observe
act --file action.json
extract --schema schema.json --scope main
inspect dom|ax|frame-tree
run-code --script-file task.js --return-schema result.schema.json
visual --scope-ref REF --output FILE
site-pack list|status|run
```

Compatibility rules:

- Existing text output là default cho legacy commands.
- `--format json` bổ sung cho legacy commands.
- New commands mặc định JSON.
- `--match URL_SUBSTRING` tiếp tục chọn tab như hiện nay.
- `--tab-id` mới cho selection chính xác hơn.
- `screenshot` không đổi flag; visual mới yêu cầu output path rõ ràng.
- CLI docs trong `SKILL.md` phải có bảng mapping legacy/new command.

## 9. Kiểm thử

Toàn bộ test chạy với Chrome profile tạm và CDP port ngẫu nhiên; tuyệt đối không dùng profile authenticated thật.

| Fixture | Kiểm chứng |
|---|---|
| Standard form | Observe label/control; fill; button enabled; delta đúng |
| Dynamic SPA | Node bị React-style replacement; stale ref recover theo fingerprint |
| Iframe khác origin | Frame token đúng; observe/action trong child frame |
| Open shadow root | Projector thấy control qua composed tree và click được |
| Closed shadow root | AX/inspect attempt; nếu không expose thì coverage báo limitation, visual escalation |
| Cookie banner | Projection có thể không thấy; `run-code`/`inspect` vẫn tìm được |
| Canvas UI | Visual crop đúng region; không giả định DOM control |
| CAPTCHA fixture | Trả human verification; không action bypass |
| Navigation | Epoch thay đổi; ref cũ không bị click nhầm |
| Site pack fixture | URL matcher, health check, fallback on UI drift |
| Legacy CLI | `list-tabs`, `goto`, `eval`, `screenshot` chạy như hiện tại |

Test bổ sung:

- JSON schema validation.
- Output size cap và truncation.
- No raw cookie/storage leakage.
- Policy chặn write code và destructive action khi không có confirmation.
- Race condition: watcher bắt mutation xảy ra ngay sau click.
- Regression test khi browser/frame detach.
- `git diff --check` và static/type checks.

## 10. SLO và acceptance criteria

Đo controller overhead tách riêng website/server/LLM latency.

| Chỉ số | Tiêu chuẩn nghiệm thu ban đầu |
|---|---|
| Warm `observe()` local fixture p50 / p95 | `<= 250 ms` / `<= 750 ms` |
| `act()` controller overhead sau page-ready p50 / p95 | `<= 350 ms` / `<= 1 s` |
| Observation payload | `<= 12 KB` mặc định |
| Full screenshot default | `0%` workflow chuẩn |
| Payload giảm so với raw HTML baseline | `>= 80%` trên fixture corpus |
| One observation + one action | `>= 95%` form chuẩn, `>= 85%` SPA fixture |
| First-action success | `>= 95%` form/iframe/open-shadow corpus |
| Stale-ref false click | `0` |
| CAPTCHA bypass | `0` |
| Site-pack UI drift | fallback generic, không retry selector mù quá 1 lần |

Các threshold phải được lưu cùng hardware, Chrome version, fixture version và baseline command trong `docs/benchmark.md`.

## 11. Phased implementation

### Phase 0 — Baseline và contract freeze

- [ ] Chạy benchmark của controller cũ: raw eval payload, screenshot latency, click/fill workflow.
- [ ] Chụp current CLI behavior bằng regression tests.
- [ ] Chốt JSON contract v1, error taxonomy, policy tiers và SLO.
- [ ] Tạo fixture local và isolated Chrome test harness.
- [ ] Không thay đổi hành vi lệnh legacy.

Verify: benchmark baseline được commit; legacy smoke tests pass.

### Phase 1 — Foundation và page lifecycle

- [ ] Tạo `browser_core` package, `connection.py`, `contracts.py`, `errors.py`.
- [ ] Tách CDP/tab/page selection ra khỏi CLI.
- [ ] Tạo `page_registry.py`, document epoch, frame token, revision lifecycle.
- [ ] Implement CDP injection và bootstrap hiện hữu.
- [ ] Thêm telemetry scaffold và JSON output writer.

Verify: controller connect/reconnect đúng; navigation invalidates ref state; legacy tests vẫn pass.

### Phase 2 — Observe fast-path

- [ ] Implement `dom_agent.js`, candidate scanner và cleanup rules.
- [ ] Implement ref/fingerprint/E-number semantics.
- [ ] Implement scoped projection và coverage metadata.
- [ ] Implement budget, truncation và `sinceRevision`.
- [ ] Viết form, SPA, iframe, open/closed shadow tests.

Verify: không full DOM snapshot mặc định; payload/SLO observe đạt trên corpus.

### Phase 3 — Act, expect, delta

- [ ] Implement resolver và visibility/occlusion check.
- [ ] Implement click/fill/select/check/press/scroll.
- [ ] Cài watcher trước action, rồi action native CDP.
- [ ] Implement postcondition types và timeout contract.
- [ ] Implement state delta và stale-ref recovery.
- [ ] Thêm navigation/race-condition/action safety tests.

Verify: one-observe/one-act completion đạt SLO; không stale false-click.

### Phase 4 — Inspect, code và visual

- [ ] Implement bounded DOM/AX/frame inspect.
- [ ] Implement `run-code` với schema/time/output/policy boundary.
- [ ] Implement code-result audit và redaction.
- [ ] Implement crop screenshot, candidate mapping và annotation.
- [ ] Implement CAPTCHA human-required behavior.

Verify: cookie-banner/iframe blind spot có escape hatch; canvas visual test pass; no sensitive leak.

### Phase 5 — Site Packs và CLI rollout

- [ ] Implement SDK, registry, manifest validation và fallback contract.
- [ ] Add `generic_forms` pack.
- [ ] Add opt-in Warwick sample pack trên fixture.
- [ ] Add CLI subcommands và `--format json`.
- [ ] Cập nhật `SKILL.md`, docs contracts/site-packs/safety.

Verify: only matching pack loads; pack drift falls back; all legacy invocations unchanged.

### Phase 6 — Hardening và acceptance

- [ ] Chạy full unit/integration suite bằng isolated profile.
- [ ] Chạy benchmark corpus lặp lại nhiều lần, báo p50/p95.
- [ ] So sánh baseline/candidate về latency, bytes, screenshot rate và action success.
- [ ] Security review: upload/download, secrets, write code, destructive action.
- [ ] Chạy `git diff --check`, lint, test và manual smoke trên non-sensitive tab.
- [ ] Ghi release notes, migration guide và rollback plan.

Verify: toàn bộ SLO đạt; legacy compatibility pass; policy tests pass; worktree sạch trước phát hành.