"""Load three real purchase cohorts, check the release, and rehearse a held delivery."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
INTEGRATION = ROOT / "01-integration"
QUALITY = ROOT / "03-data-quality"
CONTROLS = QUALITY / "data" / "source_controls.csv"


def execute(folder, arguments, expected=0):
    result = subprocess.run(
        [sys.executable, *map(str, arguments)], cwd=folder, capture_output=True,
        encoding="utf-8", errors="replace", check=False,
    )
    if result.returncode != expected:
        raise RuntimeError(
            f"{folder.name} {arguments[1]} exited {result.returncode}, expected {expected}.\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def checksum(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def check_quality(input_file, evidence, expected):
    reply = execute(QUALITY, [
        "-m", "quality", "--input", input_file, "--controls", CONTROLS, "--out", evidence
    ], expected)
    return json.loads(reply)


def run(root):
    root.mkdir(parents=True, exist_ok=True)
    # Keep each run's files together, including failures, so two demos can run concurrently.
    run_folder = Path(tempfile.mkdtemp(prefix="run-", dir=root)).resolve()
    database, candidate = run_folder / "orders.sqlite", run_folder / "candidate.csv"
    steps = []
    for month in ("2017-01", "2017-02", "2017-03", "2017-03"):
        step = json.loads(execute(INTEGRATION, [
            "-m", "pipeline", "--db", database, "--out", candidate, "--through", month
        ]))
        step.update(through=month, snapshot_sha256=checksum(candidate))
        steps.append(step)
        print(f"{month}  +{len(step['loaded'])} batches  {step['orders_exported']:,} total orders")

    if steps[-1]["loaded"] or steps[-1]["snapshot_sha256"] != steps[-2]["snapshot_sha256"]:
        raise RuntimeError("Replaying March changed the delivered snapshot.")
    contracted = ROOT / "02-power-bi" / "data" / "orders.csv"
    if checksum(candidate) != checksum(contracted):
        raise RuntimeError("The shipped report and reconstructed baseline have different inputs.")

    passed = check_quality(candidate, run_folder / "accepted-evidence", 0)
    if passed["decision"] not in {"PASS", "PASS_WITH_WARNINGS"}:
        raise RuntimeError(f"Unexpected baseline result: {passed}")
    published = run_folder / "published"
    published.mkdir()
    staged = published / "orders.pending"
    shutil.copyfile(candidate, staged)
    os.replace(staged, published / "orders.csv")

    faulty = run_folder / "fault-injected.csv"
    execute(QUALITY, ["-m", "quality.inject", "--input", candidate, "--out", faulty])
    held = check_quality(faulty, run_folder / "held-evidence", 1)
    if held["decision"] != "HOLD" or checksum(published / "orders.csv") != checksum(candidate):
        raise RuntimeError("The controlled rejection did not preserve the accepted snapshot.")
    result = {"cohorts": steps, "baseline": passed, "fault_injected_copy": held,
              "published_file": "published/orders.csv", "published_sha256": checksum(candidate)}
    (run_folder / "demo.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{passed['decision']}  {passed['warning_findings']} baseline warnings")
    print(f"{held['decision']}  {held['error_findings']} findings blocked the damaged copy")
    print(f"Accepted CSV for Power BI: {published / 'orders.csv'}")
    return run_folder


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build" / "demo")
    args = parser.parse_args()
    try:
        run(args.out.resolve())
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"{exc}\n")
