Tôi đọc tài liệu theo đúng framing mà đội OmniBrowser đặt ra: từ v2.3, Level 2 không còn là một “macro recorder” thủ công mà trở thành một **Implicit Flight Recorder + Domain-Scoped Procedural Cache**, tức hệ thống tự quan sát action stream, distill candidate recipe, rồi đưa recipe trở lại fast path thông qua `observe.suggested_recipes`. 

Kết luận kiến trúc quan trọng nhất của tôi là:

> **Procedural Recipe không nên được mô hình hóa như một cache entry thông thường. Nó nên là một *guarded executable cache entry*: một đoạn chương trình đã học, có precondition, postcondition, confidence, provenance, drift model và side-effect policy.**

Từ đó, tôi đề xuất OmniBrowser tiến tới kiến trúc **Multi-Tier Procedural Cache + Guarded State Transition System**.

---

# 0. Reference architecture tổng thể

Thay vì:

```text
URL
  ↓
matches_url()
  ↓
Recipe
  ↓
execute blindly
```

nên chuyển thành:

```text
                      ┌─────────────────────────────┐
                      │  L3 Curated Recipe Registry │
                      │ reviewed / trusted / shared │
                      └──────────────┬──────────────┘
                                     │ promotion
                      ┌──────────────▼──────────────┐
                      │ L2 Domain Shared Cache       │
                      │ verified across agents      │
                      └──────────────┬──────────────┘
                                     │ verification
                      ┌──────────────▼──────────────┐
                      │ L1 Agent / Local Cache       │
                      │ SQLite, learned candidates  │
                      └──────────────┬──────────────┘
                                     │ distillation
                      ┌──────────────▼──────────────┐
                      │ L0 Session Flight Recorder   │
                      │ transient action traces     │
                      └─────────────────────────────┘
```

Nhưng retrieval không nên là `key → recipe`. Nó nên là:

```text
PageContext
   │
   ├── hard partition
   │      domain / app / auth-state / route-family
   │
   ├── candidate retrieval
   │      URL + task family
   │
   ├── semantic ranking
   │      DOM / landmarks / forms / labels
   │
   └── guard evaluation
          preconditions
          risk policy
          confidence
                ↓
       executable recipe
```

Có thể coi một recipe là:

$$
R =
(K,\ P,\ A,\ Q,\ E,\ H)
$$

trong đó:

* `K`: retrieval/cache identity
* `P`: preconditions
* `A`: action program
* `Q`: postconditions
* `E`: side-effect/risk metadata
* `H`: health/reliability statistics

Đây là khác biệt căn bản giữa **browser procedural cache** và HTTP/cache dữ liệu truyền thống.

---

# 1. Cache Key & Matcher Granularity

Tài liệu hiện tại chỉ rõ vấn đề: matching đang dựa vào domain + URL regex/fnmatch, trong khi SPA có modal, stepper, filters và state thay đổi mà URL không đổi. 

Tôi đồng ý rằng URL-only key sẽ trở thành bottleneck correctness đầu tiên của Level 2.

## Không nên tạo một Composite Key duy nhất

Một sai lầm dễ mắc là tạo:

```text
hash(
  domain +
  pathname +
  title +
  h1 +
  forms +
  landmarks +
  buttons
)
```

rồi dùng equality lookup.

Điều đó sẽ quá brittle. Một banner mới, A/B test hay đổi một heading có thể làm toàn bộ hash thay đổi.

Tôi đề xuất **Hierarchical Key + Fuzzy Fingerprint**.

### Tier A — Hard partition key

Dùng để thu hẹp candidate set:

```text
SiteKey {
    registrable_domain
    app_scope
    auth_state
}
```

Ví dụ:

```text
github.com
authenticated
repo-app
```

`registrable_domain` có thể là eTLD+1 thay vì hostname tuyệt đối nếu các subdomain chia sẻ application semantics.

Auth state đáng đưa vào partition vì:

```text
/login
```

và:

```text
/dashboard
```

có thể có cùng shell nhưng recipe hợp lệ hoàn toàn khác nhau.

Không đưa username/account ID vào key nếu không thật sự cần; vừa gây fragmentation vừa tăng nguy cơ ghi dữ liệu nhạy cảm.

