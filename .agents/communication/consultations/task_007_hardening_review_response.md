Tôi đã attack lại toàn bộ 7 file cập nhật theo failure traces, không chỉ đọc test happy-path. Kết luận: v2.2.1 đã tốt hơn v2.2 rất đáng kể, nhưng tôi chưa xác nhận APPROVED FOR v2.2.1. Vẫn còn 3 vấn đề ở mức CRITICAL và một số MAJOR cần đóng trước khi có thể gọi control-plane này production-safe.

Các fix trước đây như exact fencing, shared/exclusive identity record, reverse-map cleanup, process-tree RSS và Class-S generation pool đều đi đúng hướng. Exact fencing hiện thực sự dùng equality thay vì chỉ reject token nhỏ hơn. 

lease_manager

 TargetRegistry cũng đã dọn reverse mapping cũ đúng cách khi ownership thay đổi. 

target_registry

1. flock: bản thân lock ổn, transaction model chưa ổn
SIGKILL có để lại stale lock không?

Không.

flock là kernel advisory lock; khi descriptor/lock ownership biến mất, lock được giải phóng. Một file state.lock còn nằm trên filesystem sau crash chỉ là một inode bình thường, không phải “stale lock”. Apple cũng mô tả flock() là advisory locking; các process không hợp tác vẫn có thể bỏ qua lock. 
Apple Developer
+1

Vì vậy:

SIGKILL
   ↓
FD closes
   ↓
kernel lock disappears
   ↓
next CLI may acquire state.lock

Phần này đúng.

Nhưng điều đó không làm operation trở thành transaction.

CRITICAL #1 — _load_state() không authoritative: revoked lease có thể sống lại

Đây là lỗi nghiêm trọng nhất tôi tìm thấy.

_state_lock() có trình tự đúng về mặt ý tưởng:

flock
load
mutate
save
unlock

session_router

Nhưng _load_state() không replace in-memory state bằng state trên disk. Nó chỉ thêm lease nếu ID chưa tồn tại:

Python
Run
if lid not in self.lease_manager._leases:
    ...

và identity cũng chỉ load nếu key chưa có. Daemon cũng tương tự. 

session_router

Điều này phá serializability mặc dù flock hoạt động hoàn hảo.

Failure trace cụ thể
Disk:
    L1 active=true
    fencing=7

P1:
    SessionRouter()
    -> constructor loads L1 active=true, token=7

P2:
    acquire flock
    revoke L1
    save active=false
    release flock

P1:
    status()
      acquire flock
      _load_state()

      L1 already exists in P1 memory
      => SKIP disk version active=false

      get_active_leases()
      => P1 still sees L1 active=true

      _save_state()
      => writes active=true back to disk

    release flock

Lease vừa revoke được resurrect.

Cùng pattern có thể rollback fencing token, identity state, target removals và process metadata.

Đặc biệt status() là read-looking operation nhưng _state_lock() luôn _save_state() khi rời block, nên một process mang snapshot cũ chỉ cần gọi status cũng có thể ghi ngược state cũ. 

session_router

Fix bắt buộc

Khi đã acquire filesystem lock:

disk state = authoritative

Không merge.

Nên reconstruct fresh:

Python
Run
new_leases = ...
new_identities = ...
new_targets = ...
new_daemons = ...

rồi atomically swap các maps.

Runtime-only handles như Popen phải nằm riêng khỏi persisted domain model và reconcile theo instance_id/PID.

Tốt hơn nữa:

constructor
    không load authoritative mutable state

operation
    acquire flock
    read fresh snapshot
    operate
    persist
    unlock
CRITICAL #2 — flock không bao phủ transaction với Chrome

Đây là câu trả lời quan trọng nhất cho câu:

CLI model + flock có đủ thay resident RPC daemon không?

Current implementation: chưa đủ.

Vì transaction của OmniBrowser không chỉ gồm JSON. Nó gồm:

filesystem state
+
Chrome process
+
BrowserContext
+
Target
+
temporary directory

flock chỉ serialize filesystem control-plane.

Failure trace SIGKILL — Class I

P1:

acquire state.lock

reserve identity

Popen Chrome
  PID=4001
  tempdir=/tmp/omnibrowser...

Chrome starts successfully

<---------------- SIGKILL HERE

_state_save never happens

Kernel giải phóng flock.

Nhưng child Chrome không tự động biến mất chỉ vì Python parent chết.

