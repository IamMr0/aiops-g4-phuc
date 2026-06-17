# W3-D3 Submission — Ngo Nguyen Phuc

## Outage chosen
- ID: 3
- Name: Cloudflare WAF regex (2019-07-02)
- Why this one: it's a clean single-mechanism failure (catastrophic backtracking) that's cheap to
  reproduce without multi-region networking, but it still produces a realistic multi-service
  cascade (edge → gateway → checkout → payment) that's enough to stress-test whether the RCA stage
  can find the actual origin point or just chases the loudest downstream symptom.
- Failure mode: catastrophic backtracking (regex), with a secondary cascading-failure pattern as
  the symptom propagates from edge-waf through api-gateway into checkout-svc and payment-svc.

## 3 things I learned from this outage
1. A pipeline can detect an incident quickly (all four alerts fired within ~50 seconds of the
   first metric anomaly here) while still getting root cause completely wrong — detection speed
   and RCA correctness are separate problems and a green "detected in <30s" metric can hide a
   red "pointed at the wrong service" problem.
2. Signals that look reasonable in isolation can fight each other: the batch RCA's own
   topology-based candidate ranking scored checkout-svc above payment-svc, but a historical
   similarity match overrode that and picked payment-svc anyway. A multi-signal RCA needs an
   explicit precedence order, not just a weighted average, or a stronger signal can get silently
   outvoted by a weaker one.
3. The most useful signal — "what changed right before this started" — was sitting unused in data
   the pipeline already had (the deploy event in `timeline.json`). The gap wasn't a missing data
   source, it was a missing join between two streams (changes and alerts) that already existed
   separately.

## 1 thing my pipeline would still miss if this outage happened for real
- Pattern: a change that causes a slow-building regression rather than an immediate spike — e.g.
  a regex that's only pathological on a rare input pattern that takes hours to appear in real
  traffic, rather than seconds.
- Why miss: ADR-001's proposed fix uses a 5-minute recency window between change event and alert
  onset to flag a service as a change-correlated root-cause candidate. A slow-building regression
  would fall outside that window, and the change-event signal would never fire, leaving RCA back
  at relying on topology and similarity alone — the same signals that already failed in this
  reproduction.
- Mitigation idea: don't discard a change event from candidacy after the recency window expires;
  instead decay its weight gradually rather than dropping it to zero, and pair it with a
  change-correlated metric drift detector (did this service's baseline shift at any point since
  its last change, not just in the last 5 minutes) rather than a fixed cutoff.

## 1 decision in my ADR I'm not fully sure about
Prioritizing the change-event signal above historical-similarity matching, specifically. It fixes
this incident, but I only have one reproduction's worth of evidence that similarity-override is a
general problem rather than a one-off case where the similarity match happened to be a bad fit
(the historical incident it matched against was a genuinely different mechanism — a connection
pool leak, not a regex CPU issue). I'd want to see this play out against at least one more
incident type, ideally one where the historical-similarity match actually is the better signal,
before I'd be confident the new precedence order doesn't just trade one override problem for
another in the opposite direction.

## Cost model verdict for my stack
- ROI: 5.0
- Payback: 0.2 months
- Verdict: worth_it