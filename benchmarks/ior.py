import time
import os
import subprocess
import threading
from itertools import product
from pathlib import Path

class IORBenchmark:
	def __init__(self, params, mpirun_path, mpi_conf, ior_path, data_path_root, log_path, runid_base, fname, machinefile, deletefiles):
		self.params = params
		self.mpirun_path = mpirun_path
		self.mpi_conf = mpi_conf
		self.ior_path = ior_path
		self.data_path_root = data_path_root
		self.log_path = log_path
		self.runid_base = runid_base
		self.fname = fname
		self.machinefile = machinefile
		self.deletefiles = deletefiles


	def start(self):


		### build out needed params

		total_ppn = self.params["ppn"] * self.params["clients"]

		## DATA POOLS
		if self.params["pools"] != "default":
			data_path = Path(f"{self.data_path_root}/ior/{self.params['pools']}")
			data_path.mkdir(parents=True, exist_ok=True)
			pool_stripe_cmd = f"lfs setstripe -p {self.params['pools']} {data_path}"
			print(f"pool striping command - {pool_stripe_cmd}")

			set_stripe_process = subprocess.run(["bash", "-c", pool_stripe_cmd])

		else:
			data_path = Path(f"{self.data_path_root}/ior")
			data_path.mkdir(parents=True, exist_ok=True)

		run_options = ""
		## OPERATION
		if self.params["operation"] == "write":
			run_options += " -w "
		elif self.params["operation"] == "read":
			run_options += " -r "

		## KEEP FILES
		if self.params["keep_files"] == 1:
			run_options += " -k "

		## FILE PER PROC
		if self.params["fileperproc"] == 1:
			run_options += " -F "

		## DIRECT IO
		if self.params["directio"] == 1:
			run_options += " --posix.odirect "

		## RANDOM
		if self.params["randomoffset"] == 1:
			run_options += " -z "

		## CLIENTS TO USE
		with open(self.machinefile, "r") as f:
			lines = f.readlines()
			line_count = len(lines)
			client_count = int(self.params["clients"])
			hosts = [line.strip() for line in lines[:client_count]]
			host_string = ",".join(hosts)

			if line_count == self.params["clients"]:
				hosts_var = f"--machinefile {self.machinefile}"
			elif line_count > self.params["clients"]:
				hosts_var = f"--host {host_string}"
			elif line_count < self.params["clients"]:
				raise ValueError("ERROR - Not enough clients in machinefile to satisfy client count request!")

		with open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}.ior.log", "w") as log_file:

			cmd = (
				f'{self.mpirun_path} {hosts_var} '
				f'{self.mpi_conf} --np {total_ppn} '
				f'{self.ior_path} -a {self.params["api"]} -v -d 1 '
				f'-b {self.params["blocksize"]} -t {self.params["xfersize"]} '
				f'{run_options} {self.params["extra_args"]} -o {data_path}/f'

			)

			process = subprocess.run(
			    ["bash", "-c", cmd],
			    stdin=subprocess.DEVNULL,
			    stdout=log_file,
			    stderr=log_file
			)

		with open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}.ior.log", "r") as f:
		    for line in f:
		        # equivalent to grep
		        if "Max Write" in line or "Max Read" in line:
		            # equivalent to awk - split on whitespace and grab columns
		            cols = line.split()
		            print(f"       {cols[0]} {cols[1]} {cols[2]} {cols[3]}")


		if self.params["operation"] == "read":
			## Delete previous run data before running write if 'deletefiles' is true
			if self.deletefiles:
				print("Cleaning up datapath from previous runs...")
				deletefiles_cmd = f"rm -rf {data_path}/*"
				deletefiles_process = subprocess.run(["bash", "-c", deletefiles_cmd])


	def stop(self):
		print("stopping BM")