---

## Tier B — Route family

Không match URL literal:

```text
/orders/984142
/orders/173922
```

mà normalize thành:

```text
/orders/:id
```

Một `RouteSignature` tốt hơn có thể là:

```yaml
route:
  origin: https://example.com
  path_template: /orders/:id
  query_keys:
    - tab
    - status
  hash_route: optional
```

Query **keys** thường hữu ích hơn query values.

Ví dụ:

```text
?tab=billing
```

có thể semantic đáng kể.

Trong khi:

```text
?utm_source=...
```

nên bị loại bỏ.

---

## Tier C — Semantic Page Fingerprint

Đây mới là phần xử lý SPA.

Tạo một canonical semantic representation từ AXTree/DOM:

```text
PageSemanticSignature {
    landmarks[]
    headings[]
    forms[]
    controls[]
    dialogs[]
    stable_test_ids[]
}
```

Ví dụ:

```yaml
landmarks:
  - navigation: "Settings"
  - main

headings:
  - "Billing"
  - "Payment methods"

forms:
  - inputs:
      - email
      - card-number
    submit_label: "Save"

dialogs:
  - role: dialog
    title: null
```

Sau canonicalization:

```text
normalize
→ remove volatile values
→ sort stable fields
→ tokenize
→ fingerprint
```

Nhưng tôi sẽ lưu cả:

```text
exact_hash
simhash/minhash
anchor_set
```

chứ không chỉ một SHA hash.

### Exact hash

Dùng cho extremely fast hot path:

```text
semantic_hash == cached_hash
```

→ confidence rất cao.

### Similarity fingerprint

Nếu exact hash khác:

$$
S_{dom} = Jaccard(A_{live}, A_{recipe})
$$

hoặc weighted Jaccard.

Ví dụ:

```text
0.96 → gần như cùng state
0.84 → likely compatible
0.63 → suspicious
0.41 → reject
```

Ngưỡng thực tế phải calibrate bằng telemetry, không nên hard-code từ đầu.

---

# 1.1 Recipe matching nên là scoring, không phải Boolean

Thay vì:

```python
recipe.matches_url(url) -> bool
```

API tương lai nên gần:

```python
recipe.match(context) -> MatchResult
```

Ví dụ:

```typescript
interface MatchResult {
  score: number;
  hardPreconditionsSatisfied: boolean;
  semanticSimilarity: number;
  routeSimilarity: number;
  missingAnchors: string[];
  conflictingAnchors: string[];
}
```

Một scoring function ban đầu:

$$
C =
0.30S_{route}
+0.40S_{semantic}
+0.20S_{anchors}
+0.10S_{history}
$$

với điều kiện:

$$
HardPreconditions = true
$$

Nếu hard predicate fail thì:

$$
C = 0
$$

dù similarity cao đến đâu.

Điểm này cực kỳ quan trọng.

**Safety-critical state không được biến thành weighted score.**

Ví dụ:

```text
must_not_have(dialog="Confirm deletion")
```

không thể được bù lại bởi việc DOM giống 99%.

---

# 1.2 Overhead của semantic key

Đừng fingerprint toàn DOM.

OmniBrowser đã có semantic observation layer, vậy nên hãy tái sử dụng **AX/ObservedNode projection**:

```text
DOM
 ↓
semantic projection
 ↓
50–300 meaningful nodes
 ↓
fingerprint
```

Không cần:

```text
10,000 raw DOM nodes
```

Các thuộc tính nên bỏ:

```text
React generated IDs
CSS classes
timestamps
random data attributes
tracking IDs
element coordinates
dynamic counters
```

Các thuộc tính nên ưu tiên:

```text
role
accessible name
input type
form relationships
landmarks
heading hierarchy
stable test-id
semantic labels
```

Đây là cách đạt resilience trước non-breaking UI shifts mà câu hỏi số 1 đang nhắm tới. 

---

# 2. Cache Invalidation & Drift Detection

Tài liệu đặt đúng ba failure classes quan trọng: transient failure, structural drift và blocking modal/pop-up. 

Tôi sẽ không chọn một trong ba:

```text
TTL
failure threshold
semantic drift
```

