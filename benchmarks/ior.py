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


		### Build out needed params

		total_ppn = self.params["ppn"] * self.params["clients"]

		## DATA POOLS
		if self.params["pools"] != "default":
			data_path = Path(f"{self.data_path_root}/ior/{self.params['pools']}")
			data_path.mkdir(parents=True, exist_ok=True)
			pool_stripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
			pool_overstripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

			set_stripe_process = subprocess.run(["bash", "-c", pool_stripe_cmd])

			if set_stripe_process.returncode != 0:
				set_overstripe_process = subprocess.run(["bash", "-c", pool_stripe_cmd])

		else:
			data_path = Path(f"{self.data_path_root}/ior")
			data_path.mkdir(parents=True, exist_ok=True)
			stripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
			overstripe_cmd = f"lfs setstripe -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

			set_stripe_process = subprocess.run(["bash", "-c", stripe_cmd])

			if set_stripe_process.returncode != 0:
				set_overstripe_process = subprocess.run(["bash", "-c", stripe_cmd])

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
			if self.params["operation"] == "read" and self.params["file_layout"]:
				### Run file layout
				with open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}.layout", "w") as layout_log_file:

				    parts = run_options.split()
				    parts = ["-w" if p == "-r" else p for p in parts]
				    layout_run_options = " " + " ".join(parts) + " "

					layout_cmd = (
						f'{self.mpirun_path} {hosts_var} '
						f'{self.mpi_conf} --np {total_ppn} '
						f'{self.ior_path} -a {self.params["api"]} -v -d 1 '
						f'-b {self.params["blocksize"]} -t {self.params["xfersize"]} '
						f'{layout_run_options} {self.params["extra_args"]} -o {data_path}/f'

					)
					print(layout_cmd)
					layout_process = subprocess.run(
					    ["bash", "-c", layout_cmd],
					    stdin=subprocess.DEVNULL,
					    stdout=layout_log_file,
					    stderr=layout_log_file
					)

				### Once layout is finished add the random flag back in to run_options
				if layout_process.returncode == 0:
					run_options += " -z "
				else:
					print("There was an issue with the file layout!")
			else:
				run_options += " -z "

		with open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}", "w") as log_file:

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

		with open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}", "r") as f:
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