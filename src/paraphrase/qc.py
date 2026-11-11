"""Paraphrase QC report: counts, agreement and check performance. No source or paraphrase text, so the
markdown it writes can be committed (for example to ``report/paraphrase_qc_{set}.md``)."""

from collections import Counter, defaultdict
from pathlib import Path

from src.data.schema import read_jsonl
from src.paraphrase.pipeline import load_checks
from src.paraphrase.review import load_decisions
from src.paraphrase.variants import STYLES, VARIANT_IDS, load_variants, validate_variants


def cohen_kappa(pairs):
    """Cohen's kappa for a list of ``(label_a, label_b)``. ``None`` if undefined."""
    n = len(pairs)
    if n == 0:
        return None
    labels = sorted({x for p in pairs for x in p})
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[l] * cb[l] for l in labels) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def _table(header, rows):
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def _pct(a, b):
    return f"{a} ({100 * a / b:.0f}%)" if b else "0"


def build_report(questions, files, set_name):
    variants = load_variants(files["variants"])
    decisions = load_decisions(files["decisions"])
    checks = load_checks(files["checks"])
    log = read_jsonl(files["log"]) if Path(files["log"]).exists() else []
    qmeta = {q["group_id"]: q for q in questions}
    group_ids = [q["group_id"] for q in questions]
    paraphrases = [(k, v) for k, v in variants.items() if k[1] in STYLES and k[0] in qmeta]

    out = [f"# Paraphrase QC report: `{set_name}`", ""]
    errors = validate_variants(list(variants.values()), group_ids, require_complete=True)
    out += ["**Ready for the model run:** " + ("yes" if not errors else f"no ({len(errors)} problems; "
            "first: " + errors[0] + ")"), ""]

    gen = sorted({(r["settings"]["provider"], r["model_version"] or r["settings"]["model"],
                   r["settings"]["temperature"], r["prompt_version"]) for r in log if not r.get("error")})
    out += ["## Setup", "",
            _table(["Provider", "Generator model", "Temperature", "Prompt"], gen) if gen else "No generation calls logged.",
            "", "Styles: " + ", ".join(f"`{k}` = {v}" for k, v in STYLES.items()) + ".", ""]
    tok_in = sum(r.get("tokens_in") or 0 for r in log if not r.get("cached"))
    tok_out = sum(r.get("tokens_out") or 0 for r in log if not r.get("cached"))
    redo_calls = sum(1 for r in log if r["attempt"] > 1 and not r.get("error"))
    out += [f"Generation calls: {len(log)} ({sum(1 for r in log if r.get('error'))} failed, "
            f"{redo_calls} regenerations). New tokens billed: {tok_in} in, {tok_out} out.", ""]

    # Current status by domain and style
    out += ["## Current validity", ""]
    status = defaultdict(Counter)
    for (g, vid), v in paraphrases:
        status[(qmeta[g]["domain"], vid)][v["validity"]] += 1
    rows = []
    for domain in sorted({q["domain"] for q in questions}):
        for vid in STYLES:
            c = status[(domain, vid)]
            n = sum(c.values())
            rows.append([domain, f"{vid} ({STYLES[vid]})", n, _pct(c["valid"], n), _pct(c["invalid"], n),
                         _pct(c["needs_review"], n)])
    out += [_table(["Domain", "Style", "Paraphrases", "Valid", "Invalid", "Needs review"], rows), ""]

    # First-pass validity: the decision on each variant's first reviewed text
    by_variant = defaultdict(list)
    for (g, vid, sha), d in decisions.items():
        by_variant[(g, vid)].append(d)
    reviewed = [d for d in decisions.values() if d["effective_validity"] in {"valid", "invalid"}]
    fp = Counter()
    for (g, vid), ds in by_variant.items():
        final = [d for d in ds if d["effective_validity"] in {"valid", "invalid"}]
        if final:
            # Variants that were regenerated have more than one decision; the invalid one came first.
            fp["invalid" if any(d["effective_validity"] == "invalid" for d in final) else "valid"] += 1
    n_fp = sum(fp.values())
    out += ["## First-pass quality", "",
            f"Of {n_fp} paraphrases with a final decision, {_pct(fp['valid'], n_fp)} were valid on the first "
            f"attempt and {_pct(fp['invalid'], n_fp)} needed regeneration.", ""]

    # Reviewer agreement
    pairs = [(d["validity_1"], d["validity_2"]) for d in decisions.values() if d["validity_1"] and d["validity_2"]]
    kappa = cohen_kappa(pairs)
    agree = sum(a == b for a, b in pairs)
    out += ["## Reviewer agreement", "",
            f"Independently double-reviewed decisions: {len(pairs)}. Raw agreement: {_pct(agree, len(pairs))}. "
            f"Cohen's kappa: {kappa:.2f}." if pairs and kappa is not None else
            f"Independently double-reviewed decisions: {len(pairs)}.", ""]
    if pairs:
        labels = sorted({x for p in pairs for x in p})
        cm = Counter(pairs)
        out += [_table(["Reviewer 1 \\ Reviewer 2", *labels], [[a, *[cm[(a, b)] for b in labels]] for a in labels]), ""]

    # Automatic checks vs. the human decision
    out += ["## Automatic checks against human decisions", "",
            "How well the rule flags and the judge predicted an `invalid` human decision, on reviewed text.", ""]
    flag_rows, judge = [], Counter()
    flag_stats = defaultdict(Counter)
    for d in reviewed:
        c = checks.get((d["group_id"], d["variant_id"], d["text_sha"]))
        if not c:
            continue
        human_invalid = d["effective_validity"] == "invalid"
        for f in c["rule_flags"] or ["(no flag)"]:
            flag_stats[f]["invalid" if human_invalid else "valid"] += 1
        if c.get("judge_verdict"):
            judge[(c["judge_verdict"], d["effective_validity"])] += 1
    for f, c in sorted(flag_stats.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        flag_rows.append([f"`{f}`", n, _pct(c["invalid"], n)])
    out += [_table(["Rule flag", "Reviewed paraphrases with flag", "Of which invalid"], flag_rows) if flag_rows
            else "No reviewed paraphrases with checks yet.", ""]
    if judge:
        verdicts = ["same", "unsure", "different"]
        out += [_table(["Judge verdict", "Human valid", "Human invalid"],
                       [[v, judge[(v, "valid")], judge[(v, "invalid")]] for v in verdicts]), ""]
        caught = judge[("different", "invalid")] + judge[("unsure", "invalid")]
        total_invalid = sum(judge[(v, "invalid")] for v in verdicts)
        out += [f"Judge recall on invalid paraphrases (different or unsure): {_pct(caught, total_invalid)}.", ""]

    # Invalid reasons (free text written by reviewers; IDs only, no source text)
    inval = [d for d in decisions.values() if d["effective_validity"] == "invalid"]
    if inval:
        out += ["## Invalid paraphrases", "", _table(["Group", "Variant", "Reason"],
                [[d["group_id"], d["variant_id"], d["effective_reason"].replace("|", "/")] for d in
                 sorted(inval, key=lambda d: (d["group_id"], VARIANT_IDS.index(d["variant_id"])))]), ""]
    if errors:
        out += ["## Remaining problems", ""] + [f"- {e}" for e in errors[:30]] + \
               ([f"- ...and {len(errors) - 30} more"] if len(errors) > 30 else []) + [""]
    return "\n".join(out)
