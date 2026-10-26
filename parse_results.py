"""
parse_results.py
----------------
Scrapes IOR/mdtest/fio log files from a run directory and outputs a CSV report
that can be imported directly into Excel.

Usage:
    python3 parse_results.py --tool ior    --config /path/to/config.json --dir /work/results/ior/<runid>
    python3 parse_results.py --tool mdtest --config /path/to/config.json --dir /work/results/mdtest/<runid>
    python3 parse_results.py --tool fio    --config /path/to/config.json --dir /work/results/fio/<runid>

    Redirect to file:
    python3 parse_results.py --tool fio --config /path/to/config.json --dir /path/to/runid > results.csv
"""

import argparse
import csv
import json
import re
import sys
from datetime import datetime
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
#
# IOR filename format:
# <runid>_<datestamp>_ior.16-clients.8-ppn.default-pool.1m-stripesize.8-stripecount.
#   10g-size.1m-xfersize.1-directio.1-fileperproc.0-random.posix-api.off-checksums.write_ior.log
#
# mdtest filename format:
# <runid>_<datestamp>_mdtest.16-clients.96-ppn.default-pool.1m-stripesize.1-stripecount.
#   0-DOM.5000-objects.1-branching.0-depth.0-byteswrite.0-uniquedir.0-itemsperdir.
#   1-directio.off-checksums_mdtest.log
#
# fio filename format:
# <runid>_<datestamp>_fio.16-clients.4-ppn.default-pool.16m-stripesize.1-stripecount.
#   8k-blocksize.10g-filesize.8-iodepth.1-directio.off-checksums.write-operation_fio.log
# ---------------------------------------------------------------------------

IOR_FILENAME_PARAM_COLUMNS = [
    "tool",
    "clients",
    "ppn",
    "pools",
    "stripesize",
    "stripecount",
    "blocksize",
    "xfersize",
    "directio",
    "fileperproc",
    "randomoffset",
    "api",
    "checksums",
    "operation",
]

MDTEST_FILENAME_PARAM_COLUMNS = [
    "tool",
    "clients",
    "ppn",
    "pools",
    "stripesize",
    "stripecount",
    "DOM",
    "objects",
    "branching",
    "depth",
    "byteswrite",
    "uniquedir",
    "itemsperdir",
    "directio",
    "checksums",
]

FIO_FILENAME_PARAM_COLUMNS = [
    "tool",
    "clients",
    "ppn",
    "pools",
    "stripesize",
    "stripecount",
    "blocksize",
    "filesize",
    "iodepth",
    "directio",
    "checksums",
    "operation",
]

TOOL_FILENAME_COLUMNS = {
    "ior":    IOR_FILENAME_PARAM_COLUMNS,
    "mdtest": MDTEST_FILENAME_PARAM_COLUMNS,
    "fio":    FIO_FILENAME_PARAM_COLUMNS,
}


def parse_test_name(filename: str, tool: str) -> dict:
    """Break a log filename into its component parameters."""
    stem = Path(filename).stem              # strip .log
    stem = stem[stem.index(f"{tool}."):]    # strip runid_datestamp_ prefix
    stem = stem.replace(f"_{tool}", "")     # strip trailing _<tool> suffix

    parts = stem.split(".")
    columns = TOOL_FILENAME_COLUMNS[tool]

    params = {}
    for i, key in enumerate(columns):
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

IOR_METRIC_COLUMNS = list(IOR_SUMMARY_COLUMNS.values())


def parse_ior_log(log_path: Path) -> dict:
    """
    Parse an IOR log file and extract metrics from the summary table.

    Returns a dict keyed by operation ("write"/"read") mapping to that
    operation's metrics dict. A standard (non-mixed) run will only have a
    single key present. A "mixed" run (-w -r in the same invocation)
    produces one row per operation in the summary table, so both "write"
    and "read" keys will be present.
    """
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    metrics_by_op = {}

    for i, line in enumerate(lines):
        if line.strip().startswith("Operation"):
            header = line.split()
            for j in range(i + 1, len(lines)):
                data_line = lines[j].strip()
                if not data_line or data_line.startswith("Finished"):
                    break
                data = data_line.split()
                row = dict(zip(header, data))
                operation = row.get("Operation", "").lower()
                metrics = {}
                for src_col, out_col in IOR_SUMMARY_COLUMNS.items():
                    metrics[out_col] = row.get(src_col, "")
                metrics_by_op[operation] = metrics
            break

    return metrics_by_op


