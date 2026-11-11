"""Paraphrase stage CLI. Run from the repo root.

    python -m src.paraphrase generate --set pilot --provider anthropic --model <generator-model>
    python -m src.paraphrase check    --set pilot [--judge-provider openai --judge-model <judge-model>]
    python -m src.paraphrase export   --set pilot          # → data/processed/paraphrase/pilot/review.csv
    # two reviewers fill reviewer_1/validity_1/reason_1 and reviewer_2/validity_2/reason_2
    python -m src.paraphrase import   --set pilot data/processed/paraphrase/pilot/review.csv
    python -m src.paraphrase redo     --set pilot --provider anthropic --model <generator-model>
    python -m src.paraphrase qc       --set pilot [--out report/paraphrase_qc_pilot.md]

``--set pilot`` reads ``data/processed/pilot.jsonl``; ``--set full`` reads ``data/processed/questions.jsonl``.
Use the **same** generator settings for ``generate`` and ``redo`` within a set.
"""

import argparse
import sys
from pathlib import Path

from src.paraphrase import pipeline, qc, review
from src.paraphrase.llm import Cache, LLMClient, LLMError, load_dotenv
from src.paraphrase.variants import SETS, paths, validate_variants


def _temperature(value):
    """A float, or ``none`` to omit temperature (some models reject it)."""
    return None if str(value).lower() == "none" else float(value)


def _client(args, prefix=""):
    model = getattr(args, f"{prefix}model")
    if not model:
        return None
    return LLMClient(getattr(args, f"{prefix}provider"), model, temperature=getattr(args, f"{prefix}temperature"),
                     seed=args.seed, max_tokens=args.max_tokens, max_tokens_field=args.max_tokens_field,
                     cache=Cache(args.cache))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m src.paraphrase", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["generate", "check", "export", "import", "redo", "qc", "validate"])
    ap.add_argument("sheets", nargs="*", help="review CSVs for `import`")
    ap.add_argument("--set", dest="set_name", choices=sorted(SETS), default="pilot")
    ap.add_argument("--input", help="override the question file for the set")
    ap.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    ap.add_argument("--model", help="generator model ID (generate, redo)")
    ap.add_argument("--temperature", type=_temperature, default=0.7, help="float, or none to omit")
    ap.add_argument("--judge-provider", choices=["anthropic", "openai"], default="anthropic")
    ap.add_argument("--judge-model", help="judge model ID (check); omit for rule checks only")
    ap.add_argument("--judge-temperature", type=_temperature, default=0.0)
    ap.add_argument("--seed", type=int, default=20261110, help="sent to OpenAI-compatible APIs that accept it")
    ap.add_argument("--max-tokens", type=int, default=1024)
    ap.add_argument("--max-tokens-field", default="max_tokens",
                    help="OpenAI-compatible only; some newer OpenAI models need max_completion_tokens")
    ap.add_argument("--limit", type=int, help="generate at most N new groups (try a few first)")
    ap.add_argument("--cache", default="data/processed/paraphrase/cache.jsonl")
    ap.add_argument("--force", action="store_true", help="export: overwrite an existing review sheet")
    ap.add_argument("--all", action="store_true", help="export: include already-decided paraphrases")
    ap.add_argument("--out", help="qc: also write the report to this markdown file")
    args = ap.parse_args(argv)

    load_dotenv()
    files = paths(args.set_name)
    qpath = Path(args.input) if args.input else SETS[args.set_name]
    if not qpath.exists():
        hint = "python -m src.data.sample --pilot" if args.set_name == "pilot" else "python -m src.data.sample"
        sys.exit(f"{qpath} not found. Build it first: bash data/fetch_raw.sh && python -m src.data.build_pools "
                 f"&& {hint}")
    questions = pipeline.load_questions(qpath)

    try:
        if args.command in {"generate", "redo"}:
            client = _client(args)
            if client is None:
                sys.exit("--model is required (the generator model ID)")
            print(f"{args.command}: {len(questions)} groups from {qpath} with {args.provider}:{args.model}")
            fn = pipeline.generate if args.command == "generate" else pipeline.redo
            kwargs = {"limit": args.limit} if args.command == "generate" else {}
            res = fn(questions, files, client, **kwargs)
            review.apply_decisions(questions, files)
            print(res)
            if args.model == args.judge_model:
                print("note: generator and judge are the same model")
        elif args.command == "check":
            judge = _client(args, "judge_")
            if judge is None:
                print("check: rule flags only (no --judge-model given)")
            pipeline.run_checks(questions, files, judge)
        elif args.command == "export":
            n = review.export_sheet(questions, files, include_decided=args.all, force=args.force)
            print(f"wrote {n} rows to {files['review']}")
        elif args.command == "import":
            if not args.sheets:
                sys.exit("give one or more review CSVs to import")
            review.import_sheets(args.sheets, questions, files)
        elif args.command == "qc":
            report = qc.build_report(questions, files, args.set_name)
            print(report)
            if args.out:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(report + "\n", encoding="utf-8")
        elif args.command == "validate":
            from src.paraphrase.variants import load_variants
            rows = list(load_variants(files["variants"]).values())
            errs = validate_variants(rows, [q["group_id"] for q in questions], require_complete=True)
            for e in errs:
                print(e)
            print("OK: all groups complete and valid" if not errs else f"{len(errs)} problems")
            return 1 if errs else 0
    except (LLMError, ValueError, FileExistsError) as e:
        sys.exit(f"error: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
