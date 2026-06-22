# RCA Report — Incident Forensics: 30-Day Sweep (2026-03-01 → 2026-03-30)

## Identified Incidents

| Incident ID | Window (UTC) | One-line summary |
|---|---|---|
| I-1 | 2026-03-05 14:30 → 15:05 | fx-api 503 burst triggers payment-svc retry storm, saturating outbound HTTP connection pool |
| I-2 | 2026-03-11 02:00 → 04:30 | inventory-svc memory leak in response cache (no TTL on large SKU entries) caused by nightly sync, ending in OOM kills |
| I-3 | 2026-03-17 11:15 → 12:00 | Feature flag `enable_loyalty_recommendations` activates unindexed query in payment-svc, draining RDS connection pool |
| I-4 | 2026-03-22 09:00 → 09:45 | DNS rotation: AZ-c resolver serves stale IPs for `pp-api`; AZ-c payment pods get `connection refused` on decommissioned IP block |
| I-5 | 2026-03-27 06:00 → 06:15 | Service mesh cert auto-rotation issues mTLS cert with `not_before` ~27s ahead of validator clock; checkout→payment handshakes fail until clock convergence |

---

## I-1 — fx-api Retry Storm (2026-03-05 14:30 → 15:05)

### Timeline

| UTC time | Event | Source |
|---|---|---|
| 13:58 | inventory-svc deployed v2.4.1 (`warehouse_timeout_ms 5000→3000`) | `deploy_log.json` |
| 14:30:00 | `fx_api_5xx_per_min` jumps from 0 to ~50 | `metrics/fx_api_5xx_per_min.csv` |
| 14:30:00 | First payment-svc WARN `upstream_call status=503 endpoint=fx-api` | `logs/payment-svc.jsonl` |
| 14:30:30 | First ERROR `fx_call_failed_after_retries` in payment-svc | `logs/payment-svc.jsonl` |
| 14:31:15 | inventory-svc single WARN `network_timeout` (stale keepalive) | `logs/inventory-svc.jsonl` |
| 14:32 | `payment_conn_pool_active` crosses 100 (max 200) | `metrics/payment_conn_pool_active.csv` |
| 14:32 | First `conn_pool_acquire_timeout` ERROR in payment-svc | `logs/payment-svc.jsonl` |
| 14:32:15 | Alert `A-001 CheckoutP99High` fires | `alerts.json` |
| 14:33–14:39 | Cascade of alerts: `A-002` through `A-008` | `alerts.json` |
| 14:34 | `rds_cpu_pct` rises from 25% to 88% | `metrics/rds_cpu_pct.csv` |
| 14:35 | `redis_hit_rate` drops from 0.95 to 0.40 | `metrics/redis_hit_rate.csv` |
| 15:00:30 | `upstream_recovered endpoint=fx-api` logged | `logs/payment-svc.jsonl` |
| 15:05 | All metrics return to baseline | metrics CSVs |

### Candidate Hypotheses

| # | Hypothesis | Confidence | Reasoning |
|---|---|---|---|
| H1 | RDS-orders overload (CPU 88%) is the root cause | 3 | CPU spike correlates with checkout slowness |
| H2 | inventory-svc deploy 32 min prior introduced a regression | 2 | Closest deploy to incident window |
| H3 | payment-svc memory leak (heap climbed 60→92%) | 3 | Memory growth correlates with errors |
| H4 | Redis cache failure causing checkout degradation | 2 | Hit rate drops 95→40% |
| H5 | fx-api upstream failure with no circuit breaker | 4 | `fx_api_5xx_per_min` jumps 2 min before any alert fires |

### Evidence Review

> **Hypothesis H1 (RDS CPU overload)**: Dismissed
> **Evidence**: `rds_cpu_pct.csv` shows CPU rising starting at 14:34, **after** checkout p99 began climbing at 14:30 and **after** payment-svc was already emitting `conn_pool_acquire_timeout` errors. `rds-orders.jsonl` slow_query lines reference `table=audit_log, op=INSERT, source_service=payment-svc`. `rds_audit_writes_per_min.csv` jumps from ~30 to ~3000/min in lockstep with `payment_retries_per_min.csv`. CPU is an **effect** of retry-driven INSERT amplification, not the cause.

