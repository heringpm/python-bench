import time
import os
import subprocess
import threading
from itertools import product
from pathlib import Path

class IORBenchmark:
	def __init__(self, params, mpirun_path, mpi_conf, ior_path, data_path_root, log_path, runid_base, fname, machinefile):
		self.params = params
		self.mpirun_path = mpirun_path
		self.mpi_conf = mpi_conf
		self.ior_path = ior_path
		self.data_path_root = data_path_root
		self.log_path = log_path
		self.runid_base = runid_base
		self.fname = fname
		self.machinefile = machinefile


	

	def start(self):


		### build out needed params
		## OPERATION
		if self.params["operation"] == "write":
			operation = "-w"
		elif self.params["operation"] == "read":
			operation = "-r"

		## FILE PER PROC
		if self.params["fileperproc"] == 1:
			fileperproc = "-F"

		## DIRECT IO
		if self.params["directio"] == 1:
			directio = "--posix.odirect"

		data_path = Path(f"{self.data_path_root}/ior")
		data_path.mkdir(parents=True, exist_ok=True)

		log_file = open(f"{self.log_path}/ior/{self.runid_base}/{self.fname}.ior.log", "w")

		cmd = (
			f'{self.mpirun_path} --machinefile {self.machinefile} '
			f'{self.mpi_conf} --np {self.params["ppn"]} '
			f'{self.ior_path} -a {self.params["api"]} -v -d 1 '
			f'-b {self.params["blocksize"]} -t {self.params["xfersize"]} '
			f'{operation} {fileperproc} -k -o {data_path}/f {directio}'

		)

		print("Starting IOR benchmark!")
		print(cmd)
		#process = subprocess.Popen(
		#    ["bash", "-c", cmd],
		#    stdin=subprocess.DEVNULL,
		#    stdout=log_file,
		#    stderr=log_file
		#)



	def stop(self):
		print("stopping BM")