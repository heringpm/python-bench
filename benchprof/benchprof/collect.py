"""Controller-side orchestration for `benchprof run`.

Starts benchprof-agent on every client via pdsh/clush, runs elbencho from
the controller, stops the agents, and gathers each client's collected
files into runs/<run-id>/nodes/<host>/. Milestone 1 scope: light profile
only (sar, pidstat, link counters, lctl); `--profile full` is accepted and
plumbed through to the agent (perf), but is only exercised starting at
milestone 3.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from benchprof.config import BenchprofConfig
from utils.hosts import resolve_hosts
from utils.shell import run_cmd


SETTLE_BEFORE_S = 5
SETTLE_AFTER_S = 5


def _remote_exec(config: BenchprofConfig, hosts: List[str], remote_cmd: str, dry_run: bool):
    if config.use_clush:
        return run_cmd(f'clush -w {",".join(hosts)} "{remote_cmd}"', dry_run=dry_run)
    return run_cmd(f'pdsh -w {",".join(hosts)} "{remote_cmd}"', dry_run=dry_run)


def make_run_id() -> str:
    now = datetime.now()
    return f"{int(now.timestamp())}_{now.strftime('%Y%m%d_%H%M%S')}"


def setup_run_dir(config: BenchprofConfig, run_id: str) -> Path:
    run_dir = Path(config.run_dir) / "runs" / run_id
    (run_dir / "nodes").mkdir(parents=True, exist_ok=True)
    return run_dir


def push_agent(config: BenchprofConfig, hosts: List[str], dry_run: bool) -> None:
    """Copy benchprof-agent + make the remote run dir on every host."""
    _remote_exec(config, hosts, f"mkdir -p {config.remote_run_dir}", dry_run)
    agent_name = Path(config.agent_path).name
    if config.use_clush:
        run_cmd(f"clush -w {','.join(hosts)} --copy {config.agent_path} --dest {config.remote_run_dir}/{agent_name}", dry_run=dry_run)
    else:
        run_cmd(f"pdcp -w {','.join(hosts)} {config.agent_path} {config.remote_run_dir}/{agent_name}", dry_run=dry_run)
    _remote_exec(config, hosts, f"chmod +x {config.remote_run_dir}/{agent_name}", dry_run)


def start_agents(config: BenchprofConfig, hosts: List[str], profile: str, perf_freq: int, dry_run: bool) -> None:
    agent_name = Path(config.agent_path).name
    cmd = (
        f"{config.remote_run_dir}/{agent_name} start --rundir {config.remote_run_dir} "
        f"--profile {profile} --perf-freq {perf_freq}"
    )
    _remote_exec(config, hosts, cmd, dry_run)


def stop_agents(config: BenchprofConfig, hosts: List[str], keep_raw: bool, dry_run: bool) -> None:
    agent_name = Path(config.agent_path).name
    keep_flag = " --keep-raw" if keep_raw else ""
    cmd = f"{config.remote_run_dir}/{agent_name} stop --rundir {config.remote_run_dir}{keep_flag}"
    _remote_exec(config, hosts, cmd, dry_run)


def gather(config: BenchprofConfig, hosts: List[str], run_dir: Path, dry_run: bool) -> None:
    """Copy each client's collected files back into run_dir/nodes/<host>/,
    then clean up the remote run dir."""
    for host in hosts:
        dest = run_dir / "nodes" / host
        dest.mkdir(parents=True, exist_ok=True)
        run_cmd(f"rsync -a {host}:{config.remote_run_dir}/ {dest}/", dry_run=dry_run)
    _remote_exec(config, hosts, f"rm -rf {config.remote_run_dir}", dry_run)


def write_meta(run_dir: Path, run_id: str, label: str, hosts: List[str], profile: str,
                t0: float, elbencho_cmd: str, phases: Optional[list] = None) -> None:
    meta = {
        "run_id": run_id,
        "label": label,
        "hosts": hosts,
        "profile": profile,
        "t0": t0,
        "elbencho_cmd": elbencho_cmd,
        "phases": phases or [],
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")


def run_benchmark(config: BenchprofConfig, hosts: List[str], label: str, profile: str,
                   perf_freq: int, elbencho_args: str, dry_run: bool = False) -> Path:
    """Full `benchprof run` sequence. Returns the local run directory."""
    run_id = make_run_id()
    run_dir = setup_run_dir(config, run_id)

    push_agent(config, hosts, dry_run)
    start_agents(config, hosts, profile, perf_freq, dry_run)

    print(f"Settling {SETTLE_BEFORE_S}s before elbencho so panels show an idle baseline...")
    if not dry_run:
        time.sleep(SETTLE_BEFORE_S)

    t0 = time.time()
    elbencho_cmd = (
        f"{config.elbencho_path} --hostsfile {config.machine_file} "
        f"--livecsv {run_dir}/elbencho_live.csv --livecsvex --liveint 1000 {elbencho_args}"
    )
    print(f"Running: {elbencho_cmd}")
    run_cmd(elbencho_cmd, dry_run=dry_run)

    print(f"Settling {SETTLE_AFTER_S}s after elbencho so panels show an idle tail...")
    if not dry_run:
        time.sleep(SETTLE_AFTER_S)

    stop_agents(config, hosts, keep_raw=False, dry_run=dry_run)
    gather(config, hosts, run_dir, dry_run)
    write_meta(run_dir, run_id, label, hosts, profile, t0, elbencho_cmd)

    if profile == "full" and not dry_run:
        from benchprof.flame import build_phase_flames
        build_phase_flames(run_dir)

    print(f"Run complete: {run_dir}")
    return run_dir


_RANGE_RE = re.compile(r"^([^\[\]]*)\[(\d+)-(\d+)\](.*)$")


def expand_hosts(spec: str) -> List[str]:
    """Expand a pdsh-style hostlist: comma-separated tokens, each either a
    bare hostname or "prefix[NN-MM]suffix" (zero-padded to match NN's
    width, e.g. "client[01-04]" -> client01..client04)."""
    hosts: List[str] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        m = _RANGE_RE.match(token)
        if not m:
            hosts.append(token)
            continue
        prefix, lo, hi, suffix = m.groups()
        width = len(lo)
        for n in range(int(lo), int(hi) + 1):
            hosts.append(f"{prefix}{str(n).zfill(width)}{suffix}")
    return hosts


def resolve_run_hosts(config: BenchprofConfig, hosts_spec: Optional[str]) -> List[str]:
    """hosts_spec is a pdsh-style hostlist (--hosts, e.g. "client[01-04]"
    or "client01,client02"); if not given, fall back to every host in the
    configured machinefile."""
    if hosts_spec:
        return expand_hosts(hosts_spec)
    with open(config.machine_file) as f:
        return [line.strip() for line in f if line.strip()]
