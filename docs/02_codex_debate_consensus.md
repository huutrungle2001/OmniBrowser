Reviewer đúng ở cả hai điểm chính. Bản kế hoạch trước có dấu hiệu thiết kế cho “hệ sinh thái tương lai” trước khi chứng minh Core Engine giải quyết được pain hiện tại. Tôi nhượng bộ và đề xuất thu hẹp v1 đáng kể.

## 1. Over-modularization: phản biện thẳng

Bạn đúng rằng 14–15 file là quá mức cho skill CLI local hiện có chỉ khoảng 100 dòng.

Điểm cần chỉnh trong lập luận của bạn: import overhead Python không phải vấn đề đáng kể; chi phí thật là:

- Người bảo trì phải nhảy giữa nhiều file để hiểu một interaction.
- Các abstraction chưa ổn định bị “đóng băng” sớm qua API giữa module.
- Circular import và glue code dễ xuất hiện khi `projector`, `delta`, `expectation`, `ref recovery` cùng dùng chung page state.
- Agent sau này patch code phải nạp nhiều context file hơn, trái mục tiêu giảm complexity.

Bạn cũng đúng về high cohesion: `observe -> act -> wait -> delta` là một transaction logic. Chia `expectations.py` và `delta.py` riêng trong v1 chỉ tạo ranh giới giả.

Tôi không bảo vệ 14 file. Đó là clean architecture bị áp dụng quá sớm.

### Cấu trúc v1 chốt lại

```text
browser-automation-cdp/
├── SKILL.md
├── requirements.txt
├── scripts/
│   ├── cdp_controller.py       # CLI, legacy compatibility, JSON I/O
│   ├── dom_agent.js            # Page-side observer/index; phải tách vì chạy trong browser
│   └── browser_core/
│       ├── contracts.py        # Request/response, errors, policy constants
│       ├── page_manager.py     # CDP connection, tab/frame, inject JS, epoch/revision
│       ├── engine.py           # observe, act, waits, delta, stale-ref recovery
│       └── primitives.py       # inspect, run-code, visual crop
└── tests/
```

Bốn module Python của bạn là ranh giới hợp lý cho v1:

- `contracts.py` giữ các contract ổn định và thuần dữ liệu.
- `page_manager.py` sở hữu browser/page/frame lifecycle.
- `engine.py` sở hữu một interaction transaction hoàn chỉnh.
- `primitives.py` sở hữu các escape hatch.

Một lưu ý thực dụng: không cần ép `engine.py` phải mãi nhỏ. Nếu sau đo đạc nó thành 1,200–1,500 dòng với các vùng logic đã rõ và test độc lập, lúc đó mới tách theo pain thực tế; ví dụ khi visual hay raw-code policy phát triển đủ lớn. File count phải là hệ quả của complexity đã tồn tại, không phải dự đoán complexity.

## 2. Site Packs: reviewer đúng gần như hoàn toàn

Tôi đồng ý cắt hẳn Site Pack SDK, registry, manifest và Warwick pack khỏi v1.0.

Lý do kỹ thuật rất rõ:

- `ref`, `page revision`, stale recovery, expectation và output contract đều chưa battle-tested.
- Site Pack sẽ khóa chặt các giả định chưa được kiểm chứng vào API/plugin format.
- Một pack Warwick viết ngay bây giờ chưa chứng minh được generic engine tốt; nó có thể chỉ che các lỗi core bằng locator bespoke.
- Đây là lệch ưu tiên khỏi mục tiêu đang đau nhất: latency, token cost và khả năng thao tác website lạ.

Điểm duy nhất tôi giữ lại là không được thiết kế Core theo cách ngăn site specialization về sau. Nhưng đó không yêu cầu SDK/plugin framework trong v1. Chỉ cần:

- `observe`, `act`, `inspect`, `run-code` có JSON contract versioned;
- action/ref không gắn cứng với một site;
- audit và telemetry có `domain`, `route`, `workflow_hint`;
- raw-code helper có thể lưu lại như artifact/replay script.

Nói ngắn: **design for extension, do not implement the extension.**

## 3. Consensus: scope v1 thực dụng

```text
v1.0: Generic Core + escape hatches + telemetry
v1.1: Validate trên workflow thực, tinh chỉnh contracts
v2.0: Chỉ xây Site Packs nếu dữ liệu chứng minh lợi ích
```

### v1.0 — Ship Core trước

Phạm vi bắt buộc:

