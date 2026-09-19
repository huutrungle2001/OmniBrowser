Tôi đã audit trực tiếp bản session_router(3).py, contracts(1).py và test_concurrency_broker(4).py, đồng thời đối chiếu các test mới với đúng failure traces của vòng trước.

Phán quyết

NOT APPROVED FOR v2.2.3.

v2.2.3 đã tiến một bước rất lớn: fail-closed exception propagation, file fsync, PID-file recovery, CDP context sweeping, provisioning cleanup và dead-process detection đều là các cải thiện thực chất. BrokerStateUnavailableError cũng được đưa vào contract đúng cách. 

contracts

Tuy nhiên, adversarial review lần này phát hiện 3 CRITICAL + 4 MAJOR. Đặc biệt có một race mới giữa constructor và provisioning mà 68 tests hiện tại không bắt được.

Severity	Finding	Verdict
CRITICAL	SessionRouter.__init__() chạy destructive _reconcile_orphans() mà không acquire state.lock	Blocker
CRITICAL	close() vẫn có thể revoke lease của client/process khác	Blocker
CRITICAL	PID-reuse protection bị bypass trong release_lease(); có đường kill PID trực tiếp	Blocker
MAJOR	_load_state() loại dead Class-S daemon trước khi reconciler invalidate leases → ghost leases	Must fix
MAJOR	Popen → chrome.pid vẫn có SIGKILL window không thể recover	Must fix
MAJOR	Atomic rename chưa đạt strict power-loss durability vì thiếu directory fsync	Hardening required
MAJOR	Save-failure rollback chưa rollback toàn bộ browser resources/in-memory transaction	Must test/fix
1. Fail-closed persistence: tốt hơn nhiều, nhưng chưa đạt strict durability

Phần này nhìn chung đã được sửa đúng về process-crash consistency.

_save_state() hiện:

write temp
→ flush()
→ fsync(temp)
→ atomic replace(state.json)
→ propagate BrokerStateUnavailableError

thay vì swallow exception. 

session_router

_load_state() cũng propagate mọi lỗi thay vì tiếp tục với stale memory. 

session_router

Test test_save_state_failure_fails_closed và corrupted-state test đúng hướng: chúng xác nhận operation không report success khi persistence thất bại. 

test_concurrency_broker

Nhưng chưa phải strict crash/power-loss durability

Sau:

Python
Run
temp_file.replace(self.state_file)

không có:

Python
Run
dir_fd = os.open(self.state_dir, os.O_DIRECTORY)
os.fsync(dir_fd)

Trên Linux, fsync(file) không đảm bảo directory entry của rename đã xuống persistent storage; manual yêu cầu fsync() directory riêng nếu guarantee đó cần thiết. 
man7.org

Python bảo đảm replace() atomic nếu thành công, nhưng atomic visibility và crash durability là hai chuyện khác nhau. 
Python documentation

Với macOS, nếu specification thực sự yêu cầu chống cả sudden power loss, Apple còn lưu ý fsync() không nhất thiết flush hardware cache; F_FULLFSYNC cung cấp guarantee mạnh hơn. 
Apple Developer
+1

Vì vậy câu trả lời cho Q1 là:

Process-level fail-closed: yes. Strict power-loss durable commit: not yet.

Tôi khuyến nghị sequence:

write tmp
fsync(tmp)
rename(tmp, state.json)
fsync(state_dir)

và nếu macOS power-loss durability nằm trong contract, cân nhắc F_FULLFSYNC cho state file.

2. CRITICAL — constructor phá chính serializability của flock

Đây là finding quan trọng nhất vòng này.

Constructor hiện gọi:

Python
Run
self._load_state()

trực tiếp, không qua _state_lock(). 

session_router

Trong khi _load_state() không còn là read-only. Cuối function nó gọi:

Python
Run
self._reconcile_orphans()

session_router

Mà reconciler có destructive CDP operation:

Target.getBrowserContexts
→ nếu context không có trong persisted active leases
→ Target.disposeBrowserContext

