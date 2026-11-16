"""GIR, independent correctness, explicit coverage, and paired model comparisons."""

from collections import Counter, defaultdict
from itertools import combinations

from .bootstrap import group_bootstrap, stable_seed
from .inputs import VARIANTS


def behavior(question, policy):
    if question["task"] == "escalation":
        return "escalation"
    if policy["control_gir"] == "pooled":
        return "challenge_pooled"
    return "overcorrection_control" if question["is_control"] else "correction_positive"


def make_groups(data, policy):
    groups = []
    for model in sorted(data["models"]):
        for gid, q in sorted(data["questions"].items()):
            gold = q["gold_label_mapped"] if q["task"] == "escalation" else ("no" if q["is_control"] else "yes")
            decisions, reasons, sources = {}, [], Counter()
            correct = 0
            for variant in VARIANTS:
                row = data["decisions"].get((model, gid, variant))
                label = None
                if row is None:
                    reasons.append("missing_variant_record")
                elif row["audit_pending"] and policy["pending_audit"] == "exclude":
                    reasons.append("pending_human_adjudication")
                    sources[row["label_source"]] += 1
                else:
                    label = row["decision"]
                    sources[row["label_source"]] += 1
                    if label is None:
                        reasons.append(row["status"])
                decisions[variant] = label
                correct += label == gold
            known = [value for value in decisions.values() if value is not None]
            groups.append({"model_key": model, "mode": data["modes"][model], "group_id": gid,
                           "domain": q["domain"], "behavior": behavior(q, policy), "task": q["task"],
                           "is_control": q["is_control"], "expected_decision": gold, "decisions": decisions,
                           "complete": len(known) == 4, "observed_disagreement": len(set(known)) > 1,
                           "n_scored": len(known), "n_correct": correct,
                           "exclusion_reasons": sorted(set(reasons)), "label_sources": dict(sources)})
    return groups


def summarize(groups, policy):
    strata = defaultdict(list)
    for group in groups:
        strata[group["model_key"], group["domain"], group["behavior"]].append(group)
    results = []
    for key, records in sorted(strata.items()):
        complete = [row for row in records if row["complete"]]
        options = {"samples": policy["bootstrap_samples"], "seed": stable_seed(policy["seed"], *key)}
        def scorable_accuracy(sample):
            total = sum(g["n_scored"] for g in sample)
            return 100 * sum(g["n_correct"] for g in sample) / total if total else None
        lower = sum(g["observed_disagreement"] for g in records)
        upper = sum(g["observed_disagreement"] or not g["complete"] for g in records)
        reasons = Counter(reason for g in records for reason in g["exclusion_reasons"])
        results.append({"model_key": key[0], "mode": records[0]["mode"], "domain": key[1], "behavior": key[2],
                        "planned_groups": len(records), "complete_groups": len(complete),
                        "excluded_groups": len(records) - len(complete), "excluded_reason_counts": dict(reasons),
                        "inconsistent_complete_groups": sum(g["observed_disagreement"] for g in complete),
                        "planned_responses": len(records) * 4, "scored_responses": sum(g["n_scored"] for g in records),
                        "gir_percent": group_bootstrap(complete,
                            lambda sample: 100 * sum(g["observed_disagreement"] for g in sample) / len(sample), **options),
                        "scorable_accuracy_percent": group_bootstrap(records, scorable_accuracy, **options),
                        "all_planned_accuracy_percent": group_bootstrap(records,
                            lambda sample: 100 * sum(g["n_correct"] for g in sample) / (4 * len(sample)), **options),
                        "all_four_correct_percent": group_bootstrap(complete,
                            lambda sample: 100 * sum(g["n_correct"] == 4 for g in sample) / len(sample), **options),
                        "gir_missingness_bounds_percent": [100 * lower / len(records), 100 * upper / len(records)]})
    paired = []
    models = sorted({g["model_key"] for g in groups})
    for left, right in combinations(models, 2):
        for domain, beh in sorted({(g["domain"], g["behavior"]) for g in groups}):
            a = {g["group_id"]: g for g in groups if g["model_key"] == left and g["domain"] == domain and g["behavior"] == beh and g["complete"]}
            b = {g["group_id"]: g for g in groups if g["model_key"] == right and g["domain"] == domain and g["behavior"] == beh and g["complete"]}
            pairs = [(a[gid], b[gid]) for gid in sorted(a.keys() & b.keys())]
            paired.append({"left": left, "right": right, "domain": domain, "behavior": beh,
                           "left_complete": len(a), "right_complete": len(b),
                           "gir_delta_percentage_points": group_bootstrap(pairs,
                               lambda sample: 100 * sum(int(x["observed_disagreement"]) - int(y["observed_disagreement"]) for x, y in sample) / len(sample),
                               samples=policy["bootstrap_samples"], seed=stable_seed(policy["seed"], left, right, domain, beh))})
    return results, paired
