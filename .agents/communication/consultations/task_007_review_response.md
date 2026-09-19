Tôi đã đọc toàn bộ 10 file đính kèm và đối chiếu thêm với CDP/Chromium hiện tại. Có một giới hạn phạm vi: báo cáo Task 007 còn tham chiếu contracts.py, scripts/cdp_controller.py, page_manager.py và AGENTS.md, nhưng bốn file đó không nằm trong 10 attachment hiện tại, nên tôi không thể xác nhận tuyệt đối rằng mọi command path đều enforce lease/fencing ở boundary CLI. 

task-007-browser-concurrency-an…

Kết luận tổng thể

Hướng kiến trúc v2.2 là đúng, nhưng implementation hiện chưa an toàn cho mô hình “nhiều CLI process thực sự chạy đồng thời”. Các test hiện tại chứng minh happy path và isolation cơ bản khá tốt — báo cáo ghi nhận 8/8 broker tests và 50/50 toàn repo đều pass. 

task-007-browser-concurrency-an…

 Nhưng adversarial review phát hiện vài lỗi thuộc lớp control-plane mà test hiện tại chưa đánh trúng.

Tôi sẽ coi các vấn đề sau là ship blockers trước khi gọi v2.2 là concurrency-safe:

Severity	Finding	Hậu quả
BLOCKER	state.json không có inter-process transaction	Lost update, duplicate fencing token, identity double-acquire, double daemon
BLOCKER	Drain dùng một singleton "class-s-daemon"	Spawn daemon mới làm mất tracking daemon cũ đang drain
CRITICAL	Provision resource trước khi reserve lease/identity	Identity conflict có thể leak BrowserContext hoặc Chrome process
CRITICAL	Crash Class S không invalidate/migrate leases cũ	5 lease vẫn “ACTIVE” nhưng trỏ vào CDP/context đã chết
CRITICAL	Cross-process reload không reconstruct process ownership	Class I/A có thể trở thành orphan process
CRITICAL	AuthStateVault “immutable” không atomic giữa process	Tôi đã tái hiện actual corruption/race
HIGH	Fencing validation chấp nhận token tương lai	Token current+1000 vẫn hợp lệ
HIGH	Watchdog chưa thật sự chạy và chỉ đo parent RSS	2 GB drain threshold có thể không bao giờ phản ánh Chrome tree thật

Đặc biệt, tôi đã chạy một vài adversarial micro-tests trực tiếp trên code attachment. Với hai process đồng thời save_snapshot("same-user") trong 20 vòng, 16 operation lỗi và 18/20 vòng chỉ còn dưới 2 version; có trường hợp cả hai process cùng trả v1, nhưng cuối cùng chỉ có một v1.json. Tôi cũng tái hiện được việc fencing token 1001 được chấp nhận khi token hiện tại là 1, và bug shared identity khiến một exclusive lease có thể được cấp trong khi một shared lease cũ vẫn còn active.

1. Race Conditions & Concurrency
1.1 os.replace() đang giải quyết sai lớp vấn đề

_save_state() tạo temp file rồi replace(state.json). Điều này tốt để tránh torn JSON, nhưng không cung cấp serializability giữa các process. Router chỉ có threading.RLock, tức chỉ khóa các thread trong cùng Python process. 

session_router

Hiện _save_state() serialize toàn bộ leases, fencing counter, identities, daemon và targets thành một snapshot rồi rename. 

session_router

Interleaving thực tế có thể là:

state: fencing_counter = 10

CLI P1                       CLI P2
------                       ------
load counter=10              load counter=10
create lease A/token=10      create lease B/token=10
save {A,counter=11}
                             save {B,counter=11}

Final state.json:
    B exists
    A disappeared
    counter = 11

But Agent A is already running.

Atomic rename chỉ đảm bảo final file là toàn bộ P1 hoặc toàn bộ P2, không đảm bảo update của P1 và P2 được merge.

Tệ hơn, _save_state() nuốt mọi exception:

Python
Run
except Exception:
    pass

