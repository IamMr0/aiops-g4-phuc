# MLOps Lifecycle Pipeline

After reviewing the code against `data-pack/HANDOUT.md` and `data-pack/README.md`, the implementation in `Phuc` fully satisfies the lab requirements, including all three stress scenarios (Combined Check Mode, Holdout Validation, and Post-Deploy Auto-Rollback).

To run the pipeline from start to finish from the root of the lab (`lab-mlops-lifecycle`), follow these steps in your terminal. Note that PowerShell syntax is included for environment variables.

## 1. Bring up the stack
```bash
bash data-pack/scripts/start_stack.sh
```
*(Wait ~30 seconds for services like Postgres and MLflow to fully initialize)*

## 2. Generate datasets
```bash
uv run python data-pack/data/generate_data.py
```

## 3. Train the baseline model and register it as production
Set the MLflow tracking URI (PowerShell syntax):
```powershell
$env:MLFLOW_TRACKING_URI="http://localhost:5000"
```
*(If using Bash/Git Bash: `export MLFLOW_TRACKING_URI=http://localhost:5000`)*

Run the pipeline:
```bash
uv run python Phuc/pipeline.py --data data-pack/data/baseline.csv
```

## 4. Start the model serving endpoint
In a new terminal window, set the environment variable again and start the server:
```powershell
$env:MLFLOW_TRACKING_URI="http://localhost:5000"
uv run python Phuc/serve.py
```
*(Verify it's running in another terminal via: `curl -s http://localhost:8000/health/active-version`)*

## 5. Verify Drift Detection (Stress Scenario 1)
Run the drift detector in `combined` mode to catch both data distribution shifts and performance concept drift:
```bash
uv run python Phuc/drift_detector.py \
  --reference data-pack/data/baseline.csv \
  --current data-pack/data/drifted.csv \
  --check-mode combined \
  --model-uri models:/anomaly-detector@production \
  --labeled-current data-pack/data/drifted.csv
```

## 6. Retraining, Holdout Validation, and Auto-Rollback (Stress Scenarios 2 & 3)
Simulate drift detection, retraining on sliding window, staging promotion, and post-deploy monitoring:
```bash
uv run python Phuc/retrain.py \
  --reference data-pack/data/baseline.csv \
  --current data-pack/data/drifted.csv \
  --holdout data-pack/data/holdout.csv \
  --post-deploy-eval data-pack/data/post_deploy_eval.csv \
  --serve-url http://localhost:8000
```

**This final step will:**
- Detect drift and train a new version on the sliding window (baseline + drifted).
- Validate the new model against the holdout set (`holdout.csv`).
- Register it as `staging` and prompt for your approval in the terminal.
- If approved, seamlessly reload the production serving endpoint with the new model.
- Monitor the new model post-deployment using `post_deploy_eval.csv`, and automatically trigger a rollback to v1 if precision drops below 0.65.
