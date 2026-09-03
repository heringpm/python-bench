#!/usr/bin/python3

# Basis for automating benchmarking

import json
import os
import re
import subprocess
import argparse
import time
from pathlib import Path
from typing import Any, Union
from monitoring import SFAMonitoring
from benchmarks.ior import IORBenchmark
from benchmarks.mdtest import MDTestBenchmark
from benchmarks.fio import FIOBenchmark
from benchmarks.fio_parallel import FIOParallelBenchmark
from benchmarks.mlperf import MLPerfBenchmark
from benchmarks.elbencho import ElbenchoBenchmark
from utils.shell import run_cmd
from datetime import datetime
from itertools import product


### Global vars

config_file = Path("./config.json")
fstrim_script = Path("/work/scripts/benchmark_script/fstrim_lustredevs_parallel.sh")
mpirun_path = None
tools = None
log_path = None
appliances = None
run_log_base = None
now = None
runid = None
file_stamp = None
stamp = None
runid_base = None
data_path_root = None
machinefile = None
mpi_conf = None
tuning_files = None



def read_arguments():
    parser = argparse.ArgumentParser(description="Storage Benchmarking Suite")
    parser.add_argument("--fstrim", action="store_true", help="Run fstrim on filesystem before tests")
    parser.add_argument("--drop-client-cache", action="store_true", help="Drop cache on clients before tests")
    parser.add_argument("--drop-server-cache", action="store_true", help="Drop cache on servers before tests")
    parser.add_argument("--delete-before-write", action="store_true", help="Delete old files before writing new test files")
    parser.add_argument("--no-client-tune", action="store_true", help="Dont run any client tuning")
    parser.add_argument("--dry-run", action="store_true", help="Print all commands that would run for the full config without executing them")

    args = parser.parse_args()

    return args


def load_config_file(path: Union[str, Path]):
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Settings file must contain a JSON array of objects.")

    parsed: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"Settings entry at index {i} must be an object.")
        parsed.append(item)
    return parsed


def setup_vars(config_data):
    global data_path_root, mpirun_path, tools, log_path, appliances, machinefile, mpi_conf, tuning_files
    data_path_root = config_data[0]["data_path_root"]
    mpirun_path = config_data[0]["mpirun_path"]
    mpi_conf = config_data[0]["mpi_conf"]
    log_path = config_data[0]["log_path"]
    tools = config_data[0]["tools"]
    appliances = config_data[0]["appliances"]
    machinefile = config_data[0]["machine_file"]
    tuning_files = config_data[0]["tuning_files"]

def setup_logging(log_path, tool):

    ### Setup the runID directory
    global now, runid, stamp, file_stamp, runid_base
    now = datetime.now()
    runid = int(now.timestamp())
    file_stamp = now.strftime("%Y-%m-%d_%H:%M:%S")
    stamp = now.strftime("%Y%m%d_%H%M")
    runid_base = f"{runid}_{stamp}"
    time.sleep(5)
        
    path = Path(f"{log_path}/{tool}/{runid_base}")
    path.mkdir(parents=True, exist_ok=True)

    


def _generate_combos(params: dict) -> list:
    combos = []
    for combo in product(*params.values()):
        cfg = dict(zip(params.keys(), combo))
        combos.append(cfg)
    return combos

def _add_test(tests: dict, name: str, cfg: dict) -> None:
    """Add cfg to tests under name, disambiguating with a numeric suffix if
    name is already taken (e.g. two combos differ only in a param that isn't
    part of the generated name, such as extra_args)."""
    final_name = name
    suffix = 2
    while final_name in tests:
        final_name = f"{name}.{suffix}"
        suffix += 1
    tests[final_name] = cfg

def _fio_combo_name(cfg: dict) -> str:
    return (
        f"{cfg['clients']}-clients.{cfg['ppn']}-ppn.{cfg['pools']}-pool.{cfg['stripesize']}-stripesize.{cfg['stripecount']}-stripecount.{cfg['blocksize']}-blocksize."
        f"{cfg['filesize']}-filesize.{cfg['iodepth']}-iodepth.{cfg['directio']}-directio.{cfg['operation']}-operation"
    )

