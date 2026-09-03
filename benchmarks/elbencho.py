import subprocess
from pathlib import Path

from utils.hosts import resolve_hosts
from utils.shell import run_cmd

class ElbenchoBenchmark:
    def __init__(self, params, elbencho_path, data_path_root, log_path, runid_base, fname, machinefile, deletefiles, dry_run=False, manage_service=True):
        self.params = params
        self.elbencho_path = elbencho_path
        self.data_path_root = data_path_root
        self.log_path = log_path
        self.runid_base = runid_base
        self.fname = fname
        self.machinefile = machinefile
        self.deletefiles = deletefiles
        self.dry_run = dry_run
        self.manage_service = manage_service

    def _start_elbencho_service(self):
        start_service_cmd = f"clush --machinefile {self.machinefile} {self.elbencho_path} --service"
        start_service_process = run_cmd(start_service_cmd, dry_run=self.dry_run)


    def _stop_elbencho_service(self):
        stop_service_cmd = f"clush --machinefile {self.machinefile} killall elbencho"
        stop_service_process = run_cmd(stop_service_cmd, dry_run=self.dry_run)


    def _hosts_var(self) -> str:
        client_count = int(self.params["clients"])
        hosts, total_available = resolve_hosts(self.machinefile, client_count)

        if client_count == total_available:
            # use the whole machinefile as-is
            return f"--hostsfile {self.machinefile}"
        # slice to the needed count via process substitution
        return f"--hostsfile <(head -{client_count} {self.machinefile})"

    def _cmd_builder(self, data_path: Path, operation: str = None, random: bool = None, runtime=None) -> str:
        operation = operation or self.params["operation"]
        # Default to random access to preserve prior behavior for configs
        # that don't set "random" explicitly.
        random = self.params.get("random", 1) if random is None else random
        # Layout passes need to fully write the file, so callers building a
        # layout command pass runtime=0 to override any --timelimit that
        # applies to the (timebased) actual test.
        runtime = self.params.get("runtime") if runtime is None else runtime

        if operation == "write":
            op_flag = "--write"
        elif operation == "read":
            op_flag = "--read"
        else:
            raise ValueError(f"Unknown elbencho operation '{operation}' (expected 'write' or 'read').")

        if random:
            op_flag += " --rand"

        hosts_var = self._hosts_var()

        cmd = (
            f'{self.elbencho_path} --thread {self.params["ppn"]} {hosts_var} '
            f'{op_flag} --size {self.params["filesize"]} --files {self.params["files"]} '
            f'--iodepth {self.params["iodepth"]} --block {self.params["blocksize"]} '
            f'--blockvaralgo {self.params["blockvaralgo"]} --blockvarpct {self.params["blockvarpct"]}'
        )

        if self.params.get("dirs"):
            cmd += f' --dirs {self.params["dirs"]}'

        if self.params.get("directio"):
            cmd += " --direct"

        if runtime:
            cmd += f' --timelimit {runtime}'

        if self.params.get("sync"):
            cmd += " --sync"

        if self.params.get("dropcache"):
            cmd += " --dropcache"

        if self.params.get("latency"):
            cmd += " --lat"

        if self.params.get("nolive"):
            cmd += " --nolive"

        if self.params.get("randalgo"):
            cmd += f' --randalgo {self.params["randalgo"]}'

        if self.params.get("verify"):
            cmd += f' --verify {self.params["verify"]}'

        if self.params.get("extra_args"):
            cmd += f' {self.params["extra_args"]}'

        cmd += f" {data_path}"

        return cmd

    def run(self):

        if self.manage_service:
            self._start_elbencho_service()
        try:

            ## DATA POOLS
            if self.params["pools"] != "default":
                data_path = Path(f"{self.data_path_root}/elbencho/{self.params['pools']}")
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
                data_path = Path(f"{self.data_path_root}/elbencho")
                if self.dry_run:
                    print(f"[DRY-RUN] mkdir -p {data_path}")
                else:
                    data_path.mkdir(parents=True, exist_ok=True)
                stripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
                overstripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

                set_stripe_process = run_cmd(stripe_cmd, dry_run=self.dry_run)

                if set_stripe_process.returncode != 0:
                    set_overstripe_process = run_cmd(stripe_cmd, dry_run=self.dry_run)

            is_random = bool(self.params.get("random", 1))
            needs_layout = (
                self.params["operation"] == "read"
                and is_random
                and self.params.get("file_layout")
            )

            ## Delete previous run data before (re)creating dirs/laying out
            ## fresh files, so we don't wipe out the dirs mkdirs is about
            ## to create.
            if needs_layout and self.deletefiles:
                print("Cleaning up datapath from previous runs...")
                deletefiles_cmd = f"rm -rf {data_path}/*"
                deletefiles_process = run_cmd(deletefiles_cmd, dry_run=self.dry_run)

            if self.params["operation"] == "write" or needs_layout:
                mkdirs_cmd = f"{self.elbencho_path} --thread {self.params['ppn']} {self._hosts_var()} --mkdirs {data_path}"
                mkdirs_process = run_cmd(mkdirs_cmd, dry_run=self.dry_run)

            if needs_layout:
                ## Sequentially write out the files first, then the actual
                ## test below does the random read against that data.
                layout_cmd = self._cmd_builder(data_path, operation="write", random=0, runtime=0)

                if self.dry_run:
                    layout_process = run_cmd(layout_cmd, dry_run=True)
                else:
                    with open(f"{self.log_path}/elbencho/{self.runid_base}/{self.fname}.layout", "w") as layout_log_file:
                        layout_process = run_cmd(
                            layout_cmd,
                            stdin=subprocess.DEVNULL,
                            stdout=layout_log_file,
                            stderr=layout_log_file
                        )

                    if layout_process.returncode != 0:
                        print("There was an issue with the file layout! Skipping the read test since the data was not fully written.")
                        return

            cmd = self._cmd_builder(data_path)

            if self.dry_run:
                run_cmd(cmd, dry_run=True)
            else:
                with open(f"{self.log_path}/elbencho/{self.runid_base}/{self.fname}", "w") as log_file:
                    process = run_cmd(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=log_file,
                        stderr=log_file
                    )

                with open(f"{self.log_path}/elbencho/{self.runid_base}/{self.fname}", "r") as f:
                    for line in f:
                        stripped = line.strip()
                        # table headers, e.g. "OPERATION   RESULT TYPE         FIRST DONE   LAST DONE"
                        # and the "===========..." separator line
                        if stripped.startswith("OPERATION") or stripped.startswith("==="):
                            print(f"       {stripped}")
                        # results table header row, e.g. "WRITE     Elapsed time     :   2m3.241s   2m5.755s"
                        elif stripped.startswith(("WRITE", "READ")):
                            print(f"       {stripped}")
                        # subsequent result rows, e.g. "IOPS             :   33395   33352"
                        elif stripped.startswith(("IOPS", "Throughput", "Total MiB", "Elapsed", "Files/s", "Files total")):
                            print(f"       {stripped}")

            if self.params["operation"] == "read":
                ## Delete previous run data before running write if 'deletefiles' is true
                if self.deletefiles:
                    print("Cleaning up datapath from previous runs...")
                    deletefiles_cmd = f"rm -rf {data_path}/*"
                    deletefiles_process = run_cmd(deletefiles_cmd, dry_run=self.dry_run)

        finally:
            if self.manage_service:
                self._stop_elbencho_service()

    def stop(self):
        print("stopping BM")
