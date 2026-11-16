"""Offline GIR, bootstrap, accuracy, agreement and reports. No model/provider calls."""

import argparse
import json
import sys

from .inputs import load_data
from .report import analyze


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "validate"):
        p = commands.add_parser(name)
        p.add_argument("--annotations", required=True, help="Scorer export directory with decisions.jsonl and annotation files")
        p.add_argument("--questions", required=True, help="Matching source questions JSONL with reference labels")
        p.add_argument("--models", nargs="+", help="Explicit expected model keys; optional when --scoring-work supplies them")
        p.add_argument("--scoring-work", help="Optional read-only PR #21 workspace for exact raw observation agreement")
        if name == "run":
            p.add_argument("--out", required=True)
            p.add_argument("--policy-file")
            p.add_argument("--bootstrap-samples", type=int)
            p.add_argument("--seed", type=int)
            p.add_argument("--control-gir", choices=("separate", "pooled"))
            p.add_argument("--pending-audit", choices=("exclude", "mapper_exploratory"))
            p.add_argument("--plots", action="store_true", help="Requires optional requirements-analysis.txt")
            p.add_argument("--study-label", default="Healthcare consistency analysis — exploratory")
    p = commands.add_parser("demo", help="Synthetic inputs, no src.score dependency or API keys")
    p.add_argument("--out", required=True)
    p.add_argument("--originals-only", action="store_true")
    p.add_argument("--plots", action="store_true")
    p.add_argument("--bootstrap-samples", type=int, default=500)
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        if command == "run":
            result = analyze(**args)
        elif command == "validate":
            data = load_data(**args)
            result = {"run_id": data["run_id"], "question_groups": len(data["questions"]),
                      "expected_models": data["models"], "decision_records": len(data["by_id"]),
                      "four_variant_slots": len(data["models"]) * len(data["questions"]) * 4}
        else:
            from .demo import demo
            result = demo(**args)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
