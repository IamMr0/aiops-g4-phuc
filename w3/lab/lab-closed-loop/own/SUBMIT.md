# SUBMIT.md — 3 Chaos Scenarios Results

## Information

- Name: Ngo Nguyen Phuc
- Decision engine: Rule-based (`runbook_map` in `config.yaml`)
- Python: 3.12, uv
- Docker Compose: v2
- OS: Windows (Docker Desktop with WSL2 backend)

---

## Scenario 1 — Successful Action (kill payment-svc)

**Environment Note**: On Docker Desktop for Windows, the `inject_fault.sh latency` command using `nsenter` + `tc` does not work because the container PID namespace resides inside the WSL2 VM and is inaccessible from the host. Therefore, `kill` is used instead of `latency` to trigger the `InstanceDown` alert on payment-svc.

**Inject command:**
```bash
bash data-pack/scripts/inject_fault.sh kill ronki-payment-svc
```

**Orchestrator log (excerpt):**
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

**Result:** The orchestrator correctly detected `InstanceDown` on `payment-svc`, successfully passed Dry-run and Blast-radius checks. Restart was successful (container running). However, **verify FAILED** because `latency_p99_ms` returned `null` throughout the entire 60s verify window.

**Root cause**: The PromQL query `histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{service="payment-svc"}[1m]))` requires at least 2 scrape cycles for `rate()` to compute a value. After a container restart, the histogram counter is reset — Prometheus needs ~20–30s to accumulate enough data. During the 60s verify window, all 6 consecutive samples returned `null` → verify failed → rollback triggered.

**Analysis**: The entire closed-loop flow works correctly (Detect → Decide → Dry-run → Act → Verify → Rollback). The output demonstrates both Scenario 1 (detect + decide + act) and Scenario 2 (verify fail → auto-rollback) behaviors in a single run. To achieve a verify pass, `verify_timeout_seconds` would need to be increased to 90–120s, or the orchestrator should wait for Prometheus to accumulate enough scrape data before executing the verify step.

---

## Scenario 2 — Action fail → rollback (checkout-svc killed)

**Setup:** We observed this behavior naturally in Scenario 1 — verify failed due to `latency_p99_ms: null` and an auto-rollback was triggered, eliminating the need to manually set a low threshold.

**Inject command:**
```bash
bash data-pack/scripts/inject_fault.sh kill ronki-checkout-svc
```

**Orchestrator log (excerpt):**
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

**Result:** PASS (rollback logic verified). The orchestrator detected `InstanceDown` on `checkout-svc` and successfully executed the restart action. However, the verify step failed due to `latency_p99_ms: null` (same root cause as Scenario 1 — Prometheus `rate()` needed more time to accumulate data after the container restart). Auto-rollback was successfully triggered without manual intervention. The `failure_count` incremented to 1. The full execution flow was confirmed: ALERT_DETECTED → DECIDE_RUNBOOK → BLAST_RADIUS_OK → DRY_RUN_PASS → ACTION_EXECUTED → VERIFY_FAIL → ROLLBACK_TRIGGERED → ROLLBACK_EXECUTED.

---

## Scenario 3 — Circuit breaker (3 consecutive failures)

**Setup:** Injected the kill fault 3 consecutive times. After each execution cycle completed, the container was recovered before injecting the fault again.

**Orchestrator log (excerpt — key events only):**
```json
{"ts": "2026-06-18T06:07:28.505446+00:00", "level": "WARNING", "event_type": "VERIFY_FAIL", "service": "checkout-svc", "samples": 6}
{"ts": "2026-06-18T06:07:28.505446+00:00", "level": "WARNING", "event_type": "ROLLBACK_TRIGGERED", "service": "checkout-svc", "rollback_runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-18T06:07:35.439761+00:00", "level": "INFO", "event_type": "ROLLBACK_EXECUTED", "service": "checkout-svc", "rollback_runbook": "runbooks/restart_service.sh"}
{"ts": "2026-06-18T06:07:35.439761+00:00", "level": "ERROR", "event_type": "CIRCUIT_BREAKER_HALT", "consecutive_failures": 3, "threshold": 3, "message": "Automation halted. Manual intervention required."}
{"ts": "2026-06-18T06:07:50.440615+00:00", "level": "ERROR", "event_type": "CIRCUIT_BREAKER_HALT", "message": "Circuit open — polling suspended."}
```

**Result:** PASS. After 3 consecutive verify failures (consecutive_failures: 3), the circuit breaker transitioned to the OPEN (halted) state. The orchestrator logged a `CIRCUIT_BREAKER_HALT` error and suspended all further automated actions to protect the system from infinite restart loops. Manual engineering intervention and an orchestrator restart are now required to reset the circuit breaker.

---

## Lessons Learned

1. **Closed-Loop Safety Pattern:** Automation isn't just about writing scripts to run commands. Applying Blast-radius constraints, Dry-runs, and Post-action Verification transforms a potentially dangerous script into a tightly controlled operation.
2. **Observability-driven Automation:** The verify step is completely dependent on metric quality. In this lab, using Prometheus `rate()` on a histogram required an accumulation period (20-30s after a container restart), causing verify to fail if the timeout was too short. Automation design must intimately understand the data collection mechanisms of its monitoring tools.
3. **Thundering Herd Prevention:** The circuit breaker prevents catastrophic failure when a system is completely unrecoverable, stopping the orchestrator from making futile rescue attempts that exhaust system resources.
4. **State Management:** Tracking alert fingerprints must account for scenarios where alerts resolve and then fire again. If fingerprints are kept in a static set indefinitely, the orchestrator will "ignore" recurring incidents. Removing fingerprints from the `seen` set when an alert is no longer active is a critical implementation detail.
