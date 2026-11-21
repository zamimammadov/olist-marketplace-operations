# Olist order integration

A delivery analyst needs one trustworthy row per order before comparing regions or tracking delivery promises. The source stores lines, freight and payment tenders at different grains. This pipeline imports four related Olist datasets into SQLite and publishes a CSV that a reporting model can consume directly.

The included purchase cohorts cover January through March 2017. They contain **5,262 genuine orders, 5,906 item lines and 5,573 payment records**. The same loader supports the full Kaggle archive. Olist published an anonymised historical snapshot. These batches replay purchases by month from that snapshot, so their status and delivery fields reflect the final available information. To examine which milestones had occurred by a historical cutoff, use the [event-time replay](docs/lifecycle.md).

## Run a three-month load

Python 3.11+ with its standard SQLite module is sufficient. Run these commands from this directory. Keep the same database and output path between runs.

```sh
python -m pipeline --through 2017-01
python -m pipeline --through 2017-02
python -m pipeline --through 2017-03
python -m pipeline
```

| Call | New purchase cohort | Orders added | Rows in published CSV |
| --- | --- | ---: | ---: |
| Through January | 2017-01 | 800 | 800 |
| Through February | 2017-02 | 1,780 | 2,580 |
| Through March | 2017-03 | 2,682 | 5,262 |
| Repeat all three | All three already accepted | 0 | 5,262 |

The command prints a JSON result with `loaded`, `skipped` and `orders_exported`. Defaults are `--batches data/batches --db build/warehouse.sqlite --out build/orders.csv`. The optional `--through YYYY-MM` selects incoming directories. Publication always includes everything already committed to the selected database.

## How one delivery becomes a warehouse row

| Source | Grain and join |
| --- | --- |
| `customers.csv` | One customer ID per cohort. Orders resolve customer state by month and customer ID. A shopper's `customer_unique_id` can occur under different `customer_id` values. |
| `orders.csv` | One row per globally unique order ID. Purchase timestamp determines its monthly directory. Optional lifecycle timestamps are retained in the warehouse. |
| `order_items.csv` | One order line per `(order_id, order_item_id)`. The pipeline sums item price and freight in BRL cents separately. |
| `order_payments.csv` | One payment record per `(order_id, payment_sequential)`. A split payment can have more than one row per order. |

A real order in this extract, `08bdbdeaf1626720051193300f93806e`, has four item lines and two payments. Joining these tables on order ID before aggregation would produce eight rows, duplicating each item twice and each payment four times. The warehouse aggregates each child table separately, then left-joins each order-level result to the order. The four items sum to R$396.00 and freight is R$77.12. The two payment records sum to R$473.12. The 99 cohort orders with zero item lines remain in the extract.

The output contains `order_id`, purchase date, customer state, final source status, actual and estimated delivery dates, item and payment counts, item value, freight and payment value. Monetary fields use integer cents parsed from base-ten source strings. Missing child rows yield zero counts and sums. Missing dates remain blank. A difference between payment and item-plus-freight totals is preserved for investigation. The downstream analytical contract is expressed in [the order_snapshot view](pipeline/schema.sql).

## Audit, retries and corrections

Each sealed monthly directory must contain the four named CSVs with their original headers. An incoming bundle is small enough to hold one cohort in memory. The loader hashes the same bytes it parses. It checks keys, foreign keys, dates, integer bounds, currency precision, the purchase month and field counts, and commits all four tables and their source receipts in one SQLite transaction. Publish immutable input directories only after all four files are ready.

* **Identical replay.** Four SHA-256 file digests form a stable monthly fingerprint. Repeated bytes skip row ingestion. Publication sorts by order ID and atomically replaces the destination CSV with a fresh whole-warehouse snapshot.
* **Late delivery.** A previously absent month can arrive after a later month. The batch registry tracks specific months, so filling a gap leaves other months intact.
* **Bad month.** The loader stops, rolls back the failing month and logs the offending file and row when available. Earlier committed months stay in the database. The last successfully published CSV remains available. Fix the failed inputs, then rerun the command. A publication failure can also be retried using the committed database.
* **Changed historical month.** Different bytes under an accepted month trigger an error, including if only the line endings changed. For an intentional restatement, use the complete, corrected set of source directories, a **new** database and an output filename for that version. Check totals against the revised source controls before promoting it.
* **Concurrent loads.** A SQLite write transaction protects each cohort and serializes snapshot publication for writers using the same warehouse. Each distinct warehouse should have its own output path.

Use a SQLite client such as DBeaver or the SQLite command line to inspect operations.

```sql
SELECT month, filename, row_count, sha256 FROM batch_files ORDER BY month, filename;
SELECT month, attempted_utc, reason FROM load_failures ORDER BY attempt_id DESC;
SELECT count(*) AS orders, sum(item_count) AS item_lines,
       sum(payment_count) AS payment_rows, sum(item_value_cents) AS item_value_cents
FROM order_snapshot;
```

For the included cohort, the last query returns `5262, 5906, 5573, 74196019`. Unchanged monthly files are rehashed when selected, and each publication scans the current warehouse. This keeps auditability and a deterministic flat file within the size of the published dataset. The archive supplies no arrival timestamps for edits to deliveries or payment records.

## Recreate the extracts or run all available history

Download version 2 of the [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce). Save the archive as `olist.zip`. Standard-library preparation selects four of the nine source files, preserves the fields, writes monthly deliveries and independently calculates reference amounts by reading source rows.

```sh
python tools/prepare_source.py --archive olist.zip --start 2017-01 --end 2017-03 --batches build/rebuilt-batches --reference-dir build/reference
python -m pipeline --batches build/rebuilt-batches --db build/rebuilt.sqlite --out build/rebuilt-orders.csv
python tools/prepare_source.py --archive olist.zip --all --batches build/full-batches --reference-dir build/full-reference
python -m pipeline --batches build/full-batches --db build/full.sqlite --out build/full-orders.csv
```

Source preparation produces `source_controls.csv` (monthly counts and sums for the independent quality gate) and a separate `orders.csv` reference. Keep those outputs apart from the warehouse publication. The full run was compared byte-for-byte with the archive-derived reference, yielding **99,441 orders across 25 purchase months** from September 2016 to October 2018. November 2016 has no source orders. The archive SHA-256 is `967e41e04fc306fe604e2a693f488995a8b41e5047418f8a5c8e4abd6deca784`.

| Export scope | SHA-256 of sorted UTF-8 CSV |
| --- | --- |
| Q1 2017 | `703d4c573fe7dbc9f085ada05cb03fd35f406c334a16a24706ac52d87be8be22` |
| Full archive | `167dea8d80184df2fc46cd3fde0cbd36bd68731bdb46a318ea8ef5e5c1df99de` |

## Test

```sh
python -m unittest discover -s tests -v
```

The loader tests read copies of the real included data. They verify three incremental steps and replay, late-month backfill, a genuine split-payment order, committed metadata, rollback after a broken foreign key, recovery from a publication failure, rejection of changed or empty deliveries, and two concurrent runs against one warehouse.

Olist's dataset and data derivatives are shared under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). The bundled raw files are filtered by purchase cohort and reserialised. Reporting extracts also aggregate order lines and payments.
