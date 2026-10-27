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
import shlex
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from benchprof.config import BenchprofConfig
from utils.hosts import resolve_hosts
from utils.shell import run_cmd


SETTLE_BEFORE_S = 5
SETTLE_AFTER_S = 5

_WRITE_FLAGS = ("--write", "-w")
_MKDIRS_FLAGS = ("--mkdirs", "-d")


def _ensure_mkdirs(elbencho_args: str) -> str:
    """Auto-add --mkdirs when --write is requested without it, so users
    don't need to remember to create directories first (elbencho allows
    --mkdirs and --write in the same invocation - it internally runs the
    phases in the sensible order: mkdirs, then write, etc). This keeps
    `benchprof run` point-and-click instead of requiring a separate
    --mkdirs pass like the older benchmarks/elbencho.py helper does."""
    try:
        tokens = shlex.split(elbencho_args)
    except ValueError:
        # Unbalanced quotes etc - leave args untouched and let elbencho
        # itself report the error.
        return elbencho_args
    has_write = any(t in _WRITE_FLAGS for t in tokens)
    has_mkdirs = any(t in _MKDIRS_FLAGS for t in tokens)
    if has_write and not has_mkdirs:
        tokens.insert(0, "--mkdirs")
        return shlex.join(tokens)
    return elbencho_args


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
    """Copy benchprof-agent (+ benchprof-exporter, which lives next to it and
    is started by the agent when --export-port is given) and make the
    remote run dir on every host."""
    _remote_exec(config, hosts, f"mkdir -p {config.remote_run_dir}", dry_run)
    agent_name = Path(config.agent_path).name
    exporter_path = Path(config.agent_path).parent / "benchprof-exporter"
    exporter_name = exporter_path.name
    if config.use_clush:
        run_cmd(f"clush -w {','.join(hosts)} --copy {config.agent_path} --dest {config.remote_run_dir}/{agent_name}", dry_run=dry_run)
        run_cmd(f"clush -w {','.join(hosts)} --copy {exporter_path} --dest {config.remote_run_dir}/{exporter_name}", dry_run=dry_run)
    else:
        run_cmd(f"pdcp -w {','.join(hosts)} {config.agent_path} {config.remote_run_dir}/{agent_name}", dry_run=dry_run)
        run_cmd(f"pdcp -w {','.join(hosts)} {exporter_path} {config.remote_run_dir}/{exporter_name}", dry_run=dry_run)
    _remote_exec(config, hosts, f"chmod +x {config.remote_run_dir}/{agent_name} {config.remote_run_dir}/{exporter_name}", dry_run)


def start_agents(config: BenchprofConfig, hosts: List[str], profile: str, perf_freq: int, dry_run: bool,
                  export_port: Optional[int] = None) -> None:
    agent_name = Path(config.agent_path).name
    cmd = (
        f"{config.remote_run_dir}/{agent_name} start --rundir {config.remote_run_dir} "
        f"--profile {profile} --perf-freq {perf_freq}"
    )
    if export_port is not None:
        cmd += f" --export-port {export_port}"
    _remote_exec(config, hosts, cmd, dry_run)


def stop_agents(config: BenchprofConfig, hosts: List[str], keep_raw: bool, dry_run: bool) -> None:
    agent_name = Path(config.agent_path).name
    keep_flag = " --keep-raw" if keep_raw else ""
    cmd = f"{config.remote_run_dir}/{agent_name} stop --rundir {config.remote_run_dir}{keep_flag}"
    _remote_exec(config, hosts, cmd, dry_run)


def abort_agents(config: BenchprofConfig, hosts: List[str], dry_run: bool) -> None:
    """Best-effort emergency teardown used when a run is interrupted
    (Ctrl-C) or fails partway through: kill -9 every collector on every
    host immediately, skipping stop()'s normal sadf/perf-script
    post-processing (which assumes a clean, completed run and can itself
    hang). Safe to call even if nothing is running."""
    agent_name = Path(config.agent_path).name
    cmd = f"{config.remote_run_dir}/{agent_name} abort --rundir {config.remote_run_dir}"
    _remote_exec(config, hosts, cmd, dry_run)


def start_elbencho_service(config: BenchprofConfig, hosts: List[str], dry_run: bool) -> None:
    """Distributed elbencho needs a persistent `elbencho --service` daemon
    running on every client before the controller's --hostsfile command can
    connect to them (same pattern as benchmarks/elbencho.py). Without this,
    the controller command has nothing to talk to - it can silently fail
    to produce any progress output or real I/O."""
    if config.use_clush:
        cmd = f"clush --machinefile {config.machine_file} {config.elbencho_path} --service"
    else:
        cmd = f'pdsh -w {",".join(hosts)} {config.elbencho_path} --service'
    run_cmd(cmd, dry_run=dry_run)


def stop_elbencho_service(config: BenchprofConfig, hosts: List[str], dry_run: bool) -> None:
    if config.use_clush:
        cmd = f"clush --machinefile {config.machine_file} killall elbencho"
    else:
        cmd = f'pdsh -w {",".join(hosts)} killall elbencho'
    run_cmd(cmd, dry_run=dry_run)


