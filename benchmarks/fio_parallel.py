import threading
import time

from benchmarks.fio import FIOBenchmark
from utils.shell import run_cmd


class FIOParallelBenchmark:
    """Runs two independently-configured fio jobs (job1/job2) concurrently.

    Each job may target its own clients (via an optional per-job
    "machinefile" override), pool/path, block size, operation, etc. Both
    jobs are launched at the same time and this call blocks until both
    have finished.
    """

    def __init__(self, params, fio_path, data_path_root, log_path, runid_base,
                 fname_job1, fname_job2, machinefile, deletefiles, dry_run=False):
        self.job1_params = params["job1"]
        self.job2_params = params["job2"]
        # Seconds to wait after job1 starts before launching job2, so job1
        # can ramp up/settle first (e.g. ready state for time-based runs).
        self.job2_start_delay = params.get("job2_start_delay", 0)
        self.fio_path = fio_path
        self.data_path_root = data_path_root
        self.log_path = log_path
        self.runid_base = runid_base
        self.fname_job1 = fname_job1
        self.fname_job2 = fname_job2
        self.default_machinefile = machinefile
        self.deletefiles = deletefiles
        self.dry_run = dry_run

    def _machinefile_for(self, job_params):
        return job_params.get("machinefile") or self.default_machinefile

    def run(self):
        mf1 = self._machinefile_for(self.job1_params)
        mf2 = self._machinefile_for(self.job2_params)

        # Start the fio --server daemon once per unique machinefile. If both
        # jobs target the same clients, this avoids starting/killing the
        # daemon out from under the other job while it's still running.
        unique_machinefiles = list(dict.fromkeys([mf1, mf2]))
        for mf in unique_machinefiles:
            start_cmd = f"clush --machinefile {mf} {self.fio_path} --server --daemonize=/tmp/fio.pid"
            run_cmd(start_cmd, dry_run=self.dry_run)

        job1 = FIOBenchmark(
            params=self.job1_params,
            fio_path=self.fio_path,
            data_path_root=self.data_path_root,
            log_path=self.log_path,
            runid_base=self.runid_base,
            fname=self.fname_job1,
            machinefile=mf1,
            deletefiles=self.deletefiles,
            dry_run=self.dry_run,
            manage_server=False,
        )
        job2 = FIOBenchmark(
            params=self.job2_params,
            fio_path=self.fio_path,
            data_path_root=self.data_path_root,
            log_path=self.log_path,
            runid_base=self.runid_base,
            fname=self.fname_job2,
            machinefile=mf2,
            deletefiles=self.deletefiles,
            dry_run=self.dry_run,
            manage_server=False,
        )

        errors = []

        def _run_job(job, label):
            try:
                job.run()
            except Exception as exc:
                errors.append((label, exc))

        try:
            t1 = threading.Thread(target=_run_job, args=(job1, "job1"), name="fio_parallel-job1")
            t2 = threading.Thread(target=_run_job, args=(job2, "job2"), name="fio_parallel-job2")
            t1.start()

            if self.job2_start_delay:
                if self.dry_run:
                    print(f"[DRY-RUN] wait {self.job2_start_delay}s before starting job2")
                else:
                    time.sleep(self.job2_start_delay)

            t2.start()
            t1.join()
            t2.join()
        finally:
            for mf in unique_machinefiles:
                stop_cmd = f"clush --machinefile {mf} killall fio"
                run_cmd(stop_cmd, dry_run=self.dry_run)

        if errors:
            raise RuntimeError(
                "fio_parallel job(s) failed: "
                + ", ".join(f"{label}: {exc}" for label, exc in errors)
            )
