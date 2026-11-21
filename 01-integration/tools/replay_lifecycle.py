"""Incrementally project Olist's recorded order milestones through a calendar date."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.rows import parsed_rows


MILESTONES = ("purchased", "approved", "handed_to_carrier", "delivered")
SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS lifecycle_checkpoint (
    id INTEGER PRIMARY KEY CHECK (id = 1), as_of TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_batches (
    month TEXT PRIMARY KEY, sha256 TEXT NOT NULL, order_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_events (
    order_id TEXT NOT NULL, rank INTEGER NOT NULL CHECK (rank BETWEEN 0 AND 3),
    observed_at TEXT NOT NULL, month TEXT NOT NULL REFERENCES lifecycle_batches(month),
    applied INTEGER NOT NULL DEFAULT 0 CHECK (applied IN (0, 1)),
    PRIMARY KEY (order_id, rank)
);
CREATE INDEX IF NOT EXISTS pending_event_time ON lifecycle_events(observed_at)
WHERE applied = 0;
CREATE TABLE IF NOT EXISTS lifecycle_current (
    order_id TEXT PRIMARY KEY, last_milestone TEXT NOT NULL,
    purchased_at TEXT NOT NULL, approved_at TEXT, carrier_at TEXT, delivered_at TEXT
);
"""
DUE = """
CREATE TEMP TABLE due AS
SELECT e.order_id, e.rank
FROM lifecycle_events AS e
JOIN lifecycle_events AS purchase
  ON purchase.order_id = e.order_id AND purchase.rank = 0
WHERE e.applied = 0 AND e.observed_at < ? AND purchase.observed_at < ?
"""
PROJECT = """
INSERT INTO lifecycle_current
SELECT e.order_id,
       CASE max(e.rank) WHEN 3 THEN 'delivered' WHEN 2 THEN 'handed_to_carrier'
                        WHEN 1 THEN 'approved' ELSE 'purchased' END,
       max(CASE e.rank WHEN 0 THEN e.observed_at END),
       max(CASE e.rank WHEN 1 THEN e.observed_at END),
       max(CASE e.rank WHEN 2 THEN e.observed_at END),
       max(CASE e.rank WHEN 3 THEN e.observed_at END)
FROM lifecycle_events AS e
WHERE e.applied = 1 AND e.order_id IN (SELECT order_id FROM due)
GROUP BY e.order_id
ON CONFLICT(order_id) DO UPDATE SET
    last_milestone=excluded.last_milestone, purchased_at=excluded.purchased_at,
    approved_at=excluded.approved_at, carrier_at=excluded.carrier_at,
    delivered_at=excluded.delivered_at
"""


def stage(conn, folder):
    """Register one immutable purchase cohort and insert only source-backed events."""
    content = (folder / "orders.csv").read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    prior = conn.execute("SELECT sha256 FROM lifecycle_batches WHERE month = ?", (folder.name,)).fetchone()
    if prior:
        if prior[0] != digest:
            raise ValueError(f"{folder.name}/orders.csv changed; rebuild a fresh lifecycle DB to adopt corrected source data")
        return False
    conn.execute("INSERT INTO lifecycle_batches VALUES (?, ?, 0)", (folder.name, digest))
    count = 0
    for line, row in parsed_rows(folder.name, "orders", content):
        # The source row also contains final status and estimated delivery. Neither is an observed event.
        events = [(row[0], rank, stamp, folder.name) for rank, stamp in enumerate(row[4:8]) if stamp]
        try:
            conn.executemany("INSERT INTO lifecycle_events(order_id,rank,observed_at,month) VALUES (?,?,?,?)", events)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"{folder.name}/orders.csv:{line}: duplicate or invalid order {row[0]}") from exc
        count += 1
    conn.execute("UPDATE lifecycle_batches SET order_count=? WHERE month=?", (count, folder.name))
    return True


def replay(batches: Path, db: Path, as_of: date):
    next_day = (as_of + timedelta(days=1)).isoformat() + " 00:00:00"
    folders = sorted(path for path in batches.iterdir()
                     if path.is_dir() and re.fullmatch(r"\d{4}-(?:0[1-9]|1[012])", path.name))
    if not folders:
        raise ValueError(f"no YYYY-MM purchase cohort folders under {batches}")
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db, timeout=30, isolation_level=None)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables and "lifecycle_checkpoint" not in tables:
            raise ValueError("specify a separate lifecycle SQLite database")
        # This transaction contains source registration, event application and the watermark.
        conn.executescript(SCHEMA)
        try:
            row = conn.execute("SELECT as_of FROM lifecycle_checkpoint WHERE id=1").fetchone()
            if row and as_of.isoformat() < row[0]:
                raise ValueError(f"latest cutoff was {row[0]}; choose a fresh DB to replay an earlier date")
            new_months = [p.name for p in folders if p.name <= as_of.strftime("%Y-%m") and stage(conn, p)]
            conn.execute(DUE, (next_day, next_day))
            applied, touched = conn.execute("SELECT count(*), count(DISTINCT order_id) FROM due").fetchone()
            conn.execute("UPDATE lifecycle_events SET applied=1 WHERE (order_id,rank) IN (SELECT order_id,rank FROM due)")
            conn.execute(PROJECT)
            conn.execute("""INSERT INTO lifecycle_checkpoint VALUES (1,?)
                            ON CONFLICT(id) DO UPDATE SET as_of=excluded.as_of""", (as_of.isoformat(),))
            counts = dict.fromkeys(MILESTONES, 0)
            counts.update(conn.execute("SELECT last_milestone,count(*) FROM lifecycle_current GROUP BY last_milestone"))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    return dict(as_of=as_of.isoformat(), new_batches=new_months, applied_events=applied,
                touched_orders=touched, visible_orders=sum(counts.values()), milestones=counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batches", type=Path, default=Path("data/batches"))
    parser.add_argument("--db", type=Path, default=Path("build/lifecycle.sqlite"))
    parser.add_argument("--as-of", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    args = parser.parse_args()
    try:
        result = replay(args.batches, args.db, args.as_of)
    except (OSError, ValueError, sqlite3.Error, OverflowError) as exc:
        parser.exit(1, f"Lifecycle replay failed: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
