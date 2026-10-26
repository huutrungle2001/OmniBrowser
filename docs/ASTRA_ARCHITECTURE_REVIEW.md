Đã đọc trọn vẹn prompt. Kết luận ngắn: hướng chuyển sang raw CDP là đúng, nhưng nên xây một “CDP session router” nhỏ có lifecycle rõ ràng, không cố tái tạo Playwright.

## 1. Raw CDP WebSocket

### Khuyến nghị

Dùng:

- `ws` cho WebSocket.
- `fetch()` để đọc `/json/version` và `/json/list`.
- Một lớp RPC khoảng 120–180 dòng:
  - `nextId`
  - `pending: Map<number, Promise>`
  - timeout từng request
  - phân biệt response có `id` và event có `method`
  - reject toàn bộ request khi socket đóng
  - ping/close/error handling
  - route `sessionId`

Không cần `chrome-remote-interface` trừ khi muốn dùng generated protocol definitions. `cri` không giải quyết các vấn đề quan trọng nhất: context invalidation, target lifecycle, stale refs và action semantics. Với v3, `ws` + wrapper nhỏ dễ kiểm soát hơn.

### Các lỗi raw CDP thường gặp

1. Đăng ký `pending` sau khi `send()`  
   Có thể response đến trước khi map được tạo. Phải tạo promise/map entry trước.

2. Không phân biệt browser event và session event  
   Message có dạng:

   ```json
   {
     "id": 12,
     "sessionId": "...",
     "result": {}
   }
   ```

   hoặc:

   ```json
   {
     "method": "Runtime.executionContextCreated",
     "sessionId": "...",
     "params": {}
   }
   ```

3. Dùng lại `ExecutionContextId` sau navigation  
   Không bao giờ cache context id lâu dài.

4. Không xử lý tab đóng giữa request  
   Cần chuyển thành lỗi có mã ổn định, ví dụ `TARGET_CLOSED`.

5. Gửi nhiều mutation song song  
   `click`, `fill`, `goto`, `select` cần queue theo target. Nếu không, các lệnh có thể chạy sai thứ tự.

6. Không giới hạn output  
   `Runtime.evaluate` có thể trả về DOM lớn, Base64 hoặc object vòng. Cần giới hạn expression, thời gian và số ký tự kết quả.

7. Không xử lý JavaScript dialog  
   `alert()` hoặc `beforeunload` có thể làm thao tác đứng. Phải lắng nghe `Page.javascriptDialogOpening` và có policy rõ ràng: auto-dismiss hoặc trả lỗi `DIALOG_OPEN`.

### Execution context

Nên theo dõi:

- `Runtime.executionContextCreated`
- `Runtime.executionContextDestroyed`
- `Runtime.executionContextsCleared`
- `Page.frameNavigated`
- `Page.frameDetached`

Chọn context mặc định theo:

```text
auxData.isDefault === true
auxData.frameId === requestedFrameId
```

Khi navigation xảy ra:

```text
invalidate frame context
đợi executionContextCreated mới
evaluate vào context mới
```

Nếu nhận lỗi kiểu `Cannot find context with specified id`, retry đúng một lần sau khi refresh context. Không retry vô hạn.

`Page.frameNavigated` là tín hiệu invalidate; `Runtime.executionContextCreated` là tín hiệu context mới đã dùng được. Không nên chỉ đợi `loadEventFired`, vì SPA có thể không reload đầy đủ.

### OOPIF

Có hai lựa chọn:

#### Đơn giản nhất cho v3.0

Không hỗ trợ evaluate/action vào cross-origin iframe; chỉ hỗ trợ frame cùng target. Trả lỗi rõ ràng:

```text
FRAME_UNSUPPORTED: cross-origin iframe requires attached target
```

Đây là lựa chọn hợp lý nếu các workflow hiện tại chủ yếu thao tác DOM chính.

#### Nếu muốn hỗ trợ ngay

Dùng browser-level WebSocket:

1. Kết nối `/json/version`.
2. `Target.setDiscoverTargets`.
3. `Target.setAutoAttach` với:

   ```json
   {
     "autoAttach": true,
     "waitForDebuggerOnStart": false,
     "flatten": true
   }
   ```

4. Theo dõi:
   - `Target.attachedToTarget`
   - `Target.detachedFromTarget`
   - `Target.targetCreated`
   - `Target.targetDestroyed`

5. Mỗi session cần enable các domain cần thiết:

   ```text
   Runtime.enable
   Page.enable
   DOM.enable
   ```

`flatten: true` nên dùng nếu đã chọn browser-level connection. Nó làm routing nhất quán: command gửi kèm `sessionId`, không cần `Target.sendMessageToTarget`.

Tuy nhiên, không nên xây một hệ thống frame abstraction hoàn chỉnh. Chỉ cần:

