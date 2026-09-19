Executive verdict

Dựa trên implementation scope, snippets và verification evidence bạn cung cấp, Task-008 đã đạt mục tiêu chức năng của Milestone v2.3: implicit recording, domain partitioning, auto-distillation, suggestions trong observe, và regression suite đều có bằng chứng hợp lý.

Tuy nhiên, với vai trò release-gate review cho một background recorder chạy mặc định trong môi trường multi-agent, tôi chưa cấp APPROVED ở trạng thái hiện tại.

Verdict của tôi là:

CHANGES REQUIRED — 2 blocking issues, không cần redesign lớn.

Hai blocker là:

Secret/privacy sanitization hiện tại chưa đủ mạnh cho một implicit recorder.

Namespace isolation bằng cách bỏ qua /sitemaps/ trong RecipeStore.load() có thể bị bypass bởi recipe run <direct_file_path>.

Nếu hai điểm này được đóng cùng regression tests tương ứng, kiến trúc hiện tại đủ tốt để tôi approve v2.3 mà không yêu cầu semantic-state machinery của v2.4 ngay bây giờ.

1. Adversarial Security & Privacy Review
1.1 Password masking hiện tại: tốt nhưng chưa đủ

Implementation hiện detect:

input type=password
field names:
pass
secret
token
key
pin

và chuyển value thành:

JSON
{"$ref": "password"}

Đây là một baseline đúng.

Nhưng tuyên bố:

“Strict PII & Credential Sanitization”

hiện mạnh hơn protection thực tế.

Một implicit recorder sẽ thấy nhiều secret không mang tên password.

Ví dụ:

OTP / verification code
2FA code
one-time-code
API key
access token
refresh token
Bearer credential
recovery code
reset token
private key/passphrase
database connection string
signed URL
session token

Và quan trọng hơn:

fill textarea "Here is our production API key: ..."

hay:

contenteditable
chat composer
email body
support ticket

không phải password field nhưng hoàn toàn có thể chứa sensitive material.

Tôi khuyến nghị đảo policy

Hiện tại model dường như là:

persist raw value
unless known-sensitive

Implicit recorder nên dùng:

parameterize user-entered value
unless explicitly safe to persist

Tức là deny-by-default for typed values.

Ví dụ Flight Recorder thấy:

Python
Run
fill(
    target="Email",
    value="trung@example.com"
)

candidate không cần biết literal email để học procedure:

JSON
{
  "op": "fill",
  "target": {
    "role": "textbox",
    "name": "Email"
  },
  "value": {
    "$ref": "email"
  }
}

Tương tự:

First name   → $ref:first_name
search query → $ref:search_query
message      → $ref:message
OTP          → $ref:otp
password     → $ref:password

Procedural memory cần shape của operation, không cần phần lớn literal user data.

Đây vừa giải privacy vừa cải thiện recipe generalization.

1.2 Sanitization phải xảy ra trước FlightJournal.append()

Điểm này rất quan trọng.

Nếu flow hiện tại là:

engine.act()
→ RecordedAction(raw value)
→ FlightJournal
→ distillation
→ sanitize
→ disk

thì câu:

raw secrets are never persisted to disk

có thể đúng, nhưng architecture vẫn giữ raw secret trong recorder state.

Tốt hơn:

engine.act()
      │
      ▼
sanitize_for_recording()
      │
      ▼
RecordedAction(sanitized)
      │
      ▼
FlightJournal

Journal không có lý do gì cần plaintext password.

Một principle tốt:

SecretLifetime
Recorder
	​

=0

tức flight recorder never owns the plaintext credential.

engine.act() có thể cần plaintext để thực thi, nhưng recorder hook chỉ nhận sanitized projection.

1.3 URL cũng là một secret channel

Hiện boundary lưu:

url_before
url_after

Nếu lưu full URL, có thể leak:

/reset-password?token=abc...
/oauth/callback?code=...
/invite?secret=...
?signed_url=...
#access_token=...

Recipe matching cũng không cần các value này.

Persisted representation nên gần:

YAML
origin: https://example.com
path: /reset-password
query_keys:
  - token

không phải:

https://example.com/reset-password?token=actual-secret

Default:

strip userinfo
strip query values
strip fragment

Chỉ allowlist query values khi chúng được chứng minh là semantically necessary và non-sensitive.

1.4 Sanitizer phải bao phủ metadata ngoài name

Nếu vẫn giữ secret classifier, inspect ít nhất:

input.type
name
id
aria-label
placeholder
autocomplete
semantic label

và normalize case/tokenization.

Đặc biệt:

HTML
autocomplete="current-password"
autocomplete="new-password"
autocomplete="one-time-code"

