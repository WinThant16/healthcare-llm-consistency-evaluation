"""Validate question JSONL files against the schema.

Usage (from repo root):
    python -m src.data.validate data/processed/candidates/*.jsonl
    python -m src.data.validate --stage final data/processed/questions.jsonl
"""

import argparse
import sys

from src.data.schema import STAGES, read_jsonl, validate_rows

MAX_SHOWN = 20


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--stage", choices=STAGES, default="candidates")
    args = parser.parse_args(argv)

    failed = False
    for path in args.paths:
        rows = read_jsonl(path)
        problems = validate_rows(rows, args.stage)
        if problems:
            failed = True
            print(f"FAIL {path}: {len(problems)} problem(s) in {len(rows)} rows")
            for line, err in problems[:MAX_SHOWN]:
                print(f"  line {line}: {err}")
            if len(problems) > MAX_SHOWN:
                print(f"  ... {len(problems) - MAX_SHOWN} more")
        else:
            print(f"OK   {path}: {len(rows)} rows")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
