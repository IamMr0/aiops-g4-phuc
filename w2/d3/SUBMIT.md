# EOD Checkpoint Reflection

**1. What is the latency budget of your endpoint (p99)? Which phase takes the most time?**
The latency budget (p99) for the endpoint is **< 10 seconds**. The phase that takes the most time is undeniably **LLM Enrichment** (typically taking 2-8 seconds depending on context length and the model's token generation speed). Other phases like Correlation or Graph RCA run completely in-memory and only take a few dozen milliseconds.

**2. How does latency differ when processing 5 alerts vs 500 alerts? Is the cost linear or fixed?**
The processing cost is not strictly linear. The Graph Correlation process (union-find and shortest path) increases in complexity as the number of alerts and nodes grows, but since operations run in-memory, the difference between 5 and 500 alerts at L1/L2 is only a few dozen milliseconds.
However, the LLM API call acts as a **fixed cost block** for each cluster. Our endpoint currently only extracts the primary cluster (the largest cluster) to send to the LLM. Therefore, whether there are 5 or 500 alerts, we still only make 1 LLM call. Because of this, the total latency remains roughly the same.

**3. How does the system behave if the LLM provider goes down during execution? What is the fallback strategy?**
If the LLM provider (Groq) goes down or times out after 10 seconds, the code catches the exception in the `run_rca` function. It will log a warning and automatically fall back to the Graph-based RCA results (with the returned method set to `graph-only-llm-fallback`). The endpoint still returns a 200 OK along with the root cause derived from PageRank, ensuring the incident triage system is not interrupted.
Additionally, the on-call engineer can set the flag `AIOPS_USE_LLM=false` to completely skip LLM calls until the provider stabilizes.

**4. What is the difference between `/healthz` and `/readyz`? When should you use each?**
*   **/healthz (Liveness):** Only checks if the HTTP Server process (Uvicorn) is alive. Kubernetes uses this to decide whether to **restart the pod**.
*   **/readyz (Readiness):** Checks if the app has finished loading the necessary data (Service graph, History data). Kubernetes uses this to decide whether to **route traffic** to this pod. If the graph is not fully loaded, the pod will remain alive but won't receive requests.

**5. Does the endpoint handle 4 concurrent POST requests well? What is the first bottleneck?**
If running locally with uvicorn's default options (1 worker), the endpoint will struggle. Because the framework processes concurrently but our LLM call is currently `sync` (`client.chat.completions.create` blocking the thread), the 4 requests will wait for each other, and the 4th request might timeout from the client's side.
If running in production with `uvicorn --workers 4`, the 4 requests will be evenly distributed across 4 processes and handled in parallel. However, the first bottleneck will immediately appear at the **Rate Limit and Concurrency Limit of the LLM Provider**.
