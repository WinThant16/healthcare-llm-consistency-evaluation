"""Nominal agreement and mapper validation against independent human observations, never gold."""

from collections import Counter, defaultdict

from .inputs import CARE_LABELS, digest, index, load_annotations, read_json, read_rows, require, text


def categorical_agreement(pairs, required_labels=()):
    pairs = list(pairs)
    for left, right in pairs:
        text(left, "reference label")
        text(right, "comparison label")
    labels = sorted(set(required_labels) | {label for pair in pairs for label in pair})
    matrix = {reference: {predicted: 0 for predicted in labels} for reference in labels}
    for reference, predicted in pairs:
        matrix[reference][predicted] += 1
    n = len(pairs)
    left, right = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    observed = sum(a == b for a, b in pairs) / n if n else None
    chance = sum(left[x] * right[x] for x in labels) / n ** 2 if n else None
    kappa = (observed - chance) / (1 - chance) if n and chance < 1 else None
    return {"n_pairs": n, "agreement": observed, "cohen_kappa": kappa,
            "kappa_note": None if kappa is not None else "Undefined: no pairs or no marginal variation",
            "reference_counts": dict(left), "predicted_counts": dict(right), "confusion_matrix": matrix,
            "class_recall": {label: matrix[label][label] / left[label] if left[label] else None for label in labels}}


def _raw_observation(row, context):
    require(isinstance(row, dict), "Observation must be an object")
    task = context["task"]
    field = "challenge_observed" if task == "correction" else "escalation_observed"
    label = row.get(field)
    require(label in ({"yes", "no", "unscorable"} if task == "correction" else {*CARE_LABELS, "unscorable"}), "Invalid raw primary label")
    acceptance = row.get("acceptance_observed")
    require(acceptance in ({"yes", "no", "unclear"} if task == "correction" else {""}), "Invalid acceptance label")
    require(row.get("response_conflict_flag") in {"yes", "no"}, "Invalid conflict label")
    evidence = row.get("evidence_quote")
    require(isinstance(evidence, str), "Invalid evidence quote")
    if evidence:
        require(evidence in context["response_text"], "Stored evidence quote is not in the response")
    if label != "unscorable":
        require(evidence.strip(), "Scorable raw observation needs evidence")
    else:
        text(row.get("annotation_notes"), "Unscorable explanation")
    return {"label": label, "acceptance": acceptance, "conflict": row["response_conflict_flag"]}


def _from_guide(row, decision):
    task, control = decision["task"], decision["is_control"]
    if task == "escalation":
        label = row.get("escalation_observed")
        require(label in {*CARE_LABELS, "unscorable"}, "Invalid guide escalation label")
    elif control:
        value = row.get("overcorrection_observed")
        require(value in {"yes", "no", "unclear"}, "Invalid guide control label")
        label = "unscorable" if value == "unclear" else value
    else:
        label = row.get("correction_observed")
        require(label in {"yes", "no", "unscorable"}, "Invalid guide correction label")
    require(row.get("response_conflict_flag") in {"yes", "no"}, "Invalid guide conflict flag")
    acceptance = row.get("accommodation_observed", "")
    require(acceptance in {"", "yes", "no", "unclear"}, "Invalid guide accommodation flag")
    return {"label": label, "acceptance": acceptance, "conflict": row["response_conflict_flag"],
            "reviewer_id": row["reviewer_id"]}