session_router

Concrete failure trace

Giả sử D1 là Class-S daemon đã persisted.

P1: request_lease()
    acquire state.lock
    load disk state
    Target.createBrowserContext()
        → C_NEW

    [C_NEW chưa persist thành lease]

                    P2 starts
                    SessionRouter.__init__()
                    ↓
                    _load_state()      <-- KHÔNG flock
                    disk chưa có C_NEW
                    ↓
                    _reconcile_orphans()
                    Target.getBrowserContexts()
                    sees C_NEW
                    ↓
                    dispose C_NEW

P1:
    Target.createTarget(... C_NEW ...)
    → browserContextId invalid / provisioning fails

Nghĩa là một CLI process mới khởi động có thể phá transaction đang được process khác bảo vệ bằng flock.

Đây không phải theoretical micro-edge. Window bao phủ toàn bộ thời gian:

createBrowserContext
→ createTarget
→ create_lease
→ register target
→ state commit
Fix bắt buộc

Không một destructive reconciliation nào được chạy ngoài inter-process lock.

Tôi sẽ refactor:

_load_state_from_disk()
    pure reconstruction only
    NO process kill
    NO CDP dispose

with _state_lock():
    _load_state_from_disk()
    _reconcile_orphans()
    mutate...
    _save_state()

Constructor cũng phải acquire cùng lock:

with _state_lock():
    load
    reconcile

hoặc có initialization-specific lock path.

3. CRITICAL — close() vẫn có thể revoke lease của client khác

Fix hiện tại chỉ ngăn daemon recycle nếu active_on_daemon > 0.

Nhưng trước đó close() vẫn làm:

Python
Run
active_ids = [
    l.lease_id
    for l in self.lease_manager.get_active_leases()
]

for lid in active_ids:
    self.release_lease(lid)

session_router

Trong mô hình authoritative shared state, lease_manager không chứa “leases owned by this Python object”; nó chứa tất cả leases load từ global state file.

Failure trace đơn giản
P1:
    Lease L1 active

P2:
    SessionRouter()
    constructor _load_state()
    → P2 now sees L1

P2:
    close()

active_ids = [L1]

release_lease(L1)
→ revoke L1
→ dispose P1's BrowserContext

Không cần race.

Một Router mới chỉ cần được tạo rồi close() là có khả năng teardown resource của Router khác.

Nếu close() được sử dụng như normal object cleanup — và tên/API hiện tại cho thấy như vậy — đây là cross-client isolation violation.

Semantics nên là
close()
    close only local handles
    DO NOT revoke globally persisted leases
    DO NOT kill globally shared daemons

Nếu muốn global shutdown:

shutdown_broker()

phải là API khác, explicit, acquire global lock, transition broker sang SHUTTING_DOWN, block admissions rồi mới revoke tất cả.

Hoặc mỗi Router phải giữ:

self._locally_created_lease_ids

và close() chỉ release các ID đó.

4. CRITICAL — PID-reuse protection chưa áp dụng end-to-end

Helper hiện tại:

Python
Run
_is_matching_chrome_process(pid, expected_udir)

kiểm command chứa chrome/chromium và expected directory. 

session_router

Reconciler dùng helper này trước một số kill(), đây là improvement tốt. 

session_router +1

Nhưng release_lease() cross-process vẫn làm:

Python
Run
os.kill(pid, SIGTERM)
sleep
os.kill(pid, SIGKILL)

không verify process identity. 

session_router

Concrete PID-reuse failure
Lease L:
    persisted PID = 8142
    user_data_dir = X

Chrome 8142 unexpectedly dies

OS later:
    unrelated process gets PID 8142

_reconcile:
    _is_process_alive(8142) == True

Liveness check chỉ biết PID đang sống, không xác nhận đó còn là Chrome của lease. 

session_router

Sau đó:

release_lease(L)
→ raw os.kill(8142)
→ unrelated process terminated

