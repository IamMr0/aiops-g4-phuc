# W3-D1 Submission — Phuc

## 3 things I learned
1. **CPU is a terrible SLI candidate**: I realized that an SLI must be strictly proportional to the actual user experience (user pain). Utilizing CPU or Memory usage (saturation signals) as primary alerting mechanisms generates massive amounts of false positives and fails to indicate whether the user is genuinely impacted.
2. **Error Budget Burn Rate over Raw Error Rate**: Alerting based on the speed of budget consumption (burn rate) normalizes the urgency of an incident across different services with varying SLOs. It perfectly answers the difficult question: "Is this error rate severe enough to wake up the on-call engineer?".
3. **Multi-Window Multi-Burn-Rate (MWMBR)**: Combining both long-window and short-window intervals with an AND condition is a brilliant strategy. It ensures the incident is significant enough for on-call intervention (long window) while automatically and swiftly resolving the alert (fast recovery via short window) once the incident passes, preventing lingering "ghost" alerts.

## 1 thing I am still unclear about
While I understand the mechanics of MWMBR, I am not entirely confident in how to practically tune the burn rate thresholds (e.g., 14.4 or 6) if I encounter specialized production datasets where Google's default parameters don't perform well. The operational procedure for discovering optimal coefficients seems like it would require extensive back-testing tooling.

## 1 trade-off in my SLO decisions that I am unsure about
In the frontend SLI definition, I decided to aggregate `dom_ready < 3000ms`, `js_error`, and `network_error` into a single composite SLI. This significantly reduces the total number of SLOs we have to report on, but it trades off immediate debuggability: when the frontend SLI drops, the on-call engineer cannot instantly tell from the alert whether the drop is caused by latency (slow DOM ready) or a sudden spike in Javascript exceptions, requiring deeper investigation.

## Validation report
- noise_reduction_pct: 86.4%
- mttd_delta_s: 0s
- false_negative: 0
- verdict: pass
