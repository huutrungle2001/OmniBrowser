# Báo Cáo Nghiên Cứu Kiến Trúc: Giải Quyết Tranh Chấp Tài Nguyên Trình Duyệt Đa Tác Tử (Browser Concurrency & Resource Contention)

**Người thực hiện:** Browser Architecture Specialist (`browser-arch`)  
**Đối tượng tham vấn:** ChatGPT Web (GPT-5.6 Sol Thinking High - Session `browser_concurrency_arch`)  
**Ngày hoàn thành:** 2026-09-19  
**Mã hồ sơ:** `.agents/communication/consultations/browser_concurrency_architecture.md`

---

## 1. Bối Cảnh Hệ Thống & Bài Toán Tranh Chấp Tài Nguyên

### 1.1 Hiện trạng Hệ sinh thái Workbench & OmniBrowser
Trong hệ sinh thái Multi-Agent hiện tại của Workbench, nhiều tác tử thuộc các dự án độc lập (`OmniBrowser`, `Trung-Le-Huu`, `Huawei_ICPC`, `ParkingOptimization`) cùng chạy song song và có nhu cầu tự động hóa trình duyệt.

Toàn bộ các tác tử này hiện đang trỏ chung vào **1 instance Google Chrome duy nhất**:
- Port CDP: `17082`
- User Data Directory: `~/.chrome-ai-profile` (profile thực tế chứa thông tin đăng nhập cá nhân: Google, ChatGPT, GitHub, v.v.)
- Pipeline tương tác chuẩn: `list-tabs ⟶ observe (--match) ⟶ act (--action, --ref) ⟶ observe`.

### 1.2 Các Rủi Ro Vận Hành & Điểm Nghẽn Cốt Lõi
1. **Khóa File & Xung Đột `SingletonLock`:** Chromium sử dụng cơ chế khóa độc quyền `SingletonLock` trên thư mục profile. Khi một tiến trình khác cố mở hoặc ghi vào cùng thư mục, browser sẽ từ chối khởi động hoặc crash.
2. **Xung Đột Tab & Tranh Chấp Focus (Tab/Focus Collision):** 
   - `list-tabs` hiện trả về toàn bộ tabs của mọi agent. Agent A có thể vô tình đọc nhầm hoặc đóng nhầm tab của Agent B.
   - Khi hai agent cùng tương tác, lệnh `Target.activateTarget` hoặc click/input làm thay đổi focus tab liên tục trên OS window, dẫn đến race condition và gián đoạn luồng thực thi.
3. **Crash Blast Radius (Sụp Đổ Dây Chuyền):** 
   - Một agent truy cập trang web nặng (chứa WebGL, Canvas 3D, memory leak JS) làm sập GPU process hoặc Browser process sẽ kéo sập toàn bộ các session của tất cả các agent khác.
4. **Phình To Bộ Nhớ (Memory Bloat):** 
   - Một Chrome instance chạy liên tục nhiều ngày với hàng chục tab tích tụ DOM nodes và renderer heap, tiêu tốn 4–8 GB+ RAM và gây nghẽn CPU trên máy trạm.
5. **Rò Rỉ Trạng Thái & Ô Nhiễm Profile (State Leakage & Profile Pollution):** 
   - Không có sự cách ly giữa các tác vụ độc lập: Cookies, localStorage, IndexedDB, Cache, và History bị ghi đè chéo, vi phạm nghiêm trọng nguyên tắc bảo mật và tính toàn vẹn dữ liệu.

---

## 2. Phân Tích Chuyên Sâu 2 Phương Án Do User Đề Xuất

### 2.1 Phương án 1: Mỗi Agent Spawn 1 Instance Chrome Riêng Biệt và Đóng Sau Khi Dùng Xong (Ephemeral Multi-Instance)
- **Cơ chế:** Mỗi khi agent có task, spawn một tiến trình Chrome mới với `--user-data-dir=$(mktemp -d)` và dynamic `--remote-debugging-port`. Khi xong việc, kill tiến trình và xóa thư mục tạm.
- **Ưu điểm:**
  - Cách ly tuyệt đối về tiến trình (Process Isolation): Crash của một agent không ảnh hưởng tới agent khác.
  - Xóa sạch trạng thái sau khi dùng (Zero State Residue), không bị xung đột `SingletonLock`.
