import time
from itertools import product

class IORBenchmark:
	def __init__(self, params, mpi_path, ior_path):
		self.params = params
		self.mpi_path = mpi_path
		self.ior_path = ior_path


	def start(self):

		print("this is where IOR is meant to run!")
		time.sleep(5)


	def stop(self):
		print("stopping BM")