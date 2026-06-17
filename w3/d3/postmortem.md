# Postmortem: Edge WAF Regex CPU Saturation (Cloudflare 2019 reproduction)

**Status:** complete
**Date:** 2026-06-15
**Authors:** Ngo Nguyen Phuc
**Severity:** SEV1
**Duration:** 10 minutes (02:00:00 UTC → 02:10:00 UTC)

## Summary

A WAF regex rule revision was promoted to the edge middleware canary and triggered catastrophic
backtracking on adversarial input, pegging edge CPU and cascading into elevated API gateway
latency, checkout downstream timeouts, and payment queue depth growth. The rollback was issued
manually by an operator roughly four minutes after the first alert fired. All customer-facing
services affected by the latency spike recovered within approximately 5–7 minutes of rollback.
No data loss occurred; impact was limited to elevated latency and timeout-driven errors during
the active window.

## Impact

- Users affected: requests served through the edge WAF canary during the 02:00–02:07 UTC window
  (exact percentage requires production traffic-split data; canary scope estimated at 1–10% of
  edge traffic based on reproduction config)
- Revenue impact: estimated using cost model assumptions in `cost_model.py`; not separately
  computed for this single incident
- SLO budget consumed: api-gateway p99 latency SLO breached for ~6 minutes (02:01:35–02:07:30);
  checkout-svc and payment-svc breached their respective warning thresholds for ~2–4 minutes
- External communication: none required for this reproduction; in a production equivalent, a
  status page update would be expected once p99 latency SLO breach was confirmed (≈02:02–02:03 UTC)

## Timeline (UTC)

| Time  | Event |
|-------|-------|
| 02:00:00 | WAF regex rule revision promoted to edge middleware canary |
| 02:01:12 | Edge CPU utilization rises from 35% to 92% |
| 02:01:35 | API gateway p99 latency rises from 180ms to 2400ms |
| 02:02:05 | Tier-1 API latency and edge 5xx alerts fire |
| 02:02:08 | Edge WAF CPU saturation alert fires (92% vs 85% threshold) |
| 02:02:17 | Checkout-svc downstream timeout rate alert fires (warning) |
| 02:02:20 | Correlator groups edge, api-gateway, checkout, and payment symptoms into one cluster |
| 02:02:30 | Payment-svc request queue depth alert fires (warning) |
| 02:02:45 | RCA selects api-gateway as root cause (confidence 0.58); edge middleware listed only as secondary evidence |
| 02:04:10 | Operator issues WAF rule rollback command |
| 02:05:00 | Edge CPU drops below 50%; latency begins recovery |
| 02:07:30 | API gateway p99 latency returns below 250ms |
| 02:10:00 | Incident moved to monitoring; no further customer-facing errors observed |

## Root cause

The WAF rule revision introduced a regex pattern containing nested quantifiers vulnerable to
catastrophic backtracking. On adversarial input matching the pattern's ambiguous repetition
structure, the regex engine attempted an exponential number of matching paths per request,
consuming CPU on the edge middleware tier. As edge CPU saturated, request processing slowed at
the edge, which propagated as elevated p99 latency at the API gateway. The API gateway's
increased latency caused checkout-svc to exceed its downstream timeout budget, and the resulting
retries and held connections drove payment-svc's request queue depth upward. The underlying
trigger — the regex rule promotion at 02:00:00 — preceded the first metric anomaly by 72 seconds
and the first alert by over two minutes, but no alerting or correlation logic in the pipeline
treated the deploy event as a candidate root-cause signal.

Detection was delayed relative to root-cause identification because the RCA stage ranked
api-gateway above edge-waf despite edge-waf being the only service with both a triggering change
event and a saturation-pattern alert. The batch RCA run (`rca_observed.json`) independently
selected payment-svc as root cause using a historical-incident similarity match, even though its
own topology-based candidate ranking scored checkout-svc higher (0.735 vs. 0.265) than the
service it ultimately chose. Both RCA outputs missed edge-waf entirely.

## Contributing factors

- The rule revision was promoted directly to the canary tier without a pre-deploy regex
  complexity / ReDoS check
- The correlator joined alert-to-alert relationships but did not join the deploy event timeline
  to the alert cluster, so the pipeline had no causal anchor pointing back to the change that
  triggered the incident
- The RCA's historical-similarity signal was weighted heavily enough to override its own
  topology-based candidate ranking, producing a root-cause pick that contradicted the pipeline's
  better-scoring internal signal
- Recovery depended on a human operator manually correlating the deploy time to the alert time
  and issuing the rollback, rather than the pipeline surfacing the deploy as the top suspect

## Detection

- The incident was detected via automated alerting (tier-1 API latency and edge 5xx alerts at
  02:02:05), not via user report
- Detection of *symptoms* was reasonably fast (alerts fired ~50 seconds after the first metric
  anomaly), but root-cause identification was incorrect on both the real-time RCA pass (picked
  api-gateway) and the batch RCA pass (picked payment-svc)
- Gap 1 — Missing change correlation: the pipeline had a deploy event in its own timeline data
  (02:00:00) that preceded every downstream alert, yet no component in `alerts_observed.json` or
  `rca_observed.json` references that deploy or treats recency-to-change as an RCA signal. This
  could have been detected earlier (and correctly) had the correlator joined alert clusters
  against a rolling window of recent change events
- Gap 2 — Similarity override of topology signal: `rca_observed.json` shows the graph-based RCA
  candidates correctly scored checkout-svc (0.735) above payment-svc (0.265), but the final
  verdict picked payment-svc anyway on the strength of a historical-incident text-similarity
  match to an unrelated prior incident (DB connection pool leak). This indicates the
  similarity-matching signal is currently weighted to override a stronger structural signal
  rather than acting as a tiebreaker, and neither RCA pass ever surfaced edge-waf — the actual
  root cause — as a candidate at all

## Response

- What went well: alerting on the symptom services (api-gateway, edge-waf) fired within roughly a
  minute of the underlying metric anomaly, and the manual rollback resolved the incident quickly
  once issued, with full recovery within ~3 minutes of rollback
- What went poorly: the automated RCA stage produced two different incorrect root-cause verdicts
  across its two passes, neither of which identified the edge WAF rule change as the cause; the
  operator effectively had to bypass the RCA output and reason from the timeline directly
- Where we got lucky: the regex's backtracking cost, while severe, did not fully exhaust edge CPU
  to the point of total request failure, so symptom alerts still fired and gave the operator a
  window to intervene before queue depth on payment-svc breached a critical (not just warning)
  threshold

## Action items

| Item                                                                                  | Owner          | Due        | Priority |
|----------------------------------------------------------------------------------------|----------------|------------|----------|
| Add deploy/change-event correlation as an RCA input signal (see ADR-001)                | Platform team  | 2026-07-01 | P0       |
| Re-weight historical-similarity signal as a tiebreaker rather than an override          | Platform team  | 2026-07-08 | P0       |
| Add a pre-deploy ReDoS / regex complexity check to the WAF rule pipeline               | Edge team      | 2026-07-15 | P1       |
| Require canary stage gating (1% → 10% → 100%) for all WAF rule promotions              | Edge team      | 2026-07-15 | P1       |
| Add a critical-severity threshold for payment-svc request queue depth                  | Payments team  | 2026-07-22 | P2       |