> **Hypothesis H2 (inventory-svc deploy)**: Dismissed
> **Evidence**: `deploy_log.json` shows the only change was `warehouse_timeout_ms 5000→3000`, affecting only inventory-svc → warehouse-api. `inventory_warehouse_health.csv` stays at 1 throughout. `inventory_p99_ms.csv` is flat near 40 ms. inventory-svc is not on the payment path in `topology.json`.

> **Hypothesis H3 (payment-svc memory leak)**: Dismissed
> **Evidence**: `payment_memory_pct.csv` and `payment_conn_pool_active.csv` rise and fall together. After fx-api recovery at 15:00:30, memory drops back to 60% within 5 minutes. A real leak does not self-recover; this is held buffers from slow upstream calls.

> **Hypothesis H4 (Redis failure)**: Dismissed
> **Evidence**: `redis_evictions_per_min.csv` is flat near 5/min throughout (no eviction storm). The hit-rate drop **lags** the checkout p99 rise by 3 minutes; if Redis caused the cascade it would lead, not lag. Redis was healthy; callers were stuck elsewhere.

> **Hypothesis H5 (fx-api upstream failure)**: Accepted
> **Evidence**: `fx_api_5xx_per_min.csv` jumps from 0 → ~50 at 14:30:00, **before any internal metric moves**. `payment-svc.jsonl` shows `fx_client upstream_call status=503` lines with `retry_attempt=1,2,3` from 14:30, ending in `fx_call_failed_after_retries`. Traces (`6a870411de257668` through `c873ad5563f242bd`) show 3-attempt fx_client spans, all returning 503. The retry burst directly explains conn-pool saturation, audit_log INSERT amplification, held memory, and Redis queue backlog.

### Root Cause

Third-party `fx-api` began returning HTTP 503 at a sustained rate (~50/min) starting at 14:30:00 UTC. payment-svc calls fx-api on the synchronous payment path with a 3-attempt retry policy and **no circuit breaker**. The retries amplified each user-initiated charge into 3 outbound HTTP calls, each holding a connection for up to ~2.5 s. The 200-connection outbound pool saturated within ~2 minutes; subsequent requests failed with `conn_pool_acquire_timeout`. checkout-svc then observed sustained 5xx from payment-svc.

```
fx-api 503 burst (14:30)
  └─> payment-svc.fx_client retries 3x per call
        ├─> outbound conn pool saturates (14:32)
        │     └─> payment-svc 5xx → checkout-svc errors
        │           └─> circuit_breaker_open → 5xx to frontend
        ├─> audit_log INSERT amplification → rds-orders CPU spike (effect)
        └─> held in-flight buffers → memory rise + Redis queue backlog (effects)
```

### Counterfactual

- **Scaling RDS-orders**: no effect; retries still amplify, pool still saturates. RDS CPU was a downstream effect.
- **Rolling back inventory-svc deploy**: no effect; inventory is not on the payment path in the topology.
- **Restarting payment-svc**: symptoms return within ~2 min as retries replay against still-failing fx-api.
- **Restarting Redis**: no effect; Redis was healthy and responding normally.

### Prevention

1. **payment-svc**: Add a circuit breaker on the fx-api dependency. Open when fx-api 5xx rate over a 30-second window exceeds 5%; half-open after 30 s; close after 5 consecutive 2xx.
2. **payment-svc**: Cap fx_client retry budget at 2 attempts (down from 3) and enforce a per-call 500 ms timeout. This reduces worst-case connection hold from ~7.5 s to ~1 s per request.

---

## I-2 — inventory-svc Memory Leak via Nightly Sync (2026-03-11 02:00 → 04:30)

### Timeline

