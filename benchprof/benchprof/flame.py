"""Generate flame graphs from benchprof-agent's per-host `perf script`
output (stacks.<host>.txt.gz, produced only under --profile full).

perf record was invoked with `-k CLOCK_REALTIME` (see benchprof-agent),
so every sample's timestamp in `perf script` output is wall-clock epoch
seconds, not seconds-since-boot. That lets us slice a window by elapsed
seconds (relative to the run's meta.json t0) instead of needing any
separate clock-offset bookkeeping between the perf host and the
controller.
"""

import gzip
import re
import shutil
from pathlib import Path
from typing import List, Optional

from utils.shell import run_cmd

FLAMEGRAPH_DIR = Path(__file__).resolve().parents[2] / "utils" / "FlameGraph"

# Matches the "time" field in `perf script -F comm,tid,cpu,time,ip,sym,dso`
# header lines, e.g. "elbencho 12345 [003] 1700000012.345678: cpu-clock:"
_TIME_RE = re.compile(r"(\d+\.\d+):")


def _vendored_script(name: str) -> Optional[str]:
    p = FLAMEGRAPH_DIR / name
    return str(p) if p.is_file() else None


def _tool(name: str) -> Optional[str]:
    return shutil.which(name) or _vendored_script(name)


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt")
    return open(path)


def extract_window(stacks_path: Path, start_epoch: float, end_epoch: float) -> List[str]:
    """Return the `perf script` sample blocks (header + stack lines +
    trailing blank line) whose header timestamp falls within
    [start_epoch, end_epoch]."""
    out: List[str] = []
    block: List[str] = []
    keep = False
    with _open_text(stacks_path) as f:
        for line in f:
            if line.strip() == "":
                if keep and block:
                    out.extend(block)
                    out.append(line)
                block = []
                keep = False
                continue
            if not line.startswith((" ", "\t")):
                # header line - starts a new block
                if keep and block:
                    out.extend(block)
                block = [line]
                m = _TIME_RE.search(line)
                keep = bool(m and start_epoch <= float(m.group(1)) <= end_epoch)
                continue
            block.append(line)
        if keep and block:
            out.extend(block)
    return out


def build_flame_svg(stacks_path: Path, start_epoch: float, end_epoch: float,
                     out_svg: Path, title: str) -> Optional[Path]:
    """Slice stacks_path to [start_epoch, end_epoch] and render an .svg via
    the vendored (or PATH) FlameGraph scripts. Returns None (with a message
    on stderr) if the scripts aren't available or no samples fall in the
    window."""
    stackcollapse = _tool("stackcollapse-perf.pl")
    flamegraph = _tool("flamegraph.pl")
    if not stackcollapse or not flamegraph:
        print(f"benchprof flame: stackcollapse-perf.pl/flamegraph.pl not found "
              f"on PATH or in {FLAMEGRAPH_DIR}")
        return None

    lines = extract_window(stacks_path, start_epoch, end_epoch)
    if not lines:
        print(f"benchprof flame: no samples for {stacks_path.name} in "
              f"[{start_epoch}, {end_epoch}]")
        return None

    out_svg.parent.mkdir(parents=True, exist_ok=True)
    window_script = out_svg.with_suffix(".perf.script")
    window_script.write_text("".join(lines))
    run_cmd(f'{stackcollapse} "{window_script}" | {flamegraph} --title "{title}" > "{out_svg}"')
    window_script.unlink(missing_ok=True)
    print(f"Wrote {out_svg}")
    return out_svg


def build_flame_for_node(run_dir: Path, node: str, from_s: float, to_s: float) -> Optional[Path]:
    """Milestone 3 entry point for `benchprof flame`: from_s/to_s are
    elapsed seconds relative to meta.json's t0."""
    import json
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text())
    t0 = meta["t0"]

    stacks_path = run_dir / "nodes" / node / f"stacks.{node}.txt.gz"
    if not stacks_path.exists():
        stacks_path = run_dir / "nodes" / node / f"stacks.{node}.txt"
    if not stacks_path.exists():
        print(f"benchprof flame: no stacks file for node {node} "
              f"(was --profile full used for this run?)")
        return None

    out_svg = run_dir / "flames" / f"{node}.{from_s:g}-{to_s:g}.svg"
    title = f"{node} [{from_s:g}s - {to_s:g}s]"
    return build_flame_svg(stacks_path, t0 + from_s, t0 + to_s, out_svg, title)


def build_phase_flames(run_dir: Path) -> List[Path]:
    """Milestone 3: automatically render one flame graph per (node, phase)
    for `full`-profile runs, called from collect.run_benchmark after
    gather(). Silently does nothing for nodes/runs without stacks files or
    without any configured phases."""
    import json
    run_dir = Path(run_dir)
    meta = json.loads((run_dir / "meta.json").read_text())
    t0 = meta["t0"]
    phases = meta.get("phases", [])
    if not phases:
        return []

    out_paths: List[Path] = []
    for node in meta.get("hosts", []):
        stacks_path = run_dir / "nodes" / node / f"stacks.{node}.txt.gz"
        if not stacks_path.exists():
            continue
        for phase in phases:
            name = phase.get("name", "phase")
            start, end = phase.get("start", 0.0), phase.get("end", 0.0)
            out_svg = run_dir / "flames" / f"{node}.{name}.svg"
            svg = build_flame_svg(stacks_path, t0 + start, t0 + end, out_svg,
                                   f"{node} [{name}]")
            if svg:
                out_paths.append(svg)
    return out_paths
