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
	parser.add_argument("--dropclientcache", action="store_true", help="Drop cache on clients before tests")
	parser.add_argument("--dropservercache", action="store_true", help="Drop cache on servers before tests")
	parser.add_argument("--deletebeforewrite", action="store_true", help="Delete old files before writing new test files")
	parser.add_argument("--noclienttune", action="store_true", help="Dont run any client tuning")

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

def generate_tool_tests(params: dict, tool) -> dict:
	tests = {}
	if tool == "ior":
		for cfg in _generate_combos(params):
			name = (
				f"ior.{cfg['clients']}-clients.{cfg['ppn']}-ppn.{cfg['pools']}-pool.{cfg['blocksize']}-size.{cfg['xfersize']}-xfersize."
				f"{cfg['directio']}-directio.{cfg['fileperproc']}-fileperproc.{cfg['randomoffset']}-random."
				f"{cfg['api']}-api.{cfg['checksums']}-checksums.{cfg['operation']}"
			)
			tests[name] = cfg
	elif tool == "mdtest":
		for cfg in _generate_combos(params):
			name = (
				f"mdtest.{cfg['clients']}-clients.{cfg['ppn']}-ppn.{cfg['pools']}-pool.{cfg['objects']}-objects.{cfg['branching']}."
				f"{cfg['depth']}-depth.{cfg['uniquedir']}-uniquedir.{cfg['itemsperdir']}-itemsperdir.{cfg['directio']}-directio"
			)
			tests[name] = cfg
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
		process = subprocess.run(["bash", "-c", fstrim_cmd])
	if args.dropclientcache:
		print("Dropping cache on servers...")
		process = subprocess.run(["bash", "-c", server_cache_cmd])
	if args.dropservercache:	
		print("Dropping cache on clients...")
		process = subprocess.run(["bash", "-c", client_cache_cmd])

	
def tune_clients(params):
	checksums = params["checksums"]
	if checksums == "off":
		tune_cmd = tuning_files["tune_no_checksums"]
	if checksums == "on":
		tune_cmd = tuning_files["tune_checksums"]

	### Add machinefile to tune command
	tune_cmd += f" {machinefile}"
	### Run tunning command
	print("Running client tuning scripts...")
	process = subprocess.run(
		["bash", "-c", tune_cmd],
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

		setup_logging(log_path, tool)

		### Setup some progress messaging
		total = len(all_tool_tests)
		completed = 0
		interrupted = False

		print(f"\n{'='*60}")
		print(f"  {tool} Benchmark Run — {total} tests queued")
		print(f"{'='*60}\n")

		try:
			for test, params in all_tool_tests.items():
				print(f"  ▶  [{completed + 1}/{total}] {test}")
				### Startup SFA Monitoring
				monitoring = SFAMonitoring(
					appliances=appliances,
					log_path=log_path,
					runid_base=runid_base,
					tool=tool,
					fname=f"{runid}_{file_stamp}_{test}_log.out"
				)
				monitoring.start()
				### PREP FILESYSTEM FOR TEST
				test_prep(args)

				### CLIENT TUNINGS
				if args.noclienttune == False:
					tune_clients(params)

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
						deletefiles=args.deletebeforewrite
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
						machinefile=machinefile
					)
					mdtest.start()

				completed += 1
				print(f"  ✓  [{completed}/{total}] Done\n")
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

if __name__ == "__main__":
	main()