# W3-D2 Submission

## 3 Things I Learned About My AIOps Pipeline

1. **Topology-aware RCA is necessary but not sufficient for infrastructure-layer faults.** The pipeline correctly traversed the service dependency graph for every application-layer fault (payment-db → payment-svc, api-gateway → downstream cascade, checkout-svc → payment-svc retry storm). But when the fault was in a layer below the service graph — the DNS resolver — RCA stalled at the first application node it could see (api-gateway) and could not traverse further. The dependency graph needs to model infrastructure primitives (DNS, NTP, disk volumes) as first-class nodes, not just application services.

2. **The monitoring dependency loop is the most dangerous silent failure mode.** Experiment 7 produced exactly zero signal — no alert, no RCA, no evidence. The log-collector's disk fill caused log ingestion to stall, which starved the detector of its own input. From the pipeline's perspective, nothing was wrong; from the user's perspective, the entire observability layer was dead. This class of failure cannot be caught by the same pipeline that depends on the failing component. An independent out-of-band health check for the observability stack itself is not optional — it is the prerequisite for trusting every other alert.

3. **A flat confidence score makes RCA output unreliable for operators.** Every experiment — correct or wrong — returned a confidence of exactly 0.84. This means an engineer cannot use the confidence score to triage which RCA results to act on versus investigate further. The retry-storm result (exp 10) and the DNS misattribution result (exp 9) look identical in the output. Grounded confidence — computed from evidence depth, topology distance, and cross-signal corroboration — is required before RCA can be trusted in an on-call workflow.

---

## 1 Fault I Expected the Pipeline to Catch but It Missed

**Experiment:** 7 — `log_collector_disk_fill`

**Why I expected detection:** The hypothesis called for the pipeline to catch log ingestion lag as a meta-monitoring signal. The disk-fill fault is slow and predictable — disk usage climbing toward 95% should be visible as a resource metric independent of log content. A pipeline that monitors the full stack should have a sensor on the log-collector's own disk usage, just as it has sensors on cpu_used_ratio and memory_used_ratio for other services.

**Why the pipeline missed (hypothesis):** The AIOps pipeline's detector is fed by metrics that flow through the log-collector. When the log-collector's disk fills, it stops ingesting logs. The detector then receives no new data — not a fault signal, but silence. The detector interprets silence as healthy, because its threshold logic is "did the metric breach a bound?" and a missing metric never breaches a bound. This is the monitoring dependency loop: the component that is failing is the same component the monitoring system depends on. The fix is an independent out-of-band probe for the observability stack itself — a process that reads disk usage directly from the OS and writes to a separate alerting channel that bypasses the log pipeline.

---

## 1 Trade-off in Pipeline Design I Want to Rethink

**The trade-off between alert sensitivity and topology depth for infrastructure-layer services.**

Right now, the pipeline's RCA graph ends at the application service boundary. This kept the design simple and made the RCA engine fast and accurate for the 8 experiments where the fault was inside the service mesh. But experiments 7 and 9 revealed that two real-world fault classes — observability infrastructure failure and DNS latency — live below that boundary and are invisible to the current design.

Adding DNS, NTP, log pipelines, and disk volumes as nodes in the dependency graph would improve coverage, but it introduces a new problem: the graph becomes much larger and noisier. Infrastructure nodes (especially DNS) are shared across every service, so a single DNS fault would create edges to every node simultaneously — making the correlator's job harder and increasing the risk of the false-positive lumping failure mode (§7.2). The design I want to rethink is whether infrastructure dependencies should be modeled as graph edges (same as service dependencies) or as a separate infrastructure anomaly layer that gates RCA rather than participates in it. A two-layer design — infrastructure health check first, service-graph RCA second — would preserve the RCA graph's clarity while still catching the blind spots that experiments 7 and 9 exposed.

---

## Scoreboard Summary

- detected: **9/10**
- rca_correct: **8/9**
- mttd_p50: **2s**
- false_alarms: **0**
- verdict: **PASS** (≥ 7/10 detected ✓, ≥ 5 RCA correct ✓, ≤ 1 false alarm ✓)