Đây chính xác là failure mà PID-reuse hardening đáng lẽ phải loại bỏ.

_handle_daemon_recycle() cũng có cross-process raw PID kill khi không có local Popen. 

session_router

Fix

Tất cả signalling phải đi qua duy nhất:

_safe_kill_browser(
    pid,
    expected_user_data_dir,
    expected_process_start_time
)

Không cho phép os.kill() resource PID trực tiếp ở bất kỳ caller nào.

Và _is_process_alive() cho persisted browser resource nên thực ra là:

PID alive
AND executable matches Chrome
AND exact --user-data-dir=<expected>
AND process-start fingerprint matches
5. MAJOR — dead Class-S lease invalidation có ordering bug

_load_state() chỉ đưa daemon vào new_daemons nếu:

process alive
AND CDP alive

rồi replace toàn bộ pool:

Python
Run
self._class_s_daemons = new_daemons

và sau đó gọi _reconcile_orphans(). 

session_router

Nhưng reconciler chỉ invalidate Class-S leases bằng cách iterating các daemon còn nằm trong _class_s_daemons. 

session_router

Do đó:

Disk:
    Lease L1 ACTIVE → daemon D1

D1 dies

status()
  _state_lock()
    _load_state()

      reconstruct L1 ACTIVE

      D1 dead
      → omitted from new_daemons

      self._class_s_daemons = {}

      _reconcile_orphans()
          no D1 to inspect

  status sees L1 ACTIVE
  save state:
      L1 ACTIVE
      D1 removed

Từ đây lease đã thành permanent ghost until lease timeout.

Nó tiếp tục có thể:

giữ auth identity;

chiếm admission capacity;

trả dead CDP URL.

Tại sao test vẫn pass?

Test hiện tại kill daemon rồi gọi trực tiếp:

Python
Run
router._reconcile_orphans()

khi dead daemon record vẫn còn trong _class_s_daemons. 

test_concurrency_broker

Đây không phải public path thực tế, nơi _state_lock() chạy _load_state() trước.

Test cần đổi thành tối thiểu:

kill daemon
router.status()
assert lease inactive

và quan trọng hơn:

kill daemon
router2 = SessionRouter(shared_state_dir)
router2.status()
assert lease inactive

Tôi dự đoán bản hiện tại sẽ fail test thứ hai.

6. MAJOR — vẫn còn crash window Popen → chrome.pid

PID file được viết ngay sau Popen, đúng hướng:

Python
Run
process = subprocess.Popen(...)
(Path(temp_dir) / "chrome.pid").write_text(...)

session_router

Nhưng “immediately” không có nghĩa “atomically”.

Failure trace
tempdir created

Popen()
→ Chrome PID 9001 successfully exists

       SIGKILL broker HERE

chrome.pid never written

Reconciler thấy:

unreferenced runtime directory
no chrome.pid

sau 30s nó xóa directory, nhưng không có PID để kill Chrome. 

session_router

Chrome 9001 có thể tiếp tục chạy với một user-data-dir vừa bị xóa.

Vì vậy PID file không đủ để claim recovery khỏi all SIGKILL provisioning windows.

Robust pattern

PID file là fast path; process-table discovery là fallback:

scan processes owned by current UID

for every Chrome:
    parse exact --user-data-dir=<path>

    if path resolves under runtime_root
       AND path not in committed resources:
          orphan

Unique runtime dir chính là durable operation identity đủ tốt cho discovery.

7. Save failure vẫn không phải full transaction rollback

test_save_state_failure_fails_closed xác nhận exception và identity unlock. 

test_concurrency_broker

Nhưng xét Class S:

create context
create target
create lease
register target
return lease
↓
_state_lock.__exit__
↓
_save_state()
FAIL

Exception phát sinh khi context manager đang exit — tức là sau khi inner provisioning try đã thành công.

Outer handler rollback identity, nhưng không rollback:

BrowserContext
Target
Lease object
TargetRegistry entry
possibly newly-created daemon

