Tôi đã đối chiếu RFC với SKILL.md, recipes.py, engine.py, CLI và recipe mẫu, đồng thời kiểm tra lại tài liệu hiện tại của Stagehand, Skyvern, Playwright và UiPath. Kết luận chính: hướng Learn → Verify → Promote → Replay là khả thi và đúng, nhưng tôi sẽ không triển khai Recorder theo kiểu “ghi lại raw browser events rồi sanitize sau”. Với OmniBrowser, kiến trúc bền hơn là record semantic intentions + state transitions tại biên act(), không record raw data.

RFC đã xác định đúng các lỗ hổng cốt lõi: act() không journal, distill_recipe() đứng riêng lẻ, ledger chỉ ở RAM, selector hiện tại chưa có compiler và CLI chưa có lifecycle học recipe. 

procedural_memory_rfc

1. Những vấn đề tôi sẽ sửa trước khi xây Recorder

Có vài issue hiện hữu quan trọng hơn cả Anchor Compiler.

Mức	Vấn đề	Tác động
P0	url_changed lấy initial_url quá muộn	Navigation postcondition có thể sai
P0	Selector path dùng .first	Ambiguous selector có thể click nhầm
P0	Ref-actions và selector-actions có hai cơ chế postcondition khác nhau	Recipe replay không có semantics đồng nhất
P0	Sanitization là blacklist regex	Không thể bảo đảm không rò secret/PII
P0	Auto-generated recipe hiện có thể chứa eval	Candidate recipe trở thành arbitrary JS execution surface
P1	Recipe actions không hỗ trợ đầy đủ check, press như act()	Trace thực tế không round-trip được
P1	Ledger nằm trong metadata của canonical recipe	Sai boundary giữa immutable recipe và runtime telemetry
P1	Sample “copy last response” dùng selector có khả năng match nhiều nút	.first có thể chọn response đầu thay vì cuối
P1	SKILL.md và RFC dùng hai cách đánh số pyramid khác nhau	Agent có thể suy luận sai tầng capability

engine.act() snapshot trước action, nhưng khi polling _expectation_met() lại truyền initial_url=page.url tại thời điểm sau action. Nếu click đã đổi URL, giá trị “initial” đã là URL mới. 

engine

Ngoài ra, opaque-ref path gọi act() với polling expectation, trong khi selector path trong RecipeEngine gọi trực tiếp locator.click()/fill() rồi _check_expectation(); text_present ở đây là một lần đọc body chứ không phải watcher theo thời gian. 

recipes

Tôi sẽ hợp nhất thành:

RecipeStep
   ↓
AnchorResolver
   ↓
Resolved Locator / Ref
   ↓
Unified ActionExecutor
   ↓
PostconditionWatcher
   ↓
StateDelta + TraceEvent

Không nên có “fast recipe executor” và “cognitive act executor” với semantics khác nhau.

Recipe mẫu cũng minh họa một edge case tốt: nó mô tả “Copy last response”, nhưng target chỉ là [data-testid='copy-turn-action-button']; nếu trang có nhiều response và engine dùng .first, recipe có thể nhấn copy của phần tử đầu tiên. Hơn nữa step và recipe đều không có postcondition. 

copy_response

 Điều này mâu thuẫn trực tiếp với invariant trong SKILL.md rằng action phải kiểm chứng postcondition. 

SKILL

2. Recorder: đừng record “events”, hãy record “semantic action transactions”

Đây là thay đổi kiến trúc quan trọng nhất tôi đề xuất.

Không nên bắt đầu bằng DOM event listener

Nếu gắn pointerdown, click, input, change, keydown vào trang rồi cố suy ra recipe, bạn sẽ gặp rất nhiều noise:

React/Vue rerender có thể detach node giữa pointerdown và click; Shadow DOM làm event retargeting; input framework có controlled state; một thao tác chọn autocomplete có thể sinh 10–30 events; password managers/autofill có semantics khác keyboard; cross-origin iframe cần instrument từng frame; page script có thể tự dispatch event mà không phải hành vi user.

