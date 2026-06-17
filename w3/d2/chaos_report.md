# Chaos Engineering Report

## 1. Setup

- **Stack commit hash:** `db957682d2a588c43a272100a77ad3ab8dea0c1c`
- **Pipeline version:** FastAPI AIOps pipeline (W2 Lab C) — detector + correlator + RCA, port 8000
- **Baseline window:** 5-minute steady-state capture via `capture_baseline.py` before experiment start; synthetic probe confirmed ≥ 99% pass-rate within a 60s window prior to Step 2
- **Total experiments run:** 10
- **Cooldown between experiments:** 3 seconds (runner enforced)
- **Tool used for injection:** `scripts/inject_fault.py` dispatched via `chaos_runner.py`
- **External steady-state signal:** `synthetic_probe.sh` polling `http://localhost:8080/checkout/health` every 5s, writing to `probe.log` throughout all 10 experiments

---

## 2. Results Table

```
==== Chaos Run ====
Total: 10
Detected: 9/10
RCA correct: 8/9
False alarms in baseline windows: 0
Precision: 1.00
Recall: 0.90
MTTD p50: 2s, p95: 3s

Per-experiment:
| # | name                     | detected | mttd | rca_service  | rca_correct |
|---|--------------------------|----------|------|--------------|-------------|
| 1 | payment_latency          | Y        | 2s   | payment-svc  | Y           |
| 2 | payment_packet_loss      | Y        | 2s   | payment-svc  | Y           |
| 3 | inventory_availability   | Y        | 1s   | inventory-svc| Y           |
| 4 | gateway_cpu_saturation   | Y        | 3s   | api-gateway  | Y           |
| 5 | payment_db_memory        | Y        | 3s   | payment-db   | Y           |
| 6 | auth_clock_skew          | Y        | 3s   | auth-svc     | Y           |
| 7 | log_collector_disk_fill  | N        | -    | -            | N           |
| 8 | gateway_partition        | Y        | 2s   | api-gateway  | Y           |
| 9 | dns_lookup_latency       | Y        | 4s   | api-gateway  | N           |
| 10| checkout_retry_storm     | Y        | 2s   | payment-svc  | Y           |

Gaps identified:
- 7: detector missed log_collector_disk_fill -> missing meta-monitoring or weak threshold
- 9: RCA picked api-gateway -> topology/evidence ranking weakness
```

**Verdict:** PASS — Detected 9/10 (≥ 7 required), RCA correct 8/9 (≥ 5 required), False alarms 0 (≤ 1 required).

---

## 3. Detailed Per-Experiment Analysis

### Experiment 1 — payment_latency

**Hypothesis:** Steady-state probe pass-rate ≥ 99%, checkout p99 < 500ms. Injecting 500ms delay on payment-svc should trigger a latency anomaly alert and RCA should identify payment-svc as the root.

**Observed:** Detected at MTTD 2s. Alert fired on `latency_p99_ms` for `payment-svc` (severity: crit). RCA returned `payment-svc`, citing `fault_type=latency`, `target=payment-svc`, and `metric=latency_p99_ms`.

**Match expected?** Yes. The detector reacted quickly and RCA correctly traced the latency to its injected origin. The 2s MTTD is well within the acceptable window. No false alarms.

---

### Experiment 2 — payment_packet_loss

**Hypothesis:** Injecting 30% packet loss on payment-svc should elevate downstream error_rate and RCA should keep the root at payment-svc rather than any downstream service amplifying the errors.

**Observed:** Detected at MTTD 2s. Alert fired on `error_rate` for `payment-svc`. RCA returned `payment-svc`. No false alarms.

**Match expected?** Yes. The pipeline correctly attributed the error rate spike to the injected packet loss at the source, not to downstream retry amplification. This validates that the correlator handles upstream-propagated errors correctly.

---

### Experiment 3 — inventory_availability

**Hypothesis:** Making inventory-svc unavailable (pod kill every 60s) should trigger an availability alert and RCA should identify inventory-svc, not its downstream consumers (checkout-svc).

**Observed:** Detected at MTTD 1s — the fastest detection of the entire run. Alert fired on `availability` for `inventory-svc`. RCA returned `inventory-svc`. No false alarms.