mà dùng **multi-signal health model**.

## TTL chỉ là ageing signal

Recipe 90 ngày tuổi nhưng chạy thành công sáng nay không nên stale.

Recipe 5 phút tuổi nhưng vừa fail trên 12 agent khác nhau có thể cần quarantine ngay.

Do đó:

```text
age != validity
```

TTL nên được hiểu là:

> “bao lâu chưa được revalidated?”

không phải:

> “bao lâu thì xóa recipe?”

---

# 2.1 Recipe health score

Mỗi recipe có:

```yaml
health:
  executions: 1432
  successes: 1407
  transient_failures: 11
  drift_failures: 4
  interrupt_failures: 10

  last_success_at: ...
  last_failure_at: ...

  semantic_similarity_ewma: 0.94
  latency_p95_ms: 420

  consecutive_failures: 0
```

Có thể duy trì EWMA:

$$
R_t = \alpha x_t + (1-\alpha)R_{t-1}
$$

trong đó:

```text
x = 1 success
x = 0 failure
```

Nhưng production tốt hơn nên phân loại failure trước.

Ví dụ:

$$
Health =
w_1Reliability +
w_2SemanticStability +
w_3Recency
-w_4DriftEvidence
$$

---

# 2.2 Failure classifier

Execution engine cần một lớp:

```text
RecipeExecutor
      ↓
FailureClassifier
      ↓
┌─────────────┬──────────────┬───────────────┐
│ TRANSIENT   │ INTERRUPT    │ STRUCTURAL    │
└─────────────┴──────────────┴───────────────┘
```

### Transient

Signals:

```text
network request pending
page loading
same expected semantic anchor still exists
selector target appears after retry
HTTP/network stall
temporary renderer latency
```

Policy:

```text
bounded retry
exponential backoff
no health penalty / very small penalty
```

Không retry vô hạn.

---

### Interrupt

Sau failure, thực hiện lightweight observe.

Nếu xuất hiện:

```text
dialog
cookie banner
consent form
browser permission prompt
session-expiry overlay
```

mà underlying expected anchor vẫn tồn tại:

```text
classify = INTERRUPT
```

Sau đó:

```text
pause current recipe
→ execute interrupt handler
→ verify state restored
→ resume from checkpoint
```

Đây chính là nơi micro-recipe như:

```text
dismiss_cookie_banner
renew_session
close_marketing_modal
```

rất có giá trị.

---

### Structural drift

Signals:

```text
expected anchor absent
replacement controls appear
semantic similarity drops
same deterministic step fails across agents
retry does not change state
```

Nếu:

$$
S_{semantic} < \theta_{drift}
$$

và target anchors biến mất, classification chuyển sang drift.

Sau đó:

```text
healthy
→ suspect
→ quarantined
```

thay vì xóa ngay.

---

# 2.3 Không invalidate ngay sau một failure

Trong multi-agent environment, một agent có thể gặp:

```text
slow connection
partial render
experiment cohort
different permission
```

Nếu một failure làm global eviction, hệ thống sẽ tự phá cache.

Tôi khuyến nghị circuit-breaker semantics:

```text
ACTIVE
   │ repeated structural evidence
   ▼
SUSPECT
   │ independent confirmation
   ▼
QUARANTINED
   │ successful repair/revalidation
   ├─────────────→ ACTIVE
   │
   └─────────────→ STALE
```

“Independent confirmation” nên ưu tiên:

```text
different session
different agent
different browser run
```

hơn:

```text
3 retry trong cùng một broken page.
```

---

# 3. Micro-Recipes vs Macro-Workflows

Hiện Flight Recorder cắt boundary tại URL change, Submit/Save hoặc 8 actions. 

Đó là heuristic rất tốt cho v2.3, nhưng tôi sẽ không dùng `8 actions` như semantic unit lâu dài.

## Đơn vị cache tốt nhất là “semantic state transition”

Tức:

$$
S_i
\xrightarrow{Recipe}
S_{i+1}
$$

Recipe nên đủ ngắn để isolate drift nhưng đủ dài để amortize reasoning overhead.

Tôi sẽ gọi loại này là **meso-recipe**.

Ví dụ:

```text
open_advanced_search
fill_search_filters
submit_search
```

