#!/usr/bin/python3
"""
profiling.py
------------
Run exactly one fio test (no sweep, no cross-product) with time-series
monitoring enabled, and produce a combined CSV overlaying fio's native
bandwidth/IOPS interval log against client-side CPU/load stats (sar) on a
shared elapsed-seconds axis.

This is a standalone tool, separate from main.py's sweep orchestrator.
It reuses FIOBenchmark, ClientMonitoring, PerfProfiling, and utils/shell.py
as-is, and does not modify main.py's sweep behavior.

Usage:
    python3 profiling.py --client-stats
    python3 profiling.py -c ./profiling.json --client-stats --perf-profile
"""

import argparse
import csv
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Union

from benchmarks.fio import FIOBenchmark
from monitoring import ClientMonitoring, PerfProfiling
from parse_results import parse_clientstats_timeseries
from timeseries import (
    parse_fio_interval_log,
    parse_perf_script_samples,
    combine_timeseries,
    COMBINED_COLUMNS,
)
from utils.shell import run_cmd


def read_arguments():
    parser = argparse.ArgumentParser(
        description="Run a single fio test with time-series client-stats profiling."
    )
    parser.add_argument("--profiling-config", "-c", default="./profiling.json",
                         help="Path to the profiling config JSON (default: ./profiling.json)")
    parser.add_argument("--output-dir", "-o", default=None,
                         help="Where to write the combined CSV (default: <log_path>/fio/<runid_base>)")
    parser.add_argument("--client-stats", action="store_true", default=True,
                         help="Collect client-side CPU/load/memory/network stats via sar (default: on)")
    parser.add_argument("--no-client-stats", dest="client_stats", action="store_false",
                         help="Disable client-stats collection")
    parser.add_argument("--client-stats-interval", type=int, default=5,
                         help="Sampling interval in seconds for client stats (default: 5)")
    parser.add_argument("--perf-profile", action="store_true", help="Also run perf-based profiling on clients")
    parser.add_argument("--perf-profile-frequency", type=int, default=25,
                         help="Sampling frequency in Hz for --perf-profile (default: 25)")
    parser.add_argument("--perf-profile-scope", choices=["system", "process", "both"], default="both",
                         help="Which flamegraph(s) to generate from --perf-profile (default: both)")
    parser.add_argument("--perf-profile-process", default=None,
                         help="Process name to isolate for the narrowed flamegraph (default: 'fio')")
    parser.add_argument("--dry-run", action="store_true",
                         help="Print all commands that would run without executing them")
    return parser.parse_args()


def load_config_file(path: Union[str, Path]) -> list:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("profiling config file must contain a JSON array of objects.")
    parsed: list = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"profiling config entry at index {i} must be an object.")
        parsed.append(item)
    return parsed


def setup_run_dirs(log_path: str) -> tuple:
    """Create <log_path>/fio/<runid_base>/ and return (runid_base, fname)."""
    now = datetime.now()
    runid = int(now.timestamp())
    file_stamp = now.strftime("%Y-%m-%d_%H:%M:%S")
    stamp = now.strftime("%Y%m%d_%H%M")
    runid_base = f"{runid}_{stamp}"
    fname = f"{runid}_{file_stamp}_profiling_fio.log"

    path = Path(f"{log_path}/fio/{runid_base}")
    path.mkdir(parents=True, exist_ok=True)
    return runid_base, fname


def fetch_interval_logs(machinefile: str, interval_log_prefix: str, dest_dir: Path, dry_run: bool) -> None:
    """
    Copy fio's native interval log files back from every client host (they
    are written locally on each client, not on the orchestrator host - see
    benchmarks/fio.py) into dest_dir, mirroring the clush --rcopy pattern
    already used by PerfProfiling.stop() in monitoring.py. Job number is
    deterministically "1" because FIOBenchmark always uses group_reporting
    with a single [fiojob] section.
    """
    bw_remote = f"{interval_log_prefix}_bw.1.log"
    iops_remote = f"{interval_log_prefix}_iops.1.log"

    run_cmd(f"clush --machinefile {machinefile} --rcopy {bw_remote} --dest {dest_dir}", dry_run=dry_run)
    run_cmd(f"clush --machinefile {machinefile} --rcopy {iops_remote} --dest {dest_dir}", dry_run=dry_run)
    run_cmd(f'clush --machinefile {machinefile} "rm -f {bw_remote} {iops_remote}"', dry_run=dry_run)


