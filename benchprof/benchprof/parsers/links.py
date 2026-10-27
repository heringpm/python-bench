"""Parse benchprof-agent's links.<host>.csv (raw IB/RoCE counter samples)
into Gb/s rates in the merged long schema.

Input columns: epoch,device,port,direction,bytes_raw,kind
  - kind == "ib": bytes_raw is in 4-byte words (IBTA counter convention
    for port_xmit_data/port_rcv_data), so actual bytes = bytes_raw * 4.
  - kind == "roce": bytes_raw is already bytes (ethtool -S byte counters).

Counters are cumulative since link reset, so the rate for a given
(device, port, direction) at sample N is the byte delta to sample N-1
divided by the elapsed-time delta. A negative delta (counter reset/wrap)
is skipped rather than reported as a bogus negative rate.
"""

import csv
from collections import defaultdict
from pathlib import Path
from typing import List

from benchprof.schema import MetricRow

_BYTES_PER_UNIT = {"ib": 4, "roce": 1}


def parse_links(path: str, node: str, t0: float) -> List[MetricRow]:
    rows: List[MetricRow] = []
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return rows

    series = defaultdict(list)  # (device, port, direction, kind) -> [(epoch, bytes)]
    with open(p, newline="") as f:
        for d in csv.DictReader(f):
            try:
                epoch = float(d["epoch"])
                bytes_val = float(d["bytes_raw"]) * _BYTES_PER_UNIT.get(d.get("kind", "ib"), 1)
            except (KeyError, ValueError):
                continue
            key = (d.get("device", "?"), d.get("port", "?"), d.get("direction", "?"))
            series[key].append((epoch, bytes_val))

    for (device, port, direction), samples in series.items():
        samples.sort(key=lambda s: s[0])
        for (prev_epoch, prev_bytes), (epoch, bytes_val) in zip(samples, samples[1:]):
            dt = epoch - prev_epoch
            delta = bytes_val - prev_bytes
            if dt <= 0 or delta < 0:
                continue
            gbps = (delta * 8) / 1e9 / dt
            rows.append(MetricRow(
                node=node, timestamp="", elapsed_sec=epoch - t0, phase="",
                source="links", metric="NetworkLink", entity=f"{device}/{port}/{direction}",
                value=gbps, unit="Gb/s",
            ))
    return rows
