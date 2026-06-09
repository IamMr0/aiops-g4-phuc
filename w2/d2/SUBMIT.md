# SUBMIT

## 1. Confidence of the Largest Cluster

The top-1 root cause candidate for the largest cluster was **payment-svc**, which achieved the highest combined graph and temporal score among all services in the cluster.

If I had to define a threshold for fully automated rollback without SRE confirmation, I would choose **0.85**. A lower threshold increases the risk of triggering remediation for the wrong service, especially when service dependencies are incomplete or multiple services fail simultaneously. A higher threshold reduces false positives and makes automated actions safer.

## 2. Classifier Variant Chosen

I selected **Variant A (Rule-Based / Retrieval-Based RCA)**.

The implementation uses graph traversal, PageRank scoring, timestamp ordering, and incident-history retrieval to determine the most likely root cause and classify the incident. This approach worked reliably without requiring external APIs or paid services.

Advantages:
- No API cost
- Fast execution
- Deterministic outputs
- Easy debugging and validation

Disadvantages:
- Limited reasoning capability
- Cannot deeply analyze novel incidents
- Depends on graph quality and historical incident coverage

Compared with Variant B or C (LLM-based approaches), the rule-based approach is more predictable and cost-effective but less flexible when handling unfamiliar failure patterns.

## 3. Industry Landscape Comparison

The pipeline I built is most similar to **Dynatrace Davis** because it assumes that the service dependency graph is trustworthy and uses topology information as the primary signal for identifying root causes.

For the GeekShop environment, this design choice is reasonable because the service architecture is relatively stable and dependency relationships are known. Graph-based RCA can respond quickly during incidents and requires less historical metric data than causal-learning approaches.

If the environment became highly dynamic or service dependencies were frequently changing, a more data-driven approach similar to Causely could become a better option. However, for the current GeekShop scenario, graph-based RCA remains an effective and practical solution because it provides fast and interpretable results.