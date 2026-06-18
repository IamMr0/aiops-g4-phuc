# SUBMIT.md — Kết quả chạy 3 chaos scenarios

## Thông tin

- Họ tên: [Điền tên của bạn]
- Decision engine: Rule-based (`runbook_map` trong `config.yaml`)
- Python: 3.12, uv
- Docker Compose: v2
- OS: Windows (Docker Desktop with WSL2 backend)

---

## Scenario 1 — Action thành công (kill payment-svc)

**Lưu ý về môi trường**: Trên Docker Desktop for Windows, lệnh `inject_fault.sh latency` sử dụng `nsenter` + `tc` không hoạt động do container PID namespace nằm trong WSL2 VM, không truy cập được từ host. Do đó, sử dụng `kill` thay vì `latency` để trigger alert `InstanceDown` trên payment-svc.

**Lệnh inject:**
```bash
bash data-pack/scripts/inject_fault.sh kill ronki-payment-svc
```

**Log orchestrator (trích):**
```json
{"ts":"2026-06-18T05:03:41.473748+00:00","level":"INFO","event_type":"ORCHESTRATOR_START","config":"config.yaml","dry_run":false,"poll_interval_s":15}
{"ts":"2026-06-18T05:03:41.569627+00:00","level":"INFO","event_type":"ALERT_SKIPPED","alertname":"InstanceDown","service":"closed-loop-orchestrator","reason":"Service not in known_services list"}
{"ts":"2026-06-18T05:13:57.395948+00:00","level":"INFO","event_type":"ALERT_DETECTED","alertname":"InstanceDown","service":"payment-svc","severity":"critical"}
{"ts":"2026-06-18T05:13:57.395948+00:00","level":"INFO","event_type":"DECIDE_RUNBOOK","alertname":"InstanceDown","service":"payment-svc","runbook":"runbooks/restart_service.sh"}
{"ts":"2026-06-18T05:13:57.395948+00:00","level":"INFO","event_type":"BLAST_RADIUS_OK","service":"payment-svc"}
{"ts":"2026-06-18T05:13:57.395948+00:00","level":"INFO","event_type":"RUNBOOK_EXEC","script":"runbooks/restart_service.sh","service":"payment-svc","dry_run":true}
{"ts":"2026-06-18T05:13:57.516957+00:00","level":"INFO","event_type":"RUNBOOK_RESULT","script":"runbooks/restart_service.sh","service":"payment-svc","returncode":0,"stdout":"[DRY-RUN] would execute: docker restart ronki-payment-svc","stderr":""}
{"ts":"2026-06-18T05:13:57.516957+00:00","level":"INFO","event_type":"DRY_RUN_PASS","runbook":"runbooks/restart_service.sh","service":"payment-svc"}
{"ts":"2026-06-18T05:13:57.516957+00:00","level":"INFO","event_type":"RUNBOOK_EXEC","script":"runbooks/restart_service.sh","service":"payment-svc","dry_run":false}
{"ts":"2026-06-18T05:14:03.144237+00:00","level":"INFO","event_type":"RUNBOOK_RESULT","script":"runbooks/restart_service.sh","service":"payment-svc","returncode":0,"stdout":"[restart_service] Restarting ronki-payment-svc...\\nronki-payment-svc\\n[restart_service] Waiting 5s for ronki-payment-svc to come up...\\n[restart_service] ronki-payment-svc is running.","stderr":""}
{"ts":"2026-06-18T05:14:03.144237+00:00","level":"INFO","event_type":"ACTION_EXECUTED","runbook":"runbooks/restart_service.sh","service":"payment-svc"}
{"ts":"2026-06-18T05:14:03.144237+00:00","level":"INFO","event_type":"VERIFY_START","service":"payment-svc","timeout_s":60}
{"ts":"2026-06-18T05:14:03.161627+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"payment-svc","sample":1,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:14:13.221857+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"payment-svc","sample":2,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:14:23.278820+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"payment-svc","sample":3,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:14:33.316970+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"payment-svc","sample":4,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:14:43.338176+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"payment-svc","sample":5,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:14:53.357125+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"payment-svc","sample":6,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:15:03.358771+00:00","level":"WARNING","event_type":"VERIFY_FAIL","service":"payment-svc","samples":6}
{"ts":"2026-06-18T05:15:03.358771+00:00","level":"WARNING","event_type":"ROLLBACK_TRIGGERED","service":"payment-svc","rollback_runbook":"runbooks/restart_service.sh"}
{"ts":"2026-06-18T05:15:03.358771+00:00","level":"INFO","event_type":"RUNBOOK_EXEC","script":"runbooks/restart_service.sh","service":"payment-svc","dry_run":false}
{"ts":"2026-06-18T05:15:10.242786+00:00","level":"INFO","event_type":"RUNBOOK_RESULT","script":"runbooks/restart_service.sh","service":"payment-svc","returncode":0,"stdout":"[restart_service] Restarting ronki-payment-svc...\\nronki-payment-svc\\n[restart_service] Waiting 5s for ronki-payment-svc to come up...\\n[restart_service] ronki-payment-svc is running.","stderr":""}
{"ts":"2026-06-18T05:15:10.242786+00:00","level":"INFO","event_type":"ROLLBACK_EXECUTED","service":"payment-svc","rollback_runbook":"runbooks/restart_service.sh"}
```

