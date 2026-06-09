# FINDINGS
 
## RCA Analysis
 
The RCA pipeline analyzed 2 alert clusters using a combination of graph traversal (PageRank on dependency subgraph), temporal timestamp ranking, and incident-history retrieval via similarity scoring.
 
For the primary cluster (c-001-000), the predicted root cause service was **payment-svc**. It achieved the highest combined score (0.9911) because it occupied the deepest dependency position in the service graph — it is called by checkout-svc but does not call any other alerting service — while also being among the earliest services to appear in the alert timeline. The graph-based PageRank identified payment-svc as the most likely culprit, while checkout-svc (0.92) and cart-svc (0.7511) were ranked lower because they depend on payment-svc and appear to be downstream victims of the failure cascading upward. The incident retrieval component found INC-2025-11-08 as the closest historical match (similarity=0.80), which involved a connection pool exhaustion caused by a payment-svc deploy — leading the pipeline to classify the current incident as **connection_pool_exhaustion** with confidence **0.95**.
 
For the secondary cluster (c-002-000), the sole alerting service was **recommender-svc** (score 1.0), making it the unambiguous root cause candidate. The LLM classified this as **memory_leak** with confidence 0.80, referencing similar past incidents INC-2025-08-02, INC-2025-10-28, and INC-2026-03-07.
 
## Confidence Assessment
 
The top-1 confidence score for the largest cluster (c-001-000) was **0.95**. This is high enough to be a strong investigative signal, but I would set an auto-rollback threshold at **0.90** before executing remediation without SRE confirmation. The reasoning: a score of 0.90+ means both the graph topology and the temporal ordering strongly agree on the same service, and at least one similar historical incident exists with a known remediation. Below 0.90, the risk of acting on a victim service instead of the true culprit increases meaningfully — especially when graph edges are incomplete or multiple services fail simultaneously. Human validation should remain the default unless the confidence exceeds this threshold consistently across multiple incident types.
 
## Uncertain Case
 
The secondary cluster (c-002-000) had only one alerting service (recommender-svc), so graph traversal could not meaningfully differentiate culprit from victim — there was simply nothing to compare against. The confidence score of 0.80 is reasonable but less trustworthy in single-service clusters because the temporal and topology signals both collapse to a trivial answer. In production, a single-service cluster with no upstream dependencies in the alert window is a case where log and metric inspection should always precede automated action.
 
## Limitations of the Rule-Based / Retrieval Approach vs Real LLM
 
The pipeline uses Groq (free LLM tier) rather than a pure rule-based approach, but the core RCA logic — graph PageRank + timestamp scoring + similarity retrieval — would behave identically without the LLM. The LLM adds classification and natural-language reasoning on top of a ranking already produced by deterministic code.
 
Compared with a full LLM-first approach, the current implementation cannot reason about deployment events, log content, trace anomalies, or multi-hop causal chains that are not captured in the service graph. The similarity retrieval is also purely keyword/overlap-based — it cannot understand that two incidents with different fingerprints are semantically the same failure. Despite these limitations, the approach is fast, cost-effective, and deterministic in its ranking step, making it a reliable baseline for environments with a stable and trusted service dependency graph.