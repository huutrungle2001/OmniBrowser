# Consultation Request: Kiến trúc Implicit Procedural Recipe dưới góc nhìn Caching & Distributed Systems

**Context**: OmniBrowser (AI Agent Browser Automation Engine) đang triển khai **Milestone v2.3: Implicit Flight Recorder & Domain-Scoped Procedural Cache**.
Hệ thống chuyển đổi Level 2 (Procedural Memory) từ cơ chế thủ công (*explicit opt-in* với `learn begin`/`--learn-run`/`complete`) sang **Implicit Flight Recorder** (tự động ghi nhận ngầm chuỗi action trong `engine.act()`, tự động chưng cất thành draft recipe khi phát hiện transition ranh giới) và **Domain-Scoped Procedural Cache** (gợi ý sẵn `suggested_recipes` ngay trong lệnh `observe`).

Chúng tôi muốn tham vấn chuyên sâu từ bạn (ChatGPT / GPT-5.6 Sol Thinking) về việc **mô hình hóa Procedural Recipes như một hệ thống Caching đa tầng (Multi-tier Cache)** trong môi trường Multi-Agent.

---

### Các câu hỏi trọng tâm cần phân tích và phản biện chuyên sâu:

#### 1. Cache Key & Matcher Granularity (Khóa bộ nhớ đệm và độ mịn nhận diện)
- Hiện tại chúng tôi match recipe dựa trên URL domain + regex/fnmatch pattern (`matches_url(url)`).
- **Vấn đề**: Trong các ứng dụng Web hiện đại (SPA, React/Vue/Next.js), URL thường không phản ánh đầy đủ trạng thái trang (ví dụ: Dynamic routing, client-side modal, multi-step stepper form giữ nguyên URL, stateful filters).
- **Câu hỏi**:
  - Cache Key tối ưu cho một Procedural Recipe nên bao gồm những thành phần nào?
  - Có nên kết hợp giữa URL path + Semantic DOM Signature (e.g. Landmark hashes, form input fingerprint, title/h1) thành một Composite Key không?
  - Chi phí tính toán (overhead) và tính ổn định (resilience against non-breaking UI shifts) của Composite Key này như thế nào?

#### 2. Cache Invalidation & Drift Detection (Xác thực tính hợp lệ & Phát hiện sai lệch UI)
- Website luôn biến đổi theo thời gian (A/B testing, dynamic class names, cập nhật UI, thay đổi flow).
- **Câu hỏi**:
  - Chiến lược Invalidation nào phù hợp nhất cho Procedural Browser Cache? (TTL theo thời gian, failure rate threshold, hay semantic drift distance giữa live DOM và cached sitemap?)
  - Khi một bước trong Recipe thất bại (e.g. `ActionTimeoutError` hoặc `StaleRefError`), làm thế nào để phân biệt giữa:
    (a) Transient latency / network hiccup (nên retry).
    (b) Genuine UI redesign / schema drift (cần invalidate / evict / mark stale).
    (c) Modal / Pop-up chặn đường (cần handle interrupt rồi tiếp tục)?

#### 3. Cache Granularity: Micro-Recipes vs Macro-Workflows (Độ mịn của Recipe)
- Hiện tại, Flight Recorder tự động cắt chuỗi khi gặp boundary: (1) URL change, (2) Submit/Save click, (3) Quá 8 actions.
- **Câu hỏi**:
  - Nên thiết kế Cache ở mức **Micro-recipes** (atomic subroutines: e.g. "dismiss_cookie_banner", "fill_login_credentials", "expand_advanced_filter") hay **Macro-recipes** (end-to-end user journeys: e.g. "login_and_export_monthly_report")?
  - Làm sao để xây dựng cơ chế **Recipe Composition / Chaining** (ghép các micro-recipes thành macro flow) mà không gây ra hiệu ứng domino khi 1 step ở giữa bị drift?

#### 4. Cache Eviction, Promotion & Governance (Chính sách đào thải và thăng cấp)
- Nếu hàng chục agent chạy ngầm hàng ngày, `FlightRecorder` sẽ sinh ra hàng trăm draft candidate recipes.
- **Câu hỏi**:
  - Chính sách nào giúp quản lý vòng đời của Recipe:
    - Draft Candidate (local/session) $\to$ Verified Candidate $\to$ Curated Domain Recipe $\to$ Stale/Archived?
  - Làm thế nào để giải quyết bài toán: Nhiều agent cùng sinh ra các recipe tương tự nhau cho cùng một tác vụ (De-duplication / Canonicalization)?
  - Khi nào nên dùng LFU/LRU eviction để tránh phình to local SQLite ledger và disk space?

#### 5. Execution Modes: Fast-Path vs Speculative Execution (An toàn khi thực thi)
- Điểm mạnh lớn nhất của Recipe Cache là tiết kiệm token và thời gian (bỏ qua LLM reasoning loop). Nhưng điểm rủi ro lớn nhất là thao tác mù quáng (blind execution) gây hậu quả nghiêm trọng trên tài khoản thật.
- **Câu hỏi**:
  - Nên thiết lập các "Chốt an toàn" (Safety Invariants) nào trước và trong khi thực thi cached recipe?
  - Kiến trúc Precondition / Postcondition verification nên thiết kế ra sao để đảm bảo: Nếu trang chưa sẵn sàng hoặc dữ liệu đầu vào không khớp, recipe lập tức fail-closed và trả quyền điều khiển về cho LLM agent mà không để lại tác dụng phụ (side-effects)?

---

Hãy phân tích chi tiết, đưa ra các kiến trúc tham chiếu (design patterns), công thức hoặc cấu trúc dữ liệu cụ thể (data contracts) để OmniBrowser áp dụng ngay vào các phiên bản tiếp theo.