| UTC time | Event | Source |
|---|---|---|
| 03-10 02:00 | inventory-svc v2.5.0 deployed (numpy 1.26 → 2.0.0) | `deploy_log.json` (24 h prior) |
| 02:00:00 | inventory-svc INFO `nightly_sku_sync started expected_sku_count=250000` | `logs/inventory-svc.jsonl` |
| 02:02 | First `cache_entry_added size_bytes=5242880 ttl_seconds=null` | `logs/inventory-svc.jsonl` |
| 02:15 | Marketing toggles `homepage_promo_carousel` to enabled | `deploy_log.json` |
| 02:30 | `inventory_memory_mb` crosses 1000 MB (baseline 200 MB) | `metrics/inventory_memory_mb.csv` |
| 02:45 | Alert `A-101 InventoryP99High` fires | `alerts.json` |
| 03:00 | Alert `A-102 InventoryMemoryHigh` (>2000 MB) | `alerts.json` |
| 03:00 | `frontend_req_rate` shows +220 RPS bump (normal diurnal traffic) | `metrics/frontend_req_rate.csv` |
| 03:20 | Alert `A-103 CheckoutErrorRateHigh` | `alerts.json` |
| 03:30 | payment-svc emits `gc_pressure_high` with `host_neighbor_inventory_mem_mb` ≈ 3500+ | `logs/payment-svc.jsonl` |
| 04:15–04:25 | Two `OOM_killed` ERROR lines in inventory-svc | `logs/inventory-svc.jsonl` |
| 04:18 | Alert `A-104 InventoryOOMKilled` | `alerts.json` |
| 04:30 | inventory-svc auto-scaled +2 replicas; metrics return to baseline | metrics CSVs |

### Candidate Hypotheses

| # | Hypothesis | Confidence | Reasoning |
|---|---|---|---|
| H1 | numpy 2.0 upgrade introduced a memory leak | 3 | Major version bump deployed 24 h before incident |
| H2 | Traffic spike at 03:00 overloaded inventory-svc | 2 | RPS +220 coincides with degradation |
| H3 | payment-svc GC pauses cascading slowness | 2 | `gc_pressure_high` lines in payment-svc |
| H4 | Marketing feature flag change broke something | 2 | Config change occurred within incident window |
| H5 | Memory leak in inventory-svc response cache (no TTL) | 4 | `cache_entry_added size_bytes=5242880 ttl_seconds=null` repeating; `current_cache_bytes` grows monotonically |

### Evidence Review

> **Hypothesis H1 (numpy upgrade)**: Dismissed
> **Evidence**: The leak signature is in `response_cache`, not in any numpy code path. inventory-svc ran for 24 hours on the new numpy version with a normal memory profile before the nightly_sku_sync kicked off. The first abnormal log line is `cache_entry_added` at 02:02, not anything from a numpy module. If numpy were the cause, the leak would have manifested at deploy time.

> **Hypothesis H2 (traffic spike)**: Dismissed
> **Evidence**: `inventory_memory_mb` had already crossed 1000 MB by 02:30, 30 minutes before the RPS bump at 03:00. The traffic spike is a normal diurnal pattern visible across multiple days in `frontend_req_rate.csv`, not exceptional load.

> **Hypothesis H3 (payment-svc GC)**: Dismissed
> **Evidence**: payment-svc's `gc_pressure_high` lines explicitly include `host_neighbor_inventory_mem_mb` showing the inventory-svc container on the same host is consuming 3.5–4 GB. payment-svc is a noisy-neighbor victim of inventory-svc's leak; not the cause.

> **Hypothesis H4 (marketing flag)**: Dismissed
> **Evidence**: The flag `homepage_promo_carousel` is on `frontend`, not inventory-svc. inventory-svc memory was already at ~1000 MB at the time of the flip (02:15). The flag does not change the inventory call pattern as confirmed by `topology.json`.

> **Hypothesis H5 (cache memory leak)**: Accepted
> **Evidence**: `inventory-svc.jsonl` `cache_entry_added` lines show `size_bytes=5242880` (~5 MB per entry) and `ttl_seconds=null`. Each tick of the `nightly_sku_sync` job loads more SKUs into the cache; `current_cache_bytes` grows linearly with entry count. By 04:15 the cache holds >3 GB, triggering OOM. After OOM-kill and pod restart, the same code path re-runs and dies again. This is a real memory leak (no eviction policy on large entries), not a buffer-held-by-slow-upstream artifact.

### Root Cause

inventory-svc maintains an in-process response cache for `warehouse-api` lookups. Cache entries for "large-catalog" SKUs (~5% of the catalog) are ~5 MB each. The cache has **no TTL eviction** on these entries (`ttl_seconds=null`). The nightly sync job at 02:00 walks the full 250,000-SKU catalog and warms the cache with every SKU; the large entries accumulate, monotonically growing the process heap until OOM at 04:15.

