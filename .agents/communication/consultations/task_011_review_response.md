Formal Architecture Review — Task-011 / Milestone v2.5.x

Commit: 49ff210
Scope: Cross-Agent Promotion, Quarantine & Shared Cache Governance
Reported validation: 117/117 tests passed, including 12/12 governance tests; no connection to live profile :17082; git diff --check clean.

Verdict: CHANGES REQUIRED

Kiến trúc tổng thể của Task-011 là đúng hướng và phần lớn được thiết kế rất tốt. Promotion lifecycle, quarantine policy, graph exclusion và non-destructive archival đều phù hợp với mô hình procedural memory đa agent.

Tuy nhiên, dựa chính xác trên implementation contract mà bạn mô tả, tôi chưa thể phê duyệt production gate vì còn ba invariants cấp hệ thống chưa được chứng minh đầy đủ:

CAS hiện được mô tả là read revision → compare → temp write → atomic rename; chuỗi này không tự tạo ra CAS tuyến tính giữa nhiều process.

Quarantine phải chặn cả stale plans / stale graph / stale in-memory recipe đã được lấy trước thời điểm quarantine.

Promotion evidence phải được ràng buộc với recipe generation/content identity, đồng thời administrative promotion không được trở thành đường bypass im lặng của evidence policy.

Đây không phải vấn đề với triết lý của Task-011; chúng là các race/trust-boundary cần đóng trước khi tuyên bố shared governance là fail-closed.

1. Evidence-Based Promotion Lifecycle
Assessment: PASS về mô hình, CONDITIONAL PASS về enforcement

Lifecycle:

DRAFT→VERIFIED_LOCAL→VERIFIED_SHARED→CURATED

là abstraction phù hợp.

Các ngưỡng cũng có logic tốt:

Transition	Evidence
DRAFT → VERIFIED_LOCAL	≥ 2 successful replays
VERIFIED_LOCAL → VERIFIED_SHARED	≥ 5 successes, ≥ 3 sessions, 0 structural failure
VERIFIED_SHARED → CURATED	≥ 20 executions, ≥ 3 agents, reliability ≥ 0.95

Điểm quan trọng là hệ thống không đơn thuần dùng:

success_count

mà dùng evidence diversity:

∣sessions∣,∣agents∣

Điều này chống một agent duy nhất replay recipe nhiều lần rồi vô tình “chứng nhận” chính recipe của mình.

DRAFT isolation

Invariant:

DRAFT không xuất hiện trong shared suggestions.

là hoàn toàn đúng.

Nó thiết lập trust boundary rõ:

learning memory
     │
     ▼
   DRAFT
     │
     │ evidence
     ▼
VERIFIED_LOCAL
     │
     │ cross-session evidence
     ▼
VERIFIED_SHARED

Shared fast path vì vậy chỉ tiêu thụ procedural knowledge đã qua verification.

Nhưng evidence phải gắn với generation

LifecycleRecord có field generation, đây là dấu hiệu tốt, nhưng mô tả chưa xác nhận một invariant cực kỳ quan trọng.

Giả sử:

recipe generation 7
20 successful executions
→ CURATED

sau đó recipe body thay đổi đáng kể:

generation 8

Nếu generation 8 vẫn thừa hưởng:

sessions_seen
agents_seen
success_count
reliability

của generation 7 thì bằng chứng đã không còn chứng minh recipe hiện tại.

Phải có:

Evidence(R
g
	​

)

⇒Evidence(R
g+1
	​

)

với thay đổi semantic/executable content.

Tôi khuyến nghị evidence được key theo ít nhất:

recipe_id
generation
content_digest

và structural mutation phải reset promotion evidence thích hợp.

Đây là acceptance requirement, không chỉ optimization.

2. Automated Quarantine
Assessment: PASS về policy

Ba trigger hiện tại rất hợp lý.

Failure streak
consecutive_failures≥3⇒QUARANTINED

phù hợp với recipe degradation thông thường.

Persistent mutation + UNKNOWN_SIDE_EFFECT

Đây là trigger quan trọng nhất:

R∈{R3,R4}∧outcome=UNKNOWN_SIDE_EFFECT⇒QUARANTINED

