"""Compute the unfiltered dashboard cards directly from the exported orders."""
import argparse
import csv
from datetime import date
from decimal import Decimal
from pathlib import Path


def summarize(rows):
    orders = assessed = on_time = days = 0
    item_cents = freight_cents = paid_cents = 0
    for row in rows:
        orders += 1
        item_cents += int(row["item_value_cents"])
        freight_cents += int(row["freight_cents"])
        paid_cents += int(row["payment_value_cents"])
        if row["order_status"] == "delivered" and row["delivered_date"] and row["estimated_date"]:
            delivered = date.fromisoformat(row["delivered_date"])
            assessed += 1
            on_time += delivered <= date.fromisoformat(row["estimated_date"])
            days += (delivered - date.fromisoformat(row["purchase_date"])).days
    return {
        "Orders placed": orders,
        "Item value": Decimal(item_cents).scaleb(-2) if orders else None,
        "Freight value": Decimal(freight_cents).scaleb(-2) if orders else None,
        "Payment value": Decimal(paid_cents).scaleb(-2) if orders else None,
        "Assessed orders": assessed,
        "On-time orders": on_time,
        "Late orders": assessed-on_time,
        "Unassessed orders": orders-assessed,
        "On-time share": None if not assessed else on_time/assessed,
        "Mean delivery days": None if not assessed else days/assessed,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[1] / "data/orders.csv")
    parser.add_argument("--month", help="Optional purchase month, YYYY-MM")
    parser.add_argument("--state", help="Optional two-letter customer state")
    parser.add_argument("--verify-demo", action="store_true", help="Compare the unfiltered sample to its published benchmark")
    args = parser.parse_args()
    if args.verify_demo and (args.month or args.state):
        parser.error("--verify-demo applies to the unfiltered sample")
    with args.input.open(newline="", encoding="utf-8-sig") as inp:
        rows = (row for row in csv.DictReader(inp)
                if (not args.month or row["purchase_date"][:7] == args.month)
                and (not args.state or row["customer_state"] == args.state))
        values = summarize(rows)
    for key, value in values.items():
        print(f"{key}: {value}")
    if args.verify_demo:
        benchmarks = {"Orders placed": 5262, "Item value": Decimal("741960.19"),
                      "Freight value": Decimal("113557.51"), "Payment value": Decimal("880259.65"),
                      "Assessed orders": 4949, "On-time orders": 4762, "Late orders": 187,
                      "Unassessed orders": 313}
        if any(values[k] != val for k, val in benchmarks.items()):
            parser.exit(1, "The input differs from the checked-in sample benchmark.\n")


if __name__ == "__main__":
    main()