def gather(config: BenchprofConfig, hosts: List[str], run_dir: Path, dry_run: bool) -> None:
    """Copy each client's collected files back into run_dir/nodes/<host>/,
    then clean up the remote run dir."""
    for host in hosts:
        dest = run_dir / "nodes" / host
        dest.mkdir(parents=True, exist_ok=True)
        run_cmd(f"rsync -a {host}:{config.remote_run_dir}/ {dest}/", dry_run=dry_run)
    _remote_exec(config, hosts, f"rm -rf {config.remote_run_dir}", dry_run)


def warn_missing_sar(run_dir: Path, hosts: List[str]) -> None:
    """Print a warning if any client is missing `sadc` (sysstat), so the
    resulting blank sar CPU/Memory report panels aren't a silent mystery."""
    missing = [h for h in hosts if (run_dir / "nodes" / h / f"sar.{h}.MISSING").exists()]
    if missing:
        print(
            f"benchprof: warning: sadc (sysstat) not found on {len(missing)} node(s) "
            f"({', '.join(missing[:5])}{', ...' if len(missing) > 5 else ''}) - "
            "CPU/Memory sar panels will be empty for them. Install sysstat to fix."
        )


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
                   perf_freq: int, elbencho_args: str, dry_run: bool = False,
                   live_metrics_port: Optional[int] = None) -> Path:
    """Full `benchprof run` sequence. Returns the local run directory.

    If live_metrics_port is given, every client runs benchprof-exporter on
    that port (CPU/Mem/process/link gauges) and the controller serves
    elbencho throughput/IOPS gauges on live_metrics_port + 1, so an existing
    Prometheus server can scrape all of them while the run is in progress
    instead of only after `benchprof report` parses the finished files.
    """
    run_id = make_run_id()
    run_dir = setup_run_dir(config, run_id)

    agents_started = False
    controller_exporter = None
    try:
        push_agent(config, hosts, dry_run)
        start_agents(config, hosts, profile, perf_freq, dry_run, export_port=live_metrics_port)
        agents_started = True

        if live_metrics_port is not None and not dry_run:
            from benchprof.live_exporter import ControllerExporter
            controller_exporter = ControllerExporter(run_dir, live_metrics_port + 1)
            controller_exporter.start()
            print(
                f"Live metrics: clients on port {live_metrics_port}, "
                f"controller (elbencho throughput) on port {live_metrics_port + 1}"
            )

        print("Starting elbencho service on clients...")
        start_elbencho_service(config, hosts, dry_run)

        print(f"Settling {SETTLE_BEFORE_S}s before elbencho so panels show an idle baseline...")
        if not dry_run:
            time.sleep(SETTLE_BEFORE_S)

        elbencho_args = _ensure_mkdirs(elbencho_args)

        t0 = time.time()
        elbencho_cmd = (
            f"{config.elbencho_path} --hostsfile {config.machine_file} "
            f"--livecsv {run_dir}/elbencho_live.csv --livecsvex --liveint 1000 "
            # --live1n: one line per --liveint update, written to stderr and
            # never overwritten in place (elbencho's default full-screen live
            # display redraws via ANSI escapes, which can look like "nothing is
            # happening" depending on the terminal/session) - this gives clear,
            # scrollable, loggable console progress alongside the CSV.
            f"--live1n {elbencho_args}"
        )
        print(f"Running: {elbencho_cmd}")
        try:
            run_cmd(elbencho_cmd, dry_run=dry_run)
        finally:
            print("Stopping elbencho service on clients...")
            stop_elbencho_service(config, hosts, dry_run)

        print(f"Settling {SETTLE_AFTER_S}s after elbencho so panels show an idle tail...")
        if not dry_run:
            time.sleep(SETTLE_AFTER_S)

        if controller_exporter is not None:
            controller_exporter.stop()
            controller_exporter = None

        stop_agents(config, hosts, keep_raw=False, dry_run=dry_run)
        gather(config, hosts, run_dir, dry_run)
        warn_missing_sar(run_dir, hosts)
        write_meta(run_dir, run_id, label, hosts, profile, t0, elbencho_cmd)

        if profile == "full" and not dry_run:
            from benchprof.flame import build_phase_flames
            build_phase_flames(run_dir)

        print(f"Run complete: {run_dir}")
        return run_dir
    except (Exception, KeyboardInterrupt):
        # Ctrl-C or any failure partway through: make sure nothing is left
        # running on the clients (elbencho --service daemon, sadc/pidstat/
        # perf/links/lctl loops) rather than leaking background processes
        # that pile up across retries and block future runs.
        print("Run interrupted/failed - cleaning up remote processes...")
        if controller_exporter is not None:
            try:
                controller_exporter.stop()
            except Exception as cleanup_err:
                print(f"  warning: failed to stop controller exporter: {cleanup_err}")
        try:
            stop_elbencho_service(config, hosts, dry_run)
        except Exception as cleanup_err:
            print(f"  warning: failed to stop elbencho service: {cleanup_err}")
        if agents_started:
            try:
                abort_agents(config, hosts, dry_run)
            except Exception as cleanup_err:
                print(f"  warning: failed to abort agents: {cleanup_err}")
        raise


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