và _load_state() cũng fail-open theo cách tương tự. 

session_router

Với một security/control-plane state store, đây là pattern rất nguy hiểm. Nếu state corrupt hoặc disk full, broker có thể tiếp tục chạy với một reality khác filesystem mà caller không biết.

Khuyến nghị

Nếu vẫn giữ multi-process CLI trực tiếp, tối thiểu phải dùng một separate lock file và giữ fcntl.flock(LOCK_EX) xuyên suốt:

LOCK
  load latest state
  validate/admit
  mutate
  fsync(temp)
  rename
  fsync(directory)
UNLOCK

Không khóa trực tiếp state.json, vì rename thay inode.

Tuy nhiên tôi không khuyến nghị tiếp tục theo mô hình đó.

Giải pháp tốt hơn là:

Một resident omnibrowser-broker process duy nhất sở hữu Chrome processes + authoritative in-memory state; mọi CLI chỉ là thin RPC client qua Unix Domain Socket.

Như vậy threading.RLock/async lock thực sự có ý nghĩa, process handles không phải serialize/restore, watchdog và CDP socket có lifecycle thật sự liên tục.

Nếu muốn durable state nữa, dùng SQLite WAL cho journal/checkpoints, thay vì dùng JSON làm distributed database.

1.2 Fencing token hiện có một bug correctness rõ ràng

validate_lease() chỉ reject khi:

Python
Run
fencing_token < lease.fencing_token

nên bất kỳ token lớn hơn hiện tại đều hợp lệ. 

lease_manager

Tôi đã kiểm chứng:

current token = 1

token 1      -> ACCEPTED
token 1001   -> ACCEPTED

Ở broker boundary, condition đúng phải là:

Python
Run
provided_token == lease.fencing_token

với mutating commands.

Ngoài ra fencing_token=None hiện được chấp nhận. Nếu fencing là security invariant, mutating actions không nên được phép bỏ token.

Một nuance còn quan trọng hơn:

Fencing token chỉ có giá trị nếu mọi CDP command bắt buộc đi qua broker.

Chrome không biết lease_id hay fencing token là gì. Nếu agent được biết raw cdp_url và có thể tự mở WebSocket tới Chrome, lease đã revoke cũng không ngăn agent thao tác trực tiếp.

Vì Lease hiện chứa cdp_url, tôi sẽ coi đây là hazard cho tới khi kiểm tra được cdp_controller.py. Giải pháp mạnh nhất là broker giữ CDP socket và không expose raw daemon CDP endpoint cho agent.

1.3 Identity lock chỉ đúng cho exclusive-only

_active_identities hiện là:

Python
Run
identity -> (lease_id, exclusive)

nó chỉ có khả năng nhớ một lease. 

lease_manager

Nếu cho phép hai non-exclusive leases:

L1 shared
L2 shared

L2 overwrite L1 trong map.

Sau đó revoke L2:

identity map = empty

mặc dù L1 vẫn active.

Lúc này exclusive L3 có thể được cấp.

Tôi đã tái hiện chính xác interleaving đó trên attached module.

Nên biểu diễn:

IdentityLock {
    exclusive_owner: lease_id | None
    shared_owners: set[lease_id]
}

hoặc nếu OmniBrowser thực tế luôn muốn serialize authenticated accounts, hãy đơn giản hóa hẳn và cấm non-exclusive auth identity.

1.4 Admission có TOCTOU ngay cả trong một process

acquire_admission() diễn ra trước SessionRouter._lock.

Hai threads có thể đồng thời thấy:

active = 9
max = 10

cả hai đều pass admission rồi lần lượt tạo lease, cho kết quả 11.

Admission và capacity reservation phải là cùng một atomic operation:

CHECK capacity
RESERVE slot
PROVISION resource
ACTIVATE lease

Không phải:

check
...
sau đó mới create lease
1.5 Provisioning trước identity reservation gây leak

Class S hiện làm theo thứ tự:

createBrowserContext
createTarget
create_lease

Class I/A:

spawn Chrome
create_lease