- **Nhược điểm & Giới hạn:**
  - **Cold-start Latency:** Mỗi lần khởi động Chrome tốn từ 1.5s đến 3.5s. Với các tác vụ micro-actions hoặc tra cứu nhanh, thời gian khởi động chiếm phần lớn tổng thời gian thực thi.
  - **Chi Phí Tài Nguyên Baseline (RAM/CPU Overhead):** Mỗi Chrome instance ngốn tối thiểu 300MB – 600MB RAM chỉ cho Browser/GPU/Network processes nền, chưa tính renderer. Nếu có 6–10 agent chạy đồng thời, hệ thống tiêu tốn 3–6 GB RAM chỉ riêng cho overhead của browser shells.
  - **Mất Phiên Đăng Nhập:** Ephemeral profile là profile trắng (incognito), không có sẵn cookie/session của người dùng.

### 2.2 Phương án 2: Sử Dụng Headless Browser Thuần Túy
- **Cơ chế:** Chạy Chrome không có giao diện đồ họa (`--headless=new`).
- **Ưu điểm:**
  - Tiết kiệm tài nguyên: Benchmark của `browser-use` ghi nhận Headless khởi động nhanh hơn ~60%, tiêu thụ ít hơn 51% CPU time và 42% peak RAM.
  - Thích hợp cho môi trường máy chủ CI/CD, batch scraping, kiểm thử tự động.
- **Nhược điểm & Giới hạn Cốt Lõi:**
  - **Tỷ Lệ Bị Chặn Bởi Anti-Bot/Cloudflare Rất Cao:** Thử nghiệm của `browser-use` trên 71 trang web có bảo vệ bot cho thấy plain headless Chromium chỉ vượt qua ~2% trường hợp do thiếu sót về fingerprint, canvas/webgl metrics, và các CDP artifacts.
  - **Mất Khả Năng Giám Sát Trực Tiếp (Human-in-the-loop / HITL):** Người dùng không thể can thiệp, xem trực tiếp agent đang thao tác gì để gỡ lỗi khi có sự cố.

---

## 3. Nghiên Cứu Mở Rộng & Kinh Nghiệm Từ Các Hệ Thống Thực Tế

### 3.1 Bài học từ Mã Nguồn `browser-use`
1. **`LocalBrowserWatchdog` (`browser_use/browser/watchdogs/local_browser_watchdog.py`):**
   - Cấp phát port linh hoạt qua hàm `_find_free_port()` (bind vào socket port 0 để lấy port khả dụng).
   - Tự động bắt các ngoại lệ liên quan đến `SingletonLock` và `user data directory already in use` để fallback sang tạo thư mục tạm `tempfile.mkdtemp(prefix='browseruse-tmp-')` với cơ chế thử lại (retry loop).
   - Giám sát tiến trình bằng `psutil.Process`, dọn dẹp an toàn khi nhận `BrowserKillEvent`.
2. **`BrowserProfile` (`browser_use/browser/profile.py`):**
   - Đưa ra cảnh báo hệ thống: **Tuyệt đối không dùng chung `storage_state` và `user_data_dir` vật lý**, vì `storage_state` sẽ ghi đè ép buộc dữ liệu lưu trữ.
   - Trong chế độ chạy song song (parallel browsing), khuyến nghị dùng `storage_state` với `user_data_dir=None` (in-memory/ephemeral), hoặc mỗi browser phải có một `user_data_dir` hoàn toàn độc lập.
3. **`SessionManager` (`browser_use/browser/session_manager.py`):**
   - Quản lý CDP session hướng sự kiện (Event-driven CDP) qua `Target.attachedToTarget` và `Target.detachedFromTarget`.
   - Cơ chế tự động khôi phục focus (`_recover_agent_focus`): Khi một target bị crash/detach, hệ thống tự động tìm tab khả dụng khác hoặc tạo tab fallback khẩn cấp để duy trì luồng chạy của agent.
4. **`Sandbox` (`browser_use/sandbox/sandbox.py`):**
   - Đóng gói toàn bộ code và context qua `cloudpickle` để chạy trên sandbox cô lập, truyền luồng logs và live browser view (`live_url`) qua SSE (Server-Sent Events).

### 3.2 Bài học từ `AnchorBrowser` & Tài liệu Chuyên Sâu
- **Bản chất của Headless Hiện Đại (`--headless=new`):**
  - Từ Chrome 112+, Chrome đã thống nhất code base giữa Headless và Headful. Headless mới vẫn build full DOM, parse CSS, thực thi JavaScript, chạy network stack và compositing y hệt Headful; điểm khác biệt duy nhất là **bỏ qua bước vẽ pixel ra màn hình hiển thị vật lý (display surface)**.
  - Do đó: Các thao tác `observe`, DOM extraction, screenshot, crop visual escalation (`inspectVisual`) hoạt động hoàn toàn chính xác trên `--headless=new`.
