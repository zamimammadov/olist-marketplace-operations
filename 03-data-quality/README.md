# Olist order data quality gate

Can the daily operations team release the order dataset to Power BI? This gate takes one row per order, checks the stated grain, measures and delivery dates, and compares the results with independent controls from Olist’s original order, item and payment tables. A reviewer can follow an exception back to the exact records that produced it.

## Run the release check

Run these commands from this project directory with Python 3.11 or later. The project uses Python’s built-in SQLite and works offline with the data included here.

```sh
python -m quality --input data/orders.csv --controls data/source_controls.csv --out build/quality
python -m unittest discover -s tests -v
```

The first command prints the release decision and run identifier. Open `build/quality/latest.json` and follow its `evidence` path inside `build/quality`. A scheduler can use the process exit code.

| Exit code | Decision | Action |
|---|---|---|
| 0 | PASS | Release the order dataset. |
| 0 | PASS_WITH_WARNINGS | Release with recorded caveats. The reviewer can investigate individual warnings before interpreting the affected metric. |
| 1 | HOLD | The run completed and found blocking errors. Correct the delivery or its source controls and rerun. |
| 2 | Execution failure | Check file paths, permissions or output storage. The previously published evidence stays available. |

## Data and independent controls

The included cohort contains all 5,262 orders purchased in January, February and March 2017. The order status and delivery fields reflect the archive’s final observation. A March purchase delivered in April still belongs to the March purchase cohort. All monetary amounts use integer centavos (100 per Brazilian real).

`data/orders.csv` is the reporting snapshot. `data/source_controls.csv` contains one row per purchase month. The portfolio’s [source preparation script](../01-integration/tools/prepare_source.py) derives six controls directly from the original CSVs. Order counts come from orders, prices and freight come from item rows, and paid amounts come from payments. The control calculation reads each child table separately to avoid multiplying amounts on orders with both multiple payments and multiple items.

The gate accepts a replacement pair of files through its CLI. Both must cover the same set of purchase months. To check a February-only release, supply the February extracts for both. To validate the cumulative warehouse after adding February, provide January and February controls. Run the gate before triggering a production-style refresh. The gate and Power BI refresh are separate commands in this portfolio. Dataset credit and licence: [Brazilian E-Commerce Public Dataset by Olist, CC BY-NC-SA 4.0](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce).

## What the verified release contains

| Check | Original cohort | Deliberately damaged copy |
|---|---:|---:|
| Order rows supplied | 5,262 | 5,263 |
| Blocking findings | 0 | 15 |
| Warning findings | 132 | 133 |
| Monthly checks matched | 18 of 18 | 5 of 18 |
| Decision | PASS_WITH_WARNINGS | HOLD |

The 132 baseline exceptions are orders where payments differ from item prices plus freight. All six independent measures still reconcile in each month. An unexplained price difference is a reason to inspect the underlying source. The gate preserves the amounts. It does not equate the difference with a loss or assume a refund.

Of 4,949 orders with delivered status and both dates, 4,762 arrived on or before the estimated calendar date (96.22%). The other 187 are late deliveries. They count towards the delivery metric and generate no quality error for being late. Orders that are still in transit at the archive’s final observation are outside this denominator.

The [baseline summary](examples/baseline/summary.json) and [comparison by month and measure](examples/baseline/monthly_controls.csv) are actual output. The small example findings files keep one representative row per observed rule. Each real run writes all findings.

### Full archive check

As a separate scale check, the gate also ran against the order-level reference extract prepared from all 99,441 original orders (25 nonempty purchase months, September 2016 through October 2018). All 150 period/measure comparisons agreed with the raw-table controls. The result had 1,349 payment-value warnings and 14 delivery-coverage warnings. The checked-in dashboard and baseline evidence use the smaller three-month release above. [Full-source check](examples/full-source-check/summary.json)

## An order a reviewer can trace

Order `0cafd6a7576a6aae0f891008a87f1546` has a BRL 42.02 payment gap. In the January source files, the order has status `unavailable`, zero item rows, a BRL 10.90 card payment and a BRL 31.12 voucher payment. The published order matches all these measures. The available source explains where the reported gap comes from, but provides no refund or bank settlement record.

In the complete portfolio checkout, reproduce the [source trace](examples/baseline/source_trace.json) with:

```sh
python -m quality.trace --batch ../01-integration/data/batches/2017-01 --input data/orders.csv --order-id 0cafd6a7576a6aae0f891008a87f1546
```