# ---------------------------------------------------------------------------
# mdtest log parsing — targets the "SUMMARY rate:" table
# ---------------------------------------------------------------------------

MDTEST_SUMMARY_ROWS = [
    "Directory creation",
    "Directory stat",
    "Directory rename",
    "Directory removal",
    "File creation",
    "File stat",
    "File read",
    "File removal",
    "Tree creation",
    "Tree removal",
]

MDTEST_METRIC_COLUMNS = [
    f"{row.replace(' ', '_')}_{stat}"
    for row in MDTEST_SUMMARY_ROWS
    for stat in ("Max", "Min", "Mean", "StdDev")
]


def parse_mdtest_log(log_path: Path) -> dict:
    """Parse an mdtest log file and extract metrics from the SUMMARY rate table."""
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    metrics = {}

    in_summary = False
    for line in lines:
        stripped = line.strip()

        if stripped.startswith("SUMMARY rate"):
            in_summary = True
            continue

        if not in_summary:
            continue

        if stripped.startswith("Operation") or stripped.startswith("---") or not stripped:
            continue

        for row_name in sorted(MDTEST_SUMMARY_ROWS, key=len, reverse=True):
            if stripped.startswith(row_name):
                rest = stripped[len(row_name):].split()
                if len(rest) >= 4:
                    base = row_name.replace(" ", "_")
                    metrics[f"{base}_Max"]    = rest[0]
                    metrics[f"{base}_Min"]    = rest[1]
                    metrics[f"{base}_Mean"]   = rest[2]
                    metrics[f"{base}_StdDev"] = rest[3]
                break

    return metrics


# ---------------------------------------------------------------------------
# fio log parsing — targets the "All clients:" summary block
#
# Key lines:
#   read/write: IOPS=353k, BW=2761Mi (2896M)(162GiB/60022msec)
#   bw (  MiB/s): min=  509, max= 4856, per=100.00%, avg=2762.46, stdev=12.19
#   iops        : min=65188, max=621622, avg=353594.93, stdev=1560.79
#   lat (usec): min=94, max=531206, avg=1446.19, stdev=4084.72
# ---------------------------------------------------------------------------

FIO_METRIC_COLUMNS = [
    "operation",
    "error",
    "BW_summary(MiB/s)",
    "BW_summary_read(MiB/s)",
    "BW_summary_write(MiB/s)",
    "IOPS_summary",
    "IOPS_summary_read",
    "IOPS_summary_write",
    "BW_min(MiB/s)",
    "BW_min_read(MiB/s)",
    "BW_min_write(MiB/s)",
    "BW_max(MiB/s)",
    "BW_max_read(MiB/s)",
    "BW_max_write(MiB/s)",
    "BW_avg(MiB/s)",
    "BW_avg_read(MiB/s)",
    "BW_avg_write(MiB/s)",
    "BW_stdev",
    "BW_stdev_read",
    "BW_stdev_write",
    "IOPS_min",
    "IOPS_min_read",
    "IOPS_min_write",
    "IOPS_max",
    "IOPS_max_read",
    "IOPS_max_write",
    "IOPS_avg",
    "IOPS_avg_read",
    "IOPS_avg_write",
    "IOPS_stdev",
    "IOPS_stdev_read",
    "IOPS_stdev_write",
    "lat_min(usec)",
    "lat_min_read(usec)",
    "lat_min_write(usec)",
    "lat_max(usec)",
    "lat_max_read(usec)",
    "lat_max_write(usec)",
    "lat_avg(usec)",
    "lat_avg_read(usec)",
    "lat_avg_write(usec)",
    "lat_stdev",
    "lat_stdev_read",
    "lat_stdev_write",
    "clat_p50(usec)",
    "clat_p50_read(usec)",
    "clat_p50_write(usec)",
    "clat_p90(usec)",
    "clat_p90_read(usec)",
    "clat_p90_write(usec)",
    "clat_p95(usec)",
    "clat_p95_read(usec)",
    "clat_p95_write(usec)",
    "clat_p99(usec)",
    "clat_p99_read(usec)",
    "clat_p99_write(usec)",
    "clat_p99.9(usec)",
    "clat_p99.9_read(usec)",
    "clat_p99.9_write(usec)",
    "clat_p99.99(usec)",
    "clat_p99.99_read(usec)",
    "clat_p99.99_write(usec)",
]