- **Headless vs Headful là một Runtime Setting per-session:**
  - Không nên xem Headless vs Headful là một quyết định kiến trúc bất biến của toàn hệ thống. Cần xem nó là một thuộc tính cấu hình động theo từng phiên làm việc (Per-Session Capability Request).

---

## 4. Tổng Hợp & So Sánh: Góc Nhìn Của Chuyên Gia vs Phản Biện Của ChatGPT (GPT-5.6 Sol Thinking High)

Sau phiên tham vấn chuyên sâu qua session `browser_concurrency_arch`, dưới đây là bảng đối chiếu chi tiết giữa góc nhìn ban đầu của Specialist và các phản biện mang tính đột phá từ ChatGPT:

| Khía Cạnh | Góc Nhìn Ban Đầu (Specialist) | Phản Biện & Định Hướng Của ChatGPT (GPT-5.6 Sol) | Điểm Đồng Thuận & Đột Phá Kiến Trúc |
| :--- | :--- | :--- | :--- |
| **Mô hình kiến trúc tổng thể** | Phân vân giữa 1 Shared Daemon duy nhất (chia BrowserContext) hoặc Pure Multi-Instance. | **Bác bỏ cả 2 cực đoan.** Đề xuất **Hybrid Bulkheaded Browser Pool**: Kết hợp warm shared daemons cho tác vụ nhẹ + dedicated instances cho tác vụ nặng. | **Đồng thuận 100%:** Không tạo ra 1 "Mega Chrome" ôm hết 10 agent (quá dễ sập toàn bộ), nhưng cũng không spawn 10 cold instances (lãng phí tài nguyên). |
| **Bản chất của Isolation** | Xem `BrowserContext` là giải pháp cách ly session chính. | **Tách biệt rành mạch 3 tầng Isolation:**<br>1. `BrowserContext` = **State/Session Isolation** (Cookies, Storage, Tabs).<br>2. `Process` = **Failure/Crash Isolation** (RAM, GPU, CPU limits).<br>3. `Account/Identity Lease` = **Server-side State Isolation**. | **Đột phá quan trọng:** Nhận diện được vấn đề **Server-side State Collision** (hai context khác nhau nhưng cùng dùng chung 1 tài khoản Gmail/GitHub thì xóa email ở context A vẫn làm mất email ở context B). Cần có **Identity Lease**. |
| **Giải quyết bài toán Headless & Anti-Bot** | Cân nhắc dùng Xvfb (Virtual Framebuffer) trên macOS hoặc stealth CDP patches để fake Headful. | **Bác bỏ Xvfb trên macOS.** macOS không phải hệ sinh thái X11; dùng XQuartz/Xvfb tạo thêm tầng trung gian cồng kềnh. Không dựa vào CDP stealth patches vì anti-bot hiện đại soi TLS, IP, Canvas, WebGL và hành vi. | **Đồng thuận:** Dùng `--headless=new` chuẩn cho machine workloads (DOM, screenshot, crop). Dành riêng Native Headful cho các workflow cần bypass bot/CAPTCHA hoặc Human Takeover. |
| **Quản lý Auth Profile (`~/.chrome-ai-profile`)** | Xem xét Copy-on-Write (CoW via `cp -c` trên APFS) hoặc export `storage_state.json`. | Đưa ra nguyên tắc bất biến: **Profile chính TUYỆT ĐỐI không được là concurrent runtime profile**, nó chỉ là **Auth Source of Truth**. Xây dựng **Auth State Broker** (Single Writer, Multi-reader Snapshot Vault). CoW chỉ là Tier B fallback khi site cần SQLite/LevelDB phức tạp. | **Đồng thuận:** Tạo kho lưu trữ Snapshot bất biến (`AuthSnapshotVault`: `google/v1`, `github/v2`) với phân quyền `chmod 600`. Các agent chỉ đọc snapshot, không bao giờ mount trực tiếp profile chính. |
| **Xung đột Focus & Tab Collision** | Quản lý bằng Mutex hoặc cờ đánh dấu tab trong `list-tabs`. | **Loại bỏ hoàn toàn khái niệm Global Active Tab / Global Focus.** Thay thế bằng **Lease-Scoped Target Registry**: Agent chỉ được nhìn và thao tác trên target thuộc `lease_id` của mình. Bổ sung **Fencing Token** để loại bỏ stale commands khi lease hết hạn. | **Đột phá:** Khái niệm Fencing Token trong Distributed Systems được áp dụng vào browser automation, dập tắt triệt để race condition và ghost actions. |
| **Kiểm soát sập (Crash Containment) & Tái chế** | Khởi động lại daemon khi RAM vượt ngưỡng hoặc tab crash. | Áp dụng **Drain Protocol**: Khi daemon chạm ngưỡng tài nguyên, chuyển trạng thái sang `DRAINING` (từ chối lease mới, để lease cũ hoàn thành tự nhiên rồi mới restart). Cung cấp **Semantic Task Checkpoint** để replay task trên daemon khác khi browser sập. | **Đột phá:** Tránh được việc kill cưỡng bức làm gián đoạn các session đang chạy khỏe mạnh. |
| **Điều phối tải (Backpressure)** | Chưa có cơ chế giới hạn số lượng agent gọi browser. | Đề xuất **Admission Controller**: Xếp hàng (queue) và áp dụng backpressure dựa trên telemetry hệ thống (Memory Pressure, CPU load) của macOS thay vì cho spawn vô tội vạ. | **Đồng thuận:** Tránh cạn kiệt RAM khi có đột biến tác vụ đồng thời. |