OmniBrowser đã có lợi thế lớn: agent đi qua act(). Vì vậy Recorder nên hook chính tại đó:

Python
Run
with learning_run.action(
    op="fill",
    ref=dom_ref,
    runtime_value=value,       # RAM only
) as tx:
    tx.capture_target_before_action(handle)
    result = execute_action(...)
    tx.complete(
        result=result,
        post_state=...,
    )

Persisted event không chứa raw value:

JSON
{
  "run_id": "lr_...",
  "seq": 17,
  "action": "fill",
  "target": {
    "role": "textbox",
    "anchor_bundle_id": "a_018"
  },
  "value_ref": {
    "kind": "param",
    "name": "email",
    "sensitivity": "pii"
  },
  "transition": {
    "revision_changed": true
  },
  "duration_ms": 83
}

Nếu sau này muốn học từ con người thao tác trực tiếp, hãy làm đó thành Recorder loại thứ hai (source=human) thay vì trộn với agent recorder.

Dynamic SPA

React/Vue/Next không làm Selector Ladder vô dụng, nhưng khiến ElementHandle không thích hợp để trở thành identity lâu dài.

backendNodeId, opaque ref hoặc ElementHandle chỉ nên dùng để correlate trong current document epoch. Anchor phải được compile trước action, khi handle còn sống, nhưng recipe chỉ lưu descriptor.

Hydration là trường hợp đặc biệt: node nhìn thấy lúc T=0 có thể bị framework thay hẳn ở T=50 ms. Playwright locators có actionability/retry tốt hơn ElementHandle cố định; locator click kiểm tra visible, stable, receives-events và enabled trước khi tương tác. 
Playwright
+1

Vì vậy replay Tier 2 nên trở thành locator-centric, còn opaque ref là cognitive-session identity.

Modal, popup, new tab

Recipe step cần browser-context transition, không chỉ target:

JSON
{
  "action": "click",
  "target": {...},
  "expect": {
    "page_transition": "popup",
    "origin": "accounts.example.com"
  }
}

Tôi sẽ model riêng:

same-document
same-page-navigation
new-page/popup
frame-transition
download
file-chooser
js-dialog

Cookie banners, chat widgets và promo modals nên là interrupt handlers, không tự động distill thành business recipe.

iframe

Anchor nên có hai ladders:

FrameLocator Ladder
        ↓
Element Anchor Ladder

Không lưu frame[2]. Lưu semantic frame descriptor như origin, name, title, iframe stable attributes và parent context.

Stagehand hiện coi nested/out-of-process iframe là primitive quan trọng và có deepLocator; tài liệu của họ cũng khuyên lưu iframe selectors và giữ hop chain ngắn. 
GitHub
+1

3. User action vs synthetic event

Phân biệt này thực sự quan trọng cho Recorder.

locator.click() của Playwright không tương đương element.dispatchEvent("click"). Locator click chạy actionability, scrolls target vào view và dùng page.mouse để thực hiện click. Ngược lại dispatchEvent() tạo DOM event programmatically, bỏ qua visibility/actionability; MDN cũng ghi nhận event từ dispatchEvent() là isTrusted=false. 
Playwright
+2
Playwright
+2

Với text cũng vậy. fill() focus element, đặt giá trị và phát input; nó không sinh chuỗi keydown/keypress/keyup cho từng ký tự. pressSequentially() mới phù hợp với web app có key handlers, autocomplete, masked input hoặc editor đặc biệt. 
Playwright
+1

Do đó RecipeStep nên giữ interaction modality:

JSON
{
  "action": "input_text",
  "mode": "fill",
  ...
}

hoặc:

JSON
{
  "action": "input_text",
  "mode": "keyboard",
  ...
}

Đừng distill fill() rồi replay bằng CDP Input.insertText và giả định hai thứ tương đương.

