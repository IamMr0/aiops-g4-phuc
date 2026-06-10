My Learning Notes Archive Search Tags Notes XBrain ← AIOps W2 --- RCA &
Smart Response W2-D3: Model Serving --- Đưa Pipeline Lên Production 12
min read 2554 words Table of Contents Model Serving --- Đưa Pipeline Lên
Production Mở đầu: từ notebook → service Bạn đã có 2 module chạy được
trong notebook: 1 cái gom alert thành cluster, 1 cái tìm root cause.
Notebook không phải production. Hôm nay biến nó thành API service --- 1
hệ thống monitoring có thể POST batch alert vào và nhận lại incident
report. 6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa Pipeline Lên
Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
1/22

------------------------------------------------------------------------

## Page 2

Khi nói "serving" trong AIOps, không chỉ là serve ML model --- nó là
serve toàn bộ pipeline (correlation + RCA + LLM call) như 1 unit có:
HTTP endpoint Latency budget (p99 ≤ 10s) Health check Versioning +
rollback Self-monitoring Cuối ngày bạn có serve.py chạy được ---
pipeline trong notebook trở thành HTTP service nhận request từ ngoài. 💡
Quy tắc vàng: Code trong notebook khác code production ở 3 thứ ---
concurrency, failure handling, observability. Đừng đợi production mới
nghĩ về 3 thứ này. 1. Framework --- FastAPI vs Flask vs BentoML 3
framework phổ biến cho Python serving: 6/9/26, 10:50 PM W2-D3: Model
Serving --- Đưa Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
2/22

------------------------------------------------------------------------

## Page 3

