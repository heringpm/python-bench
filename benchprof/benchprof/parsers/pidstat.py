"""Parse `pidstat -t -u -h 1` output (pidstat.<host>.log) into the merged
long schema.

`-h` prints one unix-epoch-timestamped header + data stream (no repeated
per-interval headers), `-t` adds both TGID and TID columns so each row is
either a process-level summary (TID == "-") or a per-thread breakdown
(TGID == "-"). Process-level rows are emitted as "ProcessCPU" and
per-thread rows as "ThreadCPU" (entity includes the TID to disambiguate
same-named threads); report.py's per-thread panel sums neither on top of
the other, it's the caller's choice which metric to chart.
"""

from pathlib import Path
from typing import List

from benchprof.schema import MetricRow


def parse_pidstat(path: str, node: str, t0: float) -> List[MetricRow]:
    rows: List[MetricRow] = []
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return rows

    header: List[str] = []
    with open(p) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line or line.startswith("Linux") or line.startswith("Average"):
                continue
            if line.lstrip().startswith("#"):
                header = line.lstrip("#").split()
                continue
            if not header:
                continue
            parts = line.split()
            if len(parts) < len(header):
                continue
            # Command (last column) may itself contain spaces/pipes for
            # thread names ("|__worker") - keep header-aligned fields as
            # the first N-1 tokens, Command as everything after.
            fixed = parts[:len(header) - 1]
            command = " ".join(parts[len(header) - 1:])
            d = dict(zip(header[:-1], fixed))
            d[header[-1]] = command

            tid = d.get("TID", "-")

            try:
                epoch = float(d.get("Time", ""))
                cpu_pct = float(d.get("%CPU", ""))
            except (TypeError, ValueError):
                continue

            if tid == "-":
                rows.append(MetricRow(
                    node=node, timestamp="", elapsed_sec=epoch - t0, phase="",
                    source="pidstat", metric="ProcessCPU", entity=d.get("Command", "?"),
                    value=cpu_pct, unit="%",
                ))
            else:
                command = d.get("Command", "?").lstrip("|_")
                rows.append(MetricRow(
                    node=node, timestamp="", elapsed_sec=epoch - t0, phase="",
                    source="pidstat", metric="ThreadCPU", entity=f"{command}/{tid}",
                    value=cpu_pct, unit="%",
                ))
    return rows
