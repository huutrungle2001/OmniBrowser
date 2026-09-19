Formal Review Verdict — Milestone v2.3.1 / Task-008
APPROVED

Dựa trên implementation details và validation evidence bạn cung cấp cho commit fa7d506a7527fdeb205febb97102196352ca7c50, tôi đánh giá cả 5 Acceptance Gates A–E đã được giải quyết ở mức đủ để gỡ release/security hold trước đó.

Formal status:

Task-008 — APPROVED for Milestone v2.3.1
Functional acceptance: PASS
Architecture direction: PASS
Security/privacy release gate: PASS
Regression evidence: PASS — 52/52 tests

Không còn issue nào trong năm gate đã đặt ra mà tôi xem là release blocker dựa trên evidence hiện tại.

Một lưu ý về phạm vi: verdict này là review dựa trên implementation description, snippets và test evidence bạn cung cấp; nó không phải line-by-line audit trực tiếp toàn bộ diff của commit.

1. Gate A — Recorder Privacy
Verdict: PASS

Đây từng là blocker quan trọng nhất, và thay đổi mới giải quyết đúng vấn đề ở đúng abstraction layer.

A1. Sanitization trước FlightJournal

Thứ tự mới:

raw action value
      ↓
LearningMemory._value_ref()
      ↓
sanitized / parameterized representation
      ↓
RecordedAction
      ↓
FlightJournal

đúng hơn đáng kể so với:

raw value
→ journal
→ sanitize at persistence time

Điều này thực hiện được invariant mà tôi yêu cầu trước đó:

SecretLifetime
FlightRecorder
	​

≈0

Recorder không còn trở thành một secondary plaintext secret store trong RAM.

Đây là thiết kế đúng.

A2. Deny-by-default cho user-entered values

Việc chuyển từ:

persist value unless sensitive

sang:

parameterize typed value unless explicitly safe

là thay đổi quan trọng hơn nhiều so với chỉ bổ sung thêm regex cho password, token, pin.

Ví dụ:

JSON
{
  "$ref": "input_value",
  "sensitivity": "runtime",
  "persist_value": false
}

phù hợp với bản chất của procedural memory:

Recipe cần nhớ cách thao tác, không cần nhớ dữ liệu người dùng đã gõ trong lần học.

Điều này đồng thời cải thiện:

privacy;

recipe generalisation;

reproducibility;

portability giữa agent/session;

khả năng parameter binding về sau.

Đặc biệt việc phân loại:

secret
pii
runtime

tạo nền tốt cho policy engine v2.4+.

A3. URL sanitisation

Việc strip:

userinfo
query values
fragment

đóng đúng một leak channel mà password-field sanitisation không thể bảo vệ.

Các dạng:

/reset?token=...
/oauth/callback?code=...
#access_token=...
https://user:pass@host/

không còn bị Flight Recorder vô tình biến thành procedural memory.

Kết luận Gate A

Original blocker fully addressed.

Non-blocking hardening

Về sau nên kiểm tra thêm rằng anchor metadata như accessible names, labels hoặc page text cũng không chứa high-entropy secret/PII ngoài ý muốn. Nhưng điều đó nằm ngoài gate hiện tại và không ngăn approval.

2. Gate B — Typed Artifact Execution
Verdict: PASS

Đây là blocker thứ hai của review trước, và cách khắc phục hiện tại đúng kiến trúc.

Bạn đã chuyển filesystem organisation từ một convention thành một typed persistence model:

omnibrowser.recipe
omnibrowser.recipe_candidate
omnibrowser.semantic_sitemap

Đây là khác biệt quan trọng.

Trước:

"file nằm trong recipes/ ⇒ chắc là executable recipe"

Bây giờ:

filesystem path
      ↓
parse artifact
      ↓
validate kind
      ↓
executable?
      ├── yes → continue
      └── no  → fail closed

Đây mới là đúng trust boundary.

B1. Direct-path bypass đã được đóng

Đây chính là attack/correctness path mà review trước nêu ra:

Bash
recipe run recipes/foo/sitemaps/bar.json

Trước đây việc RecipeStore.load() bỏ qua /sitemaps/ không bảo vệ direct path.

Giờ:

direct path
   ↓
Recipe.from_dict()
   ↓
kind validation
   ↓
semantic sitemap → InvalidExecutableArtifact

nên loader bypass không còn dẫn đến execution bypass.

Đúng.

B2. Fail-closed semantics

Exit:

INVALID_INPUT / code 2

là hợp lý.

Quan trọng hơn exit code là semantics:

unknown artifact type không được cố “interpret” thành recipe.

Đây là behavior đúng cho executable persistence.

B3. Schema versioning

Việc thêm:

JSON
"schema_version": 1

ngay từ v2.3.1 là quyết định tốt vì tránh khóa format persistence vào Python class hiện tại.