FRAMEWORK KHI NÀO DÙNG ƯU NHƯỢC Flask Quick prototype Simple, ít magic
Sync only, không validate input native FastAPI ⭐ Production API, mixed
workload Async, Pydantic validation, OpenAPI auto, type hints Magic hơn
Flask một chút BentoML ML model--centric Model versioning, batching
native, Yatai deploy Học curve cao, overhead cho non-ML workload Cho bài
tập dùng FastAPI. Lý do: pipeline có LLM call (IO-bound → hưởng async),
input có schema (Pydantic dễ), test bằng curl / requests (OpenAPI
auto-document). 2. Endpoint cơ bản 2.1 Skeleton uv pip install fastapi
uvicorn pydantic \# serve.py from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field from typing import Optional import
logging logging.basicConfig(level=logging.INFO, format='%(asctime)s
%(levelname)s %(mes logger = logging.getLogger('aiops') app = FastAPI(
title='AIOps Incident Pipeline', version='1.0.0', 6/9/26, 10:50 PM
W2-D3: Model Serving --- Đưa Pipeline Lên Production \| My Learning
Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
3/22

------------------------------------------------------------------------

## Page 4

description='Correlate alerts → RCA → suggest action', ) \# --- Input
schema --- class Alert(BaseModel): id: str ts: str service: str metric:
str severity: str value: float threshold: float labels: Optional\[dict\]
= Field(default_factory=dict) class IncidentRequest(BaseModel): alerts:
list\[Alert\] \# --- Output schema --- class Cluster(BaseModel):
cluster_id: str alert_count: int services: list\[str\] time_range:
list\[str\] class RootCause(BaseModel): service: str confidence: float
reasoning: str class SimilarIncident(BaseModel): id: str similarity:
float summary: str class IncidentResponse(BaseModel): clusters:
list\[Cluster\] root_cause: RootCause recommended_actions: list\[str\]
similar_incidents: list\[SimilarIncident\] @app.get('/healthz') def
healthz() -\> dict: 6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa
Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
4/22

------------------------------------------------------------------------

## Page 5

Chạy: Test: 2.2 Pydantic validation --- không phải optional Nếu input
sai format (thiếu field), Pydantic tự trả 422 với detail rõ ràng. Không
cần code thêm.   return {'status': 'ok'} @app.post('/incident',
response_model=IncidentResponse) def post_incident(req: IncidentRequest)
-\> IncidentResponse: logger.info(f"Received {len(req.alerts)} alerts")
if not req.alerts: raise HTTPException(status_code=400, detail='Empty
alert list') \# ... pipeline calls go here ... return IncidentResponse(
clusters=\[\], root_cause=RootCause(service='unknown', confidence=0.0,
reasoning='stub recommended_actions=\[\], similar_incidents=\[\], )
uvicorn serve:app --host 0.0.0.0 --port 8000 --reload   curl
http://localhost:8000/healthz \# {"status":"ok"} curl -X POST
http://localhost:8000/incident\
-H "Content-Type: application/json"\
-d'{"alerts":\[{"id":"a-1","ts":"2026-06-12T09:42:01Z","service":"payment-svc
\$ curl -X POST http://localhost:8000/incident -d
'{"alerts":\[{"id":"a-1"}\]}' 6/9/26, 10:50 PM W2-D3: Model Serving ---
Đưa Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
5/22

------------------------------------------------------------------------

## Page 6

Đảm bảo endpoint không trả 500 khi input sai --- luôn return 400/422 với
message cụ thể. 3. Service graph as input --- không phải static asset
Pipeline correlation + RCA của bạn xài service graph như 1 input. Trong
notebook bạn load services.json tay --- nhưng trên production, graph là
data có lifecycle: được sinh ra từ đâu đó, có thể stale, có version,
scale lên-xuống. 3.1 4 source sinh service graph SOURCE CÁCH HOẠT ĐỘNG
MẠNH YẾU Distributed tracing (OpenTelemetry / Jaeger / Tempo) Span có
service.name + parent/child. Aggregate spans qua N phút → edge weight
Auto- discover, real- time, weight theo traffic thật Cần instrument app,
sampling rate ảnh hưởng accuracy Service mesh (Istio / Linkerd) Sidecar
proxy log mọi request L7 → metric istio_requests_total có src + dst
Không cần code thay đổi, 100% coverage traffic qua mesh Chỉ thấy L7,
miss raw TCP Manual / IaC Tay điền services.json , OpenAPI specs, k8s
NetworkPolicy Source-of- truth khi mới có 5-20 service Drift nhanh --- 1
tuần là sai \# 422 Unprocessable Entity \#
{"detail":\[{"loc":\["body","alerts",0,"ts"\],"msg":"field
required",...}\]} 6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa Pipeline
Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
6/22

------------------------------------------------------------------------

## Page 7

SOURCE CÁCH HOẠT ĐỘNG MẠNH YẾU Code analysis Static AST parse HTTP/gRPC
client init, hoặc eBPF capture syscall Bắt được rare path không có trong
traffic mẫu Tooling phức tạp, false positive cao Lab này dùng "Manual"
--- đơn giản nhất cho ≤ 20 service. Lên 100+ service phải chuyển sang
tracing/mesh. 3.2 Graph freshness --- silent failure khi stale Code load
services.json 1 lần lúc start. 1 tuần sau team deploy service mới ---
code không reload, topology correlation lệch. Triệu chứng: service mới
luôn đứng riêng cluster, không gom được dù đang trong cascade. On-call
thấy cluster size bất thường nhỏ → debug pipeline → mới phát hiện graph
stale. 2 cách giải: Reload mỗi N phút (đơn giản): worker thread reload
mỗi 5 phút. Latency tối đa 5 phút stale. Subscribe event (zero lag):
service registry phát event khi có thay đổi, code subscribe + reload
ngay. Phức tạp hơn. Cho production bài tập: chọn cách 1, document
trade-off. 3.3 Graph như 1 "model" --- version + rollback Mỗi version
graph cho output correlation khác nhau. Khi cluster ratio đột nhiên kém
--- có thể là code regress, có thể là graph mới gây regress. Cần biết
đang dùng graph nào. Endpoint /version nên trả graph_version +
graph_loaded_at + graph_source : 6/9/26, 10:50 PM W2-D3: Model Serving
--- Đưa Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
7/22

------------------------------------------------------------------------

## Page 8

Khi correlation regress, kiểm tra graph_version trước khi đổ tại code.
Rollback graph là 1 cách giải --- khác hoàn toàn rollback code. 3.4
Scale --- 9 service vs 1000 service Lab có 9 service. Production có
100-1000+. Một số phép tính scale: OPERATION COST Ở 9 SERVICE COST Ở
1000 SERVICE OK KHÔNG? PageRank trên reverse subgraph \< 1ms \~50ms OK,
vẫn dùng được All-pairs shortest path \< 1ms O(V³) ≈ 1s KHÔNG ổn, phải
cache hoặc index Subgraph extraction (filter alerting service) \< 1ms
\~10ms OK Community detection (Louvain) \< 1ms \~200ms OK nếu chạy
offline mỗi N phút Bottleneck thường thấy ở scale lớn: cardinality của
cluster_id label trong Prometheus metric --- nếu mỗi alert tạo
cluster_id unique, TSDB explode (xem GET /version { "app": "1.2.0",
"graph_version": "g-2026060801", "graph_loaded_at":
"2026-06-08T03:14:22Z", "graph_source": "otel-tempo",
"graph_node_count": 87, "graph_edge_count": 142 } 6/9/26, 10:50 PM
W2-D3: Model Serving --- Đưa Pipeline Lên Production \| My Learning
Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
8/22

------------------------------------------------------------------------

## Page 9

cardinality explosion). Solution: stable cluster_id (hash của
fingerprint set, không phải timestamp). 4. Chain 3 Layer Lại Glue layer
gọi correlate → rca → enrich: \# pipeline.py --- glue layer from
correlate import correlate, build_graph_from_json from rca import
run_rca import json from pathlib import Path \# Load once at module
level (cached) GRAPH = build_graph_from_json('dataset/services.json')
HISTORY =
json.loads(Path('dataset/incidents_history.json').read_text())\['incid
def process_batch(alerts: list\[dict\]) -\> dict: """Full pipeline. Trả
về dict matching IncidentResponse schema.""" \# L1: Correlate clusters =
correlate(alerts, GRAPH, gap_sec=120, max_hop=2) if not clusters: return
{'clusters': \[\], 'root_cause': {'service': 'unknown', 'confidenc
'reasoning': 'No clusters'}, 'recommended_actions': \[\], 'simila \#
Primary incident = cluster lớn nhất primary = max(clusters, key=lambda
c: c\['alert_count'\]) \# L2 + L3: RCA + LLM enrichment rca_result =
run_rca(primary, alerts, GRAPH, HISTORY) return { 'clusters': \[
{'cluster_id': c\['cluster_id'\], 'alert_count': c\['alert_count'\],
'services': c\['services'\], 'time_range': c\['time_range'\]} for c in
clusters\], 'root_cause': { 6/9/26, 10:50 PM W2-D3: Model Serving ---
Đưa Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
9/22

------------------------------------------------------------------------

## Page 10

Cập nhật endpoint: 5. Latency Budget 5.1 Đo trước, optimize sau Add
middleware đo latency mỗi request:     'service':
rca_result\['root_cause'\], 'confidence': rca_result\['confidence'\],
'reasoning': rca_result.get('reasoning', ''), }, 'recommended_actions':
rca_result.get('actions', \[\]), 'similar_incidents': \[ {'id': inc_id,
'similarity': 0.7, 'summary': '...'} for inc_id in
rca_result.get('similar_incidents', \[\])\[:3\]\], } from pipeline
import process_batch @app.post('/incident',
response_model=IncidentResponse) def post_incident(req: IncidentRequest)
-\> IncidentResponse: alerts_dict = \[a.model_dump() for a in
req.alerts\] try: result = process_batch(alerts_dict) except Exception
as e: logger.error(f"Pipeline failed: {e}", exc_info=True) raise
HTTPException(status_code=500, detail=f'Pipeline error: {e}') return
IncidentResponse(\*\*result) import time from fastapi import Request
@app.middleware('http') 6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa
Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
10/22

------------------------------------------------------------------------

## Page 11

LLM call dominate total latency. Tối ưu 100ms ở các layer khác = 1%
improvement. Tối ưu LLM = lớn. 5.2 Optimization cho LLM call Cache ---
same prompt → cached response: Async + concurrent --- gọi LLM cho 3
cluster song song:     async def add_timing(request: Request,
call_next): start = time.perf_counter() response = await
call_next(request) duration_ms = (time.perf_counter() - start) \* 1000
response.headers\['X-Response-Time-Ms'\] = f'{duration_ms:.1f}'
logger.info(f"{request.method} {request.url.path} {response.status_code}
{d return response import hashlib from cachetools import TTLCache
\_llm_cache = TTLCache(maxsize=1000, ttl=3600) def
cached_llm_call(prompt: str) -\> dict: key =
hashlib.sha256(prompt.encode()).hexdigest() if key not in \_llm_cache:
\_llm_cache\[key\] = \_do_llm_call(prompt) return \_llm_cache\[key\]
import asyncio async def llm_call_async(prompt: str) -\> dict: ...
@app.post('/incident') async def post_incident(req: IncidentRequest):
tasks = \[llm_call_async(p) for p in prompts\] results = await
asyncio.gather(\*tasks) 6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa
Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
11/22

------------------------------------------------------------------------

## Page 12

Smaller model --- gpt-4o-mini thay gpt-4o. 5× rẻ + 2× nhanh. Skip LLM
--- nếu graph RCA confidence ≥ 0.9, không cần LLM enrichment. 5.3
Timeout Set timeout cho mọi outbound call: Không timeout → 1 LLM call
hang → endpoint hang forever → user mất kết nối, request stuck. 6.
Concurrency 6.1 1 worker vs N worker Default uvicorn serve:app chạy 1
worker = 1 process xử lý request tuần tự (sync code) hoặc concurrent với
event loop (async code). Scale cho production: 4 worker → request
load-balance round-robin. Trade-off: nhiều worker = nhiều memory (mỗi
process duplicate state). 6.2 Race condition với shared state from
openai import OpenAI client = OpenAI(timeout=10.0, max_retries=2)
uvicorn serve:app --host 0.0.0.0 --port 8000 --workers 4 6/9/26, 10:50
PM W2-D3: Model Serving --- Đưa Pipeline Lên Production \| My Learning
Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
12/22

------------------------------------------------------------------------

## Page 13

Nếu pipeline có in-memory cache + nhiều worker → mỗi worker có copy
riêng → cache không cross-worker. Solution: stateless --- mỗi request
load state từ Redis / DB. Hoặc giữ stateless trong bài tập (chấp nhận
limitation, document trong reflection). Bài tập này chấp nhận stateless
/ single-worker. Quan trọng là biết trade-off, không phải implement
perfect. 6.3 Concurrent test Theo dõi p50 / p99 + error rate. Nếu p99 \>
30s hoặc error \> 1% → cần fix. 7. Health Check + Readiness /healthz từ
section 2 đủ cho liveness. Cho readiness (rolling deploy), thêm:   \#
Apache Bench: 100 request, 10 concurrent ab -n 100 -c 10 -p alerts.json
-T application/json http://localhost:8000/incide \# wrk: 10 thread, 30s
wrk -t10 -c50 -d30s --script post.lua http://localhost:8000/incident
@app.get('/readyz') def readyz() -\> dict: """Check downstream
dependencies. Trả 503 nếu chưa ready.""" checks = { 'graph':
GRAPH.number_of_nodes() \> 0, 'history': len(HISTORY) \> 0, } \# LLM
check (optional --- readiness không nên depend external service) try:
from openai import OpenAI 6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa
Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
13/22

------------------------------------------------------------------------

## Page 14

8.  Versioning + Rollback 8.1 Version trong response Cần endpoint trả
    lời "version code đang deploy" --- quan trọng khi rollback/debug.
    8.2 Feature flag cho LLM OpenAI(timeout=2.0).models.list()
    checks\['llm'\] = True except Exception: checks\['llm'\] = False if
    not all(checks.values()): raise HTTPException(status_code=503,
    detail=checks) return {'status': 'ready', 'checks': checks}
    APP_VERSION = '1.0.0' @app.get('/version') def version() -\> dict:
    return { 'app': APP_VERSION, 'pipeline_config': {
    'correlate_gap_sec': 120, 'correlate_max_hop': 2, 'rca_method':
    'graph+llm', 'llm_model': 'gpt-4o-mini', }, } import os USE_LLM =
    os.environ.get('AIOPS_USE_LLM', 'true').lower() == 'true' def
    run_rca_with_flag(cluster, alerts, graph, history): 6/9/26, 10:50 PM
    W2-D3: Model Serving --- Đưa Pipeline Lên Production \| My Learning
    Notes
    https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
    14/22

------------------------------------------------------------------------

## Page 15

Khi LLM provider outage, set AIOPS_USE_LLM=false + restart → endpoint
vẫn chạy với graph only. 8.3 Shadow deployment Production AIOps: deploy
v2 cùng v1, nhưng v2 không serve traffic --- nó shadow v1 (nhận cùng
input, log output). So sánh v2 output với v1 output trên data thật. Nếu
OK trong 1 tuần → promote v2. Không implement trong bài tập (out of
scope). Đề cập để biết direction khi scale lên production thật. 9.
Self-Monitoring Pipeline AIOps monitor production --- nhưng bản thân nó
cũng cần monitor. 9.1 Metric Prometheus     if not USE_LLM:
candidates = rca_combined(cluster, alerts, graph) return {'root_cause':
candidates\[0\]\[0\], 'confidence': candidates\[0\]\[1\] 'method':
'graph-only-flag-off'} return run_rca(cluster, alerts, graph, history)
from prometheus_client import Counter, Histogram, make_asgi_app
REQUEST_COUNT = Counter('aiops_incident_requests_total', 'Total
requests', \['st REQUEST_LATENCY =
Histogram('aiops_incident_latency_seconds', 'Pipeline latency
LLM_FAILURES = Counter('aiops_llm_failures_total', 'LLM failures',
\['reason'\]) CLUSTER_COUNT = Histogram('aiops_clusters_per_request',
'Clusters per request') app.mount('/metrics', make_asgi_app())
@app.post('/incident') async def post_incident(req: IncidentRequest):
6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa Pipeline Lên Production \|
My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
15/22

------------------------------------------------------------------------

## Page 16

9.2 Key SLO cho AIOps pipeline SLO TARGET VÌ SAO Availability 99.5%
Pipeline down → không có incident triage p99 latency \< 10s SRE chờ \<
10s LLM failure rate \< 1% Cao hơn → nghi vấn provider issue Root-cause
precision (offline) \> 70% top-3 Thấp hơn → pipeline tạo noise thay vì
help 9.3 Logging Structured log (JSON), không print:     with
REQUEST_LATENCY.time(): try: result = process_batch(\[a.model_dump() for
a in req.alerts\]) REQUEST_COUNT.labels(status='success').inc()
CLUSTER_COUNT.observe(len(result\['clusters'\])) return
IncidentResponse(\*\*result) except Exception:
REQUEST_COUNT.labels(status='error').inc() raise import logging, json
class JsonFormatter(logging.Formatter): def format(self, record): obj =
{'ts': self.formatTime(record), 'level': record.levelname, 'msg':
record.getMessage(), 'logger': record.name} if hasattr(record, 'extra'):
obj.update(record.extra) return json.dumps(obj) handler =
logging.StreamHandler() handler.setFormatter(JsonFormatter()) 6/9/26,
10:50 PM W2-D3: Model Serving --- Đưa Pipeline Lên Production \| My
Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
16/22

------------------------------------------------------------------------

## Page 17

JSON log dễ ship vào ELK / Loki, query bằng cluster_count \> 5 thay vì
grep text. 10. Testing (optional, nâng điểm) 10.1 Unit test --- pure
function 10.2 Integration test --- endpoint logger =
logging.getLogger('aiops') logger.addHandler(handler)
logger.setLevel(logging.INFO) logger.info('Processed incident',
extra={'extra': { 'cluster_count': 3, 'root_cause': 'payment-svc',
'confidence': 0.84, }}) \# tests/test_correlate.py from correlate import
fingerprint, session_groups def test_fingerprint_excludes_timestamp(): a
= {'service': 'payment-svc', 'metric': 'latency', 'severity': 'crit',
'ts': '2026-06-12T09:42:01Z', 'value': 1840} b = {'service':
'payment-svc', 'metric': 'latency', 'severity': 'crit', 'ts':
'2026-06-12T09:42:30Z', 'value': 1900} assert fingerprint(a) ==
fingerprint(b) \# tests/test_serve.py from fastapi.testclient import
TestClient from serve import app client = TestClient(app) def
test_healthz(): r = client.get('/healthz') assert r.status_code == 200
6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa Pipeline Lên Production \|
My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
17/22

------------------------------------------------------------------------

## Page 18

10.3 Mock LLM trong test Mock LLM trong test OK. Mock LLM trong endpoint
production = không acceptable. 11. Deploy Local --- Make it run 11.1
requirements.txt     assert r.json() == {'status': 'ok'} def
test_incident_empty_alerts(): r = client.post('/incident',
json={'alerts': \[\]}) assert r.status_code == 400 def
test_incident_happy_path(): payload = {'alerts': \[ {'id': 'a-1', 'ts':
'2026-06-12T09:42:01Z', 'service': 'payment-svc', 'metric':
'latency_p99_ms', 'severity': 'crit', 'value': 1840, 'thresh\]} r =
client.post('/incident', json=payload) assert r.status_code == 200
assert 'clusters' in r.json() from unittest.mock import patch
@patch('pipeline.call_llm_rca') def
test_pipeline_with_mock_llm(mock_llm): mock_llm.return_value = {
'root_cause': 'payment-svc', 'class': 'connection_pool_exhaustion',
'confidence': 0.84, 'actions': \['Rollback'\], 'reasoning': 'mocked',
'similar_incidents': \['INC-2025-11-08'\], } \# ... test pipeline ...
6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa Pipeline Lên Production \|
My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
18/22

------------------------------------------------------------------------

## Page 19

11.2 Makefile (nâng điểm "project running") 11.3 Dockerfile (bonus)
fastapi\>=0.110 uvicorn\[standard\]\>=0.27 pydantic\>=2.5 networkx\>=3.2
pandas\>=2.0 openai\>=1.10 prometheus-client\>=0.19 pytest\>=7.4  
.PHONY: install run test clean install: uv pip install -r
requirements.txt run: uvicorn serve:app --host 0.0.0.0 --port 8000
--reload run-prod: uvicorn serve:app --host 0.0.0.0 --port 8000
--workers 4 --no-access-lo test: pytest -v tests/ clean: find . -name
"**pycache**" -exec rm -rf {} + rm -f rca_llm_trace.log FROM
python:3.11-slim WORKDIR /app 6/9/26, 10:50 PM W2-D3: Model Serving ---
Đưa Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
19/22

------------------------------------------------------------------------

## Page 20

Không bắt buộc. Có Dockerfile + chạy được → notable. 12. Bài tập ---
Code serve.py + DESIGN.md Quy ước nộp bài: Branch main Path:
aiops-`<tên>`{=html}/w2/d3/ (lowercase w2 + d3 ) File: serve.py +
DESIGN.md (HOA) + SUBMIT.md (HOA) Tên file đúng như trên --- KHÔNG phải
app.py , architecture.md , hay adr_001.md . Sai = 1 điểm. 12.1 Steps 1.
Tạo aiops-`<tên>`{=html}/w2/d3/ 2. Build skeleton serve.py theo §2-3 3.
Add /healthz + /readyz endpoint (§6) 4. Add latency middleware (§4.1) 5.
Wire với correlate + RCA (giả lập tạm --- không cần chính xác) 6. Write
DESIGN.md ≥ 100 từ: Pipeline architecture trong endpoint Latency budget
breakdown 1 production concern (concurrency hoặc fault tolerance) ---
handle thế nào     COPY requirements.txt . RUN pip install
--no-cache-dir -r requirements.txt COPY . . EXPOSE 8000 CMD \["uvicorn",
"serve:app", "--host", "0.0.0.0", "--port", "8000", "--workers 6/9/26,
10:50 PM W2-D3: Model Serving --- Đưa Pipeline Lên Production \| My
Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
20/22

------------------------------------------------------------------------

## Page 21

Trade-off: vì sao chọn FastAPI thay vì Flask/BentoML 7. Write SUBMIT.md
với reflection 12.2 Acceptance serve.py chạy với uvicorn serve:app
--port 8000 curl /healthz trả {"status":"ok"} curl POST /incident với
valid input trả 200, body có clusters , root_cause , recommended_actions
Invalid input → 422, không 500 DESIGN.md ≥ 100 từ, có concrete decision
(vd: "chọn gap_sec=120s vì...") 13. EOD Checkpoint Trong SUBMIT.md : 1.
Latency budget của endpoint bạn (p99)? Phase nào chiếm thời gian nhất?
2. Endpoint xử lý 5 alert vs 500 alert --- latency khác nhau thế nào?
Linear scale hay có fixed cost? 3. LLM provider down giữa lúc đang chạy.
Hệ thống behave ra sao? Phương án dự phòng? 4. /healthz và /readyz khác
nhau gì? Khi nào dùng cái nào? 5. POST 4 request đồng thời. Endpoint
handle ổn không? Bottleneck đầu tiên? Câu 3 + 5 là production maturity
--- viết kỹ. 14. Tài liệu tham khảo FastAPI:
https://fastapi.tiangolo.com/tutorial/ --- 5 phần đầu đủ cho bài tập
6/9/26, 10:50 PM W2-D3: Model Serving --- Đưa Pipeline Lên Production \|
My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
21/22

------------------------------------------------------------------------

## Page 22

© 2026 My Learning Notes · Powered by Hugo & PaperMod Pydantic v2:
https://docs.pydantic.dev/latest/concepts/models/ "Patterns of
Distributed Systems" --- Unmesh Joshi (Manning). Chapter Health Check +
Heartbeat Prometheus client Python:
https://github.com/prometheus/client_python uvicorn deployment:
https://www.uvicorn.org/deployment/ 6/9/26, 10:50 PM W2-D3: Model
Serving --- Đưa Pipeline Lên Production \| My Learning Notes
https://khanhnn00.github.io/learning-notes/xbrain/aiops-w2/w2-d3-model-serving/
22/22