là signals rất tốt.

Nhưng classifier này nên là second defensive layer; first layer vẫn nên là “free-form values are parameters”.

1.5 Một test suite security tối thiểu cần thêm

Tôi sẽ không approve privacy gate nếu mới test:

<input type=password>

Cần coverage cho các cases kiểu:

Case	Expected persisted value
password input	$ref
name=apiKey	$ref
autocomplete=one-time-code	$ref
textarea message	parameterized
contenteditable text	parameterized
password reset URL query	value stripped
OAuth callback ?code=	value stripped
fragment token	stripped
normal static dropdown	safe literal allowed

Đây là Blocker #1.

2. Boundary Heuristics

Với mục tiêu v2.3, tôi cho rằng:

URL transition
Submit/Save/Sign-in
8-action cap

là hợp lý để ship như bootstrap segmentation policy.

Tôi không yêu cầu semantic transition graph trước approval.

Nhưng có vài invariants phải đảm bảo.

2.1 Chỉ successful actions mới được distill thành executable program

Bạn nói:

Every action dispatched to a browser page is passively recorded.

“dispatched” làm tôi chú ý.

Nếu:

click X
→ ActionTimeoutError

vẫn được ghi như một normal successful action, sau đó action thứ 8 kích hoạt distillation, bạn có thể tạo recipe chứa một operation vốn chưa từng thành công.

Nên phân biệt:

Python
Run
RecordedAction(
    status="success" | "failed" | "unknown"
)

Executable distillation chỉ lấy:

confirmed successful transition path

Failure trace vẫn hữu ích cho diagnostics, nhưng không nên trở thành recipe body.

Tôi xem đây là high-priority verification item.

Nếu implementation hiện đã append sau successful engine.act() thì ổn.

2.2 Boundary action phải thuộc segment phía trước

Ví dụ:

fill username
fill password
click Sign in

click Sign in phải nằm trong candidate:

[fill, fill, click]

rồi mới reset journal.

Không được:

candidate = [fill, fill]
reset
click Sign in goes next journal

Tương tự Save/Submit.

2.3 Async navigation race

Detection:

Python
Run
action.url_after != action.url_before

phụ thuộc thời điểm capture url_after.

Một SPA có thể:

click
return from CDP action
100 ms later
history.pushState()

Khi đó:

url_before == url_after

dù navigation thực sự xảy ra.

Không phải blocker v2.3, nhưng nên record:

navigation_epoch
loaderId
frame lifecycle
history event

trong tương lai thay vì chỉ URL snapshot.

2.4 Cross-domain navigation cần hard boundary

Ví dụ:

shop.example.com
→ auth.provider.com

đừng distill một mixed-domain segment thành:

memory/v1/shop.example.com/candidates/...

Một procedural candidate nên thuộc một clear execution scope.

Tôi khuyên invariant:

if origin/domain changes:
    close previous segment
    start new journal segment

và không để credentials/actions của domain B rơi vào memory partition A.

2.5 8 actions nên là safety ceiling, không semantic truth

Cho v2.3:

MAX_ACTIONS = 8

hoàn toàn acceptable.

Nhưng semantics nên là:

semantic boundary first
hard cap second

v2.4 chuyển dần sang:

URL/navigation transition
form commit
dialog transition
landmark transition
major AX state change
risk boundary
MAX_ACTIONS fallback

Vì vậy boundary architecture hiện tại không phải blocker.

3. Sitemap & Recipe Namespace Isolation

Ở đây tôi không đồng ý rằng current protection là sufficient.

Bạn có:

RecipeStore.load()
→ recursively scan
→ skip /sitemaps/

Điều đó bảo vệ path:

recipe list/load normal flow

Nhưng CLI đồng thời có:

recipe run <recipe_id_or_path>

và hỗ trợ:

direct file path

Vậy một caller có thể thử:

Bash
recipe run recipes/example.com/sitemaps/dashboard.json

Nếu direct-path resolver mở JSON trực tiếp, nó đã bypass hoàn toàn:

Python
Run
RecipeStore.load()

Do đó:

Filesystem location không nên là security/type boundary.

3.1 Mọi persisted artifact cần discriminator

Tôi khuyên schema:

JSON
{
  "kind": "omnibrowser.recipe",
  "schema_version": 3,
  ...
}

Candidate:

JSON
{
  "kind": "omnibrowser.recipe_candidate",
  "schema_version": 3,
  ...
}

Sitemap:

JSON
{
  "kind": "omnibrowser.semantic_sitemap",
  "schema_version": 1,
  ...
}

recipe run phải enforce:

Python
Run
if document.kind not in {
    "omnibrowser.recipe",
    "omnibrowser.recipe_candidate",
}:
    raise InvalidExecutableArtifact(...)

irrespective of how file was found.

That gives you:

directory isolation
+
schema isolation
+
execution-type isolation
3.2 Schema validation trước execution

Không chỉ kind.

Validate:

version
actions
matcher
parameter schema
action op allowlist
risk metadata if present

Một arbitrary JSON file không được phép “gần giống recipe” và được execute.

Tôi thậm chí thích API:

Python
Run
ExecutableRecipe.parse(document)

thay vì generic:

Python
Run
json.load(...)

trong CLI runner.

3.3 Regression test bắt buộc
Python
Run
def test_recipe_run_rejects_sitemap_direct_path():
    ...
    assert exit_code != 0

và:

Python
Run
test_recipe_run_rejects_unknown_json_kind
test_recipe_run_accepts_recipe_direct_path
test_recipe_run_accepts_candidate_direct_path

Đây là Blocker #2.

4. Review thêm: Domain Helper

Có một correctness issue trong:

Python
Run
parsed.netloc.split(":")[0]

Nên dùng:

Python
Run
parsed.hostname

netloc có thể chứa:

userinfo
port
IPv6 notation

Ví dụ:

https://user:pass@example.com:8443/path

netloc là dạng:

user:pass@example.com:8443

và:

Python
Run
split(":")[0]

sẽ trả:

user

không phải domain.

IPv6 còn tệ hơn:

https://[::1]:9222

Do đó helper nên tối thiểu:

Python
Run
def _domain_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").rstrip(".").lower()
    return host or "global"

Tôi xem đây là must-fix before release, dù không phải architectural blocker lớn.

5. _normalize_path() có collision

Hiện:

Python
Run
path.strip("/").replace("/", "_")

gây:

/a/b   → a_b
/a_b   → a_b

Hai sitemap khác nhau overwrite cùng file.

Ngoài ra:

very long URL path
Unicode
percent encoding
dynamic IDs

có thể làm filename không đẹp hoặc vượt filesystem limits.

Nên dùng:

human-readable slug + short hash

ví dụ:

orders_123--8f31a92c.json

Hoặc tốt hơn:

Python
Run
hash(canonical_route_signature)

và lưu actual URL/path trong JSON.

Đây là non-blocking for v2.3, nhưng nên fix sớm vì sitemap chính là nền cho semantic matching sau này.

6. confidence=0.95 vs 0.80

Tôi sẽ đổi naming.

Hiện:

curated recipe = confidence 0.95
candidate      = confidence 0.80

Nhưng đó không thực sự là execution confidence.

Một curated recipe có thể đã drift hoàn toàn hôm nay.

Một candidate mới ghi 30 giây trước có thể match page hiện tại tốt hơn nhiều.

Thứ bạn đang encode thực chất là:

source trust prior

hoặc:

maturity score

Nên output:

JSON
{
  "source_trust": 0.95,
  "match_score": 0.72,
  "health_score": 0.99
}

và sau này:

ExecutionConfidence=f(SourceTrust,LiveMatch,Health,Drift,Risk)

Nếu giữ field confidence, LLM sẽ rất dễ hiểu nhầm:

“95% safe to run”

trong khi hiện tại nó chỉ có nghĩa:

“came from curated store”.

Không blocker nhưng là API semantics cần sửa trước khi ecosystem phụ thuộc vào field này.

7. Ready-to-run CLI strings có một security edge nữa

Bạn đang đưa vào observe:

python3 scripts/cdp_controller.py recipe run <id> [--params ...]

Nếu string này được xây bằng interpolation từ:

recipe ID
parameter values
page-derived data

thì phải tránh shell injection.

Ví dụ malicious page-controlled value:

"; rm -rf ..."

Không nên có capability biến thành shell command fragment.

Tốt hơn ObserveResult trả structured invocation:

JSON
{
  "argv": [
    "python3",
    "scripts/cdp_controller.py",
    "recipe",
    "run",
    "abc123",
    "--params",
    "{\"month\":9}"
  ]
}

CLI string chỉ là presentation layer được tạo bằng proper shell quoting.

Nếu implementation hiện interpolate raw values, tôi sẽ nâng điểm này thành third blocker.

Nếu chỉ recipe IDs generated internally và không có untrusted interpolation, severity thấp hơn.

8. Multi-Agent consistency concern

Bạn mô tả FlightJournal là thread-safe.

Trong OmniBrowser, thread-safe chưa đồng nghĩa multi-agent safe.

Các writers từ nhiều process có thể đồng thời ghi:

~/.omnibrowser/memory/v1/<domain>/candidates/
recipes/<domain>/sitemaps/