```
nightly_sku_sync starts (02:00)
  └─> response_cache.add(sku, 5MB, ttl=null) × N large SKUs
        └─> heap grows linearly
              ├─> request p99 climbs (GC pressure)
              ├─> payment-svc on same host suffers noisy-neighbor GC pauses
              └─> OOM kill at heap > available
                    └─> pod restart, cache warm-up repeats
                          └─> repeated OOM until auto-scale adds capacity
```

### Counterfactual

- **Rolling back numpy upgrade**: no effect; the numpy code path is unrelated to the response cache leak.
- **Scaling down traffic at 03:00**: no effect; the leak is driven by the sync job, not request volume.
- **Restarting payment-svc to "fix" GC**: no effect; payment is a noisy-neighbor victim, not the cause.
- **Reverting marketing flag**: no effect; flag is on `frontend`, different service entirely.

### Prevention

1. **inventory-svc**: Set a default `ttl_seconds` of 3600 on `response_cache` entries; enforce a per-entry size cap (e.g., reject `size_bytes` > 1 MB and store an indirection to S3 instead).
2. **inventory-svc**: Add an alert on `inventory_memory_mb` growth rate (> 10 MB/min sustained for 5 min) — would fire at ~02:10, well before the first visible symptom.

---

## I-3 — Feature Flag → Unindexed Query (2026-03-17 11:15 → 12:00)

### Timeline

| UTC time | Event | Source |
|---|---|---|
| 03-16 11:15 | RDS-orders instance class upgrade (db.r6g.large → xlarge) | `deploy_log.json` (24 h prior) |
| 11:15:00 | Feature flag `enable_loyalty_recommendations` set to enabled (100% rollout) | `deploy_log.json` |
| 11:15:02 | payment-svc INFO `feature_flag_observed flag=enable_loyalty_recommendations value=true` | `logs/payment-svc.jsonl` |
| 11:15:15 | First payment-svc WARN `loyalty_client slow_query duration_ms=3500 table=transactions` | `logs/payment-svc.jsonl` |
| 11:15–12:00 | rds-orders WARN `slow_query table=transactions uses_index=false rows_examined=80k–220k` | `logs/rds-orders.jsonl` |
| 11:18 | Alert `A-201 CheckoutP99High` | `alerts.json` |
| 11:19 | Alert `A-202 RdsQueryP99High` | `alerts.json` |
| 11:21 | Alert `A-204 RdsCpuHigh` (65%) | `alerts.json` |
| 11:25 | Alert `A-203 PaymentConnPoolSaturated` (pool max 50) | `alerts.json` |
| 12:00 | Signals decay; flag presumably reverted or rate-limited | metrics CSVs |

### Candidate Hypotheses

| # | Hypothesis | Confidence | Reasoning |
|---|---|---|---|
| H1 | RDS instance class change 24 h ago is unstable | 3 | Recent infrastructure event |
| H2 | fx-api retry storm again, similar to I-1 | 3 | Same `PaymentConnPoolSaturated` alert name as I-1 |
| H3 | Network latency issue between services | 2 | Generic latency hypothesis |
| H4 | Feature flag flip introduced a slow unindexed query | 5 | Deploy log explicit at 11:15; `loyalty_client` is a new component appearing in payment-svc logs from that timestamp |

### Evidence Review

> **Hypothesis H1 (RDS upgrade)**: Dismissed
> **Evidence**: The upgrade happened 24 h before with no impact. RDS query p99 had been ~18 ms for the entire 24 h until 11:15. If the class change were the trigger, symptoms would have appeared on Day 16 (deploy day) or under any earlier traffic burst.

> **Hypothesis H2 (fx-api retry storm)**: Dismissed
> **Evidence**: `fx_api_5xx_per_min.csv` is flat at 0 throughout this window. There are no `upstream_call status=503` lines in `payment-svc.jsonl` on 2026-03-17. `payment_retries_per_min` stays at baseline (~2/min). The PoolSaturated alert has the same name as I-1, but the underlying mechanism is different — slow queries holding connections, not retries.

> **Hypothesis H3 (network latency)**: Dismissed
> **Evidence**: No service emits network-layer errors. Traces complete successfully on the network layer; they only show DB-layer slowness inside payment-svc. `checkout-svc.jsonl` shows `downstream_timeout target=payment-svc`, confirming checkout is propagating payment-svc's slowness, not introducing it.

