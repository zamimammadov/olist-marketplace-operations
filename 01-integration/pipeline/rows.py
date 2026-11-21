"""Parse selected Olist CSVs using their published headers and source keys."""

import csv
from datetime import datetime
import io
import re


HEADERS = {
    "customers": ("customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city", "customer_state"),
    "orders": ("order_id", "customer_id", "order_status", "order_purchase_timestamp", "order_approved_at",
               "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"),
    "order_items": ("order_id", "order_item_id", "product_id", "seller_id", "shipping_limit_date", "price",
                    "freight_value"),
    "order_payments": ("order_id", "payment_sequential", "payment_type", "payment_installments", "payment_value"),
}

MAX_INTEGER = 2**63 - 1


class ContractError(ValueError):
    """A supplied batch failed its delivery contract."""


def required(value, field):
    if not value.strip() or value != value.strip():
        raise ContractError(f"{field} must be populated without surrounding whitespace")
    return value


def integer(value, field, minimum=0):
    if not re.fullmatch(r"[0-9]{1,19}", value):
        raise ContractError(f"{field} must be an unsigned integer")
    number = int(value)
    if number < minimum or number > MAX_INTEGER:
        raise ContractError(f"{field} is outside the supported integer range")
    return number


def cents(value, field):
    # Splitting the original base-ten text avoids binary floats and silent rounding.
    if not re.fullmatch(r"[0-9]{1,17}(?:\.[0-9]{1,2})?", value):
        raise ContractError(f"{field} must be a nonnegative BRL amount with at most two decimal places")
    units, _, fraction = value.partition(".")
    return integer(str(int(units) * 100 + int(fraction.ljust(2, "0"))), field)


def timestamp(value, field, optional=True):
    if value == "" and optional:
        return None
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", value):
        raise ContractError(f"{field} must use YYYY-MM-DD HH:MM:SS")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError(f"invalid {field}: {value}") from exc
    return value


def customers(month, row):
    customer, person, postal, city, state = row
    if not re.fullmatch(r"[A-Z]{2}", state):
        raise ContractError("customer_state must have two uppercase letters")
    if not re.fullmatch(r"[0-9]{1,5}", postal):
        raise ContractError("expected a 1-5 digit postal prefix")
    return month, required(customer, "customer_id"), required(person, "customer_unique_id"), postal, required(city, "customer_city"), state


def orders(month, row):
    order, customer, status, purchase, approved, carrier, delivered, estimate = row
    purchase = timestamp(purchase, "order_purchase_timestamp", optional=False)
    if purchase[:7] != month:
        raise ContractError(f"purchase date {purchase[:10]} is outside purchase cohort {month}")
    return (required(order, "order_id"), month, required(customer, "customer_id"), required(status, "order_status"),
            purchase, timestamp(approved, "order_approved_at"), timestamp(carrier, "order_delivered_carrier_date"),
            timestamp(delivered, "order_delivered_customer_date"), timestamp(estimate, "order_estimated_delivery_date"))


def order_items(month, row):
    order, number, product, seller, cutoff, price, freight = row
    return (month, required(order, "order_id"), integer(number, "order_item_id", 1), required(product, "product_id"),
            required(seller, "seller_id"), timestamp(cutoff, "shipping_limit_date"), cents(price, "price"),
            cents(freight, "freight_value"))


def order_payments(month, row):
    order, sequence, method, installments, amount = row
    return (month, required(order, "order_id"), integer(sequence, "payment_sequential", 1), required(method, "payment_type"),
            integer(installments, "payment_installments"), cents(amount, "payment_value"))


PARSERS = dict(customers=customers, orders=orders, order_items=order_items, order_payments=order_payments)


def parsed_rows(month, table, content):
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ContractError(f"{table}.csv: invalid UTF-8 at byte {exc.start}") from exc
    reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
    try:
        if tuple(next(reader, [])) != HEADERS[table]:
            raise ContractError(f"{table}.csv: expected header {','.join(HEADERS[table])}")
        for row in reader:
            try:
                if len(row) != len(HEADERS[table]):
                    raise ContractError(f"expected {len(HEADERS[table])} fields, received {len(row)}")
                yield reader.line_num, PARSERS[table](month, row)
            except ContractError as exc:
                raise ContractError(f"{table}.csv line {reader.line_num}: {exc}") from exc
    except csv.Error as exc:
        raise ContractError(f"{table}.csv line {reader.line_num}: {exc}") from exc