Candidate file nếu UUID độc lập thì ít conflict.

Nhưng sitemap có deterministic filename:

<normalized_path>.json

nên nhiều agent có thể ghi cùng lúc.

Tối thiểu cần:

write tempfile
fsync optional
os.replace()

để reader luôn thấy:

old valid JSON
or
new valid JSON

chứ không thấy half-written JSON.

Không nhất thiết cần global lock nếu last-write-wins được chấp nhận.

9. Distillation reset cần failure-safe ordering

Bạn nói:

Journal is atomically reset upon distillation.

Cần chắc thứ tự là:

snapshot journal
↓
construct candidate
↓
persist candidate successfully
↓
truncate committed journal range

Không nên:

clear journal
↓
save candidate
↓
disk error

vì trace biến mất.

Tốt hơn nữa dùng monotonic sequence:

1 2 3 4 5 6 7 8 9

distill:

[1..8]

persist success:

truncate_through(8)

Action 9 đến trong lúc distillation sẽ không bị mất.

Điều này đặc biệt hữu ích nếu engine.act() có concurrency.

10. suggest_for_url() trên hot path

Có một architectural performance question:

observe()
→ RecipeStore.load()
→ scan all domain directories?

Nếu mỗi observe recursively loads everything rồi filter URL, procedural cache sẽ tự tạo overhead trên command được gọi thường xuyên nhất.

Target architecture nên:

domain = current host
↓
load domain index only
↓
candidate route match

và giữ:

mtime/version cached in process

Nếu current implementation đã domain-prune trước load thì không vấn đề.

Nếu chưa, tôi xem đây là performance debt chứ chưa phải release blocker ở v2.3.

11. Acceptance decision theo từng inquiry
Inquiry	Decision
Credential sanitization	Insufficient for default implicit recording
URL/submit/8 boundary	Acceptable for v2.3
Alignment with semantic graph v2.4	Yes
/sitemaps/ exclusion	Insufficient as sole isolation mechanism
Domain partitioning architecture	Sound, helper needs fixing
Auto-suggestion model	Good v2.3 bootstrap
48/48 test evidence	Strong functional evidence, not enough for two uncovered security cases
Task-008 functional AC	Appears satisfied from supplied evidence
Production/release approval	CHANGES REQUIRED
12. Exact gates for my approval

I would not ask browser-arch to redesign Task-008. I would require these changes only:

Gate A — Recorder Privacy

Persisted recipes/candidates must not contain arbitrary free-form user values by default. Sanitization/parameterization occurs before data enters the FlightJournal, and URLs have query values/fragments stripped or safely classified.

Gate B — Typed Artifact Execution

Every persisted record has an explicit artifact kind/schema. Every execution path—including direct file path—validates that artifact as executable recipe/candidate. Sitemap and unknown JSON must fail closed.

Gate C — Domain parser correctness

Replace netloc.split(":")[0] with safe hostname parsing and add userinfo/port/IPv6 tests.

Gate D — Execution-string safety

Demonstrate that suggested_recipes cannot produce shell injection from page/candidate-controlled content, preferably by representing invocation as argv rather than raw shell composition.

Gate E — Recorder correctness

Confirm by tests that failed actions are not silently promoted into executable recipes and that journal truncation happens only after successful candidate persistence.

Gates C–E are relatively small. A and B are the material ones.

Final approval status

Task-008 implementation quality: architecturally strong and consistent with the v2.3 direction.

The separation:

FlightJournal
→ FlightRecorder
→ LearningMemory
→ RecipeStore
→ observe suggestions

is clean, and retaining the simple URL/domain matcher for this milestone is the right scope discipline rather than prematurely implementing the v2.4 semantic transition system.

But because the feature is now implicit, privacy and type safety have become stronger requirements than under the old explicit learn begin workflow.

Therefore my formal decision is:

TASK-008 — CHANGES REQUIRED

Functional acceptance: PASS
Architecture direction: PASS
Release/security gate: HOLD

Resolve Blocker #1 (capture-time privacy/parameterization) and Blocker #2 (typed executable-artifact validation on every run path), plus the small hostname/parser correction, and I would expect Task-008 to be approvable without architectural rework.

Nếu bạn muốn tiếp tục ngay, tôi có thể:

Soạn 5 acceptance gates v2.3.1 dưới dạng checklist/test cases chính xác để browser-arch implement và gửi lại review.

Thiết kế RecordedAction / RecipeArtifact schema v2 cụ thể, gồm sanitizer contract, kind, versioning và execution validation.

Review tiếp patch/diff của commit 092f996... ở mức line-by-line nếu bạn đưa các file hoặc diff nguồn.