import re
import subprocess
from pathlib import Path

from utils.hosts import resolve_hosts
from utils.shell import run_cmd

# mlpstorage's DLIO workloads each define a fixed reference dataset size
# (--param dataset.num_files_train) that the benchmark is expected to run
# against. Used as a fallback if a "datasize" phase can't be run or its
# output can't be parsed, and as the dry-run stand-in for datasize's result.
# Prefer setting per-model counts via the "file_counts" config key instead,
# which also lets datasize be bypassed entirely.
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

    def _datasize_cmd(self, hosts_str: str, results_dir: Path) -> str:
        max_accelerators = self.params.get("max_accelerators", self.params["ppn"])
        return (
            f'{self.mlperf_path} training datasize --model {self.params["model"]} '
            f'--client-host-memory-in-gb {self.params["client_host_memory_gb"]} '
            f'--max-accelerators {max_accelerators} --num-client-hosts {self.params["clients"]} '
            f'--accelerator-type {self.params["accelerator_type"]} --hosts={hosts_str} '
            f'--results-dir={results_dir} --allow-run-as-root --oversubscribe'
        )

    def _datagen_cmd(self, data_path: Path, results_dir: Path, hosts_str: str, num_files: int) -> str:
        return (
            f'{self.mlperf_path} training datagen --exec-type=mpi '
            f'--param dataset.num_files_train={num_files} '
            f'--num-processes={self.params["ppn"]} --allow-run-as-root --oversubscribe '
            f'--hosts={hosts_str} --model={self.params["model"]} --results-dir={results_dir} '
            f'--data-dir={data_path} --mpi-params="{self.params["mpi_params"]}"'
        )

    def _run_cmd(self, data_path: Path, results_dir: Path, hosts_str: str, num_files: int) -> str:
        cmd = (
            f'{self.mlperf_path} training run --accelerator-type {self.params["accelerator_type"]} '
            f'--num-accelerators {self.params["ppn"]} --num-client-hosts {self.params["clients"]} '
            f'--client-host-memory-in-gb {self.params["client_host_memory_gb"]} '
            f'--allow-run-as-root --oversubscribe --open '
            f'--param dataset.num_files_train={num_files} '
            f'--param reader.read_threads={self.params["read_threads"]} '
            f'--loops {self.params["loops"]} --hosts={hosts_str} '
            f'--model={self.params["model"]} --results-dir={results_dir} '
            f'--data-dir={data_path} --mpi-params="{self.params["mpi_params"]}"'
        )
        if self.params.get("extra_args"):
            cmd += f' {self.params["extra_args"]}'
        return cmd

    def _exec_phase(self, cmd: str, fname: str) -> int:
        """Run cmd, logging to {log_path}/mlperf/{runid_base}/{fname}. Returns the exit code (0 in dry-run)."""
        if self.dry_run:
            run_cmd(cmd, dry_run=True)
            return 0
        with open(f"{self.log_path}/mlperf/{self.runid_base}/{fname}", "w") as log_file:
            process = run_cmd(cmd, stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file)
        return process.returncode

    def _run_datasize(self, hosts_str: str, results_dir: Path) -> int:
        """Run the mlpstorage 'datasize' phase and parse dataset.num_files_train from its output."""
        model = self.params["model"]
        cmd = self._datasize_cmd(hosts_str, results_dir)

        if self.dry_run:
            run_cmd(cmd, dry_run=True)
            fallback = NUM_FILES_BY_MODEL.get(model)
            if fallback is None:
                raise ValueError(
                    f"Unknown mlperf model '{model}' with no 'file_counts' override; add it to "
                    "NUM_FILES_BY_MODEL in benchmarks/mlperf.py or set file_counts in config."
                )
            return fallback

        log_file_path = f"{self.log_path}/mlperf/{self.runid_base}/{self.fname}.datasize"
        with open(log_file_path, "w") as log_file:
            process = run_cmd(cmd, stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file)

        if process.returncode != 0:
            raise RuntimeError(f"mlpstorage datasize failed for model '{model}' (see {log_file_path}).")

        with open(log_file_path, "r") as f:
            contents = f.read()

        match = re.search(r"dataset\.num_files_train=(\d+)", contents)
        if match:
            num_files = int(match.group(1))
            print(f"       datasize: model={model} -> num_files_train={num_files}")
            return num_files

        fallback = NUM_FILES_BY_MODEL.get(model)
        if fallback is None:
            raise RuntimeError(
                f"Could not parse num_files_train from datasize output for model '{model}' "
                f"(see {log_file_path}), and no fallback is defined."
            )
        print(f"       Warning: could not parse num_files_train from datasize output; using fallback {fallback}.")
        return fallback

    def _resolve_num_files(self, hosts_str: str, results_dir: Path) -> int:
        """Look up a per-model file count from config ('file_counts'), bypassing datasize
        if present; otherwise run the datasize phase to calculate it."""
        model = self.params["model"]
        file_counts = self.params.get("file_counts", {})
        if model in file_counts:
            return file_counts[model]
        return self._run_datasize(hosts_str, results_dir)

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

        operation = self.params["operation"]

        if operation == "datasize":
            cmd = self._datasize_cmd(hosts_str, results_dir)
        elif operation in ("datagen", "run"):
            num_files = self._resolve_num_files(hosts_str, results_dir)

            ## Auto-generate the dataset ahead of the actual run, unless the
            ## caller explicitly opts out (e.g. the data is already in place).
            if operation == "run" and not self.params.get("skip_datagen", False):
                datagen_cmd = self._datagen_cmd(data_path, results_dir, hosts_str, num_files)
                datagen_rc = self._exec_phase(datagen_cmd, f"{self.fname}.datagen")
                if datagen_rc != 0:
                    print("There was an issue with datagen! Skipping the run since the dataset was not generated.")
                    return

            if operation == "run":
                cmd = self._run_cmd(data_path, results_dir, hosts_str, num_files)
            else:
                cmd = self._datagen_cmd(data_path, results_dir, hosts_str, num_files)
        else:
            raise ValueError(f"Unknown mlperf operation '{operation}' (expected 'datasize', 'datagen', or 'run').")

        self._exec_phase(cmd, self.fname)