P2:

acquire flock
load previous state

no PID 4001
no lease
no tempdir record

Chrome 4001 = orphan

Đúng cùng vấn đề với Class S:

Target.createBrowserContext succeeds
SIGKILL before commit
→ orphan BrowserContext

hoặc:

new Class-S daemon spawned
SIGKILL before state commit
→ whole Chrome daemon orphan

Trong request_lease, provisioning toàn bộ Chrome resource xảy ra bên trong lock trước khi context manager thực hiện _save_state(). 

session_router

Do đó resident daemon có bắt buộc không?

Không bắt buộc về mặt lý thuyết.

CLI model có thể production-safe nếu thêm:

Write-Ahead Intent
Recovery/Reconciliation
Idempotent operation IDs
Orphan reaper

Ví dụ:

LOCK
persist:
    operation_id = X
    state = PROVISIONING
    intended_class = I
    identity = foo
fsync
UNLOCK

spawn Chrome tagged with X

LOCK
commit:
    PID
    tempdir
    lease
    state = ACTIVE
UNLOCK

Nhưng vẫn còn crash window:

spawn()
↓
SIGKILL
↓
before PID persisted

nên resource phải discoverable bằng operation ID.

Ví dụ Class I tempdir:

.../leases/<operation-id>/

và startup reconciler scan:

runtime directories
-
committed state
=
orphans → kill/delete

Class S contexts cần reconciliation bằng Target.getBrowserContexts; CDP cung cấp API đó. 
Chrome DevTools

Đây chính là lý do resident broker vẫn đơn giản hơn đáng kể.

CRITICAL #3 — AuthStateVault vẫn có identity collision

Fix flock trong vault là đúng. Write serialization hiện tốt hơn đáng kể. 

auth_state_vault

Nhưng identity mapping vẫn là:

Python
Run
"".join(
    c for c in identity
    if c.isalnum() or c in ("-", "_", ".")
)

auth_state_vault

Vì thế:

alice@example.com
→ aliceexample.com

aliceexample.com
→ aliceexample.com

Hai identity khác nhau có cùng vault directory.

Tôi đã chạy trực tiếp source attachment:

save alice@example.com  -> sid=A
save aliceexample.com   -> sid=B

get_latest("alice@example.com")
→ trả snapshot identity aliceexample.com / sid=B

Đây là cross-identity credential disclosure.

Nếu auth_identity agent-controllable, tôi coi đây là CRITICAL security boundary failure.

Fix

Không sanitize mất thông tin.

Dùng:

directory =
    base64url(UTF8(identity))

hoặc tốt hơn:

sha256(identity).hexdigest()

và trong snapshot giữ:

JSON
{
  "canonical_identity": "alice@example.com"
}

Khi read phải verify:

snapshot.identity == requested identity

nếu không thì fail closed.

2. Identity Reservation TTL 60 giây

Ở đây có nuance thú vị.

Trong SessionRouter hiện tại, TTL 60s ít nguy hiểm hơn tưởng tượng

Bạn giữ _state_lock() suốt quá trình:

reserve identity
↓
spawn Chrome
↓
create lease
↓
save

nên một CLI process khác không thể vào critical section để “steal” reservation lúc provisioning đang chạy.

Do đó nếu provisioning mất 90 giây nhưng process vẫn giữ flock, process khác vẫn đang block ở filesystem lock.

Trong architecture hiện tại, heartbeat reservation không thực sự cần thiết.

Nhưng LeaseManager bản thân vẫn có một semantic hole

create_lease(... pre_reserved_lease_id=X) chỉ nhìn:

Python
Run
if auth_identity and not pre_reserved_lease_id:
    reserve_identity(...)

Nếu có pre_reserved_lease_id, nó không verify rằng:

reservation X còn tồn tại;

reservation X thuộc đúng identity;

mode exclusive/shared khớp;

reservation chưa hết TTL;

một owner khác chưa thay thế reservation.

lease_manager

Đây tạo split-brain nếu sau này provisioning được đưa ra ngoài global flock.

Failure trace
t0:
A reserves identity X exclusive

t+61:
reservation A expires

B reserves identity X exclusive

A returns late from provisioning

A:
create_lease(pre_reserved_lease_id=A)

Current code vẫn tạo lease A.

Bây giờ:

Lease A ACTIVE
Lease B/reservation ACTIVE

