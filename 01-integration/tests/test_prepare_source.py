"""Archive preparation contracts for real rows taken from the bundled Olist slice."""

import csv
import io
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from tools.prepare_source import TABLES, prepare


BATCHES = Path(__file__).resolve().parents[1] / "data" / "batches"


def read_rows(path):
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def sample_tables():
    tables = {name: [] for name in TABLES}
    for month in ("2017-01", "2017-02"):
        source = {name: read_rows(BATCHES / month / f"{name}.csv") for name in TABLES}
        order = next(row for row in source["orders"] if row["order_status"] == "delivered")
        tables["orders"].append(order)
        tables["customers"].extend(row for row in source["customers"] if row["customer_id"] == order["customer_id"])
        for table in ("order_items", "order_payments"):
            tables[table].extend(row for row in source[table] if row["order_id"] == order["order_id"])
    return tables


def write_archive(path, tables):
    with ZipFile(path, "w") as archive:
        for name, header in TABLES.items():
            stream = io.StringIO(newline="")
            writer = csv.DictWriter(stream, header)
            writer.writeheader()
            writer.writerows(tables[name])
            archive.writestr(f"olist_{name}_dataset.csv", stream.getvalue().encode("utf-8"))


class PrepareSourceTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.archive = base / "source.zip"
        self.batches = base / "batches"
        self.reference = base / "reference"
        self.tables = sample_tables()
        write_archive(self.archive, self.tables)

    def prepare_january(self):
        return prepare(self.archive, self.batches, self.reference, "2017-01", "2017-01")

    def test_slice_accepts_valid_children_from_other_month_and_repeats(self):
        self.assertEqual(self.prepare_january(), (1, ["2017-01"]))
        original = (self.reference / "orders.csv").read_bytes()
        self.assertEqual(self.prepare_january(), (1, ["2017-01"]))
        self.assertEqual(original, (self.reference / "orders.csv").read_bytes())
        january_id = self.tables["orders"][0]["order_id"]
        controls, = read_rows(self.reference / "source_controls.csv")
        payment_lines = [row for row in self.tables["order_payments"] if row["order_id"] == january_id]
        payment_cents = sum(int(Decimal(row["payment_value"]) * 100) for row in payment_lines)
        self.assertEqual(int(controls["payment_count"]), len(payment_lines))
        self.assertEqual(int(controls["payment_value_cents"]), payment_cents)

    def test_unknown_parent_is_not_discarded_as_an_out_of_period_order(self):
        self.tables["order_items"][0]["order_id"] = "not-an-upstream-order"
        write_archive(self.archive, self.tables)
        with self.assertRaisesRegex(ValueError, "order_items references unknown source order"):
            self.prepare_january()
        self.assertEqual(list(self.batches.iterdir()), [])
        self.assertEqual(list(self.reference.iterdir()), [])

    def test_different_existing_month_is_rejected_before_publishing_reference(self):
        self.prepare_january()
        old = (self.reference / "source_controls.csv").read_bytes()
        self.tables["order_payments"][0]["payment_value"] = "0.07"
        write_archive(self.archive, self.tables)
        with self.assertRaisesRegex(ValueError, "Existing batch differs"):
            self.prepare_january()
        self.assertEqual(old, (self.reference / "source_controls.csv").read_bytes())


if __name__ == "__main__":
    unittest.main()
