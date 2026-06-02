"""
parse_results.py
----------------
Scrapes IOR log files from a run directory and outputs a TSV report
that can be pasted directly into Excel.

Usage:
    python3 parse_results.py --tool ior --dir /work/results/ior/1748872000_20260602_1346

    Output is printed to stdout so you can redirect it:
    python3 parse_results.py --tool ior --dir /path/to/runid > results.tsv
"""

import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# IOR log parsing
# ---------------------------------------------------------------------------

# IOR summary lines look like:
# Max Write:  35330.00 MiB/sec  (35009.29 MiB/sec mean, 120.45 std dev, 34000.00 MiB/sec min)
# Max Read:   96803.36 MiB/sec  (95000.00 MiB/sec mean, 250.12 std dev, 94000.00 MiB/sec min)
# also IOPS lines:
# Max Write IOPS: 35330.00  (mean: 35009.29, std dev: 120.45, min: 34000.00)

IOR_METRIC_PATTERN = re.compile(
    r"(Max|Min)\s+(Write|Read)(?:\s+IOPS)?:\s+([\d.]+)"
)

IOR_MEAN_STDDEV_MIN_PATTERN = re.compile(
    r"([\d.]+)\s+\S+\s+mean,\s+([\d.]+)\s+std\s+dev,\s+([\d.]+)"
)

# Test name encodes all parameters, e.g.:
# ior.16-clients.64-ppn.10g-size.1m-xfersize.1-directio.1-fileperproc.0-random.posix-api.off-checksums.write
TEST_NAME_PARTS = [
    "tool",
    "clients",
    "ppn",
    "blocksize",
    "xfersize",
    "directio",
    "fileperproc",
    "random",
    "api",
    "checksums",
    "operation",
]


def parse_test_name(filename: str) -> dict:
    """Break a log filename into its component parameters."""
    # strip .log extension and split on .
    stem = Path(filename).stem  # e.g. ior.16-clients.64-ppn...write
    parts = stem.split(".")

    params = {}
    for i, part in enumerate(parts):
        if i < len(TEST_NAME_PARTS):
            # strip the label suffix (e.g. "16-clients" -> "16")
            value = part.split("-")[0]
            params[TEST_NAME_PARTS[i]] = value
        else:
            params[f"extra_{i}"] = part

    return params


def parse_ior_log(log_path: Path) -> dict:
    """
    Parse an IOR log file and extract all performance metrics.
    Returns a flat dict of metric_name -> value.
    """
    metrics = {}

    with open(log_path, "r") as f:
        for line in f:
            # match Max/Min Write/Read (bandwidth)
            m = IOR_METRIC_PATTERN.search(line)
            if m:
                qualifier = m.group(1)   # Max / Min
                operation = m.group(2)   # Write / Read
                value     = m.group(3)   # numeric value
                is_iops   = "IOPS" in line
                unit      = "IOPS" if is_iops else "MiB/sec"

                key_prefix = f"{qualifier}_{operation}_{'IOPS' if is_iops else 'BW'}"
                metrics[key_prefix] = value

                # try to pull mean, stddev, min from same line
                m2 = IOR_MEAN_STDDEV_MIN_PATTERN.search(line)
                if m2:
                    metrics[f"Mean_{operation}_{'IOPS' if is_iops else 'BW'}"] = m2.group(1)
                    metrics[f"StdDev_{operation}_{'IOPS' if is_iops else 'BW'}"] = m2.group(2)
                    metrics[f"Min_{operation}_{'IOPS' if is_iops else 'BW'}"] = m2.group(3)

    return metrics


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

# Column order for the TSV output
PARAM_COLUMNS = [
    "clients",
    "ppn",
    "blocksize",
    "xfersize",
    "directio",
    "fileperproc",
    "random",
    "api",
    "checksums",
    "operation",
]

METRIC_COLUMNS = [
    "Max_Write_BW",
    "Mean_Write_BW",
    "StdDev_Write_BW",
    "Min_Write_BW",
    "Max_Write_IOPS",
    "Mean_Write_IOPS",
    "StdDev_Write_IOPS",
    "Min_Write_IOPS",
    "Max_Read_BW",
    "Mean_Read_BW",
    "StdDev_Read_BW",
    "Min_Read_BW",
    "Max_Read_IOPS",
    "Mean_Read_IOPS",
    "StdDev_Read_IOPS",
    "Min_Read_IOPS",
]


def generate_ior_report(run_dir: Path) -> None:
    log_files = sorted(run_dir.glob("*.ior.*.log")) or sorted(run_dir.glob("*.log"))

    if not log_files:
        print(f"No log files found in {run_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(log_files)} log files in {run_dir}\n", file=sys.stderr)

    # build header
    all_columns = ["run_id"] + PARAM_COLUMNS + METRIC_COLUMNS
    print("\t".join(all_columns))

    run_id = run_dir.name

    for log_file in log_files:
        params  = parse_test_name(log_file.name)
        metrics = parse_ior_log(log_file)

        row = [run_id]
        for col in PARAM_COLUMNS:
            row.append(params.get(col, ""))
        for col in METRIC_COLUMNS:
            row.append(metrics.get(col, ""))

        print("\t".join(row))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Parse benchmark results and output TSV for Excel."
    )
    parser.add_argument(
        "--tool",
        required=True,
        choices=["ior", "mdtest"],
        help="Which tool's results to parse."
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

    if args.tool == "ior":
        generate_ior_report(args.dir)
    elif args.tool == "mdtest":
        print("mdtest parsing not yet implemented.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()