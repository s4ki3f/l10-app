"""Print the rules report as JSON for APP_NOW (default 2026-11-11T15:00:00Z).

    python -m app.rules_report                # from the database (DATABASE_URL)
    python -m app.rules_report --seed seed.json   # straight from the seed file, no database
"""
import argparse
import json
import sys
from pathlib import Path

from .rules.clock import app_now_from_env
from .rules.dataset import dataset_from_seed
from .rules.report import build_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=Path, help="read seed.json instead of the database")
    args = parser.parse_args(argv)

    if args.seed:
        data = dataset_from_seed(json.loads(args.seed.read_text(encoding="utf-8")))
    else:
        from .db.load import dataset_from_db  # imported lazily: --seed works without a database

        data = dataset_from_db()
    json.dump(build_report(data, app_now_from_env()), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