# Percentile labels fio prints in the "clat percentiles (<unit>):" block,
# mapped to the output column suffix used above.
FIO_CLAT_PERCENTILES = {
    "50.00": "p50",
    "90.00": "p90",
    "95.00": "p95",
    "99.00": "p99",
    "99.90": "p99.9",
    "99.99": "p99.99",
}


def detect_fio_error(lines: list) -> str:
    """
    Scan a fio log for indicators that the job errored out before running
    cleanly to completion (e.g. a missing fscrypt key, an io_u error such
    as misaligned/invalid I/O, or the job process exiting nonzero). When
    this happens fio often skips printing some/all of its aggregate
    bw/iops/lat stat lines, leaving those CSV columns blank - this makes
    that distinguishable from a genuinely clean run at a glance.

    Returns a short description of the first error found, or "" if the
    log looks clean.
    """
    # Prefer the most specific message, e.g.:
    #   "...err=126/file:filesetup.c:174, func=open, error=Required key not available"
    #   "fiojob: (groupid=0, jobs=16): err=22 (file:io_u.c:1889, func=io_u error, error=Invalid argument): pid=..."
    for line in lines:
        m = re.search(r"\berror=([^,:)]+)", line)
        if m:
            return m.group(1).strip()

    # Fall back to a nonzero process exit code, e.g.:
    #   "client <c214>: exited with error 16"
    for line in lines:
        m = re.search(r"exited with error (\d+)", line)
        if m and m.group(1) != "0":
            return f"exited with error {m.group(1)}"

    return ""


def to_mib(value_str: str) -> str:
    """
    Convert a fio bandwidth string to a plain MiB/s float string.
    Handles suffixes: Ki, Mi, Gi (binary) and K, M, G (decimal).
    Examples: 2761Mi -> 2761.00, 12.2Gi -> 12492.80, 1437M -> 1370.50
    """
    value_str = value_str.strip()
    m = re.match(r"([\d.]+)([KkMmGgTt]i?)?", value_str)
    if not m:
        return value_str

    val  = float(m.group(1))
    unit = (m.group(2) or "").lower()

    if unit == "gi":        # GiB -> MiB
        val *= 1024
    elif unit == "g":       # GB  -> MiB (1 GB = 1000/1024 MiB)
        val = val * 1000 / 1.048576 / 1000 * 1024
    elif unit == "ki":      # KiB -> MiB
        val /= 1024
    elif unit == "k":       # KB  -> MiB
        val = val * 1000 / 1048576
    elif unit in ("mi", "m"):
        pass                # already MiB/MB scale, leave as-is
    elif unit == "ti":      # TiB -> MiB
        val *= 1024 * 1024
    elif unit == "t":       # TB  -> MiB
        val = val * 1e12 / 1048576

    return f"{val:.2f}"


def to_plain_number(value_str: str) -> str:
    """
    Convert a fio human-readable rate string (e.g. "353k", "12.7k", "1001k",
    "3m") to a plain decimal number string, so CSV columns like IOPS_summary
    sort/graph correctly instead of being treated as text. fio formats these
    counts with decimal (not binary) suffixes: k=1e3, m=1e6, g=1e9.
    """
    value_str = value_str.strip()
    m = re.match(r"([\d.]+)([KkMmGgTt])?$", value_str)
    if not m:
        return value_str

    val  = float(m.group(1))
    unit = (m.group(2) or "").lower()

    multipliers = {"k": 1e3, "m": 1e6, "g": 1e9, "t": 1e12}
    val = round(val * multipliers.get(unit, 1), 2)

    return f"{val:.0f}" if val == int(val) else f"{val:.2f}"


def to_usec(value: str, unit: str) -> str:
    """Convert a plain numeric latency value in the given fio time unit
    (nsec/usec/msec) to a plain usec number string."""
    try:
        val = float(value)
    except ValueError:
        return value

    unit = unit.lower()
    if unit == "nsec":
        val /= 1000
    elif unit == "msec":
        val *= 1000
    # usec: no scaling needed

    return f"{val:.2f}" if val != int(val) else f"{val:.0f}"


