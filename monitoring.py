

import shutil
import subprocess
from pathlib import Path

from utils.shell import run_cmd, popen_cmd

pdstat_script = "/work/scripts/SFA/pdstats_v11.py3"
vdstat_script = "/work/scripts/SFA/vdstats_v12.py3"
iostat_script = "/work/scripts/mark/iostats_aggregator_generic2025.sh"

# Vendored copy of Brendan Gregg's FlameGraph scripts (flamegraph.pl,
# stackcollapse-perf.pl) so .svg generation for --perf-profile always works
# out of the box, with no PATH setup required. Falls back to PATH first in
# case a newer/local install is preferred.
FLAMEGRAPH_DIR = Path(__file__).parent / "utils" / "FlameGraph"

class SFAMonitoring:

    def __init__(self, appliances: dict, log_path, runid_base, tool, fname, dry_run=False):
        self.appliances = appliances
        self.log_path = log_path
        self.runid_base = runid_base
        self.tool = tool
        self.fname = fname
        self.dry_run = dry_run
        self.processes = {}

    def start(self):

        # Start the monitoring scripts for each SFA and VMs involved
        if self.processes:
            print("Old Monitoring is still running. Clean up and try again!")
            return

        for appliance, vm in self.appliances.items():
            for stat in ("vdstats", "pdstats", "iostats"):
                if stat == "vdstats":
                    script = f"{vdstat_script} {appliance}-c1"
                    host = f"{appliance}-c1"
                elif stat == "pdstats":
                    script = f"{pdstat_script} {appliance}-c0"
                    host = f"{appliance}-c0"
                elif stat == "iostats":
                    script = f'{iostat_script} "{vm}"'
                    host = vm

                if self.dry_run:
                    popen_cmd(script, dry_run=True)
                    continue

                stat_log_file = open(f"{self.log_path}/{self.tool}/{self.runid_base}/{self.fname}.{stat}.{appliance}", "w")

                stat_process = popen_cmd(
                    script,
                    stdin=subprocess.DEVNULL,
                    stdout=stat_log_file,
                    stderr=stat_log_file
                )
                self.processes[f"{appliance}_{stat}"] = {
                    "process": stat_process,
                    "log_file": stat_log_file
                }


    def stop(self):
        if self.dry_run:
            run_cmd("killall pdstats_v11.py3 vdstats_v12.py3", dry_run=True)
            return

        if not self.processes:
            print("No monitors running.")
            return

        stop_cmd = "killall pdstats_v11.py3 vdstats_v12.py3"
        self.kill_process = run_cmd(stop_cmd)

        for key, data in self.processes.items():
            data["process"].terminate()
            data["process"].wait()
            data["log_file"].close()                     # important — flush and close the file
        self.processes = {}


# sysstat's `sar` samples counters already maintained by the kernel in
# /proc (stat, meminfo, net/dev, etc.), so the per-sample overhead is
# negligible (well under 1% CPU) even at a few-second interval - safe to
# leave running for the full duration of a test.
#
#   -u -P ALL   per-core AND overall %user/%system/%iowait/%idle - high
#               %user/%system (esp. with checksums/encryption workloads)
#               points at client CPU being the bottleneck; high %iowait
#               means the client is stalled waiting on the network/storage
#               instead, i.e. NOT CPU-bound.
#   -q          run queue length / load average - shows how oversubscribed
#               the CPUs are relative to the number of client processes (ppn).
#   -r          memory utilization (free/used/buffers/cache) - flags memory
#               pressure or swapping that could be slowing the client down.
#   -n DEV      per-interface network throughput - useful for spotting a
#               saturated NIC/RoCE link as the real bottleneck instead of CPU.
CLIENT_STATS_CMD = "sar -u -P ALL -q -r -n DEV {interval}"


