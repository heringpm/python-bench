"""
timeseries.py
-------------
Parsing and alignment helpers for profiling.py's single-test time-series
output: fio's native bw/iops interval logs overlaid against client-side
sar stats (via parse_results.parse_clientstats_timeseries), all converted
to a shared "elapsed seconds since test start" axis.

Not used by the regular config.json sweep (main.py) or parse_results.py's
generate_report() - this module is profiling.py-specific.
"""

import re
from pathlib import Path
from typing import Optional, Union

# fio's data_direction column: 0=read, 1=write, 2=trim, 3=read+write split line
_DIRECTION_NAMES = {"0": "read", "1": "write", "2": "trim", "3": "readwrite"}

# `perf script` header lines look like (format depends on perf version, but
# all contain a "<seconds>.<micros>:" timestamp token somewhere after the
# comm/pid field), e.g.:
#   fio 12345/12346 [003]  1234.567891: cycles:ppp:
# Indented lines below a header are call-graph stack frames, not samples.
_PERF_HEADER_TIME_RE = re.compile(r"(\d+\.\d+):")


def parse_fio_interval_log(path: Union[str, Path]) -> list:
    """
    Parse a fio native bw/iops interval log (CSV, no header), one of the
    files produced by write_bw_log=/write_iops_log= (see benchmarks/fio.py's
    interval_log_avg_msec support). Each line is:

        time_ms, value, data_direction, block_size[, ...]

    time_ms is milliseconds elapsed since fio started logging (i.e.
    relative to the job's own start, not wall-clock). Returns a list of
    dicts: {"elapsed_s": float, "value": float, "direction": str,
    "bs": int | None}. Lines that don't parse as at least
    time_ms,value,data_direction are skipped.
    """
    rows = []
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            time_ms = float(parts[0])
            value = float(parts[1])
        except ValueError:
            continue
        direction = _DIRECTION_NAMES.get(parts[2], parts[2])
        bs = None
        if len(parts) > 3 and parts[3]:
            try:
                bs = int(parts[3])
            except ValueError:
                bs = None
        rows.append({
            "elapsed_s": time_ms / 1000.0,
            "value": value,
            "direction": direction,
            "bs": bs,
        })
    return rows


def parse_perf_script_samples(path: Union[str, Path], process_match: Optional[str] = None) -> list:
    """
    Parse a `perf script` output file (as produced by PerfProfiling.stop()
    in monitoring.py) into a per-second histogram of sample counts, so perf
    activity can be placed on the same elapsed-seconds axis as fio's bw/iops
    and client-stats CPU rows.

    perf's own timestamps are relative to whatever clock `perf record` used
    (not wall-clock epoch time), so there's no exact way to line them up
    with fio's/sar's elapsed-since-test-start axis. Since PerfProfiling.start()
    is called immediately before FIOBenchmark.run() in profiling.py, the
    file's *first* sample timestamp is treated as elapsed_s=0 - this is only
    as accurate as that start-up gap (typically well under a second).

    If process_match is given, only samples whose comm (first whitespace-
    separated field of the header line) contains it (case-insensitive
    substring) are counted - use this to get a "benchmark process only"
    histogram alongside the unfiltered "every process on the box" one.
    Returns a list of dicts: {"elapsed_s": int, "samples": int}, one per
    second that had at least one sample, in ascending order.
    """
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    process_match_lower = process_match.lower() if process_match else None

    timestamps = []
    for line in lines:
        if not line or line[0] in (" ", "\t"):
            continue  # indented call-graph stack frame line, not a sample header
        m = _PERF_HEADER_TIME_RE.search(line)
        if not m:
            continue
        if process_match_lower is not None:
            comm = line.split()[0] if line.split() else ""
            if process_match_lower not in comm.lower():
                continue
        timestamps.append(float(m.group(1)))

    if not timestamps:
        return []

    t0 = timestamps[0]
    buckets: dict = {}
    for t in timestamps:
        b = round(t - t0)
        buckets[b] = buckets.get(b, 0) + 1
    return [{"elapsed_s": b, "samples": buckets[b]} for b in sorted(buckets)]


COMBINED_COLUMNS = [
    "elapsed_s",
    "bw_bytes_per_s",
    "bw_read_bytes_per_s",
    "bw_write_bytes_per_s",
    "iops",
    "iops_read",
    "iops_write",
    "cpu_user_avg",
    "cpu_system_avg",
    "cpu_iowait_avg",
    "loadavg1_avg",
    "mem_used_avg",
    "net_ifutil_max",
    "perf_samples_total",
    "perf_samples_process",
]

# Client-stats fields averaged across hosts per time bucket (vs.
# net_ifutil, which is maxed - a single saturated NIC matters even if
# other hosts/interfaces are idle).
_CLIENTSTATS_AVG_FIELDS = (
    "cpu_user", "cpu_system", "cpu_iowait", "loadavg1", "mem_used",
)


