# OmniBrowser: Biên Niên Sử Tư Duy, Nghiên Cứu & Tiến Hóa Kiến Trúc
*(Chronicle of Problem Formulation, Architectural Debates, Market Teardown & Multi-Agent Consensus)*

- **Dự án:** OmniBrowser (Tiền thân: Skill `browser-automation-cdp`)
- **Workspace:** `/Users/huutrungle2001/Documents/OnGoing/OmniBrowser`
- **Nguồn dữ liệu:** Trích xuất nguyên bản từ nhật ký hệ thống Antigravity (`transcript.jsonl`), lịch sử tmux `workbench-codex`, và các báo cáo nghiên cứu độc lập.
- **Thời gian diễn ra:** 2026-09-16 22:30:13 UTC $\to$ 2026-09-17 01:08:08 UTC.

---

## 1. Điểm Khởi Đầu: Đặt Vấn Đề Từ Thực Chiến (The Spark)

### Bối cảnh & Yêu cầu gốc của Operator
- **Step 6666 & 6674** *(2026-09-16 22:30:13 - 22:37:11 UTC)*:
  > *"Skill `browser-automation-cdp` này khá chung chung, cũng được việc, tôi đã dùng nó để làm thủ tục nhập học Warwick, đăng ký GP ở Coventry, tạo tài khoản AutoCAD... nhưng nhiều khi nó rất chậm. Tôi expect nó phải có script lấy DOM của page, tự động làm sạch source, chỉ trả về dữ liệu có cấu trúc (có những nút gì, form nào, text gì) để agent quyết định bấm vào đâu. Và chỉ chụp màn hình khi gặp canvas hoặc blocker mà DOM không giải quyết được..."*

### Đánh giá hiện trạng của Skill cũ:
- File cũ: `cdp_controller.py` (~130 dòng) chỉ cung cấp 4 lệnh thô sơ qua Playwright: `list-tabs`, `goto`, `eval`, `screenshot`.
- **Nghẽn cổ chai (Pain Points):**
  1. *Lãng phí Token & Băng thông:* Mỗi lần quan sát trang web phải dump toàn bộ raw HTML (vài trăm KB) hoặc chụp ảnh PNG 4K full-screen (1-2 MB).
  2. *Độ trễ cao (Latency Bloat):* Quá trình truyền tải và xử lý ảnh khiến mỗi thao tác mất từ 3-8 giây.
  3. *Thiếu tính tất định (No Postconditions):* Thao tác click/type không có cơ chế chờ phản hồi (network/DOM idle), dễ dẫn đến click trượt hoặc gửi form khi DOM chưa sẵn sàng.

---

## 2. Tư Duy Độc Lập: Tham Vấn Codex CLI (Blind Brainstorming)

### Phương pháp tiếp cận (Methodology)
- **Step 6684** *(22:46:17 UTC)*: Operator yêu cầu: *"Summon một con tmux workbench-codex xong nêu vấn đề cho nó và bảo nó thiết kế giải pháp... chỉ cung cấp vấn đề, đừng cho nó biết giải pháp hiện tại của bọn mình, để nó tư duy độc lập."*
- Bắn prompt giấu giải pháp vào session `workbench-codex` (chạy model `cx/gpt-5.6-terra high`).

### Kết quả giải pháp độc lập của Codex
- Codex tự động đề xuất **Kiến trúc 3 Tầng (Three-Tier Semantic Browser Architecture)**:
  1. *In-browser DOM Normalizer (`dom_agent.js`)*: Duyệt cây DOM bằng `TreeWalker` hoặc BFS, trích xuất accessibility semantics (role, accessible name, state, bounding box).
  2. *Opaque Handle/Ref Scheme*: Đánh số phần tử tương tác bằng ref ngắn gọn (e.g. `f0.d9.n186`) thay vì CSS selector dài loằng ngoằng dễ vỡ.
  3. *Native Action Execution*: Dùng CDP native mouse/keyboard dispatch, đi kèm bộ lắng nghe biến động (MutationObserver / Network idle).

---

## 3. Khảo Sát Thị Trường & Bài Học Thực Nghiệm (Market Teardown & SOTA Research)

Operator chỉ đạo mở rộng điều tra: kiểm tra thị trường (`browser-use`, Stagehand, Skyvern, Crawl4AI), phân tích bài luận kinh điển *"The Bitter Lesson of Browser Agents"* của Gregor Zunic, và mổ xẻ bước nhảy vọt Computer Use của OpenAI GPT-6 Astra.

