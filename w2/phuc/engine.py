import argparse
import json
import yaml
from pathlib import Path

from features import extract_features
from retrieval import retrieve_and_vote
from decision import select_action

def decide(incident_path: Path, history_path: Path, actions_path: Path) -> dict:
    incident = json.loads(incident_path.read_text())
    history = json.loads(history_path.read_text())
    actions_catalog = yaml.safe_load(actions_path.read_text())
    
    vec = extract_features(incident)
    candidates = retrieve_and_vote(vec, history)
    decision = select_action(candidates, actions_catalog, vec.get("root_cause_service", "unknown"))
    
    # Ensure incident_id matches basename for evaluation
    incident_id = incident_path.stem
    decision["incident_id"] = incident_id
    
    return decision

def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    d = sub.add_parser("decide")
    d.add_argument("--incident", required=True)
    d.add_argument("--history", default="incidents_history.json")
    d.add_argument("--actions", default="actions.yaml")
    args = p.parse_args()
    
    if args.cmd == "decide":
        out = decide(Path(args.incident), Path(args.history), Path(args.actions))
        print(json.dumps(out, indent=2))
        with open("audit.jsonl", "a") as f:
            f.write(json.dumps(out) + "\n")
        return 0
    p.print_help()
    return 1

if __name__ == "__main__":
    import sys
    sys.exit(main())
