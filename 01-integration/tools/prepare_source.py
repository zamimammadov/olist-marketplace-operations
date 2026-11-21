"""Split the official Olist Kaggle ZIP into repeatable purchase-month deliveries.

From 01-integration:
    python tools/prepare_source.py --archive path/to/brazilian-ecommerce.zip
    python tools/prepare_source.py --archive path/to/brazilian-ecommerce.zip --all \
        --batches /tmp/olist-batches --reference-dir /tmp/olist-reference

The default interval is January through March 2017 inclusive. All four CSVs
keep their upstream header and field values. Source ordering is preserved
within each file, with deterministic UTF-8/LF CSV serialization. The tool does
not extract ZIP paths. Only the four explicitly named source files are read.
The reference folder contains a sorted order-level extract and monthly source
controls; counts and sums for those controls accumulate directly from the
individual source rows, separately from the pipeline and its warehouse.
"""
from __future__ import annotations

import argparse
import csv
import filecmp
import os
import re
import tempfile
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import TextIOWrapper
from pathlib import Path
from zipfile import ZipFile


TABLES = {
    'orders': ('order_id', 'customer_id', 'order_status', 'order_purchase_timestamp',
               'order_approved_at', 'order_delivered_carrier_date',
               'order_delivered_customer_date', 'order_estimated_delivery_date'),
    'customers': ('customer_id', 'customer_unique_id', 'customer_zip_code_prefix',
                  'customer_city', 'customer_state'),
    'order_items': ('order_id', 'order_item_id', 'product_id', 'seller_id',
                    'shipping_limit_date', 'price', 'freight_value'),
    'order_payments': ('order_id', 'payment_sequential', 'payment_type',
                       'payment_installments', 'payment_value'),
}
MEASURES = ('item_count', 'payment_count', 'item_value_cents', 'freight_cents',
            'payment_value_cents')
ORDER_FIELDS = ('order_id', 'purchase_date', 'customer_state', 'order_status',
                'delivered_date', 'estimated_date', *MEASURES)
CONTROL_FIELDS = ('purchase_month', 'order_count', *MEASURES)


@dataclass
class Operation:
    month: str
    customer: str
    purchase: str
    status: str
    delivered: str
    estimated: str
    measures: list[int]


def upstream_rows(archive, table):
    member = f'olist_{table}_dataset.csv'
    if archive.namelist().count(member) != 1:
        raise ValueError(f'Archive must contain exactly one {member}')
    with archive.open(member) as raw, TextIOWrapper(raw, encoding='utf-8-sig', newline='') as text:
        reader = csv.DictReader(text)
        if tuple(reader.fieldnames or ()) != TABLES[table]:
            raise ValueError(f'Unexpected columns in {member}: {reader.fieldnames}')
        for row in reader:
            if None in row or None in row.values():
                raise ValueError(f'Invalid record in {member} at line {reader.line_num}')
            yield row


def month_arg(value):
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', value):
        raise argparse.ArgumentTypeError('use YYYY-MM')
    return value


def cents(text):
    exact = Decimal(text) * 100
    if not exact.is_finite() or exact != exact.to_integral_value():
        raise ValueError(f'Amount cannot be represented exactly in cents: {text!r}')
    return int(exact)


def csv_output(stack, path, fields):
    path.parent.mkdir(exist_ok=True, parents=True)
    stream = stack.enter_context(path.open('w', encoding='utf-8', newline=''))
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(fields)
    return writer


