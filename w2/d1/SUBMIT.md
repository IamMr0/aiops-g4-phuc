# W2-D1 Alert Correlation – Submission

## Configuration Choices

### gap_sec

I selected `gap_sec = 120` seconds.

The session window closes when no new alert arrives within the gap period. A 120-second gap captures the natural burst pattern of a cascading incident — where alerts from downstream services typically arrive within one to two minutes of the root-cause alert — while avoiding false merges from unrelated incidents that happen to occur close together. A shorter gap such as 30 seconds risks splitting a single incident into multiple sessions if alerts arrive with slight delays. A larger gap such as 600 seconds risks grouping independent incidents into one cluster, making RCA harder rather than easier.

### max_hop

I selected `max_hop = 1`.

A hop distance of one on the undirected service graph groups only services that are directly connected by a dependency edge. This is the strictest topology setting: two services are correlated only if one literally calls the other. The rationale for choosing 1 over 2 is to keep cluster membership conservative — every service in a cluster has a direct, explicit relationship with at least one other member, reducing the risk of including services whose connection is only transitive.

For this dataset the result is still correct: `payment-svc`, `checkout-svc`, `edge-lb`, `cart-svc`, `notification-svc`, and `search-svc` are all reachable within one hop from at least one other alerted service in the group, so they land in c-001. `recommender-svc` has no direct edge to any of them and correctly remains isolated.

---

## Correlation Results

### Input

- Total alerts: 20

### Output

- Total clusters: 2

### Reduction Ratio

```
reduction_ratio = 1 - (2 / 20) = 0.90
```

The correlator reduced 20 raw alerts into 2 investigation units, a 90% reduction in the number of items an on-call engineer needs to reason about during RCA.

### Cluster Summary

#### Cluster c-001

- Alert count: 19
- Services: `cart-svc`, `checkout-svc`, `edge-lb`, `notification-svc`, `payment-svc`, `search-svc`

This is the primary incident cluster. All six services are connected through the service dependency graph and their alerts fall within the same session window. The cluster reflects a cascading failure originating in `payment-svc` (DB connection pool exhaustion) that propagates upstream through `checkout-svc` and `edge-lb`, and sideways into `cart-svc` and `notification-svc` via shared dependencies. `search-svc` (alert `a-0016`) is included because it is directly connected to a service in the chain within one hop; its `labels.note` field is not evaluated since noise filtering is not applied in this pipeline version.

#### Cluster c-002

- Alert count: 1
- Services: `recommender-svc`

This alert remained isolated. `recommender-svc` has no direct dependency edge to any of the six services in c-001, so at `max_hop = 1` the Union-Find algorithm never unions it with the main component. It forms its own single-alert cluster.

---

## Orphan Alert Example

**Alert ID: `a-0013`** (`recommender-svc` — `cpu_utilization`, warn, 09:45:10Z)

This alert was not merged into c-001 because `recommender-svc` has no direct edge to any alerted service on the undirected service graph. At `max_hop = 1`, the Union-Find algorithm only unions pairs whose shortest path length is exactly 1. Since no such path exists between `recommender-svc` and the checkout-payment chain, it remains its own component and forms c-002.

Note that `a-0016` (`search-svc`) is **not** an orphan in this version. Without the noise filter, its `labels.note = "noise — independent slow query"` is ignored, and its graph position (within one hop of the main chain) causes it to be absorbed into c-001. This is an intentional consequence of removing noise detection: the pipeline now relies entirely on topology and time rather than label-based heuristics.

---

## Design Trade-off

The primary trade-off in this pipeline is between **recall** (catching all alerts that belong to the same incident) and **precision** (not grouping unrelated alerts together).

Using `max_hop = 1` instead of 2 improves precision — cluster membership requires a direct dependency edge, not just a transitive graph connection — but reduces recall for incidents that propagate across two or more hops. If a future incident cascades from `payment-svc` through an intermediate service to a third service that is not directly connected to `payment-svc`, those alerts would be split into separate clusters at `max_hop = 1`.

Removing the noise filter is the more significant trade-off. The original pipeline isolated alerts whose `labels.note` contained "noise" or "unrelated" into their own clusters regardless of topology, providing an explicit override mechanism. Without it, the pipeline is simpler and fully deterministic based on graph structure and time alone, but it loses the ability to suppress alerts that operators have already labeled as unrelated. In this dataset `a-0016` ends up in c-001 as a result — acceptable here, but in a larger system with more labeled noise alerts this could inflate cluster size and increase RCA noise.

