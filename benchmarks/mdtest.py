import time
from pathlib import Path
import subprocess

from utils.hosts import resolve_hosts
from utils.shell import run_cmd

class MDTestBenchmark:
    def __init__(self, params, mpirun_path, mpi_conf, mdtest_path, data_path_root, log_path, runid_base, fname, machinefile, dry_run=False):
        self.params = params
        self.mpirun_path = mpirun_path
        self.mpi_conf = mpi_conf
        self.mdtest_path = mdtest_path
        self.data_path_root = data_path_root
        self.log_path = log_path
        self.runid_base = runid_base
        self.fname = fname
        self.machinefile = machinefile
        self.dry_run = dry_run

    def _cmd_builder(self) -> str:

        ## CLIENTS TO USE
        client_count = int(self.params["clients"])
        hosts, _ = resolve_hosts(self.machinefile, client_count)
        hosts_var = f"--host {','.join(hosts)}"

        total_ppn = self.params["ppn"] * self.params["clients"]
        cmd = f"{self.mpirun_path} {hosts_var} {self.mpi_conf} --np {total_ppn} {self.mdtest_path} "

        ### check for flags to add
        if self.params.get("objects"): cmd += f' -n {self.params["objects"]}'
        if self.params.get("branching"): cmd += f' -b {self.params["branching"]}'
        if self.params.get("depth"): cmd += f' -z {self.params["depth"]}'
        if self.params.get("bytesread"): cmd += f' -e {self.params["bytesread"]}'
        if self.params.get("byteswrite"): cmd += f' -w {self.params["byteswrite"]}'
        if self.params.get("iterations"): cmd += f' -i {self.params["iterations"]}'
        if self.params.get("itemsperdir"): cmd += f' -I {self.params["itemsperdir"]}'
        if self.params.get("directio"): cmd += f' --posix.odirect'
        if self.params.get("onlycreate"): cmd += f' -C'
        if self.params.get("onlystat"): cmd += f' -T'
        if self.params.get("onlyread"): cmd += f' -E'
        if self.params.get("onlyremove"): cmd += f' -r'
        if self.params.get("dironly"): cmd += f' -D'
        if self.params.get("fileonly"): cmd += f' -F'
        if self.params.get("uniquedir"): cmd += f' -u'
        if self.params.get("verbose"): cmd += f' {self.params["verbose"]}'
        if self.params.get("syncafterwrite"): cmd += f' -y'
        if self.params.get("extra_args"): cmd += f' {self.params["extra_args"]}'



        return cmd

    def start(self):
        
        cmd = self._cmd_builder()
        data_path = Path(f"{self.data_path_root}/mdtest")
        if self.dry_run:
            print(f"[DRY-RUN] mkdir -p {data_path}")
        else:
            data_path.mkdir(parents=True, exist_ok=True)
        if self.params["pools"] != "default":   
            pool_stripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
            pool_overstripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

            set_stripe_process = run_cmd(pool_stripe_cmd, dry_run=self.dry_run)

            if set_stripe_process.returncode != 0:
                set_overstripe_process = run_cmd(pool_stripe_cmd, dry_run=self.dry_run)

            if self.params['DOM']:
                set_dom_cmd = f"lfs setstripe -E 64k -L mdt {data_path}"
                set_dom_process = run_cmd(set_dom_cmd, dry_run=self.dry_run)
        else:

            stripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
            overstripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

            set_stripe_process = run_cmd(stripe_cmd, dry_run=self.dry_run)

            if set_stripe_process.returncode != 0:
                set_overstripe_process = run_cmd(stripe_cmd, dry_run=self.dry_run)

            if self.params['DOM']:
                set_dom_cmd = f"lfs setstripe -E 64k -L mdt {data_path}"
                set_dom_process = run_cmd(set_dom_cmd, dry_run=self.dry_run)

        cmd += f" -d {data_path}"

        if self.dry_run:
            run_cmd(cmd, dry_run=True)
        else:
            with open(f"{self.log_path}/mdtest/{self.runid_base}/{self.fname}", "w") as log_file:
                process = run_cmd(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=log_file
                )


    def stop(self):
        print("stopping BM")