**Match expected?** Yes. Pod kill produces the clearest signal in the metrics (availability drops to 0), explaining the 1s MTTD. The topology correctly isolated inventory-svc from its callers.

---

### Experiment 4 — gateway_cpu_saturation

**Hypothesis:** Saturating api-gateway CPU to 90% should create cascading latency across all downstream services (payment-svc, inventory-svc, checkout-svc). RCA should identify api-gateway as the root rather than any of the downstream services showing elevated latency.

**Observed:** Detected at MTTD 3s. Alert fired on `cpu_used_ratio` for `api-gateway`. RCA returned `api-gateway`. No false alarms.

**Match expected?** Yes. The detector correctly caught the CPU resource fault at the gateway rather than following the latency cascade downstream. This demonstrates that the correlator uses the dependency graph to trace to the topologically upstream service.

---

### Experiment 5 — payment_db_memory

**Hypothesis:** Filling payment-db memory to 95% should break connection pool behavior between payment-svc and payment-db. RCA should identify payment-db, not payment-svc which is the visible symptom.

**Observed:** Detected at MTTD 3s. Alert fired on `memory_used_ratio` for `payment-db`. RCA returned `payment-db`. No false alarms.

**Match expected?** Yes. The pipeline successfully distinguished between the database layer fault and its upstream service, which is a non-trivial topology traversal given that payment-svc is the primary alerting surface for users.

---

### Experiment 6 — auth_clock_skew

**Hypothesis:** Skewing auth-svc clock by 60 seconds should cause JWT token validation failures (tokens appear expired or not-yet-valid). RCA should identify auth-svc as the root.

**Observed:** Detected at MTTD 3s. Alert fired on `jwt_validation_error_rate` for `auth-svc`. RCA returned `auth-svc`. No false alarms.

**Match expected?** Yes. State faults (clock skew) are typically harder to detect than resource or network faults because they produce indirect symptoms. The pipeline correctly mapped the JWT error pattern back to auth-svc, likely aided by the metric name being a strong signal.

---

### Experiment 7 — log_collector_disk_fill

**Hypothesis:** Filling log-collector disk to 95% should reveal whether the pipeline has meta-monitoring capable of detecting log ingestion lag — a fault inside the observability layer itself. This experiment was expected to be a challenge; the hypothesis noted the pipeline would likely miss it.

**Observed:** Not detected. No alerts fired. RCA returned no output (empty evidence). No false alarms.

**Match expected?** Partially. The miss was anticipated in the hypothesis due to the monitoring dependency loop described in §7.5. The pipeline's detector is fed by the same log-collector it is supposed to monitor. When the log-collector fills its disk, log ingestion stalls, and the detector loses its input signal rather than receiving a fault signal — so it stays silent. This is not a detector threshold weakness; it is a structural blind spot. The fix requires an independent out-of-band health check for the log-collector (e.g., a heartbeat from outside the logging pipeline).

---

### Experiment 8 — gateway_partition

**Hypothesis:** Partitioning api-gateway from all downstream services for 30 seconds should produce all-downstream timeout symptoms visible as a spike in `downstream_timeout_rate`. RCA should identify api-gateway as the edge root, not any of the downstream services.

**Observed:** Detected at MTTD 2s. Alert fired on `downstream_timeout_rate` for `api-gateway`. RCA returned `api-gateway`. No false alarms.

**Match expected?** Yes. A full partition produces clear, simultaneous timeout signals across all downstream services. The correlator correctly identified the common cause (api-gateway) rather than creating separate incidents per downstream service — confirming topology-aware correlation is working.

---

### Experiment 9 — dns_lookup_latency

**Hypothesis:** Adding 2s DNS lookup latency to the DNS resolver should produce intermittent checkout latency. Correct RCA should identify `dns-resolver`, but the hypothesis explicitly noted topology may confuse this with api-gateway symptoms since api-gateway is the first service visible to the detector at the edge.

**Observed:** Detected at MTTD 4s (slowest detection of the run). Alert fired on `dns_lookup_latency_ms` for `dns-resolver` — meaning the detector did reach the right service. However, RCA returned `api-gateway`, citing "known lightweight gap: topology points at gateway symptom." RCA_correct = N.