Request đúng là không return success, nên fail-closed API semantics đã tốt hơn.

Nhưng statement:

“zero orphaned locks”

đúng hơn statement:

“zero orphaned resources.”

Resources chỉ được xử lý vào một reconciliation sau đó.

Tôi sẽ thêm test sau save failure:

assert no active in-memory lease
assert target registry baseline
assert BrowserContext baseline
assert no uncommitted dedicated Chrome
8. Orphan BrowserContext sweeping: bản thân mechanism là đúng

Phần này tôi đánh giá tích cực.

CDP Target.getBrowserContexts chính thức trả các context được tạo bởi Target.createBrowserContext, và Target.disposeBrowserContext đóng context cùng các page thuộc nó. 
Chrome DevTools

Test hiện tại tạo một context ngoài lease, reconcile rồi kiểm orphan biến mất trong khi leased context vẫn còn. Đây là test có giá trị. 

test_concurrency_broker

Vấn đề không nằm ở primitive; vấn đề là khi nào sweeper được phép chạy. Constructor race ở trên khiến một context “chưa commit” bị nhầm thành orphan.

Vì vậy invariant nên là:

No orphan sweeping outside state.lock.
9. _is_matching_chrome_process() còn nên harden thêm

Ngay cả sau khi áp dụng helper ở mọi kill path, implementation hiện tại vẫn là substring test:

Python
Run
"chrome" in cmd.lower()
expected_udir in cmd

session_router

Điều này tốt hơn raw PID rất nhiều, nhưng chưa phải strong process identity.

Một unrelated command như:

some-program --label=chrome --path=/expected/runtime/dir

về nguyên tắc cũng match.

Test hiện tại chỉ chứng minh sleep 30 không match. 

test_concurrency_broker

Production hardening nên parse:

executable / comm
+
exact argument:
--user-data-dir=<resolved path>
+
process creation time
Trả lời trực tiếp 4 câu hỏi
1. _save_state() / _load_state() đã đạt production durability chưa?

Đạt process-crash fail-closed semantics khá tốt; chưa đạt strict power-loss durability.

Thiếu parent-directory fsync sau rename; trên macOS nếu yêu cầu persistence rất mạnh thì còn phải quyết định có cần F_FULLFSYNC hay không. 
man7.org
+1

Ngoài ra save-failure chưa rollback toàn bộ external resources.

2. Orphan recovery có robust với mọi SIGKILL scenario chưa?

Chưa.

Hai counterexample quan trọng nhất là:

Popen → SIGKILL → before chrome.pid

và

P1 provisioning C_NEW under flock
P2 constructor → _load_state → reconcile without flock
→ disposes C_NEW

Blocker #2 do đó chưa thể coi là đóng triệt để.

3. PID reuse protection đã đủ chưa?

Chưa.

Reconciler có guard, nhưng release_lease() và daemon recycle vẫn có raw PID kill; active-lease liveness cũng chỉ kiểm PID sống thay vì process identity. 

session_router

4. Production approval?

Không cấp APPROVED FOR v2.2.3 ở trạng thái hiện tại.

Tôi sẽ yêu cầu một patch v2.2.3a với 5 acceptance gates sau:

Mọi _load_state + reconcile + mutate + save đều chạy dưới cùng inter-process lock; constructor tuyệt đối không destructive-reconcile ngoài lock.

close() không được revoke global/foreign leases; tách explicit broker shutdown API.

Mọi PID signal đi qua một verified process-identity kill helper; không còn raw persisted-PID os.kill.

Dead daemon phải invalidate leases trước khi daemon record bị drop; thêm public-path/cross-process chaos test.

Orphan process discovery phải có fallback OS process scan để đóng Popen → pidfile SIGKILL window.

Sau 5 điểm này, kiến trúc CLI + flock không còn lý do bắt buộc phải chuyển sang resident RPC daemon; nó có thể đạt một mức crash-consistency đủ nghiêm túc cho OmniBrowser.