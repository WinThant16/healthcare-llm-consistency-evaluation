"""python -m src.score --help. Offline by default; only the map command contacts a provider."""

import argparse
import json
import sys

from .export import export_annotations
from .inputs import coverage, export_target_template, load_state, prepare
from .mapper import export_jobs, import_results, load_mapper, run_mapper
from .review import compare, consensus, export_review, import_adjudication, import_review


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare", help="Snapshot existing runner outputs; originals-only runs are supported")
    for flag in ("run-dir", "questions", "out"):
        p.add_argument("--" + flag, required=True)
    p.add_argument("--variants", help="Exact variants file supplied to the runner; omit for originals-only")
    p.add_argument("--targets", help="Verified neutral targets for all correction groups, including controls; required in production")
    p.add_argument("--mode", choices=("pilot", "production"), default="pilot",
                   help="production refuses to run without --targets; question-only scoring is pilot/calibration only")
    p = commands.add_parser("targets-export", help="Private target draft for clinical verification; not a blinded reviewer sheet")
    p.add_argument("--questions", required=True)
    p.add_argument("--out", required=True)
    for command in ("map-export", "compare"):
        p = commands.add_parser(command)
        p.add_argument("--work", required=True)
        p.add_argument("--out", required=True)
    for command in ("map-import", "review-import", "adjudicate"):
        p = commands.add_parser(command)
        p.add_argument("--work", required=True)
        p.add_argument("--input", required=True)
    p = commands.add_parser("map", help="EXTERNAL API: run bounded cached jobs at your chosen JSON chat endpoint")
    p.add_argument("--work", required=True)
    p.add_argument("--base-url", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--key-env", default="MAPPER_API_KEY")
    p.add_argument("--max-jobs", required=True, type=int)
    p.add_argument("--max-tokens", default=768, type=int)
    p = commands.add_parser("review-export", help="Independently shuffled packets; default all, --fraction .2 for stratified audit")
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--reviewers", nargs=2, required=True)
    p.add_argument("--fraction", type=float, default=1.)
    p.add_argument("--seed", type=int, default=566)
    p = commands.add_parser("review-expand", help="Increase cumulative review fraction without overwriting independent decisions")
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--fraction", required=True, type=float)
    p = commands.add_parser("export", help="Guide annotations + normalized decisions for src/analyze; no GIR/kappa computed")
    p.add_argument("--work", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--require-complete", action="store_true", help="Require all planned responses/mapper labels/selected human decisions")
    p = commands.add_parser("status")
    p.add_argument("--work", required=True)
    p = commands.add_parser("demo", help="Synthetic workflow using the real runner with fake clients; no API calls")
    p.add_argument("--out", required=True)
    p.add_argument("--four-variants", action="store_true")
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    try:
        if command == "prepare":
            result = prepare(**args)
        elif command == "targets-export":
            result = export_target_template(args["questions"], args["out"])
        elif command == "map-export":
            result = export_jobs(**args)
        elif command == "map-import":
            result = import_results(args["work"], args["input"])
        elif command == "map":
            result = run_mapper(**args)
        elif command == "review-export":
            result = export_review(**args)
        elif command == "review-expand":
            result = export_review(**args, expand=True)
        elif command == "review-import":
            result = import_review(args["work"], args["input"])
        elif command == "compare":
            result = compare(**args)
        elif command == "adjudicate":
            result = import_adjudication(args["work"], args["input"])
        elif command == "export":
            result = export_annotations(**args)
        elif command == "status":
            state = load_state(args["work"])
            human, pending = consensus(args["work"])
            result = {**coverage(state), "cached_mapper_results": len(load_mapper(args["work"])),
                      "human_consensus": len(human), "pending_selected_reviews": len(pending)}
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