def _workspace_observations(data):
    state, root = data["workspace"], data["work_path"]
    require(state["guide_version"] == data["guide_version"], "Workspace/export guide differs")
    contexts = index(state["contexts"], lambda c: c["blind_id"], "blind context")
    cross = index(state["crosswalk"], lambda c: c["blind_id"], "blind crosswalk")
    index(state["crosswalk"], lambda c: c["response_id"], "crosswalk response ID")
    require(set(contexts) == set(cross), "Crosswalk/context IDs differ")
    for bid, context in contexts.items():
        rid = cross[bid]["response_id"]
        require(rid in data["by_id"] and data["by_id"][rid]["mode"] == "chat", "Invalid blind crosswalk response")
        require(context["task"] == data["by_id"][rid]["task"], "Blind task differs from response task")
    plan_paths = sorted((root / "private/review_plans").glob("round_*.json"))
    data["files"] += plan_paths
    plans = [read_json(path) for path in plan_paths]
    selected = set(plans[-1]["selected_all"]) if plans else set()
    require(selected <= set(contexts), "Unknown selected human-review ID")
    actors = plans[0]["reviewers"] if plans else None
    if actors:
        require(len(actors) == 2 and len({a.strip().casefold() for a in actors}) == 2, "Reviewers must be distinct")
    maps = []
    for number in (1, 2):
        result = {}
        paths = sorted((root / f"private/reviews/reviewer_{number}").glob("*.jsonl"))
        data["files"] += paths
        for path in paths:
            for row in read_rows(path):
                bid = row["blind_id"]
                require(bid in selected, "Raw review was not selected")
                require(row["guide_version"] == data["guide_version"] and row["reviewer_id"] == actors[number-1], "Raw reviewer/version changed")
                rid = cross[bid]["response_id"]
                parsed = {**_raw_observation(row["observation"], contexts[bid]), "reviewer_id": row["reviewer_id"]}
                require(rid not in result or result[rid] == parsed, "Conflicting independent reviews")
                result[rid] = parsed
        maps.append(result)
    mapper = {}
    identities = set()
    paths = sorted((root / "private/mapper").glob("*.json"))
    data["files"] += paths
    for path in paths:
        row = read_json(path)
        bid = row["blind_id"]
        require(bid in contexts and path.stem == bid, "Unknown mapper cache ID")
        expected_hash = digest({"context": contexts[bid], "prompt_sha256": state["prompt_sha256"], "guide_version": state["guide_version"]})
        require(row["input_sha256"] == expected_hash and row["prompt_sha256"] == state["prompt_sha256"]
                and row["guide_version"] == state["guide_version"], "Mapper cache is stale")
        identities.add((row["mapper_identity"], row["mapper_model"]))
        mapper[cross[bid]["response_id"]] = _raw_observation(row["observation"], contexts[bid])
    require(len(identities) <= 1, "Mixed mapper versions/settings")
    adjudicated = {}
    paths = sorted((root / "private/adjudications").glob("*.jsonl"))
    data["files"] += paths
    for path in paths:
        for row in read_rows(path):
            bid = row["blind_id"]
            require(bid in selected and row["guide_version"] == state["guide_version"], "Unknown adjudication/version")
            rid = cross[bid]["response_id"]
            require(rid not in adjudicated, "Duplicate adjudication")
            text(row.get("adjudication_reason"), "adjudication reason")
            adjudicated[rid] = {**_raw_observation(row["observation"], contexts[bid]), "reviewer_id": row["reviewer_id"]}
    return mapper, *maps, adjudicated, {cross[bid]["response_id"] for bid in selected}, "raw_scoring_workspace"


def _guide_observations(data):
    sources = [load_annotations(data, name) for name in ("mapper", "reviewer_1", "reviewer_2", "adjudicated")]
    parsed = [{rid: _from_guide(row, data["by_id"][rid]) for rid, row in source.items()} for source in sources]
    selected = set(parsed[1]) | set(parsed[2])
    return *parsed, selected, "guide_export_files (control unclear mapped to unscorable; raw acceptance may be unavailable)"


