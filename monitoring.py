

import subprocess

from utils.shell import run_cmd, popen_cmd

pdstat_script = "/work/scripts/SFA/pdstats_v11.py3"
vdstat_script = "/work/scripts/SFA/vdstats_v12.py3"
iostat_script = "/work/scripts/mark/iostats_aggregator_generic2025.sh"

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
