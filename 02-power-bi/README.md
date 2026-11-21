# Olist marketplace operations · Power BI

An order can include multiple items and several payment records. This report gives an operations analyst one row per order to investigate purchase volumes, freight and payment amounts, and deliveries against the promised date. The standalone demo covers **5,262 purchases** from January through March 2017.

[Open the native Power BI project](Olist%20Operations.pbip) · [Read the fulfilment review (PDF)](examples/fulfilment-review.pdf) · [Verification results](tools/VERIFICATION.md)

## Open and refresh

1. Extract the full project folder on Windows. Keep the `.pbip`, `.Report` and `.SemanticModel` files and folders together.
2. Open **Olist Operations.pbip** in a recent Power BI Desktop with support for Power BI projects, the enhanced report format (PBIR) and TMDL. If Desktop prompts for the associated preview features, enable them under **File → Options and settings → Options → Preview features** and restart.
3. Click **Home → Refresh** to populate the local import model. The default Power Query parameter, `OrdersCsvPath`, is empty. Power Query reads a compressed copy of the included real CSV embedded in the model, so the sample can refresh offline without choosing a file location. Local Desktop caches are created on the first refresh.
4. On **Order activity**, confirm **5,262** orders. Select **2017-03** from the month slicer to see **2,682**. On **Delivery reliability**, select March again to see **116** late orders and **95.4%** on time. Slicers apply to their own page.
5. To use the warehouse export, choose **Home → Transform data → Edit parameters**, set `OrdersCsvPath` to the absolute path of the exported CSV (for example, `C:\Olist\warehouse\orders.csv`), apply and refresh. Leave the path empty to return to the embedded sample. Power BI Desktop can also save a local `.pbix` after refresh.

The warehouse ingests purchase cohorts incrementally; Power BI imports the selected CSV in full on refresh. To update the embedded default for another shareable version, run `python tools/embed_snapshot.py --input path/to/orders.csv` from this folder and bring `data/orders.csv` into line with that same file.

## Read the report

| Page | Business question | How to explore |
|---|---|---|
| Order activity | How does order volume change across purchase weeks and customer states? | Filter by month, state or recorded status. Compare order count, item value, freight and payment value. Select a state bar to examine its weekly pattern. |
| Delivery reliability | Which states contribute the most missed promises, and how large is the assessed population? | The state table shows both late counts and on-time shares. A high-volume state can contribute many delayed deliveries even when its rate is comparatively good. |
| Order detail | Which individual orders explain an unusual result? | Select a purchase month, state, delivery outcome or order status. For a late order, compare its actual date, promised date and days past promise. The order ID can be matched directly to the warehouse export. |

For the three-month cohort, **4,762 of 4,949 assessed deliveries arrived by the promised day (96.22%)**. The remaining 313 purchases had no assessable outcome. March accounted for 116 of the 187 late deliveries. It also had the largest assessed population, so the counts need context: its late share was 4.56% (116/2,546), compared with January’s 2.93% (22/750). Purchase month groups historical orders by when they were placed; delivery may occur later. The archive contains the final observed records. It cannot establish what an operator knew at an earlier cutoff or why a delivery was late.

## Metric definitions

| Metric | Definition in the selected filter context |
|---|---|
| Orders placed | Count of rows in the order-level extract across all recorded statuses. |
| Item value, freight value, payment value | Each sums its own integer-cent field and divides by 100 to display Brazilian reais. Item values and freight come from item lines; payments come from payment records. Recorded payments can differ from item value plus freight. |
| Assessed orders | Status is delivered **and** both actual and promised delivery dates exist. |
| On-time share | Assessed orders delivered on or before the promised calendar date divided by all assessed orders. Empty denominators display a blank. |
| Mean delivery days | Average calendar days elapsed from purchase to delivery across assessed orders. |
| Days past promise | Actual delivery date minus promised date, in calendar days. A positive value indicates a missed promise, and this column is blank for unassessed orders. |

The date table covers the span between the first and last purchase dates in the loaded extract. Its active one-to-many relationship filters Orders on purchase date. The weekly chart uses Monday as the start of the week, so the first visible week of a monthly slice may start in the previous month. All monetary figures refer to the records in the file and include recorded lines from every selected status. They represent neither recognized revenue nor profit.

## Reproduce the numbers

Run these commands from the Power BI project folder using Python 3.11 or later. They do not require Power BI Desktop.

```sh
python tools/check_metrics.py --verify-demo
python tools/check_metrics.py --month 2017-03 --state SP
python tools/validate_project.py
```

The first command compares unfiltered card values with the supplied sample benchmark. The second independently computes the filtered metrics from the CSV. The third checks the report and model structure, source and bindings. Full schema validation is available with `python -m pip install "jsonschema>=4.18,<5"` and `python tools/validate_project.py --schemas`. The integration module supplies the order-level export, and the quality module reconciles it with independent monthly controls.

## Data source

The original Brazilian E-Commerce Public Dataset by Olist is licensed under CC BY-NC-SA 4.0. The supplied purchase cohort and aggregated monetary fields are derived from Olist's order, order-item, payment and customer tables. [Dataset on Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) · [Data licence](https://creativecommons.org/licenses/by-nc-sa/4.0/)
