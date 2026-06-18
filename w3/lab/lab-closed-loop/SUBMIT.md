# SUBMIT.md — Kết quả chạy 3 chaos scenarios

## Thông tin

- Họ tên: [Điền tên của bạn]
- Decision engine: Rule-based (`runbook_map` trong `config.yaml`)
- Python: 3.12, uv
- Docker Compose: v2

---

## Scenario 1 — Action thành công (latency inject trên payment-svc)

**Lệnh inject:**
```bash
bash data-pack/scripts/inject_fault.sh latency ronki-payment-svc 500ms
```

**Log orchestrator (trích):**
```json
[Paste log output tại đây sau khi chạy scenario 1]
```

**Kết quả:** [PASS/FAIL — mô tả kết quả]

---

## Scenario 2 — Action fail → rollback (checkout-svc killed, threshold thấp)

**Thiết lập:** Đặt tạm `verify_thresholds.latency_p99_max_ms: 1` trong `data-pack/data/baseline.json` để verify luôn fail, kiểm tra rollback logic.

**Lệnh inject:**
```bash
bash data-pack/scripts/inject_fault.sh kill ronki-checkout-svc
```

**Log orchestrator (trích):**
```json
[Paste log output tại đây sau khi chạy scenario 2]
```

**Kết quả:** [PASS/FAIL — mô tả kết quả]

---

## Scenario 3 — Circuit breaker (3 consecutive failures)

**Thiết lập:** Giữ nguyên threshold thấp từ Scenario 2. Inject kill 3 lần, mỗi lần để orchestrator xử lý xong trước khi inject tiếp.

**Log orchestrator (trích — chỉ key events):**
```json
[Paste log output tại đây sau khi chạy scenario 3]
```

**Kết quả:** [PASS/FAIL — mô tả kết quả]

---

## Điều học được

[Viết reflection sau khi hoàn thành 3 scenarios]
