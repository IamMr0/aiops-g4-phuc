# EOD Checkpoint Reflection

**1. Actual Latency and Phase Scaling**
Based on testing with a dataset of 20 alerts:
*   **Latency Measurement:** The measured p50 latency is incredibly fast at ~**0.6 ms**, and the p99 latency (the first cold-start request) is ~**4.9 ms** (measured via the `X-Response-Time-Ms` header). 
*   **Dominant Phase:** In this specific scenario, because the Graph RCA calculated a confidence of 1.0 (which is >= the 0.9 threshold), the pipeline *dynamically skipped* the LLM Enrichment phase entirely. Therefore, the dominant phases were simply Pydantic Validation and Graph PageRank, which both ran entirely in-memory and completed in under 1 millisecond. If the LLM were not skipped, it would dominate 99% of the latency (typically taking 2-8 seconds).
*   **Scaling with 10x Input:** 
    *   **Linear/Non-Linear Scale:** The `Validation`, `Correlation` (shortest-path graph traversal), and `Graph RCA` phases will scale non-linearly (graph operations can approach $O(V^3)$ complexity) as the alert volume and topology size increases.
    *   **Fixed Cost:** The **LLM Enrichment** phase (when triggered) acts as a fixed cost. We only send the primary (largest) cluster to the LLM, making 1 API call whether the input has 5 or 500 alerts.

**2. Concurrency Handling & LLM Downtime**
*   **4 Concurrent Requests:** Testing with PowerShell `Start-Job` showed all 4 concurrent requests succeeded immediately. Since the LLM was dynamically skipped due to high graph confidence, there was no IO blocking. 
    However, if the LLM *were* required, the endpoint handles it well because FastAPI automatically assigns synchronous `def` endpoints to an external ThreadPool. The 4 requests would process in parallel across 4 separate threads, meaning they wouldn't sequentially block each other.
*   **First Bottleneck observed:** The first bottleneck (when the LLM is not skipped) is the **LLM Provider's Rate Limit (HTTP 429)** and concurrency limits, rather than local CPU or memory limits on the single Uvicorn worker.
*   **Fallback Path:** If the LLM provider goes down or times out during execution, the `run_rca` function catches the exception and gracefully degrades. It falls back to the deterministic Graph-based RCA results. The endpoint successfully returns a `200 OK` with the method marked as `graph-only-llm-fallback`, ensuring incident triage is never interrupted.

**3. `/healthz` vs `/readyz`**
*   **What they check:**
    *   `/healthz` (Liveness): Checks if the HTTP Server process (Uvicorn) is alive.
    *   `/readyz` (Readiness): Checks if the application has fully loaded its necessary dependencies into memory (Service Graph and Historical Incidents data).
*   **Why separate them?** Kubernetes uses them differently. If a liveness probe fails, Kubernetes will completely **restart the pod**. If a readiness probe fails, Kubernetes will simply **stop routing traffic** to it. We separate them because an app might still be loading data (not ready) but the process hasn't crashed (is alive). We don't want to kill a pod just because it's taking time to load the graph.
*   **If the LLM API goes down, does `/readyz` fail?** No, it **still passes**. The LLM is treated as an optional external dependency. Because our pipeline is fault-tolerant and has a graph-only fallback mechanism, the endpoint can still serve functional responses without the LLM. Failing `/readyz` would take the entire service offline, which violates our graceful degradation design.
