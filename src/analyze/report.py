"""Offline analysis orchestration, private artifacts, tables and optional Matplotlib figures."""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from . import ANALYSIS_VERSION
from .agreement import analyze_agreement, categorical_agreement
from .gir import INVALIDATED, make_groups, pooled_secondary, summarize
from .inputs import (CARE_LABELS, canonical, load_annotations, load_data, policy_from, provenance, require)


def write_text(path, content):
    with Path(path).open("x", encoding="utf-8", newline="") as stream:
        stream.write(content)


def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def write_rows(path, rows, fields=None):
    path = Path(path)
    with path.open("x", encoding="utf-8", newline="") as stream:
        if path.suffix == ".csv":
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        else:
            for row in rows:
                stream.write(canonical(row) + "\n")


def create_private_directory(out):
    out = Path(out)
    require(not out.exists(), "Output destination exists; choose a new analysis directory")
    out.mkdir(parents=True, exist_ok=False)
    # Local self-ignore protects generated results without changing the shared project .gitignore.
    write_text(out / ".gitignore", "*\n")
    return out


def supplementary(data):
    annotations = load_annotations(data, "consensus")
    grouped = defaultdict(list)
    for row in data["by_id"].values():
        grouped[row["model_key"], row["domain"]].append(row)
    flags = []
    for (model, domain), rows in sorted(grouped.items()):
        values = {}
        for field in ("correction_observed", "overcorrection_observed", "accommodation_observed", "response_conflict_flag"):
            counts = Counter(annotations[r["response_id"]].get(field, "") for r in rows
                             if r["status"] == "scored" and r["mode"] == "chat" and not r["audit_pending"]
                             and r["response_id"] in annotations)
            n = counts["yes"] + counts["no"]
            values[field] = {"yes": counts["yes"], "no": counts["no"], "unclear": counts["unclear"],
                             "n_binary": n, "percent_yes": 100 * counts["yes"] / n if n else None}
        triage = [(data["questions"][r["group_id"]]["gold_label_mapped"], r["decision"]) for r in rows
                  if r["task"] == "escalation" and r["status"] == "scored" and not r["audit_pending"]]
        controls = [r for r in rows if r["is_control"] and r["status"] == "scored" and not r["audit_pending"]]
        flags.append({"model_key": model, "domain": domain, "mode": rows[0]["mode"], "text_flags": values,
                      "control_decision_challenges": {"yes": sum(r["decision"] == "yes" for r in controls), "n_scored": len(controls)},
                      "triage_vs_source_gold": categorical_agreement(triage, CARE_LABELS) if triage else None})
    return flags


def error_records(data, groups):
    rows = []
    for group in groups:
        for variant, label in group["decisions"].items():
            source = data["decisions"].get((group["model_key"], group["group_id"], variant))
            invalidated = variant in group["invalidated_variants"]
            if invalidated or label is None or label != group["expected_decision"] or (group["complete"] and group["observed_disagreement"]):
                rows.append({"model_key": group["model_key"], "group_id": group["group_id"], "variant_id": variant,
                             "response_id": source["response_id"] if source else None, "decision": label,
                             "source_gold_decision": group["expected_decision"], "status": source["status"] if source else "missing_variant_record",
                             "audit_pending": source["audit_pending"] if source else False,
                             "invalidated_paraphrase": invalidated,
                             "group_inconsistent": group["complete"] and group["observed_disagreement"]})
    return rows


def fmt(value, digits=1):
    return "NA" if value is None else f"{value:.{digits}f}"


def interval(metric):
    return f"{fmt(metric['estimate'])} [{fmt(metric['ci95'][0])}, {fmt(metric['ci95'][1])}]"


