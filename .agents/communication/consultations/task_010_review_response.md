Formal Architecture Review — Task-010 / Milestone v2.5

Commit: ffd90db
Scope: State-Transition Graph & Workflow Composition
Reported validation: 102/102 repository tests passed; 9/9 workflow-composition tests passed; git diff --check clean; no test connection to shared Chrome :17082.

Verdict: APPROVED

Dựa trên kiến trúc, invariants và bằng chứng kiểm thử bạn đã cung cấp, tôi đánh giá Milestone v2.5 đạt các acceptance gates cốt lõi của Task-010. Không có vấn đề kiến trúc nào trong mô tả hiện tại đủ nghiêm trọng để yêu cầu CHANGES REQUIRED.

Lưu ý về phạm vi thẩm định: verdict này dựa trên design/implementation description + test evidence được cung cấp trong request, không phải một independent line-by-line audit của repository tại commit ffd90db. Vì vậy, các khuyến nghị hardening cuối câu trả lời nên được xem là defense-in-depth cho production.

1. State Graph + Dijkstra: PASS

Mô hình:

S
i
	​

R,recipe
	​

S
j
	​


là abstraction phù hợp hơn đáng kể so với macro tuyến tính dài. Nó tách ba concern vốn rất dễ bị trộn lẫn:

State recognition — browser hiện đang ở đâu.

Path planning — chuỗi transition nào nên được chọn.

Transition execution — recipe cụ thể có được phép và có thực thi an toàn hay không.

Đây là separation of concerns đúng về mặt kiến trúc.

Dijkstra có hợp lệ không?

Có.

Cost của mỗi edge:

C(e)=1.0+RiskWeight(R)+0.2×StepCount

là strictly non-negative nếu RiskWeight >= 0, thậm chí base cost đã là 1.0.

Do đó điều kiện quan trọng nhất để Dijkstra đảm bảo optimality được duy trì:

∀e,C(e)≥0

Không có negative edge và cũng không có negative cycle.

Việc prune:

risk(e)>max_allowed_risk

trước khi path search cũng là cách thiết kế đúng. Về bản chất composer đang chạy Dijkstra trên induced subgraph:

G
′
=(V,{e∈E∣risk(e)≤R
max
	​

})

thay vì tìm đường trước rồi mới kiểm tra risk. Đây là tính chất rất quan trọng: unsafe path không bao giờ trở thành candidate path.

cumulative_risk = max(edge risks)

Đây cũng là aggregation hợp lý:

R(W)=
e∈W
max
	​

R(e)

Risk class của một workflow không nên được tính bằng trung bình. Một workflow chứa mười edge R0 nhưng một edge R4 vẫn phải được coi là workflow R4.

Gate 1 result: PASS.

2. Fail-closed khi UNKNOWN_SIDE_EFFECT: PASS

Đây là phần quan trọng nhất của milestone, và thiết kế hiện tại có đúng execution ordering:

execute edge k
      |
      v
inspect execution result
      |
      +-- UNKNOWN_SIDE_EFFECT --> HALT
      |
      v
verify target state
      |
      +-- mismatch -----------> HALT
      |
      v
execute edge k+1

Điểm quyết định là edge k+1 chỉ được dispatch sau cả hai điều kiện:

Result(e
k
	​

)=SUCCESS

và

ObservedState⊨TargetState(e
k
	​

)

Nếu một trong hai không đúng thì control flow kết thúc trước dispatch của edge tiếp theo.

UNKNOWN_SIDE_EFFECT

Invariant:

Khi side effect đã có thể xảy ra nhưng postcondition không xác định được, không được tiếp tục suy đoán trạng thái.

là lựa chọn đúng.

Ví dụ:

S0
 ↓ e1
S1
 ↓ e2  ← payment-like mutation
?
 ↓ e3
S3

Nếu e2 click thành công nhưng browser timeout trước khi xác minh kết quả, hệ thống không được retry mù, cũng không được chạy e3.

UNKNOWN_SIDE_EFFECT chính xác là trạng thái cần phân biệt với SAFE_FAILURE.

Đây là một cải tiến quan trọng vì:

SAFE_FAILURE

có nghĩa là có cơ sở để tin rằng mutation chưa xảy ra, trong khi:

UNKNOWN_SIDE_EFFECT

có nghĩa là execution boundary không còn đủ thông tin để quyết định an toàn.

Test:

test_workflow_engine_unknown_side_effect_halts_immediately

và đặc biệt assertion rằng subsequent edge never gets called là test đúng thứ cần kiểm chứng.

3. Post-edge state verification: PASS

