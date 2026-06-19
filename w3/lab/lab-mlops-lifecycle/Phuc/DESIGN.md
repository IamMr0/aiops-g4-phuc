# DESIGN.md — MLOps Lifecycle: Anomaly Detection Pipeline

## Overview

A pipeline that detects drift in payment gateway metrics (latency_p99, error_rate, rps), triggers retraining of the IsolationForest model, and swaps the new version via MLflow Registry alias.

---

## Sub-checkpoint 1: Drift Threshold

**Chosen value: 0.15** (15% of features drifted according to Evidently DataDriftPreset).

**Selection method:** First, run `drift_detector` on the `baseline.csv` itself, split 70/30 (first 2 months as reference, last 1 month as current). The resulting drift score = 0.04 — this is the "noise floor" when there is no actual drift. From there, select a threshold = 0.15, which is 3.75× the noise floor. With `drifted.csv`, the actual measured score is 0.67 (2/3 features drifted), clearly exceeding the threshold.

**Risk if the threshold is too low (e.g., 0.05):** false positive — retraining is triggered after every normal seasonal fluctuation (morning/evening traffic differs). Wastes compute and causes alert fatigue.

**Risk if the threshold is too high (e.g., 0.50):** false negative — missing actual drift, the model continues to serve with a distribution that is no longer appropriate, and precision/recall silently decrease.

---

## Sub-checkpoint 2: Drift Type

**Detected type: Data drift** — P(X) changes, meaning the input features distribution (latency_p99, error_rate, rps) has shifted compared to the training data.

**Evidently DataDriftPreset detection:** Statistical test on each feature. Defaults to using Wasserstein distance for numerical features. When `share_of_drifted_columns > threshold` → flag.

**Why data drift fits this problem:** Payment gateway anomaly detection needs to know when the "new normal" differs from the "old normal". After a campaign, the latency baseline increases to 156ms — model v1 trained with a 120ms baseline will consider 156ms as an anomaly even though it's actually normal. Detecting data drift allows retraining the model with the new distribution before precision drops significantly.

**Concept drift (P(Y|X) changes) is not directly detected** in this pipeline because there are no ground truth labels in production. Performance drift (proxy: tracking the anomaly rate trend) is logged into MLflow on every drift check to be visualized.

---

## Sub-checkpoint 3: Retrain Trigger Configuration

**Trigger type: Manual approval gate** — semi-automatic.

**Cadence:** No fixed schedule. The drift check is called when there is a new batch of data (can be integrated into a daily batch job). But promotion from staging → production always requires human approval.

**Reason for choosing manual:** An anomaly detection model in a payment system directly affects the on-call SLA. A worse model promoted automatically can cause false negatives on an actual incident, or an alert storm from false positives. The approval gate ensures the ML engineer reviews the metric (anomaly_rate of v2 vs v1) before cutover.

**Approval timeout:** Timeout is not implemented in the lab. In production, a 24h timeout is recommended — if there is no approval within 24h, the staging version is archived and the drift check resets. Prevents the "staging model hanging forever with no one reviewing it" state.

**If fully automated:** A/B shadow mode can be used (optional D in HANDOUT) — `serve.py` calls both v1 (production) and v2 (staging) in parallel for 24h, comparing the `anomaly_rate` delta. If delta < 5% and there are no false negatives on a known incident window → auto-promote. A 5% threshold is conservative for the payment domain.

---

## Sub-checkpoint 4: Versioning and Rollback

**Versioning strategy:** MLflow Registry with aliases, independent of version numbers.

- `production` alias → the version currently serving
- `staging` alias → the candidate version after retraining
- Version numbers (1, 2, 3...) are immutable audit trails

**Why aliases are better than version numbers in the serve.py code:** `mlflow.pyfunc.load_model("models:/anomaly-detector@production")` does not change when swapping. If the version number is hardcoded, `serve.py` must be redeployed every time it is retrained.

**Rollback path:**
1. Detect v2 underperforming (precision drops, alert storm): `MlflowClient.set_registered_model_alias("anomaly-detector", "production", "1")` — swap alias back to v1.
2. Call `POST /reload` on `serve.py` — reload v1 from the registry.
3. The entire process takes < 30 seconds, no container redeployment needed.

**Who has rollback rights:** ML engineer on-call (with MLflow admin access). In production, a rollback should be wrapped into a Runbook command with an audit log.

**Retention policy:** Keep all registered versions indefinitely (artifacts consume storage but IsolationForest models are < 1MB). Do not delete old versions as they are needed for audits and rollbacks at any time.

---

## Component Architecture

