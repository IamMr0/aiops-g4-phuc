import math
from collections import Counter


def parse_history_action(s: str) -> dict:
    parts = s.split(":")
    if not parts:
        return {"name": "page_oncall", "params": []}
    return {"name": parts[0], "params": parts[1:]}


def jaccard(set1, set2):
    if not set1 and not set2:
        return 1.0
    if not set1 or not set2:
        return 0.0
    return len(set1.intersection(set2)) / len(set1.union(set2))


import re

def compute_idf(corpus: list[str]) -> dict:
    df = Counter()
    for doc in corpus:
        tokens = set(re.findall(r'\b[a-zA-Z_]+\b', doc.lower()))
        for t in tokens:
            df[t] += 1
    N = len(corpus)
    return {t: math.log((1 + N) / (1 + count)) + 1 for t, count in df.items()}


def log_containment(query_doc: str, hist_doc: str, idf: dict) -> float:
    if not hist_doc:
        return 0.0
    
    query_tokens = set(re.findall(r'\b[a-zA-Z_]+\b', query_doc.lower()))
    hist_tokens = re.findall(r'\b[a-zA-Z_]+\b', hist_doc.lower())
    
    if not hist_tokens:
        return 0.0
        
    hist_tf = Counter(hist_tokens)
    score = 0.0
    total_weight = 0.0
    
    for t, count in hist_tf.items():
        weight = count * idf.get(t, 1.0)
        total_weight += weight
        if t in query_tokens:
            score += weight
            
    if total_weight == 0:
        return 0.0
    return score / total_weight


def trace_similarity(query_traces, history_traces):
    if not query_traces and not history_traces:
        return 0.5  # neutral — both empty
    if not query_traces or not history_traces:
        return 0.0
    if not history_traces:
        return 0.0
        
    query_edges = {frozenset([t["from"], t["to"]]) for t in query_traces}
    history_edges = {frozenset([t["from"], t["to"]]) for t in history_traces}
    
    if not history_edges:
        return 0.0

    overlap = len(history_edges.intersection(query_edges))
    containment = overlap / len(history_edges)
    
    if containment > 0:
        ratio_sims = []
        for edge in history_edges.intersection(query_edges):
            qr = max([t.get("p99_deviation_ratio", 1.0) for t in query_traces if frozenset([t["from"], t["to"]]) == edge])
            hr = max([t.get("p99_deviation_ratio", 1.0) for t in history_traces if frozenset([t["from"], t["to"]]) == edge])
            
            ratio_sim = 1.0 - abs(qr - hr) / max(qr, hr, 1.0)
            ratio_sims.append(max(0, ratio_sim))
        quality_bonus = sum(ratio_sims) / len(ratio_sims) * 0.3
        containment = min(1.0, containment + quality_bonus)

    return containment


def similarity(query: dict, hist_entry: dict, q_doc: str, h_doc: str, idf: dict) -> float:
    """Layer 2 helper: adaptive similarity with dynamic weighting using TF-IDF."""
    q_svc = set(query.get("affected_services", []))
    h_svc = set(hist_entry.get("affected_services", []))
    sim_svc = jaccard(q_svc, h_svc)

    sim_logs = log_containment(q_doc, h_doc, idf)
    sim_traces = trace_similarity(query.get("trace_signatures", []), hist_entry.get("trace_signatures", []))

    # Adaptive weights: choose based on trace evidence availability
    query_has_traces = len(query.get("trace_signatures", [])) > 0
    hist_has_traces = len(hist_entry.get("trace_signatures", [])) > 0

    if query_has_traces and hist_has_traces:
        # Both have trace data — traces are the strongest direct signal
        w_svc, w_log, w_trace = 0.1, 0.3, 0.6
    else:
        # Trace data is thin/absent — rely heavily on logs
        w_svc, w_log, w_trace = 0.1, 0.8, 0.1

    return w_svc * sim_svc + w_log * sim_logs + w_trace * sim_traces


def retrieve_and_vote(query: dict, history: list[dict], top_k: int = 5, ood_threshold: float = 0.10) -> dict:
    """Layer 2: kNN over history + outcome-weighted action voting."""
    query_doc = " ".join(query.get("log_signatures", []))
    history_docs = [" ".join(h.get("log_signatures", [])) for h in history]

    idf = compute_idf([query_doc] + history_docs)

    scored = []
    for i, h in enumerate(history):
        score = similarity(query, h, query_doc, history_docs[i], idf)
        scored.append((score, h))

    scored.sort(key=lambda x: x[0], reverse=True)

    candidates = {}
    if not scored or scored[0][0] < ood_threshold:
        return {"candidates": {}, "top_matches": scored[:top_k], "ood": True}

    # Dynamic relevance threshold: include matches >= 40% of best match
    best_score = scored[0][0]
    relevance_threshold = max(ood_threshold, best_score * 0.4)

    top_matches = [(s, h) for s, h in scored if s >= relevance_threshold][:top_k]

    for score, h in top_matches:
        outcome = h.get("outcome", "failed")
        if outcome == "success":
            weight = 1.0
        elif outcome == "partial":
            weight = 1.0  # Partial outcomes are handled by success_ratio in decision.py
        else:
            weight = -1.0

        actions = set(h.get("actions_taken", []))
        for act_str in actions:
            parsed = parse_history_action(act_str)
            action_name = parsed["name"]
            target = parsed["params"][0] if parsed["params"] else ""

            key = f"{action_name}:{target}"
            if key not in candidates:
                candidates[key] = {
                    "name": action_name,
                    "target": target,
                    "score": 0.0,
                    "supporting_incidents": [],
                    "params": parsed["params"],
                    "success_count": 0,
                    "partial_count": 0,
                    "failed_count": 0,
                }
            candidates[key]["score"] += score * weight
            candidates[key]["supporting_incidents"].append({
                "id": h["id"],
                "similarity": round(score, 3),
                "outcome": outcome
            })
            if outcome == "success":
                candidates[key]["success_count"] += 1
            elif outcome == "partial":
                candidates[key]["partial_count"] += 1
            else:
                candidates[key]["failed_count"] += 1

    return {"candidates": candidates, "top_matches": top_matches, "ood": False}