Một hardening tôi vẫn khuyên

Nếu chưa làm, parser tương lai nên enforce:

Python
Run
if schema_version not in SUPPORTED_RECIPE_SCHEMA_VERSIONS:
    raise UnsupportedArtifactVersion(...)

chứ không chỉ validate kind.

Ví dụ:

JSON
{
  "kind": "omnibrowser.recipe",
  "schema_version": 999
}

không nên được parser v1 cố diễn giải.

Tuy nhiên đây không phải blocker cho gate B hiện tại, vì typed-artifact bypass đã được đóng.

3. Gate C — Domain Parser Correctness
Verdict: PASS

Thay:

Python
Run
parsed.netloc.split(":")[0]

bằng:

Python
Run
(parsed.hostname or "").rstrip(".").lower()

là correction đúng.

Nó giải các lỗi structural parsing như:

https://user:pass@example.com:8443/
                     ↓
                example.com

thay vì:

user

và hỗ trợ IPv6 đúng hơn:

https://[::1]:9222/
        ↓
       ::1

Coverage bao gồm:

userinfo;

explicit port;

IPv6;

IPv4;

special URL case.

đủ cho gate này.

Một nuance về domain abstraction

hostname vẫn là host scope, chưa phải “registrable domain/eTLD+1”.

Ví dụ:

app.example.com
auth.example.com

sẽ vẫn là hai partitions.

Nhưng đó không phải bug.

Thực tế ở v2.3 tôi còn thích host-level isolation hơn việc prematurely merge subdomains.

eTLD+1/domain-family semantics có thể được đưa vào v2.4 khi composite matcher xuất hiện.

4. Gate D — Execution-String Safety
Verdict: PASS

Thay đổi quan trọng nhất không phải shlex.quote() mà là việc có:

JSON
"argv": [
  "python3",
  "scripts/cdp_controller.py",
  "recipe",
  "run",
  "...",
  "--params",
  "..."
]

Structured argv là representation đúng.

Architecture tốt nên xem:

argv = authoritative execution representation
command = human-readable convenience representation

chứ không phải ngược lại.

D1. Recipe ID validation

Regex:

regex
[A-Za-z0-9][A-Za-z0-9_.-]*

loại bỏ phần lớn shell grammar:

;
&
|
'
"
space
$()

và cũng giúp recipe IDs trở thành stable storage identifiers.

Defense in depth tốt.

D2. Shell quoting

shlex.quote() cho recipe_id và param_str cung cấp lớp bảo vệ thứ hai cho presentation string.

Bạn hiện có ba lớp:

structured argv
+
ID grammar validation
+
shell quoting

Đây là mức bảo vệ phù hợp.

Architectural recommendation

Code nội bộ nên không bao giờ parse lại field command để execution.

Nên luôn:

Python
Run
subprocess.run(suggestion["argv"], shell=False)

nếu sau này OmniBrowser execute suggestion programmatically.

5. Gate E — Recorder Correctness & Atomic Persistence
Verdict: PASS

Gate này có ba phần khác nhau và cả ba đều đã được xử lý hợp lý.

E1. Failed actions không được distill

Filter:

Python
Run
successful_events = [
    e for e in journal.events
    if e.get("status", "success") == "success"
]

và stamping explicit:

Python
Run
event["status"] = "success"

trên producer path cung cấp separation cần thiết giữa:

observed attempt

và:

known-successful procedural step

Đây là requirement quan trọng: procedural memory phải học từ successful trajectory, không phải chỉ từ attempted trajectory.

Một hardening nhỏ

Tôi sẽ đổi sau này:

Python
Run
e.get("status", "success")

thành:

Python
Run
e.get("status") == "success"

tức missing status → non-executable, không phải success.

Fail-closed tốt hơn cho:

legacy journal entries;

malformed events;

future producers quên stamp status.

Tôi không xem đây là blocker bởi producer hiện tại đã explicit stamp status.

5.2 Journal truncation sau persistence

Ordering:

distill candidate
      ↓
persist successfully
      ↓
return non-None
      ↓
clear journal

đóng data-loss case của review trước:

clear
→ persistence fails
→ trace irrecoverably lost

Nếu distill_journal() chỉ trả non-None sau successful atomic write như mô tả, invariant hiện tại là đúng.

5.3 Atomic multi-agent file writes

Pattern:

unique temp file
→ complete write
→ atomic replace(target)

phù hợp cho loại persistence này.

Reader thấy:

old complete artifact

hoặc:

new complete artifact

thay vì:

half-written JSON

Đây chính xác là consistency guarantee cần thiết cho:

candidate JSON;

sitemap JSON;

concurrent readers.

Một nuance

Atomic replace không giải quyết logical ordering.

Hai agents có thể:

Agent A observes newer sitemap
Agent B observes older sitemap

A replace()
B replace()

và B trở thành final version.

Nhưng đây là last-writer-wins semantics, không phải torn-write corruption.

Cho v2.3.1, hoàn toàn acceptable.

v2.4 có thể bổ sung revision/timestamp/CAS nếu sitemap trở thành authoritative shared semantic cache.

6. Regression Evidence

52/52 tests là một cải thiện đáng kể so với 48/48 trước đó vì bốn test mới tập trung đúng vào các uncovered failure modes từ review:

typed artifact rejection
privacy deny-by-default
argv / quoting safety
failure-safe distillation

Đây là cách tốt để đóng review findings: mỗi blocker trở thành executable regression invariant.

Việc concurrency suite:

29/29

vẫn xanh cũng quan trọng, vì Task-008 đang đụng vào shared filesystem/session infrastructure.

git diff --check, communication-record lint và clean worktree là hygiene tốt nhưng tôi xem chúng là supporting evidence, không phải reason chính để approve.

7. Tổng hợp 5 Acceptance Gates
Gate	Previous status	v2.3.1 assessment	Release blocker?
A — Recorder Privacy	BLOCKER	PASS	No
B — Typed Artifact Execution	BLOCKER	PASS	No
C — Domain Parser	Must-fix	PASS	No
D — Execution Safety	Must-verify	PASS	No
E — Recorder Correctness / Atomic Writes	Must-verify	PASS	No

All five gates closed.

8. Những issue còn lại tôi không dùng để giữ release

Một strict review vẫn nên ghi nhận technical debt, nhưng cần phân biệt technical debt với acceptance blocker.

Tôi ghi lại bốn mục sau là non-blocking follow-ups:

status missing nên default về not successful, không phải "success".

schema_version nên có strict supported-version validation.

Sitemap writes hiện có atomicity nhưng chưa có revision/CAS để ngăn stale last-writer-win.

"confidence": 0.95/0.80 về lâu dài nên đổi thành source_trust và tách khỏi live match/health confidence.

Không mục nào trong số này làm mất hiệu lực của năm gate hiện tại.

9. Architectural assessment sau v2.3.1

Quan trọng hơn việc “test xanh” là v2.3.1 đã sửa đúng những chỗ làm cho implicit recording nguy hiểm hơn explicit recording.

Trước đây trust path gần như:

Agent acts
   ↓
Flight Recorder
   ↓
candidate JSON
   ↓
suggest
   ↓
execute

Sau v2.3.1 nó gần hơn với:

Agent acts
   │
   ├── sanitize at capture boundary
   │
   ▼
Typed RecordedAction
   │
   ▼
FlightJournal
   │
   ├── successful events only
   ▼
Distillation
   │
   ├── atomic persistence
   ▼
Typed RecipeCandidate
   │
   ▼
Suggestion
   │
   ├── structured argv
   ▼
Execution boundary
   │
   ├── artifact type validation
   ▼
Recipe executor

Đây là một trust pipeline có cấu trúc hợp lý.

Đặc biệt, ba boundaries giờ đã rõ:

privacy boundary
artifact/type boundary
execution boundary

Điều đó làm cho việc chuyển sang Semantic State / Multi-Tier Cache ở v2.4 dễ hơn nhiều.

10. Formal Approval
MILESTONE v2.3.1 — APPROVED

Tôi gỡ trạng thái trước:

CHANGES REQUIRED

và thay bằng:

APPROVED — Task-008 / Milestone v2.3.1

Basis of approval

Gate A: capture-time deny-by-default privacy model implemented.

Gate B: executable artifacts now strongly typed and sitemap/direct-path bypass closed.

Gate C: URL hostname parsing corrected and edge cases tested.

Gate D: structured argv plus validation/quoting closes the command-construction concern.

Gate E: executable learning is success-only, journal clearing is persistence-dependent, and shared file writes are atomic.

Regression: 52/52 relevant tests pass with no reported regressions.

Không cần thêm redesign để merge/close Task-008.

Từ góc nhìn architecture governance, tôi sẽ đánh dấu trạng thái:

Task-008
└── Milestone v2.3.1
    ├── Functional ACs ............. PASS
    ├── Privacy Gate ............... PASS
    ├── Artifact Safety Gate ....... PASS
    ├── Execution Safety Gate ...... PASS
    ├── Concurrency/Persistence .... PASS
    └── FINAL STATUS ............... APPROVED

Bước hợp lý tiếp theo không phải tiếp tục mở rộng Task-008, mà là freeze v2.3.1 semantics và chuyển các cải tiến như semantic fingerprinting, state-transition boundaries, health scores và promotion/quarantine lifecycle sang Milestone v2.4 thay vì làm scope creep vào release đã đạt acceptance.