def _parse_kv_line(line: str) -> dict:
    """Parse a line of key=value pairs separated by commas into a dict."""
    result = {}
    for part in line.split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def parse_fio_log(log_path: Path) -> dict:
    """
    Parse a fio --client aggregate log file and extract metrics from the
    'All clients:' summary block. All BW values normalized to MiB/s.
    """
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    metrics = {}

    in_block = False
    in_percentile_block = False
    percentile_unit = "usec"
    # Mixed rw/randrw jobs print a separate read: ... / write: ... block,
    # each with its own bw/iops/lat/clat lines - track which block we're
    # currently in so both sides can be captured (see *_read/*_write
    # columns below), instead of the second block silently overwriting
    # the first's values in the plain (unsuffixed) columns.
    current_direction = None

    for line in lines:
        stripped = line.strip()

        # entry point: the aggregate summary block.
        # fio --client (distributed) runs report an "All clients:" header
        # before the aggregate read/write/bw/iops/lat lines. Single-host
        # runs have no such header - the per-group summary line
        # ("fiojob: (groupid=0, jobs=16): err= 0: pid=...") is immediately
        # followed by those same aggregate lines, so treat it as an
        # equivalent entry point. (Not to be confused with the per-job
        # startup line, which is prefixed with the hostname, e.g.
        # "<c214.maas> fiojob: (g=0): rw=read, ...".)
        if stripped.startswith("All clients:") or (
            stripped.startswith("fiojob:") and "groupid=" in stripped
        ):
            in_block = True
            continue

        if not in_block:
            continue

        # read/write summary line: "  read: IOPS=353k, BW=2761Mi ..."
        m = re.match(r"(read|write|randread|randwrite|randrw):\s+IOPS=(\S+),\s+BW=(\S+)", stripped)
        if m:
            label = m.group(1)
            if "read" in label:
                current_direction = "read"
            elif "write" in label:
                current_direction = "write"
            else:
                current_direction = None
            metrics["operation"]         = label
            metrics["IOPS_summary"]      = to_plain_number(m.group(2))
            metrics["BW_summary(MiB/s)"] = to_mib(m.group(3))
            if current_direction:
                metrics[f"IOPS_summary_{current_direction}"]      = to_plain_number(m.group(2))
                metrics[f"BW_summary_{current_direction}(MiB/s)"] = to_mib(m.group(3))
            continue

        # aggregate bw line: "bw (  MiB/s): min=  509, max= 4856, per=100.00%, avg=2762.46, stdev=12.19"
        # note: the unit in the header tells us the scale fio is reporting in
        if stripped.startswith("bw"):
            # extract the unit from the header e.g. "bw (  MiB/s):" or "bw (  GiB/s):"
            unit_m = re.search(r"\(([^)]+)\)", stripped)
            bw_unit = unit_m.group(1).strip() if unit_m else "MiB/s"
            after_colon = stripped.split(":", 1)[1] if ":" in stripped else stripped
            kv = _parse_kv_line(after_colon)

            def scale_bw(v: str) -> str:
                # fio reports the bw line already in the unit shown in the header
                # append the unit suffix so to_mib can parse it correctly
                suffix_map = {
                    "MiB/s": "Mi", "GiB/s": "Gi", "KiB/s": "Ki",
                    "MB/s":  "M",  "GB/s":  "G",  "KB/s":  "K",
                }
                suffix = suffix_map.get(bw_unit, "Mi")
                return to_mib(v.strip() + suffix)

            metrics["BW_min(MiB/s)"] = scale_bw(kv.get("min", ""))
            metrics["BW_max(MiB/s)"] = scale_bw(kv.get("max", ""))
            metrics["BW_avg(MiB/s)"] = scale_bw(kv.get("avg", ""))
            metrics["BW_stdev"]      = scale_bw(kv.get("stdev", ""))
            if current_direction:
                metrics[f"BW_min_{current_direction}(MiB/s)"] = scale_bw(kv.get("min", ""))
                metrics[f"BW_max_{current_direction}(MiB/s)"] = scale_bw(kv.get("max", ""))
                metrics[f"BW_avg_{current_direction}(MiB/s)"] = scale_bw(kv.get("avg", ""))
                metrics[f"BW_stdev_{current_direction}"]      = scale_bw(kv.get("stdev", ""))
            continue

        # aggregate iops line: "iops        : min=65188, max=621622, avg=353594.93, stdev=1560.79"
        if stripped.startswith("iops"):
            after_colon = stripped.split(":", 1)[1] if ":" in stripped else stripped
            kv = _parse_kv_line(after_colon)
            metrics["IOPS_min"]   = kv.get("min", "")
            metrics["IOPS_max"]   = kv.get("max", "")
            metrics["IOPS_avg"]   = kv.get("avg", "")
            metrics["IOPS_stdev"] = kv.get("stdev", "")
            if current_direction:
                metrics[f"IOPS_min_{current_direction}"]   = kv.get("min", "")
                metrics[f"IOPS_max_{current_direction}"]   = kv.get("max", "")
                metrics[f"IOPS_avg_{current_direction}"]   = kv.get("avg", "")
                metrics[f"IOPS_stdev_{current_direction}"] = kv.get("stdev", "")
            continue

        # aggregate lat line: "lat (usec): min=94, max=531206, avg=1446.19, stdev=4084.72"
        # note: fio also prints a "lat (msec)   : 2=0.02%, 4=99.97%, ..."
        # percentile-bucket distribution line later in the same block, which
        # also starts with "lat (" but has no min/max/avg/stdev keys - only
        # overwrite the metrics when this is actually the min/max/avg/stdev
        # line, so that distribution line doesn't blank out the real values.
        if stripped.startswith("lat ("):
            after_colon = stripped.split(":", 1)[1] if ":" in stripped else stripped
            kv = _parse_kv_line(after_colon)
            if "min" in kv:
                metrics["lat_min(usec)"] = kv.get("min", "")
                metrics["lat_max(usec)"] = kv.get("max", "")
                metrics["lat_avg(usec)"] = kv.get("avg", "")
                metrics["lat_stdev"]     = kv.get("stdev", "")
                if current_direction:
                    metrics[f"lat_min_{current_direction}(usec)"] = kv.get("min", "")
                    metrics[f"lat_max_{current_direction}(usec)"] = kv.get("max", "")
                    metrics[f"lat_avg_{current_direction}(usec)"] = kv.get("avg", "")
                    metrics[f"lat_stdev_{current_direction}"]     = kv.get("stdev", "")
            continue

        # clat percentile block, e.g.:
        #   clat percentiles (msec):
        #    |  1.00th=[   21],  5.00th=[   26], 10.00th=[   29], 20.00th=[   34],
        #    | 30.00th=[   39], ... 99.00th=[  103], 99.50th=[  112], ...
        #    | 99.99th=[  165]
        # The block is introduced by a "clat percentiles (<unit>):" header and
        # continues over following lines that start with "|", each containing
        # one or more "<pct>th=[ <val>]" entries.
        pct_header = re.match(r"clat percentiles \(([^)]+)\):", stripped)
        if pct_header:
            in_percentile_block = True
            percentile_unit = pct_header.group(1).strip()
            continue

        if in_percentile_block:
            if stripped.startswith("|"):
                for pct_str, val_str in re.findall(r"([\d.]+)th=\[\s*([\d.]+)\]", stripped):
                    col_suffix = FIO_CLAT_PERCENTILES.get(pct_str)
                    if col_suffix:
                        metrics[f"clat_{col_suffix}(usec)"] = to_usec(val_str, percentile_unit)
                        if current_direction:
                            metrics[f"clat_{col_suffix}_{current_direction}(usec)"] = to_usec(val_str, percentile_unit)
                continue
            else:
                in_percentile_block = False

        # end of block on cpu line
        if stripped.startswith("cpu"):
            break

    metrics["error"] = detect_fio_error(lines)

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