Nếu create_lease() sau đó ném IdentityConflictError, context/process vừa tạo không được rollback.

Đây là case rất dễ xảy ra chính xác trong Class A, nơi identity exclusivity quan trọng nhất.

Nên thêm lease state machine:

RESERVED
    ↓
PROVISIONING
    ↓
ACTIVE
    ↓
RELEASING
    ↓
CLOSED

failure → FAILED/CLEANUP_PENDING

Identity/capacity phải được reserve ở RESERVED, trước khi Chrome resource được tạo.

2. CDP Context Lifecycle & Resource Leaks

Có tin tốt ở đây: Target.disposeBrowserContext là đúng primitive.

CDP mô tả disposeBrowserContext là xóa context và đóng toàn bộ pages thuộc context. createBrowserContext cũng được Chrome mô tả là tương tự một incognito profile. 
Chrome DevTools

Ở tầng Chromium, destruction của BrowserContext/StoragePartition chủ động shutdown Service Worker và Shared Worker; source hiện tại thậm chí ghi rõ workers có thể giữ RenderProcessHost sống nên chúng phải được shutdown trước khi BrowserContext bị destroy. 
Chromium Git Repositories
+1

Do đó:

Nếu disposeBrowserContext thành công, tôi không lo service worker bị “lọt” ra ngoài context như một leak logic thường trực.

Nhưng implementation hiện có ba leak path khác.

Leak path A — dispose failure bị nuốt

Release hiện catch mọi exception khi dispose và tiếp tục xem lease là released.

Điều đó biến:

context cleanup failed

thành:

broker forgets context_id
context vẫn tồn tại

Đây là orphan.

Nên dùng:

ACTIVE
  ↓ fence immediately
RELEASING
  ↓
disposeBrowserContext
  ↓
Target.getBrowserContexts verification
  ↓
CLOSED

Nếu dispose fail:

CLEANUP_PENDING

và reaper retry. Không xóa context ID khỏi authoritative state.

Target.getBrowserContexts được CDP cung cấp chính xác để enumerate các context tạo bởi Target.createBrowserContext. 
Chrome DevTools

Một orphan sweeper rất đơn giản:

actual contexts from Chrome
    -
contexts referenced by ACTIVE/RELEASING leases
    =
orphans → dispose
Leak path B — crash giữa createContext và persist lease

Vì context được tạo trước lease, nếu Python process chết giữa:

Target.createBrowserContext

và:

_save_state()

Chrome vẫn giữ context.

CDP có parameter disposeOnDetach cho Target.createBrowserContext: context tự dispose khi debugging session disconnects. 
Chrome DevTools

Hiện không thể bật nó trực tiếp vì SessionRouter đóng CDPClient ngay sau khi tạo target; bật lên sẽ giết context ngay lập tức.

Nhưng đây lại là một lý do mạnh để chuyển v2.3 sang persistent broker-owned CDP connection. Khi đó có thể dùng:

disposeOnDetach=true

để nếu broker chết đột ngột, Chrome tự dọn các context do connection đó sở hữu.

Leak path C — process RSS không giảm sau context disposal

BrowserContext destruction giải phóng logical storage/workers/pages; nó không đảm bảo:

RSS_before - RSS_after == memory_used_by_context

V8 allocators, renderer reuse, Skia/GPU buffers, browser/network process caches và allocator fragmentation có thể giữ committed memory trong process tree.

Do đó daemon recycling vẫn cần thiết.

Nhưng watchdog hiện đo:

Python
Run
psutil.Process(pid).memory_info().rss

chỉ cho browser parent PID, không cộng renderer/GPU/network utility child processes. 

lifecycle_watchdog

Đây là một lỗi telemetry lớn. Chrome có thể đang dùng 6 GB tổng process tree trong khi parent browser process chỉ vài trăm MB.

Nên tính:

tree_rss =
browser RSS
+ Σ renderer RSS
+ GPU RSS
+ network-service RSS
+ utility RSS

