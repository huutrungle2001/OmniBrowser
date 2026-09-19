## Phán quyết

**APPROVED — Milestone v2.2.3a đạt 5/5 Acceptance Gates đã đặt ra cho Task-007.**

Tôi chấp thuận milestone này ở mức **production-ready cho controlled rollout**. Không có phát hiện nào buộc phải reopen Gate 1–5. Tuy nhiên, review source thực tế cho thấy một số điểm hardening nên được ghi thành follow-up trước khi coi broker là hoàn toàn “battle-hardened” cho multi-process production dài hạn.

Tôi cũng đã kiểm tra cú pháp trực tiếp ba file đính kèm bằng `py_compile`: **không có lỗi syntax**. Tôi chưa thể độc lập tái chạy con số `29/29` và `71/71` từ các file rời vì môi trường attachment không chứa package/repository `browser_core`; do đó các con số pass đó được xem là **test evidence do implementer cung cấp**, còn source/test logic bên dưới đã được tôi review trực tiếp.

### Acceptance matrix

| Gate                                                   | Kết luận               | Đánh giá                                                            |
| ------------------------------------------------------ | ---------------------- | ------------------------------------------------------------------- |
| **1. Lock-safe state/reconciliation**                  | **PASS**               | Strong                                                              |
| **2. Local `close()` + explicit global shutdown**      | **PASS**               | Strong, 1 hardening note                                            |
| **3. Verified process signalling**                     | **PASS**               | Strong                                                              |
| **4. Dead daemon → lease invalidation before removal** | **PASS**               | Strong                                                              |
| **5. OS process-table orphan fallback**                | **PASS**               | Implementation proven; test coverage nên tăng                       |
| Directory fsync                                        | **PASS**               | Strong                                                              |
| “Full” transactional rollback                          | **PARTIAL BONUS PASS** | Physical rollback tốt, bookkeeping chưa hoàn toàn transaction-clean |

---

## Gate 1 — Lock-safe reconciliation: **PASS**

Thiết kế hiện tại đã giải quyết đúng race nguy hiểm nhất của v2.2.3.

Constructor chỉ reconstruct state qua `_load_state_from_disk()`; destructive reconciliation không chạy ở constructor. Sau đó `_state_lock()` lấy thread `RLock`, mở `state.lock`, lấy `flock(LOCK_EX)`, **reload authoritative disk state → reconcile → mutation body → save** trong cùng critical section. 

Quan trọng hơn, `_load_state_from_disk()` thực sự chỉ reconstruct lease/identity/target/daemon/dedicated metadata. Dead daemon không bị loại bỏ tại load-time; daemon records được giữ lại để reconciler xử lý sau khi lock đã được giữ. 

Tôi cũng kiểm tra toàn bộ source: production code chỉ có một call-site của `_reconcile_orphans()`, nằm bên trong `_state_lock()`. Việc một số unit test gọi private method trực tiếp không thay đổi invariant production.

**Gate 1 được đóng.**

---

## Gate 2 — `close()` không phá foreign leases: **PASS**

`self._local_leases` là ownership boundary hợp lý. `close()` chỉ iterate những lease ID được chính router instance này acquisition, gọi `release_lease()` trên chúng, sau đó chỉ recycle Class-S daemon khi daemon không còn active lease. `shutdown_broker()` được tách thành API global teardown riêng. 

Test cross-router đặc biệt có giá trị: R1 tạo lease, R2 gọi `close()`, rồi R1 reload/status và lease vẫn active cả trên disk lẫn lease manager. Đây đúng là failure mode mà Gate 2 nhằm loại bỏ. 

**Gate 2 được đóng.**

Có một hardening issue ở `shutdown_broker()`: `active_ids` hiện được snapshot **trước khi** vào `_state_lock()`.  Trong một race cực đoan, router có stale in-memory state có thể miss lease vừa được process khác tạo, rồi phần cuối shutdown vẫn recycle daemon. Đây không làm hỏng yêu cầu cốt lõi của Gate 2 về `close()`, nhưng global shutdown nên được làm atomic trong một broker-wide transaction.

---

## Gate 3 — Process identity verified signalling: **PASS**

Implementation của `_safe_kill_browser()` đúng theo security model đã yêu cầu.

Đối với foreign PID, trước SIGTERM code xác minh process là Chrome/Chromium và khớp `expected_user_data_dir`; sau grace period, trước SIGKILL code **xác minh lại identity lần thứ hai**, xử lý chính xác PID-reuse window.  

Static inspection toàn file cho thấy chỉ còn ba `os.kill`:

* `os.kill(pid, 0)` trong liveness probe — **không phải signalling termination**.
* SIGTERM bên trong `_safe_kill_browser`.
* SIGKILL bên trong `_safe_kill_browser`.

Không còn destructive `os.kill(...SIG*)` bypass helper trong router production paths.

Các paths recycle, orphan cleanup, provisioning failure và dedicated release đều route qua `_safe_kill_browser`. Ví dụ persisted dedicated cleanup cũng dùng helper với expected user-data-dir. 

**Gate 3 được đóng.**

---

## Gate 4 — Dead daemon invalidates leases trước khi record biến mất: **PASS**

Thứ tự hiện tại đúng:

`daemon_dead` được xác định → tìm toàn bộ active lease trên `browser_instance_id` → `is_active=False` → rollback identity → clear targets → **sau đó mới** pop daemon và unregister watchdog. 