có thể là một recipe.

Nhưng:

```text
login
navigate reports
change account
set date range
generate report
download
logout
```

không nên là một atomic cache entry.

---

# 3.1 Macro-flow nên là graph composition

Không nên:

```text
MacroRecipe:
  action1
  action2
  ...
  action37
```

Mà:

```text
Workflow DAG

AuthenticatedDashboard
       |
       v
OpenReports
       |
       v
ReportBuilderReady
       |
       v
SetMonthlyFilters
       |
       v
FiltersReady
       |
       v
GenerateReport
       |
       v
ReportReady
       |
       v
Download
```

Mỗi edge là một guarded recipe:

```text
Precondition → Actions → Postcondition
```

Ví dụ:

```yaml
id: reports.set_monthly_filter

requires_state:
  semantic_state: report_builder_ready

actions:
  - set date_start
  - set date_end
  - choose monthly

ensures_state:
  semantic_state: monthly_filter_ready
```

Nếu edge 4 drift, không làm invalid toàn bộ workflow.

Chỉ:

```text
SetMonthlyFilters = stale
```

Các recipe trước và sau vẫn reusable.

Đây là biện pháp quan trọng nhất để tránh “domino drift” mà tài liệu hỏi tới. 

---

# 3.2 Boundary detector v2.4

Ngoài ba heuristic hiện tại, tôi sẽ thêm semantic boundary.

Một recorder segment nên đóng khi xảy ra một trong các event:

```text
URL/navigation transition
semantic page-state transition
form submission
modal open/close
authentication transition
download initiation
irreversible action
major landmark change
postcondition becomes observable
action count budget reached
```

`8 actions` nên trở thành fallback guard, không phải primary semantic segmentation rule.

---

# 4. Eviction, Promotion & Governance

Tài liệu đúng khi dự đoán hàng chục agent sẽ sinh hàng trăm draft candidate và đặt bài toán promotion, canonicalization, LFU/LRU. 

Đây là chỗ tôi cho rằng OmniBrowser nên thực sự áp dụng tư duy distributed cache.

## Lifecycle đề xuất

```text
OBSERVED_TRACE
      │
      ▼
DRAFT
      │ local replay works
      ▼
VERIFIED_LOCAL
      │ independent executions
      ▼
VERIFIED_SHARED
      │ stability + usage
      ▼
CURATED
      │
      ├──────────────┐
      ▼              │
SUSPECT              │
      │              │
      ▼              │
QUARANTINED          │
      │              │
   ┌──┴───┐          │
   │      │          │
 repair  expire      │
   │      │          │
   ▼      ▼          │
ACTIVE   ARCHIVED ◄──┘
```

Tôi sẽ không cho:

```text
DRAFT → shared fast path
```

trực tiếp.

---

# 4.1 Promotion cần evidence, không chỉ count

Ví dụ promotion policy:

```yaml
draft_to_verified_local:
  min_successes: 2
  same_session_allowed: true

verified_local_to_shared:
  min_successes: 5
  min_independent_sessions: 3
  min_semantic_similarity: 0.85
  structural_failures: 0

shared_to_curated:
  min_executions: 20
  min_independent_agents: 3
  success_rate_lower_bound: 0.95
```

Tôi đặc biệt nhấn mạnh **independent evidence**.

20 executions của một agent trong một session không mạnh bằng 5 executions trên 5 sessions.

---

# 4.2 Canonicalization / deduplication

Giả sử:

Agent A ghi:

```text
click "Advanced"
fill role=textbox name="Location"
click "Search"
```

Agent B:

```text
click button[name="Advanced"]
fill input[label="Location"]
click button[name="Search"]
```

Raw representation khác nhau nhưng procedural semantics giống nhau.

Cần hai fingerprints.

### Exact structural hash

Canonicalize:

```text
selector/ref
→ semantic locator

literal input values
→ typed parameters

volatile wait
→ normalized wait policy
```

Sau đó:

```text
SHA256(canonical_program)
```

giải quyết exact duplicate.

### Semantic recipe fingerprint

Ví dụ:

```text
transition:
 search_page
 → advanced_search_results

action_types:
 [click, fill, click]

target_semantics:
 [advanced, location, search]
```

