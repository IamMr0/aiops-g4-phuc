# ADR-001: Add deploy/change-event correlation as an RCA input signal

## Status
Accepted

## Context

During the reproduction of the Cloudflare 2019 WAF regex outage (see `postmortem.md`), the AIOps
RCA stage produced two different root-cause verdicts, and both were wrong. The real-time RCA pass
selected `api-gateway` (confidence 0.58); the batch RCA pass (`rca_observed.json`) selected
`payment-svc` (confidence 0.7). Neither identified `edge-waf` — the service where the triggering
WAF rule revision was promoted at 02:00:00 UTC, 65 seconds before the first anomalous metric and
over two minutes before the first alert.

The pipeline's own timeline data (`timeline.json`) already contains the deploy event. The failure
is not missing data — it is that no stage of the RCA pipeline joins alert clusters against a
window of recent change events (deploys, config pushes, feature flag flips). Without that join,
RCA has to infer root cause purely from alert topology and historical similarity, both of which
are weaker signals than "this service changed right before the symptoms started." This is the
same blind spot that contributed to delayed root-causing during the GitHub 2018 split-brain
incident (orchestrator failover was a change-like event the on-call team had to reconstruct
manually) and is a known general failure mode in the catalog (§4: cascading failure, where the
naive RCA also picks the wrong service based on alert volume rather than causal precedence).

## Decision

Add a change-event correlation signal to the RCA stage. For each alert cluster, the RCA will
query a rolling window (default: 5 minutes prior to first alert in the cluster) of recorded change
events — deploys, config pushes, feature flag changes, infrastructure changes — scoped to the
services in or topologically adjacent to the cluster. Any service with a change event inside that
window is boosted as a root-cause candidate, weighted by recency (closer to the first anomaly =
higher weight) and proximity (the changed service itself outweighs a topological neighbor of the
changed service).

This signal is combined with the existing topology-distance and historical-similarity signals
rather than replacing them, but is given higher priority than historical-similarity specifically,
since the Cloudflare reproduction showed similarity matching overriding a stronger topology signal
(checkout-svc scored 0.735 on topology vs. payment-svc's 0.265, yet payment-svc won on similarity
alone).

## Alternatives considered

- **Keep similarity + topology only, re-weight similarity down** — Pros: smallest implementation
  change, no new data dependency on a change-event feed. Cons: still would not have surfaced
  edge-waf in this incident, since edge-waf had no historical-similarity match and was not the
  topology-favored candidate either (checkout-svc was). Rejected as insufficient — it fixes the
  override problem but not the root miss.
- **LLM-only RCA over raw logs and recent deploys** — Pros: flexible, could in principle reason
  about deploy timing without an explicit structured signal. Cons: the failure catalog (§4) and
  general AIOps literature flag LLM-only RCA as prone to confident-wrong root-cause picks,
  especially under cascading-failure conditions where many services show symptoms simultaneously.
  Rejected as primary mechanism; could be considered later as a narrative/explanation layer on
  top of structured signals, not a replacement for them.
- **Require all deploys to carry a manual "blast radius" tag, route directly to on-call instead of
  through RCA** — Pros: would have caught this specific case (every deploy becomes a flagged
  candidate). Cons: pushes the burden onto deploy authors, doesn't generalize to non-deploy
  changes (config push, infra change), and would generate alert fatigue for benign deploys with
  no correlated symptoms. Rejected as a process-only fix when a signal-level fix is available.

## Consequences

+ Closes the specific gap observed in the Cloudflare reproduction: a service with a change event
  immediately preceding alert onset is now a first-class RCA candidate, not something the operator
  has to notice by manually reading the timeline
+ Generalizes beyond WAF regex deploys to any change-triggered incident pattern (config push,
  feature flag, infra change), which is the same family of root cause behind the AWS S3 2017 and
  GitHub 2018 incidents in the catalog
- Requires a reliable, low-latency feed of change events (deploys, config pushes, flag flips)
  joined to the same time axis as metrics/alerts; if that feed is incomplete or delayed, the new
  signal silently degrades back to current (topology + similarity) behavior, masking the gap
  rather than failing loudly
- Risk: recency-window tuning (5 minutes here) is a guess based on this single reproduction: too
  narrow a window misses slow-building regressions from a change made hours earlier; too wide a
  window reintroduces false positives by implicating unrelated recent deploys. Needs validation
  against more incident replays before the default is trusted in production.