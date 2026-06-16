from flask import Flask, jsonify
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import os
import time
import random

app = Flask(__name__)

# Metrics
REQUEST_COUNT = Counter('http_requests_total', 'Total requests', ['status'])
REQUEST_LATENCY = Histogram('http_request_duration_seconds', 'Request latency')

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'inventory-svc'})

@app.route('/api/inventory')
@REQUEST_LATENCY.time()
def inventory():
    REQUEST_COUNT.labels(status='200').inc()
    # Simulate processing time
    time.sleep(random.uniform(0.01, 0.1))
    return jsonify({'status': 'success', 'stock': random.randint(0, 100)})

@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