Dùng similarity clustering cho near duplicates.

Khi cluster có nhiều variants:

```text
RecipeFamily
   ├── variant A
   ├── variant B
   └── canonical recipe
```

Không merge mù quáng.

Hai variants có thể tương ứng A/B UI thực sự khác nhau.

---

# 4.3 Multi-agent writes cần optimistic concurrency

Shared cache không nên để:

```text
agent A loads recipe
agent B updates recipe
agent A overwrites B
```

Data record nên có:

```yaml
recipe_id: ...
revision: 42
generation: 7
```

Update:

```text
UPDATE ... WHERE revision = 42
```

Nếu zero rows:

```text
CAS conflict
→ reread
→ merge telemetry
```

Action program nên immutable theo version:

```text
recipe:v17
recipe:v18
```

Telemetry mutable riêng.

Đừng mutate executable body tại chỗ.

---

# 4.4 LFU/LRU không nên quyết định semantic deletion

LRU/LFU hợp lý cho:

```text
L0/L1 materialized execution cache
compiled matchers
semantic fingerprints
local draft traces
screenshots
raw flight recorder events
```

Nhưng không nên dùng để xóa canonical procedural knowledge.

Nên tách:

```text
metadata retention
≠
hot execution cache retention
```

Một recipe ít dùng nhưng tốn hàng nghìn token để rediscover có thể vẫn cực kỳ đáng giữ.

Tốt hơn LRU thuần là utility score:

$$
U(R)=
\frac{
Frequency
\times Reliability
\times RecomputeCost
\times RecencyDecay
}{
StorageCost
}
$$

Recipe có `U` thấp mới trở thành eviction candidate.

Raw traces có thể aggressively TTL.

Canonical recipes thì archive thay vì delete.

---

# 5. Fast-Path vs Speculative Execution

Đây là phần quan trọng nhất.

Tài liệu xác định đúng trade-off: Level 2 tiết kiệm reasoning/token nhưng blind execution trên tài khoản thật có thể tạo hậu quả nghiêm trọng. 

Tôi đề xuất nguyên tắc:

> **Speculation được phép trên observation và reversible state; side-effecting execution phải đi qua write barrier.**

## Chia action thành risk classes

| Class                    | Ví dụ                          | Cached execution        |
| ------------------------ | ------------------------------ | ----------------------- |
| R0 Read-only             | observe, inspect, expand       | automatic               |
| R1 Reversible navigation | open tab, filter UI            | automatic               |
| R2 Local mutable UI      | fill draft form                | guarded                 |
| R3 Persistent mutation   | save settings, submit form     | strict verification     |
| R4 Irreversible/external | payment, delete, publish, send | explicit high-risk gate |

Đừng dựa vào action name duy nhất.

Ví dụ:

```text
click("Next")
```

có thể R1.

Nhưng:

```text
click("Confirm purchase")
```

là R4.

Risk thuộc về **semantic effect**, không thuộc về CDP primitive.

---

# 5.1 Three-phase execution protocol

Tôi đề xuất executor:

```text
VERIFY
  ↓
EXECUTE
  ↓
COMMIT VERIFY
```

### Phase 1 — Verify

Trước recipe:

```text
URL family
semantic fingerprint
required anchors
forbidden anchors
auth state
input schema
recipe health
risk policy
```

Ví dụ:

```yaml
preconditions:
  all:
    - route: /reports/*
    - role_exists:
        role: heading
        name: Reports
    - role_exists:
        role: button
        name: Generate
    - not:
        role_exists:
          role: dialog
          name: Session expired
```

Không thỏa:

```text
FAIL_CLOSED
```

Không thử “có lẽ vẫn được”.

---

### Phase 2 — Execute with checkpoints

Không execute 12 steps completely blind.

```text
action
action
checkpoint
action
checkpoint
write barrier
action
```

Checkpoint lightweight hơn full `observe`.

Ví dụ chỉ verify:

```text
anchor exists
dialog absent
expected state token present
```

---

### Phase 3 — Postcondition

Sau recipe:

```yaml
postconditions:
  any:
    - role_exists:
        role: status
        name_regex: "Saved"
    - semantic_state: report_ready
    - download_started: true
```