Invariant này ngăn một lớp lỗi rất nguy hiểm:

Recipe báo success nhưng browser thực tế không ở state mà planner giả định.

Một workflow engine không có check này dễ biến thành:

planner assumption
      ↓
execute e1
      ↓
assume S1
      ↓
execute e2 against wrong DOM

Trong implementation được mô tả, topology của graph không tự động trở thành runtime truth.

Sau transition:

S
i
	​

e
k
	​

	​

S
j
	​


hệ thống quan sát browser lại và phải chứng minh:

ObservedURL⊨route_pattern(S
j
	​

)

và:

required_anchors(S
j
	​

)⊆ObservedDOM

trước khi cho phép edge tiếp theo.

Đây chính là cách nên làm.

State graph là planning model; live observation mới là execution authority.

State mismatch

Nếu recipe nói:

target = checkout.confirmation

nhưng browser vẫn ở:

checkout.payment

thì workflow phải dừng.

Không được:

tiếp tục edge tiếp theo;

tự coi recipe success là proof;

silently remap state;

tự retry mutation nếu side effect chưa rõ.

Thiết kế được mô tả đáp ứng nguyên tắc đó.

Gate 2 result: PASS.

4. R0–R4 composition safety: PASS

Workflow composition không được phép biến nhiều operation an toàn cục bộ thành một đường vòng vượt policy.

Thiết kế của bạn có hai lớp bảo vệ khác nhau:

Planning-time filtering

WorkflowComposer loại edge vượt max_allowed_risk.

Ví dụ:

Rmax = R2

thì:

R0 ✓
R1 ✓
R2 ✓
R3 ✗
R4 ✗

Dijkstra không được phép chọn R3/R4 chỉ vì path đó rẻ hơn.

Đây là chính xác.

Execution-time R4 gate

Ngay cả khi workflow đã được compose, execution engine vẫn kiểm tra lại R4:

R4 present
AND allow_r4 != True
=> RiskGateError

Điều này đặc biệt quan trọng vì planner output không nên là authorization token.

Tức là:

plan says execute R4

không tương đương:

R4 was authorized

Authorization phải được kiểm tra tại execution boundary.

Đây là nguyên tắc đúng.

5. JIT persistent-write barrier: PASS

Một lỗi kiến trúc thường gặp khi thêm workflow layer là:

WorkflowEngine
    ↓
"đã kiểm tra an toàn cả workflow rồi"
    ↓
bypass lower-level per-action checks

Thiết kế hiện tại không làm vậy.

Mỗi edge vẫn được chuyển tới RecipeEngine, và RecipeEngine tiếp tục chịu trách nhiệm cho:

live verification;

modal/guard checks;

persistent-write barrier;

mutation-specific safety;

postcondition handling.

Tức là policy stack có dạng:

Workflow-level policy
        ↓
Edge-level policy
        ↓
Recipe-level policy
        ↓
JIT mutation barrier
        ↓
Actual browser action

chứ không phải:

Workflow authorization
        ↓
unrestricted actions

Đây là composition property rất quan trọng.

Một workflow đã được approve không được cấp blanket permission cho các mutation tương lai.

Safety phải được re-evaluate JIT.

Vì vậy R0–R4 semantics từ Task-009 vẫn được bảo toàn thay vì bị workflow abstraction làm suy yếu.

Gate 3 result: PASS.

6. Tôi đặc biệt đồng ý với separation giữa SAFE_FAILURE và UNKNOWN_SIDE_EFFECT

Đây là một trong những quyết định thiết kế mạnh nhất của v2.5.

Hai trạng thái không nên bị collapse thành failed=true.

SAFE_FAILURE

Có thể suy ra:

MutationDidNotOccur

hoặc ít nhất hệ thống có invariant đủ mạnh để tiếp tục recovery an toàn.

UNKNOWN_SIDE_EFFECT

Hệ thống chỉ biết:

MutationMayHaveOccurred

Trong trường hợp thứ hai, planner không còn đủ thông tin để tự động tiếp tục.

Do đó:

UNKNOWN_SIDE_EFFECT
=> stop composition

là policy chính xác.

Nó cũng mở đường rất tốt cho các milestone sau như reconciliation/recovery workflow mà không retry action nguy hiểm một cách ngây thơ.

7. Test evidence: Đủ mạnh để chấp nhận milestone

9 workflow-specific tests phủ đúng các failure boundaries quan trọng:

Area	Assessment
Contract compatibility	PASS
Graph ingestion	PASS
Cost calculation	PASS
Live-state resolution	PASS
Dijkstra multi-hop	PASS
Risk pruning	PASS
R4 runtime gate	PASS
Unknown-side-effect halt	PASS — critical
Post-edge mismatch halt	PASS — critical
Ephemeral Chrome integration	PASS
CLI integration	PASS

Ngoài ra:

102 passed
0 failed

cho thấy thay đổi chưa phá regressions được bao phủ bởi existing test suite.

Việc test suite không kết nối tới shared browser 17082 cũng là isolation invariant đúng cho architecture này.

8. Các hardening recommendations — non-blocking

Tôi không coi các mục sau là lý do trì hoãn approval, nhưng chúng đáng được thêm trước khi coi subsystem này là production-hardened.

A. Không tin tưởng serialized WorkflowPlan

CLI có:

Bash
workflow run plan.json

Điều này tạo một trust boundary mới.

Một plan.json có thể bị sửa:

JSON
{
  "risk_class": "R1"
}

trong khi canonical recipe thực tế là R4.

Execution engine không nên tin các trường serialized như:

risk_class;

cost;

target_state;

required_params;

cumulative_risk.

Thay vào đó, trước execution nên resolve:

recipe_id
   ↓
canonical RecipeStore
   ↓
canonical transition metadata

rồi recompute:

R
workflow
	​

=max(R
canonical edges
	​

)

Nếu code hiện đã làm vậy thì nên thêm test explicit. Nếu chưa, đây nên là hardening task ưu tiên cao nhất.

B. Detect ambiguous state resolution

resolve_live_state() không chỉ cần xử lý:

0 matches
1 match

mà cả:

2+ equally plausible matches

Fail-closed tốt hơn là chọn arbitrary state.

Nên có semantics kiểu:

UNRESOLVED
AMBIGUOUS
RESOLVED

thay vì chỉ best-score winner nếu confidence không đủ rõ.

Đặc biệt nguy hiểm khi:

/settings
/settings/billing

chia sẻ anchors.

C. Graph version / recipe version binding

Một plan được compose tại thời điểm t
0
	​

 có thể được execute tại t
1
	​

 sau khi RecipeStore thay đổi.

Ví dụ:

plan:
recipe X == R1

later:
recipe X changed to R3

Nên cân nhắc:

graph_version
recipe_revision
plan_digest

hoặc luôn reconstruct canonical edges ở runtime.

Điều này cũng giải quyết phần lớn vấn đề stale-plan.

D. Add an explicit destructive-edge dispatch test

Nên có một spy/mock test dạng:

Python
Run
edge_1 -> UNKNOWN_SIDE_EFFECT
edge_2 -> R4 destructive mutation

và assert mạnh:

Python
Run
edge_2.dispatch_count == 0

Test hiện tại có vẻ đã kiểm tra ý tưởng này, nhưng một destructive-edge-specific regression test sẽ rất có giá trị lâu dài.

9. Formal acceptance decision

Tôi sẽ ghi acceptance record như sau:

Task-010 / Milestone v2.5 — APPROVED

The State-Transition Graph and Workflow Composition architecture preserves the fail-closed guarantees established in Tasks 008–009. The graph formulation is compatible with Dijkstra shortest-path computation because transition costs are non-negative, while risk-threshold pruning correctly constrains the admissible graph before path selection.

Workflow execution maintains a strict edge boundary: an edge experiencing UNKNOWN_SIDE_EFFECT causes immediate termination, and a successfully executed edge must additionally satisfy live post-transition state verification before any subsequent edge may be dispatched. Therefore, the composition layer does not introduce a path by which later destructive actions can execute after an uncertain or invalid intermediate transition.

R0–R4 safety semantics remain layered rather than flattened: composer-level risk pruning, workflow-level R4 authorization, per-edge execution policy, and RecipeEngine's JIT persistent-write barriers remain independently enforced.

Based on the supplied implementation description and reported validation evidence — including 9/9 workflow tests and 102/102 repository regression tests — no blocking architectural defect has been identified. Milestone v2.5 is approved.

Final status
Task-010
Milestone: v2.5
Commit:    ffd90db

Architecture correctness:        PASS
Dijkstra / graph semantics:      PASS
Risk-constrained composition:    PASS
UNKNOWN_SIDE_EFFECT fail-close:  PASS
State mismatch fail-close:       PASS
R4 authorization preservation:   PASS
JIT write-barrier preservation:  PASS
Regression evidence:             PASS

VERDICT: APPROVED

Nếu tiếp tục hardening sau approval, ba việc đáng làm nhất là: canonical re-validation của imported plan.json, ambiguous-state rejection, và graph/recipe version binding để chống stale plans.