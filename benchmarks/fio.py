import time
import os
import subprocess
import threading
from itertools import product
from pathlib import Path

class FIOBenchmark:
	def __init__(self, params, fio_path, data_path_root, log_path, runid_base, fname, machinefile, deletefiles):
		self.params = params
		self.fio_path = fio_path
		self.data_path_root = data_path_root
		self.log_path = log_path
		self.runid_base = runid_base
		self.fname = fname
		self.machinefile = machinefile
		self.deletefiles = deletefiles
		self.fio_job_path = Path("./benchmarks/fio_jobs")

	def _start_fio_server(self):
		start_server_cmd = f"clush --machinefile {self.machinefile} {self.fio_path} --server --daemonize=/tmp/fio.pid"
		start_server_process = subprocess.run(["bash", "-c", start_server_cmd])


	def _stop_fio_server(self):
		stop_server_cmd = f"clush  --machinefile {self.machinefile} killall fio"
		stop_server_process = subprocess.run(["bash", "-c", stop_server_cmd])


	def run(self):

		self._start_fio_server()
		try:

			### Build out needed params

			## DATA POOLS
			if self.params["pools"] != "default":
				data_path = Path(f"{self.data_path_root}/fio/{self.params['pools']}")
				data_path.mkdir(parents=True, exist_ok=True)
				pool_stripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -c {self.params['stripecount']} {data_path}"
				pool_overstripe_cmd = f"lfs setstripe -p {self.params['pools']} -S {self.params['stripesize']} -C {self.params['stripecount']} {data_path}"

				set_stripe_process = subprocess.run(["bash", "-c", pool_stripe_cmd])

				if set_stripe_process.returncode != 0:
					set_overstripe_process = subprocess.run(["bash", "-c", pool_stripe_cmd])

			else:
				data_path = Path(f"{self.data_path_root}/fio")
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

				if client_count > line_count:
					raise ValueError(f"ERROR - Not enough clients in machinefile! Requested {client_count}, only {line_count} available.")
				elif client_count == line_count:
					# use the whole machinefile as-is
					hosts_var = f"--client {self.machinefile}"
				else:
					# slice to the needed count via process substitution
					hosts_var = f"--client <(head -{client_count} {self.machinefile})"

			with open(f"{self.log_path}/fio/{self.runid_base}/{self.fname}", "w") as log_file:

				cmd = (
					f'DIRECTIO={self.params["directio"]} IOENGINE={self.params["ioengine"]} '
					f'READWRITE={self.params["operation"]} BLOCKSIZE={self.params["blocksize"]} '
					f'IODEPTH={self.params["iodepth"]} FILESIZE={self.params["filesize"]} '
					f'NUMJOBS={self.params["ppn"]} DIRECTORY={data_path} '
				)

				if self.params["operation"] in ("rw", "randrw"):
					cmd += f' RWMIXREAD={self.params["rwmixread"]}'
				elif self.params["operation"] == "write":
					cmd += f' RWMIXREAD=0'
				elif self.params["operation"] == "read":
					cmd += f' RWMIXREAD=100'

				if self.params["timebased"]:
					job_file = f"{self.fio_job_path}/fio_timebased.job"
					cmd += f' RUNTIME={self.params["runtime"]}s '

				else :
					job_file = f"{self.fio_job_path}/fio.job"

				cmd += f' {self.fio_path} {hosts_var} {job_file}'
				

				process = subprocess.run(
				    ["bash", "-c", cmd],
				    stdin=subprocess.DEVNULL,
				    stdout=log_file,
				    stderr=log_file
				)

			with open(f"{self.log_path}/fio/{self.runid_base}/{self.fname}", "r") as f:
				for line in f:
					line = line.strip()

					# main BW/IOPS summary line
					# "  write: IOPS=8756, BW=8757Mi" or "  read: IOPS=12.5k, BW=12.2Gi"
					if line.startswith("read:") or line.startswith("write:"):
						# split on space and comma to get the parts
						parts = line.replace(",", " ").split()
						operation = parts[0].rstrip(":")
						iops = parts[1].split("=")[1]
						bw   = parts[2].split("=")[1]
						print(f"       {operation} -- IOPS: {iops}  BW: {bw}")

					# aggregate bw line with avg
					# "bw (  MiB/s): min=  512, max=30108, per=100.00%, avg=12615.26"
					elif line.startswith("bw"):
						parts = line.replace(",", " ").split()
						avg_bw = [p.split("=")[1] for p in parts if p.startswith("avg=")][0]
						print(f"       Avg BW: {avg_bw} MiB/s")

					# aggregate iops line with avg
					# "iops        : min=  512, max=30102, avg=12614.85"
					elif line.startswith("iops"):
						parts = line.replace(",", " ").split()
						avg_iops = [p.split("=")[1] for p in parts if p.startswith("avg=")][0]
						print(f"       Avg IOPS: {avg_iops}")


			if self.params["operation"] in ("read", "randread"):
				## Delete previous run data before running write if 'deletefiles' is true
				if self.deletefiles:
					print("Cleaning up datapath from previous runs...")
					deletefiles_cmd = f"rm -rf {data_path}/*"
					deletefiles_process = subprocess.run(["bash", "-c", deletefiles_cmd])

		finally:
			self._stop_fio_server()