---

## 5. Kiến Trúc Hoàn Thiện: OmniBrowser Concurrency & Session Broker

Dựa trên sự kết hợp giữa nghiên cứu thực nghiệm và kết quả tham vấn chuyên sâu, kiến trúc chuẩn thế hệ mới của OmniBrowser được định nghĩa như sau:

```
                                  Agent Request
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │    OmniBrowser Session API    │
                        └───────────────┬───────────────┘
                                        │ (BrowserRequirements)
                                        ▼
                        ┌───────────────────────────────┐
                        │      Admission Controller     │
                        │    (Memory/CPU Backpressure)  │
                        └───────────────┬───────────────┘
                                        │
                                        ▼
                        ┌───────────────────────────────┐
                        │        Session Router         │
                        └───────┬───────────────┬───────┘
                                │               │
                  Class S (Light)               │ Class I / Class A (Heavy/Auth)
                                ▼               ▼
                  ┌────────────────────┐ ┌────────────────────┐
                  │ Shared Daemon Pool │ │ Dedicated Pool     │
                  │ (Daemon A, B)      │ │ (Warm/On-demand)   │
                  └─────────┬──────────┘ └─────────┬──────────┘
                            │                      │
                            ▼                      ▼
                  ┌───────────────────────────────────────────┐
                  │               Lease Manager               │
                  │ (lease_id, fencing_token, identity_lease) │
                  └─────────────────────┬─────────────────────┘
                                        │
                                        ▼
                  ┌───────────────────────────────────────────┐
                  │              Target Registry              │
                  │  (Virtual scoped tabs: Target -> Lease)   │
                  └─────────────────────┬─────────────────────┘
                                        │
                         ┌──────────────┴──────────────┐
                         ▼                             ▼
              ┌─────────────────────┐       ┌─────────────────────┐
              │  Auth State Vault   │       │  Lifecycle Watchdog │
              │ (Snapshot Storage)  │       │   (Drain Protocol)  │
              └─────────────────────┘       └─────────────────────┘
```

### 5.1 Ba Phân Lớp Thực Thi (Execution Classes)
1. **Class S (Shared - Multi-Context):**
   - *Runtime:* Warm Chrome Daemon chạy `--headless=new`.
   - *Isolation:* Ephemeral `BrowserContext` (`Target.createBrowserContext`).
   - *Mục đích:* Dành cho 70–80% tác vụ: cào dữ liệu, đọc tài liệu, kiểm thử nhanh, quan sát semantic DOM, chụp màn hình phục vụ LLM reasoning.
   - *Ưu điểm:* Khởi tạo < 50ms, tốn thêm rất ít RAM (~20–40MB/context).
2. **Class I (Isolated - Dedicated Ephemeral):**
   - *Runtime:* Tiến trình Chrome độc lập với `--user-data-dir=$(mktemp -d)`.
   - *Isolation:* Process-level boundary.
   - *Mục đích:* Các trang web nặng (WebGL, Canvas, video), trang web lạ chưa được kiểm chứng an toàn, hoặc tác vụ crawl kéo dài có nguy cơ rò rỉ bộ nhớ.
   - *Ưu điểm:* Crash containment tuyệt đối, không ảnh hưởng đến bất kỳ agent nào khác.
3. **Class A (Authenticated & Interactive):**
   - *Runtime:* Dedicated Headful Chrome hoặc Context gắn kèm Auth Snapshot từ Vault.
   - *Isolation:* Kết hợp Process Isolation và **Exclusive Identity Lease**.
   - *Mục đích:* Tác vụ cần phiên đăng nhập nhạy cảm (Gmail, GitHub, ChatGPT), vượt tường lửa Cloudflare/CAPTCHA, hoặc chế độ Human Takeover cần hiển thị cửa sổ trực quan.