def generate_tool_tests(params: dict, tool) -> dict:
    tests = {}
    if tool == "ior":
        for cfg in _generate_combos(params):
            name = (
                f"ior.{cfg['clients']}-clients.{cfg['ppn']}-ppn.{cfg['pools']}-pool.{cfg['stripesize']}-stripesize.{cfg['stripecount']}-stripecount.{cfg['blocksize']}-size.{cfg['xfersize']}-xfersize."
                f"{cfg['directio']}-directio.{cfg['fileperproc']}-fileperproc.{cfg['randomoffset']}-random.{cfg['api']}-api.{cfg['checksums']}-checksums.{cfg['operation']}"
            )
            _add_test(tests, name, cfg)
    elif tool == "mdtest":
        for cfg in _generate_combos(params):
            name = (
                f"mdtest.{cfg['clients']}-clients.{cfg['ppn']}-ppn.{cfg['pools']}-pool.{cfg['stripesize']}-stripesize.{cfg['stripecount']}-stripecount.{cfg['DOM']}-DOM.{cfg['objects']}-objects."
                f"{cfg['branching']}-branching.{cfg['depth']}-depth.{cfg['byteswrite']}-byteswrite.{cfg['uniquedir']}-uniquedir.{cfg['itemsperdir']}-itemsperdir.{cfg['directio']}-directio"
            )
            _add_test(tests, name, cfg)
    elif tool == "fio":
        for cfg in _generate_combos(params):
            name = f"fio.{_fio_combo_name(cfg)}"
            _add_test(tests, name, cfg)
    elif tool == "fio_parallel":
        job1_combos = _generate_combos(params["job1"])
        job2_combos = _generate_combos(params["job2"])
        if len(job1_combos) != len(job2_combos):
            raise ValueError(
                f"fio_parallel job1 produces {len(job1_combos)} combo(s) but job2 produces "
                f"{len(job2_combos)}; they must produce the same number of combos so each "
                "job1 combo can be paired 1:1 with a job2 combo."
            )
        job2_start_delay = params.get("job2_start_delay", 0)
        for cfg1, cfg2 in zip(job1_combos, job2_combos):
            name = f"fio_parallel.job1-{_fio_combo_name(cfg1)}__job2-{_fio_combo_name(cfg2)}"
            _add_test(tests, name, {"job1": cfg1, "job2": cfg2, "job2_start_delay": job2_start_delay})
    elif tool == "mlperf":
        for cfg in _generate_combos(params):
            name = (
                f"mlperf.{cfg['model']}-model.{cfg['operation']}-operation.{cfg['clients']}-clients.{cfg['ppn']}-ppn."
                f"{cfg['accelerator_type']}-accelerator"
            )
            _add_test(tests, name, cfg)
    elif tool == "elbencho":
        for cfg in _generate_combos(params):
            name = (
                f"elbencho.{cfg['clients']}-clients.{cfg['ppn']}-ppn.{cfg['pools']}-pool.{cfg['stripesize']}-stripesize.{cfg['stripecount']}-stripecount."
                f"{cfg['blocksize']}-blocksize.{cfg['filesize']}-filesize.{cfg['iodepth']}-iodepth.{cfg['directio']}-directio.{cfg['operation']}-operation"
            )
            _add_test(tests, name, cfg)
    return tests

def test_prep(args):
    ### This will run fstrim and drop all client and server caches
    all_vms = ",".join(appliances.values())
    first_vm = list(appliances.values())[0]  # "sv30[0-3]"
    match = re.search(r"(\w+)\[(\d+)-\d+\]", first_vm)
    if match:
        first_vm = f"{match.group(1)}{match.group(2)}"
    


    fstrim_cmd = f"ssh {first_vm} 'clush -ab {fstrim_script}'"
    client_cache_cmd = f"clush --machinefile {machinefile} 'echo 3 > /proc/sys/vm/drop_caches'"
    server_cache_cmd = f"clush -w {all_vms} 'echo 3 > /proc/sys/vm/drop_caches'"

    if args.fstrim:
        print("Running fstrim...")
        process = run_cmd(fstrim_cmd, dry_run=args.dry_run, stdout=subprocess.DEVNULL, stderr=None)
    if args.drop_client_cache:
        print("Dropping cache on clients...")
        process = run_cmd(client_cache_cmd, dry_run=args.dry_run)
    if args.drop_server_cache:
        print("Dropping cache on servers...")
        process = run_cmd(server_cache_cmd, dry_run=args.dry_run)


def tune_clients(params, dry_run=False, target_machinefile=None):
    checksums = params["checksums"]
    if checksums == "off":
        tune_cmd = tuning_files["tune_no_checksums"]
    if checksums == "on":
        tune_cmd = tuning_files["tune_checksums"]

    ### Add machinefile to tune command
    tune_cmd += f" {target_machinefile or machinefile}"
    ### Run tunning command
    print("Running client tuning scripts...")
    process = run_cmd(
        tune_cmd,
        dry_run=dry_run,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=None
    )