For this dataset, `gap_sec = 120` and `max_hop = 1` achieve a 90% reduction ratio while keeping cluster membership directly traceable to explicit service dependencies.

---

## Scaling to 10,000 Alerts

If the input grows from 20 to 10,000 alerts, the main bottleneck is `topology_group()`.

The current implementation calls `nx.shortest_path_length()` for every pair of distinct alerted services. If K services appear in a session, this is O(K²) path lookups, each of which traverses the graph. With a large alert volume, many distinct services will appear per session and the pairwise comparison count grows quadratically.

Concrete optimizations:

- **Pre-compute all-pairs shortest paths** once at startup using `nx.all_pairs_shortest_path_length()` and cache the result. Path lookups become O(1) dictionary reads.
- **Limit session size**: cap a session at N alerts before forcing a close, preventing one huge session from dominating processing time.
- **Stream processing**: rather than loading all alerts into memory, process alerts in a rolling buffer and emit closed sessions immediately.
- **Connected-component shortcut**: at `max_hop = 1` specifically, build a subgraph containing only alerted services and run `nx.connected_components()` directly — no pairwise iteration needed at all.

These changes preserve correlation quality while reducing time complexity from O(K² · E) to O(K²) or better.

---

# EOD Checkpoint

## 1. Why does fingerprint not include timestamp or value?

Timestamp and value change on every firing of an alert, even when the alert represents the exact same underlying condition. If either field were included in the fingerprint, two firings of `payment-svc | latency_p99_ms | crit` at 09:42 (value 3.2s) and 09:43 (value 3.8s) would produce different fingerprints and never be deduplicated. The dedup layer would be completely ineffective — every alert would appear unique and the output cluster count would equal the input alert count.

## 2. Difference between duplicate and correlated alerts

A **duplicate** alert is the same alert firing repeatedly. It has the same fingerprint (`service | metric | severity`) and represents one condition being reported multiple times.

Example from the dataset:
- `payment-svc | latency_p99_ms | crit` fires as `a-0003`, then again as `a-0008`, then again as `a-0015`

Same fingerprint → count = 3 → treated as one logical alert.

A **correlated** alert is a *different* alert that originates from the same underlying incident.

Example:
- `payment-svc | db_connection_pool_used_ratio | crit` (root symptom, `a-0002`)
- `checkout-svc | downstream_payment_error_rate | crit` (cascade effect, `a-0006`)
- `edge-lb | upstream_5xx_rate | warn` (further upstream effect, `a-0007`)

Different fingerprints → not deduplicated → grouped by time-window + topology into the same cluster.

## 3. gap_sec = 30 vs gap_sec = 600

**gap_sec = 30**: Sessions close very quickly. A single incident whose alerts arrive spread over 90 seconds would be split into multiple small clusters, increasing the number of units an engineer must investigate and potentially obscuring the cascade relationship between services.

**gap_sec = 600**: Sessions stay open for 10 minutes. Two independent incidents — for example, a payment outage at 09:42 and an unrelated database maintenance alert at 09:48 — could be merged into one large cluster, creating a false correlation that sends the engineer chasing symptoms from two different root causes simultaneously.

## 4. In the main scenario, should recommender-svc be merged into the main cluster?

No. Although `recommender-svc` generated an alert during the same general time period, it has no direct dependency edge to any service in the checkout-payment chain. At `max_hop = 1` the Union-Find algorithm correctly keeps it separate.

The alert (`a-0013`, cpu_utilization during a batch retrain) also reflects an independent operational event unrelated to the DB connection pool exhaustion that caused the main incident. Merging it would add noise to the RCA investigation rather than reducing it. The correlator's output of c-002 as a separate single-alert cluster is the correct behaviour.

## 5. Biggest limitation of topology grouping

Topology grouping assumes the service dependency graph is complete and accurate. In practice, services often share infrastructure that is not represented as explicit edges: the same database cluster, the same message queue, the same cloud region, or the same DNS resolver. A failure in any of those shared components can cause alerts in services that appear unconnected on the graph, and the correlator will split them into separate clusters even though they share a root cause.

One way to address this is to augment the graph with **infrastructure dependency edges** — adding nodes for shared databases, queues, and network zones, and drawing edges from every service that depends on them. This makes implicit shared dependencies explicit so the topology correlator can group alerts that propagate through shared infrastructure rather than only through direct service-to-service call relationships.