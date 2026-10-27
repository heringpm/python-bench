#!/usr/bin/python3
"""
Standalone (no pytest) unit tests for benchprof/benchprof/parsers/*.py and
merge.py. Run with:

    python3 test_benchprof_parsers.py
"""

import gzip
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "benchprof"))

from benchprof.parsers.elbencho import parse_elbencho
from benchprof.parsers.sar import parse_sar
from benchprof.parsers.pidstat import parse_pidstat
from benchprof.parsers.links import parse_links
from benchprof.parsers.lctl import parse_lctl
from benchprof.merge import merge_run
from benchprof.flame import extract_window
import json

FAILURES = []


def check(name, cond):
    if cond:
        print(f"PASS: {name}")
    else:
        print(f"FAIL: {name}")
        FAILURES.append(name)


def test_parse_elbencho_rank_mapping():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "live.csv"
        p.write_text(
            "ISO Date,Label,Phase,RuntimeMS,Rank,MixType,Done%,DoneBytes,MiB/s,IOPS,Entries,Entries/s,Lat Ent us,Lat IO us\n"
            "2023-11-14 22:13:20,,main,1000,0,,50,1048576,100.0,25600,10,10,0,40\n"
            "2023-11-14 22:13:20,,main,1000,1,,50,1048576,120.0,30720,10,10,0,35\n"
        )
        rows = parse_elbencho(str(p), ["client01", "client02"], t0=1700000000, phases=[])
        tput = {r.node: r.value for r in rows if r.metric == "Throughput"}
        check("elbencho rank->host mapping", tput == {"client01": 100.0, "client02": 120.0})
        check("elbencho elapsed_sec is 0 at t0", all(abs(r.elapsed_sec) < 1e-6 for r in rows))


def test_parse_elbencho_tz_offset_and_total_rank():
    # Real --livecsvex output: ISO date carries a "+0000" UTC offset, and
    # each interval includes a "Total" row (the only one with real
    # MiB/s/IOPS/latency numbers) plus one row per rank that leaves those
    # columns blank (only DoneBytes is populated per-rank).
    import calendar
    t0 = calendar.timegm((2026, 10, 27, 18, 10, 49))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "live.csv"
        p.write_text(
            "ISO Date,Label,Phase,RuntimeMS,Rank,MixType,Done%,DoneBytes,MiB/s,IOPS,Entries,Entries/s,Lat Ent us,Lat IO us\n"
            "2026-10-27T18:10:49.371+0000,,WRITE,1006,Total,,2,21884829696,200,200,0,0,0,38931\n"
            "2026-10-27T18:10:49.371+0000,,WRITE,1006,0,,2,1319108608,,,0,,,\n"
            "2026-10-27T18:10:49.371+0000,,WRITE,1006,1,,2,1319108608,,,0,,,\n"
        )
        rows = parse_elbencho(str(p), ["client01", "client02"], t0=t0, phases=[])
        check("elbencho tz-offset rows parsed (not dropped)", len(rows) > 0)
        tput = {r.node: r.value for r in rows if r.metric == "Throughput"}
        check("elbencho tz-offset elapsed_sec is ~0 at t0", all(abs(r.elapsed_sec) < 1.0 for r in rows))
        check("elbencho Total row is the only Throughput source", tput == {"controller": 200.0})


def test_parse_sar_cpu_mem_net():
    with tempfile.TemporaryDirectory() as d:
        sar_p = Path(d) / "sar.csv"
        sar_p.write_text(
            "# hostname;interval;timestamp;CPU;%usr;%nice;%system;%iowait;%steal;%irq;%soft;%guest;%gnice;%idle\n"
            "h1;1;2023-11-14 22:13:20;-1;10.00;0.00;5.00;2.00;0.00;0.00;0.00;0.00;0.00;83.00\n"
            "# hostname;interval;timestamp;kbmemfree;kbavail;kbmemused;%memused;kbbuffers;kbcached;kbcommit;%commit;kbactive;kbinact;kbdirty\n"
            "h1;1;2023-11-14 22:13:20;1000000;900000;500000;33.00;1000;2000;100000;5.00;300000;100000;10\n"
        )
        net_p = Path(d) / "sar_net.csv"
        net_p.write_text(
            "# hostname;interval;timestamp;IFACE;rxpck/s;txpck/s;rxkB/s;txkB/s;rxcmp/s;txcmp/s;rxmcst/s;%ifutil\n"
            "h1;1;2023-11-14 22:13:20;eth0;100.00;90.00;1000.00;900.00;0.00;0.00;0.00;5.00\n"
        )
        rows = parse_sar(str(sar_p), str(net_p), "h1", t0=1700000000)
        cpu = {r.entity: r.value for r in rows if r.metric == "CPU"}
        mem = {r.entity: r.value for r in rows if r.metric == "Memory"}
        net = {r.entity: r.value for r in rows if r.metric == "NetworkIface"}
        check("sar CPU %usr -> user entity", cpu.get("user") == 10.0)
        check("sar CPU idle entity", cpu.get("idle") == 83.0)
        check("sar Memory used%", mem.get("used") == 33.0)
        check("sar NetworkIface eth0/rx", net.get("eth0/rx") == 1000.0)


