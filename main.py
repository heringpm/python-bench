#!/usr/bin/python3

# Basis for automating benchmarking

import json
import os
from pathlib import Path
from typing import Any, Union
from monitoring import SFAMonitoring
from datetime import datetime
import time



config_file = Path("./config.json")
mpi_path = None
tools = None
log_path = None
appliances = None
run_log_base = None
now = None
runid = None
file_stamp = None
stamp = None
runid_base = None


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
	global mpi_path, tools, log_path, appliances
	mpi_path = config_data[0]["mpirun_path"]
	log_path = config_data[0]["log_path"]
	tools = config_data[0]["tools"]
	appliances = config_data[0]["appliances"]

def setup_logging(log_path, tool):

	### Setup the runID directory
	global now, runid, stamp, file_stamp, runid_base
	print(f"changing now variable from {now}...")
	now = datetime.now()
	print(f"...to {now}")
	runid = int(now.timestamp())
	file_stamp = now.strftime("%Y-%m-%d_%H:%M:%S")
	stamp = now.strftime("%Y%m%d_%H%M")
	print(f"file stamp is {file_stamp}")
	runid_base = f"{runid}_{stamp}"
	print(f"for {tool} this is our runID {runid}_{stamp}")
	time.sleep(5)

	path = Path(f"{log_path}/{tool}/{runid_base}")
	path.parent.mkdir(parents=True, exists_ok=True)

	

def main() -> None:
	config_data = load_config_file(config_file)
	setup_vars(config_data)

	# Setup the logging root path
	for tool in tools:
		setup_logging(log_path, tool)

		### Startup SFA Monitoring
		monitoring = SFAMonitoring(
			appliances=appliances,
			log_path=log_path,
			runid_base=runid_base,
			tool=tool,
			fname=f"{runid}_{file_stamp}_{tool}_log.out"
		)
		#monitoring.start()

		### RUN BENCHMARK HERE

		monitoring.stop()

if __name__ == "__main__":
	main()