import subprocess
from pathlib import Path

from utils.hosts import resolve_hosts
from utils.shell import run_cmd

# mlpstorage's DLIO workloads each define a fixed reference dataset size
# (--param dataset.num_files_train) that the benchmark is expected to run
# against. Mirrors the mapping used in the mlperf bash script.
NUM_FILES_BY_MODEL = {
    "unet3d":    224000,
    "cosmoflow": 5830919,
    "resnet50":  114978,
}


class MLPerfBenchmark:
    def __init__(self, params, mlperf_path, data_path_root, log_path, runid_base, fname, machinefile, dry_run=False):
        self.params = params
        self.mlperf_path = mlperf_path
        self.data_path_root = data_path_root
        self.log_path = log_path
        self.runid_base = runid_base
        self.fname = fname
        self.machinefile = machinefile
        self.dry_run = dry_run

    def _num_files(self) -> int:
        model = self.params["model"]
        if model not in NUM_FILES_BY_MODEL:
            raise ValueError(
                f"Unknown mlperf model '{model}'; add it to NUM_FILES_BY_MODEL in benchmarks/mlperf.py."
            )
        return NUM_FILES_BY_MODEL[model]

    def _cmd_builder(self, data_path: Path, results_dir: Path, hosts_str: str) -> str:
        num_files = self._num_files()
        operation = self.params["operation"]
        ppn = self.params["ppn"]
        clients = self.params["clients"]

        if operation == "datagen":
            op_params = (
                f'training datagen --exec-type=mpi '
                f'--param dataset.num_files_train={num_files} '
                f'--num-processes={ppn} --allow-run-as-root --oversubscribe'
            )
        elif operation == "run":
            op_params = (
                f'training run --accelerator-type {self.params["accelerator_type"]} '
                f'--num-accelerators {ppn} --num-client-hosts {clients} '
                f'--client-host-memory-in-gb {self.params["client_host_memory_gb"]} '
                f'--allow-run-as-root --oversubscribe --open '
                f'--param dataset.num_files_train={num_files} '
                f'--param reader.read_threads={self.params["read_threads"]} '
                f'--loops {self.params["loops"]}'
            )
        else:
            raise ValueError(f"Unknown mlperf operation '{operation}' (expected 'datagen' or 'run').")

        cmd = (
            f'{self.mlperf_path} {op_params} --hosts={hosts_str} '
            f'--model={self.params["model"]} --results-dir={results_dir} '
            f'--data-dir={data_path} --mpi-params="{self.params["mpi_params"]}"'
        )

        if self.params.get("extra_args"):
            cmd += f' {self.params["extra_args"]}'

        return cmd

    def run(self):
        data_path = Path(f"{self.data_path_root}/mlperf")
        if self.dry_run:
            print(f"[DRY-RUN] mkdir -p {data_path}")
        else:
            data_path.mkdir(parents=True, exist_ok=True)

        results_dir = Path(f"{self.log_path}/mlperf/{self.runid_base}")

        ## CLIENT HOSTS TO USE
        client_count = int(self.params["clients"])
        hosts, _ = resolve_hosts(self.machinefile, client_count)
        hosts_str = ",".join(hosts)

        cmd = self._cmd_builder(data_path, results_dir, hosts_str)

        if self.dry_run:
            run_cmd(cmd, dry_run=True)
        else:
            with open(f"{self.log_path}/mlperf/{self.runid_base}/{self.fname}", "w") as log_file:
                process = run_cmd(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=log_file
                )