def test_parse_sar_combined_row_and_sys_field():
    # Real `sadf -d -h -- -u ALL -r -B -q` output: CPU, memory, paging,
    # and load all come back in a single combined header/row per
    # timestamp (not one header block per activity), and the CPU field is
    # named "%sys" (not "%system") on this sysstat build (12.5.4/EL9).
    with tempfile.TemporaryDirectory() as d:
        sar_p = Path(d) / "sar.csv"
        sar_p.write_text(
            "# hostname;interval;timestamp;CPU;%usr;%nice;%sys;%iowait;%steal;%irq;%soft;%guest;%gnice;%idle;"
            "pgpgin/s;pgpgout/s;fault/s;majflt/s;pgfree/s;pgscank/s;pgscand/s;pgsteal/s;%vmeff;"
            "kbmemfree;kbavail;kbmemused;%memused;kbbuffers;kbcached;kbcommit;%commit;kbactive;kbinact;kbdirty;"
            "runq-sz;plist-sz;ldavg-1;ldavg-5;ldavg-15;blocked\n"
            "h1;1;2023-11-14 22:13:20;-1;10.00;0.00;5.00;2.00;0.00;0.00;0.00;0.00;0.00;83.00;"
            "128;49581;0;41916;0;0;0;0;0;"
            "1000000;900000;500000;33.00;1000;2000;100000;5.00;300000;100000;10;"
            "0;0;0.43;0.45;0.39;0\n"
        )
        rows = parse_sar(str(sar_p), "", "h1", t0=1700000000)
        cpu = {r.entity: r.value for r in rows if r.metric == "CPU"}
        mem = {r.entity: r.value for r in rows if r.metric == "Memory"}
        check("sar combined-row CPU %sys -> system entity", cpu.get("system") == 5.00)
        check("sar combined-row CPU idle still present", cpu.get("idle") == 83.0)
        check("sar combined-row Memory also extracted (not dropped)", mem.get("used") == 33.0)


def test_parse_pidstat_process_and_thread_rows():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "pidstat.log"
        p.write_text(
            "Linux 5.14.0 (h1)  11/14/2023  _x86_64_ (16 CPU)\n\n"
            "#      Time   UID      TGID       TID    %usr %system  %guest   %wait    %CPU   CPU  Command\n"
            " 1700000000     0         100         -    5.00    2.00    0.00    0.00    7.00     1  elbencho\n"
            " 1700000000     0           -       101    5.00    2.00    0.00    0.00    7.00     1  |__worker\n"
        )
        rows = parse_pidstat(str(p), "h1", t0=1700000000)
        proc_rows = [r for r in rows if r.metric == "ProcessCPU"]
        thread_rows = [r for r in rows if r.metric == "ThreadCPU"]
        check("pidstat emits one process row and one thread row", len(proc_rows) == 1 and len(thread_rows) == 1)
        check("pidstat ProcessCPU value", proc_rows[0].value == 7.0 and proc_rows[0].entity == "elbencho")
        check("pidstat ThreadCPU entity includes tid", thread_rows[0].entity == "worker/101")


def test_parse_pidstat_iso_time_format():
    # S_TIME_FORMAT=ISO output (agent sets this): without it, pidstat's
    # default locale time ("06:10:43 PM") has an embedded space, which
    # shifts every whitespace-split column and silently corrupts all rows.
    import calendar
    t0 = calendar.timegm((2026, 10, 27, 18, 10, 43))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "pidstat.log"
        p.write_text(
            "Linux 5.14.0 (h1)  10/27/2026  _x86_64_ (48 CPU)\n\n"
            "#          Time   UID      TGID       TID    %usr %system  %guest   %wait    %CPU   CPU  Command\n"
            "2026-10-27T18:10:43     0         100         -    5.00    2.00    0.00    0.00    7.00     1  elbencho\n"
            "2026-10-27T18:10:43     0           -       101    5.00    2.00    0.00    0.00    7.00     1  |__worker\n"
        )
        rows = parse_pidstat(str(p), "h1", t0=t0)
        thread_rows = [r for r in rows if r.metric == "ThreadCPU"]
        check("pidstat ISO time format parses rows", len(thread_rows) == 1)
        check("pidstat ISO time elapsed_sec is 0 at t0", thread_rows and abs(thread_rows[0].elapsed_sec) < 1e-6)


