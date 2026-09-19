Tôi đã attack lại code v2.2.2 theo cả normal concurrency, SIGKILL, partial provisioning, stale process state và crash-consistency. Kết quả: hai trong ba CRITICAL cũ đã được đóng tốt; CRITICAL #2 mới được đóng một phần. Ngoài ra còn một failure mode persistence đủ nghiêm trọng để tôi chưa thể cấp APPROVED FOR v2.2.2.

Phán quyết

NOT APPROVED FOR v2.2.2 — còn 2 blocker cần đóng.

Hạng mục cũ	Kết quả
CRITICAL #1 — stale-state resurrection	FIXED trong trường hợp state file hợp lệ
CRITICAL #2 — SIGKILL/orphan resources	PARTIALLY FIXED
CRITICAL #3 — cross-identity vault collision	FIXED
Admission TOCTOU	FIXED
Reservation validation	FIXED
Synthetic TargetID	FIXED
Nested flock	FIXED
Daemon generation binding	FIXED đáng kể
Vault gap versioning	FIXED
Real multiprocessing flock test	FIXED
1. CRITICAL #1 — Resurrection bug

Phần này về cơ bản đã được sửa đúng.

_load_state() giờ reconstruct new_leases, new_identities, clear/rebuild target registry và sau đó swap authoritative state thay vì merge conditionally. browser_instance_id cũng được restore từ disk. 

session_router

Test stale-router cũng đúng failure trace mà tôi nêu vòng trước: R1 giữ state cũ, R2 revoke trên disk, sau đó R1 gọi status() và không được resurrect lease. 

test_concurrency_broker

Kết luận: resurrection do normal multi-process interleaving đã đóng.

Nhưng còn một failure mode persistence

_load_state() khi JSON corrupt hoặc gặp exception chỉ:

Python
Run
print("Warning...")

rồi return; _state_lock() sau đó vẫn có thể tiếp tục vào yield. Tương tự, _save_state() bắt toàn bộ exception và chỉ in warning. 

session_router +1

Điều này tạo failure trace:

disk state = authoritative S1

Router has stale S0 in memory

_load_state()
    disk read / semantic decode fails
    warning only
    stale S0 remains

operation mutates S0

_save_state()
    succeeds

disk <- stale-derived S0'

Hoặc nguy hiểm hơn:

request_lease()
  Chrome provisioned
  Lease L created

_save_state()
  ENOSPC / permission error
  warning only

request_lease() returns SUCCESS

CLI tiếp theo không biết Lease L tồn tại.

Với một broker dựa trên filesystem state, persistence failure phải fail closed.

Tôi xếp đây là:

CRITICAL — control-plane durability/invariant failure

Fix:

_load_state failure
    → BrokerStateUnavailableError
    → no mutation allowed

_save_state failure
    → operation must not report success

Và nên fsync(temp_fd) trước rename + fsync(state_dir) sau rename nếu muốn crash durability thực sự.

2. CRITICAL #2 — SIGKILL & orphan resources

Đây là blocker lớn nhất còn lại.

runtime_root là improvement tốt: nó được validate trước và browser tempdirs đều nằm bên dưới trusted runtime root. 

session_router

_reconcile_orphans() cũng xử lý được:

runtime directories không còn referenced sau 60 giây;

dedicated process nếu PID đã nằm trong _dedicated_processes_data và lease không còn active. 

session_router

Nhưng nó không đóng crash window quan trọng nhất.

Failure trace A — SIGKILL sau Popen, trước commit
P1 acquires state.lock

P1:
    tempdir X created
    Popen Chrome PID=9132
    Chrome successfully starts

        ↓ SIGKILL HERE ↓

lease not created/persisted
_dedicated_processes_data does not contain PID 9132

P2 về sau:

_reconcile_orphans()

sees X unreferenced
after 60s:
    rm -rf X

Nhưng không có code nào discover PID 9132.

Process đó chưa từng được ghi vào _dedicated_processes_data, nên loop lines 210–225 không biết nó tồn tại. 

session_router

Xóa user-data-dir không tương đương kill process.

Failure trace B — orphan Class S BrowserContext

Còn tinh tế hơn:

existing healthy daemon D1

Target.createBrowserContext()
    → context C99

        ↓ SIGKILL ↓

before lease/state commit

D1 vẫn sống.

C99 không thuộc lease nào.

Current reconciler:

scan filesystem directories;

scan persisted dedicated PID records.

Nó không gọi:

Target.getBrowserContexts

và không diff:

actual contexts - leased contexts

Vì thế C99 có thể sống tới lúc daemon recycle.

CDP có Target.getBrowserContexts chính xác để enumerate các context đã tạo bằng Target.createBrowserContext.

Failure trace C — normal exception, không cần SIGKILL

Class S:

Python
Run
ctx = Target.createBrowserContext()   # success
Target.createTarget(...)              # throws

outer exception handler chỉ rollback identity reservation. Nó không dispose ctx. 

session_router

Class I/A:

Popen Chrome        SUCCESS
get_browser_ws_url  SUCCESS
Target.createTarget FAIL

