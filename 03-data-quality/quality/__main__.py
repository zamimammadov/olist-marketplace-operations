"""Run the gate and publish an immutable evidence directory."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from .checks import COMPARISON_HEADER, FINDING_HEADER, RULES, run_checks


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_text(value):
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"


def run_gate(orders_path, control_path, output):
    orders = orders_path.read_bytes()
    controls = control_path.read_bytes()
    code = Path(__file__).parent
    provenance = {
        "orders_sha256": sha(orders), "source_controls_sha256": sha(controls),
        "checks_sha256": sha((code / "checks.py").read_bytes()),
        "gate_sha256": sha((code / "__main__.py").read_bytes()),
    }
    run_id = sha(json_text(provenance).encode())
    result = run_checks(orders, controls)
    errors = sum(count for key, count in result.counts.items() if RULES[key][0] == "error")
    warnings = len(result.findings) - errors
    decision = "HOLD" if errors else "PASS_WITH_WARNINGS" if warnings else "PASS"
    summary = {
        "decision": decision, "run_id": run_id, "inputs_and_rules": provenance,
        "action": {
            "HOLD": "Withhold the mart. Resolve all errors and validate the corrected delivery.",
            "PASS_WITH_WARNINGS": "Release with the documented caveats. Review warning findings and delivery coverage.",
            "PASS": "Release this delivery with the recorded evidence.",
        }[decision],
        "error_findings": errors, "warning_findings": warnings,
        "rule_counts": dict(result.counts), "profile": dict(result.profile),
    }
    summary["profile"]["on_time_rate"] = (
        result.profile["on_time_rows"] / result.profile["eligible_delivered_rows"]
        if result.profile["eligible_delivered_rows"] else None
    )
    runs = output / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    destination = runs / run_id
    with tempfile.TemporaryDirectory(dir=runs) as temp_dir:
        # Rename a completed sibling directory so readers only see complete runs.
        staging = Path(temp_dir) / "evidence"
        staging.mkdir()
        (staging / "summary.json").write_text(json_text(summary), encoding="utf-8")
        with (staging / "findings.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FINDING_HEADER, lineterminator="\n")
            writer.writeheader()
            writer.writerows(result.findings)
        with (staging / "monthly_controls.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow(COMPARISON_HEADER)
            writer.writerows(result.comparisons)
        try:
            staging.rename(destination)
        except OSError:
            # Concurrent identical runs converge. A partial directory is an error.
            required = ("summary.json", "findings.csv", "monthly_controls.csv")
            if not all((destination / name).is_file() for name in required):
                raise
    latest = {"run_id": run_id, "decision": decision, "evidence": f"runs/{run_id}"}
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=output, encoding="utf-8", delete=False) as f:
            name = f.name
            f.write(json_text(latest))
        os.replace(name, output / "latest.json")
    finally:
        if name is not None:
            Path(name).unlink(missing_ok=True)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/orders.csv"))
    parser.add_argument("--controls", type=Path, default=Path("data/source_controls.csv"))
    parser.add_argument("--out", type=Path, default=Path("build/quality"))
    args = parser.parse_args(argv)
    try:
        summary = run_gate(args.input, args.controls, args.out)
    except (OSError, OverflowError) as exc:
        print(f"Quality gate could not finish: {exc}", file=sys.stderr)
        return 2
    print(json_text({k: summary[k] for k in ("decision", "run_id", "error_findings", "warning_findings")}), end="")
    return 1 if summary["error_findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
