# Order milestones through a reporting date

The main warehouse answers questions about the final records in Olist's published archive. Operations may also want to know how many orders had reached customers **by a particular date**. This replay uses four timestamped milestones from the original orders file. An order can move from the carrier to the customer after its purchase month closes.

## Run the real example

From the integration project directory, start with an unused SQLite filename. Each cutoff includes the full calendar day in the wall-clock timestamps supplied by Olist. The files provide no timezone offset.

```sh
python tools/replay_lifecycle.py --batches data/batches --db build/lifecycle.sqlite --as-of 2017-01-31
python tools/replay_lifecycle.py --batches data/batches --db build/lifecycle.sqlite --as-of 2017-02-02
python tools/replay_lifecycle.py --batches data/batches --db build/lifecycle.sqlite --as-of 2017-02-02
```

| Through | Newly applied milestones | Orders updated | Visible orders | Delivered |
|---|---:|---:|---:|---:|
| 31 January 2017 | 2,455 | 800 | 800 | 283 |
| 2 February 2017 | 476 | 312 | 942 | 354 |
| 2 February again | 0 | 0 | 942 | 354 |

Order `76c6e866289321a7c93b82b54852dc33` offers a reproducible example. It was purchased on 23 January at 18:29:09 and approved on 25 January at 02:50:47. The carrier received it on 26 January at 14:16:31. After the January cutoff its projection says `handed_to_carrier` and its delivery timestamp is null. After the 2 February cutoff it says `delivered` and shows 14:08:10 on 2 February.

Run this query in the lifecycle database after each command to inspect that order.

```sql
SELECT order_id, last_milestone, purchased_at, approved_at, carrier_at, delivered_at
FROM lifecycle_current
WHERE order_id = '76c6e866289321a7c93b82b54852dc33';
```

## How the replay works

1. Each eligible purchase-month `orders.csv` enters `lifecycle_batches` under its SHA-256. Unchanged bytes skip parsing. Duplicate IDs across registered cohorts or changed bytes abort the transaction. The immutable archive contract avoids silently rewriting an already reported history.
2. Only nonempty recorded timestamps enter `lifecycle_events`. A missing approval can remain null even for a delivered order. The loader preserves source timestamps and requires the purchase to be visible before publishing any milestone for that order.
3. A partially indexed queue selects unapplied events before midnight following the cutoff. Events already in the archive can remain queued for a future date. The staging tables therefore contain future observations and are intended for audit. **Use `lifecycle_current` for as-of reporting.**
4. Only order IDs with newly due events are projected. `last_milestone` is the furthest recorded step in the purchase → approval → handover → delivery sequence at the cutoff. Missing intermediate steps are left null. Purchase takes precedence as the minimum visibility requirement if timestamps disagree.
5. Event application and checkpoint advancement commit in one SQLite transaction. Reruns apply zero events for unchanged input. If a previously absent purchase cohort arrives late, its unapplied older events enter the next run even with the same cutoff. That run restates the current projection with the newly supplied evidence.
6. The checkpoint moves forwards. To reproduce an earlier date or adopt a corrected archive, use a separate, fresh `--db` pathname and replay the required input revision. Keep the earlier DB and sources to preserve its original provenance.

## Interpretation

This is an **event-time reconstruction from a historical snapshot**. Olist provides the business timestamps but no source-system arrival times, original CDC messages, or cancellation timestamps. A visible row with `purchased` as its last recorded milestone gives no basis to infer a cancellation. The final source status and estimated delivery timestamp are excluded from this projection. The packaged January–March cohorts also exclude orders purchased before January, including those that may have been delivered during January.

```sh
python -m unittest discover -s tests -p test_lifecycle.py -v
```