```
baseline.csv (reference)
     │
     ├──► pipeline.py ──► MLflow Run ──► Registry v1 @production
     │
drifted.csv (current window)
     │
     ├──► drift_detector.py
     │         │ score=0.67 > threshold=0.15
     │         ▼
     └──► retrain.py
               │
               ├── train IsoForest on drifted.csv
               ├── MLflow Run → Registry v2 @staging
               ├── [HUMAN APPROVAL]
               ├── set alias production → v2
               └── POST /reload → serve.py
```

---

---

## Sub-checkpoint 5: Drift Detection Mechanism — why combined mode is needed

Using only `DataDriftPreset` (data drift) is not enough. Data drift detects when P(X) changes — i.e., the input feature distribution shifts. But in a payment gateway scenario, **concept drift** can occur: P(Y|X) changes while P(X) remains stable. Specific example: after rolling out a new payment processor, the same latency of 180ms might be the "new normal" for the old processor but an "actual anomaly" for the new processor — or vice versa. Evidently will not detect this because the feature distribution hasn't changed.

`--check-mode combined` runs 2 mechanisms in parallel: (1) Evidently `DataDriftPreset` on feature distribution, and (2) evaluates precision/recall of the current model on `holdout.csv` (labeled set from the old pattern). If either flags — `is_drift = True` or `perf_is_degraded = True` — retraining will be triggered. The default performance threshold is precision ≥ 0.70; if model v1 reached 0.91 on the initial validation set but is only 0.62 on the current holdout, it's a clear signal of concept drift even though Evidently's feature score remains low.

---

## Sub-checkpoint 6: Data Selection Strategy — sliding window vs alternatives

When retraining only on the drift window (last 7 days), model v2 overfits to the new distribution: it learns that 156ms latency is "normal" but forgets that the system still has to process batch jobs running on the old pattern. Experiment: training on the drift window → v2 precision on `holdout.csv` (old pattern) drops ~18% compared to v1.

**Sliding window strategy** (baseline + drift window concat) yields better results because the model sees both regimes. With `baseline.csv` (4320 rows) + `drifted.csv` (1008 rows), the total training set is 5328 rows — enough so the IsolationForest is not dominated by the new distribution. Acceptance criterion: v2 precision and recall on `holdout.csv` must be ≥ v1 precision/recall measured on the same set.

Alternatives: (a) **Pure drift window** — simple but overfits as analyzed above; (b) **Weighted sampling** (oversample baseline) — more complex, reasonable when the drift window is very small; (c) **Full historical concat** — safest but computationally expensive when data accumulates over many months. The sliding window is the best trade-off for this lab.

---

## Sub-checkpoint 7: Auto-rollback — threshold and policy

After v2 is promoted to `@production`, `post_deploy_monitor` runs N polling cycles evaluating precision on `post_deploy_eval.csv` (200 rows clearly labeled: 60% clear-normal, 40% clear-anomaly). Default threshold: `precision < 0.65` → auto-rollback.

Why 0.65? This is a conservative threshold — lower than the 91% baseline but far enough to avoid triggering a false rollback due to sampling noise on 200 rows. Calculation: with 80 anomaly rows (40%), if the model misses 30 → precision = 50/57 ≈ 0.88; if the model is completely confused → precision ≈ 0.40. The 0.65 threshold is at the point where the "model is clearly deviating significantly".

Rollback flow: `client.set_registered_model_alias(MODEL_NAME, "archived", v2_version)` → `client.set_registered_model_alias(MODEL_NAME, "production", v1_version)` → `POST /reload`. Total < 5 seconds. All events are appended to `outputs/audit_log.jsonl` with event key `auto_rollback_v2_to_v1`, including the demoted version, the restored version, the precision trigger value, and the cycle number.

---

## Observability: why these metrics are important in MLOps

MLOps monitoring differs from regular service monitoring in that the cause of degradation is not a code bug but a **data shift**. The drift score and precision/recall over time allow detecting model decay before on-call receives a complaint. The active version gauge and alias state table solve the problem of "which version is being served?" — a question that usually takes several minutes to look up in the MLflow UI. The retrain event counter and auto-rollback counter create a minimal audit trail: the number of times the system intervened is a signal about the stability of the production distribution. These metrics do not replace MLflow experiment tracking but supplement it: MLflow logs details of each run, Grafana visualizes the operational trend in real-time.

---

## Accepted Trade-offs

| Decision | Pros | Cons |
|---|---|---|
| Manual approval gate | Safety, human oversight | Latency in retrain loop (hours, not minutes) |
| Data drift only (no performance drift) | Simple, requires no labels | Misses concept drift when the distribution is stable but model accuracy drops |
| IsolationForest (no LSTM-AE) | Trains in < 1s, explainable, no GPU | Does not capture temporal patterns, each row is independent |
| Local artifact store | No S3 setup needed | Does not scale multi-node, artifacts are lost when the volume is deleted |
