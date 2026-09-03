

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