1. Giữ tương thích `list-tabs`, `goto`, `eval`, `screenshot`.
2. `observe()` với compact semantic projection, `ref`, scope, budget và coverage metadata.
3. `act()` cho `click`, `fill`, `select`, `check`, `press`, kèm native interaction và postcondition.
4. State delta và stale-ref recovery tối thiểu.
5. `inspect()` cho targeted DOM, AX tree và frame tree.
6. `run-code()` bounded: timeout, output JSON/schema, size cap.
7. `visual()` chỉ capture viewport/container crop; không SoM default.
8. Fixture tests + benchmark baseline/candidate.
9. Telemetry: latency, payload bytes, retries, stale refs, raw/visual escalation.

Không làm ở v1:

- Site Pack SDK, registry, manifest.
- Generic workflow DSL.
- Persistent notebook framework.
- Automatic recipe mining.
- Full telemetry platform/database.
- Complex policy engine; chỉ giữ policy tối thiểu cho destructive actions, CAPTCHA và secret leakage.

### v1.1 — Battle-test, không “xây thêm”

Chạy Core trên ít nhất ba workflow thật, khác loại:

| Loại workflow | Ví dụ cần kiểm chứng |
|---|---|
| Form semantic chuẩn | Điền, validation, submit review |
| SPA/custom component | Re-render, stale ref, dynamic dropdown |
| Structural edge case | iframe, shadow DOM, banner/overlay hoặc visual-only area |

Mỗi workflow phải ghi:

- số tool call và LLM turn;
- p50/p95 controller latency;
- bytes/tokens observation;
- tỷ lệ action lần đầu thành công;
- số lần cần raw code;
- số lần cần visual;
- nguyên nhân failed/retry.

Đây là giai đoạn để sửa contract. Nếu `observe()` không đủ, bổ sung escape hatch; không tăng heuristic bừa bãi.

### Điều kiện mở v2.0 Site Packs

Chỉ bắt đầu khi tất cả điều kiện sau cùng đúng:

1. Một domain/workflow được chạy thành công lặp lại ít nhất 5–10 lần.
2. Generic Core đã ổn định qua ít nhất 3 loại site/workflow khác nhau.
3. Có số liệu cho thấy workflow lặp lại vẫn tốn nhiều turn/latency dù Core đã tốt.
4. Contract `observe`/`act` không có thay đổi breaking trong một chu kỳ validation đã định.
5. Site Pack dự kiến giảm được ít nhất một chỉ số đo được: turn, p95 latency, hoặc first-action failure.

Pack đầu tiên không cần “plugin system”. Nó có thể chỉ là một module Python nội bộ:

```text
scripts/recipes/warwick_enrolment.py
```

Chỉ khi có pack thứ hai hoặc thứ ba với pattern chung thật sự, mới extract `SitePack` protocol/registry/manifest. Đây là trình tự đúng: concrete implementation trước, abstraction sau.

## 4. Kế hoạch triển khai sửa đổi

### Phase 0 — Freeze baseline

- Chụp regression behavior của 4 lệnh legacy.
- Đo baseline raw HTML/eval/screenshot trên fixture.
- Viết JSON contracts v1 cho core tools.
- Dựng fixture local và Chrome profile/port tạm.

### Phase 1 — Thin Core

- Tách `page_manager.py` từ controller cũ.
- Inject `dom_agent.js`.
- Implement `observe()` scope/budget/ref/coverage.
- Không cache phức tạp trước; chỉ cache/revision khi benchmark chứng minh cần.

### Phase 2 — Interaction loop

- Implement `act()`, expect và state delta trong một `engine.py`.
- Implement stale recovery theo fingerprint đơn giản.
- Test form, SPA re-render, iframe/open shadow root.
- Không tự động click khi recovery ambiguous.

### Phase 3 — Escape hatch

- Add bounded `inspect()` và `run-code()`.
- Add targeted `visual()` crop.
- Test cookie banner, closed shadow limitation, canvas/CAPTCHA fixture.
- Đo visual escalation rate; không làm overlay/SoM mặc định.

### Phase 4 — Live validation và hardening

- Chạy workflow thật có review con người trước external side effect.
- So benchmark với baseline.
- Sửa contract theo failure evidence.
- Ship v1.0 khi SLO đạt và legacy commands không regress.

## 5. Quyết định cuối

Tôi chấp nhận đề xuất 4-module Core của reviewer và loại Site Packs khỏi v1.0.

Bản chất “future-proof” đúng không phải là viết trước registry/plugin SDK. Nó là giữ Core API nhỏ, versioned, observable, có raw escape hatch, và chỉ trừu tượng hóa khi có ít nhất vài implementation thật buộc ta phải làm vậy.