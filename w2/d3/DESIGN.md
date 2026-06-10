# AIOps Incident Pipeline - Design Document

## Pipeline Architecture
The endpoint `POST /incident` implements a multi-stage pipeline designed to quickly correlate and diagnose incoming alert batches:
1. **Validation (FastAPI & Pydantic):** Incoming requests are immediately validated against the `IncidentRequest` schema. Invalid payloads are rejected with a `422 Unprocessable Entity` before entering the pipeline.
2. **Correlation (L1):** Valid alerts are grouped temporally (`gap_sec=120`) and topologically (`max_hop=2`) using the loaded service graph.
3. **Graph RCA (L2):** For the primary cluster (the one with the most alerts), we run PageRank on the reversed subgraph of alerting services to identify the upstream root cause deterministically.
4. **LLM Enrichment (L3):** We construct a detailed prompt containing the alerts, L2 candidates, and historical incidents. We call the Groq LLM API to provide human-readable reasoning, classify the failure, and suggest remediation actions.
5. **Serialization:** The result is transformed into the `IncidentResponse` schema and returned to the client.

## Latency Budget Breakdown
The target p99 latency for the endpoint is **< 10 seconds**.
Here is the approximate breakdown for a typical request:
*   **Pydantic Validation:** < 10 ms
*   **Graph Correlation & Shortest Path:** ~10-50 ms (in-memory)
*   **Graph RCA (PageRank):** ~50-100 ms
*   **LLM Enrichment:** ~2,000-8,000 ms (This phase dominates the latency budget due to network IO and LLM token generation)
*   **Response Formatting:** < 10 ms
*   **Total:** ~2.1 - 8.2 seconds (safely within the 10s budget).

## Production Concern: Fault Tolerance (LLM Outages)
**Concern:** The pipeline relies on an external LLM API which is prone to latency spikes, rate limits, or complete outages. If the LLM provider hangs, it could exhaust all uvicorn worker threads.
**Mitigation:** 
1. **Timeouts:** The LLM client is configured with a strict `timeout=10.0` seconds to prevent hanging worker threads. 
2. **Feature Flags:** We implemented an environment variable `AIOPS_USE_LLM`. If the LLM goes down, we can set `AIOPS_USE_LLM=false` and restart the service. The pipeline will gracefully degrade to return only the deterministic Graph RCA (L2) results, bypassing the LLM entirely while keeping the incident triage functioning.
3. **Graceful Fallback:** If the LLM call throws an exception, the code catches it and gracefully falls back to the graph-only RCA, returning the result with the method marked as `graph-only-llm-fallback`.

## Trade-off: Why FastAPI?
For this pipeline, **FastAPI** was chosen over Flask and BentoML for several reasons:
*   **Built-in Validation:** Native integration with Pydantic ensures we don't have to write manual `if not alert.get("service")` checks. It auto-generates 422 errors for malformed requests.
*   **Asynchronous Support:** Since the LLM call is IO-bound and takes several seconds, FastAPI's asynchronous core allows it to scale better under concurrent loads compared to Flask's synchronous blocking model.
*   **Auto-Documentation:** The automatic Swagger UI (`/docs`) makes it significantly easier to test the complex nested JSON payloads required for the incident batch without needing external Postman collections.