def markdown(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def summary_table(summary):
    table = []
    for row in summary:
        flat = {k: row[k] for k in ("model_key", "mode", "domain", "behavior", "planned_groups", "complete_groups", "excluded_groups",
                                    "invalidated_paraphrase_groups", "inconsistent_complete_groups", "planned_responses", "scored_responses")}
        for field in ("gir_percent", "scorable_accuracy_percent", "all_planned_accuracy_percent", "all_four_correct_percent"):
            flat[field] = row[field]["estimate"]
            flat[field + "_ci_low"], flat[field + "_ci_high"] = row[field]["ci95"]
        flat["gir_missingness_lower"], flat["gir_missingness_upper"] = row["gir_missingness_bounds_percent"]
        table.append(flat)
    return table


def write_tables(result, groups, out):
    table = summary_table(result["summary"])
    write_rows(out / "summary.csv", table, list(table[0]))
    if result["secondary_pooled"] and result["secondary_pooled"]["summary"]:
        pooled = [{"analysis_tier": "secondary_pooled", **row} for row in summary_table(result["secondary_pooled"]["summary"])]
        write_rows(out / "secondary_pooled_summary.csv", pooled, list(pooled[0]))
    group_table = [{**g, "decisions": canonical(g["decisions"]), "exclusion_reasons": ";".join(g["exclusion_reasons"]),
                    "invalidated_variants": ";".join(g["invalidated_variants"]),
                    "label_sources": canonical(g["label_sources"])} for g in groups]
    write_rows(out / "groups.csv", group_table, list(group_table[0]))
    agreements, recalls, matrices = [], [], []
    for comparison in ("human_human", "mapper_human"):
        for task, row in result["agreement"][comparison].items():
            agreements.append({"comparison": comparison, "task": task, "n_pairs": row["n_pairs"],
                               "agreement": row["agreement"], "cohen_kappa": row["cohen_kappa"], "note": row["kappa_note"]})
            for label, recall in row["class_recall"].items():
                recalls.append({"comparison": comparison, "task": task, "class": label,
                                "reference_n": row["reference_counts"].get(label, 0), "recall": recall})
            for truth, predictions in row["confusion_matrix"].items():
                for predicted, count in predictions.items():
                    matrices.append({"comparison": comparison, "task": task, "reference": truth, "prediction": predicted, "n": count})
    write_rows(out / "agreement.csv", agreements, ("comparison", "task", "n_pairs", "agreement", "cohen_kappa", "note"))
    write_rows(out / "class_recall.csv", recalls, ("comparison", "task", "class", "reference_n", "recall"))
    write_rows(out / "agreement_confusion.csv", matrices, ("comparison", "task", "reference", "prediction", "n"))


def summary_lines(summary):
    lines = ["| Model (mode) | Domain / behavior | Complete/planned | GIR | Scorable accuracy | All-planned accuracy |",
             "|---|---|---:|---:|---:|---:|"]
    for row in summary:
        lines.append(f"| {markdown(row['model_key'])} ({row['mode'] or 'unknown'}) | {row['domain']} / {row['behavior']} | "
                     f"{row['complete_groups']}/{row['planned_groups']} | {interval(row['gir_percent'])} | "
                     f"{interval(row['scorable_accuracy_percent'])} | {interval(row['all_planned_accuracy_percent'])} |")
    return lines


def write_markdown(result, out):
    lines = [f"# {markdown(result['study_label'])}", "", f"**{result['status']}**", "",
             "## Consistency and correctness (primary)", "",
             "Percentages with question-group bootstrap 95% CIs. NA means undefined, not zero. Correction positives",
             "(`correction_positive`) and controls (`overcorrection_control`) are always reported separately.", "",
             *summary_lines(result["summary"])]
    lines += ["", "GIR uses complete original/p1/p2/p3 groups. Originals-only runs cannot establish four-wording consistency.",
              "Scorable accuracy excludes unknown decisions; all-planned accuracy counts missing/unusable slots as unsuccessful.",
              "The two accuracy measures and GIR have different denominators. Missingness sensitivity bounds in metrics.json",
              "are not confidence intervals. They retain observed disagreement even within incomplete groups.", ""]
    if result["invalidated_paraphrases"]:
        lines += ["## Invalidated paraphrases", "",
                  "Paraphrases found invalid after collection. Their groups are excluded from primary GIR for every model",
                  f"(reason `{INVALIDATED}`), even when all four decisions exist. The collected records are kept, and no",
                  "replacement paraphrase was substituted after results were seen.", "",
                  "| Group | Variant | Reason | Invalidated by |", "|---|---|---|---|"]
        lines += [f"| {markdown(r['group_id'])} | {r['variant_id']} | {markdown(r['reason'])} | {markdown(r['invalidated_by'])} |"
                  for r in result["invalidated_paraphrases"]]
        lines.append("")
    if result["secondary_pooled"]:
        lines += ["## Secondary: pooled correction GIR (positives + controls)", "",
                  "Requested with `control_gir=pooled`. **Secondary result only**: it does not replace the separate primary",
                  "positive and control results above. Same complete-group and invalidation rules.", "",
                  *summary_lines(result["secondary_pooled"]["summary"]), ""]
    lines += ["## Agreement and mapper validation", "",
              "Human-human kappa uses raw independent decisions before adjudication. Mapper reference is human consensus or",
              "third-person adjudication, never source clinical gold. Missing/unscorable mapper predictions count as misses",
              "for scorable human classes. An absent reference class has NA recall, not 100%.", "",
              "| Task | Human pairs | Human agreement | Human kappa | Mapper-human kappa | Mapper class recall |",
              "|---|---:|---:|---:|---:|---|"]
    for task, h in result["agreement"]["human_human"].items():
        m = result["agreement"]["mapper_human"][task]
        recall = ", ".join(f"{label}: {fmt(value * 100) if value is not None else 'NA'}" for label, value in m["class_recall"].items())
        lines.append(f"| {task} | {h['n_pairs']} | {fmt(h['agreement']*100) if h['agreement'] is not None else 'NA'} | "
                     f"{fmt(h['cohen_kappa'],3)} | {fmt(m['cohen_kappa'],3)} | {recall} |")
    lines += ["", "## Diagnostics and readiness", ""]
    lines += [f"- {markdown(issue)}" for issue in result["diagnostics"]] or ["- No mechanical input/reliability issues found; scientific approval remains a team responsibility."]
    lines += ["", "## Methods and limits", "",
              f"- Run: `{markdown(result['run_id'])}`; analysis version: `{ANALYSIS_VERSION}`.",
              f"- Policy: `{result['policy']['policy_version']}`; control GIR: `{result['policy']['control_gir']}` (positives and controls are always separate in primary results; `pooled` adds a secondary pooled result); pending audits: `{result['policy']['pending_audit']}`.",
              f"- Group-bootstrap replicates: {result['policy']['bootstrap_samples']}; seed: {result['policy']['seed']}.",
              f"- Agreement adapter: {markdown(result['agreement']['adapter'])}.",
              "- Entire questions, with all variants, are resampled. Paired model deltas use the jointly complete intersection and matched-question resampling.",
              "- A positive paired GIR delta means more inconsistency in the left model. No significance or multiple-comparison conclusion is implied.",
              "- Jev/direct-choice stability is a different interface from behavior expressed in a chat answer. Text-only scores are unavailable for direct-choice models.",
              "- Source-label accuracy and agreement of the response scorer measure different things. Consistency alone does not establish clinical correctness or safety.",
              "- Single-question/constant samples can have zero-width intervals. All-one-class kappa can be undefined. Small pilots are integration checks.",
              "- Token-cap flags are retained; no long answer is silently dropped. Final denominator/control/token policies need the team's agreement.",
              "- Outputs, including provenance and response IDs, are local/private by default. Review selected aggregates before publication.", "",
              "## Artifacts", "", "- summary.csv (primary), groups.csv, metrics.json and provenance.json",
              *(["- secondary_pooled_summary.csv (secondary pooled correction GIR)"] if result["secondary_pooled"] else []),
              "- agreement.csv, class_recall.csv, agreement_confusion.csv",
              "- excluded_groups.jsonl, error_analysis.jsonl and mapper_errors.jsonl"]
    if result["plots_requested"]:
        lines += ["- [Consistency and accuracy](consistency_accuracy.png)", "- [Agreement confusion matrices](agreement_confusion.png)",
                  "- SVG versions are also included for vector export."]
    write_text(out / "report.md", "\n".join(lines) + "\n")


def plot_figures(result, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rows = result["summary"]
    with plt.rc_context({"font.size": 9}):
        figure, axes = plt.subplots(1, 2, figsize=(16, max(5, .43 * len(rows))), sharey=True)
        labels = [f"{r['model_key']} [{r['mode'] or 'unknown'}] / {r['domain']} / {r['behavior']}" for r in rows]
        for axis, field, title, color in zip(axes, ("gir_percent", "scorable_accuracy_percent"), ("GIR (%)", "Scorable accuracy (%)"), ("#2563eb", "#047857")):
            for y, row in enumerate(rows):
                metric = row[field]
                if metric["estimate"] is None:
                    axis.text(2, y, "NA", va="center", color="#64748b")
                else:
                    lo, hi = metric["ci95"]
                    if lo is not None:
                        axis.hlines(y, lo, hi, color=color, linewidth=2)
                    axis.plot(metric["estimate"], y, "o", color=color)
            axis.set_xlim(-3, 103)
            axis.set_xlabel(title + " — group-bootstrap 95% CI")
            axis.grid(axis="x", alpha=.2)
        axes[0].set_yticks(range(len(rows)), [f"{label} (GIR-complete {r['complete_groups']}/{r['planned_groups']})" for label, r in zip(labels, rows)])
        axes[0].invert_yaxis()
        figure.suptitle(result["study_label"] + "\nExploratory — consistency and accuracy are separate measures", fontsize=12)
        figure.tight_layout()
        for extension in ("png", "svg"):
            figure.savefig(out / ("consistency_accuracy." + extension), dpi=180, bbox_inches="tight")
        plt.close(figure)
        tasks = list(result["agreement"]["human_human"])
        figure, axes = plt.subplots(2, len(tasks), figsize=(7 * len(tasks), 11), squeeze=False)
        for row_index, comparison in enumerate(("human_human", "mapper_human")):
            for column, task in enumerate(tasks):
                axis = axes[row_index][column]
                stat = result["agreement"][comparison][task]
                labels = list(stat["confusion_matrix"])
                values = [[stat["confusion_matrix"][truth][prediction] for prediction in labels] for truth in labels]
                axis.imshow(values, cmap="Blues", vmin=0)
                axis.set_xticks(range(len(labels)), labels, rotation=35, ha="right")
                axis.set_yticks(range(len(labels)), labels)
                axis.set_title(f"{comparison} / {task} / n={stat['n_pairs']}")
                axis.set_xlabel("Comparison label")
                axis.set_ylabel("Reference: raw human 1" if comparison == "human_human" else "Reference: human consensus")
                maximum = max((max(row) for row in values), default=0)
                for y, row in enumerate(values):
                    for x, value in enumerate(row):
                        axis.text(x, y, str(value), ha="center", va="center", color="white" if value > maximum / 2 else "black")
        figure.suptitle(result["study_label"] + "\nExploratory response-label agreement — counts, not clinical gold accuracy", fontsize=11)
        figure.tight_layout()
        for extension in ("png", "svg"):
            figure.savefig(out / ("agreement_confusion." + extension), dpi=180, bbox_inches="tight")
        plt.close(figure)


def analyze(annotations, questions, out, models=None, scoring_work=None, policy_file=None, plots=False,
            study_label="Healthcare consistency analysis — exploratory", bootstrap_samples=None, seed=None,
            control_gir=None, pending_audit=None, invalidations=None):
    policy = policy_from(policy_file, {"bootstrap_samples": bootstrap_samples, "seed": seed,
                                     "control_gir": control_gir, "pending_audit": pending_audit})
    require(not Path(out).exists(), "Output destination exists; choose a new analysis directory")
    if plots:
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            raise ValueError("Plots require: python3 -m pip install -r requirements-analysis.txt") from None
    data = load_data(annotations, questions, models, scoring_work, invalidations)
    if policy_file:
        data["files"].append(Path(policy_file))
    groups = make_groups(data, policy)
    summary, paired = summarize(groups, policy)
    secondary = pooled_secondary(groups, policy) if policy["control_gir"] == "pooled" else None
    invalidated = sorted(data["invalidations"].values(), key=lambda r: (r["group_id"], r["variant_id"]))
    invalidated_groups = sum(bool(g["invalidated_variants"]) for g in groups)
    agreement = analyze_agreement(data, policy)
    extras = supplementary(data)
    diagnostics = list(agreement["issues"])
    absent_slots = len(data["models"]) * len(data["questions"]) * 4 - len(data["by_id"])
    if absent_slots:
        diagnostics.append(f"{absent_slots} planned original/p1/p2/p3 slots have no decision record")
    explicit_missing = sum(d["status"] == "missing_response" for d in data["by_id"].values())
    if explicit_missing:
        diagnostics.append(f"{explicit_missing} decision records explicitly report missing runner responses")
    if any(not row["complete_groups"] for row in summary):
        diagnostics.append("One or more strata have no complete four-wording groups; their GIR is NA")
    if {row["variant_id"] for row in data["by_id"].values()} == {"original"}:
        diagnostics.append("Originals-only inputs: all-planned accuracy uses the four-wording grid; scorable accuracy describes the collected originals")
    if any(row["complete_groups"] == 1 for row in summary):
        diagnostics.append("One or more GIR intervals use a single group and are degenerate")
    pending = sum(d["audit_pending"] for d in data["by_id"].values())
    if pending:
        diagnostics.append(f"{pending} decisions have pending human audits; policy={policy['pending_audit']}")
    if any(d.get("target_context") == "question_only_calibration" for d in data["by_id"].values() if d["task"] == "correction"):
        diagnostics.append("Question-only target inference still needs clinical target-alignment review")
    if invalidated:
        diagnostics.append(f"{len(invalidated)} paraphrases invalidated after collection; {invalidated_groups} model-groups "
                           f"excluded from primary GIR ({INVALIDATED}); original records retained, no substitutes")
    if secondary:
        diagnostics.append("Pooled correction GIR requested: reported as a SECONDARY result; primary positives/controls stay separate")
    cap_count = sum(d["over_visible_cap"] is True for d in data["by_id"].values())
    if cap_count:
        diagnostics.append(f"{cap_count} over-visible-cap responses retained; agree token-cap policy")
    if data["scoring_summary"] and data["scoring_summary"].get("issues"):
        diagnostics += ["Scoring export: " + str(issue) for issue in data["scoring_summary"]["issues"]]
    result = {"analysis_version": ANALYSIS_VERSION, "study_label": study_label,
              "status": "EXPLORATORY / NOT VALIDATED STUDY FINDINGS", "run_id": data["run_id"],
              "guide_version": data["guide_version"], "policy": policy, "plots_requested": plots,
              "summary": summary, "paired_model_comparisons": paired, "secondary_pooled": secondary,
              "invalidated_paraphrases": invalidated, "agreement": agreement,
              "supplementary": extras, "diagnostics": sorted(set(diagnostics)),
              "coverage": {"planned_responses": len(groups) * 4, "decision_records": len(data["by_id"]),
                           "slot_definition": "Four wording slots per declared model/question; originals-only runs intentionally fill one",
                           "scoring_export_planned_responses": data["scoring_summary"].get("planned_responses") if data["scoring_summary"] else None,
                           "missing_variant_records": absent_slots, "explicit_missing_response_records": explicit_missing,
                           "planned_model_groups": len(groups), "complete_model_groups": sum(g["complete"] for g in groups),
                           "invalidated_paraphrase_model_groups": invalidated_groups},
              "scientific_approval_note": "Policy defaults are proposed operational choices; this tool does not certify clinical validity or team approval."}
    output = create_private_directory(out)
    write_json(output / "metrics.json", result)
    write_rows(output / "group_metrics.jsonl", groups)
    write_rows(output / "excluded_groups.jsonl", [g for g in groups if not g["complete"]])
    write_rows(output / "error_analysis.jsonl", error_records(data, groups))
    write_rows(output / "mapper_errors.jsonl", agreement["mapper_errors"])
    write_json(output / "provenance.json", {"analysis_version": ANALYSIS_VERSION, "policy": policy, "inputs": provenance(data["files"])})
    write_tables(result, groups, output)
    write_markdown(result, output)
    if plots:
        plot_figures(result, output)
    return {"output": str(output.resolve()), "status": result["status"], "coverage": result["coverage"],
            "reporting_strata": len(summary), "secondary_pooled_strata": len(secondary["summary"]) if secondary else 0,
            "review_expansion_required": agreement["review_expansion_required"], "diagnostics": result["diagnostics"]}
