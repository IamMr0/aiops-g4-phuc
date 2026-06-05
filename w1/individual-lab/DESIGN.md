# Detection Approach — DESIGN.md

## Approach I used
Static Thresholds / Rule-based Detection

## Why I chose this approach
Given the data stream configuration for this lab, the baseline metrics and fault metrics have very distinct profiles with well-defined ranges (e.g., normal memory is ~800MB, CPU 20-45%, latency P99 35-65ms). 
Using hard static thresholds is extremely fast, simple, and computationally inexpensive, making it highly suitable for processing streaming data without complex overhead. It allows for immediate anomaly detection as soon as metrics cross the limit (e.g., detecting memory_leak when memory > 1GB, dependency_timeout when timeout rate > 5%).

## How it works
The pipeline continuously listens to HTTP POST requests containing `metrics`. Each incoming payload is evaluated against a set of `if/elif` conditions corresponding to the abnormal thresholds:
- If `upstream_timeout_rate` exceeds the normal timeout limit.
- If `http_requests_per_sec` (RPS) or `queue_depth` spikes unexpectedly (indicating a traffic_spike).
- If `memory_usage_bytes` or `jvm_gc_pause_ms_avg` grows larger than the normal configuration (indicating a memory_leak).
If any condition is met and this specific fault hasn't been alerted recently (state is tracked via `last_alerted_type` to prevent alert spam), the pipeline immediately writes a JSON log to `alerts.jsonl`.

## Parameters chosen
- **Dependency Timeout**: `upstream_timeout_rate > 5.0` (Baseline: 0 - 0.4%). Set to 5.0% to avoid false positives from minor fluctuations.
- **Traffic Spike**: `http_requests_per_sec > 250` or `queue_depth > 20` (Baseline RPS: 80-160, Baseline Queue: 2-10). These thresholds are safe enough to catch massive surges (which scale up to 8x according to the generator code).
- **Memory Leak**: `memory_usage_bytes > 1_000_000_000` (~1GB) or `jvm_gc_pause_ms_avg > 30` (Baseline is ~800MB and GC pause 8-18ms). A continuous memory leak will quickly breach the 1GB mark (the generator adds up to 1.1GB).

## Improvements with more time
1. Instead of static thresholds, we could implement a Simple Moving Average (SMA) or Z-Score for dynamic anomaly detection that adapts to the day/night cycles (since the `http_requests_per_sec` metric follows an hourly diurnal pattern).
2. Incorporate log analysis into the logic (currently, only metrics are parsed for the decision). For example, capturing logs like "OutOfMemoryWarning" or "Circuit breaker OPEN" to confirm anomalies with 100% certainty rather than relying solely on metric numbers.
