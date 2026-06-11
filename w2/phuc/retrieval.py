from features import ROOT_CAUSE_PATTERNS


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


def text_overlap(text1, text2):
    """Enhanced text overlap using both token Jaccard and keyword matching."""
    tokens1 = set(text1.lower().split())
    tokens2 = set(text2.lower().split())
    token_sim = jaccard(tokens1, tokens2)

    # Also check keyword overlap (words >3 chars, excluding placeholders)
    t1_lower = text1.lower()
    t2_lower = text2.lower()
    substring_bonus = 0.0
    if len(t1_lower) > 5 and len(t2_lower) > 5:
        key_words1 = {w for w in t1_lower.split() if len(w) > 3 and w not in ('<num>', '<ip>', '<uuid>')}
        key_words2 = {w for w in t2_lower.split() if len(w) > 3 and w not in ('<num>', '<ip>', '<uuid>')}
        if key_words1 and key_words2:
            overlap = len(key_words1 & key_words2)
            total = len(key_words1 | key_words2)
            if total > 0:
                substring_bonus = overlap / total

    return max(token_sim, substring_bonus)


def log_similarity(query_logs, history_logs):
    if not query_logs and not history_logs:
        return 0.5  # neutral — no info
    if not query_logs or not history_logs:
        return 0.0

    scores = []
    for hl in history_logs:
        best_score = 0.0
        for ql in query_logs:
            score = text_overlap(hl, ql)
            if score > best_score:
                best_score = score
        scores.append(best_score)
    return sum(scores) / len(scores)


def trace_similarity(query_traces, history_traces):
    if not query_traces and not history_traces:
        return 0.5  # neutral — both empty
    if not query_traces or not history_traces:
        return 0.0

    query_edges = {frozenset([t["from"], t["to"]]) for t in query_traces}
    history_edges = {frozenset([t["from"], t["to"]]) for t in history_traces}
    edge_overlap = jaccard(query_edges, history_edges)

    # Bonus for matching edge characteristics (error_rate, p99_deviation)
    if edge_overlap > 0:
        q_by_edge = {frozenset([t["from"], t["to"]]): t for t in query_traces}
        h_by_edge = {frozenset([t["from"], t["to"]]): t for t in history_traces}
        common = query_edges & history_edges
        if common:
            ratio_sims = []
            for e in common:
                qt = q_by_edge[e]
                ht = h_by_edge[e]
                qr = qt.get("p99_deviation_ratio", 1.0)
                hr = ht.get("p99_deviation_ratio", 1.0)
                ratio_sim = 1.0 - abs(qr - hr) / max(qr, hr, 1.0)
                ratio_sims.append(max(0, ratio_sim))
            quality_bonus = sum(ratio_sims) / len(ratio_sims) * 0.3
            edge_overlap = min(1.0, edge_overlap + quality_bonus)

    return edge_overlap


def root_cause_class_similarity(query_classes: dict, hist_entry: dict) -> float:
    """Compare inferred root cause classes against historical root_cause_class."""
    if not query_classes:
        return 0.0

    hist_rc = hist_entry.get("root_cause_class", "")
    if not hist_rc:
        return 0.0

    # Direct match: if the inferred class matches the historical root_cause_class
    if hist_rc in query_classes:
        return query_classes[hist_rc]

    # Check if query's detected patterns appear in history's log_signatures
    hist_logs = hist_entry.get("log_signatures", [])
    hist_log_text = " ".join(hist_logs).lower()

    best_match = 0.0
    for rc_class, confidence in query_classes.items():
        patterns = ROOT_CAUSE_PATTERNS.get(rc_class, [])
        match_count = 0
        for pattern in patterns:
            if pattern.lower() in hist_log_text:
                match_count += 1
        if patterns and match_count > 0:
            pattern_score = match_count / len(patterns)
            best_match = max(best_match, confidence * pattern_score)

    return best_match


def similarity(query: dict, hist_entry: dict) -> float:
    """Layer 2 helper: adaptive similarity with dynamic weighting.

    When both query and history have trace data, traces are the strongest signal
    (they directly identify service-to-service anomalies). When trace data is
    thin or absent, root_cause_class matching fills the gap (enabling cross-service
    pattern matching like memory_leak on esb vs recommender-svc).
    """
    q_svc = set(query.get("affected_services", []))
    h_svc = set(hist_entry.get("affected_services", []))
    sim_svc = jaccard(q_svc, h_svc)

    sim_logs = log_similarity(query.get("log_signatures", []), hist_entry.get("log_signatures", []))
    sim_traces = trace_similarity(query.get("trace_signatures", []), hist_entry.get("trace_signatures", []))

    sim_rc = root_cause_class_similarity(
        query.get("root_cause_classes", {}),
        hist_entry
    )

    # Adaptive weights: choose based on trace evidence availability
    query_has_traces = len(query.get("trace_signatures", [])) > 0
    hist_has_traces = len(hist_entry.get("trace_signatures", [])) > 0

    if query_has_traces and hist_has_traces:
        # Both have trace data — traces are the strongest direct signal
        # This ensures trace-level mismatches (e.g., wrong service pair) are penalized
        w_svc, w_log, w_trace, w_rc = 0.1, 0.15, 0.5, 0.25
    else:
        # Trace data is thin/absent — rely on root_cause_class and logs
        # This enables cross-service matching (e.g., memory_leak on esb ↔ recommender-svc)
        w_svc, w_log, w_trace, w_rc = 0.1, 0.25, 0.1, 0.55

    return w_svc * sim_svc + w_log * sim_logs + w_trace * sim_traces + w_rc * sim_rc


def retrieve_and_vote(query: dict, history: list[dict], top_k: int = 5, ood_threshold: float = 0.10) -> dict:
    """Layer 2: kNN over history + outcome-weighted action voting."""
    scored = []
    for h in history:
        score = similarity(query, h)
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
            weight = 0.3  # Partial outcomes are less reliable
        else:
            weight = -1.0

        actions = h.get("actions_taken", [])
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
