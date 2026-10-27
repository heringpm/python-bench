"""Parse `sadf -d -h -- -u ALL -r -B -q <sa file>` (sar.<host>.csv) and
`sadf -d -h -- -n DEV <sa file>` (sar_net.<host>.csv) output into the
merged long schema.

`-h` makes sadf emit a "# hostname;interval;timestamp;<field names>"
header line before each activity's data rows, so activities are told
apart by field names (robust across sysstat versions) rather than by
assumed column order. Each activity's rows are detected by which
well-known field is present in its header (e.g. "%idle" => CPU stats).
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from benchprof.schema import MetricRow


def _parse_ts(date_str: str, time_str: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(f"{date_str} {time_str}", fmt)
        except ValueError:
            continue
    return None


def _rows_from_block(header: List[str], data: List[str], node: str, t0: float,
                      source: str, metric_map) -> List[MetricRow]:
    if len(data) != len(header):
        return []
    d = dict(zip(header, data))
    # sadf -d prefixes every row with hostname;interval;timestamp (the
    # timestamp column's own header name varies by locale/version, so
    # just use the 3rd field positionally).
    ts_raw = data[2] if len(data) > 2 else ""
    if " " in ts_raw:
        date_part, time_part = ts_raw.split(" ", 1)
    else:
        date_part, time_part = "", ts_raw
    dt = _parse_ts(date_part, time_part) if date_part else None
    if dt is None:
        return []
    elapsed = dt.timestamp() - t0
    out = []
    for field, metric, entity_fn, unit in metric_map:
        if field not in d or d[field] == "":
            continue
        try:
            value = float(d[field])
        except ValueError:
            continue
        entity = entity_fn(d)
        out.append(MetricRow(
            node=node, timestamp=dt.isoformat(), elapsed_sec=elapsed, phase="",
            source=source, metric=metric, entity=entity, value=value, unit=unit,
        ))
    return out


_CPU_FIELDS = [
    ("%usr", "CPU", lambda d: "user", "%"),
    ("%system", "CPU", lambda d: "system", "%"),
    ("%iowait", "CPU", lambda d: "iowait", "%"),
    ("%idle", "CPU", lambda d: "idle", "%"),
]
_MEM_FIELDS = [
    ("%memused", "Memory", lambda d: "used", "%"),
    ("kbmemused", "Memory", lambda d: "used_kb", "kB"),
    ("kbcached", "Memory", lambda d: "cached_kb", "kB"),
]
_PAGING_FIELDS = [
    ("pgfree/s", "Paging", lambda d: "free", "pages/s"),
    ("majflt/s", "Paging", lambda d: "majflt", "faults/s"),
]
_LOAD_FIELDS = [
    ("ldavg-1", "LoadAvg", lambda d: "1min", "load"),
    ("ldavg-5", "LoadAvg", lambda d: "5min", "load"),
    ("ldavg-15", "LoadAvg", lambda d: "15min", "load"),
]
_NET_FIELDS = [
    ("rxkB/s", "NetworkIface", lambda d: f"{d.get('IFACE', '?')}/rx", "kB/s"),
    ("txkB/s", "NetworkIface", lambda d: f"{d.get('IFACE', '?')}/tx", "kB/s"),
    ("%ifutil", "NetworkIface", lambda d: f"{d.get('IFACE', '?')}/util", "%"),
]


def _pick_metric_map(header: List[str]):
    hset = set(header)
    if "%idle" in hset:
        return _CPU_FIELDS
    if "%memused" in hset:
        return _MEM_FIELDS
    if "pgfree/s" in hset:
        return _PAGING_FIELDS
    if "ldavg-1" in hset:
        return _LOAD_FIELDS
    if "rxkB/s" in hset:
        return _NET_FIELDS
    return None


def _parse_sadf_file(path: Path, node: str, t0: float) -> List[MetricRow]:
    rows: List[MetricRow] = []
    if not path.is_file() or path.stat().st_size == 0:
        return rows
    header: Optional[List[str]] = None
    metric_map = None
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("#"):
                header = line.lstrip("#").strip().split(";")
                metric_map = _pick_metric_map(header)
                continue
            if header is None or metric_map is None:
                continue
            data = line.split(";")
            rows.extend(_rows_from_block(header, data, node, t0, "sar", metric_map))
    return rows


def parse_sar(sar_csv: str, sar_net_csv: str, node: str, t0: float) -> List[MetricRow]:
    """sar_csv is the -u/-r/-B/-q output, sar_net_csv the -n DEV output
    (kept separate - see benchprof-agent - since DEV is multi-instance per
    timestamp while the others are single-instance)."""
    rows = _parse_sadf_file(Path(sar_csv), node, t0)
    rows.extend(_parse_sadf_file(Path(sar_net_csv), node, t0))
    return rows