SystemInfo.getProcessInfo của CDP có thể enumerate Chrome process IDs/types; kết hợp chúng với psutil để lấy RSS. 
Go Packages
+1

Ở target level có thể bổ sung soft signals như DOM nodes, JS heap và event listener counts; Chrome DevTools cũng expose các loại metric này. 
Chrome for Developers
+1

Watchdog hiện chưa thực sự “watch”

LifecycleWatchdog có check_memory_and_drain() và record_target_crash(), nhưng trong toàn bộ attached runtime code tôi không thấy loop/background thread nào gọi RSS check, cũng không thấy CDP event bridge gọi record_target_crash().

Tests gọi check_memory_and_drain() trực tiếp, nên unit test pass không chứng minh daemon runtime sẽ tự drain. Watchdog state machine bản thân hợp lý. 

lifecycle_watchdog

CDPClient hiện cũng không hỗ trợ event dispatch: request() đọc frame liên tục và chỉ làm gì khi response.id == req_id; các notification không có id bị đọc rồi bỏ. 

cdp_client

Do đó Target.targetCreated, targetDestroyed, targetCrashed hiện chưa thể trở thành event-driven control plane qua client này.

3. Failure Domain khi Class S daemon crash/OOM

Với implementation hiện tại, giả sử daemon D1 chết khi có 5 leases.

State persisted vẫn chứa 5 lease active, identity locks và target records. Loader khôi phục các record đó từ state.json. 

session_router

Khi load daemon state, code kiểm tra PID/CDP endpoint; nếu CDP không alive thì không phục hồi _class_s_cdp_url. 

session_router

Nhưng 5 leases không bị mark LOST.

Kết quả:

D1 = dead

L1 ACTIVE → dead cdp_url/context
L2 ACTIVE → dead cdp_url/context
L3 ACTIVE → dead cdp_url/context
L4 ACTIVE → dead cdp_url/context
L5 ACTIVE → dead cdp_url/context

new request
   ↓
spawn D2
   ↓
new leases work

old leases vẫn ACTIVE cho tới expiry

Điều này gây ba side effect:

admission count có thể coi lease chết là active và gây backpressure giả;

authenticated identity có thể vẫn locked;

agent nhận lỗi network/CDP thay vì một error semantics như BrowserInstanceLost.

Cần thêm browser_instance_id / generation

Lease hiện phải biết không chỉ:

cdp_url

mà:

browser_instance_id = s-00017
browser_generation = 17

Daemon pool:

S-17  DRAINING/DEAD
  ├── L1
  ├── L2
  └── L3

S-18  HEALTHY
  ├── L4
  └── ...

Crash D17:

all leases where instance_id == S-17
    → LOST or RECOVERING

Không được dựa vào một singleton global Class-S field.

Drain Protocol hiện có một generation bug rất nghiêm trọng

Hiện chỉ có một:

_class_s_process
_class_s_dir
_class_s_cdp_url

và watchdog key cố định:

"class-s-daemon"

Nếu daemon hiện tại bị mark DRAINING, request mới không reuse nó mà launch daemon mới — đây là đúng ý tưởng.

Nhưng daemon mới lại overwrite chính các singleton fields và register_daemon("class-s-daemon", new_pid) overwrite record cũ.

Từ thời điểm đó:

old draining daemon vẫn phục vụ active leases
nhưng broker mất process/temp-dir/watchdog identity của nó

Đây không phải zero-downtime drain; nó là lost ownership.

Pool phải là:

Python
Run
instances: dict[InstanceId, BrowserInstance]

và router chọn:

HEALTHY accepting instance

trong khi DRAINING instances vẫn được giữ nguyên cho tới khi lease count của chính instance đó về zero.

Cross-process restore của process ownership cũng chưa đúng

state.json persist dedicated_processes, nhưng _load_state() trong attached router không reconstruct _dedicated_processes.

Điều này rất quan trọng vì shell CLI invocation bình thường là:

process A:
  broker lease request Class I
  exits

process B:
  broker lease release <id>