identity record chỉ phản ánh B
Thiết kế đúng

Tạo object:

Reservation {
    reservation_id
    identity
    mode
    owner
    expires_at
    generation
}

và API duy nhất:

activate_reservation(reservation_id, ...)

phải atomically assert:

reservation exists
AND not expired
AND identity matches
AND mode matches
AND reservation is still current owner

rồi convert reservation → active lease.

Có cần heartbeat?

Nếu vẫn giữ global flock trong provisioning: không cần, và TTL có thể chỉ là stale-data safeguard.

Nếu v2.3 đưa provisioning ra ngoài lock để tránh head-of-line blocking: có, hoặc TTL phải ≥ maximum provisioning SLA + margin.

Tôi thích:

TTL 30s
heartbeat every 10s

hoặc explicit lease extension.

3. Class S Draining Pool

Generation pool sửa đúng flaw lớn của v2.2.

Bạn đã chuyển từ singleton thành:

_class_s_daemons[instance_id]

và ID unique. 

session_router

Đây là improvement thật sự.

Nhưng lifecycle vẫn chưa closed-loop.

MAJOR — Watchdog không được gọi tự động

Trong attached SessionRouter, tôi không tìm thấy runtime call tới:

Python
Run
watchdog.check_memory_and_drain(...)

LifecycleWatchdog có logic RSS tree đúng hơn trước: parent + recursive children. 

lifecycle_watchdog

Nhưng function tồn tại ≠ system monitoring.

Hiện nó giống:

monitoring primitive

chứ chưa phải:

monitor

Test cũng trực tiếp gọi method với fake RSS. 

test_concurrency_broker

Cần một trong:

background watchdog thread

hoặc mọi broker operation gọi:

refresh_daemon_health()

trước routing.

MAJOR — Drain state có hai source-of-truth

_ensure_class_s_daemon() kiểm:

dinfo["is_draining"]
OR
watchdog.is_draining(did)

nhưng persistence lại serialize chỉ:

Python
Run
dinfo.get("is_draining", False)

session_router

Trong khi check_memory_and_drain() chỉ đổi:

DaemonRecord.state = DRAINING

lifecycle_watchdog

Failure trace
Process P1:
Watchdog D1 → DRAINING

dinfo["is_draining"] vẫn False

save state:
D1 is_draining=false

P1 exits

P2:
load state
register D1
D1 → HEALTHY

new lease
→ route back into daemon đáng lẽ đang draining

Cần một single authoritative BrowserInstance.state.

Ví dụ:

HEALTHY
DEGRADED
DRAINING
DEAD
RECYCLED

persist trực tiếp.

MAJOR — Lease chưa thật sự gắn với generation

Pool có instance_id, nhưng Lease vẫn được associate daemon bằng:

cdp_url

Release tìm daemon bằng:

Python
Run
if dinfo.get("cdp_url") == lease.cdp_url

session_router

Điều này không tận dụng generation ID.

Lease nên có:

browser_instance_id

explicitly.

Lý do:

dynamic port có thể được reuse sau crash/restart;

crash handling cần all leases where browser_instance_id=D17;

drain count nên dựa trên generation ID, không endpoint string.

MAJOR — transient CDP failure có thể orphan daemon

_ensure_class_s_daemon():

is_cdp_alive == false
→ pop daemon from _class_s_daemons

session_router

Không kill process.
Không cleanup tempdir.
Không mark existing leases LOST.

Một overloaded Chrome có thể fail một 500ms probe nhưng phục hồi ngay sau đó.

Khi đó:

D1 briefly stalls
↓
broker forgets D1
↓
spawn D2
↓
D1 recovers but is now untracked

Nên:

one failed health probe
→ DEGRADED

N consecutive failures / PID dead
→ DEAD

chứ không pop() ngay.

4. /json/list có timing race không?

Có.

Bạn đã sửa được phần lớn synthetic-target issue, nhưng fallback hiện vô tình đưa bug cũ trở lại:

Python
Run
initial_target_id = f"target-{time.time_ns()}"

try:
    GET /json/list
    choose first page
except:
    pass

Nếu:

endpoint đã sẵn sàng;

nhưng about:blank target chưa xuất hiện;

/json/list trả [];

hoặc query timeout;

thì function không exception bắt buộc, và synthetic ID vẫn được return.

Sau đó TargetRegistry tin ID giả là real CDP target.

DevTools protocol đã có primitive deterministic tốt hơn:

Target.createTarget
→ targetId

và trả chính TargetID của page vừa tạo. 
Chrome DevTools

Tôi sẽ bỏ /json/list entirely ở path này

Sau khi Chrome debugger ready:

GET /json/version
→ websocketDebuggerUrl

Target.createTarget {
    url: "about:blank"
}

→ exact targetId

Có thể đóng default page nếu muốn.

Lợi ích:

không polling
không timing ambiguity
không "first page wins"
không synthetic fallback
5. Test suite vẫn có blind spot lớn

Test tên:

test_inter_process_state_lock_concurrency

nhưng implementation thực tế dùng:

Python
Run
ThreadPoolExecutor

và một SessionRouter object duy nhất, cùng một Python process. 

test_concurrency_broker

Hơn nữa _state_lock vào:

Python
Run
with self._lock:

trước khi gọi flock. 

session_router

Tức 10 threads đã bị threading.RLock serialize trước khi filesystem locking có cơ hội được test.

Nói cách khác:

Test này gần như không chứng minh inter-process flock correctness.

Nó chứng minh RLock hoạt động.

Cần test thật bằng:

Python
Run
multiprocessing.Process

với:

N independent SessionRouter objects
same state_dir
same lock file

và Barrier để ép interleaving.

Một vấn đề lock khác: nested _state_lock

close() làm:

with _state_lock():
    release_lease()

nhưng release_lease() lại:

with _state_lock():

session_router +1

threading.RLock reentrant.

Nhưng _state_lock() mỗi lần lại open(state.lock) tạo FD mới.

Trên Linux, flock() locks gắn với open-file descriptions; hai open() độc lập được xem độc lập và một process có thể bị chính lock của mình block. Linux manual nói rõ điều này. 
man7.org

Darwin/BSD có semantics hơi khác và tài liệu Apple mô tả lock theo file/duplicated descriptors, nên tôi không dựa vào issue này để gọi macOS blocker. 
Apple Developer

Nhưng code đã tuyên bố support Linux nữa, vì vậy đây là portability bug.

Cách đúng:

close():
    không giữ outer _state_lock
    gọi release_lease từng lease

hoặc implement explicit reentrant process lock với depth counter và reuse cùng fd.

AuthStateVault: còn hai lỗi nhỏ hơn
MAJOR/MINOR — version allocation bằng len(existing)+1

Numeric sorting đã đúng, nhưng next version vẫn là:

Python
Run
next_ver = len(existing) + 1

auth_state_vault

Nếu:

v1
v3

tồn tại, next version = v3.

replace() sẽ overwrite v3, phá invariant immutable.

Tôi đã tái hiện trực tiếp:

before: v1, v3
save_snapshot()
→ returns v3
→ old v3 overwritten

Dùng:

Python
Run
next_ver = max(numeric_versions, default=0) + 1

và tốt hơn create destination với no-overwrite semantics.

MINOR/MAJOR — crash giữa snapshot và latest.json

Writer làm:

rename v13.json
↓
rename latest.json

auth_state_vault

SIGKILL giữa hai bước cho:

v13 exists
latest → v12

get_snapshot(None) nếu latest.json vẫn valid sẽ lấy v12, không compare với highest version. 

auth_state_vault

Không corrupt credentials, nhưng newest auth snapshot bị bỏ qua.

Recovery đơn giản:

latest_pointer_version
vs
max(v*.json)

choose/reconcile highest valid committed snapshot
Admission Controller vẫn còn TOCTOU

acquire_admission() nằm trước _state_lock(). 

session_router

Failure:

max=10
current=9

P1 checks → 9 → PASS
P2 checks → 9 → PASS

P1 acquires flock → creates #10
P2 later acquires flock → does not re-check → creates #11

Đây là MAJOR.

Pattern đúng:

loop:
    LOCK
    authoritative reload
    check admission
    if allowed:
        reserve capacity slot
        persist reservation
        UNLOCK
        provision
        break
    UNLOCK
    sleep/backoff

Không sleep 5 giây trong global lock.

save/load vẫn fail silently

Cả _save_state() và _load_state() vẫn:

Python
Run
except Exception:
    pass

session_router

Điều này đặc biệt nguy hiểm trong CLI architecture.

Ví dụ disk full:

lease successfully provisioned
↓
save state fails
↓
exception swallowed
↓
request_lease returns SUCCESS
↓
next CLI has no lease