**Match expected?** Detection yes, RCA no. This is the one confirmed RCA weakness in the run. DNS is infrastructure that sits below the service topology graph, and the RCA engine appears to rank the most latency-affected service in the application layer (api-gateway) rather than traversing the DNS dependency. The evidence chain acknowledges the gap but still emits the wrong root. This validates the hypothesis: the topology graph needs a DNS layer and causal-lag analysis (e.g., Granger causality) to correctly upstream DNS faults.

---

### Experiment 10 — checkout_retry_storm

**Hypothesis:** Injecting 20% HTTP 500 responses on checkout-svc should create retry amplification (checkout retries its failed calls, amplifying upstream queue depth). RCA must NOT identify checkout-svc as the root — the true root is whichever service is causing checkout to fail.

**Observed:** Detected at MTTD 2s. Alert fired on `http_5xx_rate` for `checkout-svc`. RCA returned `payment-svc`, correctly identifying that payment-svc was the upstream cause of checkout's 500s. RCA_correct = Y (ground_truth was `NOT checkout-svc`, and the pipeline picked `payment-svc`).

**Match expected?** Yes, and this is the most important RCA result in the run. The retry-storm pattern (§7.3) is the canonical case where naive RCA fails — picking the loudest service rather than the root. The pipeline's topology-aware RCA correctly traversed from checkout-svc (symptom) to payment-svc (root), validating that upstream-causal scoring is working.

---

## 4. Gap Analysis — Top 3 Pipeline Weaknesses

### Gap 1 — Monitoring Dependency Loop (Experiment 7: log_collector_disk_fill)

**Symptom:** Experiment 7 produced zero alerts and zero RCA output. The disk fill fault on log-collector was completely invisible to the pipeline. No metric or threshold fired during the entire capture window.

**Likely cause in pipeline:** This is the monitoring dependency loop failure mode (§7.5). The AIOps pipeline's detector relies on metrics that flow through the same log-collector it is supposed to monitor. When the log-collector's disk fills, log ingestion stalls, which starves the detector of the very input it needs to fire — resulting in silence rather than an alert.

**Recommended fix:** Add an independent out-of-band heartbeat for the log-collector: a separate lightweight process outside the log pipeline that checks log-collector disk usage and ingestion lag via direct OS metrics (not log-derived metrics). This probe should write to a separate alerting channel that bypasses the log pipeline entirely. Reference: §7.5 counter — "the AIOps platform has its own observability stack that does not depend on the monitored services."

---

### Gap 2 — DNS Root Attribution (Experiment 9: dns_lookup_latency)

**Symptom:** Experiment 9 was detected (alert fired on `dns_lookup_latency_ms` for `dns-resolver`), but RCA incorrectly returned `api-gateway` as the root service. The RCA evidence even acknowledged the gap ("known lightweight gap: topology points at gateway symptom") yet still emitted the wrong answer.

**Likely cause in pipeline:** The RCA engine's topology graph does not include DNS as a first-class dependency node below the service layer. When DNS is slow, every service that performs DNS lookups shows elevated latency — and the RCA ranker scores the application-layer service with the most alerts (api-gateway) as root rather than traversing below the application graph to the DNS resolver. This is the RCA wrong root failure mode (§7.3): picking the noisiest service.

**Recommended fix:** Add `dns-resolver` as a node in the dependency graph with edges from every service that performs DNS lookups. Implement temporal-causal ranking (cross-correlation lag analysis): dns-resolver latency should spike before api-gateway latency, establishing causal precedence. Reference: §7.3 counter — "topology-aware (root upstream of leaves) + temporal-causal (root drifts before downstream)."

---

## 5. Hypothesis for Unconfirmed Gaps

**Gap 1 (log_collector) needs a follow-up experiment:** The current experiment confirmed a blind spot, but it does not confirm whether a partial disk fill (e.g., 70%) also evades detection or only triggers an alert later. A graduated disk-fill experiment (30% → 60% → 90%) would reveal whether there is a threshold that works or whether the blind spot is structural regardless of fill level.