Process B biết PID từ persisted Lease, nhưng _dedicated_processes runtime map của nó trống. release_lease() vì thế không có Popen object để terminate process/temp dir theo code path hiện tại.

Đây có thể tạo orphan Chrome.

Một resident broker process loại bỏ toàn bộ class bug này.

Recovery “seamless” nên được thiết kế như thế nào?

Browser state không thể magically resurrect. Cách đúng là recovery ở task semantic level:

daemon socket closes / PID exits
        ↓
instance S-17 = DEAD
        ↓
atomically fence all S-17 leases
        ↓
for each lease:
    checkpoint available?
       ├─ yes → RECOVERING
       │        allocate S-18 context
       │        rehydrate auth
       │        new fencing token
       │        replay semantic checkpoint
       └─ no  → LOST(retryable=true)

Một agent không checkpoint thì nên nhận lỗi deterministically thay vì tiếp tục tưởng lease còn hợp lệ.

Đối với auth identity, giữ identity reservation trong lúc RECOVERING để không có agent thứ hai lấy cùng account.

4. Profile Sanctity / Invariant 9

assert_not_protected_profile() có một điểm tốt: Path.resolve() giúp bắt cả exact live profile và các descendant, kể cả symlink resolution trong phần lớn case thông thường. 

auth_state_vault

Nhưng câu trả lời cho câu hỏi “có hoàn toàn ngăn accidental lock không?” là:

Chưa.

4.1 Guard không được áp dụng lên mọi writable path

AuthStateVault.__init__() chấp nhận arbitrary vault_dir, tạo directory và chmod 0700 ngay. 

auth_state_vault

Nếu misconfiguration:

OMNIBROWSER_VAULT_DIR=~/.chrome-ai-profile

thì Vault có thể tự chmod và ghi dữ liệu vào protected profile mà không gọi assert_not_protected_profile().

Tôi đã test cùng logic này trên một temporary stand-in protected path: constructor chấp nhận nó và chmod thành 0700.

state_dir cũng cần guard tương tự.

Invariant phải được enforce tại mọi file-write/process-spawn boundary, không chỉ ở temp user-data-dir.

4.2 Test “port 17082 blocked” không chứng minh profile sanctity

Báo cáo xem việc không đụng live profile/port 17082 là evidence. 

task-007-browser-concurrency-an…

Nhưng:

protected profile

và:

debugging port 17082

là hai resources khác nhau.

Một accidental process có thể:

--user-data-dir=~/.chrome-ai-profile
--remote-debugging-port=32147

và vẫn vi phạm Invariant 9.

Test đúng phải kiểm tra process command line / spawn guard trên --user-data-dir, không kiểm tra một port cụ thể.

4.3 Vault hiện không encrypted

Result report ghi:

Encrypted snapshot storage with chmod 600

task-007-browser-concurrency-an…

Nhưng implementation serialize cookies/localStorage/sessionStorage thành JSON plaintext rồi chmod 600. 

auth_state_vault

Đây là documentation mismatch.

0700 parent + 0600 file là baseline khá tốt trên single-user workstation, nhưng không phải encryption.

Nếu muốn tuyên bố encrypted:

snapshot JSON
    ↓ envelope encrypt
AES-GCM
    ↓
key from macOS Keychain

hoặc bỏ chữ “encrypted” khỏi report.

4.4 Vault “single-writer” chưa enforce single writer

File header gọi nó là Single-Writer/Multi-Reader, nhưng version allocation hiện là:

Python
Run
existing = glob(...)
next_ver = len(existing) + 1

sau đó temp filename dùng version + int(time.time()). 

auth_state_vault

Hai writers cùng second có thể chọn cùng:

v1
.tmp_v1_1789....json

Tôi đã tái hiện race này trực tiếp.

Ngoài ra latest.json được viết trực tiếp, không temp+rename. 

auth_state_vault

Cần một trong hai:

OS file lock per identity

hoặc tốt hơn:

SQLite transaction

và version row có unique constraint.

