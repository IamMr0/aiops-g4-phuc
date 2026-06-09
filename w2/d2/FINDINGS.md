# FINDINGS

## RCA Analysis

The RCA pipeline analyzed 2 alert clusters using a combination of graph traversal, temporal ranking, and incident-history retrieval.

For the primary cluster, the predicted root cause service was **payment-svc**. The service achieved the highest combined score because it occupied the deepest dependency position in the service graph while also appearing among the earliest services involved in the alert timeline. The graph-based ranking identified payment-svc as the most likely culprit, while checkout-svc and edge-lb were ranked lower because they appeared to be downstream victims of the failure.

The retrieval component searched historical incidents and identified several similar incidents involving payment-related failures. Using those historical records, the pipeline classified the incident as **connection_pool_exhaustion** and suggested remediation actions based on previously successful responses. This demonstrates how retrieval can provide additional context beyond graph analysis alone.

## Confidence Assessment

The confidence score for the top-ranked candidate was relatively high and provided a useful signal for narrowing investigation efforts. The ranking successfully reduced multiple alerted services into a small set of likely root-cause candidates.

However, I would not immediately deploy automatic remediation solely based on the confidence score. Incorrect dependency information, cascading failures, or simultaneous service degradation could still produce false positives. Human validation remains important before executing actions such as rollbacks or infrastructure modifications.

## Uncertain Case

One challenging scenario occurs when multiple services have similar graph positions and alert timestamps. In these situations, the score difference between candidates becomes very small, making it difficult to confidently determine the actual culprit. Historical incident retrieval may also return incidents that are operationally similar but not identical to the current situation.

## Limitations of the Rule-Based Approach

Compared with an LLM-assisted RCA system, the current implementation relies heavily on graph structure and historical similarity matching. It cannot deeply reason about logs, traces, deployment events, or complex multi-service interactions. The retrieval mechanism is effective when similar incidents already exist in the historical dataset, but it may struggle when encountering completely new failure patterns.

Despite these limitations, the rule-based approach remains fast, deterministic, inexpensive, and suitable for environments where external LLM APIs are unavailable. It provides a practical baseline RCA solution while maintaining predictable behavior and low operational cost.