def collect_perf_timeseries(perf_dir: Path, fname: str, process_match: str) -> tuple:
    """
    Find the raw (unfiltered, non-.svg) `perf script` output files that
    PerfProfiling.stop() left in perf_dir - one per client host, named
    "<fname>.perf.script.<host>" - and parse each into a per-second sample
    histogram via parse_perf_script_samples, both unfiltered (every process
    on the box) and filtered to process_match (the benchmark itself).
    PerfProfiling also writes "<fname>.perf.script.<host>.<process_match>"
    (pre-filtered copy, used for its own flamegraph) and "*.svg" files
    alongside the raw ones in the same directory; both are skipped here
    since parse_perf_script_samples does its own filtering from the raw
    files instead. Returns (perf_rows, perf_process_rows), each a flat list
    of {"elapsed_s", "samples"} dicts across all hosts.
    """
    perf_rows, perf_process_rows = [], []
    for script_file in perf_dir.glob(f"{fname}.perf.script.*"):
        name = script_file.name
        if name.endswith(".svg") or name.endswith(f".{process_match}"):
            continue
        perf_rows.extend(parse_perf_script_samples(script_file))
        perf_process_rows.extend(parse_perf_script_samples(script_file, process_match=process_match))
    return perf_rows, perf_process_rows


def write_csv(rows: list, out_path: Path) -> None:
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COMBINED_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    args = read_arguments()

    config_data = load_config_file(args.profiling_config)
    globals_ = config_data[0]
    params = dict(globals_["tests"]["fio"])
    params.setdefault("interval_log_avg_msec", 1000)

    machinefile = globals_["machine_file"]
    log_path = globals_["log_path"]

    runid_base, fname = setup_run_dirs(log_path)
    dest_dir = Path(args.output_dir) if args.output_dir else Path(f"{log_path}/fio/{runid_base}")
    dest_dir.mkdir(parents=True, exist_ok=True)

    client_monitoring = None
    perf_profiling = None
    if args.client_stats:
        client_monitoring = ClientMonitoring(
            machinefile=machinefile, log_path=log_path, runid_base=runid_base,
            tool="fio", fname=fname, interval=args.client_stats_interval, dry_run=args.dry_run
        )
    if args.perf_profile:
        perf_profiling = PerfProfiling(
            machinefile=machinefile, log_path=log_path, runid_base=runid_base,
            tool="fio", fname=fname, frequency=args.perf_profile_frequency,
            scope=args.perf_profile_scope, process_match=args.perf_profile_process or "fio",
            dry_run=args.dry_run
        )

    fio = FIOBenchmark(
        params=params, fio_path=globals_["tools"]["fio"], data_path_root=globals_["data_path_root"],
        log_path=log_path, runid_base=runid_base, fname=fname, machinefile=machinefile,
        deletefiles=False, dry_run=args.dry_run
    )

    print(f"Starting single-test fio profiling run (runid_base={runid_base})...")

    start_wallclock = time.time()
    if client_monitoring:
        client_monitoring.start()
    if perf_profiling:
        perf_profiling.start()

    try:
        fio.run()
    finally:
        if client_monitoring:
            client_monitoring.stop()
        if perf_profiling:
            perf_profiling.stop()

    bw_rows, iops_rows, clientstats_rows = [], [], []

    if fio.interval_log_prefix:
        fetch_interval_logs(machinefile, fio.interval_log_prefix, dest_dir, args.dry_run)
        if not args.dry_run:
            prefix_name = Path(fio.interval_log_prefix).name
            for bw_file in dest_dir.glob(f"{prefix_name}_bw.1.log.*"):
                bw_rows.extend(parse_fio_interval_log(bw_file))
            for iops_file in dest_dir.glob(f"{prefix_name}_iops.1.log.*"):
                iops_rows.extend(parse_fio_interval_log(iops_file))

    if client_monitoring and not args.dry_run:
        clientstats_path = Path(f"{log_path}/fio/{runid_base}/{fname}.clientstats")
        if clientstats_path.exists():
            clientstats_rows = parse_clientstats_timeseries(clientstats_path, start_wallclock)

    perf_rows, perf_process_rows = [], []
    if perf_profiling and not args.dry_run:
        perf_dir = Path(f"{log_path}/fio/{runid_base}")
        perf_rows, perf_process_rows = collect_perf_timeseries(
            perf_dir, fname, perf_profiling.process_match
        )

    if args.dry_run:
        print("[DRY-RUN] Skipping CSV generation (no real log data was produced).")
        return

    combined = combine_timeseries(bw_rows, iops_rows, clientstats_rows, perf_rows, perf_process_rows)
    csv_path = dest_dir / f"{fname}.timeseries.csv"
    write_csv(combined, csv_path)
    print(f"Wrote {len(combined)} time-series rows to {csv_path}")


if __name__ == "__main__":
    main()