> **Hypothesis H4 (feature flag → slow query)**: Accepted
> **Evidence**: The flag flip is logged in `deploy_log.json` at 11:15:00; payment-svc emits `feature_flag_observed` at 11:15:02 confirming receipt; the first `loyalty_client slow_query` line is at 11:15:15. The `loyalty_client` component does not appear anywhere else in payment-svc logs in the 30-day window. `rds-orders.jsonl` slow_query lines include `uses_index: false` and `where: user_id = ?`. Traces (`97ec012decd77be1` through `93e08247f9509e76`) show `loyalty_client.recommend` spans with `rows_examined=180000, uses_index=false`. The query was tested in staging on 10k users (per deploy_log note); in production some users have >100k transaction rows, blowing the query plan.

### Root Cause

At 11:15:00, the `enable_loyalty_recommendations` flag was flipped to 100% rollout. payment-svc's `loyalty_client` is invoked on every charge and issues `SELECT * FROM transactions WHERE user_id = ? ORDER BY ts DESC`. There is no index on `(user_id, ts)`, so the planner does a full sort of all rows for that user. For users with >100k transactions (~1% of users), the query takes 3–4 s and holds a connection from payment-svc's RDS pool (max 50). Under concurrent traffic, ~30 such queries run in parallel, draining the pool. Other charges queue, p99 climbs, errors rise.

```
flag flip (11:15:00)
  └─> payment-svc.loyalty_client.recommend(user_id) on every charge
        └─> SELECT * FROM transactions WHERE user_id=? ORDER BY ts DESC  (no index)
              ├─> rds-orders runs full sort: 3–4 s, CPU climbs to 75%
              └─> connection held → payment-svc RDS pool drains
                    └─> charges queue → checkout sees downstream_timeout
                          └─> 5xx visible at frontend
```

### Counterfactual

- **Reverting RDS upgrade**: no effect; the upgrade is unrelated. The unindexed query would still hold connections on the older instance class.
- **Adding circuit breaker on fx-api** (the I-1 prevention): no effect; fx-api isn't involved in this incident.
- **Restarting checkout-svc**: no effect; checkout is propagating payment-svc's slowness, not causing it.
- **Scaling out payment-svc**: temporary relief at best; each replica's pool still drains under the same unindexed query load.

### Prevention

1. **payment-svc**: Gate every new feature flag rollout behind a staging load test against a production-like dataset (≥ 1M users with realistic distribution of transaction counts) before enabling at 100%.
2. **rds-orders**: Add a composite index `(user_id, ts DESC)` on the `transactions` table, and add a query-pattern alert on `slow_query uses_index=false` (>10/min for 1 min) — would fire within 30 seconds of the flag flip.

---

## I-4 — DNS AZ-c Split for pp-api (2026-03-22 09:00 → 09:45)

### Timeline

| UTC time | Event | Source |
|---|---|---|
| 03-15 09:00 | Vendor announcement: `pp-api` IP block rotating 203.0.113.0/24 → 198.51.100.0/24, TTL 3600, old block removed 03-22 09:00 | `deploy_log.json` (7 days prior) |
| 03-17 09:00 | TLS cert rotated for pp-api client mTLS | `deploy_log.json` (5 days prior) |
| 03-20 09:00 | Security group sg-pay-egress tightened (removed legacy 0.0.0.0/0:443) | `deploy_log.json` (48 h prior) |
| 09:00:00 | First payment-svc ERROR `connection_refused endpoint=pp-api az=c resolved_ip=203.0.113.10` | `logs/payment-svc.jsonl` |
| 09:00:00 | payment-svc INFO `charge_ok az=a resolved_ip=198.51.100.20` | `logs/payment-svc.jsonl` |
| 09:01 | `payment_az_c_error_rate` jumps 0.005 → 0.30 | `metrics/payment_az_c_error_rate.csv` |
| 09:02 | Alert `A-301 CheckoutErrorRateHigh` (3%) | `alerts.json` |
| 09:03 | Alert `A-302 PaymentRegionalErrorRateHigh` (>20%, az=c) | `alerts.json` |
| 09:05 | Alert `A-303 PaymentRetriesElevated` | `alerts.json` |
| 09:45 | `az_c_resolver_refreshed` INFO line; traffic returns to baseline | `logs/payment-svc.jsonl` |

