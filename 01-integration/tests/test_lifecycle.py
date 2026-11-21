import csv
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.replay_lifecycle import replay


SOURCE = ROOT / "data/batches"
ORDER = "76c6e866289321a7c93b82b54852dc33"


class LifecycleReplayTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.home = Path(work.name)
        self.batches = self.home / "batches"
        self.batches.mkdir()
        self.db = self.home / "lifecycle.sqlite"

    def add_month(self, month):
        folder = self.batches / month
        folder.mkdir()
        shutil.copyfile(SOURCE / month / "orders.csv", folder / "orders.csv")
        return folder / "orders.csv"

    def cutoff(self, day):
        return replay(self.batches, self.db, date.fromisoformat(day))

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_recorded_milestones_without_future_leakage_and_repeated_cutoff(self):
        self.add_month("2017-01")
        self.add_month("2017-02")
        self.cutoff("2017-01-22")
        self.assertEqual([], self.query("SELECT order_id FROM lifecycle_current WHERE order_id=?", (ORDER,)))
        for day, state in [("2017-01-23", "purchased"), ("2017-01-25", "approved"),
                           ("2017-01-31", "handed_to_carrier"), ("2017-02-02", "delivered")]:
            result = self.cutoff(day)
            stage, delivered = self.query("SELECT last_milestone, delivered_at FROM lifecycle_current WHERE order_id=?",
                                          (ORDER,))[0]
            self.assertEqual(stage, state)
            self.assertEqual(delivered, "2017-02-02 14:08:10" if state == "delivered" else None)
        second = self.cutoff("2017-02-02")
        self.assertEqual(result["milestones"], second["milestones"])
        self.assertEqual(([], 0, 0), (second["new_batches"], second["applied_events"], second["touched_orders"]))

    def test_late_registration_before_watermark_matches_chronological_import(self):
        self.add_month("2017-02")
        feb = self.cutoff("2017-02-02")
        self.add_month("2017-01")
        late = self.cutoff("2017-02-02")
        expected = replay(self.batches, self.home / "clean.sqlite", date(2017, 2, 2))
        self.assertGreater(late["visible_orders"], feb["visible_orders"])
        self.assertEqual(late["milestones"], expected["milestones"])
        self.assertEqual(["2017-01"], late["new_batches"])
        self.assertEqual(self.cutoff("2017-02-02")["applied_events"], 0)

    def test_edited_archive_or_backwards_cutoff_leaves_committed_watermark(self):
        file = self.add_month("2017-01")
        self.cutoff("2017-01-31")
        with self.assertRaisesRegex(ValueError, "earlier date"):
            self.cutoff("2017-01-01")
        with file.open("ab") as f:
            f.write(b"\n")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.cutoff("2017-02-01")
        self.assertEqual([("2017-01-31",)], self.query("SELECT as_of FROM lifecycle_checkpoint"))

    def test_new_input_with_duplicate_key_rolls_back_fresh_batch(self):
        self.add_month("2017-01")
        self.cutoff("2017-01-31")
        file = self.add_month("2017-02")
        with file.open(newline="", encoding="utf8") as stream:
            row = next(csv.DictReader(stream))
        with file.open("a", newline="", encoding="utf8") as stream:
            csv.DictWriter(stream, fieldnames=list(row)).writerow(row)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.cutoff("2017-02-02")
        self.assertEqual([("2017-01",)], self.query("SELECT month FROM lifecycle_batches"))
        self.assertEqual([("2017-01-31",)], self.query("SELECT as_of FROM lifecycle_checkpoint"))


if __name__ == "__main__":
    unittest.main()
