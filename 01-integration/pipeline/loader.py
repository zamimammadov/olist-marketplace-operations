"""Load immutable purchase cohorts into SQLite and atomically export an order snapshot."""

import csv
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile

from .rows import ContractError, HEADERS, parsed_rows


ORDER_FIELDS = (
    "order_id", "purchase_date", "customer_state", "order_status", "delivered_date", "estimated_date",
    "item_count", "payment_count", "item_value_cents", "freight_cents", "payment_value_cents",
)
MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
APPLICATION_ID = 0x4F4C5354  # OLST


@contextmanager
def write_transaction(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def open_warehouse(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=60, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        with write_transaction(connection):
            app_id = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if app_id == APPLICATION_ID and version == 1:
                return connection
            if app_id or version or connection.execute("SELECT 1 FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' LIMIT 1").fetchone():
                raise ContractError(f"{path} is not a supported Olist warehouse; use a separate --db path")
            # executescript() may commit an existing transaction. Execute complete SQL statements instead.
            sql = ""
            for line in Path(__file__).with_name("schema.sql").read_text(encoding="utf-8").splitlines(keepends=True):
                sql += line
                if sqlite3.complete_statement(sql):
                    connection.execute(sql)
                    sql = ""
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
            connection.execute("PRAGMA user_version = 1")
        return connection
    except BaseException:
        connection.close()
        raise


def selected_months(root, through):
    if through is not None and not MONTH.fullmatch(through):
        raise ContractError("--through must be a purchase month in YYYY-MM format")
    if not root.is_dir():
        raise ContractError(f"batch directory not found: {root}")
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))
    for path in dirs:
        if not MONTH.fullmatch(path.name):
            raise ContractError(f"unexpected directory {path.name!r} under {root}; expected YYYY-MM")
    if not dirs:
        raise ContractError(f"no YYYY-MM batch directories found under {root}")
    return [path for path in dirs if through is None or path.name <= through]


def read_bundle(folder):
    content = {}
    hashes = {}
    manifest = hashlib.sha256()
    for table in HEADERS:
        name = f"{table}.csv"
        try:
            payload = (folder / name).read_bytes()
        except OSError as exc:
            raise ContractError(f"cannot read {name}: {exc.strerror}") from exc
        content[table] = payload
        hashes[table] = hashlib.sha256(payload).hexdigest()
        manifest.update(f"{name}\t{hashes[table]}\n".encode("ascii"))
    return content, hashes, manifest.hexdigest()


def load_month(connection, folder):
    month = folder.name
    digest = None
    try:
        content, hashes, digest = read_bundle(folder)
        with write_transaction(connection):
            receipt = connection.execute("SELECT sha256 FROM batches WHERE month = ?", (month,)).fetchone()
            if receipt is not None:
                if receipt[0] == digest:
                    return "skipped"
                raise ContractError("already loaded with different bytes; restore the original files or rebuild all corrected cohorts into a new database")
            connection.execute("INSERT INTO batches(month, sha256) VALUES (?, ?)", (month, digest))
            for table in HEADERS:
                count = 0
                # Each destination has the source columns plus its owning month.
                placeholders = ", ".join("?" for _ in range(len(HEADERS[table]) + 1))
                statement = f"INSERT INTO {table} VALUES ({placeholders})"
                for line_no, fields in parsed_rows(month, table, content[table]):
                    try:
                        connection.execute(statement, fields)
                    except sqlite3.IntegrityError as exc:
                        raise ContractError(f"{table}.csv line {line_no}: {exc}") from exc
                    count += 1
                if table == "orders" and count == 0:
                    raise ContractError("orders.csv has no orders; supply a complete, nonempty purchase cohort")
                connection.execute("INSERT INTO batch_files VALUES (?, ?, ?, ?)",
                                   (month, f"{table}.csv", hashes[table], count))
            unused = connection.execute("""
                SELECT c.customer_id FROM customers c
                WHERE c.month = ? AND NOT EXISTS (
                    SELECT 1 FROM orders o WHERE o.month = c.month AND o.customer_id = c.customer_id
                ) LIMIT 1
                """, (month,)).fetchone()
            if unused:
                raise ContractError(f"customer {unused[0]!r} has no orders in this purchase cohort")
        return "loaded"
    except ContractError as exc:
        # The data transaction already rolled back. Keep failure evidence separately.
        connection.execute("INSERT INTO load_failures(month, attempted_sha256, reason) VALUES (?, ?, ?)",
                           (month, digest, str(exc)))
        raise ContractError(f"{month}: {exc}") from exc


def publish(connection, output):
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        # Serialize publication with other writers to this DB. Query and replace under the
        # same lock, so a slower exporter cannot overwrite a newer committed state.
        with write_transaction(connection):
            query = "SELECT " + ", ".join(ORDER_FIELDS) + " FROM order_snapshot ORDER BY order_id"
            count = 0
            with tempfile.NamedTemporaryFile(mode="w", newline="", encoding="utf-8", prefix=".orders-",
                                             suffix=".tmp", dir=output.parent, delete=False) as handle:
                pending = Path(handle.name)
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(ORDER_FIELDS)
                for row in connection.execute(query):
                    writer.writerow(row)
                    count += 1
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(pending, output)
        return count
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def run(batches, db, out, through=None):
    batches, db, out = (Path(path).resolve() for path in (batches, db, out))
    if db == out or db.is_relative_to(batches) or out.is_relative_to(batches):
        raise ContractError("use separate database and output paths outside the batch input directory")
    folders = selected_months(batches, through)
    result = {"loaded": [], "skipped": []}
    connection = open_warehouse(db)
    try:
        for folder in folders:
            action = load_month(connection, folder)
            result[action].append(folder.name)
        result["orders_exported"] = publish(connection, out)
        return result
    finally:
        connection.close()