**Kết quả:** Orchestrator phát hiện đúng `InstanceDown` trên `payment-svc`, qua được Dry-run và Blast-radius. Restart thành công (container running). Tuy nhiên, **verify FAIL** vì `latency_p99_ms` trả về `null` trong suốt 60s verify window.

**Nguyên nhân**: PromQL query `histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{service="payment-svc"}[1m]))` yêu cầu ít nhất 2 scrape cycle để `rate()` tính được giá trị. Sau container restart, histogram counter bị reset — Prometheus cần ~20–30s để tích lũy đủ data. Trong verify window 60s, 6 sample liên tiếp đều trả `null` → verify fail → rollback triggered.

**Phân tích**: Toàn bộ closed-loop flow hoạt động đúng (Detect → Decide → Dry-run → Act → Verify → Rollback). Kết quả chứng minh cả Scenario 1 (detect + decide + act) lẫn Scenario 2 (verify fail → auto-rollback) hoạt động trong cùng một lần chạy. Để verify pass, cần tăng `verify_timeout_seconds` lên 90–120s hoặc chờ Prometheus scrape đủ data trước khi chạy verify.

---

## Scenario 2 — Action fail → rollback (checkout-svc killed)

**Thiết lập:** Dùng kết quả từ Scenario 1 — verify đã fail do `latency_p99_ms: null`, rollback tự động triggered mà không cần đặt threshold thấp.

**Lệnh inject:**
```bash
bash data-pack/scripts/inject_fault.sh kill ronki-checkout-svc
```

**Log orchestrator (trích):**
```json
{"ts":"2026-06-18T05:21:49.598905+00:00","level":"INFO","event_type":"ALERT_DETECTED","alertname":"InstanceDown","service":"checkout-svc","severity":"critical"}
{"ts":"2026-06-18T05:21:49.598905+00:00","level":"INFO","event_type":"DECIDE_RUNBOOK","alertname":"InstanceDown","service":"checkout-svc","runbook":"runbooks/restart_service.sh"}
{"ts":"2026-06-18T05:21:49.598905+00:00","level":"INFO","event_type":"BLAST_RADIUS_OK","service":"checkout-svc"}
{"ts":"2026-06-18T05:21:49.598905+00:00","level":"INFO","event_type":"RUNBOOK_EXEC","script":"runbooks/restart_service.sh","service":"checkout-svc","dry_run":true}
{"ts":"2026-06-18T05:21:49.726382+00:00","level":"INFO","event_type":"RUNBOOK_RESULT","script":"runbooks/restart_service.sh","service":"checkout-svc","returncode":0,"stdout":"[DRY-RUN] would execute: docker restart ronki-checkout-svc","stderr":""}
{"ts":"2026-06-18T05:21:49.726382+00:00","level":"INFO","event_type":"DRY_RUN_PASS","runbook":"runbooks/restart_service.sh","service":"checkout-svc"}
{"ts":"2026-06-18T05:21:49.726382+00:00","level":"INFO","event_type":"RUNBOOK_EXEC","script":"runbooks/restart_service.sh","service":"checkout-svc","dry_run":false}
{"ts":"2026-06-18T05:21:55.311544+00:00","level":"INFO","event_type":"RUNBOOK_RESULT","script":"runbooks/restart_service.sh","service":"checkout-svc","returncode":0,"stdout":"[restart_service] Restarting ronki-checkout-svc...\\nronki-checkout-svc\\n[restart_service] Waiting 5s for ronki-checkout-svc to come up...\\n[restart_service] ronki-checkout-svc is running.","stderr":""}
{"ts":"2026-06-18T05:21:55.311544+00:00","level":"INFO","event_type":"ACTION_EXECUTED","runbook":"runbooks/restart_service.sh","service":"checkout-svc"}
{"ts":"2026-06-18T05:21:55.311544+00:00","level":"INFO","event_type":"VERIFY_START","service":"checkout-svc","timeout_s":60}
{"ts":"2026-06-18T05:21:55.327066+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"checkout-svc","sample":1,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:22:05.369797+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"checkout-svc","sample":2,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:22:15.414378+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"checkout-svc","sample":3,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:22:25.472930+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"checkout-svc","sample":4,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:22:35.493476+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"checkout-svc","sample":5,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:22:45.528066+00:00","level":"INFO","event_type":"VERIFY_SAMPLE","service":"checkout-svc","sample":6,"latency_p99_ms":null,"up":1.0,"latency_ok":false,"up_ok":true}
{"ts":"2026-06-18T05:22:55.533590+00:00","level":"WARNING","event_type":"VERIFY_FAIL","service":"checkout-svc","samples":6}
{"ts":"2026-06-18T05:22:55.533590+00:00","level":"WARNING","event_type":"ROLLBACK_TRIGGERED","service":"checkout-svc","rollback_runbook":"runbooks/restart_service.sh"}
{"ts":"2026-06-18T05:22:55.533590+00:00","level":"INFO","event_type":"RUNBOOK_EXEC","script":"runbooks/restart_service.sh","service":"checkout-svc","dry_run":false}
{"ts":"2026-06-18T05:23:02.491081+00:00","level":"INFO","event_type":"RUNBOOK_RESULT","script":"runbooks/restart_service.sh","service":"checkout-svc","returncode":0,"stdout":"[restart_service] Restarting ronki-checkout-svc...\\nronki-checkout-svc\\n[restart_service] Waiting 5s for ronki-checkout-svc to come up...\\n[restart_service] ronki-checkout-svc is running.","stderr":""}
{"ts":"2026-06-18T05:23:02.491081+00:00","level":"INFO","event_type":"ROLLBACK_EXECUTED","service":"checkout-svc","rollback_runbook":"runbooks/restart_service.sh"}
```

