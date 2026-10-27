"""Merge every node's collected files (nodes/<host>/...) plus the
controller-side elbencho live CSV into a single runs/<id>/merged.csv in
the schema.MetricRow long format, ready for report.py to pivot into
panels.
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import List

from benchprof.parsers.elbencho import parse_elbencho
from benchprof.parsers.lctl import parse_lctl
from benchprof.parsers.links import parse_links
from benchprof.parsers.pidstat import parse_pidstat
from benchprof.parsers.sar import parse_sar
from benchprof.schema import MetricRow, phase_for, write_rows


def _diff_cumulative(rows: List[MetricRow]) -> List[MetricRow]:
    """Convert LustreIOBytesCum cumulative-counter rows into LustreIOBytes
    bytes/s rate rows, diffing consecutive samples per (node, entity). Any
    counter reset (negative delta) is skipped rather than reported."""
    series = defaultdict(list)
    for r in rows:
        if r.metric == "LustreIOBytesCum":
            series[(r.node, r.entity)].append(r)
    out = []
    for (_node, _entity), samples in series.items():
        samples.sort(key=lambda r: r.elapsed_sec)
        for prev, cur in zip(samples, samples[1:]):
            dt = cur.elapsed_sec - prev.elapsed_sec
            delta = cur.value - prev.value
            if dt <= 0 or delta < 0:
                continue
            out.append(MetricRow(
                node=cur.node, timestamp=cur.timestamp, elapsed_sec=cur.elapsed_sec,
                phase=cur.phase, source="lctl", metric="LustreIOBytes", entity=cur.entity,
                value=delta / dt, unit="bytes/s",
            ))
    return out


def merge_run(run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text())
    hosts = meta["hosts"]
    t0 = meta["t0"]
    phases = meta.get("phases", [])

    rows: List[MetricRow] = []

    live_csv = run_dir / "elbencho_live.csv"
    rows.extend(parse_elbencho(str(live_csv), hosts, t0, phases))

    for host in hosts:
        node_dir = run_dir / "nodes" / host
        if not node_dir.is_dir():
            continue
        rows.extend(parse_sar(str(node_dir / f"sar.{host}.csv"), str(node_dir / f"sar_net.{host}.csv"), host, t0))
        rows.extend(parse_pidstat(str(node_dir / f"pidstat.{host}.log"), host, t0))
        rows.extend(parse_links(str(node_dir / f"links.{host}.csv"), host, t0))
        rows.extend(parse_lctl(str(node_dir / f"lctl.{host}.log"), host, t0))

    rows.extend(_diff_cumulative(rows))
    rows = [r for r in rows if r.metric != "LustreIOBytesCum"]

    for r in rows:
        if not r.phase:
            r.phase = phase_for(r.elapsed_sec, phases)

    out_path = run_dir / "merged.csv"
    write_rows(out_path, rows)
    print(f"Wrote {len(rows)} rows to {out_path}")
    return out_path