### Candidate Hypotheses

| # | Hypothesis | Confidence | Reasoning |
|---|---|---|---|
| H1 | Firewall change 48 h ago blocked outbound to pp-api | 3 | Recent security group edit is suspicious |
| H2 | TLS cert rotation 5 days ago is failing | 2 | Recent cert event on pp-api path |
| H3 | Network partition between AZ-c and ingress | 2 | One AZ failing fits a partition model |
| H4 | DNS rotation: AZ-c resolver did not refresh the cached IP block in time | 5 | `resolved_ip` field partitions success/failure; `az` label correlates cleanly |

### Evidence Review

> **Hypothesis H1 (firewall)**: Dismissed
> **Evidence**: The security group change at 03-20 09:00 was 48 h before the incident. Outbound traffic to pp-api worked normally for those 48 h. The removed rule was `0.0.0.0/0:443` — a redundant blanket allow. The error is `connection_refused`, not `connection_timed_out` — refused implies the connection reached the destination IP and was rejected, which a firewall block would not produce.

> **Hypothesis H2 (TLS cert)**: Dismissed
> **Evidence**: There are no TLS errors in the logs. `mtls_handshake_failures_per_min.csv` shows 0 handshake failures in this window. The errors are `connection_refused`, a TCP-layer event well before TLS. A cert issue would manifest at handshake.

> **Hypothesis H3 (network partition)**: Dismissed
> **Evidence**: AZ-c payment-svc can still reach checkout-svc, rds-orders, fx-api, and other internal services in the same window — `payment-svc.jsonl` shows successful `audit_log INSERT` lines on AZ-c pods during the incident. The failure is specific to one external destination (`pp-api`). A real partition would affect multiple destinations.

> **Hypothesis H4 (DNS rotation)**: Accepted
> **Evidence**: The `extra.resolved_ip` field in payment-svc logs sharply partitions the population:
> - All `connection_refused` events resolve to `203.0.113.10` (the old, decommissioned block)
> - All `charge_ok` events resolve to `198.51.100.20` or `198.51.100.21` (the new block)
> - The `az` label correlates: AZ-c sees `203.0.113.10`; AZ-a and AZ-b see `198.51.100.x`
> - Traces (`b4dd34265bcc3021`, `48f69e70f818595f`, `dc55907f3c0ecaa7`, `8d09fa79857221ee`) show AZ-c spans fail with `connection_refused` and `resolved_ip=203.0.113.10`; AZ-a/b traces succeed with `198.51.100.20`
> - The vendor announcement 7 days prior in `deploy_log.json` warned of the rotation with `TTL 3600`
> - The `az_c_resolver_refreshed` log line at 09:45 confirms post-incident recovery

### Root Cause

The `pp-api` vendor rotated their IP block from 203.0.113.0/24 to 198.51.100.0/24 with TTL 3600 s. The vendor decommissioned the old block at 09:00:00 UTC on 03-22, exactly as announced. AZ-a and AZ-b resolvers had refreshed against the new authoritative record within the TTL period. AZ-c's resolver served stale `203.0.113.10` past TTL expiry. AZ-c payment-svc pods opened TCP connections to the decommissioned IP and received `connection_refused`.

```
vendor decommissions 203.0.113.0/24 at 09:00:00
  ├─> AZ-a, AZ-b resolvers: cached 198.51.100.20/21 → traffic OK
  └─> AZ-c resolver: stale 203.0.113.10 cached past TTL
        └─> payment-svc(AZ-c) TCP-connects to dead IP
              └─> connection_refused → checkout-svc 5xx for AZ-c traffic
                    └─> bimodal user-visible error rate (~33% of total)
```

### Counterfactual

- **Rolling back firewall change**: no effect; firewall did not block this traffic (refused vs. blocked have different signatures).
- **Re-rotating TLS certs**: no effect; no TLS handshake errors in the window.
- **Failing LB targets in AZ-c manually**: technically masks the symptom by draining AZ-c, but does not fix the resolver.
- **Restarting payment-svc in AZ-c**: only effective if restart triggers a fresh DNS lookup — accidental, not reliable.

### Prevention