Tôi cũng sẽ sửa wording trong SKILL.md: “Zero Anti-Bot Triggering” là một invariant quá mạnh. Kết nối vào real Chrome profile qua CDP giảm một số dấu hiệu automation, nhưng không tạo ra bảo đảm “undetectable”; behavior, timing và nhiều browser/server signals khác vẫn có thể phân biệt automation.

4. Anchor Compiler: không phải một ladder tuyến tính, mà là scored candidate bundle

RFC đề xuất test-id → semantic tuple → stable ID → scoped CSS. 

procedural_memory_rfc

 Hướng đó tốt, nhưng tôi sẽ không lưu duy nhất selector thắng cuộc.

Tôi đề xuất mỗi target chứa 3–6 candidates:

JSON
{
  "target": {
    "role": "button",
    "candidates": [
      {
        "kind": "test_attr",
        "selector": "[data-testid='submit']",
        "score": 0.97
      },
      {
        "kind": "role_name",
        "role": "button",
        "name": "Submit",
        "score": 0.91
      },
      {
        "kind": "scoped_css",
        "selector": "form[action='/apply'] button[type='submit']",
        "score": 0.84
      }
    ]
  }
}

Playwright Codegen hiện cũng sinh locator bằng cách phân tích rendered page, ưu tiên role, text và test-id rồi refinement nếu locator không unique. 
Playwright
+1

Scoring tôi sẽ dùng

Thay vì hard-code “testid luôn thắng”, tính:

confidence =
    0.30 * uniqueness
  + 0.25 * observed_stability
  + 0.20 * semantic_strength
  + 0.10 * scope_quality
  + 0.10 * historical_success
  + 0.05 * actionability
  - dynamic_penalties

uniqueness phải là runtime property. Một data-testid xuất hiện 12 lần không đáng 0.95.

Base prior hợp lý:

Anchor	Base prior
Unique data-testid/data-qa/data-cy	0.95
role + accessible-name unique	0.91
label + input name/autocomplete/type	0.91
ID đã chứng minh ổn định qua nhiều snapshots/runs	0.90
Link role/name + normalized href route	0.86
Stable scoped CSS	0.78
Neighborhood/relative anchor	0.70
Text-only locator	0.60
positional selector / nth-child	không promote

ID chưa được quan sát qua nhiều run chỉ nên khoảng 0.7 vì SPA thường generate IDs.

Query performance

Fast path không nên fuzzy-search toàn DOM ngay lập tức.

1. Cheap exact CSS candidates
2. Unique semantic candidate
3. Scoped/contextual candidate
4. Neighborhood fingerprint
5. Cognitive repair

Ở mỗi candidate, chạy count() và descriptor validation. Không dùng .first cho mutating action.

Ambiguity phải là error:

Python
Run
n = locator.count()

if n == 0:
    raise AnchorNotFound(...)
if n > 1:
    raise AnchorAmbiguous(...)

.first chỉ hợp lý khi recipe chủ động encode ordinal semantics, ví dụ first_search_result.

5. Có nên lưu DOM neighborhood fingerprint?

Có, nhưng nó phải là secondary evidence chứ không phải primary selector.

UiPath là bằng chứng thực tế khá mạnh cho pattern này: Modern UI Automation kết hợp strict/fuzzy selectors với anchors; tài liệu của UiPath khuyến nghị nearby anchors khi target không unique, thậm chí nhiều anchors để phân biệt các field giống nhau. 
UiPath Documentation
+1

Tôi sẽ lưu semantic neighborhood tối đa 1–2 hops:

nearest landmark/form
nearest heading
associated label
parent role
stable sibling roles
section identity

Không lưu raw outerHTML.

Ví dụ:

JSON
"context": {
  "landmark": {"role": "form"},
  "heading": {"name_token": "application"},
  "label": {"name_token": "email-address"},
  "siblings": ["textbox", "combobox", "button"]
}

Điều quan trọng là neighborhood cũng có thể chứa PII. Một heading có thể là “John Smith's Profile”. Vì vậy fingerprint pipeline cũng phải qua privacy boundary.

6. PII: typed placeholder hiện tại chưa đủ