def main() -> None:

    args = read_arguments()

    config_data = load_config_file(config_file)
    setup_vars(config_data)

    # Setup the logging root path
    for tool, tool_path in tools.items():
        tool_tests = config_data[0]["tests"][tool]
        all_tool_tests = generate_tool_tests(tool_tests, tool)

        # FIOBenchmark always logs under the "fio" subdirectory, so route
        # fio_parallel's logging setup there too, keeping both job logs
        # alongside regular fio runs.
        setup_logging(log_path, "fio" if tool == "fio_parallel" else tool)

        ### Setup some progress messaging
        total = len(all_tool_tests)
        completed = 0
        interrupted = False

        print(f"\n{'='*60}")
        print(f"  {tool} Benchmark Run — {total} tests queued")
        print(f"{'='*60}\n")

        ### elbencho uses a persistent service on each client rather than
        ### one started/stopped per test, so start it once for the whole
        ### batch of elbencho tests and tear it down once at the end.
        if tool == "elbencho":
            print("Starting elbencho service on clients...")
            run_cmd(f"clush --machinefile {machinefile} {tool_path} --service", dry_run=args.dry_run)

        try:
            for test, params in all_tool_tests.items():
                print(f"  ▶  [{completed + 1}/{total}] {test}")
                ### Startup SFA Monitoring
                monitoring = SFAMonitoring(
                    appliances=appliances,
                    log_path=log_path,
                    runid_base=runid_base,
                    tool=tool,
                    fname=f"{runid}_{file_stamp}_{test}_log.out",
                    dry_run=args.dry_run
                )
                monitoring.start()
                ### PREP FILESYSTEM FOR TEST
                test_prep(args)

                ### CLIENT TUNINGS
                if args.no_client_tune == False:
                    if tool == "fio_parallel":
                        # Each job may target its own client set (via a
                        # per-job "machinefile" override), so tune each
                        # side against its own clients.
                        job1_mf = params["job1"].get("machinefile") or machinefile
                        job2_mf = params["job2"].get("machinefile") or machinefile
                        tune_clients(params["job1"], dry_run=args.dry_run, target_machinefile=job1_mf)
                        if job2_mf != job1_mf:
                            tune_clients(params["job2"], dry_run=args.dry_run, target_machinefile=job2_mf)
                    else:
                        tune_clients(params, dry_run=args.dry_run)

                ### RUN BENCHMARKING HERE
                if tool == "ior":
                    ior = IORBenchmark(
                        params=params,
                        mpirun_path=mpirun_path,
                        mpi_conf=mpi_conf,
                        ior_path=tool_path,
                        data_path_root=data_path_root,
                        log_path=log_path,
                        runid_base=runid_base,
                        fname=f"{runid}_{file_stamp}_{test}_ior.log",
                        machinefile=machinefile,
                        deletefiles=args.delete_before_write,
                        dry_run=args.dry_run
                    )
                    ior.start()
                elif tool == "mdtest":
                    mdtest = MDTestBenchmark(params=params,
                        mpirun_path=mpirun_path,
                        mpi_conf=mpi_conf,
                        mdtest_path=tool_path,
                        data_path_root=data_path_root,
                        log_path=log_path,
                        runid_base=runid_base,
                        fname=f"{runid}_{file_stamp}_{test}_mdtest.log",
                        machinefile=machinefile,
                        dry_run=args.dry_run
                    )
                    mdtest.start()
                elif tool == "fio":
                    fio = FIOBenchmark(
                        params=params,
                        fio_path=tool_path,
                        data_path_root=data_path_root,
                        log_path=log_path,
                        runid_base=runid_base,
                        fname=f"{runid}_{file_stamp}_{test}_fio.log",
                        machinefile=machinefile,
                        deletefiles=args.delete_before_write,
                        dry_run=args.dry_run
                    )
                    fio.run()
                elif tool == "fio_parallel":
                    fio_parallel = FIOParallelBenchmark(
                        params=params,
                        fio_path=tool_path,
                        data_path_root=data_path_root,
                        log_path=log_path,
                        runid_base=runid_base,
                        fname_job1=f"{runid}_{file_stamp}_{test}_job1_fio.log",
                        fname_job2=f"{runid}_{file_stamp}_{test}_job2_fio.log",
                        machinefile=machinefile,
                        deletefiles=args.delete_before_write,
                        dry_run=args.dry_run
                    )
                    fio_parallel.run()
                elif tool == "mlperf":
                    mlperf = MLPerfBenchmark(
                        params=params,
                        mlperf_path=tool_path,
                        data_path_root=data_path_root,
                        log_path=log_path,
                        runid_base=runid_base,
                        fname=f"{runid}_{file_stamp}_{test}_mlperf.log",
                        machinefile=machinefile,
                        dry_run=args.dry_run
                    )
                    mlperf.run()
                elif tool == "elbencho":
                    elbencho = ElbenchoBenchmark(
                        params=params,
                        elbencho_path=tool_path,
                        data_path_root=data_path_root,
                        log_path=log_path,
                        runid_base=runid_base,
                        fname=f"{runid}_{file_stamp}_{test}_elbencho.log",
                        machinefile=machinefile,
                        deletefiles=args.delete_before_write,
                        dry_run=args.dry_run,
                        manage_service=False
                    )
                    elbencho.run()

                completed += 1
                print(f"  ✓  [{completed}/{total}] Done\n")
                monitoring.stop()
        except KeyboardInterrupt:
            interrupted = True

        finally:
            print(f"\n{'='*60}")
            print(f"  Summary")
            print(f"{'─'*60}")
            print(f"  Total:      {total}")
            print(f"  Completed:  {completed}")
            print(f"  Not run:    {total - completed}")
            print(f"  Status:     {'✓ COMPLETED' if not interrupted else '✗ INTERRUPTED'}")
            print(f"{'='*60}\n")


            monitoring.stop()

            if tool == "elbencho":
                print("Stopping elbencho service on clients...")
                run_cmd(f"clush --machinefile {machinefile} killall elbencho", dry_run=args.dry_run)


        

if __name__ == "__main__":
    main()