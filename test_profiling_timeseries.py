#!/usr/bin/python3
"""
Standalone (no pytest) unit tests for timeseries.py and
parse_results.parse_clientstats_timeseries. Run with:

    python3 test_profiling_timeseries.py
"""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

from timeseries import parse_fio_interval_log, parse_perf_script_samples, combine_timeseries
from parse_results import parse_clientstats_timeseries

FAILURES = []


def check(name, cond):
    if cond:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}")
        FAILURES.append(name)


def test_parse_fio_interval_log():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bw.log"
        p.write_text("1000, 2048, 1, 1048576\n2000, 4096, 1, 1048576\n\nbad,line\n")
        rows = parse_fio_interval_log(p)
        check("parse_fio_interval_log row count", len(rows) == 2)
        check("parse_fio_interval_log elapsed_s", rows[0]["elapsed_s"] == 1.0)
        check("parse_fio_interval_log value", rows[1]["value"] == 4096.0)
        check("parse_fio_interval_log direction", rows[0]["direction"] == "write")
        check("parse_fio_interval_log bs", rows[0]["bs"] == 1048576)


def test_combine_timeseries():
    bw_rows = [{"elapsed_s": 1.0, "value": 100.0}, {"elapsed_s": 1.4, "value": 50.0}]
    iops_rows = [{"elapsed_s": 1.0, "value": 10.0}]
    clientstats_rows = [
        {"elapsed_s": 1.0, "host": "h1", "cpu_user": 10.0, "cpu_system": 2.0, "cpu_iowait": 1.0},
        {"elapsed_s": 1.2, "host": "h2", "cpu_user": 20.0, "cpu_system": 4.0, "cpu_iowait": 3.0},
        {"elapsed_s": 1.0, "host": "h1", "net_ifutil": 30.0},
        {"elapsed_s": 1.0, "host": "h2", "net_ifutil": 50.0},
    ]
    perf_rows = [{"elapsed_s": 1.0, "samples": 100}, {"elapsed_s": 1.3, "samples": 50}]
    perf_process_rows = [{"elapsed_s": 1.0, "samples": 40}]

    combined = combine_timeseries(bw_rows, iops_rows, clientstats_rows, perf_rows, perf_process_rows)
    check("combine_timeseries bucket count", len(combined) == 1)
    row = combined[0]
    check("combine_timeseries elapsed_s bucket", row["elapsed_s"] == 1)
    check("combine_timeseries bw_bytes_per_s", row["bw_bytes_per_s"] == round(150.0 * 1024, 2))
    check("combine_timeseries iops", row["iops"] == 10.0)
    check("combine_timeseries cpu_user_avg", row["cpu_user_avg"] == 15.0)
    check("combine_timeseries net_ifutil_max", row["net_ifutil_max"] == 50.0)
    check("combine_timeseries perf_samples_total", row["perf_samples_total"] == 150)
    check("combine_timeseries perf_samples_process", row["perf_samples_process"] == 40)

    empty = combine_timeseries([], [], [])
    check("combine_timeseries empty input", empty == [])
    check("combine_timeseries empty perf columns present", "perf_samples_total" not in str(empty))

    # When no perf rows are given at all, the perf columns on bucketed rows
    # that DO exist (from other sources) should come back blank, not error.
    no_perf = combine_timeseries(bw_rows, [], [])
    check("combine_timeseries no perf arg perf_samples_total blank", no_perf[0]["perf_samples_total"] == "")
    check("combine_timeseries no perf arg perf_samples_process blank", no_perf[0]["perf_samples_process"] == "")