### A. Phân tích các sản phẩm mã nguồn mở (Step 6771)
1. **`browser-use`**:
   - *Ưu điểm:* Tiên phong trong việc gán nhãn thị giác (Set-of-Marks - SoM) kết hợp DOM accessibility tree.
   - *Nhược điểm:* Phụ thuộc nặng nề vào ảnh chụp màn hình kèm overlay đánh số. Mỗi bước gửi ảnh toàn màn hình ngốn hàng ngàn token, chi phí cực cao và tốc độ chậm.
2. **`stagehand` (Browserbase)**:
   - *Ưu điểm:* Điểm sáng lớn nhất là tư duy API 3 hành động nguyên tử: `act`, `extract`, `observe`.
   - *Hạn chế:* Vẫn phụ thuộc nhiều vào wrapper Playwright bề mặt, chưa can thiệp sâu vào CDP level để kiểm soát document epoch.
3. **`crawl4ai`**:
   - Cung cấp thuật toán làm sạch DOM cực tốt (loại bỏ thẻ rác, strip tag vô ích), tối ưu hóa markdown output cho LLM.

### B. Bài học từ "The Bitter Lesson of Browser Agents" & GPT-6 Astra (Step 6869)
- **Luận điểm của Gregor Zunic (Browser-use founder):**
  - Mọi hệ thống cố định nghĩa trước trạng thái thế giới (hardcoded finite state machine, heuristic parsing) cuối cùng đều bị đánh bại bởi các phương pháp tổng quát tận dụng compute và search.
  - Các heuristic lọc DOM cứng nhắc sẽ gãy khi gặp các trang web dị biệt.
- **Bài học từ GPT-6 Astra (Native Computer Use):**
  - Astra mạnh không phải vì nó có một bộ parser DOM hoàn hảo, mà vì nó có khả năng **nhận thức đa phương thức phối hợp (Multimodal Co-reasoning)**: Dùng DOM dạng semantic làm fast-path, nhưng luôn có sẵn đường thoát (escape hatch) là **Computer Use / Native Coordinates / Code Execution** khi DOM bị mã hóa hoặc vẽ trên Canvas.
- **Quyết định kiến trúc của chúng ta:**
  - `observe()` là một **fast-path chấp nhận có thể thiếu sót (lossy projection)**, luôn đi kèm metadata `coverage.is_complete = false`.
  - Không bao giờ khóa chết agent trong DOM; luôn cung cấp escape hatch: `runBrowserCode` (chạy JS trực tiếp) và `inspectVisual` (crop ảnh có mục tiêu).

---

## 4. Tranh Luận Kiến Trúc & Phản Biện (The Adversarial Debate)

- **Step 6950 $\to$ 7006** *(23:05:43 - 23:12:18 UTC)*: Operator yêu cầu: *"Argue với Codex về 2 điểm cần tinh chỉnh, cãi nhau đi, đưa ra luận điểm của bạn bảo nó phản biện để chốt cái tốt nhất."*

### Hai điểm va chạm nảy lửa giữa Antigravity (Reviewer) và Codex (Implementer):

#### Điểm 1: Vấn đề phân mảnh vi mô (Over-modularization)
- **Codex đề xuất ban đầu:** Tách Core Engine thành **14-15 micro-files** (`connection.py`, `page_registry.py`, `projector.py`, `action_engine.py`, `expectations.py`, `delta.py`, `inspector.py`, `visual.py`, `policy.py`, `telemetry.py`...).
- **Antigravity phản biện:**
  - Đây là căn bệnh "Clean Architecture giáo điều" áp dụng quá sớm cho một codebase chỉ mới bắt đầu.
  - Vòng đời `observe -> act -> wait expectations -> compute delta` là một **giao dịch gắn kết cao (high cohesion transaction)**. Tách rời `expectations.py` và `delta.py` chỉ tạo ra ranh giới giả, gây circular import, làm phình to context window khi agent sau này phải nạp cả tá file để đọc hiểu luồng tương tác.