```text
frameId -> sessionId -> executionContextId
```

và một API evaluate/action tối thiểu.

### Một số CDP policy quan trọng

- `Runtime.evaluate`:
  - `awaitPromise: true`
  - `returnByValue: true`
  - `userGesture: true`
  - giới hạn output
- Không giữ `RemoteObjectId` lâu hơn một operation.
- Không dùng `networkIdle` làm điều kiện mặc định cho `goto`; dùng `commit` hoặc `load`.
- Với native `<select>`, không click popup OS. Dùng JS setter + `input`/`change` event.
- Với hidden radio/checkbox, ưu tiên `label[for]`, sau đó mới fallback tới `.click()` bằng JavaScript.
- `evaluate` là quyền gần như toàn quyền trên session Chrome; chỉ dùng cho trusted local agent và giới hạn kích thước/thời gian.

## 2. Project structure

Cấu trúc nhỏ, đủ dùng:

```text
omni_v3/
├── package.json
├── tsconfig.json
├── tsup.config.ts
├── src/
│   ├── cdp.ts         # WebSocket RPC, events, timeout, session routing
│   ├── browser.ts     # target discovery, attach, lifecycle, frame map
│   ├── page.ts        # goto, evaluate, act, batch, scroll
│   ├── observe.ts     # scoped DOM extraction, controls, refs, truncation
│   ├── artifacts.ts   # screenshot, download
│   ├── cli.ts         # CLI parser/output
│   └── mcp.ts         # stdio MCP adapter
└── test/
```

Mục tiêu thực tế:

```text
cdp.ts        160–200 LOC
browser.ts     90–120 LOC
page.ts       160–200 LOC
observe.ts    100–130 LOC
artifacts.ts   60–80 LOC
cli.ts         80–100 LOC
mcp.ts        100–130 LOC
```

Khoảng 750–850 LOC cho phần core là khả thi nếu không thêm framework nội bộ.

Chỉ nên có hai abstraction chính:

- `CdpConnection`
- `TargetSession`

Không cần dependency injection, plugin system, locator class hierarchy hoặc protocol code generation.

### Build

Khuyến nghị:

```json
{
  "type": "module",
  "engines": {
    "node": ">=22"
  },
  "bin": {
    "omni": "dist/cli.js",
    "omni-mcp": "dist/mcp.js"
  }
}
```

Dùng `tsup` hoặc `esbuild` để build hai entrypoint:

```text
dist/cli.js
dist/mcp.js
```

Node 22 native TypeScript runner phù hợp cho development, nhưng không nên là runtime phân phối chính vì:

- shebang kém portable
- chỉ type-strip, không transform đầy đủ
- khác biệt giữa các patch version Node
- khó kiểm soát source map và packaging

MCP server nên là process dài sống lâu. CLI mỗi lệnh tự kết nối vẫn chỉ mất khoảng vài chục mili-giây, chấp nhận được.

## 3. API contract

### Quy ước chung

- Luôn dùng `tab_id` ổn định từ Chrome `targetId`, không dùng index/title.
- Ref chỉ có giá trị trong `snapshot_id` của lần `observe` gần nhất.
- Mỗi request có `timeout_ms`, nhưng server áp dụng hard cap.
- Mặc định:
  - timeout: `10_000`
  - batch tối đa: `32`
  - output tối đa: `50_000` ký tự
- Lỗi có cấu trúc:

```json
{
  "ok": false,
  "error": {
    "code": "STALE_REF",
    "message": "Reference belongs to an older observation",
    "retryable": true
  }
}
```

### `list_tabs`

```json
{}
```

Trả:

```json
{
  "tabs": [
    {
      "tab_id": "page-ABC",
      "title": "Example",
      "url": "https://example.com",
      "type": "page",
      "attached": true
    }
  ]
}
```

### `goto`

```json
{
  "tab_id": "page-ABC",
  "url": "https://example.com",
  "wait": "commit",
  "timeout_ms": 10000
}
```

`wait`: `none | commit | load`. Không nên có `networkidle`.

### `observe`

```json
{
  "tab_id": "page-ABC",
  "selector": "main",
  "frame_id": "FRAME_ID",
  "max_chars": 12000,
  "include": ["text", "controls", "links"]
}
```

Trả:

```json
{
  "snapshot_id": "s_42",
  "url": "https://example.com",
  "title": "Example",
  "text": "...",
  "controls": [
    {
      "ref": "r7",
      "tag": "input",
      "type": "radio",
      "label": "Option A",
      "name": "answer",
      "value": "a",
      "checked": false,
      "disabled": false
    }
  ],
  "truncated": false
}
```

Nên trích xuất text bằng `TreeWalker` trong browser rồi lọc:

