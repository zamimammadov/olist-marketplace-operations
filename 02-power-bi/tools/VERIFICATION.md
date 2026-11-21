# Report verification

Open a terminal in `02-power-bi` and run:

```console
python tools/validate_project.py
```

The offline check resolves the project → report → semantic model links. It verifies page and visual IDs, page order, visual bounds, data bindings, DAX references, and the purchase-date relationship. It decodes the embedded demo and compares its bytes with the distributed CSV. An independent SQLite calculation then checks unfiltered, filtered, and empty selections against the packaged purchase cohort. The SQL reads the order export directly.

To check the report definitions against the versioned Microsoft Fabric JSON schemas declared by each file:

```console
python -m pip install "jsonschema>=4.18,<5"
python tools/validate_project.py --schemas
```

This optional check requires internet access to Microsoft's schema host. All 34 project, report and semantic-model JSON documents passed, including the 25 visual containers on three pages. The official Microsoft.AnalysisServices Tabular Object Model, NuGet version 19.114.8, also deserialized the model through `TmdlSerializer.DeserializeDatabaseFromFolder`. It loaded the model at compatibility level 1601, with two tables, 17 columns, ten measures, two import partitions, and a many-to-one purchase-date relationship. These are metadata and source-data checks. Power BI Desktop rendering, Power Query refresh, visual interactions and DAX engine execution require a Desktop session.

## Desktop acceptance check

Extract the full project first. Open `Olist Operations.pbip` in a current Power BI Desktop installation and refresh. Leave `OrdersCsvPath` blank for this exercise. On **Order activity**, compare the cards with the values below. On **Delivery reliability**, select the same month and state again, then check the delivery cards. Page-level filters are local to each page.

| Purchase cohort | Orders | Item value (BRL) | Recorded payment value (BRL) | Freight value (BRL) | Assessed | Late | On time |
|---|---:|---:|---:|---:|---:|---:|---:|
| All | 5,262 | 741,960.19 | 880,259.65 | 113,557.51 | 4,949 | 187 | 96.2% |
| February 2017, SP | 654 | 78,687.08 | 92,521.94 | 11,200.64 | 601 | 18 | 97.0% |

In the complete sample, 313 orders have no assessable outcome. The 4,762 on-time orders are divided by 4,949 assessed orders. A selection with no assessable deliveries has a blank on-time percentage. On **Order detail**, filter **Delivery outcome** to **Late** and check an individual delivery date against its promised date.

The bundled checks have intentional reference values for January–March 2017. Refreshing a report with a wider export and an explicit file parameter changes the visible totals. Reconcile that release against its own source controls and date range.