def get_sort_key(row: dict, config_params: dict, sort_first: list) -> tuple:
    """Build a sort tuple — sort_first cols first, then config param order."""
    sort_cols = [c for c in sort_first if c in row] + \
        [k for k in config_params if k not in sort_first]
    return tuple(numeric_sort_val(str(row.get(col, ""))) for col in sort_cols)


# ---------------------------------------------------------------------------
# Client-side stats log parsing — targets *.clientstats files produced by
# `clush --machinefile <mf> "sar -u -P ALL -q -r -n DEV <interval>"`
# (see monitoring.py ClientMonitoring). Output is a continuous time series
# across every client host, prefixed per-line with "<hostname>: ". This is
# condensed down into a handful of avg/max summary columns per test so it
# can sit alongside the normal benchmark metrics in the same CSV row.
# ---------------------------------------------------------------------------

CLIENT_STATS_METRIC_COLUMNS = [
    "client_cpu_user_avg(%)",
    "client_cpu_user_max(%)",
    "client_cpu_system_avg(%)",
    "client_cpu_system_max(%)",
    "client_cpu_iowait_avg(%)",
    "client_cpu_iowait_max(%)",
    "client_loadavg_1min_max",
    "client_mem_used_avg(%)",
    "client_mem_used_max(%)",
    "client_net_ifutil_max(%)",
]