def test_parse_perf_script_samples():
    content = (
        "fio 1001/1001 [000]  1000.000000: cycles:ppp:\n"
        "\tffffffff deadbeef some_func (kernel)\n"
        "\n"
        "fio 1001/1002 [001]  1000.400000: cycles:ppp:\n"
        "\tffffffff cafebabe other_func (kernel)\n"
        "\n"
        "sshd 2002/2002 [002]  1001.100000: cycles:ppp:\n"
        "\tffffffff f00dface sshd_func (sshd)\n"
        "\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "run.perf.script.host1"
        p.write_text(content)

        all_rows = parse_perf_script_samples(p)
        check("parse_perf_script_samples total bucket count", len(all_rows) == 2)
        check("parse_perf_script_samples first bucket elapsed_s", all_rows[0]["elapsed_s"] == 0)
        check("parse_perf_script_samples first bucket samples", all_rows[0]["samples"] == 2)
        check("parse_perf_script_samples second bucket elapsed_s", all_rows[1]["elapsed_s"] == 1)
        check("parse_perf_script_samples second bucket samples", all_rows[1]["samples"] == 1)

        fio_rows = parse_perf_script_samples(p, process_match="fio")
        check("parse_perf_script_samples fio-filtered bucket count", len(fio_rows) == 1)
        check("parse_perf_script_samples fio-filtered samples", fio_rows[0]["samples"] == 2)

        none_rows = parse_perf_script_samples(p, process_match="nonexistent")
        check("parse_perf_script_samples no-match returns empty", none_rows == [])

    with tempfile.TemporaryDirectory() as d:
        empty_p = Path(d) / "empty.perf.script.host1"
        empty_p.write_text("")
        check("parse_perf_script_samples empty file returns empty", parse_perf_script_samples(empty_p) == [])


def test_parse_clientstats_timeseries():
    start_dt = datetime(2026, 10, 23, 20, 0, 0)
    start_wallclock = start_dt.timestamp()
    content = (
        "sv300: 08:00:05 PM all 10.00 0.00 2.00 1.00 0.00 87.00\n"
        "sv300: 08:00:05 PM 0 1 0.50 0.25 0.75 0 \n"
        "sv300: 08:00:05 PM 1000 2000 500 50.00 100 200 300 10.00 10 20 30\n"
        "sv300: 08:00:05 PM IFACE rxpck/s txpck/s rxkB/s txkB/s rxcmp/s txcmp/s rxmcst/s %ifutil\n"
        "sv300: 08:00:05 PM eth0 1.00 1.00 1.00 1.00 0.00 0.00 0.00 75.00\n"
        "sv300: 08:00:05 PM lo 1.00 1.00 1.00 1.00 0.00 0.00 0.00 99.00\n"
        "garbage line without timestamp\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "run.clientstats"
        p.write_text(content)
        rows = parse_clientstats_timeseries(p, start_wallclock)

        cpu_rows = [r for r in rows if "cpu_user" in r]
        load_rows = [r for r in rows if "loadavg1" in r]
        mem_rows = [r for r in rows if "mem_used" in r]
        net_rows = [r for r in rows if "net_ifutil" in r]

        check("parse_clientstats_timeseries cpu row found", len(cpu_rows) == 1)
        check("parse_clientstats_timeseries cpu_user value", cpu_rows[0]["cpu_user"] == 10.0)
        check("parse_clientstats_timeseries elapsed_s", cpu_rows[0]["elapsed_s"] == 5.0)
        check("parse_clientstats_timeseries host", cpu_rows[0]["host"] == "sv300")

        check("parse_clientstats_timeseries loadavg1 row found", len(load_rows) == 1)
        check("parse_clientstats_timeseries loadavg1 value", load_rows[0]["loadavg1"] == 0.50)

        check("parse_clientstats_timeseries mem_used row found", len(mem_rows) == 1)
        check("parse_clientstats_timeseries mem_used value", mem_rows[0]["mem_used"] == 50.00)

        check("parse_clientstats_timeseries net_ifutil excludes lo/header", len(net_rows) == 1)
        check("parse_clientstats_timeseries net_ifutil value", net_rows[0]["net_ifutil"] == 75.0)


def test_parse_clientstats_timeseries_midnight_wrap():
    # Run starts just before midnight; a sample taken 10 minutes later
    # actually falls just after midnight (next calendar date), but sar
    # only reports time-of-day, so naively anchoring it to start's date
    # would make it look like it happened *before* start. The wrap-detection
    # should correct elapsed_s to +600s instead of the naive -85800s.
    start_dt = datetime(2026, 10, 23, 23, 55, 0)
    start_wallclock = start_dt.timestamp()
    content = "sv300: 12:05:00 AM all 5.00 0.00 1.00 0.50 0.00 93.00\n"
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "run.clientstats"
        p.write_text(content)
        rows = parse_clientstats_timeseries(p, start_wallclock)
        check("parse_clientstats_timeseries midnight wrap row found", len(rows) == 1)
        check("parse_clientstats_timeseries midnight wrap elapsed_s", rows[0]["elapsed_s"] == 600.0)


if __name__ == "__main__":
    test_parse_fio_interval_log()
    test_combine_timeseries()
    test_parse_perf_script_samples()
    test_parse_clientstats_timeseries()
    test_parse_clientstats_timeseries_midnight_wrap()

    if FAILURES:
        print(f"\n{len(FAILURES)} FAILURE(S): {FAILURES}")
        sys.exit(1)
    else:
        print("\nAll tests passed.")
        sys.exit(0)