Tôi đánh giá đây là policy đúng.

Một UNKNOWN side effect trên persistent operation không nên được xử lý giống một DOM selector failure thông thường.

Health degradation
health<0.60

sau số execution tối thiểu cũng hợp lý, tránh quarantine trên sample quá nhỏ.

3. Quarantine exclusion

Ba lớp protection được mô tả đều đúng:

suggest_for_url()     → exclude
StateTransitionGraph  → exclude
RecipeEngine.execute  → reject

Đây là defense-in-depth tốt.

Đặc biệt, RecipeEngine.execute() là lớp quan trọng nhất vì suggestion và graph chỉ là planning/discovery layers.

Tuy nhiên ở đây xuất hiện một concurrency invariant còn thiếu.

4. Blocking issue #1 — Stale-plan quarantine race

Xét timeline:

T0 Agent A builds graph
T1 recipe X is healthy
T2 Agent A obtains WorkflowPlan containing X

T3 Agent B executes X
T4 UNKNOWN_SIDE_EFFECT
T5 X becomes QUARANTINED

T6 Agent A executes its old WorkflowPlan

Việc:

StateTransitionGraph.ingest_recipe()

lọc quarantined recipes không giúp được plan đã tạo trước T5.

Do đó correctness phụ thuộc hoàn toàn vào:

RecipeEngine.execute(X)

tại T6.

Execution phải kiểm tra canonical current lifecycle state, không phải state nằm trong object đã cache ở T0/T2.

Invariant bắt buộc:

Lifecycle
canonical
	​

(X)=QUARANTINED⇒dispatch(X)=DENIED

bất kể:

workflow plan chứa gì;

graph snapshot chứa gì;

recipe object trong RAM chứa gì;

lifecycle lúc composition là gì.

Test hiện tại chưa chứng minh race này

test_quarantined_recipe_execution_raises_quarantined_error

là cần thiết nhưng chưa đủ nếu test tạo recipe sau khi nó đã quarantined.

Tôi yêu cầu thêm regression test kiểu:

1. Build graph.
2. Compose plan containing recipe X.
3. Keep old plan / old recipe snapshot.
4. Quarantine X.
5. Execute old plan.
6. Assert X's browser dispatch count == 0.

Đây là acceptance gate.

5. Blocking issue #2 — CAS hiện chưa được chứng minh là CAS

Mô tả hiện tại:

read disk revision → compare expected_revision → increment → write temp → rename atomically

có một race kinh điển.

Giả sử disk đang ở revision 17.

Hai process đồng thời:

Process A                    Process B

read rev 17                 read rev 17
expected = 17 ✓             expected = 17 ✓

prepare rev 18              prepare rev 18

rename A                    rename B

Kết quả:

revision = 18

nhưng cả A và B đều tin rằng transaction của mình thành công.

Một update đã bị lost.

Atomic rename chỉ đảm bảo:

reader không thấy half-written file.

Nó không đảm bảo:

compare-and-swap transaction là atomic.

Hai khái niệm khác nhau.

Formal requirement của CAS là:

Compare(current,expected)+Write(new)

phải là một atomic serialization point.

6. CAS acceptance criterion

Nếu store là local filesystem, cách đơn giản nhất:

acquire inter-process lock
    ↓
read canonical revision
    ↓
compare expected_revision
    ↓
raise CASConflictError if mismatch
    ↓
write temp
    ↓
fsync if durability required
    ↓
atomic replace
    ↓
release lock

Quan trọng là:

read+compare+increment+commit

phải nằm trong cùng critical section.

Nếu Workbench có thể chạy multi-host và shared filesystem thì filesystem locking cần được đánh giá riêng; lúc đó transactional DB/KV store với compare-and-set primitive sẽ an toàn hơn.

Test cần thêm

Không chỉ sequential:

Python
Run
save(expected_revision=4)
save(expected_revision=4)  # conflict

mà phải có race thật:

Process A ─┐
           ├── barrier ── both attempt expected_revision=N
Process B ─┘

Expected result:

exactly one writer succeeds
exactly one CASConflictError
final revision == N + 1

Không được:

two successes
final revision == N + 1

