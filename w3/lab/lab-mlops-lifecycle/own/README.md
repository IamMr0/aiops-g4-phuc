# MLOps Lifecycle Pipeline

To run the pipeline from start to finish, follow these steps:

1. Bring up the stack with:
   `bash data-pack/scripts/start_stack.sh`
   (Wait a few seconds for services to fully initialize)
   
2. Generate datasets with:
   `uv run python data-pack/data/generate_data.py`
   
3. Train the baseline model and register it as production with:
   `export MLFLOW_TRACKING_URI=http://localhost:5000`
   `uv run python pipeline.py --data ../data-pack/data/baseline.csv`
   
4. Start the model serving endpoint in a background terminal with:
   `export MLFLOW_TRACKING_URI=http://localhost:5000`
   `uv run python serve.py`
   (You can verify it's running via `curl -s http://localhost:8000/health/active-version`)
   
5. Simulate drift detection and automatic retraining by running:
   `export MLFLOW_TRACKING_URI=http://localhost:5000`
   `uv run python retrain.py --reference ../data-pack/data/baseline.csv --current ../data-pack/data/drifted.csv --serve-url http://localhost:8000`

This final step will detect drift, train a new version, register it as staging, prompt for your approval in the terminal, and if approved, seamlessly reload the production serving endpoint with the new model.