def combine_timeseries(
    bw_rows: list,
    iops_rows: list,
    clientstats_rows: list,
    perf_rows: Optional[list] = None,
    perf_process_rows: Optional[list] = None,
) -> list:
    """
    Outer-join fio's bw/iops interval rows, sar-derived clientstats rows,
    and (optionally) perf-script-derived sample-count rows onto a shared
    1-second-resolution elapsed_s axis (nearest-second rounding), producing
    one combined row per bucket that has data from at least one source.
    bw values (fio's native KiB/s) are summed across every row in a bucket
    (e.g. multiple jobs/threads) and converted to bytes/s; iops values are
    summed. For mixed rw/randrw jobs, fio's interval log contains both
    read (direction=0) and write (direction=1) rows in the same file -
    bw_read_bytes_per_s/bw_write_bytes_per_s and iops_read/iops_write
    break those out separately (in addition to the combined
    bw_bytes_per_s/iops totals), so both sides of a mixed workload are
    visible rather than lumped into one number. clientstats values are
    averaged across hosts (maxed for net_ifutil); perf sample counts
    (from parse_perf_script_samples, total and process-filtered) are
    summed across hosts. Returns a list of dicts with keys from
    COMBINED_COLUMNS, in ascending elapsed_s order.
    """
    buckets: dict = {}

    def bucket_for(elapsed_s: float) -> int:
        return round(elapsed_s)

    def direction_key(row: dict) -> str:
        # Mixed rw/randrw jobs log both read and write rows (data_direction
        # 0/1) into the same interval log file - split them here so both
        # sides of the test are visible, instead of silently lumping them
        # into a single number that (depending on the mix) looks like it's
        # only reflecting one direction. Anything that isn't "read"/"write"
        # (e.g. "trim", or a plain write-only/read-only job where direction
        # defaults to the job's own operation) still counts toward the
        # combined total via "_bw_vals"/"_iops_vals" below.
        return row.get("direction")

    for row in bw_rows:
        b = buckets.setdefault(bucket_for(row["elapsed_s"]), {})
        b.setdefault("_bw_vals", []).append(row["value"])
        d = direction_key(row)
        if d == "read":
            b.setdefault("_bw_read_vals", []).append(row["value"])
        elif d == "write":
            b.setdefault("_bw_write_vals", []).append(row["value"])

    for row in iops_rows:
        b = buckets.setdefault(bucket_for(row["elapsed_s"]), {})
        b.setdefault("_iops_vals", []).append(row["value"])
        d = direction_key(row)
        if d == "read":
            b.setdefault("_iops_read_vals", []).append(row["value"])
        elif d == "write":
            b.setdefault("_iops_write_vals", []).append(row["value"])

    for row in clientstats_rows:
        b = buckets.setdefault(bucket_for(row["elapsed_s"]), {})
        for field in _CLIENTSTATS_AVG_FIELDS:
            val = row.get(field)
            if val is not None:
                b.setdefault(f"_{field}_vals", []).append(val)
        net_val = row.get("net_ifutil")
        if net_val is not None:
            b.setdefault("_net_ifutil_vals", []).append(net_val)

    for row in (perf_rows or []):
        b = buckets.setdefault(bucket_for(row["elapsed_s"]), {})
        b.setdefault("_perf_total_vals", []).append(row["samples"])

    for row in (perf_process_rows or []):
        b = buckets.setdefault(bucket_for(row["elapsed_s"]), {})
        b.setdefault("_perf_process_vals", []).append(row["samples"])

    combined = []
    for elapsed_s in sorted(buckets):
        b = buckets[elapsed_s]
        bw_vals = b.get("_bw_vals", [])
        iops_vals = b.get("_iops_vals", [])

        bw_read_vals = b.get("_bw_read_vals", [])
        bw_write_vals = b.get("_bw_write_vals", [])
        iops_read_vals = b.get("_iops_read_vals", [])
        iops_write_vals = b.get("_iops_write_vals", [])

        row = {
            "elapsed_s": elapsed_s,
            # fio's bw log is in KiB/s by default -> convert to bytes/s
            "bw_bytes_per_s": round(sum(bw_vals) * 1024, 2) if bw_vals else "",
            "bw_read_bytes_per_s": round(sum(bw_read_vals) * 1024, 2) if bw_read_vals else "",
            "bw_write_bytes_per_s": round(sum(bw_write_vals) * 1024, 2) if bw_write_vals else "",
            "iops": round(sum(iops_vals), 2) if iops_vals else "",
            "iops_read": round(sum(iops_read_vals), 2) if iops_read_vals else "",
            "iops_write": round(sum(iops_write_vals), 2) if iops_write_vals else "",
        }

        for field in _CLIENTSTATS_AVG_FIELDS:
            vals = b.get(f"_{field}_vals", [])
            row[f"{field}_avg"] = round(sum(vals) / len(vals), 2) if vals else ""

        net_vals = b.get("_net_ifutil_vals", [])
        row["net_ifutil_max"] = max(net_vals) if net_vals else ""

        perf_total_vals = b.get("_perf_total_vals", [])
        row["perf_samples_total"] = sum(perf_total_vals) if perf_total_vals else ""

        perf_process_vals = b.get("_perf_process_vals", [])
        row["perf_samples_process"] = sum(perf_process_vals) if perf_process_vals else ""

        combined.append(row)

    return combined
