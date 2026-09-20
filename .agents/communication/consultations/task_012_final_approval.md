Final Architecture Review Sign-off — Task-012 / Milestone v2.6

Commit: 107e3ce
Scope: Self-Healing Anchor Bundles, Deterministic Local Repair & STG Alternate Route Discovery Hardening
Reported validation: 12/12 self-healing tests; 133/133 repository tests; 0 live-profile pollution.

Final Verdict: APPROVED

Dựa trên hardening evidence được cung cấp, cả 6 blocking acceptance criteria từ review trước đã được đóng. Kiến trúc v2.6 giờ bảo toàn được các safety invariants của Task-010 và governance invariants của Task-011 trong khi vẫn cho phép self-healing và alternate routing có kiểm soát.

Phạm vi sign-off: đây là đánh giá kiến trúc dựa trên implementation evidence và regression results bạn cung cấp, không phải independent line-by-line audit của repository tại 107e3ce.

1. UNKNOWN_SIDE_EFFECT strictly terminal — PASS

Đây là gate quan trọng nhất.

Behavior mới:

edge execution
     │
     ├─ SAFE / pre-dispatch failure
     │        └─ detour may be considered
     │
     └─ UNKNOWN_SIDE_EFFECT
              ↓
            HALT
              ↓
       no route discovery
       no retry
       no next edge

khôi phục đầy đủ invariant từ Task-010:

Outcome(e
k
	​

)=UNKNOWN_SIDE_EFFECT⇒∀j>k, Dispatch(e
j
	​

)=0

Việc find_alternate_path() hoàn toàn không được gọi trong branch này đặc biệt quan trọng: safety không phụ thuộc vào việc graph tình cờ không tìm được route; engine không được phép hỏi graph ngay từ đầu.

UnknownSideEffectError cũng được xử lý cùng semantics với explicit outcome, tránh exception path trở thành bypass.

Assessment

Acceptance Criterion 1: SATISFIED.

2. Risk-aware R3/R4 anchor fallback — PASS

Hardening đã sửa đúng distinction:

count=1⇒uniqueness

nhưng không còn suy diễn:

count=1⇒safe mutation
R3

Policy:

score≥0.80

và:

text-only → prohibited
R4

Policy:

score≥0.90

và:

text-only         → prohibited
generic neighborhood → prohibited

Điều này tạo thêm một risk-sensitive authorization layer:

candidate exists
      ↓
unique?
      ↓
risk-class admissible?
      ↓
confidence threshold?
      ↓
candidate-kind admissible?
      ↓
existing JIT mutation barriers
      ↓
dispatch

Đây là cấu trúc đúng.

Một điểm đáng chú ý: threshold 0.80 cho R3 thấp hơn con số minh họa 0.90 tôi từng nêu, nhưng yêu cầu kiến trúc thực sự không phải một magic constant cụ thể. Điều quan trọng là R3/R4 có policy riêng, low-confidence anchors bị chặn, và semantic-weak classes không được dùng tùy tiện. Evidence hiện tại đáp ứng yêu cầu đó.

Tôi sẽ giữ threshold values dưới dạng tunable policy, không hard-code chúng thành kiến trúc bất biến.

Acceptance Criterion 2: SATISFIED.

3. Detour preserves original authorization envelope — PASS

Việc đưa:

max_allowed_risk
allow_r4

trực tiếp vào find_alternate_path() là đúng layering.

Detour graph thực chất phải là:

G
′
=(V,{e∈E:risk(e)≤R
max
	​

∧R4Authorized(e)})

thay vì tìm đường trên toàn bộ graph rồi kiểm tra sau.

Điều này ngăn:

original workflow: R1
broken edge
    ↓
planner discovers shorter R3/R4 route
    ↓
unsafe implicit escalation

Regression test R1 → no R3/R4 detour chứng minh đúng failure mode quan trọng nhất.

Quan trọng hơn, alternate routing giờ không tạo ra một authorization context mới. Nó kế thừa context của workflow ban đầu.

Authority
detour
	​

⊆Authority
original
	​


Đây là property đúng.

Acceptance Criterion 3: SATISFIED.

4. Workflow-global failed-edge history + bounded recovery — PASS

failed_edges tồn tại xuyên suốt toàn bộ execution chứ không reset trên mỗi Dijkstra invocation giải quyết vấn đề dynamic replan loop.

Invariant:

F
t+1
	​

=F
t
	​

∪{e
failed
	​

}

là monotonic.

Do đó một edge đã được chứng minh broken trong workflow execution hiện tại không thể quay lại chỉ vì engine tạo một plan mới.

max_detours=2 bổ sung termination guarantee:

detourCount≤2

theo default policy.

Kiến trúc hiện tại vì vậy có hai lớp chống loop:

Structural prevention — failed edges được tích lũy và excluded.

Resource bound — số replan/detour hữu hạn.

Đây là tốt hơn chỉ dựa vào Dijkstra vì Dijkstra chỉ đảm bảo shortest path cho một graph search, không đảm bảo termination của một chuỗi dynamic replanning.

