# SUBMIT.md — Reflection: MLOps Lifecycle Lab

## Question 1: What drift threshold did you choose and why?

The threshold is **0.15** (15% of features drifted). Selection method: running `drift_detector` on `baseline.csv` itself, 70/30 split — the measured noise floor is 0.04. Threshold 0.15 = 3.75× the noise floor, which is far enough to avoid false positives from seasonal variation (morning/evening), but low enough to catch actual drift. When tested with `drifted.csv`, the score is 0.67 — clearly exceeding the threshold. If 0.05 was chosen, the drift check would fire every day due to intraday traffic patterns. If 0.50 was chosen, it would miss early-stage drift when only 1-2 features begin to shift.

---

## Question 2: What happens if model v2 is worse than v1 after retraining?

The current pipeline has a manual approval gate — the ML engineer checks the `anomaly_rate` of v2 before promoting. If the v2 `anomaly_rate` is abnormal (too high or too low compared to v1), the engineer refuses to promote, and v2 remains under the `staging` alias. Rollback if v2 was already promoted: call `MlflowClient.set_registered_model_alias("anomaly-detector", "production", "1")` to swap the alias back to v1, then `POST /reload` on `serve.py`. The whole process takes < 30 seconds because it only changes the alias, with no redeployment needed. Improvement: implement shadow mode to compare v1 vs v2 in parallel on production traffic before cutover.

---

## Question 3: What is the difference between data drift and concept drift?

**Data drift**: input distribution changes — P(X) changes, but the X→Y relationship remains the same. Example: latency baseline increases from 120ms to 156ms due to adding a 3rd-party integration. The model is still correct in principle, but the anomaly threshold is no longer appropriate.

**Concept drift**: input-output relationship changes — P(Y|X) changes. Example: the same 200ms latency used to be an anomaly, but after scaling up infrastructure, 200ms is normal. The model is completely wrong even though the input distribution hasn't changed much.

`Evidently DataDriftPreset` in this lab detects **data drift** using statistical tests on feature values. Concept drift is not directly detected because there are no production labels. Proxy: monitor the `anomaly_rate` trend over time in MLflow — if the rate spikes without a real incident, it's a sign of concept drift.

---

## Question 4: Why is a blue-green swap more important than replacing the file directly?

Replacing the file directly (overwriting the model artifact) creates a race condition: `serve.py` is processing a request using the old model, and at the same time the file is overwritten → corrupted read → crash or wrong prediction. There is no rollback — the old version is deleted.

Blue-green via MLflow alias: the `production` alias is swapped atomically from v1 → v2. `serve.py` only loads the new model when it receives `POST /reload` — all prior in-flight requests finish with v1. If v2 has an issue, swapping the alias to v1 + reload = immediate rollback without needing to redeploy. Both versions exist in parallel within the registry — nothing is lost.

---

## Question 5: If automating the approval gate, what metric and threshold would you use?

Use the **`anomaly_rate` delta** between v2 and v1 on the same validation window (using the last 20% of the current window as holdout). Auto-promote conditions:

- `abs(v2_anomaly_rate - v1_anomaly_rate) < 0.05` — v2 hasn't changed behavior too much
- `v2_anomaly_rate < 0.10` — not degenerate (flagging all data as anomalies)
- `v2_anomaly_rate > 0.01` — not overly conservative (detecting nothing)

The 5% delta threshold is conservative for the payment domain — a 5% deviation on 1000 requests/minute = 50 missed anomalies/minute, not including the SLA impact. Additionally, check that the v2 drift score on the validation window < threshold (i.e., v2 is trained on the correct distribution). If all 3 conditions are met, auto-promote. If not, trigger an alert for an ML engineer to review within 4h.
