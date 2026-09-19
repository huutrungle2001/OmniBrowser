Bạn là Adversarial Distributed Systems & Browser Engine Security Architect.
Trong vòng review trước (Milestone v2.2), bạn đã chỉ ra 8 lỗ hổng kiến trúc nghiêm trọng trong Concurrency Broker của OmniBrowser.

Chúng tôi đã hoàn thành đợt Hardening toàn diện (Milestone v2.2.1) để vá triệt để toàn bộ 8 vấn đề trên, đồng thời viết bộ test đối kháng 15 bài kiểm thử cục bộ (`tests/test_concurrency_broker.py`), kết quả: toàn bộ 57/57 unit & integration test của OmniBrowser đều PASS (exit code 0).

Dưới đây là các files mã nguồn đã được cập nhật đính kèm:
1. `src/browser_core/broker/lease_manager.py`:
   - Hỗ trợ `IdentityLockRecord` phân biệt `exclusive_lease_id` và tập hợp `shared_lease_ids`. Nhiều shared lease có thể cùng giữ 1 identity; exclusive lease bị chặn nếu có bất kỳ shared hoặc exclusive nào đang hoạt động.
   - Thêm cơ chế Pre-reservation (`reserve_identity` & `rollback_identity`) với TTL 60s (`reserved_at`) cho pending reservation để bảo vệ khoảng thời gian provisioning tài nguyên.
   - Kiểm tra Fencing Token chính xác tuyệt đối: Bác bỏ cả stale token (< current) và fabricated/future token (> current) bằng `fencing_token != lease.fencing_token`.
2. `src/browser_core/broker/target_registry.py`:
   - Dọn dẹp triệt để reverse mapping trong `_lease_targets` khi re-register target ID sang lease mới.
3. `src/browser_core/broker/auth_state_vault.py`:
   - Thêm inter-process file lock (`fcntl.flock(lock_fd, fcntl.LOCK_EX)`) trên `.lock` khi ghi snapshot.
   - Đảm bảo version được sắp xếp theo thứ tự số học thực (`v1..v12`), tránh lỗi lexicographical sort (`v10` trước `v2`).
   - Cập nhật con trỏ `latest.json` bằng kỹ thuật atomic replace file tạm với permissions 0600.
   - Chặn tuyệt đối `~/.chrome-ai-profile` ngay tại constructor (Invariant 9).
4. `src/browser_core/broker/session_router.py`:
   - Bổ sung context manager `_state_lock` dùng `fcntl.flock` trên `state.lock` để đảm bảo serializability xuyên suốt các tiến trình CLI độc lập khi đọc/ghi state file `session_router_state.json`.
   - Chuyển daemon singleton thành pool `_class_s_daemons` với generation IDs (`class-s-<hex>`) để theo dõi các daemon đang drain cho đến khi toàn bộ active leases trên daemon đó về 0.
   - Áp dụng cơ chế Pre-reservation trước khi khởi tạo tài nguyên và rollback tự động nếu thất bại.
   - Truy vấn Target ID thực từ Chrome endpoint `/json/list` thay vì tạo synthetic ID cho Class I và Class A.
   - Lưu trữ PID mapping trong state file để hỗ trợ `release_lease` từ tiến trình CLI khác.
   - Chặn `~/.chrome-ai-profile` ngay tại constructor.
5. `src/browser_core/broker/lifecycle_watchdog.py`:
   - Hàm `_get_pid_rss_mb` tính toán tổng RSS của toàn bộ cây tiến trình (parent + child renderers + GPU process + network service).
6. `src/browser_core/broker/admission_controller.py`:
   - Loại bỏ sàn nhân tạo `max(free_mb, 2048)` trên macOS fallback, phản ánh chính xác áp lực bộ nhớ.
7. `tests/test_concurrency_broker.py`:
   - Bổ sung các bài test đối kháng: `test_exact_fencing_token_validation`, `test_shared_and_exclusive_identity_locks`, `test_reserve_before_provision_rollback`, `test_target_registry_re_registration`, `test_profile_sanctity_constructor_guards`, `test_auth_state_vault_atomic_and_numeric_sort`, và `test_inter_process_state_lock_concurrency` (chạy multi-thread/process stress test với `fcntl.flock`).

---
### Yêu cầu đối kháng & Review nghiêm ngặt:
Hãy tấn công (attack) mã nguồn đã cập nhật từ góc độ Distributed Systems, OS Concurrency và Browser Architecture:
1. **File Locking & Multi-Process Concurrency (`fcntl.flock`)**:
   - Có kịch bản nào gây race condition, deadlock, lock inversion, hoặc stale lock file nếu một tiến trình CLI bị kill đột ngột (`SIGKILL`)?
   - Lưu ý rằng `fcntl.flock` được kernel tự động giải phóng khi file descriptor đóng hoặc tiến trình kết thúc. Thiết kế này có đủ an toàn cho CLI model mà không cần resident RPC daemon hay không? Nếu bạn cho rằng chưa đủ, hãy đưa ra kịch bản thất bại cụ thể (concrete failure trace).
2. **Identity Locking & Pending Reservation TTL**:
   - Cơ chế TTL 60s cho pre-reservation có rủi ro gì không nếu provisioning kéo dài hơn 60s do tải nặng? Có cần heartbeat hoặc renewal cho pending state không?
3. **Class S Draining Pool Lifecycle**:
   - Khi một Class S daemon đang drain và một daemon mới được spawn, có kịch bản race condition nào giữa việc shutdown daemon cũ và route request mới không?
4. **Target Registry & Browser Context Isolation**:
   - Việc truy vấn `/json/list` sau khi spawn Class I/A có rủi ro timing (race condition giữa lúc Chrome mở page mặc định và lúc router query `/json/list`) không?
5. **Đánh giá tổng thể & Phán quyết**:
   - Hãy chỉ ra các lỗi còn tồn tại (nếu có), phân loại theo mức độ: CRITICAL, MAJOR, MINOR.
   - Nếu mã nguồn đã đạt chuẩn production cho Milestone v2.2.1, hãy xác nhận "APPROVED FOR v2.2.1".