# Host-prefix added by clush, e.g. "c214: 06:00:31 PM     all      0.38 ..."
_HOST_PREFIX_RE = re.compile(r"^\S+:\s?(.*)$")

# sar -u -P ALL: "06:00:31 PM     all      0.38      0.00      0.06      0.06      0.00     99.50"
_CPU_ALL_RE = re.compile(
    r"^\S+\s+[AP]M\s+all\s+([\d.]+)\s+[\d.]+\s+([\d.]+)\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s*$"
)
# sar -q: "06:00:38 PM         2       742      0.23      0.15      0.06         0"
_LOADAVG_RE = re.compile(
    r"^\S+\s+[AP]M\s+\d+\s+\d+\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s+\d+\s*$"
)
# sar -r: "06:00:38 PM 364075892 388469328   2839564      0.72    759768  21493484   3708540      0.92   3930608  19678140         4"
_MEM_RE = re.compile(
    r"^\S+\s+[AP]M\s+\d+\s+\d+\s+\d+\s+([\d.]+)\s+\d+\s+\d+\s+\d+\s+[\d.]+\s+\d+\s+\d+\s+\d+\s*$"
)
# sar -n DEV: "06:00:38 PM   eno1np0    154.00   1356.00     10.98   1978.08      0.00      0.00      0.00      1.62"
_NET_RE = re.compile(
    r"^\S+\s+[AP]M\s+(\S+)\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)\s*$"
)


def get_clientstats_path(log_file: Path, tool: str) -> "Path | None":
    """
    Map a benchmark log file (e.g. "<runid>_<ts>_<test>_fio.log") to its
    matching client-stats sidecar file, if one was captured for that test
    (e.g. "<runid>_<ts>_<test>_log.out.clientstats" - see main.py, where
    both share the same "<test>" parameter string).
    """
    suffix = f"_{tool}.log"
    name = log_file.name
    base = name[: -len(suffix)] if name.endswith(suffix) else log_file.stem
    candidate = log_file.parent / f"{base}_log.out.clientstats"
    return candidate if candidate.exists() else None


def parse_clientstats_log(log_path: Path) -> dict:
    """
    Parse a *.clientstats file into avg/max summary metrics across every
    sample and every client host in the file. Returns {} if nothing
    recognizable was found (e.g. clush/ssh failed to reach any host).
    """
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    cpu_user, cpu_system, cpu_iowait = [], [], []
    loadavg1, mem_used, net_ifutil = [], [], []

    for raw_line in lines:
        m = _HOST_PREFIX_RE.match(raw_line)
        line = m.group(1) if m else raw_line
        stripped = line.strip()
        if not stripped:
            continue

        m = _CPU_ALL_RE.match(stripped)
        if m:
            cpu_user.append(float(m.group(1)))
            cpu_system.append(float(m.group(2)))
            cpu_iowait.append(float(m.group(3)))
            continue

        m = _LOADAVG_RE.match(stripped)
        if m:
            loadavg1.append(float(m.group(1)))
            continue

        m = _MEM_RE.match(stripped)
        if m:
            mem_used.append(float(m.group(1)))
            continue

        m = _NET_RE.match(stripped)
        if m and m.group(1).lower() not in ("iface", "lo"):
            net_ifutil.append(float(m.group(2)))
            continue

    if not any((cpu_user, loadavg1, mem_used, net_ifutil)):
        return {}

    def avg(vals):
        return round(sum(vals) / len(vals), 2) if vals else ""

    def mx(vals):
        return max(vals) if vals else ""

    return {
        "client_cpu_user_avg(%)":    avg(cpu_user),
        "client_cpu_user_max(%)":    mx(cpu_user),
        "client_cpu_system_avg(%)":  avg(cpu_system),
        "client_cpu_system_max(%)":  mx(cpu_system),
        "client_cpu_iowait_avg(%)":  avg(cpu_iowait),
        "client_cpu_iowait_max(%)":  mx(cpu_iowait),
        "client_loadavg_1min_max":   mx(loadavg1),
        "client_mem_used_avg(%)":    avg(mem_used),
        "client_mem_used_max(%)":    mx(mem_used),
        "client_net_ifutil_max(%)":  mx(net_ifutil),
    }


# ---------------------------------------------------------------------------
# Client-side stats time-series parsing — used by profiling.py (not the
# regular sweep report above) to build a per-sample, per-host time series
# instead of collapsing each .clientstats file down to avg/max summary
# columns. Reuses the same host-prefix/line-shape knowledge as
# parse_clientstats_log, but also captures each sample's own HH:MM:SS AM/PM
# timestamp so it can be aligned to fio's interval log on a shared
# elapsed-seconds-since-test-start axis (see timeseries.combine_timeseries).
# ---------------------------------------------------------------------------

