# Olist marketplace operations

Three independently runnable projects follow an order from the original Olist records to a checked reporting dataset and an interactive Power BI report. The operational question is straightforward. When order volume rises, where are deliveries missing their promised dates, and can the figures be traced to the source?

The quick start ships with all **5,262 actual orders purchased in January–March 2017**. The full-archive acceptance run covered **99,441 orders in 25 nonempty purchase months**. Olist published the historical source on Kaggle. [Dataset and attribution](01-integration/DATA-SOURCE.md) · [Full run evidence](evidence/full-archive.json)

| Project | What to open | What it demonstrates |
|---|---|---|
| [Data integration](01-integration/README.md) | `01-integration` | Incremental ingestion of four source tables, batch receipts, transactional rollback, replay, late cohort backfill and an order-grain SQL model. An additional [milestone projection](01-integration/docs/lifecycle.md) advances existing orders using recorded event times. |
| [Data visualization](02-power-bi/README.md) | `02-power-bi/Olist Operations.pbip` in Power BI Desktop | Native order activity, delivery reliability and order detail pages. Ten DAX measures share a purchase-date calendar and consistent delivery eligibility rules. A [one-page operations briefing](02-power-bi/examples/fulfilment-review.pdf) can be read without Desktop. |
| [Data quality engineering](03-data-quality/README.md) | `03-data-quality` | A release decision backed by independent source totals, row-level defect evidence, severity rules and a reproducible failure and recovery drill. |

## Run the connected demo

Extract the complete archive into a folder and open a terminal there. Use Python 3.11 or newer. The connected demo uses the standard library and the real data packaged with it.

```console
python run_demo.py
```

On Windows, `py -3 run_demo.py` also works when the Python launcher points to 3.11 or newer. The output identifies a new run directory under `build/demo`. Each run retains its own warehouse, receipts, quality evidence, accepted CSV and deliberately damaged test copy. The accepted CSV is suitable for the Power BI model's optional `OrdersCsvPath` parameter. To inspect the included baseline in Desktop, keep the report and semantic-model folders together, open the PBIP and refresh with the path parameter blank. The report includes the same CSV content in its import model.

| Step | Purchase month loaded | Cumulative orders |
|---|---|---:|
| First delivery | January 2017 | 800 |
| Second delivery | February 2017 | 2,580 |
| Third delivery | March 2017 | 5,262 |
| Replay March on the same warehouse | Zero new batches | 5,262 |

The baseline passes with **132 nonblocking findings** and all **18 independent monthly control totals** matching. A damaged copy introduces a repeated order, an invalid state and an altered payment. The gate returns **HOLD** with **15 blocking findings**, including effects on aggregate controls. The accepted delivery stays available.

## Reading the numbers

Of the sample's 5,262 purchases, 4,949 have a delivered status, an actual delivery date and an estimated date. Of these, 4,762 were on time and 187 were late. A delivery on the promised calendar day counts as on time. The weighted on-time rate is 96.22%. In March, 116 out of 2,546 assessed orders arrived late. These figures describe a purchase cohort and the outcomes eventually recorded for it.

The business timestamp and the date a source system first made a record available answer different questions. The Kaggle archive supplies the former. Month-by-month loading is a reproducible historical replay. The event-time projection uses only recorded purchase, approval, carrier and customer-delivery milestones through its selected date. The Power BI model refreshes its reporting extract in Import mode. Incremental processing takes place in the integration project.

## Verification

```console
python -m unittest discover -s tests -v
```

Run the test command from `01-integration` or `03-data-quality`. In `02-power-bi`, run `python tools/validate_project.py`. The [report verification record](02-power-bi/tools/VERIFICATION.md) documents the metadata schema and tabular model checks, independent metric comparisons, and the Desktop refresh and interaction checks still to perform.

The integration README includes the command to rebuild all 25 cohorts from Kaggle and compare them with their source records. Each subproject includes its own source attribution, data and operating instructions.
