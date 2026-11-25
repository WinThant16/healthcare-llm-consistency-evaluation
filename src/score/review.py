"""Independent human packets, cumulative stratified review, and immutable adjudication."""

import math
import random
from collections import defaultdict
from pathlib import Path

from .inputs import load_state
from .schema import (CONTEXT_FIELDS, LABEL_FIELDS, OBSERVATION_FIELDS, PACKET_FIELDS, read_json, read_rows,
                     require, sha256, text_value, unique, validate_observation, write_json, write_rows)


def plans(work):
    return [read_json(p) for p in sorted((Path(work) / "private/review_plans").glob("round_*.json"))]


def export_review(work, out, reviewers=None, fraction=1., seed=566, expand=False):
    require(isinstance(fraction, (int, float)) and math.isfinite(fraction) and 0 < fraction <= 1, "fraction must be in (0,1]")
    state = load_state(work)
    previous = plans(work)
    if expand:
        require(previous, "No initial review selection to expand")
        reviewers, seed = previous[0]["reviewers"], previous[0]["seed"]
    else:
        require(not previous, "Review already selected; use review-expand rather than redraw it")
    require(reviewers and len(reviewers) == len(set(reviewers)) == 2, "Supply two different reviewer IDs")
    for reviewer in reviewers:
        text_value(reviewer, "reviewer ID")
    require(not Path(out).exists(), "Reviewer output directory exists")
    ctx = {c["blind_id"]: c for c in state["contexts"]}
    require(ctx, "No usable chat responses need human review; decision-only models skip this step")
    cross = {r["blind_id"]: r["response_id"] for r in state["crosswalk"]}
    expected = {r["response_id"]: r for r in state["planned"]}
    questions = {q["group_id"]: q for q in state["questions"]}
    strata = defaultdict(list)
    for bid in ctx:
        plan = expected[cross[bid]]
        q = questions[plan["group_id"]]
        strata[(plan["model_key"], q["domain"], q["gold_label_mapped"])].append(bid)
    selected, counts = [], []
    for key, ids in sorted(strata.items()):
        ordered = sorted(ids, key=lambda bid: sha256([seed, cross[bid]]))
        n = math.ceil(fraction * len(ordered))
        selected.extend(ordered[:n])
        counts.append({"stratum": list(key), "available": len(ids), "selected": n})
    old = set(previous[-1]["selected_all"]) if previous else set()
    require(old <= set(selected), "Expansion cannot remove earlier selected responses")
    new = sorted(set(selected) - old)
    require(new, "No new responses at this fraction; increase it")
    plan = {"round": len(previous) + 1, "reviewers": list(reviewers), "seed": seed, "fraction": fraction,
            "selected_all": sorted(selected), "selected_new": new, "strata": counts}
    for index, reviewer in enumerate(reviewers, 1):
        ids = list(new)
        random.Random(seed + index).shuffle(ids)
        rows = [{**ctx[bid], "reviewer_id": reviewer, **dict.fromkeys(OBSERVATION_FIELDS, ""),
                 "guide_version": state["guide_version"]} for bid in ids]
        write_rows(Path(out) / f"reviewer_{index}.csv", rows, PACKET_FIELDS)
    write_json(Path(work) / f"private/review_plans/round_{len(previous)+1:03d}.json", plan)
    return {"new_packet_rows_per_reviewer": len(new), "total_selected": len(selected), "available": len(ctx), "fraction": fraction}


def raw_reviews(work):
    state = load_state(work)
    selection = plans(work)
    if not selection:
        return {}, {}
    ctx = {c["blind_id"]: c for c in state["contexts"]}
    maps = []
    for index in (1, 2):
        collected = {}
        for path in sorted((Path(work) / f"private/reviews/reviewer_{index}").glob("*.jsonl")):
            for row in read_rows(path):
                bid = row["blind_id"]
                require(bid in selection[-1]["selected_all"], "Unknown stored review ID")
                require(row["reviewer_id"] == selection[0]["reviewers"][index-1], "Stored reviewer identity changed")
                require(row["guide_version"] == state["guide_version"], "Stored review guide changed")
                validate_observation(row["observation"], ctx[bid])
                require(bid not in collected or collected[bid] == row, "Conflicting raw review submissions")
                collected[bid] = row
        maps.append(collected)
    return tuple(maps)


