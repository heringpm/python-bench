"""Parse elbencho's --livecsv (with --livecsvex) output into the merged
long schema.

Live CSV header (elbencho >= 3.x, confirmed against the project's own
docs/issue tracker):

    ISO Date,Label,Phase,RuntimeMS,Rank,MixType,Done%,DoneBytes,MiB/s,
    IOPS,Entries,Entries/s,Lat Ent us,Lat IO us

With --livecsvex there is one row per interval *per service rank* (one
rank per --hostsfile line, in file order) instead of one aggregate row;
`hosts` below is that same ordered host list so Rank N can be mapped back
to the hostname that produced it, letting these throughput/IOPS rows line
up on the per-node panels next to that node's sar/pidstat/link data.

MixType is only meaningful for --rwmixpct runs; it's "" (or missing) for
single-direction read/write runs, so Entity falls back to "io" so we
always emit a non-empty dimension.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from benchprof.schema import MetricRow

_METRIC_COLUMNS = [
    # (csv column name, Metric, Unit)
    ("MiB/s", "Throughput", "MiB/s"),
    ("IOPS", "IOPS", "IOPS"),
    ("Entries/s", "EntriesRate", "entries/s"),
    ("Lat Ent us", "LatencyEntry", "us"),
    ("Lat IO us", "LatencyIO", "us"),
]


def _parse_iso(iso_date: str) -> Optional[datetime]:
    # Elbencho emits a numeric UTC offset (e.g. "+0000") with no colon,
    # which strptime can only parse via "%z" - try offset-aware formats
    # first, then fall back to the naive ones for older elbencho builds
    # that don't include an offset at all.
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(iso_date, fmt)
        except ValueError:
            continue
    return None


def parse_elbencho(path: str, hosts: List[str], t0: float, phases: Optional[list] = None) -> List[MetricRow]:
    rows: List[MetricRow] = []
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return rows

    # With --livecsvex, elbencho emits a "Total" row (the real
    # rate/IOPS/latency numbers, pre-aggregated across all ranks) plus one
    # row per rank in the same interval. In standalone/thread mode the
    # per-rank rows leave MiB/s/IOPS/latency blank (they only carry
    # DoneBytes) so only the Total row ever contributes - but in
    # distributed/service mode (--hostsfile) the per-rank rows *do* carry
    # real populated values too, so counting both the Total row and the
    # per-rank rows double-counts every metric (Total already equals the
    # sum of the per-rank rows). To avoid that, rows are first grouped by
    # interval (RuntimeMS+Phase) and, within each group, the Total row is
    # only used as a fallback for columns that have no real per-rank data;
    # whenever per-rank rows carry real values for a column, those are
    # used instead (and the Total row's value for that column is dropped)
    # so throughput/IOPS/etc. are counted exactly once, either per-node or
    # once as a controller-level aggregate.
    groups: "dict" = {}
    order: List[tuple] = []
    with open(p, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return rows
        for d in reader:
            dt = _parse_iso((d.get("ISO Date") or "").strip())
            if dt is None:
                continue
            key = (d.get("RuntimeMS"), (d.get("Phase") or "").strip())
            if key not in groups:
                groups[key] = {"dt": dt, "total": [], "rank": []}
                order.append(key)
            rank_raw = (d.get("Rank") or "").strip()
            if rank_raw in ("", "-1", "Total"):
                groups[key]["total"].append(d)
            else:
                groups[key]["rank"].append(d)

    for key in order:
        g = groups[key]
        dt = g["dt"]
        elapsed = dt.timestamp() - t0

        # Which columns have real (non-blank) data in the per-rank rows for
        # this interval - those columns are taken from the per-rank rows
        # only, and skipped on the Total row to avoid double-counting.
        rank_has_col = {
            col for col, _, _ in _METRIC_COLUMNS
            for d in g["rank"]
            if (d.get(col) or "").strip() != ""
        }

        for d in g["rank"]:
            rank_raw = (d.get("Rank") or "").strip()
            try:
                rank = int(rank_raw)
                node = hosts[rank] if 0 <= rank < len(hosts) else f"rank{rank}"
            except ValueError:
                node = f"rank{rank_raw}"
            entity = (d.get("MixType") or "").strip() or "io"
            phase = (d.get("Phase") or "").strip()
            for col, metric, unit in _METRIC_COLUMNS:
                raw = (d.get(col) or "").strip()
                if raw == "":
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                rows.append(MetricRow(
                    node=node, timestamp=dt.isoformat(), elapsed_sec=elapsed,
                    phase=phase, source="elbencho", metric=metric, entity=entity,
                    value=value, unit=unit,
                ))

        for d in g["total"]:
            entity = (d.get("MixType") or "").strip() or "io"
            phase = (d.get("Phase") or "").strip()
            for col, metric, unit in _METRIC_COLUMNS:
                if col in rank_has_col:
                    continue
                raw = (d.get(col) or "").strip()
                if raw == "":
                    continue
                try:
                    value = float(raw)
                except ValueError:
                    continue
                rows.append(MetricRow(
                    node="controller", timestamp=dt.isoformat(), elapsed_sec=elapsed,
                    phase=phase, source="elbencho", metric=metric, entity=entity,
                    value=value, unit=unit,
                ))
    return rows
