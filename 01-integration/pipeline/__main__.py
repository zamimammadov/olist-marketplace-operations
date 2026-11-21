import argparse
import json
import sqlite3
import sys

from .loader import run
from .rows import ContractError


def main():
    parser = argparse.ArgumentParser(description="Load and publish Olist order purchase cohorts")
    parser.add_argument("--batches", default="data/batches", help="directory containing YYYY-MM deliveries")
    parser.add_argument("--db", default="build/warehouse.sqlite", help="persistent SQLite warehouse")
    parser.add_argument("--out", default="build/orders.csv", help="published order snapshot")
    parser.add_argument("--through", metavar="YYYY-MM", help="load input cohorts up to and including this month")
    args = parser.parse_args()
    try:
        result = run(args.batches, args.db, args.out, args.through)
    except (ContractError, OSError, sqlite3.Error) as exc:
        print(f"Integration error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