v*.json cũng đang sort lexicographically; khi latest.json mất, v10/v11 không có thứ tự numeric đúng so với v9.

4.5 Snapshot directory identity có collision

Identity được sanitize bằng cách xóa ký tự không nằm trong allow-list. 

auth_state_vault

Vì vậy hai raw identity khác nhau có thể map tới cùng directory.

Tốt hơn:

dir = base64url(SHA256(identity))

và raw identity nằm trong metadata encrypted.

4.6 Auth injection chưa xuất hiện trong attached broker code

Trong toàn bộ attached .py, AuthStateVault.get_snapshot() chỉ có call site trong test. Tôi không thấy SessionRouter lấy snapshot rồi inject cookies/localStorage vào Class A/S.

Vì vậy hiện tại, trong phạm vi source tôi được xem:

auth_identity

thực hiện identity locking, chưa thực hiện authentication state hydration.

Điều này phù hợp một phần với limitation của result rằng auth snapshot vẫn cần export/sync, nhưng còn thiếu cả runtime injection path. 

task-007-browser-concurrency-an…

5. Một vài lỗi bổ sung đáng sửa
TargetRegistry có stale-owner bug

register_target() overwrite _targets[target_id], nhưng không remove target đó khỏi _lease_targets của owner cũ. 

target_registry

Tôi đã test:

register T → Lease 1
register T → Lease 2

sau đó:

get_targets_for_lease(Lease 1)

vẫn trả record của Lease 2.

is_target_owned_by_lease() đúng, nhưng lease-scoped enumeration có thể leak visibility. 

target_registry

Nên reject target rebinding hoặc atomically remove old reverse mapping.

Class I/A dùng fake TargetID

Router tạo:

target-<time_ns>

cho initial target Class I/A thay vì lấy actual CDP TargetID.

Nếu action routing thực sự sử dụng registry ID để attach/navigate, ID này không tồn tại trong Chrome.

Test Class I hiện chỉ xác nhận process/temp-dir lifecycle; chưa action qua registered target.

Class I/A nên sau spawn gọi browser CDP:

Target.getTargets

hoặc chủ động:

Target.createTarget

và lưu actual targetId.

Admission fallback trên macOS đang fail-open

Fallback vm_stat trả:

Python
Run
return max(free_mb, 2048)

nên nếu máy chỉ còn 200 MB thực tế, broker vẫn báo tối thiểu 2 GB. 

admission_controller

Và nếu telemetry lỗi hoàn toàn thì trả 4096 MB.

Đối với admission control chống OOM, telemetry unknown nên degrade conservatively, không invent available RAM.

Native CDP WebSocket client phù hợp bootstrap, chưa đủ làm multiplexer

Client hiện đủ cho request ngắn như createBrowserContext, nhưng chưa phải RFC-complete:

nhận wss:// nhưng không tạo TLS socket;

chỉ kiểm HTTP 101, không verify Sec-WebSocket-Accept;

đọc FIN nhưng bỏ qua nó;

không support continuation frames;

không có thread-safe request multiplexing;

events bị drop.

RFC 6455 yêu cầu TLS cho wss, validation Sec-WebSocket-Accept, và client phải support fragmented WebSocket messages. 
RFC Editor

Đây chưa phải blocker cho các browser-level request nhỏ hiện tại, nhưng sẽ trở thành blocker ngay khi bạn xây event-stream/multiplexer.

Roadmap tôi đề xuất
v2.2.1 — Correctness Hardening, trước mọi feature mới

Ưu tiên theo thứ tự:

Resident Broker Service + Unix Domain Socket. CLI không trực tiếp own Chrome/process state nữa. Đây là thay đổi có ROI lớn nhất; nó xóa phần lớn race của state.json, Popen recovery, watchdog lifecycle và identity locking cùng lúc.

Lease state machine + exact fencing. RESERVED → PROVISIONING → ACTIVE → RELEASING → CLOSED, thêm LOST/RECOVERING/CLEANUP_PENDING; mutating action bắt buộc exact fence.