1. **payment-svc**: Log `resolved_ip` and `az` on every outbound call (already present in incident logs; make this the default). Alert on `resolved_ip ∈ old_block` after vendor rotation events.
2. **platform**: Configure resolver-side TTL ceilings (max 600 s for vendor records) and AZ-aware DNS health-check probes that compare resolved IPs across AZs every 5 min, alerting on divergence.

---

## I-5 — mTLS Cert Rotation + Clock Skew (2026-03-27 06:00 → 06:15)

### Timeline

| UTC time | Event | Source |
|---|---|---|
| 03-27 05:30 | payment-svc deployed v3.2.1 (log format change to ECS-compatible) | `deploy_log.json` |
| 06:00:00 | Service mesh controller rotates checkout-svc → payment-svc mTLS cert; new cert `not_before=2026-03-27T06:00:15Z` | `deploy_log.json` |
| 06:00:11 | First checkout-svc ERROR `TLS_handshake_error_certificate_not_yet_valid` with `not_before=06:00:15Z, current_time=05:59:43Z, delta_seconds=-27` | `logs/checkout-svc.jsonl` |
| 06:00:45 | Alert `A-401 MtlsHandshakeFailureSpike` | `alerts.json` |
| 06:01:00 | Alert `A-402 CheckoutErrorRateHigh` (>20%) | `alerts.json` |
| 06:02:00 | Alert `A-403 SyntheticCheckoutFailing` | `alerts.json` |
| 06:15:00 | `TLS_handshake_recovered` INFO line; `mtls_handshake_failures_per_min` returns to 0 | `logs/checkout-svc.jsonl` |

### Candidate Hypotheses

| # | Hypothesis | Confidence | Reasoning |
|---|---|---|---|
| H1 | Recent payment-svc deploy (30 min ago) broke something | 3 | Closest deploy to incident |
| H2 | Network or firewall issue between checkout-svc and payment-svc | 3 | TLS errors often misread as network issues |
| H3 | Service mesh infrastructure failure | 3 | Mesh is involved in mTLS |
| H4 | mTLS cert rotation pushed a cert with `not_before` ahead of the validator's clock | 5 | Log line carries `not_before` and `current_time` fields; delta is -27s |

### Evidence Review

> **Hypothesis H1 (recent deploy)**: Dismissed
> **Evidence**: The 05:30 deploy was a log format change — no code or cert path was touched. payment-svc ran cleanly between 05:30 and 06:00 with no errors. The first error is at 06:00:11, exactly when the mesh issued the new cert at 06:00:00 (not when the deploy completed). Reverting the deploy would not change cert rotation behavior.

> **Hypothesis H2 (network/firewall)**: Dismissed
> **Evidence**: The error message is `certificate_not_yet_valid`, a TLS-layer assertion that requires the TCP connection to have completed successfully — so the network path is open. Firewalls cannot inject this specific error type. Traces (`6fc9a5bbc22ea4f2` through `e8996494b3b3883a`) show the handshake step completing but being rejected at TLS validation.

> **Hypothesis H3 (mesh outage)**: Dismissed
> **Evidence**: The mesh did not go down; it operated correctly per its design. The mesh control plane was functional but issued a clock-sensitive artifact without verifying clock health across the fleet. The bug is policy-level, not infrastructure failure.

> **Hypothesis H4 (cert clock skew)**: Accepted
> **Evidence**: `checkout-svc.jsonl` `TLS_handshake_error_certificate_not_yet_valid` lines carry `not_before=2026-03-27T06:00:15Z` and `current_time=2026-03-27T05:59:43Z` — a 32-second delta. The validator clock is behind the cert-issuer clock by ~27 seconds. Traces show `validator_clock_skew_seconds=-27`. The cert becomes valid only after the validator's clock advances past `06:00:15`. The `TLS_handshake_recovered` INFO at 06:15 confirms the window closes when clocks reconverge. The deploy log explicitly records the rotation at 06:00:00 with the same `not_before`.

### Root Cause

The service mesh's automatic 24-hour cert rotation issued a new mTLS cert for the checkout-svc → payment-svc edge at 06:00:00. The cert's `not_before` was set to `06:00:15` (a 15-second positive offset). The cert-signing host's clock was NTP-synced and correct. One or more validator hosts (payment-svc replicas) had ~27 seconds of NTP drift. From those validators' perspective, `current_time (05:59:48)` < `not_before (06:00:15)`, so the cert is "not yet valid". Handshakes failed for ~15 minutes until NTP correction advanced the validator clocks past the cert's `not_before` instant.

