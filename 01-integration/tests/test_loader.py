import csv
from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from pipeline.loader import run
from pipeline.rows import ContractError


SAMPLE = Path(__file__).resolve().parents[1] / "data" / "batches"


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.batches = base / "batches"
        self.batches.mkdir()
        self.db = base / "warehouse.sqlite"
        self.out = base / "orders.csv"

    def deliver(self, month):
        return Path(shutil.copytree(SAMPLE / month, self.batches / month))

    def load(self, through=None):
        return run(self.batches, self.db, self.out, through)

    def select(self, sql, bindings=()):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql, bindings).fetchall()
        finally:
            con.close()

    def test_incremental_cohorts_replay_and_audit(self):
        months = ["2017-01", "2017-02", "2017-03"]
        for month in months:
            self.deliver(month)
        for index, (month, count) in enumerate(zip(months, (800, 2580, 5262))):
            self.assertEqual(self.load(month), {
                "loaded": [month], "skipped": months[:index], "orders_exported": count,
            })
        expected = self.out.read_bytes()
        self.assertEqual(hashlib.sha256(expected).hexdigest(),
                         "703d4c573fe7dbc9f085ada05cb03fd35f406c334a16a24706ac52d87be8be22")
        self.assertEqual(self.load(), {"loaded": [], "skipped": months, "orders_exported": 5262})
        self.assertEqual(self.out.read_bytes(), expected)
        self.assertEqual(self.select("SELECT count(*) FROM batch_files"), [(12,)])
        for month, filename, digest in self.select("SELECT month, filename, sha256 FROM batch_files"):
            self.assertEqual(digest, hashlib.sha256((self.batches / month / filename).read_bytes()).hexdigest())
        # --through limits incoming files, not what the snapshot may publish.
        self.assertEqual(self.load("2017-01")["orders_exported"], 5262)

    def test_genuine_order_with_multiple_items_and_payments(self):
        for month in ("2017-01", "2017-02", "2017-03"):
            self.deliver(month)
        self.load()
        self.assertEqual(self.select("""
            SELECT item_count, payment_count, item_value_cents, freight_cents, payment_value_cents
              FROM order_snapshot WHERE order_id = ?
        """, ("08bdbdeaf1626720051193300f93806e",)), [(4, 2, 39600, 7712, 47312)])
        # Counts and money expected from separate raw-source tables.
        self.assertEqual(self.select("""
            SELECT sum(item_count), sum(payment_count), sum(item_value_cents), sum(freight_cents),
                   sum(payment_value_cents), sum(item_count = 0) FROM order_snapshot
        """), [(5906, 5573, 74196019, 11355751, 88025965, 99)])

    def test_late_month_backfill(self):
        self.deliver("2017-03")
        self.assertEqual(self.load()["orders_exported"], 2682)
        self.deliver("2017-01")
        self.assertEqual(self.load(), {"loaded": ["2017-01"], "skipped": ["2017-03"], "orders_exported": 3482})
        self.assertEqual(self.select("SELECT DISTINCT month FROM orders ORDER BY month"), [("2017-01",), ("2017-03",)])

    def test_broken_fk_rolls_back_month_and_keeps_previous_export(self):
        self.deliver("2017-01")
        self.load()
        prior = self.out.read_bytes()
        folder = self.deliver("2017-02")
        payment_file = folder / "order_payments.csv"
        raw = payment_file.read_bytes()
        with payment_file.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(["nonexistent-order", 1, "credit_card", 1, "8.91"])
        with self.assertRaisesRegex(ContractError, r"order_payments.csv line .*FOREIGN KEY"):
            self.load()
        self.assertEqual(self.out.read_bytes(), prior)
        for table in ("batches", "orders", "customers", "order_items", "order_payments", "batch_files"):
            self.assertEqual(self.select(f"SELECT count(*) FROM {table} WHERE month = '2017-02'"), [(0,)])
        self.assertEqual(self.select("SELECT month FROM load_failures"), [("2017-02",)])
        payment_file.write_bytes(raw)
        self.assertEqual(self.load(), {"loaded": ["2017-02"], "skipped": ["2017-01"], "orders_exported": 2580})

    def test_changed_historical_source_refused(self):
        folder = self.deliver("2017-01")
        self.load()
        output = self.out.read_bytes()
        payments = folder / "order_payments.csv"
        with payments.open("ab") as handle:
            handle.write(b"\n")
        with self.assertRaisesRegex(ContractError, "different bytes"):
            self.load()
        self.assertEqual(self.out.read_bytes(), output)
        self.assertEqual(self.select("SELECT count(*) FROM orders"), [(800,)])

    def test_empty_orders_supply_is_not_booked_as_loaded(self):
        folder = self.deliver("2017-01")
        orders = folder / "orders.csv"
        with orders.open(encoding="utf-8") as handle:
            header = handle.readline()
        orders.write_text(header, encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "no orders"):
            self.load()
        self.assertFalse(self.out.exists())
        self.assertEqual(self.select("SELECT count(*) FROM batches"), [(0,)])

    def test_failed_publish_recoverable_without_reloading_rows(self):
        self.deliver("2017-01")
        self.out.mkdir()
        with self.assertRaises(OSError):
            self.load()
        self.assertEqual(self.select("SELECT count(*) FROM orders"), [(800,)])
        self.out.rmdir()
        self.assertEqual(self.load(), {"loaded": [], "skipped": ["2017-01"], "orders_exported": 800})

    def test_two_simultaneous_loaders_do_not_duplicate_data(self):
        self.deliver("2017-01")
        with ThreadPoolExecutor(2) as worker:
            futures = [worker.submit(self.load) for _ in range(2)]
            reports = [f.result() for f in futures]
        self.assertEqual(sum(len(r["loaded"]) for r in reports), 1)
        self.assertEqual(sum(len(r["skipped"]) for r in reports), 1)
        self.assertEqual(self.select("SELECT count(*) FROM orders"), [(800,)])


if __name__ == "__main__":
    unittest.main()