def partition(archive, stage, reference, start, end):
    orders = {}
    source_order_ids = set()
    customers = defaultdict(set)
    states = {}
    source_controls = defaultdict(lambda: [0] * (len(MEASURES) + 1))

    # Keep IDs and the few attributes needed for a source reference in memory.
    # Raw rows are streamed directly to disk; each source file is read once.
    def stream_table(name, consume):
        with ExitStack() as files:
            writers = {}

            def emit(month, row):
                if month not in writers:
                    writers[month] = csv_output(files, stage / month / f'{name}.csv', TABLES[name])
                writers[month].writerow([row[key] for key in TABLES[name]])

            for row in upstream_rows(archive, name):
                consume(row, emit)
            # Every delivery has four files, even a cohort without a child row.
            for month in source_controls.keys() - writers.keys():
                csv_output(files, stage / month / f'{name}.csv', TABLES[name])

    def order(row, emit):
        key = row['order_id']
        if key in source_order_ids:
            raise ValueError(f'Duplicate source order {key}')
        source_order_ids.add(key)
        purchase = date.fromisoformat(row['order_purchase_timestamp'][:10]).isoformat()
        month = purchase[:7]
        if (start and month < start) or (end and month > end):
            return
        customer_id = row['customer_id']
        customers[customer_id].add(month)
        orders[key] = Operation(month, customer_id, purchase, row['order_status'],
                                row['order_delivered_customer_date'][:10],
                                row['order_estimated_delivery_date'][:10],
                                [0] * len(MEASURES))
        source_controls[month][0] += 1
        emit(month, row)

    def customer(row, emit):
        key = row['customer_id']
        if key not in customers:
            return
        if key in states:
            raise ValueError(f'Duplicate source customer {key}')
        states[key] = row['customer_state']
        for month in sorted(customers[key]):
            emit(month, row)

    def selected_order(row, table):
        key = row['order_id']
        if key not in source_order_ids:
            raise ValueError(f'{table} references unknown source order {key}')
        return orders.get(key)

    def item(row, emit):
        current = selected_order(row, 'order_items')
        if current is None:
            return
        price, freight = cents(row['price']), cents(row['freight_value'])
        current.measures[0] += 1
        current.measures[2] += price
        current.measures[3] += freight
        totals = source_controls[current.month]
        totals[1] += 1
        totals[3] += price
        totals[4] += freight
        emit(current.month, row)

    def payment(row, emit):
        current = selected_order(row, 'order_payments')
        if current is None:
            return
        value = cents(row['payment_value'])
        current.measures[1] += 1
        current.measures[4] += value
        totals = source_controls[current.month]
        totals[2] += 1
        totals[5] += value
        emit(current.month, row)

    stream_table('orders', order)
    if not orders:
        raise ValueError('No orders in the requested date range')
    stream_table('customers', customer)
    missing = customers.keys() - states.keys()
    if missing:
        raise ValueError(f'Missing customer rows for {len(missing)} selected keys')
    stream_table('order_items', item)
    stream_table('order_payments', payment)

    with ExitStack() as files:
        order_file = csv_output(files, reference / 'orders.csv', ORDER_FIELDS)
        controls_file = csv_output(files, reference / 'source_controls.csv', CONTROL_FIELDS)
        for key, current in sorted(orders.items()):
            order_file.writerow([key, current.purchase, states[current.customer], current.status,
                                 current.delivered, current.estimated, *current.measures])
        for month, totals in sorted(source_controls.items()):
            controls_file.writerow([month, *totals])
    return len(orders), sorted(source_controls)


def prepare(archive_path, batches, reference, start=None, end=None):
    batches = batches.resolve()
    reference = reference.resolve()
    if batches == reference or batches in reference.parents or reference in batches.parents:
        raise ValueError('Use separate directories for batches and references')
    batches.mkdir(parents=True, exist_ok=True)
    reference.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=batches.parent) as batch_work, \
         tempfile.TemporaryDirectory(dir=reference.parent) as reference_work:
        staging, staging_ref = Path(batch_work), Path(reference_work)
        with ZipFile(archive_path) as archive:
            n, months = partition(archive, staging, staging_ref, start, end)
        # Validate existing deliveries before publishing anything. An identical
        # rerun reuses the previous folder. A different input gets a fresh path.
        for month in months:
            target = batches / month
            if not target.exists():
                continue
            files = [f'{t}.csv' for t in TABLES]
            if not target.is_dir() or sorted(p.name for p in target.iterdir()) != sorted(files):
                raise ValueError(f'Unexpected contents in existing batch {target}; choose a fresh --batches directory')
            if not all(filecmp.cmp(target / f, staging / month / f, shallow=False) for f in files):
                raise ValueError(f'Existing batch differs: {target}; choose a fresh --batches directory')
        for month in months:
            if not (batches / month).exists():
                os.rename(staging / month, batches / month)
        for name in ('orders.csv', 'source_controls.csv'):
            os.replace(staging_ref / name, reference / name)
        return n, months


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--archive', required=True, type=Path, help='original Olist Kaggle ZIP')
    parser.add_argument('--batches', type=Path, default=Path('data/batches'))
    parser.add_argument('--reference-dir', type=Path, default=Path('build/source-reference'),
                        help='reference CSVs for the selected interval only')
    parser.add_argument('--start', type=month_arg, help='inclusive purchase month (default 2017-01)')
    parser.add_argument('--end', type=month_arg, help='inclusive purchase month (default 2017-03)')
    parser.add_argument('--all', action='store_true', help='all purchase months in the archive')
    args = parser.parse_args()
    if args.all and (args.start or args.end):
        parser.error('--all cannot be combined with --start or --end')
    start = None if args.all else args.start or '2017-01'
    end = None if args.all else args.end or '2017-03'
    if start and end and start > end:
        parser.error('--start must not follow --end')
    n, months = prepare(args.archive, args.batches, args.reference_dir, start, end)
    print(f'{n:,} orders; {len(months)} purchase months ({months[0]} to {months[-1]}).')
    print(f'Monthly input: {args.batches}\nSelected-source references: {args.reference_dir}')


if __name__ == '__main__':
    main()