_spawn_dedicated_process() raises trước khi return (process,temp_dir) cho caller; caller vì vậy không có cleanup handle. 

session_router

Đây là immediate orphan path.

Test hiện chưa bắt được nó

test_orphan_reconciler chỉ:

mkdir dummy directory
set mtime to 75s ago
_reconcile_orphans()
assert directory gone

Không có Chrome PID, SIGKILL, orphan context hoặc injected CDP failure. 

test_concurrency_broker

Do đó test pass không chứng minh CRITICAL #2.

3. CRITICAL #3 — Vault identity collision

Phần này tôi xác nhận đã sửa tốt.

Directory derivation hiện dùng SHA-256-based identity key thay vì destructive sanitization; snapshot lưu cả identity và canonical_identity; read path fail closed nếu requested identity không match. 

auth_state_vault +2

Gap-safe versioning cũng đúng:

Python
Run
max(numeric_versions, default=0) + 1

auth_state_vault

Và test v1 + v3 → v4 kiểm đúng bug trước đây. 

test_concurrency_broker

Một hardening nhỏ: các bạn truncate SHA-256 xuống 128 bit ([:32]). Thực tế đã đủ mạnh cho use case này, đặc biệt vì canonical identity được verify sau read, nhưng dùng full 64 hex chars gần như miễn phí và tránh phải gọi nó “collision-free” theo nghĩa tuyệt đối.

Không phải blocker.

4. Admission TOCTOU

Đã sửa đúng.

Admission count được lấy sau authoritative reload, bên trong _state_lock(), và provisioning cũng diễn ra trước khi lock được nhả. 

session_router

Hai process không còn cùng nhìn thấy N=max-1 rồi cùng allocate.

Trade-off bây giờ là:

global lock held
    while Chrome starts / CDP calls execute

nên concurrency throughput thấp hơn, nhưng correctness đúng.

Đối với 3–10 local agents đây là trade-off chấp nhận được cho v2.2.x.

5. Pending reservation validation

Fix đúng về logic.

Khi activate pre-reserved lease, code kiểm:

reservation tồn tại/chưa hết TTL;

identity đúng;

exclusive reservation thực sự là owner;

shared reservation nằm trong shared set. 

lease_manager

Tuy nhiên còn một resource-cleanup implication:

nếu reservation hết 60s sau khi browser resource đã provision thành công, create_lease() sẽ raise, nhưng outer exception handler chỉ rollback identity; Chrome/context vừa tạo vẫn leak. 

session_router

Với timeout hiện tại, case >60s không thường gặp, nhưng cùng cleanup bug đã tồn tại qua các CDP exception bình thường.

Tôi xếp root problem này là MAJOR, và nên giải quyết bằng resource transaction/guard thay vì tăng TTL.

6. Deterministic TargetID

Đã sửa đúng.

Class I/A không còn /json/list hoặc synthetic fallback mà gọi thẳng:

Target.createTarget
→ real targetId

session_router

Class S cũng lấy target ID trực tiếp từ CDP. 

session_router

Timing race trước đây đã biến mất.

7. Multiprocessing lock test

Đây hiện thực sự là multi-process test:

4 multiprocessing.Process
multiprocessing.Barrier
independent SessionRouter instances
shared state.lock

test_concurrency_broker

Nó chứng minh flock đang serialize critical section xuyên process đúng như mong muốn.

Lưu ý: test đang bảo vệ một external counter file, không stress actual concurrent:

request lease
renew
release
identity reservation
daemon spawn

Nhưng đối với câu hỏi “flock có thực sự inter-process không?”, test này hợp lệ.

Các failure mode mới/còn lại
CRITICAL — persistence errors vẫn fail-open

Như trên, _save_state()/_load_state() chỉ warn. 

session_router

Trong CLI architecture, disk state chính là coordination database. Nếu database commit fail mà API vẫn trả success, mutual exclusion không còn được đảm bảo.

Phải sửa trước approval.

CRITICAL — orphan discovery chưa đủ

Reconciler chỉ biết những PID đã commit. 

session_router

Muốn giữ non-resident CLI architecture, cần một trong hai pattern:

A. Runtime discovery
   scan OS process table
   find Chrome processes whose --user-data-dir nằm dưới runtime_root
   compare với committed instance/lease set
   kill orphan

và:

B. Browser reconciliation
   for each living Class S daemon:
       Target.getBrowserContexts
       minus lease.browser_context_id set
       → dispose orphan contexts

Tốt hơn nữa, thêm operation manifest:

runtime/<operation-id>/manifest.json
    state=PROVISIONING
    created_at
    class
    pid
    user_data_dir

được durable-write trước/Ngay sau resource creation.

MAJOR — PID reuse có thể kill nhầm process

Ở cross-process cleanup, code làm:

Python
Run
os.kill(pid, SIGTERM)
...
os.kill(pid, SIGKILL)

chỉ dựa vào numeric PID. 

session_router

Tương tự release persisted process cũng kill raw PID. 

session_router

Concrete trace:

Chrome PID 5000 dies unexpectedly
state still says PID 5000

OS later reuses PID 5000
for unrelated user process

