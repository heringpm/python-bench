"""
parse_results.py
----------------
Scrapes IOR log files from a run directory and outputs a CSV report
that can be imported directly into Excel.

Usage:
    python3 parse_results.py --tool ior --config /path/to/config.json --dir /work/results/ior/1748872000_20260602_1346

    Redirect to file:
    python3 parse_results.py --tool ior --config /path/to/config.json --dir /path/to/runid > results.csv
"""

import argparse
import csv
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_config(config_path: Path) -> dict:
    """Load the config JSON and return the test params dict."""
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw = raw[0]
    return raw


# ---------------------------------------------------------------------------
# Test name parsing
# Filename format:
# ior.16-clients.64-ppn.10g-blocksize.1m-xfersize.1-directio.1-fileperproc.0-random.posix-api.off-checksums.write.log
# ---------------------------------------------------------------------------

FILENAME_PARAM_COLUMNS = [
    "tool",
    "clients",
    "ppn",
    "pools",
    "stripe_count",
    "blocksize",
    "xfersize",
    "directio",
    "fileperproc",
    "randomoffset",
    "api",
    "checksums",
    "operation",
]

def parse_test_name(filename: str) -> dict:
    """Break a log filename into its component parameters."""
    stem = Path(filename).stem          # strip .log
    stem = stem[stem.index("ior."):]    # strip runid_datestamp_ prefix
    stem = stem.replace("_ior", "")     # strip trailing _ior from operation
    parts = stem.split(".")

    params = {}
    for i, key in enumerate(FILENAME_PARAM_COLUMNS):
        if i < len(parts):
            params[key] = parts[i].split("-")[0]
        else:
            params[key] = ""
    return params


# ---------------------------------------------------------------------------
# IOR log parsing — targets the "Summary of all tests:" table
# ---------------------------------------------------------------------------

IOR_SUMMARY_COLUMNS = {
    "Max(MiB)":  "Max_BW(MiB/s)",
    "Min(MiB)":  "Min_BW(MiB/s)",
    "Mean(MiB)": "Mean_BW(MiB/s)",
    "StdDev":    "StdDev_BW",
    "Max(OPs)":  "Max_IOPS",
    "Min(OPs)":  "Min_IOPS",
    "Mean(OPs)": "Mean_IOPS",
    "Mean(s)":   "Mean_Latency(s)",
}

METRIC_COLUMNS = list(IOR_SUMMARY_COLUMNS.values())


def parse_ior_log(log_path: Path) -> dict:
    """
    Parse an IOR log file and extract metrics from the summary table.
    Returns a flat dict of output_column_name -> value.
    """
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    metrics = {}

    for i, line in enumerate(lines):
        if line.strip().startswith("Operation"):
            header = line.split()
            for j in range(i + 1, len(lines)):
                data_line = lines[j].strip()
                if data_line and not data_line.startswith("Finished"):
                    data = data_line.split()
                    row = dict(zip(header, data))
                    for src_col, out_col in IOR_SUMMARY_COLUMNS.items():
                        metrics[out_col] = row.get(src_col, "")
                    break
            break

    return metrics


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def parse_size_val(val: str):
    """Convert size strings like 1m, 16m, 10g to a numeric value for sorting."""
    units = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    val = val.lower().strip()
    if val and val[-1] in units:
        try:
            return float(val[:-1]) * units[val[-1]]
        except ValueError:
            pass
    try:
        return float(val)
    except ValueError:
        return val


def numeric_sort_val(val: str):
    """Return a sortable value — size-aware numeric if possible, lowercase string otherwise."""
    parsed = parse_size_val(val)
    if isinstance(parsed, (int, float)):
        return (0, parsed)
    return (1, val.lower())


def get_sort_key(row: dict, config_params: dict) -> tuple:
    """
    Build a sort tuple from the row.
    operation is always first so reads/writes group together.
    Remaining params follow config key order so incrementing values step naturally.
    """
    sort_cols = ["operation"] + [k for k in config_params if k != "operation"]

    key = []
    for col in sort_cols:
        val = row.get(col, "")
        key.append(numeric_sort_val(str(val)))
    return tuple(key)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_ior_report(run_dir: Path, config: dict) -> None:
    log_files = sorted(run_dir.glob("*.log"))

    if not log_files:
        print(f"No .log files found in {run_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(log_files)} log files\n", file=sys.stderr)

    config_params = config.get("tests", {}).get("ior", {})

    filename_params = [c for c in FILENAME_PARAM_COLUMNS if c != "tool"]

    log_captured = {c.lower() for c in FILENAME_PARAM_COLUMNS}
    extra_config_params = [
        k for k in config_params
        if k not in log_captured
        and k not in ("keep_files", "extra_args", "pools", "stripe_count", "stripecount", "tasks", "tpn", "blocksize(b)", "xfersize(b)")
    ]

    all_columns = (
        ["run_id"]
        + filename_params
        + extra_config_params
        + METRIC_COLUMNS
    )

    rows = []
    run_id = run_dir.name

    for log_file in log_files:
        params  = parse_test_name(log_file.name)
        metrics = parse_ior_log(log_file)

        if not metrics:
            print(f"  WARNING: no summary data found in {log_file.name}", file=sys.stderr)
            continue

        row = {"run_id": run_id}

        for col in filename_params:
            row[col] = params.get(col, "")

        for col in extra_config_params:
            val = config_params.get(col, [""])[0]
            row[col] = val

        for col in METRIC_COLUMNS:
            row[col] = metrics.get(col, "")

        rows.append(row)

    # sort: operation first, then remaining config params in config order, all ascending
    rows.sort(key=lambda r: get_sort_key(r, config_params))

    writer = csv.writer(sys.stdout)
    writer.writerow(all_columns)
    for row in rows:
        writer.writerow([row.get(col, "") for col in all_columns])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse benchmark results and output CSV for Excel."
    )
    parser.add_argument(
        "--tool",
        required=True,
        choices=["ior", "mdtest"],
        help="Which tool's results to parse."
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to the config JSON file."
    )
    parser.add_argument(
        "--dir",
        required=True,
        type=Path,
        help="Path to the run ID directory containing log files."
    )
    args = parser.parse_args()

    if not args.dir.exists():
        print(f"Directory not found: {args.dir}", file=sys.stderr)
        sys.exit(1)

    if not args.config.exists():
        print(f"Config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)

    config = load_config(args.config)

    if args.tool == "ior":
        generate_ior_report(args.dir, config)
    elif args.tool == "mdtest":
        print("mdtest parsing not yet implemented.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()