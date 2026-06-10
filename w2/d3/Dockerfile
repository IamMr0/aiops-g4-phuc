FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY correlate.py rca.py pipeline.py serve.py ./
COPY dataset/ ./dataset/

EXPOSE 8000

# Production: 4 workers, no access log (latency middleware handles it)
CMD ["uvicorn", "serve:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--no-access-log"]