```
mesh rotation at 06:00:00 → cert.not_before=06:00:15
  └─> checkout-svc presents cert during handshake
        └─> payment-svc validators (clock-27s) reject as "not_yet_valid"
              └─> checkout-svc 5xx → frontend errors
                    └─> resolves at 06:15 when validator clocks catch up
```

### Counterfactual

- **Rolling back the 05:30 payment-svc deploy**: no effect; the deploy did not touch cert handling.
- **Replacing the cert manually**: at best replaces it with another cert exhibiting the same `not_before` future-dated issue.
- **Restarting checkout-svc to re-handshake**: re-handshake hits the same validator clock skew and fails again.
- **Investigating firewall rules**: zero return; the error is at TLS layer, not network layer.

### Prevention

1. **service mesh**: Change the cert-rotation controller to issue certs with `not_before` set 60 s in the **past** (negative offset) rather than 15 s in the future. This makes the new cert valid immediately for any reasonable validator clock skew.
2. **platform**: Monitor NTP drift on every node (`time_drift_seconds` metric, alert at > 10 s) and gate mesh cert rotation on max-drift-across-fleet being below 5 s.

---

## Summary Table

| ID | Root cause class | Smoking-gun field | Strongest false positive |
|---|---|---|---|
| I-1 | Third-party 5xx + no circuit breaker | `extra.endpoint=fx-api status=503` | rds_cpu spike |
| I-2 | Real memory leak (no TTL on cache) | `extra.size_bytes=5242880 ttl_seconds=null` | numpy upgrade 24 h prior |
| I-3 | Feature flag → unindexed query | deploy_log `enable_loyalty_recommendations` + log `uses_index=false` | RDS class change 24 h prior |
| I-4 | DNS rotation, AZ-scoped stale resolver | `extra.resolved_ip=203.0.113.10 az=c` | firewall change 48 h prior |
| I-5 | mTLS cert `not_before` ahead of validator clock | `extra.not_before` vs `extra.current_time` | recent deploy 30 min prior |

---

## Non-incident events ruled out

### Weekly Thursday ETL — 14:00 to 14:30 UTC every Thursday

**Signal that drew attention**: `rds_cpu_pct.csv` shows a sustained ~60% CPU bump every Thursday between 14:00 and 14:30, on the four Thursdays in the window (2026-03-05, 03-12, 03-19, 03-26). On 2026-03-05 this ETL overlaps with the start of I-1.

**Ruled out as a non-incident because**:
- `logs/rds-orders.jsonl` contains paired `scheduled_job_started job=weekly_finance_etl recurrence=weekly` and `scheduled_job_completed` entries at the start and end of each window. Recurrence + clean completion = routine.
- No alerts fire on these windows; CPU peaks below the 80% alert threshold.
- The pattern repeats on the exact same day-of-week and hour for four consecutive weeks — a strong signature of a scheduled job, not an incident.

**Distinction from I-1**: On 2026-03-05 the ETL finishes at 14:30 exactly when I-1 begins. The ETL is responsible for the elevated ~60% baseline at 14:30; I-1's audit-log INSERT amplification then pushes CPU from 60% to 88%. The two are independent and additive.

### One-time DB backup window — 2026-03-14 02:00 to 03:00 UTC

**Signal that drew attention**: `metrics/checkout_p99_ms.csv` rises from ~200 ms to ~800 ms during the window; `rds_cpu_pct.csv` rises to ~70%. Alert `A-501 SyntheticCheckoutFailing` fires at 02:15 and clears by 03:00.

**Ruled out as a non-incident because**:
- `deploy_log.json` contains an entry at 2026-03-13T18:00:00Z (8 hours before the window) from `actor=dba-team`: "scheduled one-time full DB backup: window 2026-03-14 02:00-03:00 UTC; expect elevated read replica lag and synthetic check noise". The entry is approved under change-board ticket CR-2026-0287.
- `logs/rds-orders.jsonl` contains `backup_window_started type=full_database_snapshot` at 02:00 and `backup_window_completed status=ok` at 03:00.
- Symptoms decay cleanly at the announced end of the window.
- Only one alert fires (synthetic check), and it correlates exactly with the announced read-replica-lag side effect.
