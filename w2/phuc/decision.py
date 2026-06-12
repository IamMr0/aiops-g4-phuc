import math


def parse_action_params(action_name: str, raw_params: list, catalog_item: dict, root_cause_service: str) -> dict:
    param_names = catalog_item.get("params", [])
    result = {}
    for i, name in enumerate(param_names):
        if name == "service":
            result[name] = root_cause_service
        elif action_name == "rollback_service" and name == "target_version":
            result[name] = "previous"
        elif i < len(raw_params):
            result[name] = raw_params[i]
        else:
            if action_name == "page_oncall" and name == "team":
                result[name] = "platform-team"
            else:
                result[name] = "unknown"
    return result


def select_action(retrieval_result: dict, actions_catalog: list[dict], root_cause_service: str) -> dict:
    """Layer 3: cost-aware utility + blast-radius gate + breadth-of-support scoring.

    Key design decisions:
    - Uses accumulated vote score (not per-incident average) so actions with broader
      historical support rank higher than single-incident matches.
    - Applies sqrt(unique_incidents) breadth multiplier to reward consistency.
    - Penalizes page_oncall to prevent zero-cost bias (page has 0 cost/blast in catalog).
    - Gates high-blast actions behind confidence threshold.
    """
    catalog = {a["name"]: a for a in actions_catalog}

    candidates = retrieval_result.get("candidates", {})
    ood = retrieval_result.get("ood", False)

    if ood or not candidates:
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": 0.0,
            "evidence": {
                "reason": "Out of distribution or no valid candidates found.",
                "top_matches": [
                    {"id": m[1]["id"], "score": round(m[0], 3)}
                    for m in retrieval_result.get("top_matches", [])
                ]
            },
            "top_3_neighbors": [
                {"id": m[1]["id"], "score": round(m[0], 3)}
                for m in retrieval_result.get("top_matches", [])[:3]
            ],
            "consensus_score": 0.0,
            "selected_action_meta": {
                "blast_radius_services": 0
            }
        }

    best_action = None
    best_ev = -float('inf')
    best_candidate_info = None

    for key, c in candidates.items():
        name = c["name"]
        cat_info = catalog.get(name)
        if not cat_info:
            continue

        cost = cat_info.get("cost_min", 0)
        blast = cat_info.get("blast_radius_services", 0)

        # --- Confidence from best supporting incident ---
        max_sim = max(s["similarity"] for s in c["supporting_incidents"])
        total_incidents = len(c["supporting_incidents"])
        unique_incidents = len(set(s["id"] for s in c["supporting_incidents"]))
        success_count = c.get("success_count", 0)

        # Confidence = best match similarity × success ratio
        success_ratio = (success_count + 0.3 * c.get("partial_count", 0)) / max(1, total_incidents)
        confidence = max_sim * success_ratio
        confidence = max(0.0, min(1.0, confidence))
        p_fail = 1.0 - confidence

        # --- Breadth-adjusted score ---
        # We no longer multiply by sqrt(unique_incidents) as it creates an O(n^1.5) growth curve
        adjusted_score = c["score"]

        # --- EV calculation ---
        benefit_per_unit = 60  # benefit per unit of adjusted vote score
        penalty = blast * 30
        ev = adjusted_score * benefit_per_unit - cost - (p_fail * penalty)

        # Blast-radius gate: block modifying actions at low confidence
        if confidence < 0.35 and blast >= 1:
            continue

        if ev > best_ev:
            best_ev = ev
            best_action = c
            best_candidate_info = {
                "p_success": round(confidence, 3),
                "ev": round(ev, 3),
                "cost": cost,
                "blast_radius": blast,
                "score": round(c["score"], 3),
                "breadth": unique_incidents,
                "supporting_incidents": c["supporting_incidents"]
            }

    if not best_action:
        return {
            "selected_action": "page_oncall",
            "params": {"team": "platform-team"},
            "confidence": 0.0,
            "evidence": {"reason": "All candidates failed blast radius gate."},
            "top_3_neighbors": [
                {"id": m[1]["id"], "score": round(m[0], 3)}
                for m in retrieval_result.get("top_matches", [])[:3]
            ],
            "consensus_score": 0.0,
            "selected_action_meta": {
                "blast_radius_services": 0
            }
        }

    return {
        "selected_action": best_action["name"],
        "params": parse_action_params(best_action["name"], best_action["params"], catalog.get(best_action["name"], {}), root_cause_service),
        "confidence": best_candidate_info["p_success"],
        "evidence": {
            "ev": best_candidate_info["ev"],
            "supporting_incidents": best_candidate_info["supporting_incidents"]
        },
        "top_3_neighbors": [
            {"id": m[1]["id"], "score": round(m[0], 3)}
            for m in retrieval_result.get("top_matches", [])[:3]
        ],
        "consensus_score": best_candidate_info["score"],
        "selected_action_meta": {
            "blast_radius_services": best_candidate_info["blast_radius"]
        }
    }