class ClientMonitoring:
    """
    Collects lightweight, continuous client-side resource stats (CPU, load,
    memory, network) across all hosts in a machinefile via `clush` + `sar`,
    so slow runs can be diagnosed as client-CPU-bound, network-bound, etc.
    rather than only ever looking at server-side (SFA) stats.

    Opt-in: intended to be started/stopped around each test the same way
    SFAMonitoring is, but only when explicitly enabled (e.g. a --client-stats
    CLI flag), since it adds extra ssh fan-out traffic to every client.
    """

    def __init__(self, machinefile, log_path, runid_base, tool, fname, interval=5, dry_run=False):
        self.machinefile = machinefile
        self.log_path = log_path
        self.runid_base = runid_base
        self.tool = tool
        self.fname = fname
        self.interval = interval
        self.dry_run = dry_run
        self.process = None
        self.log_file = None

    def start(self):
        if self.process is not None:
            print("Old client stats monitoring is still running. Clean up and try again!")
            return

        sar_cmd = CLIENT_STATS_CMD.format(interval=self.interval)
        script = f'clush --machinefile {self.machinefile} "{sar_cmd}"'

        if self.dry_run:
            popen_cmd(script, dry_run=True)
            return

        log_file_path = f"{self.log_path}/{self.tool}/{self.runid_base}/{self.fname}.clientstats"
        self.log_file = open(log_file_path, "w")

        self.process = popen_cmd(
            script,
            stdin=subprocess.DEVNULL,
            stdout=self.log_file,
            stderr=self.log_file
        )

    def stop(self):
        if self.dry_run:
            run_cmd(f'clush --machinefile {self.machinefile} "pkill -f \'sar -u -P ALL\'"', dry_run=True)
            return

        if self.process is None:
            print("No client stats monitor running.")
            return

        self.process.terminate()
        self.process.wait()
        self.log_file.close()                            # important — flush and close the file

        # sar runs with no sample count, so it loops until killed; make sure
        # it's actually gone on every client even if terminating the local
        # clush/ssh fan-out process didn't cleanly tear down the remote sar.
        run_cmd(f'clush --machinefile {self.machinefile} "pkill -f \'sar -u -P ALL\'"')

        self.process = None
        self.log_file = None


# `perf record -a -g` profiles every process on the box system-wide (so it
# captures the benchmark client process plus anything the kernel is doing on
# its behalf, e.g. network/storage I/O paths) with call-graph (-g) info
# needed to build a flamegraph. It is opt-in and intended for one job at a
# time (not every test in a sweep) since the resulting perf.data/perf.script
# files can get large and the call-graph unwinding adds measurable overhead,
# unlike the lightweight sar-based ClientMonitoring above.
PERF_RECORD_CMD = "sudo perf record -F {frequency} -a -g -o {data_path}"

# Best-guess process/command name to isolate when narrowing a flamegraph
# down to "just the benchmark", keyed by tool name. Can be overridden per
# run via PerfProfiling(process_match=...) / --perf-profile-process, since
# e.g. mlperf's actual client process name depends on the training script.
PERF_TOOL_PROCESS_MAP = {
    "ior": "ior",
    "mdtest": "mdtest",
    "fio": "fio",
    "fio_parallel": "fio",
    "elbencho": "elbencho",
    "io500": "io500",
    "mlperf": "mlpstorage",
}


