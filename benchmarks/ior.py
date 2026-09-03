import time
import os
import subprocess
import threading
from itertools import product
from pathlib import Path

from utils.hosts import resolve_hosts
from utils.shell import run_cmd

class IORBenchmark:
    def __init__(self, params, mpirun_path, mpi_conf, ior_path, data_path_root, log_path, runid_base, fname, machinefile, deletefiles, dry_run=False):
        self.params = params
        self.mpirun_path = mpirun_path
        self.mpi_conf = mpi_conf
        self.ior_path = ior_path
        self.data_path_root = data_path_root
        self.log_path = log_path
        self.runid_base = runid_base
        self.fname = fname
        self.machinefile = machinefile
        self.deletefiles = deletefiles
        self.dry_run = dry_run


    def start(self):


        ### Build out needed params

        total_ppn = self.params["ppn"] * self.params["clients"]

        ## DATA POOLS
        if self.params["pools"] != "default":
            data_path = Path(f"{self.data_path_root}/ior/{self.params['pools']}")
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
            data_path = Path(f"{self.data_path_root}/ior")
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
        hosts, _ = resolve_hosts(self.machinefile, client_count)
        hosts_var = f"--host {','.join(hosts)}"

        run_options = ""
        ## OPERATION
        if self.params["operation"] == "write":
            run_options += " -w "
        elif self.params["operation"] == "read":
            run_options += " -r "

        ## KEEP FILES
        if self.params["keep_files"] == 1:
            run_options += " -k "

        ## FILE PER PROC
        if self.params["fileperproc"] == 1:
            run_options += " -F "

        ## DIRECT IO
        if self.params["directio"] == 1:
            run_options += " --posix.odirect "

        ## RANDOM
        if self.params["randomoffset"] == 1:
            if self.params["operation"] == "read" and self.params["file_layout"]:
                ### Run file layout

                ## Delete previous run data before running write if 'deletefiles' is true
                if self.deletefiles:
                    print("Cleaning up datapath from previous runs...")
                    deletefiles_cmd = f"rm -rf {data_path}/*"
                    deletefiles_process = run_cmd(deletefiles_cmd, dry_run=self.dry_run)

                parts = run_options.split()
                parts = ["-w" if p == "-r" else p for p in parts]
                layout_run_options = " " + " ".join(parts) + " "

                layout_cmd = (
                    f'{self.mpirun_path} {hosts_var} '
                    f'{self.mpi_conf} --np {total_ppn} '
                    f'{self.ior_path} -a {self.params["api"]} -v -d 1 '
                    f'-b {self.params["blocksize"]} -t {self.params["xfersize"]} '
                    f'{layout_run_options} {self.params["extra_args"]} -o {data_path}/f'

                )
                print(layout_cmd)

                if self.dry_run:
                    layout_process = run_cmd(layout_cmd, dry_run=True)
                else:
                    with open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}.layout", "w") as layout_log_file:
                        layout_process = run_cmd(
                            layout_cmd,
                            stdin=subprocess.DEVNULL,
                            stdout=layout_log_file,
                            stderr=layout_log_file
                        )

                ### Once layout is finished add the random flag back in to run_options
                if layout_process.returncode == 0:
                    run_options += " -z "
                else:
                    print("There was an issue with the file layout!")
            else:
                run_options += " -z "

        cmd = (
            f'{self.mpirun_path} {hosts_var} '
            f'{self.mpi_conf} --np {total_ppn} '
            f'{self.ior_path} -a {self.params["api"]} -v -d 1 '
            f'-b {self.params["blocksize"]} -t {self.params["xfersize"]} '
            f'{run_options} {self.params["extra_args"]} -o {data_path}/f'

        )

        if self.dry_run:
            run_cmd(cmd, dry_run=True)
        else:
            with open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}", "w") as log_file:
                process = run_cmd(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=log_file
                )

            with open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}", "r") as f:
                for line in f:
                    # equivalent to grep
                    if "Max Write" in line or "Max Read" in line:
                        # equivalent to awk - split on whitespace and grab columns
                        cols = line.split()
                        print(f"       {cols[0]} {cols[1]} {cols[2]} {cols[3]}")

        if self.params["operation"] == "read":
            ## Delete previous run data before running write if 'deletefiles' is true
            if self.deletefiles:
                print("Cleaning up datapath from previous runs...")
                deletefiles_cmd = f"rm -rf {data_path}/*"
                deletefiles_process = run_cmd(deletefiles_cmd, dry_run=self.dry_run)


    def stop(self):
        print("stopping BM")