Tôi xếp CRITICAL hoặc high-MAJOR, tùy semantics mong muốn.

Control-plane persistence errors phải fail closed.

Exact fencing: fix đúng, nhưng token vẫn optional

LeaseManager hiện reject mọi token không equal. Fix này đúng. 

lease_manager

Nhưng:

Python
Run
fencing_token: int | None = None

và:

Python
Run
if fencing_token is not None:

nghĩa là caller truyền không token vẫn được validate.

Nếu upper-layer cdp_controller.py bắt buộc fencing cho mọi mutating command thì ổn.

File đó không nằm trong attachment lần này, nên tôi không thể certify end-to-end fencing invariant.

Defense-in-depth tốt hơn:

validate_for_read(...)
validate_for_mutation(..., token REQUIRED)
Profile Sanctity

Constructor guard hiện đã sửa đúng:

AuthStateVault(vault_dir)
SessionRouter(state_dir)
→ protected path rejected

auth_state_vault

 

session_router

Có một edge nhỏ:

Python
Run
temp_dir = tempfile.mkdtemp(...)
assert_not_protected_profile(temp_dir)

Guard xảy ra sau directory creation.

Nếu malicious/misconfigured:

TMPDIR=~/.chrome-ai-profile

thì mkdtemp() đã tạo subdirectory bên trong protected profile trước khi guard reject.

Chrome chưa được start nên không gây SingletonLock, nhưng strict “zero modification” invariant đã bị phá.

Nên validate trusted temp root trước:

~/.omnibrowser/runtime/

rồi:

Python
Run
mkdtemp(dir=trusted_runtime_root)
Severity summary
Severity	Finding
CRITICAL	_load_state() merge semantics cho phép stale process resurrect revoked leases / rollback fencing
CRITICAL	SIGKILL giữa external Chrome side-effect và state commit tạo orphan process/context; flock không giải quyết distributed transaction
CRITICAL	Auth identity sanitization collision có thể làm identity A đọc auth snapshot identity B
MAJOR	_save_state/_load_state swallow all errors
MAJOR	Admission check trước flock → capacity TOCTOU
MAJOR	pre_reserved_lease_id không được validate khi activation
MAJOR	Watchdog không được wiring thành runtime monitoring loop
MAJOR	Watchdog DRAINING state và persisted daemon state có thể diverge
MAJOR	Lease không có browser_instance_id; daemon ownership vẫn dựa trên CDP URL
MAJOR	One-shot /json/list có thể fallback về synthetic TargetID
MAJOR	“inter-process” test thực tế chỉ là multithread test
MAJOR/portable	Nested _state_lock có thể self-block trên Linux
MINOR/MAJOR	Vault len(files)+1 có thể overwrite immutable version khi có gap
MINOR	Snapshot→latest là two-file commit, SIGKILL có thể để pointer stale
MINOR	mkdtemp() diễn ra trước protected-profile validation
MINOR	macOS telemetry failure vẫn trả synthetic 1024MB; với default threshold điều này có thể fail-open cho Class I/A 

admission_controller

Phán quyết
NOT APPROVED FOR v2.2.1 — YET

Tôi không còn thấy kiến trúc “sai nền” như v2.2. Các fix chính đã đưa implementation đến gần production hơn rất nhiều.

Nhưng ba blocker sau phải đóng trước approval:

1. authoritative state reload / no stale resurrection
2. crash-consistent resource transaction + orphan recovery
3. collision-free AuthStateVault identity mapping

Sau ba cái đó, tôi sẽ yêu cầu thêm:

real multiprocessing tests
SIGKILL fault-injection tests
admission re-check/reservation
deterministic CDP Target.createTarget
drain state single-source-of-truth

Nếu các patch đó pass, tôi kỳ vọng vòng review tiếp theo có khả năng đạt APPROVED FOR v2.2.1 mà không cần thay Hybrid Bulkheaded architecture.

Ba bước có giá trị nhất tiếp theo là:

Tôi có thể viết Task 007.1 remediation spec với patch-level pseudocode cho từng CRITICAL/MAJOR ở trên.

Tôi có thể thiết kế 10 bài multiprocessing + SIGKILL chaos tests tái hiện chính xác các failure traces này.

Tôi có thể đề xuất một CLI-safe WAL/recovery protocol để giữ kiến trúc không-resident-daemon nhưng vẫn crash-consistent.