- **Codex nhượng bộ (Concession):** Đồng ý rút gọn thành **4 module cốt lõi**:
  - `contracts.py`: Định nghĩa schemas dữ liệu, error hierarchy.
  - `page_manager.py`: Quản lý CDP session, frame lifecycle, epoch token.
  - `engine.py`: Gom trọn bộ `observe`, `act`, postconditions và delta.
  - `primitives.py`: Gom toàn bộ escape hatches (`inspect`, `run-code`, `visual`).

#### Điểm 2: Vấn đề Site-Packs SDK (Premature Abstraction)
- **Codex đề xuất ban đầu:** Xây dựng hẳn một bộ plugin SDK `SitePack(Protocol)`, `pack_registry`, `manifest.json`, và viết mẫu `warwick_portal_pack`.
- **Antigravity phản biện:**
  - Vi phạm nguyên tắc YAGNI (You Aren't Gonna Need It).
  - Bản thân Core Engine (`ref`, `document epoch`, `stale recovery`) còn chưa được kiểm chứng thực tế. Xây SDK sớm sẽ "đóng băng" các giả định chưa được kiểm chứng thành chuẩn API.
  - Một plugin viết riêng cho Warwick ngay lúc này chỉ có tác dụng che giấu khiếm khuyết của generic engine.
- **Codex nhượng bộ (Concession):** Cắt bỏ 100% Site-Packs SDK khỏi v1.0. Áp dụng nguyên lý: *"Design for extension, do not implement the extension."*

---

## 5. Nghiên Cứu Đa Chiều Từ Cộng Đồng: Role-Based vs Feature-Squads (Community Research)

- **Step 7042 $\to$ 7047** *(23:27:56 - 23:32:06 UTC)*: Operator đặt câu hỏi sâu sắc:
  > *"Trong Huawei tôi đã tạo một bộ agent theo vai trò (implementer, reviewer, oracle...). Nếu chia theo feature squads (mỗi feature có 1 cặp dev + reviewer riêng: dom-impl, dom-revi) thì cái nào tốt hơn? Đi search Google, Reddit, X, GitHub xem thiên hạ họ làm ra sao, summon vô hạn subagent để đào sâu."*

### Điều động 3 Subagent nghiên cứu song song:
1. **Subagent 1 (`4f6c404f`)**: Khảo sát học thuật & frameworks (MetaGPT, ChatDev, AutoGen, CrewAI, LangGraph).
2. **Subagent 2 (`adf46ac7`)**: Khảo sát Frontier Labs & SOTA Coding Products (Anthropic, Cognition/Devin, SWE-bench leaders, OpenHands, Factory.ai).
3. **Subagent 3 (`2e233e62`)**: Khảo sát cộng đồng kỹ sư, Reddit r/LocalLLaMA, Hacker News & Failure Post-mortems về "Coordination Tax" và "Conway's Law trên LLMs".

### Kết quả điều tra thực chứng:
1. **Số liệu định lượng từ Google Research / DeepMind / MIT (*Kim et al., 12/2025*, arXiv:2512.08296)**:
   - Trên các bài toán coding tuần tự và có tính phụ thuộc cao, hệ thống Multi-Agent dạng swarm/squad **làm giảm hiệu năng từ 39% đến 70%** so với Single-Agent có công cụ tốt.
   - Tỷ lệ lỗi dây chuyền khuếch đại lên tới **17.2 lần**.
2. **MAST Taxonomy (*NeurIPS 2025*, arXiv:2503.13657)**:
   - 41% - 86.7% lỗi trong multi-agent bắt nguồn từ việc các agent nói chuyện lòng vòng, mất mát ngữ cảnh và premature termination.
3. **Tại sao chia "Feature Squads có Reviewer riêng" (`dom-impl + dom-revi`) là thảm họa?**
   - *Thảm họa tích hợp (Local Optima vs Integration Hell):* `dom-revi` duyệt `dom_agent.js` thấy đúng $\to$ PASS. `engine-revi` duyệt `engine.py` thấy đúng $\to$ PASS. Nhưng khi ráp vào runtime Chrome thật thì gãy interface schema vì không có ai chịu trách nhiệm cho Global Contract.
   - *Bệnh xu nịnh (Reviewer Sycophancy & Echo Chambers):* Dùng chung một họ mô hình khiến dev và reviewer có chung điểm mù nhận thức, dẫn đến "Dấu tích xanh giả tạo" (False Green Check).
   - *Ngụy biện nhân hóa (The Anthropomorphic Fallacy):* Cố sao chép mô hình Spotify Squad của con người vào LLM là sai lầm; con người lập squad để giảm họp hành, còn LLM không cần họp nhưng lại bị suy thoái ngữ cảnh khi tóm tắt qua lại (telephone game).
4. **Quy chuẩn của các Frontier Labs (Anthropic, Devin, Agentless)**:
   - Áp dụng **Workflows tất định > Swarms**.
   - Dùng mô hình **Orchestrator $\to$ Surgical Implementer $\to$ Deterministic Test Harness $\to$ Adversarial Reviewer**.
   - Trọng tài tối cao phải là **Deterministic Sandbox Execution (PyTest, Linter, Exit code 0)** chứ không phải LLM-on-LLM text review.

---

## 6. Định Hình Hệ Thống: Chuẩn Hóa Cấu Trúc Hai Tầng (Two-Tier Architecture)

- **Step 7081 $\to$ 7187** *(23:44:25 - 01:05:23 UTC)*:
  - Operator phản ánh sự chênh lệch chất lượng giữa template cũ của `Workbench` và hệ sinh thái tối tân của `Huawei_ICPC_2026`.
  - Phân định ranh giới rõ ràng:
    - **Tier 1 (Workbench - Meta-Hub):** Trụ sở điều phối trung tâm, quản lý tài nguyên dùng chung (Dual RTX 4090 server, CDP clients, skill `agent-protocol`).
    - **Tier 2 (OmniBrowser - Dedicated Subproject):** Dự án độc lập tại `/Users/huutrungle2001/Documents/OnGoing/OmniBrowser`, sở hữu Git repo riêng, `AGENTS.md` riêng, `GOAL.md`, `PLAN.md`, `TODO.md` và `.agents/communication/`.

### Thiết lập hoàn chỉnh tại `OmniBrowser`:
- Khởi tạo Git repo (`commit 72b1dd3`, `8e68ef3`, `39ad280`, `3905230`).
- Ban hành bộ tứ Agent Blueprints: `omni_hub`, `omni_orchestrator`, `omni_implementer`, `omni_reviewer`.
- Tích hợp toàn bộ bản Master Plan ~400 dòng của Codex vào `PLAN.md`.
- Ban hành Task đầu tiên: `task-001-core-foundation.md`.

---

## 7. Tổng Kết Các Văn Kiện & Hồ Sơ Đã Lưu Trữ

Toàn bộ quá trình tư duy trên được bảo tồn nguyên vẹn và xác thực thông qua các tệp hồ sơ bền vững sau:

| Tài liệu / Artifact | Vị trí | Ý nghĩa & Vai trò |
| :--- | :--- | :--- |
| **Bản Master Plan Chi Tiết** | [`OmniBrowser/PLAN.md`](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/PLAN.md) | Kim chỉ nam kỹ thuật cho 4 Phase triển khai của OmniBrowser |
| **Bản Kế Hoạch Gốc từ Codex** | [`OmniBrowser/docs/01_codex_original_plan.md`](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/docs/01_codex_original_plan.md) | Bản thiết kế 18KB đầu tiên do Codex sinh ra |
| **Biên Bản Tranh Luận & Nhượng Bộ** | [`OmniBrowser/docs/02_codex_debate_consensus.md`](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/docs/02_codex_debate_consensus.md) | Ghi nhận phản biện về Over-modularization và cắt giảm Site Packs |
| **Hiến Pháp Meta-Hub** | [`Workbench/AGENTS.md`](file:///Users/huutrungle2001/Documents/OnGoing/Workbench/AGENTS.md) | Quy định điều phối hai tầng giữa Workbench và các dự án con |
| **Đặc Tả Nhiệm Vụ Phase 1** | [`OmniBrowser/.agents/communication/tasks/task-001-core-foundation.md`](file:///Users/huutrungle2001/Documents/OnGoing/OmniBrowser/.agents/communication/tasks/task-001-core-foundation.md) | Task spec chuẩn schema sẵn sàng cho Implementer |
| **Nhật Ký Hệ Thống Gốc** | `/Users/huutrungle2001/.gemini/antigravity-cli/brain/5c26fc8b-4ef2-430d-841f-5fbb36f87a81/.system_generated/logs/transcript.jsonl` | Nhật ký bất biến 7.200+ dòng ghi lại từng prompt và tool call |
