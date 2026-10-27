"""Merged long-schema data model that every benchprof parser normalizes
into, so wildly different telemetry sources (elbencho live stats, sar,
pidstat, IB/RoCE link counters, lctl snapshots) can be merged, aligned on
a single elapsed-seconds x-axis, and plotted/compared uniformly.

Columns: Node, Timestamp, ElapsedSec, Phase, Source, Metric, Entity,
Value, Unit

    Node        hostname the sample came from (or "controller" for
                elbencho aggregate rows collected locally)
    Timestamp   ISO-8601 wall-clock time of the sample
    ElapsedSec  seconds since the run's t0 (float, may be negative during
                the pre/post settle windows)
    Phase       phase label from meta.json's "phases" list covering this
                ElapsedSec, or "" if none/not configured
    Source      collector that produced the row: elbencho | sar | pidstat
                | links | lctl
    Metric      metric name, e.g. "Throughput", "CPU", "Memory",
                "NetworkLink", "ProcessCPU", "LustreIO"
    Entity      sub-dimension within Metric, e.g. "read"/"write" for
                Throughput, a CPU id or "all" for CPU, a process command
                name for ProcessCPU, "ib0/1/tx" for NetworkLink
    Value       numeric value
    Unit        unit string, e.g. "MiB/s", "%", "Gb/s", "us"
"""

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Union


FIELDS = ["Node", "Timestamp", "ElapsedSec", "Phase", "Source", "Metric", "Entity", "Value", "Unit"]


@dataclass
class MetricRow:
    node: str
    timestamp: str
    elapsed_sec: float
    phase: str
    source: str
    metric: str
    entity: str
    value: float
    unit: str

    def as_csv_row(self) -> list:
        return [self.node, self.timestamp, round(self.elapsed_sec, 3), self.phase,
                self.source, self.metric, self.entity, self.value, self.unit]


def phase_for(elapsed_sec: float, phases: list) -> str:
    """phases is a list of {"name": str, "start": float, "end": float}
    (elapsed seconds, from meta.json); returns the first match or ""."""
    for p in phases or []:
        if p.get("start", float("-inf")) <= elapsed_sec <= p.get("end", float("inf")):
            return p.get("name", "")
    return ""


def write_rows(path: Union[str, Path], rows: Iterable[MetricRow]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        for row in rows:
            w.writerow(row.as_csv_row())


def read_rows(path: Union[str, Path]) -> List[MetricRow]:
    rows = []
    with open(path, newline="") as f:
        for d in csv.DictReader(f):
            rows.append(MetricRow(
                node=d["Node"], timestamp=d["Timestamp"], elapsed_sec=float(d["ElapsedSec"]),
                phase=d["Phase"], source=d["Source"], metric=d["Metric"], entity=d["Entity"],
                value=float(d["Value"]), unit=d["Unit"],
            ))
    return rows


def merge_into(path: Union[str, Path], rows: Iterable[MetricRow], phases: Optional[list] = None) -> None:
    """Append rows to an existing merged CSV (creating it with a header if
    it doesn't exist yet), filling in Phase from `phases` if a row didn't
    already set one."""
    rows = list(rows)
    if phases:
        for r in rows:
            if not r.phase:
                r.phase = phase_for(r.elapsed_sec, phases)
    path = Path(path)
    write_header = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(FIELDS)
        for row in rows:
            w.writerow(row.as_csv_row())
