# DESIGN.md — Ronki Closed-Loop Orchestrator

## 1. Decision engine: Rule-based or LLM-based?

**Choice: Rule-based.**

Reason: The Ronki stack has 3 clearly defined alert types (`HighLatency`, `HighErrorRate`, `InstanceDown`) and each maps 1-1 to a runbook that has been verified by the ops team. In this environment, a rule-based approach provides **decision latency < 1ms** and is **deterministic — the same alert always triggers the same runbook**. LLM-based is more suitable when alert descriptions are complex, ambiguous, or when reasoning across multiple steps (like analyzing logs + metrics simultaneously) is required.

Trade-offs:

| | Rule-based | LLM-based |
|---|---|---|
| Decision Latency | < 1ms | 200–800ms (API round-trip) |
| Determinism | 100% | Depends on temperature, prompt |
| Adding new alerts | Requires manual map update | Can infer if prompt is good enough |
| Cost | Free | ~$0.002–0.01/decision |
| Fallback when offline | Not needed | Needs rule-based fallback |

Conclusion: with 3 fixed alert types and high reliability requirements in the production lab, rule-based is the correct choice. If expanding to 20+ alert types with natural language descriptions, we would consider LLM-based with a 0.6 confidence threshold.

## 2. Blast-radius config

```yaml
blast_radius:
  max_actions_per_minute: 3
  max_restarts_per_service_per_hour: 5
```

**Reasoning for chosen values:**

- `max_actions_per_minute: 3` — The stack has 5 services. If a cascade failure occurs, a maximum of 3 actions/minute prevents the orchestrator from restarting all services simultaneously and increasing database load. This number is responsive enough (3 services in 1 minute) without causing a thundering herd.
- `max_restarts_per_service_per_hour: 5` — If a service is restarted > 5 times in an hour and still fails, it's a sign of a non-recoverable error (continuous OOM, bad config, dependency down). Continuing to restart is useless — human escalation is needed.

When threshold exceeded: orchestrator logs `BLAST_RADIUS_EXCEEDED` and takes no action, letting the alert continue firing until a human intervenes.

## 3. Verify step

**Checked metrics:** p99 latency (ms) AND `up` (1/0).

**Thresholds:**
- `latency_p99_max_ms: 500` — from `baseline.json`, normal p99 ranges from 72–230ms depending on the service. Choosing 500ms = roughly 2x the baseline p99 of the slowest service (checkout-svc: 230ms), wide enough to avoid false negatives but still detects if the action had no effect.
- `up_required: 1` — service must be reachable before verifying latency is meaningful.

**Timeout and polling:**
- `verify_timeout_seconds: 60` — container restart takes 5–10s, then needs another 15–20s for metrics to stabilize in Prometheus (scrape interval 10s). 60s = enough time for 3 scrape cycles after the container is up.
- `verify_poll_interval_seconds: 10` — matches the Prometheus scrape interval.
- `verify_min_samples: 3` — requires 3 consecutive passing samples before concluding verify is successful. Avoids false positives from a single lucky sample.

## 4. Circuit breaker reset

**Reset mode: manual.**

Reason: the circuit breaker opens when 3 consecutive failures occur. This is a severe abnormal state — the orchestrator tried and failed 3 times in a row. If automatically reset after N minutes, there's a risk the orchestrator continues an infinite loop and causes further disruption (thundering herd, database connection exhaustion, etc.).

Manual reset ensures: an engineer reviews the logs, identifies the root cause, and confirms the fix is applied before automation resumes. The cost of manual reset (a few minutes of delay) is lower than the risk of an incorrect automated reset.

How to reset: press `Ctrl+C` to stop the orchestrator, fix the issue, and restart with `uv run python closed_loop.py --config config.yaml`.

If automatic reset is desired: add `cool_down_seconds: 1800` (30 minutes) to the config and implement time-based reset. But there must be a separate alert to notify on-call when the circuit opens.

## 5. Mutex strategy (Stress 2 — concurrent alert race)

**Design**: A separate `threading.Lock` for each service name, stored in a `_service_locks` dict protected by a meta-lock. When an alert arrives, the orchestrator calls `acquire(blocking=False)` — if the service currently has a runbook running, it logs `SERVICE_LOCK_BUSY` and skips the duplicate alert instead of queuing it. Two different services always have different locks, so they run in parallel without blocking.

Reason for using `blocking=False` instead of a queue: in closed-loop production, a runbook actively running on service A is an ongoing event — a new alert on the same service A within 30s is a duplicate of the same incident, not a new incident. Queuing it would cause the runbook to re-execute immediately after the lock releases, i.e., performing the action twice on the same service consecutively — more dangerous than skipping it.

## 6. Rollback chain ordering (Stress 1 — multi-step transactional deploy)

**Design**: `run_transactional_steps` executes steps A→B→C and accumulates a `completed` list in execution order. When step C fails, the orchestrator slices `rollback_steps[:len(completed)]` and iterates over `reversed()` — meaning rollback-B runs before rollback-A. It does not rollback steps that were never executed.

Reason reverse-order is technically correct: step A (drain traffic) creates a state that step B (apply config) depends on. If A is rolled back before B, the service might receive traffic while the config is in an inconsistent state. Reverse order ensures teardown is the opposite of setup — the same LIFO stack principle as transaction rollbacks in a database.

## 7. Reason for observability metrics selection

The five metrics were chosen based on debug-driven principles: each metric answers a specific question when an incident occurs. `closed_loop_actions_total{outcome}` immediately shows if the orchestrator acted or rolled back — no need to read logs. `closed_loop_circuit_breaker_state` shows when automation is halted; if gauge = 1 and no alerts are processed, engineers know they must restart the orchestrator manually. `closed_loop_blast_radius_remaining` provides early warning before rate limits are hit — gauge hitting 0 means the orchestrator is silent not because there are no alerts, but because the quota is exhausted. `closed_loop_mutex_locked` helps debug race conditions: if a service is continually LOCKED for many minutes, a runbook might be hung. `closed_loop_verify_status` with an in-progress value (2) shows the orchestrator is waiting for Prometheus confirmation — if this state lasts longer than `verify_timeout_seconds`, the verify timed out. There are no "vanity" metrics like total polls or skipped alerts — those numbers don't help find root causes faster.

## 8. Decision validation policy (Stress 3 — LLM hallucination defense)

**Design**: before calling dry-run, `validate_runbook` checks if the runbook name returned from the decision engine exists in `runbook_registry` (an explicit list of paths declared in the config). If it doesn't exist → log `DECISION_VALIDATION_FAILED` with full details (`bad_runbook`, `alertname`, `raw_decision`, `action=escalate_no_auto_action`), then return immediately — no subprocess spawned, no action executed.

Reason an explicit whitelist is needed: An LLM might return a runbook name that makes linguistic sense but doesn't exist in the system (`scale_down_database.sh`, `reboot_kernel.sh`). If the orchestrator blindly trusts that name and runs `subprocess` with a non-existent path, bash will exit non-zero and the action will fail — but that failure will increment the circuit breaker counter, potentially opening the circuit after 3 consecutive hallucinations. Pre-dry-run validation breaks that loop and keeps a clear audit trail for human review.