Đây chính xác là ordering cần có để tránh ghost leases.

Quan trọng hơn, cross-process test không chỉ gọi private reconciler. Nó kill daemon của R1, tạo **independent R2**, rồi gọi public `status()`. `status()` đi qua `_state_lock()`, do đó reload + reconcile + save xảy ra qua production path; R2 quan sát 0 active lease và identity lock đã được giải phóng. 

Điểm này làm bằng chứng Gate 4 mạnh hơn nhiều so với unit test in-process.

**Gate 4 được đóng.**

Một cleanup hardening nhỏ nhưng đáng làm: nếu `is_cdp_alive()` false trong khi PID Chrome vẫn sống, branch `daemon_dead` hiện remove record + `rmtree()` nhưng không gọi `_safe_kill_browser()` trong chính branch đó.  Lease correctness vẫn đúng, nhưng có thể tồn tại một Chrome orphan cho đến reconciliation sau.

---

## Gate 5 — OS process-table fallback: **PASS**

Fallback mà tôi yêu cầu thực sự tồn tại và được nối vào reconciliation path.

`_scan_running_orphan_chrome_pids()` gọi `ps -eo pid,command=`, tìm Chrome/Chromium có `--user-data-dir=...`, so user-data-dir với runtime root và active directories, sau đó trả về orphan candidates. 

Reconciler chạy scan này độc lập với `chrome.pid`, rồi mọi candidate đều được đưa qua `_safe_kill_browser()`. Vì vậy crash xảy ra **sau `Popen()` nhưng trước khi `chrome.pid` được ghi** không còn hoàn toàn invisible với broker. 

Do đó về architecture, **Popen → pidfile SIGKILL window đã được đóng**.

Tuy nhiên, trong test file đính kèm tôi không thấy một integration test trực tiếp mô phỏng “Chrome tồn tại nhưng hoàn toàn không có `chrome.pid`”. Test PID-reuse hiện tại kiểm tra một unrelated `sleep` process được ghi vào `chrome.pid` và chứng minh nó không bị kill. 

Vì thế Gate 5 **pass về implementation**, nhưng test evidence của riêng Gate 5 yếu hơn Gate 2/4.

---

## Durability bonus — directory fsync: **PASS**

Persistence sequence là:

write temp → flush → file `fsync()` → atomic `replace()` → mở parent directory → directory `fsync()`.

Đó là ordering đúng để tăng crash durability của rename metadata. 

Phần này tôi chấp thuận.

---

## Transaction rollback bonus — chưa nên gọi là “full” 100%

Đây là điểm duy nhất tôi sẽ chỉnh lại wording của milestone.

Khi request provisioning/save thất bại, code rollback identity, remove lease, clear targets, dispose Class-S context và kill spawned dedicated process. Đây là rollback vật lý khá tốt. 

Nhưng ngay trước `_state_lock()` exit/save, successful provisioning đã add lease vào `_local_leases`; với Class I/A còn add `_dedicated_processes` và `_dedicated_processes_data`.  Nếu `_save_state()` phát exception, outer rollback hiện **không** `discard(temp_lease_id)` khỏi `_local_leases` và không pop hai dedicated maps ngay tại rollback site.

Process thực tế được kill và tempdir được xoá, nên đây không phải leaked Chrome. Nhưng in-memory bookkeeping chưa trở lại bit-for-bit pre-transaction state.

Test `test_save_state_failure_full_resource_rollback` hiện chỉ dùng **Class S**, và chỉ assert identity unlocked, active lease count = 0, target registry = 0. Nó chưa kiểm tra `_local_leases`, Class I/A maps, process death hoặc tempdir cleanup. 

Vì đây là **bonus** chứ không phải một trong 5 production gates, tôi không dùng nó để reject v2.2.3a.

---

# Final production decision

**Milestone v2.2.3a: APPROVED.**

Theo tiêu chí acceptance đã thống nhất trước đó:

**5/5 mandatory gates are satisfied.**

Kiến trúc đã chuyển từ một broker có các race-condition nguy hiểm sang một control plane có các đặc tính quan trọng: authoritative reload under inter-process locking, destructive reconciliation serialized với mutations, local lifecycle ownership, PID-reuse-safe process termination, cross-process lease invalidation và orphan recovery ngoài pidfile.

Tôi sẽ ghi release disposition như sau:

> **APPROVED FOR PRODUCTION / CONTROLLED ROLLOUT — v2.2.3a**
> All five mandatory Browser Concurrency Broker acceptance gates are satisfied. No gate-reopening defects identified. Remaining findings are hardening items rather than milestone blockers.

Ba follow-up tôi khuyến nghị trước khi gắn nhãn “fully hardened / GA” là:

* **P1:** làm `shutdown_broker()` thành một authoritative, lock-contained global transaction và thêm race test giữa shutdown và foreign lease acquisition.
* **P1:** harden orphan scan bằng true path containment (`Path.is_relative_to()`/`commonpath`) thay vì `startswith()`, đồng thời thêm test thật cho orphan Chrome **không có `chrome.pid`**.
* **P2:** hoàn thiện rollback bằng cách xoá `_local_leases`, `_dedicated_processes`, `_dedicated_processes_data` khi commit/save thất bại, rồi bổ sung failure-injection test cho cả Class I và Class A.