Nếu implementation hiện đã có _state_lock() / flock bao quanh toàn bộ CAS transaction nhưng phần mô tả chỉ bỏ sót chi tiết đó, issue này có thể được đóng ngay bằng code evidence + concurrency test.

7. Blocking issue #3 — Administrative promotion bypass

CLI:

Bash
recipe promote <recipe_id> --target-state ...

tạo một trust boundary rất lớn.

Câu hỏi bắt buộc phải trả lời trong code là:

Can DRAFT → CURATED be performed
without satisfying policy?

Nếu câu trả lời là có, thì cần phân biệt rõ:

policy promotion

với:

privileged governance override

Không nên có generic method:

Python
Run
promote(recipe, CURATED)

silently bypass evidence requirements.

Một override hợp lệ nên yêu cầu tối thiểu:

explicit privileged flag / authority
reason
actor identity
timestamp
previous state
new state
revision
audit record

Nếu không có privileged override, promote() phải gọi cùng PromotionPolicy như automatic promotion.

8. Restore semantics cũng cần fail-closed

Test:

test_recipe_restore_re_enables_execution

là tốt, nhưng restore không nên đồng nghĩa:

QUARANTINED
→ previous VERIFIED_SHARED

một cách tự động.

Một recipe đã bị quarantine vì:

UNKNOWN_SIDE_EFFECT on R4

không nên chỉ qua:

Bash
recipe restore

rồi lập tức quay trở lại shared fleet.

Tôi khuyến nghị:

QUARANTINED
      │
      │ explicit restore
      ▼
VERIFIED_LOCAL / SUSPECT
      │
      │ fresh evidence
      ▼
VERIFIED_SHARED

Đặc biệt nếu recipe code/content đã được sửa:

generation++

và verification evidence phải bắt đầu lại cho generation mới.

9. StateTransitionGraph exclusion
Assessment: PASS

Việc loại:

SUSPECT
QUARANTINED
ARCHIVED

ra khỏi graph là quyết định đúng.

Dijkstra khi đó chạy trên usable subgraph:

G
usable
	​

=(V,{e∣Lifecycle(e)∈AllowedStates})

Do đó planner không có khả năng cố tình chọn quarantined edge chỉ vì nó có cost thấp.

Đây là preservation tốt của safety guarantees từ Task-010.

Nhưng cần nhớ:

Graph pruning là planning-time safety, không phải execution-time authority.

Execution-time canonical check vẫn là bắt buộc.

10. U-Score eviction
Assessment: APPROVED WITH HARDENING

Formula:

U(R)=
StorageCost
Frequency×Reliability×RecomputeCost×RecencyDecay
	​


là heuristic hợp lý cho cache governance.

Nó tốt hơn LRU thuần túy vì giữ lại recipe:

được dùng thường xuyên;

đáng tin;

đắt để tái khám phá;

vẫn còn recent;

có storage efficiency tốt.

Archive thay vì delete

Đây là lựa chọn rất tốt:

ACTIVE → ARCHIVED

thay vì:

ACTIVE → deleted forever

Nó giúp:

forensic analysis;

rollback;

audit;

cache tuning;

tránh mất procedural knowledge vì heuristic sai.

Các guard nên có

Không để:

StorageCost=0

gây division error.

Nên có floor:

StorageCost
′
=max(StorageCost,ϵ)

Ngoài ra raw Frequency có thể tăng vô hạn và thống trị score. Với cache dài hạn, thường ổn hơn nếu sử dụng:

log(1+Frequency)

hoặc bounded/normalized frequency.

Đây là hardening, không phải blocking defect.

11. Lifecycle race ordering

Quarantine update cũng phải dùng chính concurrency mechanism của store.

Ví dụ:

Agent A: execution success → update health
Agent B: UNKNOWN_SIDE_EFFECT → quarantine

Outcome mong muốn không được là:

B writes QUARANTINED rev 31
A stale write rev 31 overwrites it ACTIVE

Nếu CAS thực sự linearizable, stale update của A phải fail.

Đây là lý do CAS không chỉ là storage optimization; nó trực tiếp là safety mechanism của quarantine.

Trong Task-011:

CAScorrectness⟹Quarantinecorrectness
12. Test suite assessment