lease expires
reconciler runs

SIGTERM/SIGKILL → unrelated process

Persist thêm process fingerprint:

pid
create_time
executable
expected user_data_dir

và verify trước signal.

psutil.Process(pid).create_time() + cmdline containing exact runtime-root --user-data-dir là đủ tốt cho local broker.

MAJOR — dead Class S daemon không invalidate leases

Authoritative loader chỉ thêm daemon nếu:

PID exists AND CDP alive

nếu daemon chết, nó bị bỏ khỏi new_daemons. 

session_router

Nhưng các Lease có:

browser_instance_id = dead-daemon-id
is_active = true

không bị revoke/LOST.

browser_instance_id đã được thêm rất đúng vào contract. 

contracts

Nhưng cần reconciliation:

active Class-S lease
AND browser_instance_id not in live daemon set
    → LOST / revoked
    → release identity
    → remove from admission count

Nếu không, daemon crash vẫn để lại ghost leases tới expiry.

MAJOR — dedicated dead process không được detect khi lease active

Comment nói reconciler xử lý “dead or unreferenced dedicated processes”, nhưng implementation chỉ vào cleanup branch khi:

Python
Run
if not lease or not lease.is_active:

Không có pid_is_alive check cho active lease. 

session_router

Nếu dedicated Chrome crash:

lease ACTIVE
PID dead

thì lease tiếp tục:

chiếm admission slot;

giữ auth identity;

trả CDP endpoint chết;

giữ tempdir như active.

Nên mark lease LOST ngay khi process fingerprint không còn sống.

MAJOR — close() chưa an toàn trong multi-process model

close() lấy:

Python
Run
active_ids = self.lease_manager.get_active_leases()

trước khi authoritative reload dưới state lock. Sau đó cuối cùng acquire state lock và recycle mọi Class S daemon. 

session_router

Failure trace:

Router R1 created earlier
R1 memory: no active leases

R2 later creates Class-S lease L on daemon D

R1.close()
    active_ids from stale memory = []

    acquire state lock
    authoritative reload sees L + D

    for every daemon:
        _handle_daemon_recycle(D)

D killed
L remains active

Nếu close() chỉ là explicit global administrator shutdown, cần broker-level SHUTTING_DOWN state để block new leases rồi revoke all leases authoritatively.

Nếu nó là normal object cleanup, nó không được kill shared global daemons.

Đây cần định nghĩa semantics rõ trước production.

Những phần tôi xem là đã đạt yêu cầu v2.2.2

Các fix này tôi không còn objection đáng kể:

exact fencing equality;

shared/exclusive identity set model;

pending reservation validation;

trusted runtime root;

profile constructor guards;

deterministic CDP target creation;

authoritative normal-state reload;

browser_instance_id;

admission check dưới inter-process lock;

gap-safe vault version;

latest pointer reconciliation;

actual multiprocessing.Process lock test.

Đặc biệt exact fencing vẫn đúng ở LeaseManager: mọi token được cung cấp phải bằng current token. 

lease_manager

Test matrix còn thiếu trước approval

Tôi sẽ thêm tối thiểu 7 chaos tests:

SIGKILL after Popen() before state commit → không còn Chrome process sau recovery.

SIGKILL after Target.createBrowserContext before lease commit → context bị orphan sweeper dispose.

Inject Target.createTarget failure sau successful Class-S context creation → context count trở về baseline.

Inject Target.createTarget failure sau dedicated Popen → PID và tempdir biến mất.

Monkeypatch _save_state() thành ENOSPC → request_lease() phải fail, không trả successful lease.

Kill Class-S daemon có 3 active leases → cả 3 chuyển LOST/revoked, identity/capacity được release.

Simulate PID reuse → reconciler từ chối kill process có create-time/cmdline fingerprint không match.

Hiện test orphan chỉ kiểm filesystem cleanup nên không đủ bao phủ các case trên. 

test_concurrency_broker

Kết luận cuối cùng

CRITICAL #1: fixed đối với normal multi-process state; cần fail-closed persistence để hoàn tất production semantics.

CRITICAL #2: chưa fixed triệt để. Reconciler mới xử lý committed resource metadata và orphan directories; chưa xử lý uncommitted Chrome processes hoặc orphan BrowserContexts.

CRITICAL #3: fixed.

Vì vậy tôi chưa cấp:

APPROVED FOR v2.2.2

Hai blocker bắt buộc là:

1. Fail-closed durable state persistence
2. Real process/context orphan discovery + cleanup

Sau đó các MAJOR quan trọng nhất là PID fingerprinting, dead-instance → LOST lease reconciliation, và định nghĩa lại semantics của close().

Nếu triển khai vòng cuối, tôi có thể:

Viết Task 007.2 final-hardening spec chỉ tập trung vào 2 blocker + 3 MAJOR này.

Soạn SIGKILL fault-injection test suite với exact injection points cho Popen, createBrowserContext, createTarget và _save_state.

Thiết kế crash-consistent runtime manifest/WAL protocol để OmniBrowser vẫn giữ mô hình CLI + flock mà không cần resident broker daemon.