### 5.2 Các Thành Phần Lõi Của Broker
1. **`SessionRouter`:** Tiếp nhận yêu cầu dạng `BrowserRequirements` (cần visual không? cần auth không? tải nặng hay nhẹ?) để tự động phân luồng vào Class S, I, hay A.
2. **`LeaseManager`:**
   - Cấp phát `lease_id` kèm `fencing_token` (epoch number).
   - Quản lý khóa định danh (`auth_identity_lease`): Ngăn chặn 2 agent cùng thực hiện tác vụ thay đổi dữ liệu (destructive action) trên cùng một tài khoản người dùng tại cùng một thời điểm.
3. **`TargetRegistry` (Loại bỏ triệt để Tab/Focus Collision):**
   - Quản lý ánh xạ: `TargetID ⟶ BrowserContextID ⟶ LeaseID ⟶ AgentID`.
   - Lệnh `list-tabs` sẽ tự động lọc chỉ hiển thị các tab thuộc quyền sở hữu của lease hiện tại.
   - Các lệnh `observe`, `act`, `eval` bắt buộc truyền qua lease context; loại bỏ hoàn toàn việc chuyển tab OS (`Target.activateTarget`) trong các tác vụ tự động ngầm.
4. **`AuthStateVault`:**
   - Hoạt động theo nguyên tắc **Single-Writer / Multi-Reader**.
   - Profile gốc `~/.chrome-ai-profile` chỉ dùng để xuất snapshot định kỳ (hoặc khi người dùng đăng nhập mới).
   - Snapshot chứa: Cookies, localStorage, sessionStorage (tùy biến), metadata nguồn. Dữ liệu được mã hóa và cấp quyền `chmod 600`.
5. **`LifecycleWatchdog` & `AdmissionController`:**
   - Giám sát sức khỏe browser qua các sự kiện CDP (`Target.targetCrashed`, `Inspector.detached`) và telemetry hệ thống (`psutil` RSS memory, CPU load).
   - Thực thi **Drain Protocol** khi daemon vượt ngưỡng bộ nhớ: Từ chối lease mới, đợi các session hiện tại kết thúc an toàn, sau đó khởi động lại daemon.
   - Điều tiết hàng đợi (Backpressure) khi hệ thống chịu tải cao, đảm bảo không làm sập máy trạm.

---

## 6. Ba Nguyên Tắc Bất Biến Mới (System Invariants) Đề Xuất Cho OmniBrowser

Để đảm bảo tính bền vững lâu dài, OmniBrowser sẽ bổ sung 3 invariant mới vào [AGENTS.md](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/AGENTS.md):

1. **Invariant 8 — Lease Ownership Invariant:**
   > *Không có lệnh nào được thực thi mà không có Lease hợp lệ. Không có agent nào được truy cập hoặc đóng Target nằm ngoài phạm vi Lease của mình.*
2. **Invariant 9 — Profile Sanctity Invariant:**
   > *Profile gốc (`~/.chrome-ai-profile`) là Auth Source of Truth, tuyệt đối không bao giờ được dùng làm Concurrent Runtime Profile cho các tác vụ tự động.*
3. **Invariant 10 — Failure Domain Separation Invariant:**
   > *BrowserContext dùng để cách ly trạng thái (State Isolation); Process Boundary dùng để cách ly sự cố sập (Crash Isolation); Account Lease dùng để cách ly dữ liệu phía máy chủ (Server-side State Isolation).*

---

## 7. Lộ Trình Triển Khai (Implementation Roadmap)

- **Giai đoạn 1 (Immediate Fix - Giảm nghẽn tức thì):**
  - Cập nhật `cdp_controller.py`: Thêm cờ `--session <id>` và lọc `list-tabs` theo target ownership.
  - Áp dụng cơ chế cấp phát dynamic port và temp user-data-dir của `browser-use` vào các test runners và task độc lập để ngắt phụ thuộc vào port 17082.
- **Giai đoạn 2 (Core Broker & Context Pool):**
  - Xây dựng module `src/browser_core/broker/`: Hiện thực hóa `SessionRouter`, `LeaseManager`, và `TargetRegistry`.
  - Hỗ trợ `Target.createBrowserContext` trên warm daemon cho Class S workloads.
- **Giai đoạn 3 (Auth State Vault & Watchdog):**
  - Hiện thực hóa `AuthStateVault` để trích xuất và nạp `storage_state` an toàn.
  - Tích hợp `LifecycleWatchdog` với Drain Protocol và Admission Controller theo dõi memory pressure trên Apple Silicon.
