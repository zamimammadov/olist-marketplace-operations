"""Verify report plumbing, data bindings, and the published Olist cohort.

Run from anywhere with Python 3.11+. Add --schemas to validate PBIR against
Microsoft's versioned public schemas (requires network + the jsonschema package).
This is an asset check, not an execution of Power BI Desktop, Power Query or DAX.
"""

import argparse
import base64
import csv
import gzip
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MICROSOFT = "developer.microsoft.com"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def descendants(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from descendants(value)
    elif isinstance(node, list):
        for value in node:
            yield from descendants(value)


def schema_checks(documents):
    from jsonschema.validators import validator_for
    from referencing import Registry, Resource

    cache = {}

    def get_resource(uri):
        if uri not in cache:
            parsed = urlparse(uri)
            require(parsed.scheme == "https" and parsed.hostname == MICROSOFT,
                    f"unexpected schema host for {uri}")
            with urlopen(uri, timeout=30) as response:
                raw = json.load(response)
            cache[uri] = Resource.from_contents(raw)
        return cache[uri]

    registry = Registry(retrieve=get_resource)
    for path, content in documents.items():
        uri = content["$schema"]
        schema = get_resource(uri).contents
        validator = validator_for(schema)(schema, registry=registry)
        errors = list(validator.iter_errors(content))
        if errors:
            err = errors[0]
            raise ValueError(f"{path.name}: {err.json_path}: {err.message}")
    return len(cache)


def names_in_tmdl(text, kind):
    pattern = rf"^\t{kind} (?:'((?:''|[^'])+)'|([^\s=]+))"
    return {(quoted or plain).replace("''", "'")
            for quoted, plain in re.findall(pattern, text, re.MULTILINE)}


def check_model(model_dir, data):
    tables = {}
    for path in sorted((model_dir / "definition/tables").glob("*.tmdl")):
        text = path.read_text(encoding="utf-8")
        tables[path.stem] = {kind: names_in_tmdl(text, keyword)
                             for kind, keyword in (("Column", "column"), ("Measure", "measure"))}
    require({"Orders", "Calendar"}.issubset(tables), "orders or date table missing")
    required = {"Orders placed", "Assessed orders", "On-time orders", "On-time share",
                "Late orders", "Item value", "Payment value", "Freight value", "Mean delivery days"}
    require(required <= tables["Orders"]["Measure"], "missing KPI definitions")
    relationships = (model_dir / "definition/relationships.tmdl").read_text()
    require("fromColumn: Orders.'Purchase date'" in relationships and
            "toColumn: Calendar.Date" in relationships, "date dimension relationship missing")
    expr = (model_dir / "definition/expressions.tmdl").read_text()
    require('expression OrdersCsvPath = ""' in expr and "expression EmbeddedOrders" in expr,
            "missing configurable source path or embedded reference")
    body = expr.split("expression EmbeddedOrders", 1)[1]
    require("BinaryEncoding.Base64" in body and "Compression.GZip" in body,
            "embedded CSV representation missing or unsupported")
    chunks = re.findall(r'"([+/A-Za-z0-9=]{40,})"', body)
    # Chunks are joined before decoding, so the final fragment may be very short.
    literal_list = re.search(r'Text\.Combine\(\s*\{(.+?)\}\s*\)', body, re.S)
    if literal_list:
        chunks = re.findall(r'"([+/A-Za-z0-9=]*)"', literal_list[1])
    binary = base64.b64decode("".join(chunks), validate=True)
    require(gzip.decompress(binary) == data.read_bytes(), "embedded CSV differs from data/orders.csv")

    with data.open(encoding="utf-8-sig", newline="") as stream:
        csv_columns = next(csv.reader(stream))
    orders_model = (model_dir / "definition/tables/Orders.tmdl").read_text()
    declared_columns = re.findall(r'^\s+sourceColumn: (\w+)\s*$', orders_model, re.M)
    require(set(csv_columns) <= set(declared_columns), "CSV fields missing in the model")
    # Check DAX references in model measures as well as report bindings.
    known_columns = tables["Orders"]["Column"]
    known_measures = tables["Orders"]["Measure"]
    for expression in re.findall(r"^\tmeasure .+? = (.+)$", orders_model, re.M):
        for owner, field in re.findall(r"(\w*)\[([^\]]+)\]", expression):
            if owner:
                require(owner in tables and field in tables[owner]["Column"],
                        f"unresolved DAX column {owner}[{field}]")
            else:
                require(field in known_measures or field in known_columns,
                        f"unresolved DAX reference [{field}]")
    return tables


def verify_visual(visual, tables):
    projection_count = 0
    for item in descendants(visual):
        for kind in ("Column", "Measure"):
            token = item.get(kind)
            if not isinstance(token, dict) or "Expression" not in token:
                continue
            entity = token["Expression"].get("SourceRef", {}).get("Entity")
            name = token.get("Property")
            require(entity in tables and name in tables[entity][kind],
                    f"{visual['name']}: unresolved {kind} {entity}.{name}")
            projection_count += 1
    return projection_count


def check_report(report_dir, tables):
    definition = report_dir / "definition"
    metadata = read_json(definition / "pages/pages.json")
    paths = sorted((definition / "pages").glob("*/page.json"))
    pages = {p.parent.name: read_json(p) for p in paths}
    order = metadata["pageOrder"]
    require(len(order) == len(set(order)) and set(order) == set(pages),
            "page order has missing or duplicate pages")
    require(len(order) >= 2 and metadata["activePageName"] in pages,
            "incomplete report page list")
    names = set()
    display = []
    for page_id in order:
        page = pages[page_id]
        require(page["name"] == page_id, "page filename and ID differ")
        visuals = sorted((definition / "pages" / page_id / "visuals").glob("*/visual.json"))
        kinds = set()
        for visual_path in visuals:
            v = read_json(visual_path)
            vid = v["name"]
            require(vid == visual_path.parent.name and vid not in names,
                    f"incorrect or repeated visual ID {vid}")
            names.add(vid)
            position = v["position"]
            x, y, w, h = (position[k] for k in ("x", "y", "width", "height"))
            require(w > 0 and h > 0 and x >= 0 and y >= 0 and
                    x+w <= page["width"]+0.01 and y+h <= page["height"]+0.01,
                    f"visual {vid} extends beyond page")
            kind = v["visual"]["visualType"]
            kinds.add(kind)
            bound = verify_visual(v, tables)
            if kind not in {"shape", "textbox", "actionButton", "image", "pageNavigator"}:
                require(bound > 0, f"visual {vid} has no data binding")
        require("slicer" in kinds, f"{page_id} has no interactive filter")
        require(len(visuals) >= 3, f"{page_id} missing report visuals")
        display.append({"page": page["displayName"], "visuals": len(visuals)})
    return display


SOURCE_FIELDS = ["order_id", "purchase_date", "customer_state", "order_status", "delivered_date",
                 "estimated_date", "item_count", "payment_count", "item_value_cents",
                 "freight_cents", "payment_value_cents"]
COHORT_SQL = """
WITH scoped AS (
    SELECT *, (order_status='delivered' AND delivered_date<>'' AND estimated_date<>'') AS assessed
    FROM orders
    WHERE purchase_date >= ? AND purchase_date < ? AND (? IS NULL OR customer_state=?)
)
SELECT COUNT(*), COUNT(DISTINCT order_id), SUM(item_value_cents), SUM(payment_value_cents),
       SUM(freight_cents), COUNT(*) FILTER (WHERE assessed),
       COUNT(*) FILTER (WHERE assessed AND delivered_date <= estimated_date),
       AVG(julianday(delivered_date)-julianday(purchase_date)) FILTER (WHERE assessed)
FROM scoped
"""


def reference_metrics(path):
    """Independent SQL calculation against the exact distributed three-month slice."""
    with sqlite3.connect(":memory:") as conn, path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)
        require(next(reader) == SOURCE_FIELDS, "order export columns differ")
        conn.execute("CREATE TABLE orders (" + ",".join(SOURCE_FIELDS) + ")")
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)", reader)
        output = {}
        cases = [
            ("All purchases", "2017-01-01", "2017-04-01", None,
             (5262, 5262, 74196019, 88025965, 11355751, 4949, 4762, 13.0856738735098)),
            ("São Paulo, February", "2017-02-01", "2017-03-01", "SP",
             (654, 654, 7868708, 9252194, 1120064, 601, 583, 9.970049916805324)),
            ("Empty selection", "2018-01-01", "2018-02-01", "SP",
             (0, 0, None, None, None, 0, 0, None)),
        ]
        require(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 5262,
                "expected the published 5,262-order sample; a custom export needs its own baseline")
        for label, start, end, state, expected in cases:
            row = conn.execute(COHORT_SQL, (start, end, state, state)).fetchone()
            require(row[:-1] == expected[:-1], f"{label}: reference totals mismatch {row}")
            exact_duration = row[-1] == expected[-1]
            approximate_duration = row[-1] is not None and expected[-1] is not None and math.isclose(
                row[-1], expected[-1], abs_tol=1e-10)
            require(exact_duration or approximate_duration,
                    f"{label}: reference delivery duration mismatch")
            output[label] = dict(orders=row[0], item_value_cents=row[2], payment_value_cents=row[3],
                                 freight_cents=row[4], assessed=row[5], on_time=row[6],
                                 late=row[5]-row[6], on_time_share=row[6]/row[5] if row[5] else None,
                                 mean_delivery_days=row[-1])
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schemas", action="store_true", help="also fetch and check official PBIR schemas")
    args = parser.parse_args()
    project, = ROOT.glob("*.pbip")
    project_data = read_json(project)
    report_dir = ROOT / project_data["artifacts"][0]["report"]["path"]
    reference = report_dir / "definition.pbir"
    model_dir = (report_dir / read_json(reference)["datasetReference"]["byPath"]["path"]).resolve()
    docs = [project, reference, model_dir / "definition.pbism", *sorted((report_dir / "definition").rglob("*.json"))]
    documents = {p: read_json(p) for p in docs}
    data = ROOT / "data/orders.csv"
    model = check_model(model_dir, data)
    result = {"report_pages": check_report(report_dir, model), "PBIR_files": len(documents),
              "reference_metrics": reference_metrics(data)}
    if args.schemas:
        result["official_schemas_loaded"] = schema_checks(documents)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, ImportError) as exc:
        sys.exit(f"Verification failed: {exc}")
