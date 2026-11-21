"""Introduce controlled defects into a copy for a release-gate drill."""
import argparse
import csv
import io
from pathlib import Path
from .checks import ORDER_HEADER


def inject(source, target):
    if source.resolve() == target.resolve():
        raise ValueError("Choose a separate path for the fault-injected copy")
    with source.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if tuple(reader.fieldnames or ()) != ORDER_HEADER:
            raise ValueError("Unexpected input columns")
        rows = list(reader)
    if len(rows) < 3:
        raise ValueError("This drill needs at least three orders")
    # Keep original IDs and month controls. Only the COPY is deliberately damaged.
    rows[1]["customer_state"] = "ZZ"
    rows[2]["payment_value_cents"] = str(int(rows[2]["payment_value_cents"]) + 700)
    rows.append(dict(rows[0]))
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, ORDER_HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    target.parent.mkdir(parents=True, exist_ok=True)
    # An exclusive create also prevents replacing an original via a symlink.
    with target.open("x", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/orders.csv"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        inject(args.input, args.out)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