Đây là chỗ RFC cần mạnh hơn đáng kể.

sanitize_value() hiện chỉ nhận diện email, Bearer, JWT và field name có các từ như password/token/secret/api_key. 

recipes

Nó sẽ không bắt chắc chắn:

phone numbers
addresses
names
credit card / bank details
OTP
OAuth authorization_code
refresh token không có dạng JWT
GitHub/PAT/provider-specific secret formats
session IDs
cookies
CSRF tokens
query-string credentials
PII nằm trong visible DOM text
PII nằm trong error message

Tệ hơn, distill_recipe() truyền validation nguyên vẹn, không qua sanitize_value(). Description/metadata/domain pattern cũng có thể trở thành leakage surface.

Và nếu Recorder lưu ActResult.delta, _delta() hiện serialize cả node value; sau một password/email fill, DOM snapshot có thể chứa chính giá trị đó. 

engine

Muốn gần với yêu cầu “không bao giờ persist secret”, hãy đổi nguyên lý

Không phải:

raw value → sanitizer → DB

mà phải là:

runtime value
     ├── RAM-only resolver
     └── classification → placeholder → persistence

Raw value không bao giờ đi vào object có khả năng serialize.

Tôi sẽ dùng structured ref thay vì magic string:

JSON
"value": {
  "$ref": "runtime_param",
  "name": "account_password",
  "sensitivity": "secret",
  "persist_value": false
}

Không dùng {{secret:password}} như một mini-language. Trong code hiện tại _TEMPLATE chỉ parse identifier kiểu {{foo}}; syntax có dấu : thậm chí không được substitution engine hiện tại hiểu. 

recipes

Một policy tốt cho distilled recipes là:

mọi fill value mặc định trở thành parameter, trừ khi compiler chứng minh đó là static UI constant như "United Kingdom" hoặc "JSON".

URL cũng cần sanitize trước persistence:

https://site.com/oauth/callback?code=SECRET&state=XYZ

persist thành:

origin = https://site.com
path   = /oauth/callback
query_keys = ["code", "state"]

không persist query values.

Authorization, Cookie, Set-Cookie, request/response bodies nên default-deny. OWASP cũng khuyến nghị không log trực tiếp access tokens, passwords, session identifiers và sensitive PII. 
OWASP Cheat Sheet Series

Và đừng quên traces.db-wal: nếu raw secret từng được INSERT rồi DELETE, WAL có thể vẫn chứa nó. “Sanitize sau khi insert” không đáp ứng yêu cầu.

7. Self-Healing: online resolver, offline learner

Tôi không chọn hoàn toàn online hoặc hoàn toàn offline.

Tôi sẽ chia thành:

ONLINE
selector ladder
    ↓ fail
deterministic local repair
    ↓ fail
Level-1 semantic rediscovery
    ↓
perform current action only if verified
    ↓
write RepairCandidate

OFFLINE / PROMOTION PIPELINE
RepairCandidate
    ↓ replay tests
cross-run validation
    ↓
new recipe version

Canonical recipe không bao giờ tự mutate trong online session.

Stagehand hiện có self-healing primitives và đề xuất agent fallback khi một single-step action biến thành multi-step flow. Đây là pattern tương tự nhưng OmniBrowser có thể làm strict hơn bằng cách tách “complete current task” khỏi “rewrite procedural memory”. 
GitHub
+1

Phân biệt UI drift nhỏ với business-flow change

Đừng đo chỉ selector similarity. Đo state transition.

Ví dụ:

BEFORE
route=/application
form=job-application

ACTION
intent=submit-form