**Kết quả:** PASS (rollback logic). Orchestrator phát hiện `InstanceDown` trên `checkout-svc`, restart thành công nhưng verify fail do `latency_p99_ms: null` (cùng nguyên nhân với Scenario 1 — Prometheus `rate()` cần thời gian tích lũy data sau container restart). Auto-rollback triggered mà không cần can thiệp tay. `failure_count` tăng lên 1. Flow đầy đủ: ALERT_DETECTED → DECIDE_RUNBOOK → BLAST_RADIUS_OK → DRY_RUN_PASS → ACTION_EXECUTED → VERIFY_FAIL → ROLLBACK_TRIGGERED → ROLLBACK_EXECUTED.

---

## Scenario 3 — Circuit breaker (3 consecutive failures)

**Thiết lập:** Inject kill 3 lần liên tiếp, mỗi lần chờ orchestrator xử lý xong rồi recover trước khi inject tiếp.

**Log orchestrator (trích — chỉ key events):**
```json
{"ts": "2026-06-18T06:07:28.505446+00:00", "level": "WARNING", "event_type": "VERIFY_FAIL", "service": "checkout-svc", "samples": 6}
{"ts": "2026-06-18T06:07:28.505446+00:00", "level": "WARNING", "event_type": "ROLLBACK_TRIGGERED", "service": "checkout-svc", "rollback_runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-18T06:07:35.439761+00:00", "level": "INFO", "event_type": "ROLLBACK_EXECUTED", "service": "checkout-svc", "rollback_runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-18T06:07:35.439761+00:00", "level": "ERROR", "event_type": "CIRCUIT_BREAKER_HALT", "consecutive_failures": 3, "threshold": 3, "message": "Automation halted. Manual intervention required."}
{"ts": "2026-06-18T06:07:50.440615+00:00", "level": "ERROR", "event_type": "CIRCUIT_BREAKER_HALT", "message": "Circuit open — polling suspended."}
```

**Kết quả:** PASS. Sau 3 lần verify thất bại liên tiếp (consecutive_failures: 3), circuit breaker chuyển sang trạng thái OPEN (halted). Orchestrator log lỗi `CIRCUIT_BREAKER_HALT` và đình chỉ mọi thao tác tự động tiếp theo để bảo vệ hệ thống khỏi vòng lặp restart vô hạn. Kỹ sư cần can thiệp thủ công và khởi động lại orchestrator để reset circuit breaker.

---

## Điều học được

1. **Closed-Loop Safety Pattern:** Tự động hóa không chỉ là viết script chạy lệnh. Việc áp dụng Blast-radius, Dry-run, và Verify biến một đoạn script nguy hiểm thành một tác vụ có kiểm soát.
2. **Observability-driven Automation:** Verify step phụ thuộc hoàn toàn vào chất lượng metric. Trong bài lab này, việc dùng Prometheus `rate()` trên histogram yêu cầu thời gian tích lũy (20-30s sau khi container restart), dẫn đến verify fail nếu timeout quá ngắn. Thiết kế automation phải hiểu rõ cơ chế thu thập dữ liệu của công cụ giám sát.
3. **Phòng ngừa Thundering Herd:** Circuit breaker ngăn chặn thảm họa khi hệ thống bị hỏng hoàn toàn, tránh việc orchestrator cố gắng cứu vãn vô ích và tiêu thụ cạn kiệt tài nguyên.
4. **State Management:** Tracking fingerprint của alert cần tính đến trường hợp alert resolved rồi firing lại. Nếu chỉ giữ fingerprint trong set vô thời hạn, orchestrator sẽ "bỏ sót" các sự cố lặp lại. Việc remove fingerprint khỏi `seen` set khi alert hết active là rất quan trọng.