def test_parse_links_diff_and_unit_conversion():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "links.csv"
        p.write_text(
            "epoch,device,port,direction,bytes_raw,kind\n"
            "1700000000,mlx5_0,1,tx,1000,ib\n"
            "1700000001,mlx5_0,1,tx,3000,ib\n"
        )
        rows = parse_links(str(p), "h1", t0=1700000000)
        check("links produces one diffed rate row", len(rows) == 1)
        # delta=2000 words * 4 bytes/word = 8000 bytes over 1s -> 8000*8/1e9 Gb/s
        expected = (2000 * 4 * 8) / 1e9
        check("links Gb/s conversion", abs(rows[0].value - expected) < 1e-12)

    with tempfile.TemporaryDirectory() as d2:
        # A counter reset (value goes down) must be skipped, not reported
        # as a bogus negative rate.
        p2 = Path(d2) / "links2.csv"
        p2.write_text(
            "epoch,device,port,direction,bytes_raw,kind\n"
            "1700000000,mlx5_0,1,tx,5000,ib\n"
            "1700000001,mlx5_0,1,tx,100,ib\n"
        )
        rows2 = parse_links(str(p2), "h1", t0=1700000000)
        check("links skips counter reset", len(rows2) == 0)


def test_parse_lctl_cumulative():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "lctl.log"
        p.write_text(
            "### 1700000000\n"
            "osc.lustre-OST0000-osc.stats=\n"
            "snapshot_time             1700000000.123456 secs.usecs\n"
            "read_bytes                10 samples [bytes] 4096 1048576 1000000\n"
            "write_bytes               5 samples [bytes] 4096 1048576 500000\n"
        )
        rows = parse_lctl(str(p), "h1", t0=1700000000)
        check("lctl parses cumulative read/write rows", len(rows) == 2)
        check("lctl entity includes device", rows[0].entity == "lustre-OST0000-osc/read")


def test_extract_window_filters_by_sample_timestamp():
    content = (
        "elbencho 1234 [003] 1700000000.100000: cpu-clock:\n"
        "\tffff1 funcA ([kernel.kallsyms])\n"
        "\n"
        "elbencho 1234 [003] 1700000005.200000: cpu-clock:\n"
        "\tffff3 funcC ([kernel.kallsyms])\n"
        "\n"
        "elbencho 1234 [003] 1700000010.300000: cpu-clock:\n"
        "\tffff4 funcD ([kernel.kallsyms])\n"
    )
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "stacks.h1.txt.gz"
        with gzip.open(p, "wt") as f:
            f.write(content)
        lines = "".join(extract_window(p, 1700000000, 1700000006))
        check("extract_window keeps in-range sample (funcA)", "funcA" in lines)
        check("extract_window keeps in-range sample (funcC)", "funcC" in lines)
        check("extract_window drops out-of-range sample (funcD)", "funcD" not in lines)


def test_merge_run_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        run_dir = Path(d)
        (run_dir / "nodes" / "h1").mkdir(parents=True)
        (run_dir / "meta.json").write_text(json.dumps({
            "run_id": "t", "label": "", "hosts": ["h1"], "profile": "light",
            "t0": 1700000000, "elbencho_cmd": "", "phases": [{"name": "main", "start": 0, "end": 5}],
        }))
        (run_dir / "elbencho_live.csv").write_text(
            "ISO Date,Label,Phase,RuntimeMS,Rank,MixType,Done%,DoneBytes,MiB/s,IOPS,Entries,Entries/s,Lat Ent us,Lat IO us\n"
            "2023-11-14 22:13:20,,main,1000,0,,50,1048576,100.0,25600,10,10,0,40\n"
        )
        out = merge_run(run_dir)
        check("merge_run writes merged.csv", out.exists())
        content = out.read_text()
        check("merge_run output has header", content.splitlines()[0].startswith("Node,"))
        check("merge_run phase filled in from meta.json", ",main," in content)


if __name__ == "__main__":
    test_parse_elbencho_rank_mapping()
    test_parse_elbencho_tz_offset_and_total_rank()
    test_parse_sar_cpu_mem_net()
    test_parse_sar_combined_row_and_sys_field()
    test_parse_pidstat_process_and_thread_rows()
    test_parse_pidstat_iso_time_format()
    test_parse_links_diff_and_unit_conversion()
    test_parse_lctl_cumulative()
    test_extract_window_filters_by_sample_timestamp()
    test_merge_run_end_to_end()

    if FAILURES:
        print(f"\n{len(FAILURES)} test(s) failed: {FAILURES}")
        sys.exit(1)
    print("\nAll tests passed.")
