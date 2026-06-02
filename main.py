#!/usr/bin/python3

# Basis for automating benchmarking

import json
import os
from pathlib import Path
from typing import Any
from monitoring import SFAMonitoring



config_file = Path("./config.json")
mpi_path = None
tools = None
log_path = None
appliances = None


def load_config_file(path: str | Path):
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

def setup_logging(log_path):
	for tool in tools:
		tool_path = Path(log_path+"/"+tool)
		#tool_path.parent.mkdir(parents=True, exists_ok=True)
	
	# -----Old way of doing this---- setup the logging path for each tool
	#for tool in tools:
	#	print("mkdir "+log_path+"/"+tool)	

def main() -> None:
	config_data = load_config_file(config_file)
	setup_vars(config_data)

	# Setup the logging root path
	setup_logging(log_path)

	### Startup SFA Monitoring
	monitoring = SFAMonitoring(
		appliances=config_data[0]["appliances"],
		log_path=log_path,
		tools=tools,
		fname="log.out"
	)
	monitoring.start()

if __name__ == "__main__":
	main()