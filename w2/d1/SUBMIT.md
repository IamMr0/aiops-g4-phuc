# W2-D1 Alert Correlation – Submission

## Configuration Choices

### gap_sec

I selected `gap_sec = 120` seconds.

The lab notes recommend 120 seconds as a practical default because it captures most incident bursts while avoiding excessive merging of unrelated events. A shorter window such as 30 seconds could split a single incident into multiple sessions, while a much larger window such as 600 seconds could incorrectly merge independent incidents into one cluster.

### max_hop

I selected `max_hop = 2`.

Using a topology distance of two hops allows the correlator to group services that are directly connected or separated by one intermediate dependency.

Example:

```text
edge-lb → checkout-svc → payment-svc
```

This captures common cascade failures while still keeping unrelated services separated.

A larger value would increase the risk of over-grouping alerts across the service graph, while a smaller value could split alerts that are actually part of the same incident.

---

## Correlation Results

### Input

- Total alerts: 20

### Output

- Total clusters: 3

### Reduction Ratio

```text
Reduction Ratio = 1 - (3 / 20) = 0.85
```

The correlator reduced 20 raw alerts into 3 investigation units, reducing the amount of work required for RCA by 85%.

### Cluster Summary

#### Cluster c-001

- Alert count: 18
- Services:
  - edge-lb
  - checkout-svc
  - payment-svc
  - cart-svc
  - notification-svc

This cluster represents the primary incident path. The services are connected through the application dependency graph and occurred within the same alert session. The cluster likely reflects a cascading failure propagating through the checkout and payment flow.

#### Cluster c-002

- Alert count: 1
- Services:
  - recommender-svc

This alert remained isolated because it is not within the topology distance threshold of the main payment incident and did not have enough related alerts to form a larger group.

#### Cluster c-003

- Alert count: 1
- Services:
  - search-svc

This alert also remained isolated. Although search-svc depends on catalog resources, it was not connected to the primary incident cluster within the configured topology and time constraints.

---

## Orphan Alert Example

One orphan cluster is the alert associated with `recommender-svc`.

It was not merged into the main cluster because topology-aware correlation only groups alerts that are sufficiently close in the service graph. The recommender service operates on a separate path and does not participate in the checkout-payment dependency chain that generated the majority of alerts.

Therefore the correlator correctly preserved it as a separate cluster rather than creating a false correlation.

---

## Design Trade-off

The primary trade-off is between correlation quality and over-grouping.

Using a larger `max_hop` value would create fewer clusters and potentially improve alert reduction, but it risks combining unrelated incidents into a single cluster. Using a smaller value improves precision but may split a real incident into multiple clusters.

Similarly, increasing `gap_sec` captures longer incidents but can merge unrelated alert bursts. Decreasing it improves separation but may fragment a single outage.

For this lab, `gap_sec = 120` and `max_hop = 2` provide a reasonable balance between noise reduction and incident accuracy.

---

## Scaling to 10,000 Alerts

If the input grows from 200 alerts to 10,000 alerts, the main bottleneck will be topology grouping.

The current implementation compares pairs of services and repeatedly computes shortest-path distances on the graph. As the number of alerted services increases, the number of pairwise comparisons grows significantly.

Potential optimizations include:

- Pre-computing shortest-path distances
- Caching graph traversal results
- Using connected-component techniques instead of repeated path calculations
- Processing alerts in streaming windows instead of loading all alerts into memory

These changes would improve performance while preserving correlation quality.

---

# EOD Checkpoint

## 1. Why does fingerprint not include timestamp or value?

Timestamp and value change every time an alert fires, even when the alert represents the same underlying issue.

For example, a payment latency alert may fire at 09:42 with value 3.2s and again at 09:43 with value 3.8s. If timestamp or value were included in the fingerprint, both alerts would generate different fingerprints and deduplication would never occur.

## 2. Difference between duplicate and correlated alerts

A duplicate alert is the same alert firing repeatedly.

Example:

- payment-svc latency_p99 critical
- payment-svc latency_p99 critical

These share the same fingerprint.

Correlated alerts are different alerts that likely originate from the same incident.

Example:

- payment-svc latency alert
- checkout-svc error alert
- edge-lb latency alert

The alerts are different but belong to the same cascade chain.

## 3. gap_sec = 30 vs gap_sec = 600

### gap_sec = 30

A very short window may split a single incident into multiple small clusters because alerts arriving slightly later will not be grouped together.

### gap_sec = 600

A very large window may merge unrelated incidents into one large cluster, creating false correlations and reducing RCA accuracy.

## 4. In the main scenario, should recommender-svc be merged into the main cluster?

No.

Although it generated an alert during the same overall period, topology-aware correlation considers service dependencies. The main incident involved the checkout-payment path, while recommender-svc belongs to a separate dependency chain.

Keeping it separate reduces false correlation and preserves incident accuracy.

## 5. Biggest limitation of topology grouping

Topology grouping assumes the service graph accurately represents failure propagation.

In reality, failures can propagate through shared infrastructure, databases, queues, cloud resources, or indirect dependencies that are not explicitly represented in the graph.

One improvement would be adding semantic similarity or historical incident patterns so that correlation decisions use both topology and behavioral evidence rather than graph distance alone.