- `display:none`
- `visibility:hidden`
- `aria-hidden=true`
- thuộc tính Base64 hoặc chuỗi dài hơn 100 ký tự
- các vùng ngoài selector

Không cần HTML-to-Markdown engine đầy đủ. Plain text có heading/list heuristic là đủ.

### `act`

Không cần tool `smart_act` riêng. Gộp smart behavior vào `act` với `strategy: "smart"` mặc định.

```json
{
  "tab_id": "page-ABC",
  "snapshot_id": "s_42",
  "ref": "r7",
  "action": "click",
  "strategy": "smart",
  "timeout_ms": 5000
}
```

Các action nên giới hạn:

```text
click
fill
check
uncheck
select
press
```

Không cho phép đồng thời `ref` và `selector`; một request chỉ có một target.

`select` phải thao tác bằng JS setter và phát event, tránh popup Cocoa/OS.

### `act_batch`

```json
{
  "tab_id": "page-ABC",
  "snapshot_id": "s_42",
  "actions": [
    { "ref": "r1", "action": "fill", "value": "Alice" },
    { "ref": "r7", "action": "click" },
    { "selector": "button.next", "action": "click" }
  ],
  "stop_on_error": true,
  "timeout_ms": 20000
}
```

Không gọi đây là transaction/atomic operation. Browser action không rollback được. Chỉ bảo đảm thứ tự thực thi.

### `evaluate`

```json
{
  "tab_id": "page-ABC",
  "frame_id": "FRAME_ID",
  "expression": "(() => ({url: location.href, count: document.forms.length}))()",
  "await_promise": true,
  "timeout_ms": 10000,
  "max_output_chars": 20000
}
```

Giới hạn nên có:

- expression tối đa 64 KB
- output tối đa 50 KB
- timeout cứng
- trả lỗi rõ cho circular object hoặc unserializable value

### `scroll`

```json
{
  "tab_id": "page-ABC",
  "ref": "r12",
  "direction": "down",
  "amount_px": 700,
  "behavior": "instant"
}
```

Nếu không có `ref`, thao tác trên page viewport. Với container con, dùng element được resolve từ ref/selector và gọi `scrollBy`.

### `screenshot`

```json
{
  "tab_id": "page-ABC",
  "selector": "main",
  "format": "png",
  "full_page": false,
  "quality": 90
}
```

Không trả Base64 khổng lồ trong text MCP. Adapter nên:

- CLI: ghi file và trả path.
- MCP: trả image attachment hoặc file path.
- JSON core: chỉ trả metadata và optional binary handle.

### `download`

```json
{
  "tab_id": "page-ABC",
  "ref": "r20",
  "path": "/tmp/omni-downloads",
  "timeout_ms": 30000
}
```

Dùng browser-level CDP để theo dõi:

- `Browser.downloadWillBegin`
- `Browser.downloadProgress`

Trả:

```json
{
  "state": "completed",
  "path": "/tmp/omni-downloads/report.pdf",
  "suggested_filename": "report.pdf",
  "bytes": 183920
}
```

Sanitize filename và không cho phép path traversal.

## 4. Những thứ không nên xây

Tuyệt đối tránh các phần sau trong v3:

- auto-wait/actionability engine như Playwright
- locator engine riêng với hàng chục chiến lược fallback
- browser launcher hoặc profile manager
- multi-browser abstraction
- stealth/anti-detection
- network interception, HAR, tracing, video
- full accessibility tree cache
- DOM snapshot database
- plugin system
- retry “thông minh” không minh bạch
- transaction/rollback cho browser actions
- arbitrary shell execution
- tự động đăng nhập hoặc quản lý credential
- hỗ trợ worker/service-worker như một mục tiêu riêng
- Markdown converter đầy đủ

Đặc biệt, đừng biến `observe` thành bản sao của DOM inspector. Nó chỉ cần trả:

```text
text ngắn gọn
controls có ref
links quan trọng
trạng thái truncated
```

## Kiến trúc nên chốt

```text
CLI/MCP
   ↓
Shared operation router
   ↓
TargetSession
   ↓
CdpConnection
   ↓
Chrome DevTools Protocol
```

MVP hợp lý:

1. browser-level WebSocket + flat sessions
2. target lifecycle
3. context invalidation
4. scoped observe
5. `act` với smart label/select behavior
6. `act_batch`
7. evaluate
8. screenshot/download
9. OOPIF chỉ ở mức routing tối thiểu

Ba bài test bắt buộc trước khi chốt:

- navigation giữa hai lần `evaluate` không dùng context chết
- hidden radio và native select hoạt động đúng
- output trang có `__VIEWSTATE` lớn vẫn bị cắt dưới ngân sách cố định

Đây là điểm cân bằng tốt nhất giữa tốc độ raw CDP và khả năng sử dụng thực chiến, mà không tái tạo một Playwright mới.