_HOST_PREFIX_WITH_NAME_RE = re.compile(r"^(\S+):\s?(.*)$")
_TIMESTAMP_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+([AP]M)\s+(.*)$")

# Same column shapes as _CPU_ALL_RE/_LOADAVG_RE/_MEM_RE/_NET_RE above, minus
# the leading "<time> <AM/PM>" (already stripped off by _TIMESTAMP_RE).
_CPU_ALL_REST_RE = re.compile(
    r"^all\s+([\d.]+)\s+[\d.]+\s+([\d.]+)\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s*$"
)
_LOADAVG_REST_RE = re.compile(r"^\d+\s+\d+\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s+\d+\s*$")
_MEM_REST_RE = re.compile(
    r"^\d+\s+\d+\s+\d+\s+([\d.]+)\s+\d+\s+\d+\s+\d+\s+[\d.]+\s+\d+\s+\d+\s+\d+\s*$"
)
_NET_REST_RE = re.compile(
    r"^(\S+)\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)\s*$"
)


def parse_clientstats_timeseries(log_path: Path, start_wallclock: float) -> list:
    """
    Parse a *.clientstats file (same format as parse_clientstats_log) into
    one row per recognized sample per host, instead of a single avg/max
    summary. Each row is a dict with "elapsed_s" (seconds since
    start_wallclock), "host", and exactly one of "cpu_user"/"cpu_system"/
    "cpu_iowait" (together, from a `sar -u -P ALL` line), "loadavg1",
    "mem_used", or "net_ifutil" set, depending on which sar line it came
    from.

    sar's timestamps have no date, only HH:MM:SS AM/PM, so each sample is
    assumed to fall on start_wallclock's calendar date; a sample whose
    time-of-day is more than 12 hours earlier than start_wallclock's is
    assumed to have wrapped past midnight and is pushed a day forward.
    Samples that don't parse as a recognized sar line are skipped.
    """
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    start_dt = datetime.fromtimestamp(start_wallclock)
    start_date_str = start_dt.strftime("%Y-%m-%d")

    rows = []
    for raw_line in lines:
        m = _HOST_PREFIX_WITH_NAME_RE.match(raw_line)
        host, line = (m.group(1), m.group(2)) if m else ("unknown", raw_line)
        stripped = line.strip()
        if not stripped:
            continue

        tm = _TIMESTAMP_RE.match(stripped)
        if not tm:
            continue
        time_str, ampm, rest = tm.groups()

        try:
            sample_dt = datetime.strptime(
                f"{start_date_str} {time_str} {ampm}", "%Y-%m-%d %I:%M:%S %p"
            )
        except ValueError:
            continue

        elapsed_s = (sample_dt - start_dt).total_seconds()
        if elapsed_s < -43200:  # more than 12h "before" start -> wrapped past midnight
            elapsed_s += 86400

        m2 = _CPU_ALL_REST_RE.match(rest)
        if m2:
            rows.append({
                "elapsed_s": elapsed_s, "host": host,
                "cpu_user": float(m2.group(1)),
                "cpu_system": float(m2.group(2)),
                "cpu_iowait": float(m2.group(3)),
            })
            continue

        m2 = _LOADAVG_REST_RE.match(rest)
        if m2:
            rows.append({"elapsed_s": elapsed_s, "host": host, "loadavg1": float(m2.group(1))})
            continue

        m2 = _MEM_REST_RE.match(rest)
        if m2:
            rows.append({"elapsed_s": elapsed_s, "host": host, "mem_used": float(m2.group(1))})
            continue

        m2 = _NET_REST_RE.match(rest)
        if m2 and m2.group(1).lower() not in ("iface", "lo"):
            rows.append({"elapsed_s": elapsed_s, "host": host, "net_ifutil": float(m2.group(2))})
            continue

    return rows


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

