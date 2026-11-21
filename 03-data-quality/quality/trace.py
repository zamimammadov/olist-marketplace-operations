"""Trace a single published order to original item and payment source lines."""

import argparse
import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .checks import ORDER_ID


def rows_for(path, oid):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if "order_id" not in (reader.fieldnames or ()):
            raise ValueError(f"Expected an order_id column in {path.name}")
        return [(reader.line_num, row) for row in reader if row["order_id"] == oid]


def cents(amount):
    exact = Decimal(amount) * 100
    if not exact.is_finite() or exact != exact.to_integral_value() or exact < 0:
        raise ValueError(f"Expected a nonnegative amount in whole cents, got {amount!r}")
    return int(exact)


def trace(batch, mart, oid):
    if not ORDER_ID.fullmatch(oid):
        raise ValueError("Supply an original 32-character lowercase hexadecimal order ID")
    orders = rows_for(batch / "orders.csv", oid)
    if len(orders) != 1:
        raise ValueError(f"Expected one order in {batch.name}, found {len(orders)}")
    order_line, order = orders[0]
    items = [
        {"line": line, "item_id": row["order_item_id"], "price_cents": cents(row["price"]),
         "freight_cents": cents(row["freight_value"])}
        for line, row in rows_for(batch / "order_items.csv", oid)
    ]
    payments = [
        {"line": line, "sequence": row["payment_sequential"], "type": row["payment_type"],
         "paid_cents": cents(row["payment_value"])}
        for line, row in rows_for(batch / "order_payments.csv", oid)
    ]
    source_totals = {
        "item_count": len(items), "payment_count": len(payments),
        "item_value_cents": sum(item["price_cents"] for item in items),
        "freight_cents": sum(item["freight_cents"] for item in items),
        "payment_value_cents": sum(payment["paid_cents"] for payment in payments),
    }
    published = rows_for(mart, oid)
    mart_values = [
        {"line": line, "measures": {k: int(row[k]) for k in source_totals}}
        for line, row in published
    ]
    matches = len(mart_values) == 1 and mart_values[0]["measures"] == source_totals
    return {
        "purchase_batch": batch.name, "order_id": oid, "source_order_line": order_line,
        "source_status": order["order_status"], "items": items, "payments": payments,
        "source_totals": source_totals, "published_rows": mart_values,
        "published_measures_match_source": matches,
        "source_payment_gap_cents": (source_totals["payment_value_cents"]
                                     - source_totals["item_value_cents"]
                                     - source_totals["freight_cents"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True, help="One original purchase-month directory")
    parser.add_argument("--input", type=Path, default=Path("data/orders.csv"))
    parser.add_argument("--order-id", required=True)
    args = parser.parse_args()
    try:
        result = trace(args.batch, args.input, args.order_id)
    except (OSError, csv.Error, ValueError, InvalidOperation, KeyError) as exc:
        parser.exit(2, f"Trace failed: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0 if result["published_measures_match_source"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