AFTER
route≈/application/*
confirmation="Application submitted"

Nếu "Submit" → "Send" nhưng transition contract vẫn y hệt: minor UI drift.

Nếu sau click xuất hiện:

mandatory identity verification
new payment page
new required questionnaire
new OAuth origin
new legal confirmation

thì đó là flow change.

Tôi sẽ dùng ba vùng:

confidence >= .92
→ auto-resolve online

.75 <= confidence < .92
→ execute only after strong postcondition/preflight;
   create repair candidate

confidence < .75
or state-transition mismatch
→ Level 3 fallback; mark recipe degraded

Với action có side effects cao như submit application, send message, purchase, delete, grant permission, tôi sẽ đặt threshold cao hơn và không cho LLM fuzzy-repair target rồi click ngay chỉ vì target similarity tốt.

8. Postconditions cần trở thành “state contracts”

Hiện recipe schema tập trung quá nhiều vào:

action + target + value + expect

Tôi sẽ nâng thành:

precondition
action intent
target anchor bundle
interaction modality
postcondition
risk class

Ví dụ:

JSON
{
  "action": "click",
  "intent": "submit_application",
  "risk": "external_write",

  "pre": {
    "route": "/jobs/*/apply",
    "target_enabled": true
  },

  "target": {
    "$anchor": "submit_application_button"
  },

  "post": {
    "one_of": [
      {"url_matches": "/applications/*"},
      {"role_name_visible": ["status", "Application submitted"]}
    ]
  }
}

Recipe nên được xem như một small deterministic state machine, không phải macro recorder.

Tuy nhiên tôi sẽ cố ý giữ control flow nhỏ. optional, guard, one_of, retry là hợp lý; loops/planning/complex branching nên đẩy lên Level 3.

9. eval nên bị loại khỏi learned recipes

Current Recipe.validate() cho phép "eval", và _execute_step() thực hiện trực tiếp page.evaluate(str(value)). 

recipes +1

Nếu một candidate được LLM distill, đây là security boundary nguy hiểm.

Tôi sẽ quy định:

recorded/promoted automatic recipe:
    eval = forbidden

manually authored trusted recipe:
    eval = optionally allowed

signed/curated recipe:
    potentially allowed under policy

Nếu Tier 2 cần capability mới, hãy thêm primitive typed như:

scroll
press
check
upload
download
clipboard_read
clipboard_write

thay vì escape hatch arbitrary JS.

10. So với các hệ thống hiện tại
Hệ thống	OmniBrowser nên học	Nên tránh
Stagehand	Hybrid deterministic/AI; action caching; self-heal; agent fallback; token-efficient accessibility context; deep iframe locator	Không để self-healing trở thành implicit canonical mutation
Skyvern	Rich per-run artifacts, element tree, frame maps, selector maps, replay/debug visibility	Không persist toàn bộ artifacts/raw HTML/network mặc định trong memory store
Playwright Codegen	Recorder UX, locator generation, uniqueness refinement, semantic locators, auto-wait/actionability	Không học selector thành .first; positional selectors là cuối cùng
UiPath	Target+anchor descriptors, fuzzy matching và multi-anchor contextual identification	Fuzzy match không được phép trở thành “best effort click” cho risky actions

Stagehand hiện nhấn mạnh self-healing, accessibility-tree trimming và support cho complex DOM/iframes; họ cũng khuyến nghị observe() một lần rồi execute các discovered actions mà không cần inference lại, khá gần với triết lý Tier 1 → Tier 2 của OmniBrowser. 
GitHub
+1

Skyvern lưu các artifact như visible element tree, CSS map, frame map, XPath map, HTML và network/debug artifacts. Đây là pattern rất tốt cho diagnosis, nhưng OmniBrowser nên phân biệt debug artifacts có retention policy riêng với procedural memory tối giản. 
GitHub

UiPath cho thấy neighborhood anchor không phải ý tưởng “LLM-specific”; đây là pattern automation trưởng thành. Họ cũng dùng fuzzy selector + anchor và thậm chí nhiều anchors khi UI có các target giống nhau. 
UiPath Documentation
+1

11. Target architecture tôi khuyến nghị

