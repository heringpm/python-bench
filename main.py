#!/usr/bin/python3

# Basis for automating benchmarking

import json
import os
from pathlib import Path
from typing import Any, Union
from monitoring import SFAMonitoring
from benchmarks.ior import IORBenchmark
from benchmarks.mdtest import MDTestBenchmark
from datetime import datetime
from itertools import product
import time



config_file = Path("./config.json")
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
	global data_path_root, mpirun_path, tools, log_path, appliances, machinefile, mpi_conf
	data_path_root = config_data[0]["data_path_root"]
	mpirun_path = config_data[0]["mpirun_path"]
	mpi_conf = config_data[0]["mpi_conf"]
	log_path = config_data[0]["log_path"]
	tools = config_data[0]["tools"]
	appliances = config_data[0]["appliances"]
	machinefile = config_data[0]["machine_file"]

def setup_logging(log_path, tool):

	### Setup the runID directory
	global now, runid, stamp, file_stamp, runid_base
	now = datetime.now()
	runid = int(now.timestamp())
	file_stamp = now.strftime("%Y-%m-%d_%H:%M:%S")
	stamp = now.strftime("%Y%m%d_%H%M")
	runid_base = f"{runid}_{stamp}"
	time.sleep(5)

	### Create directories for logs
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
			name = f"ior.{cfg['clients']}-clients.{cfg['ppn']}-ppn.{cfg['blocksize']}-size.{cfg['xfersize']}-xfersize.{cfg['directio']}-directio.{cfg['fileperproc']}-fileperproc.{cfg['fsync']}-fsync.{cfg['api']}-api.{cfg['checksums']}-checksums.{cfg['operation']}"
			tests[name] = cfg
	elif tool == "mdtest":
		for cfg in _generate_combos(params):
			name = f"mdtest_{cfg['clients']}clients_{cfg['ppn']}ppn_{cfg['objects']}"
			tests[name] = cfg
	return tests

	
def main() -> None:
	config_data = load_config_file(config_file)
	setup_vars(config_data)

	# Setup the logging root path
	for tool, tool_path in tools.items():
		setup_logging(log_path, tool)

		tool_tests = config_data[0]["tests"][tool]
		all_tool_tests = generate_tool_tests(tool_tests, tool)

		for test, params in all_tool_tests.items():
			### Startup SFA Monitoring
			monitoring = SFAMonitoring(
				appliances=appliances,
				log_path=log_path,
				runid_base=runid_base,
				tool=tool,
				fname=f"{runid}_{file_stamp}_{test}_log.out"
			)
			monitoring.start()
			print(params)
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
					machinefile=machinefile
				)
				ior.start()
			elif tool == "mdtest":
				mdtest = MDTestBenchmark(config=config_data)
				#mdtest.start()
			

			monitoring.stop()

if __name__ == "__main__":
	main()