For a standalone checkout, pass the path to a month from the integration project. The trace reads the original order, item and payment CSVs. It returns source line numbers, child counts and cents, and compares those figures with every matching row in the reporting file. Exit 0 indicates one published row with matching measures, exit 1 a missing, repeated or mismatching published row, and exit 2 an invalid source, order ID or path.

## Failure and recovery drill

Create a new output filename for each injected copy. The injector will not overwrite an existing file.

```sh
python -m quality.inject --input data/orders.csv --out build/fault-drill/orders.csv
python -m quality --input build/fault-drill/orders.csv --controls data/source_controls.csv --out build/quality
```

The second command is expected to return exit 1. The drill duplicates one order, sets another order’s customer state to `ZZ`, and increases a third order’s payment by BRL 7.00. The month controls stay untouched. One fault can trigger several findings. The supplied example yields one `ORDER_DOMAIN`, one `DUPLICATE_ORDER`, and thirteen `CONTROL_TOTAL` errors. A row with an invalid domain is excluded from typed sums, while a typed duplicate still contributes to those sums. This exposes both the bad key and its effect on the report. Review [the held decision](examples/fault-drill/summary.json) and [the signed deltas](examples/fault-drill/monthly_controls.csv).

To recover in this drill, rerun the first release command against the intact input. Its original run ID and decision return. The held run remains available. In a live process the equivalent step would be to obtain a corrected extract and rerun the same controls.

## Rules

| Rule | Level | Grain and condition |
|---|---|---|
| FILE_SCHEMA | Error | Either file. Require the published UTF-8 CSV header in order, valid quoting and exactly the expected number of fields. |
| EMPTY_FILE | Error | Either file with headers and no data rows. A blank file fails FILE_SCHEMA. |
| ORDER_ID | Error | Order. Require a 32-character lowercase hexadecimal Olist identifier. |
| DUPLICATE_ORDER | Error | Order ID. Record every repeat and the source line of its first occurrence. |
| ORDER_DOMAIN | Error | Order. Require a recognized Brazilian state and one of the source order status codes. |
| ORDER_DATE | Error | Order. Require a valid calendar purchase date. Optional delivery and estimate fields must be valid dates when populated. |
| ORDER_MEASURE | Error | Order. Require counts and monetary values as nonnegative integers within the supported range of 0 to 10¹². |
| CHILD_TOTAL | Error | Order. Zero items imply zero item value and freight. Zero payment rows imply zero paid value. Zero-valued source rows remain valid. |
| ORDER_CHRONOLOGY | Warning | Order. Flag an actual or estimated delivery date before the purchase date. |
| DELIVERY_COVERAGE | Warning | Order. Flag delivered status without both required dates, or a delivery date with a different final status. |
| VALUE_VARIANCE | Warning | Order. Compare payment cents against item value plus freight at zero tolerance and record the signed difference. |
| CONTROL_ROW | Error | Source control. Require one row per valid YYYY-MM and six valid integer measures. |
| CONTROL_SCOPE | Error | Purchase month. Flag missing or unexpected months. A source-only month with six explicit zeros is valid when other months contain data. |
| CONTROL_TOTAL | Error | Purchase month and measure. Compare mart values with independent order count, item count, payment count, item value, freight and paid value, at zero tolerance. |

## Evidence, repeatability and scope

Each run creates three files under `runs/<run_id>/`:

- `summary.json` groups the decision, severity counts, coverage and calculated delivery figures. A zero eligible denominator gives a null on-time rate.
- `findings.csv` records rule, severity, affected file, physical line, order ID or month, observed and expected values, and the next action.
- `monthly_controls.csv` records both sides and the signed difference for every comparable month and measure. Missing periods are reported separately through CONTROL_SCOPE.

The run ID hashes the two inputs and the two gate modules. Unchanged inputs and code reuse the same evidence directory. A complete directory is published before the latest pointer is atomically replaced. The latest pointer tracks the most recently completed run, including a HOLD. A reader deciding whether to refresh must inspect its decision. Concurrent jobs releasing different cohorts should use separate output directories if they require independent latest pointers.

The gate checks the delivered snapshot and the controls for its declared periods. Monthly totals cannot detect two exactly offsetting changes inside one month, or prove that a source system recorded a real-world event correctly. The per-order source trace supports targeted investigation. This implementation holds the cohort and findings in memory; the supplied quarter and the historical Olist archive are appropriate for a local analyst machine.
