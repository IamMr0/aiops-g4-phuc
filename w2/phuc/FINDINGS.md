# Findings

1. **Which similarity function did you choose for Layer 2, and why?**

I chose a **composite similarity** combining three sub-scores with adaptive weighting:

- **TF-IDF weighted log containment** (`log_containment` in `retrieval.py`): builds an IDF dictionary across all historical log signatures and the query, then measures what fraction of each historical entry's weighted tokens appear in the query. This replaces naive Jaccard over raw tokens because TF-IDF downweights common words (e.g., "error", "timeout") and upweights discriminative terms (e.g., "ConnectionPool", "OOMKilled").
- **Trace edge similarity** (`trace_similarity`): compares anomalous trace edges by service-pair overlap (containment) plus a quality bonus based on how closely the p99 deviation ratios match between query and historical entry.
- **Jaccard over affected services**: a lightweight structural check on which services are involved.

The weights adapt dynamically: when both query and history have trace data, weights are (services=0.1, logs=0.3, traces=0.6); when trace data is thin or absent, weights shift to (0.1, 0.8, 0.1) to lean on log evidence.

**Alternative considered:** I considered using pure Jaccard over tokenized log templates. I rejected it because Jaccard treats all tokens equally — "ConnectionPool" and "error" contribute the same weight. On E01, TF-IDF containment scored the correct match (INC-2025-11-08) at 0.933, while pure Jaccard would have diluted the score by giving equal weight to generic error tokens shared across many historical entries.

2. **How does outcome-weighted voting change the candidate ranking versus a pure-similarity ranking?**

In outcome-weighted voting, each historical action's vote is `similarity × outcome_weight`, where `outcome_weight` is +1.0 for success/partial and −1.0 for failed outcomes. A pure-similarity ranking would use `similarity × 1.0` regardless of outcome.

**Concrete example — E05:** The top three neighbors are INC-2025-07-04 (sim=0.824), INC-2025-09-05 (sim=0.789), and INC-2026-05-10 (sim=0.789). INC-2025-09-05 had outcome `success` and action `rollback_service`, contributing +0.789 to rollback's vote score. INC-2026-05-10 had outcome `partial`, also contributing +0.789. In a pure-similarity ranking, if a top neighbor had outcome `failed`, its action would still accumulate a high positive vote. With outcome weighting, that action would receive a *negative* contribution (e.g., −0.789), pushing it down the ranking and allowing a less-similar but historically successful action to surface instead. The final selected action for E05 was `rollback_service` with a consensus score of 1.578 — the sum of two positive votes from successful/partial incidents.

3. **For one eval incident, explain the EV calculation in full**

**E01** — the engine finds `increase_pool_size` as a candidate, supported by INC-2025-11-08 (similarity=0.933, outcome=success).

Step-by-step from `decision.py`:
- **max_sim** = 0.933 (best supporting incident similarity)
- **success_ratio** = (success_count + 0.3 × partial_count) / total_incidents = (1 + 0) / 1 = 1.0
- **confidence** = max_sim × success_ratio = 0.933 × 1.0 = **0.933**
- **p_fail** = 1 − 0.933 = 0.067
- **adjusted_score** = accumulated vote score = 0.933
- **benefit_per_unit** = 60 (constant in code)
- **cost** = `cost_min` from `actions.yaml` for `increase_pool_size` = 1
- **blast** = `blast_radius_services` = 1
- **penalty** = blast × 30 = 30

**EV = adjusted_score × benefit_per_unit − cost − (p_fail × penalty)**
**EV = 0.933 × 60 − 1 − (0.067 × 30) = 55.98 − 1 − 2.01 = 52.97 ≈ 52.99**

The blast-radius gate requires confidence ≥ 0.35 for blast ≥ 1; confidence 0.933 clears this easily. `increase_pool_size` wins over `rollback_service` because `rollback_service` has a higher `cost_min` (10 vs. 1), which reduces its EV, and the vote scores are comparable.

4. **When did your engine choose to escalate (page_oncall) instead of auto-act?**

The engine selected `page_oncall` on **E02, E04, E06, E07, and E08**. The reasons differ by incident:

- **E04** (confidence=0.292) and **E08** (confidence=0.292): The top historical matches had low similarity scores (0.292). All candidate actions with blast_radius ≥ 1 were blocked by the blast-radius gate (requires confidence ≥ 0.35), leaving `page_oncall` as the only remaining option. This is correct — both E04 and E08 accept `page_oncall`.

- **E02** and **E07**: The best-matching historical incidents (INC-2025-08-17 for E02, INC-2025-10-15 for E07) themselves had `page_oncall` as their recorded action. The engine selected it because it was the highest-voted candidate, not because OOD was detected. Both are correct against ground truth.

- **E06** (confidence=0.633): The best match INC-2026-02-22 (sim=0.633) had `page_oncall` as its historical action, so that action was the top-voted candidate. This is acceptable per `expected.json`.

**Regarding E07 specifically:** The OOD threshold in the code is 0.10, but E07's top match scores 0.803 — well above it. The engine reaches the correct answer (`page_oncall`) because the closest historical incident happened to use that action, not because it detected novelty. A more robust approach would use a higher OOD threshold or a distribution-based novelty metric (e.g., distance to k-th neighbor relative to the corpus's average inter-distance).

5. **What is the most likely class of incident that breaks your engine?**

Incidents where the actual root cause service emits only generic error logs (e.g., "internal error", "request failed"), while a downstream dependency emits highly specific, discriminative error messages. Because the similarity function weights logs heavily (up to 0.8 when trace data is absent), the engine may strongly match the downstream symptom's historical signature rather than the true root cause's pattern. This could lead to recommending an action targeting the wrong service.

**Concrete scenario:** If `checkout-svc` logs generic "upstream timeout" messages while `inventory-svc` logs distinctive "Redis CLUSTERDOWN" errors, the engine's TF-IDF scoring would give high weight to the distinctive Redis errors and match against historical incidents involving `inventory-svc` — even if the root cause is actually a deployment issue in `checkout-svc` that cascaded downstream.

**Proposed improvement:** Implement a topology-aware dampening factor. Using `topology.json`, reduce the similarity weight of log signatures from downstream (leaf-tier) services when they are reachable from the alerting service in the topology graph. Instead, prioritize log signals from services on the critical path between the alerting service and the deepest anomalous trace edge. I did not implement this within the time budget because it requires building a graph traversal layer and re-tuning the similarity weights, which risks destabilizing results on the easy cases (E01–E04) that currently work correctly.
