.PHONY: install run run-prod test test-v lint clean docker-build docker-run

# ── Install ──────────────────────────────────────────────────────────────────
install:
	pip install -r requirements.txt

# ── Dev server (auto-reload) ─────────────────────────────────────────────────
run:
	uvicorn serve:app --host 0.0.0.0 --port 8000 --reload

# ── Production server (4 workers) ────────────────────────────────────────────
run-prod:
	uvicorn serve:app --host 0.0.0.0 --port 8000 --workers 4 --no-access-log

# ── Tests ────────────────────────────────────────────────────────────────────
test:
	pytest tests/ -q

test-v:
	pytest tests/ -v

# ── Lint (optional) ──────────────────────────────────────────────────────────
lint:
	python -m py_compile serve.py pipeline.py correlate.py rca.py
	@echo "Syntax OK"

# ── Smoke test (requires server running on :8000) ────────────────────────────
smoke:
	@echo "==> GET /healthz"
	curl -s http://localhost:8000/healthz | python -m json.tool
	@echo "\n==> GET /readyz"
	curl -s http://localhost:8000/readyz | python -m json.tool
	@echo "\n==> GET /version"
	curl -s http://localhost:8000/version | python -m json.tool
	@echo "\n==> POST /incident"
	curl -s -X POST http://localhost:8000/incident \
		-H "Content-Type: application/json" \
		-d '{"alerts":[{"id":"a-1","ts":"2026-06-12T09:42:01Z","service":"payment-svc","metric":"latency_p99_ms","severity":"critical","value":1840,"threshold":500}]}' \
		| python -m json.tool

# ── Docker ───────────────────────────────────────────────────────────────────
docker-build:
	docker build -t aiops-pipeline:latest .

docker-run:
	docker run -p 8000:8000 \
		-e AIOPS_USE_LLM=false \
		aiops-pipeline:latest

# ── Clean ────────────────────────────────────────────────────────────────────
clean:
	find . -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -f rca_llm_trace.log .pytest_cache -rf