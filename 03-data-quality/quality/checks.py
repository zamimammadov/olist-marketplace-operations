"""Validate each record, then reconcile monthly measures across both sources."""

import csv
import io
import re
import sqlite3
from collections import Counter
from datetime import date

METRICS = (
    "order_count", "item_count", "payment_count",
    "item_value_cents", "freight_cents", "payment_value_cents",
)
ORDER_HEADER = (
    "order_id", "purchase_date", "customer_state", "order_status", "delivered_date",
    "estimated_date", *METRICS[1:],
)
CONTROL_HEADER = ("purchase_month", *METRICS)
STATES = set("AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split())
STATUSES = set("approved canceled created delivered invoiced processing shipped unavailable".split())
ORDER_ID = re.compile(r"[0-9a-f]{32}")
INTEGER = re.compile(r"0|[1-9][0-9]*")
DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
MONTH = re.compile(r"[0-9]{4}-[0-9]{2}")
# Keeps SQLite monthly sums safe for the source's 100k-order scale.
MAX_INTEGER = 10**12

RULES = {
    "FILE_SCHEMA": ("error", "Correct the header or malformed CSV and resubmit."),
    "EMPTY_FILE": ("error", "Check the requested period and input selection."),
    "ORDER_ID": ("error", "Restore the source order identifier."),
    "DUPLICATE_ORDER": ("error", "Check the mart grain and replay logic."),
    "ORDER_DOMAIN": ("error", "Check the order status or customer-state mapping."),
    "ORDER_DATE": ("error", "Inspect date parsing and required purchase dates."),
    "ORDER_MEASURE": ("error", "Check integer counts and BRL minor-unit conversion."),
    "CHILD_TOTAL": ("error", "Check the child aggregation and missing-child defaults."),
    "ORDER_CHRONOLOGY": ("warning", "Review source timestamps before analysing lead times."),
    "DELIVERY_COVERAGE": ("warning", "Inspect the missing delivery evidence or status mapping."),
    "VALUE_VARIANCE": ("warning", "Investigate payment versus merchandise and freight in the source."),
    "CONTROL_ROW": ("error", "Rebuild the independent controls from the original source."),
    "CONTROL_SCOPE": ("error", "Align purchase cohorts in the mart and source controls."),
    "CONTROL_TOTAL": ("error", "Investigate the month, measure and both source child aggregates."),
}
FINDING_HEADER = ("rule_id", "severity", "source", "source_line", "order_id", "purchase_month",
                  "field", "observed", "expected", "action")
COMPARISON_HEADER = ("purchase_month", "measure", "source_value", "mart_value", "mart_minus_source", "result")


class Results:
    def __init__(self):
        self.findings = []
        self.comparisons = []
        self.counts = Counter()
        self.profile = Counter({
            "order_rows": 0, "typed_order_rows": 0, "control_rows": 0,
            "source_months": 0, "order_months": 0, "checks_compared": 0,
            "eligible_delivered_rows": 0, "on_time_rows": 0, "late_rows": 0,
            "delivered_missing_date_rows": 0, "payment_gap_gross_cents": 0,
        })

    def add(self, rule, source, *, line="", oid="", month="", field="", observed="", expected=""):
        severity, action = RULES[rule]
        self.counts[rule] += 1
        self.findings.append(dict(zip(FINDING_HEADER, (
            rule, severity, source, line, oid, month, field, observed, expected, action,
        ))))


def records(content, header, source, result):
    stream = io.TextIOWrapper(io.BytesIO(content), encoding="utf-8-sig", newline="")
    reader = csv.reader(stream, strict=True)
    try:
        names = tuple(next(reader, ()))
        if names != header:
            result.add("FILE_SCHEMA", source, line=1, field="header",
                       observed=",".join(names), expected=",".join(header))
            return
        rows = 0
        for row in reader:
            rows += 1
            if len(row) != len(header):
                result.add("FILE_SCHEMA", source, line=reader.line_num, field="column_count",
                           observed=len(row), expected=len(header))
                continue
            yield reader.line_num, dict(zip(header, row))
        if not rows:
            result.add("EMPTY_FILE", source, line=1, expected="At least one data row")
    except (UnicodeDecodeError, csv.Error) as exc:
        result.add("FILE_SCHEMA", source, line=reader.line_num, observed=str(exc), expected="UTF-8 CSV")


def parse_count(value):
    if len(value) > 13 or not INTEGER.fullmatch(value):
        raise ValueError(value)
    integer = int(value)
    if integer > MAX_INTEGER:
        raise ValueError(value)
    return integer


def parse_date(value):
    if not DATE.fullmatch(value):
        raise ValueError(value)
    return date.fromisoformat(value)


def load_order(row, line, sql, result):
    result.profile["order_rows"] += 1
    oid = row["order_id"]
    ctx = {"line": line, "oid": oid}
    valid = True
    if not ORDER_ID.fullmatch(oid):
        result.add("ORDER_ID", "orders", **ctx, field="order_id", observed=oid, expected="32 lowercase hex characters")
        valid = False
    else:
        sql.execute("insert into identities values (?, ?)", (oid, line))
    for field, allowed in [("customer_state", STATES), ("order_status", STATUSES)]:
        if row[field] not in allowed:
            result.add("ORDER_DOMAIN", "orders", **ctx, field=field, observed=row[field],
                       expected="One of the documented source codes")
            valid = False
    for field in ("purchase_date", "delivered_date", "estimated_date"):
        if field != "purchase_date" and not row[field]:
            continue
        try:
            parse_date(row[field])
        except ValueError:
            result.add("ORDER_DATE", "orders", **ctx, field=field, observed=row[field], expected="ISO calendar date")
            valid = False
    measures = {}
    for field in METRICS[1:]:
        try:
            measures[field] = parse_count(row[field])
        except ValueError:
            result.add("ORDER_MEASURE", "orders", **ctx, field=field, observed=row[field],
                       expected=f"Integer between 0 and {MAX_INTEGER}")
            valid = False
    if not valid:
        return
    month = row["purchase_date"][:7]
    ctx["month"] = month
    result.profile["typed_order_rows"] += 1
    sql.execute("insert into orders values (?,?,?,?,?,?)", (month, *measures.values()))
    for count, amounts in [("item_count", ("item_value_cents", "freight_cents")),
                           ("payment_count", ("payment_value_cents",))]:
        for amount in amounts:
            if measures[count] == 0 and measures[amount] != 0:
                result.add("CHILD_TOTAL", "orders", **ctx, field=amount,
                           observed=measures[amount], expected=f"0 when {count}=0")
    gap = measures["payment_value_cents"] - measures["item_value_cents"] - measures["freight_cents"]
    if gap:
        result.profile["payment_gap_gross_cents"] += abs(gap)
        result.add("VALUE_VARIANCE", "orders", **ctx, field="payment_minus_items_and_freight_cents",
                   observed=gap, expected="0 for an exact match")
    purchased, delivered, estimated = (row[f] for f in ("purchase_date", "delivered_date", "estimated_date"))
    for field in ("delivered_date", "estimated_date"):
        if row[field] and row[field] < purchased:
            result.add("ORDER_CHRONOLOGY", "orders", **ctx, field=field,
                       observed=row[field], expected=f"On or after {purchased}")
    if row["order_status"] == "delivered":
        if delivered and estimated:
            result.profile["eligible_delivered_rows"] += 1
            result.profile["on_time_rows" if delivered <= estimated else "late_rows"] += 1
        else:
            result.profile["delivered_missing_date_rows"] += 1
            result.add("DELIVERY_COVERAGE", "orders", **ctx, field="delivered_date / estimated_date",
                       observed=f"{delivered} / {estimated}", expected="Both dates for on-time reporting")
    elif delivered:
        result.add("DELIVERY_COVERAGE", "orders", **ctx, field="order_status",
                   observed=row["order_status"], expected="Check final status against the recorded delivery date")


def load_controls(raw, result):
    controls = {}
    for line, row in records(raw, CONTROL_HEADER, "controls", result):
        result.profile["control_rows"] += 1
        month = row["purchase_month"]
        try:
            if not MONTH.fullmatch(month):
                raise ValueError
            parse_date(month + "-01")
        except ValueError:
            result.add("CONTROL_ROW", "controls", line=line, month=month, field="purchase_month",
                       observed=month, expected="Valid YYYY-MM")
            continue
        if month in controls:
            result.add("CONTROL_ROW", "controls", line=line, month=month, field="purchase_month",
                       observed=month, expected="One control row per month")
            continue
        try:
            controls[month] = {key: parse_count(row[key]) for key in METRICS}
        except ValueError as exc:
            result.add("CONTROL_ROW", "controls", line=line, month=month,
                       observed=str(exc), expected="Valid integer counts and cent totals")
    return controls


def run_checks(orders, controls):
    result = Results()
    expected = load_controls(controls, result)
    sql = sqlite3.connect(":memory:")
    try:
        sql.execute("create table identities (order_id text, line integer)")
        sql.execute("create table orders (month text, items integer, payments integer, price integer, freight integer, paid integer)")
        for line, row in records(orders, ORDER_HEADER, "orders", result):
            load_order(row, line, sql, result)
        for oid, line, first_line in sql.execute("""
            with keys as (
                select order_id, line, min(line) over (partition by order_id) as first_line
                from identities
            )
            select order_id, line, first_line from keys where line <> first_line order by line
        """):
            result.add("DUPLICATE_ORDER", "orders", oid=oid, line=line, field="order_id",
                       observed=f"Repeated from line {first_line}", expected="One published row per order")
        actual = {m: dict(zip(METRICS, values)) for m, *values in sql.execute("""
            select month, count(*), sum(items), sum(payments), sum(price), sum(freight), sum(paid)
            from orders group by month
        """)}
    finally:
        sql.close()
    result.profile["source_months"] = len(expected)
    result.profile["order_months"] = len(actual)
    for month in sorted(actual.keys() | expected.keys()):
        if month not in expected or month not in actual:
            if month in expected and all(x == 0 for x in expected[month].values()):
                continue
            result.add("CONTROL_SCOPE", "reconciliation", month=month, field="purchase_month",
                       observed="present" if month in actual else "absent", expected="present" if month in expected else "absent")
            continue
        for metric in METRICS:
            result.profile["checks_compared"] += 1
            source_value = expected[month][metric]
            mart_value = actual[month][metric]
            difference = mart_value - source_value
            result.comparisons.append((month, metric, source_value, mart_value, difference,
                                       "MISMATCH" if difference else "MATCH"))
            if difference:
                result.add("CONTROL_TOTAL", "reconciliation", month=month, field=metric,
                           observed=mart_value, expected=source_value)
    return result
