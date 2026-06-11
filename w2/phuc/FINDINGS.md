# Findings

1. **Which similarity function did you choose for Layer 2, and why?**
I chose a weighted Jaccard similarity function over structural tuples (affected services and trace anomalous edges) and log template tokens. I considered dense embeddings but rejected them because the historical corpus is extremely small (~30 entries), making a high-dimensional embedding prone to false similarities on out-of-domain terms. Token overlap on templated log lines gives direct, unambiguous similarity signals for well-known errors without the risk of overfitting.

2. **How does outcome-weighted voting change the candidate ranking versus a pure-similarity ranking?**
Outcome-weighted voting applies a penalty (negative weight) to historically failed actions and halves the weight of partial success. For example, if a high-similarity incident resulted in a failure, a pure-similarity ranking would still surface that failed action as the top candidate. Outcome-weighting pushes it down and surfaces alternative actions that succeeded in slightly less similar incidents.

3. **For one eval incident, explain the EV calculation in full**
In E01, the system finds `increase_pool_size` and `rollback_service` as candidates.
Taking `increase_pool_size` as an example:
- P_success is derived from the normalized weighted score of supporting historical votes (e.g., ~0.83).
- The intrinsic benefit is set to 100.
- The `cost_min` from `actions.yaml` is 1.
- `blast_radius_services` is 1, yielding a failure penalty of 50.
- EV = (0.83 * 100) - 1 - ((1 - 0.83) * 50) = 83 - 1 - 8.5 = 73.5.
Because EV > 0 and blast radius is low, it safely auto-acts.

4. **When did your engine choose to escalate (page_oncall) instead of auto-act?**
The engine escalates on out-of-distribution (OOD) patterns, such as E07. For E07, the maximum similarity score to any historical incident falls below the `ood_threshold` of 0.15, triggering an immediate fallback to `page_oncall` with zero confidence. This aligns correctly with the eval ground truth which marks any auto-action on E07 as wrong.

5. **What is the most likely class of incident that breaks your engine?**
Incidents where the actual root cause service emits generic errors (e.g., "internal error"), but a downstream dependency emits highly specific, distinct error messages. Because the similarity function weights logs heavily (0.5), it might strongly match the downstream symptom's signature rather than the true root cause.
**Proposed Improvement**: Implement a topology-aware dampening factor. We could use `topology.json` to reduce the weight of log signatures originating from downstream (leaf) services when determining the primary action target, and heavily weight the top-most service in the anomalous trace edge.