TOOL_CONFIG = {
    "ior": {
        "filename_columns": IOR_FILENAME_PARAM_COLUMNS,
        "metric_columns":   IOR_METRIC_COLUMNS,
        "sort_first":       ["operation"],
        "log_parser":       parse_ior_log,
        "skip_extra":       ("keep_files", "extra_args", "pools", "stripecount", "stripesize"),
    },
    "mdtest": {
        "filename_columns": MDTEST_FILENAME_PARAM_COLUMNS,
        "metric_columns":   MDTEST_METRIC_COLUMNS,
        "sort_first":       [],
        "log_parser":       parse_mdtest_log,
        "skip_extra":       ("keep_files", "extra_args", "pools", "stripecount", "stripesize"),
    },
    "fio": {
        "filename_columns": FIO_FILENAME_PARAM_COLUMNS,
        "metric_columns":   FIO_METRIC_COLUMNS,
        "sort_first":       ["operation"],
        "log_parser":       parse_fio_log,
        "skip_extra":       ("keep_files", "extra_args", "pools", "stripecount", "stripesize", "operation"),
    },
}


def sanitize_csv_value(val):
    """
    Guard against Excel/Sheets interpreting a cell as a formula.

    Any value whose text begins with =, +, -, or @ is treated as a formula
    by Excel on open/paste (e.g. a config flag like "-v" becomes a bogus
    formula and renders as "#NAME?"). Genuine negative numbers are left
    untouched since they're safe and meaningful; everything else gets a
    leading apostrophe, which Excel recognizes as a "force text" marker
    and strips from the displayed value.
    """
    s = str(val)
    if not s or s[0] not in "=+-@":
        return val
    try:
        float(s)
        return val
    except ValueError:
        return "'" + s


def generate_report(run_dir: Path, config: dict, tool: str) -> None:
    log_files = sorted(run_dir.glob("*.log"))

    if not log_files:
        print(f"No .log files found in {run_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(log_files)} log files\n", file=sys.stderr)

    tc = TOOL_CONFIG[tool]
    config_params    = config.get("tests", {}).get(tool, {})
    filename_columns = tc["filename_columns"]
    metric_columns   = tc["metric_columns"]
    sort_first       = tc["sort_first"]
    log_parser       = tc["log_parser"]
    skip_extra       = tc["skip_extra"]

    filename_params = [c for c in filename_columns if c != "tool"]
    log_captured    = {c.lower() for c in filename_columns}

    extra_config_params = [
        k for k in config_params
        if k.lower() not in log_captured
        and k not in skip_extra
    ]

    all_columns = ["run_id"] + filename_params + extra_config_params + metric_columns + CLIENT_STATS_METRIC_COLUMNS

    rows = []
    run_id = run_dir.name

    for log_file in log_files:
        params = parse_test_name(log_file.name, tool)

        clientstats_path = get_clientstats_path(log_file, tool)
        clientstats = parse_clientstats_log(clientstats_path) if clientstats_path else {}

        if tool == "ior":
            # IOR returns a dict keyed by operation ("write"/"read"). A
            # "mixed" run (-w -r in one invocation) has both keys present,
            # so it is expanded into one output row per operation, just
            # like a standard write-only or read-only run.
            metrics_by_op = parse_ior_log(log_file)

            if not metrics_by_op:
                print(f"  WARNING: no summary data found in {log_file.name}", file=sys.stderr)
                continue

            for operation, metrics in metrics_by_op.items():
                row = {"run_id": run_id}

                for col in filename_params:
                    row[col] = params.get(col, "")

                for col in extra_config_params:
                    val = config_params.get(col, [""])[0]
                    row[col] = val

                for col in metric_columns:
                    row[col] = metrics.get(col, "")

                for col in CLIENT_STATS_METRIC_COLUMNS:
                    row[col] = clientstats.get(col, "")

                row["operation"] = operation

                rows.append(row)
            continue

        metrics = log_parser(log_file)

        if not metrics:
            print(f"  WARNING: no summary data found in {log_file.name}", file=sys.stderr)
            continue

        row = {"run_id": run_id}

        for col in filename_params:
            row[col] = params.get(col, "")

        for col in extra_config_params:
            val = config_params.get(col, [""])[0]
            row[col] = val

        for col in metric_columns:
            row[col] = metrics.get(col, "")

        for col in CLIENT_STATS_METRIC_COLUMNS:
            row[col] = clientstats.get(col, "")

        rows.append(row)

    rows.sort(key=lambda r: get_sort_key(r, config_params, sort_first))

    writer = csv.writer(sys.stdout)
    writer.writerow(all_columns)
    for row in rows:
        writer.writerow([sanitize_csv_value(row.get(col, "")) for col in all_columns])


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
        choices=["ior", "mdtest", "fio"],
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
    generate_report(args.dir, config, args.tool)


if __name__ == "__main__":
    main()