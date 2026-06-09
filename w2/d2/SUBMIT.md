# SUBMIT
 
## 1. Confidence of the Largest Cluster
 
The top-1 root cause candidate for the largest cluster (c-001-000) was **payment-svc**, with a combined graph + temporal score of **0.9911** and a final LLM-reported confidence of **0.95**.
 
If I had to set a threshold for fully automated rollback without SRE confirmation, I would pick **0.90**. Here is the reasoning based on what I observed from the output:
 
- At 0.95, both the PageRank signal and the timestamp signal agreed strongly on the same service, and a historical incident with an identical root cause class existed (INC-2025-11-08, similarity=0.80). This level of multi-signal agreement gives enough confidence to act automatically.
- At the 0.80 level (c-002-000 / recommender-svc), the cluster had only one service — the score is trivially high but the evidence is thinner because there was nothing to compare against. I would not auto-rollback at this level.
- 0.90 as a threshold filters out the thin-evidence cases while allowing action on the well-supported ones. It also matches the intuition that a wrong automated rollback on a production payment service is far more costly than a delayed response — so the bar should be high.
## 2. Classifier Variant Chosen
 
I selected **Variant B — Free LLM (Groq)**.
 
The implementation uses graph traversal (PageRank on the reversed dependency subgraph), timestamp-based temporal scoring, and incident-history retrieval to generate top-K root cause candidates. These candidates are then passed to the Groq API (free tier, `llama-3.3-70b-versatile`) which classifies the root cause class, suggests remediation actions, and provides a reasoning summary. The output method field confirms this: `"method": "graph+groq"`.
 
**How it ran in practice:** Groq's free tier was responsive and handled both cluster calls without rate-limit issues. The JSON structured output was valid on the first attempt for both clusters, meaning the hallucination guard (`validate_llm_output`) did not need to trigger a fallback in either case.
 
**Trade-offs vs Variant A (Rule-Based Only):**
- Variant A would have used `fake_llm_rca()` — pulling the root cause class and remediation directly from the most similar historical incident. It is fully deterministic, zero-cost, and requires no network call, but it cannot generate novel classifications or reasoning. If the historical dataset had no similar incident, it would fall back to `class="other"` with no actionable suggestion.
- Variant B adds LLM reasoning on top of the same graph ranking, producing richer classification and more contextual action suggestions. The cost is near-zero on Groq's free tier, and the added latency (~1–2 seconds per call) is acceptable during an incident investigation.
**Trade-offs vs Variant C (Paid LLM):**
- A paid model (GPT-4o, Claude) would likely produce more accurate reasoning and handle edge cases better, but for this dataset the Groq output was already coherent and correctly identified both root causes. The marginal accuracy gain does not justify the cost for a system running 100+ incidents per day.
## 3. Industry Landscape Comparison
 
The pipeline I built is most similar to **Dynatrace Davis**.
 
Both systems treat the service dependency graph as the primary source of truth for RCA. The core logic — traverse the topology, find the service that is depended upon by others but does not itself depend on any other alerting service, and rank by graph position — is exactly the "Smartscape-first" philosophy that Davis uses. The LLM layer I added for classification and action suggestion is an augmentation on top of that graph signal, not a replacement for it.
 
**Is this the right choice for GeekShop?**
 
Yes, for the current scenario. GeekShop is described as an e-commerce system with a relatively stable service map and high alert volume. In this context:
 
- The service dependency graph is reliable enough to trust as the primary signal. Graph-based RCA responds in under 1 second for the ranking step, which matters during active incidents.
- The LLM call adds classification and reasoning without changing the underlying ranking, keeping the system interpretable.
- Causely's approach (learning causal graphs from time-series data) would be a better fit if the service map were frequently changing or unavailable, but it requires long metric history to learn from — a constraint that makes it less suitable for fast incident response.
If GeekShop's architecture became significantly more dynamic (frequent service additions, changing dependency patterns), switching to a data-driven causal approach would be worth evaluating. For now, the graph-first design is appropriate.