import re
import subprocess
from pathlib import Path

from utils.hosts import resolve_hosts
from utils.shell import run_cmd


class IO500Benchmark:
    """Drives the io500 binary (built via io500's own prepare.sh, which
    statically links its own pinned ior/mdtest/pfind - it can't reuse
    externally-built ior/mdtest binaries). This class generates the .ini
    file io500 reads its config from, then launches it via mpirun."""

    def __init__(self, params, io500_path, mpirun_path, mpi_conf, data_path_root, log_path, runid_base, fname, machinefile, dry_run=False):
        self.params = params
        self.io500_path = io500_path
        self.mpirun_path = mpirun_path
        self.mpi_conf = mpi_conf
        self.data_path_root = data_path_root
        self.log_path = log_path
        self.runid_base = runid_base
        self.fname = fname
        self.machinefile = machinefile
        self.dry_run = dry_run

    def _bool(self, value) -> str:
        return "TRUE" if value else "FALSE"

    def _build_sections(self, data_path: Path, results_dir: Path) -> dict:
        p = self.params

        sections = {
            "global": {
                "datadir": str(data_path),
                "resultdir": str(results_dir),
                "timestamp-datadir": "FALSE",
                "timestamp-resultdir": "FALSE",
                "api": p.get("api", "POSIX"),
                "drop-caches": self._bool(p.get("drop_caches", 0)),
                "verbosity": p.get("verbosity", 1),
            },
            "debug": {
                "stonewall-time": p.get("stonewall_time", 300),
            },
            "ior-easy": {
                "transferSize": p.get("ior_easy_transfer_size", "2m"),
                "blockSize": p.get("ior_easy_block_size", "9920000m"),
                "filePerProc": self._bool(p.get("ior_easy_file_per_proc", 1)),
                "run": self._bool(p.get("run_ior_easy", 1)),
            },
            "mdtest-easy": {
                "n": p.get("mdtest_easy_n", 1000000),
                "run": self._bool(p.get("run_mdtest_easy", 1)),
            },
            "find-easy": {
                "run": self._bool(p.get("run_find_easy", 1)),
            },
            "ior-hard": {
                "segmentCount": p.get("ior_hard_segment_count", 10000000),
                "run": self._bool(p.get("run_ior_hard", 1)),
            },
            "mdtest-hard": {
                "n": p.get("mdtest_hard_n", 1000000),
                "run": self._bool(p.get("run_mdtest_hard", 1)),
            },
            "find": {
                "run": self._bool(p.get("run_find", 1)),
            },
        }

        ## Free-form escape hatch for any ini section/key not covered above,
        ## e.g. {"ior-hard": {"collective": "TRUE"}, "mdworkbench": {"run": "TRUE"}}.
        for section, kv in (p.get("extra_ini") or {}).items():
            sections.setdefault(section, {}).update(kv)

        return sections

    def _build_ini(self, sections: dict) -> str:
        lines = []
        for section, kv in sections.items():
            lines.append(f"[{section}]")
            for key, value in kv.items():
                lines.append(f"{key} = {value}")
            lines.append("")
        return "\n".join(lines)

    def run(self):
        ## DATA POOLS
        if self.params["pools"] != "default":
            data_path = Path(f"{self.data_path_root}/io500/{self.params['pools']}")
            if self.dry_run:
                print(f"[DRY-RUN] mkdir -p {data_path}")
            else:
                data_path.mkdir(parents=True, exist_ok=True)
            pool_stripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
            pool_overstripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

            set_stripe_process = run_cmd(pool_stripe_cmd, dry_run=self.dry_run)

            if set_stripe_process.returncode != 0:
                set_overstripe_process = run_cmd(pool_overstripe_cmd, dry_run=self.dry_run)
        else:
            data_path = Path(f"{self.data_path_root}/io500")
            if self.dry_run:
                print(f"[DRY-RUN] mkdir -p {data_path}")
            else:
                data_path.mkdir(parents=True, exist_ok=True)
            stripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
            overstripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

            set_stripe_process = run_cmd(stripe_cmd, dry_run=self.dry_run)

            if set_stripe_process.returncode != 0:
                set_overstripe_process = run_cmd(overstripe_cmd, dry_run=self.dry_run)

        results_dir = Path(f"{self.log_path}/io500/{self.runid_base}")

        ## CLIENT HOSTS TO USE
        client_count = int(self.params["clients"])
        hosts, _ = resolve_hosts(self.machinefile, client_count)
        hosts_var = f"--host {','.join(hosts)}"
        total_ppn = self.params["ppn"] * client_count

        ## GENERATE THE .ini FILE
        sections = self._build_sections(data_path, results_dir)
        ini_contents = self._build_ini(sections)
        ini_path = Path(f"{self.log_path}/io500/{self.runid_base}/{self.fname}.ini")

        if self.dry_run:
            print(f"[DRY-RUN] write io500 ini to {ini_path}:\n{ini_contents}")
        else:
            results_dir.mkdir(parents=True, exist_ok=True)
            with open(ini_path, "w") as f:
                f.write(ini_contents)

        cmd = (
            f'{self.mpirun_path} {hosts_var} '
            f'{self.mpi_conf} --np {total_ppn} '
            f'{self.io500_path} {ini_path} --timestamp {self.runid_base}'
        )

        if self.params.get("extra_args"):
            cmd += f' {self.params["extra_args"]}'

        if self.dry_run:
            run_cmd(cmd, dry_run=True)
        else:
            with open(f"{self.log_path}/io500/{self.runid_base}/{self.fname}", "w") as log_file:
                process = run_cmd(cmd, stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file)

            with open(f"{self.log_path}/io500/{self.runid_base}/{self.fname}", "r") as f:
                for line in f:
                    stripped = line.rstrip("\n")
                    if re.search(r"\[RESULT\]|SCORE|bandwidth|IOPS", stripped, re.IGNORECASE):
                        print(f"       {stripped.strip()}")

    def stop(self):
        print("stopping BM")