def analyze_agreement(data, policy):
    mapper, first, second, adjudicated, selected, adapter = (_workspace_observations(data) if data["workspace"] else _guide_observations(data))
    eligible = {rid for rid, d in data["by_id"].items() if d["mode"] == "chat"
                and d["status"] not in {"model_error", "empty_response", "missing_response"}}
    require(selected <= eligible, "Human review includes unusable/non-chat response")
    for rid in first.keys() & second.keys():
        require(first[rid]["reviewer_id"].strip().casefold() != second[rid]["reviewer_id"].strip().casefold(), "Independent reviews have the same actor")
    human, pending = {}, []
    for rid in sorted(selected):
        if rid in adjudicated:
            require(rid in first and rid in second, "Adjudication needs both independent reviews")
            actor = adjudicated[rid]["reviewer_id"].strip().casefold()
            require(actor not in {first[rid]["reviewer_id"].strip().casefold(), second[rid]["reviewer_id"].strip().casefold()}, "Adjudicator must be a third person")
            human[rid] = adjudicated[rid]
        elif rid in first and rid in second and not data["by_id"][rid]["audit_pending"] and all(first[rid][f] == second[rid][f] for f in ("label", "acceptance", "conflict")):
            human[rid] = first[rid]
        else:
            pending.append(rid)
    require(set(adjudicated) <= selected, "Adjudication outside human sample")
    if data["workspace"]:
        # Scorer snapshots can gain new immutable review batches after an export. Do not mix phases.
        for rid in eligible:
            row = data["by_id"][rid]
            require(row["audit_pending"] == (rid in pending), "Scoring export is stale relative to human review; re-export annotations")
            if rid in human:
                expected_label = None if human[rid]["label"] == "unscorable" else human[rid]["label"]
                expected_source = "human_adjudicated" if rid in adjudicated else "human_agreed"
                require(row["decision"] == expected_label and row["label_source"] == expected_source,
                        "Scoring export is stale relative to consensus; re-export annotations")
            elif rid in mapper and not row["audit_pending"]:
                expected_label = None if mapper[rid]["label"] == "unscorable" else mapper[rid]["label"]
                require(row["decision"] == expected_label, "Scoring export is stale relative to mapper; re-export annotations")
    tasks = sorted({q["task"] for q in data["questions"].values()})
    raw_stats, mapper_stats, issues = {}, {}, []
    for task in tasks:
        labels = ("yes", "no") if task == "correction" else CARE_LABELS
        pairs = [(first[rid]["label"], second[rid]["label"]) for rid in sorted(first.keys() & second.keys()) if data["by_id"][rid]["task"] == task]
        raw_stats[task] = categorical_agreement(pairs, labels)
        comparison = [(row["label"], mapper.get(rid, {}).get("label", "missing_mapper"))
                      for rid, row in sorted(human.items()) if data["by_id"][rid]["task"] == task]
        mapper_stats[task] = categorical_agreement(comparison, labels)
        kappa = raw_stats[task]["cohen_kappa"]
        if kappa is None or kappa < policy["kappa_threshold"]:
            issues.append(f"{task}: human-human kappa undefined or below {policy['kappa_threshold']}")
        mapper_kappa = mapper_stats[task]["cohen_kappa"]
        if mapper_kappa is None or mapper_kappa < policy["kappa_threshold"]:
            issues.append(f"{task}: mapper-human kappa undefined or below {policy['kappa_threshold']}")
        for label in labels:
            value = mapper_stats[task]["class_recall"][label]
            if value is None or value < policy["mapper_recall_threshold"]:
                issues.append(f"{task}/{label}: mapper recall undefined or below {policy['mapper_recall_threshold']}")
    strata = defaultdict(lambda: {"eligible": [], "selected": [], "paired": [], "consensus": []})
    for rid in sorted(eligible):
        d = data["by_id"][rid]
        q = data["questions"][d["group_id"]]
        key = (d["model_key"], q["domain"], q["gold_label_mapped"])
        strata[key]["eligible"].append(rid)
        if rid in selected:
            strata[key]["selected"].append(rid)
        if rid in first and rid in second:
            strata[key]["paired"].append(rid)
        if rid in human:
            strata[key]["consensus"].append(rid)
    coverage, stratified = [], []
    for key, counts in sorted(strata.items()):
        n = len(counts["eligible"])
        coverage.append({"model_key": key[0], "domain": key[1], "reference_class": key[2],
                         **{k: len(v) for k, v in counts.items()}, "paired_fraction": len(counts["paired"]) / n})
        if len(counts["paired"]) < n * policy["minimum_audit_fraction"]:
            issues.append(f"{key[0]}/{key[1]}/{key[2]}: paired human coverage below {policy['minimum_audit_fraction']:.0%}")
        raw_pairs = [(first[rid]["label"], second[rid]["label"]) for rid in counts["paired"]]
        map_pairs = [(human[rid]["label"], mapper.get(rid, {}).get("label", "missing_mapper")) for rid in counts["consensus"]]
        stratified.append({"model_key": key[0], "domain": key[1], "reference_class": key[2],
                           "human_human": categorical_agreement(raw_pairs), "mapper_human": categorical_agreement(map_pairs)})
    if not selected:
        issues.append("No independent human sample available")
    if pending:
        issues.append(f"{len(pending)} selected human reviews/adjudications unresolved")
    missing_mapper = sorted(eligible - set(mapper))
    if missing_mapper:
        issues.append(f"{len(missing_mapper)} usable chat responses lack mapper labels")
    errors = [{"response_id": rid, "task": data["by_id"][rid]["task"], "human_reference": row["label"],
               "mapper": mapper.get(rid, {}).get("label", "missing_mapper")}
              for rid, row in sorted(human.items()) if row["label"] != mapper.get(rid, {}).get("label", "missing_mapper")]
    flags = {}
    for field in ("acceptance", "conflict"):
        flags[field] = categorical_agreement([(first[rid][field], second[rid][field])
            for rid in sorted(first.keys() & second.keys()) if first[rid][field] and second[rid][field]])
    return {"adapter": adapter, "human_human": raw_stats, "mapper_human": mapper_stats,
            "stratified": stratified, "auxiliary_human_agreement": flags, "audit_coverage": coverage,
            "mapper_errors": errors, "pending_response_ids": pending, "missing_mapper_response_ids": missing_mapper,
            "review_expansion_required": bool(issues), "issues": sorted(set(issues)),
            "note": "Raw human kappa is pre-adjudication. Mapper reference is human observation, not clinical gold. Required absent classes have null recall."}
