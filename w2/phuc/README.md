# Lab — Evidence-Driven Remediation Engine — Data Pack

This pack contains everything you need to run the lab described in the handout.

## Contents

```
phuc/
├── eval/
│   ├── E01.json ... E08.json          (8 evaluation incidents)
│   └── expected.json                  (ground-truth accepted actions)
├── incidents_history.json             (~29 past incidents)
├── topology.json                      (canonical service topology)
├── actions.yaml                       (remediation action catalog)
├── grade.py                           (auto-grader — run after you produce audit.jsonl)
├── engine_skeleton.py                 (optional starting skeleton — feel free to ignore)
├── optional-helpers.py                (two pure-mechanical schema parsers — see HANDOUT §2.6)
├── engine.py
├── features.py
├── retrieval.py
├── decision.py
└── audit.jsonl
└── README.md                          (this file)
```

## Quick start

```bash
unzip lab-w2-evidence-driven-remediation-*.zip
cd data-pack
uv venv --python 3.12 && uv pip install pandas numpy scikit-learn pyyaml
# Write your engine.py, features.py, retrieval.py, decision.py.
# Run on each eval incident:
for i in 01 02 03 04 05 06 07 08; do
  .venv/bin/python engine.py decide --incident eval/E$i.json \
                              --history incidents_history.json \
                              --actions actions.yaml
done
# Auto-grade your audit.jsonl:
.venv/bin/python grade.py --audit audit.jsonl --expected eval/expected.json
```

## Reading the schemas

- `eval/E*.json` — see handout §2.1.
- `incidents_history.json` — see handout §2.2.
- `actions.yaml` — see handout §2.3.
- `eval/expected.json` — `accepted_actions` is a list; engine recommending any one of them gets credit. `must_not_action` is a hard veto.
- `topology.json` — same structure as `eval/E*.json.topology` (nodes + edges).

## Submission

See handout §7.


## How to run

```bash
cd phuc
uv venv --python 3.12 && uv pip install pandas numpy scikit-learn pyyaml

Remove-Item -ErrorAction SilentlyContinue audit.jsonl; foreach ($i in "01", "02", "03", "04", "05", "06", "07", "08") { python engine.py decide --incident eval/E$i.json --history incidents_history.json --actions actions.yaml }

python grade.py --audit audit.jsonl --expected eval/expected.json
```

## Expect output
```bash
python engine.py decide --incident eval/E01.json --history incidents_history.json --actions actions.yaml
```
```json
{                                                
  "selected_action": "increase_pool_size",
  "params": {
    "service": "payment-svc",
    "from_value": "50",
    "to_value": "100"
  },
  "confidence": 0.933,
  "evidence": {
    "ev": 52.99,
    "supporting_incidents": [
      {
        "id": "INC-2025-11-08",
        "similarity": 0.933,
        "outcome": "success"
      }
    ]
  },
  "top_3_neighbors": [
    {
      "id": "INC-2025-11-08",
      "score": 0.933
    },
    {
      "id": "INC-2026-02-22",
      "score": 0.682
    }
  ],
  "consensus_score": 0.933,
  "selected_action_meta": {
    "blast_radius_services": 1
  },
  "incident_id": "E01"
}

```