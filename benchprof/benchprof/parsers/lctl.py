"""Parse benchprof-agent's lctl.<host>.log (periodic `lctl get_param`
snapshots) into the merged long schema.

Format (see benchprof-agent's start_lctl()): repeating blocks of

    ### <epoch>
    osc.<device>.stats=
    snapshot_time             <ts> secs.usecs
    read_bytes                <N> samples [bytes] <min> <max> <sum>
    write_bytes               <N> samples [bytes] <min> <max> <sum>
    ...

`read_bytes`/`write_bytes` are cumulative counters since mount, so these
are emitted as cumulative-byte-counter rows (metric "LustreIOBytesCum");
report.py converts them to a rate by diffing consecutive samples per
(node, device, direction), the same way links.py's raw IB counters would
be handled if diffed there instead of in-parser - done at report time
here since lctl's polling interval (5s, vs. 1s for sar/links) differs
enough from the other sources that a shared downsampled diff pass in the
report stage is simpler than duplicating the diff logic per-parser.
"""

import re
from pathlib import Path
from typing import List

from benchprof.schema import MetricRow

_DEVICE_RE = re.compile(r"^osc\.([^.]+)\.stats=$")
_STAT_RE = re.compile(r"^(read_bytes|write_bytes)\s+(\d+)\s+samples\s+\[bytes\]\s+(\d+)\s+(\d+)\s+(\d+)\s*$")


def parse_lctl(path: str, node: str, t0: float) -> List[MetricRow]:
    rows: List[MetricRow] = []
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        return rows

    epoch = None
    device = None
    with open(p) as f:
        for line in f:
            line = line.rstrip("\n").strip()
            if not line:
                continue
            if line.startswith("### "):
                try:
                    epoch = float(line[4:].strip())
                except ValueError:
                    epoch = None
                device = None
                continue
            m = _DEVICE_RE.match(line)
            if m:
                device = m.group(1)
                continue
            m = _STAT_RE.match(line)
            if m and epoch is not None and device is not None:
                stat_name, _samples, _min, _max, total = m.groups()
                direction = "read" if stat_name == "read_bytes" else "write"
                rows.append(MetricRow(
                    node=node, timestamp="", elapsed_sec=epoch - t0, phase="",
                    source="lctl", metric="LustreIOBytesCum", entity=f"{device}/{direction}",
                    value=float(total), unit="bytes",
                ))
    return rows