def import_review(work, source):
    state = load_state(work)
    selection = plans(work)
    require(selection, "Export reviewer packets first")
    ctx = {c["blind_id"]: c for c in state["contexts"]}
    rows = read_rows(source)
    by_id = unique(rows, lambda row: row["blind_id"], "review blind_id")
    require(any(set(by_id) == set(p["selected_new"]) for p in selection), "Submit one complete exported packet; no missing/extra rows")
    actors = {row.get("reviewer_id") for row in rows}
    require(len(actors) == 1 and next(iter(actors)) in selection[0]["reviewers"], "Unexpected reviewer ID")
    actor = next(iter(actors))
    index = selection[0]["reviewers"].index(actor) + 1
    stored = raw_reviews(work)[index-1]
    clean = []
    for bid, row in by_id.items():
        require(set(row) == set(PACKET_FIELDS), "Packet columns changed")
        require(all(row[k] == ctx[bid][k] for k in CONTEXT_FIELDS), "Supplied response/question context was edited")
        require(row["guide_version"] == state["guide_version"], "Guide version changed")
        observation = validate_observation({k: row[k] for k in OBSERVATION_FIELDS}, ctx[bid])
        record = {"blind_id": bid, "reviewer_id": actor, "guide_version": state["guide_version"], "observation": observation}
        require(bid not in stored or stored[bid] == record, "Independent submission already stored; use adjudication, never overwrite")
        clean.append(record)
    clean.sort(key=lambda row: row["blind_id"])
    dest = Path(work) / f"private/reviews/reviewer_{index}/batch_{sha256(clean)}.jsonl"
    if not dest.exists():
        write_rows(dest, clean)
    return {"reviewer_id": actor, "rows": len(clean)}


def adjudicated(work):
    rows = [r for p in sorted((Path(work) / "private/adjudications").glob("*.jsonl")) for r in read_rows(p)]
    return unique(rows, lambda row: row["blind_id"], "adjudication")


def disagreement_rows(work):
    state = load_state(work)
    selection = plans(work)
    require(selection, "No review sample")
    first, second = raw_reviews(work)
    expected = set(selection[-1]["selected_all"])
    require(first.keys() == second.keys() == expected, "Both reviewers must finish every selected packet before comparison")
    ctx = {c["blind_id"]: c for c in state["contexts"]}
    resolved = adjudicated(work)
    rows = []
    for bid in sorted(expected):
        fields = [f for f in LABEL_FIELDS if first[bid]["observation"][f] != second[bid]["observation"][f]]
        if fields and bid not in resolved:
            rows.append({"context": ctx[bid], "reviewer_1": first[bid], "reviewer_2": second[bid],
                         "differing_fields": fields, "adjudicator_id": "", "adjudication_reason": "",
                         "resolution": dict.fromkeys(OBSERVATION_FIELDS, "")})
    return rows


def compare(work, out):
    rows = disagreement_rows(work)
    write_rows(out, rows)
    first, second = raw_reviews(work)
    raw_disagreements = sum(any(first[bid]["observation"][f] != second[bid]["observation"][f] for f in LABEL_FIELDS)
                            for bid in first)
    return {"paired_responses": len(first), "raw_disagreements": raw_disagreements,
            "unresolved_disagreements": len(rows), "note": "Kappa/recall belong to src/analyze; raw decisions are preserved."}


def import_adjudication(work, source):
    selection = plans(work)
    require(selection, "No independent reviews")
    expected = {r["context"]["blind_id"]: r for r in disagreement_rows(work)}
    rows = read_rows(source)
    by_id = unique(rows, lambda row: row["context"]["blind_id"], "adjudication blind_id")
    require(expected and set(by_id) == set(expected), "Resolve all currently exported disagreements, exactly once")
    clean = []
    for bid, row in by_id.items():
        for field in ("context", "reviewer_1", "reviewer_2", "differing_fields"):
            require(row.get(field) == expected[bid][field], "Adjudication context/raw review changed")
        actor = text_value(row.get("adjudicator_id"), "adjudicator_id")
        require(actor not in selection[0]["reviewers"], "Adjudicator must be a third person")
        reason = text_value(row.get("adjudication_reason"), "adjudication_reason")
        observation = validate_observation(row.get("resolution"), row["context"])
        clean.append({"blind_id": bid, "reviewer_id": actor, "adjudication_reason": reason,
                      "guide_version": expected[bid]["reviewer_1"]["guide_version"], "observation": observation})
    clean.sort(key=lambda row: row["blind_id"])
    write_rows(Path(work) / f"private/adjudications/batch_{sha256(clean)}.jsonl", clean)
    return {"adjudicated": len(clean)}


def consensus(work):
    state = load_state(work)
    ctx = {c["blind_id"]: c for c in state["contexts"]}
    selection = plans(work)
    first, second = raw_reviews(work)
    resolved = adjudicated(work)
    final, pending = {}, []
    for bid, row in resolved.items():
        require(bid in ctx, "Unknown stored adjudication")
        require(selection and row["reviewer_id"] not in selection[0]["reviewers"], "Invalid stored adjudicator")
        require(row["guide_version"] == state["guide_version"], "Stored adjudication guide changed")
        validate_observation(row["observation"], ctx[bid])
    for bid in (selection[-1]["selected_all"] if selection else []):
        if bid in resolved:
            final[bid] = {**resolved[bid], "label_source": "human_adjudicated"}
        elif bid in first and bid in second and all(first[bid]["observation"][f] == second[bid]["observation"][f] for f in LABEL_FIELDS):
            final[bid] = {**first[bid], "reviewer_id": ";".join(selection[0]["reviewers"]), "label_source": "human_agreed"}
        else:
            pending.append(bid)
    return final, pending
