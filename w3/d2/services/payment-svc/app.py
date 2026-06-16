from flask import Flask, jsonify, request
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
    return jsonify({'status': 'ok', 'service': 'payment-svc'})

@app.after_request
def after_request(response):
    if request.path != '/metrics':
        REQUEST_COUNT.labels(status=str(response.status_code)).inc()
    return response

@app.route('/api/payment')
@REQUEST_LATENCY.time()
def payment():
    # Simulate processing time
    time.sleep(random.uniform(0.01, 0.1))
    return jsonify({'status': 'success', 'payment_id': f'pay_{int(time.time())}'})

@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080)
