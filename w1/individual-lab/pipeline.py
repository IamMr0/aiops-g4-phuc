import json
import uvicorn
from fastapi import FastAPI, Request

app = FastAPI()
ALERTS_FILE = "alerts.jsonl"

# State to avoid spamming the same alert repeatedly
last_alerted_type = None

@app.post("/ingest")
async def ingest(request: Request):
    global last_alerted_type
    
    payload = await request.json()
    metrics = payload.get("metrics", {})
    timestamp = payload.get("timestamp")

    # Defined thresholds based on baseline profile
    # Normal dependency timeout rate: 0-0.4%
    dependency_timeout_threshold = 5.0
    # Normal http req/s: 80-160
    traffic_spike_rps_threshold = 250.0
    traffic_spike_queue_threshold = 20
    # Normal memory: ~800M
    memory_leak_threshold = 1_000_000_000
    # Normal GC pause: 8-18ms
    memory_leak_gc_threshold = 30.0

    detected_type = None
    severity = "critical"
    message = ""

    # Simple rule-based anomaly detection
    if metrics.get("upstream_timeout_rate", 0) > dependency_timeout_threshold:
        detected_type = "dependency_timeout"
        message = f"Dependency timeout detected. Upstream timeout rate: {metrics['upstream_timeout_rate']}%"
    elif metrics.get("http_requests_per_sec", 0) > traffic_spike_rps_threshold or metrics.get("queue_depth", 0) > traffic_spike_queue_threshold:
        detected_type = "traffic_spike"
        message = f"Traffic spike detected. RPS: {metrics['http_requests_per_sec']}, Queue Depth: {metrics['queue_depth']}"
    elif metrics.get("memory_usage_bytes", 0) > memory_leak_threshold or metrics.get("jvm_gc_pause_ms_avg", 0) > memory_leak_gc_threshold:
        detected_type = "memory_leak"
        message = f"Memory leak detected. Memory: {metrics['memory_usage_bytes']} bytes, GC Pause: {metrics['jvm_gc_pause_ms_avg']} ms"

    if detected_type and detected_type != last_alerted_type:
        alert = {
            "timestamp": timestamp,
            "type": detected_type,
            "severity": severity,
            "message": message
        }
        with open(ALERTS_FILE, "a") as f:
            f.write(json.dumps(alert) + "\n")
        
        last_alerted_type = detected_type
        print(f"[ALERT] {timestamp} - {detected_type} - {message}")

    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
