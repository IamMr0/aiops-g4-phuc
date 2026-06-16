import requests
import uuid
from datetime import datetime, timezone

alert = {
    "id": f"chaos-{uuid.uuid4().hex[:8]}",
    "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "service": "api-gateway",
    "metric": "availability",
    "severity": "critical",
    "value": 0.0,
    "threshold": 1.0,
    "labels": {"source": "chaos_runner"},
}

try:
    r = requests.post("http://localhost:8000/incident", json={"alerts": [alert]})
    print("STATUS:", r.status_code)
    print("BODY:", r.text)
except Exception as e:
    print("EXCEPTION:", e)
