import csv
import json
import tempfile
import unittest
from pathlib import Path

from quality.__main__ import run_gate
from quality.checks import ORDER_HEADER
from quality.trace import trace


ROOT = Path(__file__).resolve().parents[1]


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        writer.writerows(rows)


class EvidenceTests(unittest.TestCase):
    def test_delivered_snapshot_reconciles_to_independent_controls(self):
        with tempfile.TemporaryDirectory() as out:
            result = run_gate(ROOT / "data/orders.csv", ROOT / "data/source_controls.csv", Path(out))
            self.assertEqual(result["decision"], "PASS_WITH_WARNINGS")
            self.assertEqual(result["profile"]["order_rows"], 5262)
            self.assertEqual(result["rule_counts"], {"VALUE_VARIANCE": 132})
            self.assertEqual(result["profile"]["eligible_delivered_rows"], 4949)
            self.assertEqual(result["profile"]["on_time_rows"], 4762)
            evidence = Path(out) / "runs" / result["run_id"]
            with (evidence / "monthly_controls.csv").open(newline="") as stream:
                comparisons = list(csv.DictReader(stream))
            self.assertEqual(len(comparisons), 18)
            self.assertTrue(all(row["mart_minus_source"] == "0" for row in comparisons))
            self.assertEqual({row["result"] for row in comparisons}, {"MATCH"})

    def test_trace_compares_separate_child_tables_and_detects_corrupt_mart(self):
        oid = "a" * 32
        with tempfile.TemporaryDirectory() as directory:
            batch = Path(directory) / "demo"
            batch.mkdir()
            write_csv(batch / "orders.csv", ["order_id", "order_status"], [[oid, "delivered"]])
            write_csv(batch / "order_items.csv", ["order_id","order_item_id","price","freight_value"],
                      [[oid,1,"11.50","2.30"], [oid,2,"1.99","0.00"]])
            write_csv(batch / "order_payments.csv", ["order_id","payment_sequential","payment_type","payment_value"],
                      [[oid,1,"voucher","5.00"], [oid,2,"credit_card","10.79"]])
            mart = Path(directory) / "mart.csv"
            row = [oid,"2017-01-02","SP","delivered","2017-01-03","2017-01-05",2,2,1349,230,1579]
            write_csv(mart, ORDER_HEADER, [row])
            result = trace(batch, mart, oid)
            self.assertTrue(result["published_measures_match_source"])
            self.assertEqual(result["source_payment_gap_cents"],0)
            self.assertEqual([payment["line"] for payment in result["payments"]],[2,3])
            row[-1] += 1
            write_csv(mart, ORDER_HEADER, [row])
            self.assertFalse(trace(batch, mart, oid)["published_measures_match_source"])
            write_csv(mart, ORDER_HEADER, [row, row])
            self.assertEqual(len(trace(batch, mart, oid)["published_rows"]),2)


if __name__ == "__main__":
    unittest.main()
