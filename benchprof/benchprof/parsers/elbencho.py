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
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
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

    with open(p, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return rows
        for d in reader:
            dt = _parse_iso((d.get("ISO Date") or "").strip())
            if dt is None:
                continue
            elapsed = dt.timestamp() - t0
            rank_raw = (d.get("Rank") or "").strip()
            node = "controller"
            if rank_raw not in ("", "-1"):
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
    return rows