Nếu action chạy mà postcondition không đạt:

```text
UNKNOWN_OUTCOME
```

Điều này phải khác hẳn:

```text
FAILURE
```

Bởi vì retry trong trạng thái `UNKNOWN_OUTCOME` có thể gây duplicate side effect.

Ví dụ payment:

```text
POST /pay
timeout
```

không được tự động POST lại chỉ vì client chưa thấy success.

---

# 5.2 Cần thêm trạng thái UNKNOWN_SIDE_EFFECT

Tôi đặc biệt khuyến nghị OmniBrowser thêm explicit state:

```text
SAFE_FAILURE
UNKNOWN_EFFECT
CONFIRMED_SUCCESS
```

Ví dụ:

```text
click Submit
↓
browser disconnect
```

Hệ thống không biết submit có thành công hay không.

Đây không phải transient retry thông thường.

Policy phải là:

```text
re-observe/reconcile
→ detect persisted outcome
→ only retry if definitely not committed
```

Đây là pattern tương tự distributed systems khi không thể biết remote operation đã commit trước network failure hay chưa.

---

# 5.3 Recipe data contract tôi khuyến nghị

Một schema đủ tốt cho v2.4 có thể như sau:

```yaml
recipe:
  id: "reports.generate_monthly"
  version: 12
  family_id: "reports.generate"

  scope:
    domain: "example.com"
    route_template: "/reports/:id"
    auth_state: "authenticated"

  matcher:
    required_anchors:
      - role: heading
        name: "Reports"
      - role: button
        name: "Generate"

    forbidden_anchors:
      - role: dialog
        name: "Session expired"

    semantic_fingerprint:
      algo: "semantic-minhash-v2"
      digest: "..."
      min_similarity: 0.84

  inputs:
    year:
      type: integer
    month:
      type: integer

  preconditions:
    - state: "report_builder_ready"

  program:
    - op: fill
      target:
        role: combobox
        name: "Month"
      value: "${month}"

    - checkpoint:
        requires:
          - anchor: "Generate"

    - op: click
      target:
        role: button
        name: "Generate"
      effect: persistent

  postconditions:
    - state: "report_ready"

  safety:
    max_risk: R3
    retry_policy: before_side_effect_only
    unknown_effect_policy: reconcile
    requires_write_barrier: true

  provenance:
    source: implicit_flight_recorder
    first_agent: "..."
    created_at: "..."
    recorder_version: "2.3"

  health:
    executions: 183
    successes: 178
    structural_failures: 2
    transient_failures: 3
    independent_sessions: 41
    semantic_similarity_ewma: 0.94
    status: VERIFIED_SHARED
```

---

# 6. Fast path thực tế nên chạy như thế nào

Một invocation cuối cùng có thể là:

```text
observe()
   │
   ▼
derive PageContext
   │
   ▼
L0 session candidates
   │ miss
   ▼
L1 local candidates
   │ miss
   ▼
L2 shared candidates
   │
   ▼
rank
   │
   ├── confidence < T1
   │       → LLM
   │
   ├── T1 ≤ confidence < T2
   │       → recipe proposal / guarded execution
   │
   └── confidence ≥ T2
           ↓
      check preconditions
           ↓
      risk admission
           ↓
      execute segment
           ↓
      checkpoints
           ↓
      postcondition
           ↓
      telemetry update
```

`suggested_recipes` vì vậy không nên chỉ trả:

```json
[
  "login",
  "export_report"
]
```

mà nên trả:

```json
{
  "recipe_id": "export_report:v12",
  "match_score": 0.93,
  "health": 0.987,
  "risk": "R2",
  "match_reason": {
    "route": 1.0,
    "semantic": 0.91,
    "anchors": 1.0
  }
}
```

LLM khi đó có thể quyết định dựa trên meaningful confidence thay vì tên recipe.

---

# 7. Một điểm kiến trúc sâu hơn: Recipe Cache thực chất là learned transition model

Nếu OmniBrowser tiếp tục đúng hướng này, tôi nghĩ nên thay mental model từ:

```text
"macro cache"
```

thành:

```text
"empirically learned deterministic transition graph"
```

Các node:

$$
S_i = SemanticBrowserState
$$

Các edge:

$$
R_i = CachedTransition(S_i,S_j)
$$

và mỗi edge có:

$$
P(success | state, recipe, environment)
$$

Flight Recorder không chỉ đang ghi macro.

Nó đang **học các edge của state graph từ trajectories của agent**.

Khi nhìn theo mô hình này:

* deduplication = edge canonicalization
* drift = transition model decay
* recipe chaining = graph planning
* postcondition = destination state verification
* recipe confidence = empirical edge reliability
* invalidation = edge quarantine
* self-healing = edge regeneration
* macro workflow = path through graph

Đây theo tôi là abstraction tốt nhất cho Level 2 lâu dài.

---

# 8. Tôi sẽ thay đổi boundary hiện tại như thế nào

Tài liệu hiện đang dùng URL change, Submit/Save và giới hạn 8 actions làm Flight Recorder boundaries. 

Cho v2.4, tôi sẽ giữ chúng nhưng bổ sung **semantic transition detector**:

$$
D(S_t,S_{t+1}) > \theta
\Rightarrow boundary
$$

với:

$$
D =
w_lD_{landmark}
+w_fD_{form}
+w_hD_{heading}
+w_dD_{dialog}
+w_rD_{route}
$$

Điều này cho phép recorder tự nhận ra:

```text
step 1 → step 2
```

dù URL hoàn toàn không đổi.

Đây sẽ là bước tiến lớn hơn việc đơn thuần tăng `MAX_ACTIONS` từ 8 lên 12 hay 16.

---

# 9. Roadmap tôi đề xuất cho OmniBrowser

| Version           | Architectural objective                                       |
| ----------------- | ------------------------------------------------------------- |
| **v2.3 hiện tại** | Flight recording + domain cache                               |
| **v2.4**          | Composite matcher + semantic fingerprint + pre/postconditions |
| **v2.4.1**        | Failure taxonomy + interrupt handling + health telemetry      |
| **v2.4.2**        | Recipe versioning + CAS + canonicalization                    |
| **v2.5**          | State-transition graph + micro/meso composition               |
| **v2.5.x**        | Cross-agent promotion, quarantine, shared cache governance    |
| **v2.6**          | Automatic repair / alternate transition discovery             |

Tôi sẽ **không** triển khai sophisticated ML matcher ngay.

v2.4 chỉ cần:

```text
semantic canonicalization
+
MinHash/weighted-set similarity
+
explicit anchors
+
health telemetry
```

đã đủ tạo bước nhảy lớn về reliability.

---

# Phán quyết kiến trúc về 5 vấn đề

Với riêng Milestone v2.3, mô hình Implicit Flight Recorder + Domain-Scoped Procedural Cache là **đúng hướng**, nhưng `domain + URL matcher → recipe` chỉ nên được xem là bootstrap architecture. Chính tài liệu đã chỉ ra những limitation căn bản của URL state trên SPA và nguy cơ blind execution.  

Kiến trúc target mà tôi khuyến nghị là:

```text
Procedural Cache
=
Multi-tier storage
+
Hierarchical/Fuzzy Retrieval
+
Semantic State Model
+
Guarded Transition
+
Empirical Health
+
Lifecycle Governance
```

Trong đó ba quyết định quan trọng nhất là:

1. **Không dùng Composite Hash làm exact cache key duy nhất** — dùng hard partition + fuzzy semantic ranking.
2. **Không cache macro journey như một monolith** — cache guarded state transitions và compose chúng thành workflow graph.
3. **Không bao giờ coi cached recipe là permission để execute blind** — mọi recipe phải mang theo precondition, postcondition, risk class, checkpoint và `UNKNOWN_EFFECT` reconciliation semantics.

Nếu ba nguyên tắc này được đưa vào architecture ngay từ v2.4, OmniBrowser sẽ tránh được một điểm chết rất phổ biến của browser automation systems: **fast path càng nhanh thì càng trở nên nguy hiểm khi UI hoặc state chỉ lệch một chút**. Với thiết kế guarded-transition, ngược lại, cache càng tích lũy nhiều evidence thì hệ thống càng nhanh **và** càng biết khi nào nó không được phép tin vào cache.
