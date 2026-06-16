import time
from pathlib import Path
import subprocess

class MDTestBenchmark:
	def __init__(self, params, mpirun_path, mpi_conf, mdtest_path, data_path_root, log_path, runid_base, fname, machinefile):
		self.params = params
		self.mpirun_path = mpirun_path
		self.mpi_conf = mpi_conf
		self.mdtest_path = mdtest_path
		self.data_path_root = data_path_root
		self.log_path = log_path
		self.runid_base = runid_base
		self.fname = fname
		self.machinefile = machinefile

	def _cmd_builder(self) -> str:

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

		total_ppn = self.params["ppn"] * self.params["clients"]
		cmd = f"{self.mpirun_path} {hosts_var} {self.mpi_conf} --np {total_ppn} {self.mdtest_path} "

		### check for flags to add
		if self.params.get("objects"): cmd += f' -n {self.params["objects"]}'
		if self.params.get("branching"): cmd += f' -b {self.params["branching"]}'
		if self.params.get("depth"): cmd += f' -z {self.params["depth"]}'
		if self.params.get("bytesread"): cmd += f' -e {self.params["bytesread"]}'
		if self.params.get("byteswrite"): cmd += f' -w {self.params["byteswrite"]}'
		if self.params.get("iterations"): cmd += f' -i {self.params["iterations"]}'
		if self.params.get("itemsperdir"): cmd += f' -I {self.params["itemsperdir"]}'
		if self.params.get("directio"): cmd += f' --posix.odirect'
		if self.params.get("onlycreate"): cmd += f' -C'
		if self.params.get("onlystat"): cmd += f' -T'
		if self.params.get("onlyread"): cmd += f' -E'
		if self.params.get("onlyremove"): cmd += f' -r'
		if self.params.get("dironly"): cmd += f' -D'
		if self.params.get("fileonly"): cmd += f' -F'
		if self.params.get("uniquedir"): cmd += f' -u'
		if self.params.get("verbose"): cmd += f' {self.params["verbose"]}'
		if self.params.get("syncafterwrite"): cmd += f' -y'
		if self.params.get("extra_args"): cmd += f' {self.params["extra_args"]}'



		return cmd

	def start(self):
		
		cmd = self._cmd_builder()
		data_path = Path(f"{self.data_path_root}/mdtest")
		data_path.mkdir(parents=True, exist_ok=True)
		if self.params["pools"] != "default":	
			pool_stripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
			pool_overstripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

			set_stripe_process = subprocess.run(["bash", "-c", pool_stripe_cmd])

			if set_stripe_process.returncode != 0:
				set_overstripe_process = subprocess.run(["bash", "-c", pool_stripe_cmd])

			if self.params['DOM']:
				set_dom_cmd = f"lfs setstripe -E 64k -L mdt -p {self.params['pools']} {data_path}"
				set_dom_process = subprocess.run(["bash", "-c", set_dom_cmd])
		else:
			if self.params['DOM']:
				set_dom_cmd = f"lfs setstripe -E 64k -L mdt {data_path}"
				set_dom_process = subprocess.run(["bash", "-c", set_dom_cmd])

		cmd += f" -d {data_path}"

		with open(f"{self.log_path}/mdtest/{self.runid_base}/{self.fname}", "w") as log_file:

			process = subprocess.run(
			    ["bash", "-c", cmd],
			    stdin=subprocess.DEVNULL,
			    stdout=log_file,
			    stderr=log_file
			)


	def stop(self):
		print("stopping BM")
