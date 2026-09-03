import time
import os
import shlex
import subprocess
import threading
from itertools import product
from pathlib import Path

from utils.hosts import resolve_hosts
from utils.shell import run_cmd

class FIOBenchmark:
    def __init__(self, params, fio_path, data_path_root, log_path, runid_base, fname, machinefile, deletefiles, dry_run=False, manage_server=True):
        self.params = params
        self.fio_path = fio_path
        self.data_path_root = data_path_root
        self.log_path = log_path
        self.runid_base = runid_base
        self.fname = fname
        self.machinefile = machinefile
        self.deletefiles = deletefiles
        self.dry_run = dry_run
        # When run as part of FIOParallelBenchmark, the caller manages the
        # fio --server daemon(s) itself so two concurrent jobs sharing the
        # same clients don't start/kill each other's server.
        self.manage_server = manage_server
        self.fio_job_path = Path("./benchmarks/fio_jobs")

    def _start_fio_server(self):
        start_server_cmd = f"clush --machinefile {self.machinefile} {self.fio_path} --server --daemonize=/tmp/fio.pid"
        start_server_process = run_cmd(start_server_cmd, dry_run=self.dry_run)


    def _stop_fio_server(self):
        stop_server_cmd = f"clush  --machinefile {self.machinefile} killall fio"
        stop_server_process = run_cmd(stop_server_cmd, dry_run=self.dry_run)


    def run(self):

        if self.manage_server:
            self._start_fio_server()
        try:

            ### Build out needed params

            ## DATA POOLS
            if self.params["pools"] != "default":
                data_path = Path(f"{self.data_path_root}/fio/{self.params['pools']}")
                if self.dry_run:
                    print(f"[DRY-RUN] mkdir -p {data_path}")
                else:
                    data_path.mkdir(parents=True, exist_ok=True)
                pool_stripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
                pool_overstripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

                set_stripe_process = run_cmd(pool_stripe_cmd, dry_run=self.dry_run)

                if set_stripe_process.returncode != 0:
                    set_overstripe_process = run_cmd(pool_stripe_cmd, dry_run=self.dry_run)

            else:
                data_path = Path(f"{self.data_path_root}/fio")
                if self.dry_run:
                    print(f"[DRY-RUN] mkdir -p {data_path}")
                else:
                    data_path.mkdir(parents=True, exist_ok=True)
                stripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
                overstripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

                set_stripe_process = run_cmd(stripe_cmd, dry_run=self.dry_run)

                if set_stripe_process.returncode != 0:
                    set_overstripe_process = run_cmd(stripe_cmd, dry_run=self.dry_run)

            ## CLIENTS TO USE
            client_count = int(self.params["clients"])
            hosts, total_available = resolve_hosts(self.machinefile, client_count)

            if client_count == total_available:
                # use the whole machinefile as-is
                hosts_var = f"--client {self.machinefile}"
            else:
                # slice to the needed count via process substitution
                hosts_var = f"--client <(head -{client_count} {self.machinefile})"

            extra_args = self.params.get("extra_args", "") or ""

            cmd = (
                f'DIRECTIO={self.params["directio"]} IOENGINE={self.params["ioengine"]} '
                f'READWRITE={self.params["operation"]} BLOCKSIZE={self.params["blocksize"]} '
                f'IODEPTH={self.params["iodepth"]} FILESIZE={self.params["filesize"]} '
                f'NUMJOBS={self.params["ppn"]} DIRECTORY={data_path} '
                f'EXTRA_ARGS={shlex.quote(extra_args)} '
            )

            if self.params["operation"] in ("rw", "randrw"):
                cmd += f' RWMIXREAD={self.params["rwmixread"]}'
            elif self.params["operation"] in ("write", "randwrite"):
                cmd += f' RWMIXREAD=0'
            elif self.params["operation"] in ("read", "randread"):
                cmd += f' RWMIXREAD=100'

            if self.params["timebased"]:
                job_file = f"{self.fio_job_path}/fio_timebased.job"
                cmd += f' RUNTIME={self.params["runtime"]}s '

            else :
                job_file = f"{self.fio_job_path}/fio.job"

            if self.params["operation"] in ("write", "randwrite"):
                layout_cmd = (
                    f'IOENGINE={self.params["ioengine"]} '
                    f'IODEPTH={self.params["iodepth"]} FILESIZE={self.params["filesize"]} '
                    f'NUMJOBS={self.params["ppn"]} DIRECTORY={data_path} '
                    f' {self.fio_path} {hosts_var} {self.fio_job_path}/fio_create.job'
                )

                if self.dry_run:
                    layout_process = run_cmd(layout_cmd, dry_run=True)
                else:
                    with open(f"{self.log_path}/fio/{self.runid_base}/{self.fname}", "w") as log_file:
                        layout_process = run_cmd(
                            layout_cmd,
                            stdin=subprocess.DEVNULL,
                            stdout=log_file,
                            stderr=log_file
                        )

            cmd += f' {self.fio_path} {hosts_var} {job_file}'

            if self.dry_run:
                run_cmd(cmd, dry_run=True)
            else:
                with open(f"{self.log_path}/fio/{self.runid_base}/{self.fname}", "w") as log_file:
                    process = run_cmd(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=log_file
                    )

                with open(f"{self.log_path}/fio/{self.runid_base}/{self.fname}", "r") as f:
                    for line in f:
                        line = line.strip()

                        # main BW/IOPS summary line
                        # "  write: IOPS=8756, BW=8757Mi" or "  read: IOPS=12.5k, BW=12.2Gi"
                        if line.startswith("read:") or line.startswith("write:"):
                            # split on space and comma to get the parts
                            parts = line.replace(",", " ").split()
                            operation = parts[0].rstrip(":")
                            iops = parts[1].split("=")[1]
                            bw   = parts[2].split("=")[1]
                            print(f"       {operation} -- IOPS: {iops}  BW: {bw}")

                        # aggregate bw line with avg
                        # "bw (  MiB/s): min=  512, max=30108, per=100.00%, avg=12615.26"
                        elif line.startswith("bw"):
                            parts = line.replace(",", " ").split()
                            avg_bw = [p.split("=")[1] for p in parts if p.startswith("avg=")][0]
                            print(f"       Avg BW: {avg_bw} MiB/s")

                        # aggregate iops line with avg
                        # "iops        : min=  512, max=30102, avg=12614.85"
                        elif line.startswith("iops"):
                            parts = line.replace(",", " ").split()
                            avg_iops = [p.split("=")[1] for p in parts if p.startswith("avg=")][0]
                            print(f"       Avg IOPS: {avg_iops}")

            if self.params["operation"] in ("read", "randread"):
                ## Delete previous run data before running write if 'deletefiles' is true
                if self.deletefiles:
                    print("Cleaning up datapath from previous runs...")
                    deletefiles_cmd = f"rm -rf {data_path}/*"
                    deletefiles_process = run_cmd(deletefiles_cmd, dry_run=self.dry_run)

        finally:
            if self.manage_server:
                self._stop_fio_server()