12 tests hiện tại bao phủ đúng các functional cases:

Area	Status
Lifecycle serialization	PASS
Promotion thresholds	PASS
Session diversity	PASS
Agent diversity	PASS
Three-failure quarantine	PASS
UNKNOWN_SIDE_EFFECT quarantine	PASS
Health quarantine	PASS
Suggestion exclusion	PASS
Graph exclusion	PASS
Execution rejection	PASS
U-Score archive	PASS
CLI governance	PASS

117/117 regression pass cũng là tín hiệu tốt.

Nhưng đối với hệ thống Cross-Agent Governance, test suite còn thiếu các race tests quan trọng nhất.

Tôi sẽ yêu cầu thêm tối thiểu bốn cases:

Real concurrent CAS — hai processes cùng expected revision; chỉ một được commit.

Stale plan after quarantine — plan được compose trước quarantine nhưng execution sau quarantine phải bị chặn.

Generation evidence reset — recipe mutation không được thừa hưởng promotion evidence của generation cũ.

Restore revalidation — restored R3/R4 recipe không tự quay lại shared/curated eligibility mà chưa có fresh evidence.

Formal Gate Assessment
Acceptance Gate	Verdict
Lifecycle state model	PASS
Draft isolation	PASS
Independent session evidence	PASS
Independent agent evidence	PASS
Auto-quarantine policy	PASS
R3/R4 UNKNOWN_SIDE_EFFECT quarantine	PASS
Suggestion exclusion	PASS
Graph exclusion	PASS
Execution-time quarantine concept	PASS, race proof required
CAS sequential conflict detection	PASS
CAS true multi-process atomicity	NOT ESTABLISHED
U-Score model	PASS
Non-destructive archive	PASS
Generation-bound evidence	NOT ESTABLISHED
Administrative promotion safety	NOT ESTABLISHED
Formal Review Verdict

Task-011 / Milestone v2.5.x — CHANGES REQUIRED

The governance architecture is fundamentally sound: lifecycle-based trust promotion, automatic quarantine, shared-path exclusion, workflow graph pruning, and non-destructive U-Score archival are appropriate mechanisms for a multi-agent procedural cache.

However, production approval requires stronger guarantees at the concurrency and trust boundaries. In particular, optimistic concurrency must provide a true atomic compare-and-swap serialization point across competing writers; execution must revalidate the canonical lifecycle state so that recipes quarantined after graph construction or plan composition cannot be dispatched from stale snapshots; and promotion evidence must be scoped to the executable recipe generation/content identity rather than inherited across materially changed revisions.

Administrative promotion and restoration must also preserve these trust guarantees or be implemented as explicit privileged, auditable overrides.

The reported 117/117 passing tests demonstrate strong functional coverage, but they do not yet establish these cross-agent race invariants from the evidence supplied.

Required criteria for approval
1. CAS LINEARIZABILITY
   read + compare + revision increment + commit
   occur under one serialization mechanism.

2. REAL CONCURRENCY TEST
   Two concurrent writers using revision N:
   exactly one succeeds;
   exactly one receives CASConflictError;
   final revision = N + 1.

3. STALE-PLAN QUARANTINE TEST
   A recipe quarantined after graph/plan creation
   must never reach browser dispatch.

4. CANONICAL EXECUTION CHECK
   RecipeEngine must check current authoritative
   lifecycle/revision, not trust stale plan/object state.

5. GENERATION-BOUND EVIDENCE
   Promotion evidence must belong to the current
   recipe generation/content identity.

6. SAFE RESTORE/PROMOTION
   Restore must not silently regain shared trust;
   policy promotion cannot be bypassed except through
   an explicit privileged and auditable override.

Sau khi sáu điều kiện này được chứng minh bằng code và regression tests, kiến trúc Task-011 theo tôi sẽ đủ điều kiện chuyển sang APPROVED mà không cần redesign lớn.

Ba bước tiếp theo có giá trị nhất là: tôi có thể thiết kế chính xác bộ concurrency tests cho CAS/quarantine race, đề xuất state-machine chuẩn cho restore/re-promotion, hoặc review trực tiếp diff của recipes.py, contracts.py và workflows.py nếu bạn gửi code của commit 49ff210.