class PerfProfiling:
    """
    Runs `perf record -a -g` (system-wide, with call graphs) across every
    host in a machinefile for the duration of a single test, then converts
    the result to `perf script` output (the standard input format for
    Brendan Gregg's FlameGraph scripts:
    https://github.com/brendangregg/FlameGraph) and copies it back to the
    local run's log directory, one file per client host.

    The capture is always system-wide (so a "what else on the box is
    slowing this down?" full-system flamegraph is always possible), but
    which flamegraph(s) get generated from it is controlled by `scope`:
      - "system":  one flamegraph of every process/thread on the host.
      - "process": one flamegraph filtered down to only stacks whose comm
                    name matches `process_match` (the benchmark itself).
      - "both":    generate both of the above (default).

    If `stackcollapse-perf.pl` and `flamegraph.pl` are found on PATH, the
    .svg flamegraph(s) are generated locally per host as a convenience;
    otherwise the raw `.perf.script.<host>` files (and, for "process"/
    "both", the filtered `.perf.script.<host>.<process>` files) are left
    for you to run through your own copy of those scripts.
    """

    def __init__(self, machinefile, log_path, runid_base, tool, fname, frequency=99,
                 scope="both", process_match=None, dry_run=False):
        self.machinefile = machinefile
        self.log_path = log_path
        self.runid_base = runid_base
        self.tool = tool
        self.fname = fname
        self.frequency = frequency
        self.scope = scope
        self.process_match = process_match or PERF_TOOL_PROCESS_MAP.get(tool, tool)
        self.dry_run = dry_run
        self.process = None
        self.remote_data_path = f"/tmp/{self.fname}.perf.data"
        self.remote_script_path = f"/tmp/{self.fname}.perf.script"

    def start(self):
        if self.process is not None:
            print("Old perf profiling is still running. Clean up and try again!")
            return

        perf_cmd = PERF_RECORD_CMD.format(frequency=self.frequency, data_path=self.remote_data_path)
        script = f'clush --machinefile {self.machinefile} "{perf_cmd}"'

        if self.dry_run:
            popen_cmd(script, dry_run=True)
            return

        print("Starting perf profiling on clients...")
        self.process = popen_cmd(
            script,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    def stop(self):
        dest_dir = f"{self.log_path}/{self.tool}/{self.runid_base}"

        if self.dry_run:
            run_cmd(f'clush --machinefile {self.machinefile} "sudo pkill -INT -f \'perf record -F\'"', dry_run=True)
            run_cmd(
                f'clush --machinefile {self.machinefile} '
                f'"sudo perf script -i {self.remote_data_path} > {self.remote_script_path}"',
                dry_run=True
            )
            run_cmd(f'clush --machinefile {self.machinefile} "sudo chmod 644 {self.remote_script_path}"', dry_run=True)
            run_cmd(f'clush --machinefile {self.machinefile} "test -s {self.remote_script_path}"', dry_run=True)
            run_cmd(f"clush --machinefile {self.machinefile} --rcopy {self.remote_script_path} --dest {dest_dir}", dry_run=True)
            run_cmd(f'clush --machinefile {self.machinefile} "sudo rm -f {self.remote_data_path} {self.remote_script_path}"', dry_run=True)
            return

        if self.process is None:
            print("No perf profiling running.")
            return

        # `perf record` needs SIGINT (not a plain kill) to flush and close
        # out the perf.data file cleanly - a hard kill can leave it corrupt.
        # It (and perf.data) runs as root via sudo (perf_event_paranoid
        # blocks unprivileged system-wide profiling), so sudo is needed here
        # too.
        run_cmd(f'clush --machinefile {self.machinefile} "sudo pkill -INT -f \'perf record -F\'"')
        self.process.wait()
        self.process = None

        # Fail loudly here instead of letting a missing perf.data (e.g. if
        # `perf record` never started due to a permissions error) propagate
        # into a confusing "failed to open perf.data" error from `perf
        # script` below.
        check = run_cmd(
            f'clush --machinefile {self.machinefile} "test -s {self.remote_data_path}"',
            check=False
        )
        if check.returncode != 0:
            print(
                f"Error: {self.remote_data_path} is missing or empty on one or more clients - "
                "perf record likely failed to start (check sudo/perf permissions on the client). "
                "Skipping perf script conversion."
            )
            return

        print("Converting perf.data to perf script output on clients...")
        # Don't swallow stderr here (as before) - if `perf script` fails
        # (e.g. kernel symbol/build-id issues) it would otherwise silently
        # leave an empty .perf.script file, which only surfaces later as a
        # confusing "Stack count is low (0)" error from flamegraph.pl.
        run_cmd(
            f'clush --machinefile {self.machinefile} '
            f'"sudo perf script -i {self.remote_data_path} > {self.remote_script_path}"'
        )

        # perf script's output is owned by root (sudo); make it readable so
        # the non-root clush --rcopy below can read and copy it back.
        run_cmd(f'clush --machinefile {self.machinefile} "sudo chmod 644 {self.remote_script_path}"')

        # Fail loudly if the conversion produced an empty file on any
        # client, instead of copying it back and only discovering the
        # problem when flamegraph.pl complains about zero stack counts.
        check = run_cmd(
            f'clush --machinefile {self.machinefile} "test -s {self.remote_script_path}"',
            check=False
        )
        if check.returncode != 0:
            print(
                f"Error: {self.remote_script_path} is missing or empty on one or more clients - "
                "`perf script` conversion likely failed (see any error output above). "
                f"Raw {self.remote_data_path} is left on the client(s) for manual inspection."
            )
            return

        print(f"Copying perf script output back to {dest_dir}...")
        run_cmd(f"clush --machinefile {self.machinefile} --rcopy {self.remote_script_path} --dest {dest_dir}")

        # Clean up the (potentially large) remote files now that they've
        # been copied back locally.
        run_cmd(f'clush --machinefile {self.machinefile} "sudo rm -f {self.remote_data_path} {self.remote_script_path}"')

        self._generate_flamegraphs(dest_dir)

    def _generate_flamegraphs(self, dest_dir):
        """Best-effort: if Brendan Gregg's FlameGraph scripts are installed
        and on PATH, fold + render .svg(s) per host automatically (which
        one(s) depends on `self.scope`). If the scripts aren't found, the
        raw .perf.script.<host> files are left in dest_dir for you to run
        through your own copy of those scripts."""
        stackcollapse = shutil.which("stackcollapse-perf.pl") or self._vendored_flamegraph_script("stackcollapse-perf.pl")
        flamegraph = shutil.which("flamegraph.pl") or self._vendored_flamegraph_script("flamegraph.pl")
        if not stackcollapse or not flamegraph:
            print(
                "Note: stackcollapse-perf.pl/flamegraph.pl not found on PATH or in "
                f"{FLAMEGRAPH_DIR}, skipping automatic .svg generation. "
                f"Raw perf script output is in {dest_dir}."
            )
            return

        for script_file in Path(dest_dir).glob(f"{self.fname}.perf.script.*"):
            if self.scope in ("system", "both"):
                svg_file = script_file.with_name(script_file.name + ".full.svg")
                run_cmd(f'{stackcollapse} "{script_file}" | {flamegraph} --title "Full system" > "{svg_file}"')
                print(f"Generated full-system flamegraph: {svg_file}")

            if self.scope in ("process", "both"):
                filtered_file = self._filter_script_by_process(script_file)
                if filtered_file is None:
                    print(
                        f"Note: no stacks matching process '{self.process_match}' found in "
                        f"{script_file}, skipping narrowed flamegraph for this host."
                    )
                    continue
                svg_file = script_file.with_name(script_file.name + f".{self.process_match}.svg")
                run_cmd(f'{stackcollapse} "{filtered_file}" | {flamegraph} --title "{self.process_match} only" > "{svg_file}"')
                print(f"Generated {self.process_match}-only flamegraph: {svg_file}")

    @staticmethod
    def _vendored_flamegraph_script(script_name):
        """Fallback to the copy of this script vendored under
        utils/FlameGraph/ when it isn't found on PATH."""
        script_path = FLAMEGRAPH_DIR / script_name
        return str(script_path) if script_path.is_file() else None

    def _filter_script_by_process(self, script_file):
        """Narrow a `perf script` output file down to only the stack
        samples whose comm (process/thread name) matches `self.process_match`
        (case-insensitive substring), so the resulting flamegraph shows
        only where the benchmark itself is spending time instead of every
        process on the host. Returns the path to the filtered file, or
        None if no matching samples were found.

        `perf script` output is a series of blocks separated by blank
        lines; each block's first line starts with the comm name followed
        by "pid/tid", e.g. "fio 12345/12346 1234.567891: ...".
        """
        process_match = self.process_match.lower()
        filtered_file = script_file.with_name(script_file.name + f".{self.process_match}")

        matched_any = False
        with open(script_file) as src, open(filtered_file, "w") as dst:
            current_block = []
            keep_block = False

            def flush_block():
                nonlocal matched_any
                if keep_block and current_block:
                    dst.writelines(current_block)
                    dst.write("\n")
                    matched_any = True

            for line in src:
                if line.strip() == "":
                    flush_block()
                    current_block = []
                    keep_block = False
                    continue
                if not current_block:
                    comm = line.split()[0] if line.split() else ""
                    keep_block = process_match in comm.lower()
                current_block.append(line)
            flush_block()

        if not matched_any:
            filtered_file.unlink(missing_ok=True)
            return None
        return filtered_file