BrowserInstance pool có generation ID. Không còn _class_s_process singleton; mỗi daemon draining vẫn được track độc lập. New leases route sang generation mới.

Persistent CDP multiplexer/event loop. Target.setDiscoverTargets, targetCreated, targetDestroyed, targetCrashed; update TargetRegistry tự động. Đây là chỗ native CDP client nên được nâng cấp đúng RFC.

Context reaper + tree-RSS watchdog. Target.getBrowserContexts reconciliation, process-tree RSS, daemon age, context churn count và crash rate.

Vault transaction + profile guard toàn cục. Single privileged auth writer, numeric/UUID snapshot versions, atomic latest pointer, secure create mode, optional Keychain encryption.

v2.3 — Resilience & performance

Sau khi control plane đúng, tôi mới thêm warm Class-I pool.

Nhưng không nên “reuse” một Chrome đã chạy workload không tin cậy rồi sanitize và cho agent khác. Mô hình tốt hơn là:

warm blank dedicated process
        ↓
one lease
        ↓
destroy completely
        ↓
background replenishes replacement

Như vậy cold-start bị che đi nhưng process isolation vẫn sạch.

Sau đó mới làm semantic checkpoint recovery và automatic auth refresh.

Test suite cần bổ sung

8/8 hiện tại là foundation tốt, nhưng test suite v2.3 nên thêm adversarial cases sau:

Spawn 20 OS processes đồng thời xin leases; assert không lost lease và mọi fencing token unique.

Hai process đồng thời xin cùng exclusive identity; đúng một process phải thắng.

Hai shared identity holders rồi xin exclusive; exclusive phải bị block cho tới khi cả hai shared release.

Kill Class-S PID bằng SIGKILL khi 5 leases active; toàn bộ leases cùng generation phải chuyển LOST/RECOVERING.

Drain S-17 khi leases còn active, đồng thời request mới; S-18 phải chạy song song nhưng S-17 vẫn được track.

Request Class-I ở process A, release ở process B; Chrome PID và temp profile phải biến mất.

Fault injection sau từng bước createContext → createTarget → createLease → persist; assert không orphan contexts.

Loop 1,000 create/dispose contexts rồi compare Target.getBrowserContexts với active lease set và quan sát tree RSS.

Concurrent AuthStateVault.save_snapshot() trên cùng identity.

Test OMNIBROWSER_VAULT_DIR/BROKER_DIR trỏ qua symlink vào protected profile.

Class I/A action thực tế bằng registered target ID, không chỉ kiểm PID.

CDP fragmented WebSocket response/event test.

Chốt kiến trúc

Tôi không thay đổi Hybrid Bulkheaded Browser Pool. Class S/I/A vẫn là abstraction đúng.

Thứ tôi sẽ thay đổi là control plane bên dưới:

v2.2 hiện tại
────────────
CLI process
  ↕ state.json
CLI process
  ↕ state.json
CLI process
  ↕ state.json
      ↓
   Chrome(s)


v2.3 mục tiêu
─────────────
CLI ─┐
CLI ─┼── Unix Socket ──► Resident Browser Broker
CLI ─┘                       │
                             ├─ Lease/Identity Authority
                             ├─ CDP Event Multiplexer
                             ├─ BrowserInstance Pool
                             ├─ Watchdog/Reaper
                             ├─ Auth Vault
                             └─ SQLite checkpoint journal
                                      │
                       ┌──────────────┼──────────────┐
                     S-17           S-18          I/A Pool

Đây là thay đổi sẽ biến v2.2 từ một working broker implementation thành một actual concurrency broker có failure semantics rõ ràng.

Nếu đi tiếp, tôi có thể:

Viết Task 008 / v2.2.1 hardening specification đầy đủ, chia thành acceptance criteria và test matrix.

Soạn patch design cụ thể cho Resident Broker + Unix Domain Socket + BrowserInstance generations, giữ API CLI hiện tại gần như không đổi.

Viết bộ adversarial pytest/multiprocessing tests để tái hiện từng race/blocker ở trên trước khi implement fix.