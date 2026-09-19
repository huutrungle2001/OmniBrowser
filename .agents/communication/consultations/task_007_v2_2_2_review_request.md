Bạn là Adversarial Distributed Systems & Browser Engine Security Architect.

Cảm ơn bạn vì lượt review cực kỳ xuất sắc và chuẩn xác vừa qua đối với Milestone v2.2.1.
Chúng tôi đã phân tích toàn bộ các failure traces bạn đưa ra và tiến hành đợt Hardening toàn diện **Milestone v2.2.2**, giải quyết dứt điểm toàn bộ 3 lỗi CRITICAL và tất cả các lỗi MAJOR bạn đã chỉ ra.

Dưới đây là chi tiết các bản vá đã được áp dụng và kiểm chứng qua 62/62 test passes:

### 1. Giải quyết CRITICAL #1 (Resurrection Bug do `_load_state` merge)
- Trong `SessionRouter._load_state()`: Disk state được thiết lập là **Authoritative Source of Truth** tuyệt đối.
- Loại bỏ hoàn toàn cơ chế merge cũ (`if lid not in self.lease_manager._leases`).
- Tái cấu trúc mới toàn bộ in-memory maps (`_leases`, `_active_identities`, `_targets`, `_lease_targets`, `_class_s_daemons`) từ snapshot trên đĩa. Khi một process khác revoke lease trên đĩa, bất kỳ process nào chạy `status()` hay acquire lock đều reload authoritative state và không bao giờ "hồi sinh" (resurrect) lease cũ.
- Đã kiểm chứng qua test: `test_revoked_lease_not_resurrected`.

### 2. Giải quyết CRITICAL #2 (SIGKILL & Orphan Resources)
- Thiết lập thư mục runtime tin cậy: `self.runtime_root = self.state_dir / "runtime"` (chmod 0700, kiểm tra nghiêm ngặt `assert_not_protected_profile` trước khi tạo bất kỳ tempdir nào).
- Bổ sung `_reconcile_orphans()`:
  - Tự động quét dọn toàn bộ thư mục user-data-dir trong `runtime_root` không thuộc bất kỳ active lease hay daemon nào và có mtime > 60s.
  - Tự động quét `_dedicated_processes_data`, nếu lease tương ứng không còn active hoặc PID đã chết thì gửi `SIGTERM/SIGKILL` và dọn dẹp thư mục tạm.
  - Hàm này được gọi tự động trong `_load_state()`, `status()` và broker init.
- Đã kiểm chứng qua test: `test_orphan_reconciler`.

### 3. Giải quyết CRITICAL #3 (Cross-Identity Credential Collision trong `AuthStateVault`)
- Thay thế hàm sanitize cũ bằng SHA-256 hash: `ident_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]`, tạo thư mục `id_{ident_hash}`. Do đó `alice@example.com` và `aliceexample.com` có 2 thư mục hoàn toàn độc lập, triệt tiêu nguy cơ va chạm.
- Trong snapshot lưu `"canonical_identity": identity`.
- Khi đọc (`get_snapshot`), kiểm tra đối chiếu bắt buộc: `if data.get("identity") != identity and data.get("canonical_identity") != identity: return None` (fail closed).
- Đã kiểm chứng qua test: `test_auth_state_vault_no_identity_collision`.

### 4. Giải quyết các vấn đề MAJOR & MINOR:
- **Admission TOCTOU**: Việc kiểm tra capacity và slot được chuyển vào **bên trong `_state_lock`** kèm vòng lặp retry/backpressure cho đến khi timeout (`test_admission_capacity_inside_lock`).
- **Pre-reservation Validation**: Trong `LeaseManager.create_lease()`, nếu có `pre_reserved_lease_id`, hệ thống kiểm tra nghiêm ngặt: reservation phải còn tồn tại trong `_active_identities`, chưa hết TTL 60s, khớp identity và khớp exclusive/shared lock mode.
- **Deterministic CDP Target Creation**: Bỏ hoàn toàn endpoint `/json/list` và synthetic fallback. Dùng trực tiếp CDP `Target.createTarget` (`about:blank`) để nhận TargetID thực thể tức thì, triệt tiêu timing race.
- **Nested `_state_lock`**: Trong `close()`, bỏ outer `_state_lock()`, tiến hành `release_lease()` tuần tự (mỗi hàm tự acquire lock), tránh mở nhiều file descriptor lồng nhau trên cùng 1 lock file (portability trên Linux).
- **Daemon Drain & Generation Tracking**: Thêm `browser_instance_id` vào `Lease` dataclass. Drain completion kiểm tra trực tiếp theo `browser_instance_id`. Thêm `_refresh_daemon_health()` gọi `check_memory_and_drain()` định kỳ trong `status()` và `_ensure_class_s_daemon()`.
- **Vault Gap-Safe Versioning & Pointer Reconciliation**: Version tính bằng `max(numeric_versions, default=0) + 1` thay vì `len(existing) + 1` (`test_auth_state_vault_version_gap_no_overwrite`). Khi đọc, đối chiếu con trỏ `latest.json` với `max(v*.json)`.
- **Multiprocessing Stress Test**: Test `test_inter_process_state_lock_concurrency` đã được viết lại hoàn toàn bằng `multiprocessing.Process` với `multiprocessing.Barrier` qua 4 tiến trình OS độc lập, chứng minh `fcntl.flock` duy trì serializability 100% qua ranh giới tiến trình hệ điều hành.

---
### Đính kèm các tệp mã nguồn:
- `src/browser_core/contracts.py`
- `src/browser_core/broker/auth_state_vault.py`
- `src/browser_core/broker/lease_manager.py`
- `src/browser_core/broker/session_router.py`
- `tests/test_concurrency_broker.py`

### Yêu cầu đánh giá:
Xin mời bạn attack lại toàn bộ mã nguồn trên và cho biết:
1. Đã giải quyết triệt để 3 lỗi CRITICAL và các failure traces trước đây chưa?
2. Có còn bất kỳ góc khuất hoặc failure mode nào chưa được bao phủ không?
3. Nếu tất cả đã đạt chuẩn production-ready, xin cấp xác nhận **APPROVED FOR v2.2.2**!