Tôi sẽ điều chỉnh RFC thành:

                    ┌──────────────────┐
                    │  OmniBrowser act │
                    └────────┬─────────┘
                             │
                  resolve live target
                             │
                    ┌────────▼─────────┐
                    │ Anchor Compiler  │
                    │ BEFORE action    │
                    └────────┬─────────┘
                             │
              ┌──────────────▼───────────────┐
              │ Sensitive Data Boundary      │
              │ raw values NEVER cross here │
              └──────────────┬───────────────┘
                             │
                     Semantic Trace
                             │
                    ┌────────▼────────┐
                    │ SQLite WAL      │
                    │ append-only     │
                    └────────┬────────┘
                             │ successful run
                    ┌────────▼────────┐
                    │ Distiller       │
                    │ parameterize    │
                    │ deduplicate     │
                    └────────┬────────┘
                             │
                      Draft Recipe v2
                             │
                    ┌────────▼────────┐
                    │ Replay verifier │
                    │ strict locators │
                    │ state contracts │
                    └────────┬────────┘
                             │
                       Candidate Store
                             │ approval /
                             │ policy gate
                    ┌────────▼────────┐
                    │ Canonical Store │
                    │ immutable ver.  │
                    └────────┬────────┘
                             │
                         Runtime
                             │
              deterministic anchor ladder
                             │ fail
                    semantic rediscovery
                             │
                     Repair Candidate

Điểm rất quan trọng: RFC đã đúng khi tách recipes/ canonical khỏi traces.db và ledger.db. 

procedural_memory_rfc

 Đừng “fix” RAM-only ledger bằng cách đơn giản gọi RecipeStore.save(recipe) sau mỗi execution. save() hiện ghi JSON recipe vào store; telemetry runtime không nên làm dirty Git recipe, và với multi-agent execution còn tạo contention/race. 

recipes

Ledger phải là data plane riêng.

12. Một số bổ sung cho multi-agent mà RFC hiện chưa nhấn mạnh

SQLite WAL là lựa chọn hợp lý, nhưng mỗi event cần ít nhất:

run_id
agent_id
browser_context_id
page_id
frame_id
monotonic_seq
parent_event_id
recipe_id/version nếu đang replay
timestamp

Đặc biệt CLI của bạn chạy thành nhiều process độc lập. learn begin, rồi act, rồi learn complete không thể dựa vào Python in-memory context. Learning session cần một explicit run_id, ví dụ:

Bash
RUN=$(omnibrowser learn begin ...)
omnibrowser act ... --learn-run "$RUN"
omnibrowser learn complete "$RUN" --success

hoặc process-local environment/session file.

Nếu không, hai coding agents cùng điều khiển một Chrome profile rất dễ ghi action của nhau vào cùng learning run.

Kết luận kiến trúc

Tôi sẽ giữ nguyên triết lý chính của RFC, nhưng thay ba assumptions:

Recorder: từ “capture rồi redact” → construct privacy-safe semantic trace from the beginning.

Recipe: từ “linear selector macro” → state-transition contract + anchor bundle.

Self-healing: từ “repair recipe online” → resolve online, learn repair offline.

Nếu ba thay đổi này được làm đúng, OmniBrowser có một điểm khác biệt khá rõ so với macro recorder truyền thống: Tier 2 không chỉ là cached Playwright code, mà trở thành một verified procedural memory layer giữa deterministic automation và cognitive agent.

Một chi tiết cuối cần dọn ngay: RFC mô tả pyramid Level 0–3, trong khi SKILL.md hiện mô tả Level 1–4 với Level 3 là diagnostics và Level 4 là vision. 

procedural_memory_rfc

 

SKILL

 Tôi sẽ thống nhất taxonomy trước khi API learn được public, vì tầng capability sẽ trở thành vocabulary mà mọi agent dựa vào.

Nếu muốn đi tiếp, tôi có thể:

Thiết kế Recipe Schema v2 hoàn chỉnh: AnchorBundle, StateContract, ValueRef, RepairCandidate và JSON Schema.

Viết patch trực tiếp cho recipes.py, engine.py và cdp_controller.py để thêm Recorder + learn begin/complete + safe trace persistence.

Thiết kế bộ benchmark 20–30 edge cases cho React rerender, iframe, popup, label drift, PII leakage và self-healing để đo promotion threshold.