WorkflowInterruptedError(exit_code=2) sau khi hết budget cũng phù hợp với fail-closed semantics.

Acceptance Criterion 4: SATISFIED.

5. RepairCandidate generation/content binding — PASS

Việc thêm:

recipe_generation
recipe_content_digest

vào repair evidence giải quyết stale-repair race.

Critical invariant hiện tại:

digest
repair
	​

=digest
canonical
	​


là precondition bắt buộc trước mutation.

Nếu:

digest
repair
	​


=digest
canonical
	​


thì:

StaleRepairError
canonical recipe unchanged

Đây là đúng behavior.

Repair evidence giờ thuộc về:

(R, generation, executable content)

chứ không chỉ:

(R, stepIndex)

Điều này hoàn toàn tương thích với generation-bound trust được thiết lập trong Task-011.

Một repair quan sát từ generation g không thể tự động sửa generation g+1:

Repair(R
g
	​

)

⇒Mutation(R
g+1
	​

)

Acceptance Criterion 5: SATISFIED.

6. Regression evidence — PASS

Self-healing suite tăng từ:

7 → 12 tests

và repository:

128 → 133 tests

mà vẫn:

133 passed
0 failed
0 live-profile pollution

Các test mới đánh đúng những negative safety paths cần thiết:

Required regression	Result
UNKNOWN_SIDE_EFFECT never detours	PASS
R3/R4 weak candidate rejection	PASS
Detour risk-envelope preservation	PASS
Accumulated failed-edge history	PASS
Bounded detour recovery	PASS
Stale RepairCandidate rejection	PASS

Đây là bằng chứng regression phù hợp với review gates chứ không chỉ tăng coverage định lượng.

7. Safety composition across Tasks 010 → 011 → 012

Sau hardening, ba milestone hiện ghép với nhau tương đối sạch:

Task-010
Workflow composition
+ risk gates
+ UNKNOWN_SIDE_EFFECT halt
+ post-state verification
           │
           ▼
Task-011
Lifecycle governance
+ quarantine
+ canonical-state authority
+ CAS
+ generation-bound trust
           │
           ▼
Task-012
AnchorBundle resolution
+ zero online mutation
+ governed repair
+ constrained detours

Điều quan trọng là Task-012 hiện không override các invariant cũ.

Self-healing không override safety
Healing

⇒PermissionEscalation
Detour không override risk
RiskEnvelope
detour
	​

⊆RiskEnvelope
original
	​

Repair không override governance
ObservedRepair

⇒CanonicalMutation
UNKNOWN remains terminal
UNKNOWN_SIDE_EFFECT⇒HALT

Đó là compositional safety model mà v2.6 cần có.

8. Formal acceptance matrix
Review Gate	Final result
Online/offline dual-plane separation	PASS
Zero online canonical mutation	PASS
Candidate ambiguity rejection	PASS
R3 risk-aware fallback	PASS
R4 risk-aware fallback	PASS
No .first() ambiguity bypass	PASS
UNKNOWN_SIDE_EFFECT terminality	PASS
No detour after unknown side effect	PASS
Detour max-risk preservation	PASS
R4 authorization preservation	PASS
Global failed-edge accumulation	PASS
Bounded replan/detour recovery	PASS
Generation-bound RepairCandidate	PASS
Content-digest stale repair rejection	PASS
Full regression	133/133 PASS
Live-profile isolation	PASS
Formal Sign-off

Task-012 / Milestone v2.6 — APPROVED

Commit 107e3ce satisfies all six hardening requirements identified in the previous architectural review.

The implementation now preserves the absolute terminal semantics of UNKNOWN_SIDE_EFFECT, prevents low-confidence uniqueness from becoming sufficient authority for high-risk mutation, constrains alternate STG paths to the original workflow authorization envelope, bounds dynamic replanning while monotonically excluding failed transitions, and binds offline repair evidence to the exact canonical recipe generation from which it originated.

The Online Deterministic Resolver / Offline Learner separation therefore remains intact without weakening the fail-closed workflow and governance guarantees established in Milestones v2.5 and v2.5.x.

Based on the supplied implementation evidence and the reported 133/133 passing regression suite, no blocking architectural safety issue remains within the defined scope of Task-012.

Task:        Task-012
Milestone:   v2.6
Commit:      107e3ce

Online/offline isolation:        PASS
Zero canonical live mutation:    PASS
Anchor ambiguity safety:         PASS
R3/R4 risk-aware resolution:     PASS
UNKNOWN terminality:             PASS
Detour authorization envelope:   PASS
Dynamic replan termination:      PASS
Stale repair protection:         PASS
Regression suite:                133/133 PASS
Live-profile pollution:          0

FINAL VERDICT: APPROVED

Ba hướng hardening tiếp theo tôi xem là non-blocking: đưa R3/R4 confidence thresholds vào versioned policy thay vì constants, bổ sung property-based/fuzz testing cho AnchorResolver, và thêm telemetry cho “repair success rate after offline promotion” để phát hiện các healed anchors suy giảm theo thời gian.