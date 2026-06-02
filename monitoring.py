

import subprocess

pdstat_script = "/work/scripts/SFA/pdstats_v11.py3"
vdstat_script = "/work/scripts/SFA/vdstats_v12.py3"
iostat_script = "/work/scripts/mark/iostats_aggregator_generic2025.sh"

class SFAMonitoring:

	def __init__(self, appliances: dict, log_path, runid_base, tool, fname):
		self.appliances = appliances
		self.log_path = log_path
		self.runid_base = runid_base
		self.tool = tool
		self.fname = fname
		self.processes = {}

	def start(self):

		# Start the monitoring scripts for each SFA and VMs involved
		if self.processes:
			print("Old Monitoring is still running. Clean up and try again!")
			exit	

		for appliance, vm in self.appliances.items():
			for stat in ("vdstats", "pdstats", "iostats"):
				stat_log_file = open(f"{self.log_path}/{self.tool}/{self.runid_base}/{self.fname}.{stat}.{appliance}", "w")
					
				if stat == "vdstats":
					print(f"starting {stat} on {appliance}")
					script = f"{vdstat_script} {appliance}-c1"
					host = f"{appliance}-c1"
				elif stat == "pdstats":
					print(f"starting {stat} on {appliance}")
					script = f"{pdstat_script} {appliance}-c0"
					host = f"{appliance}-c0"
				elif stat == "iostats":
					print(f"starting {stat} on {vm}")
					script = f'{iostat_script} "{vm}"'
					host = vm


				stat_process = subprocess.Popen(
					["bash", "-c", script],
					stdin=subprocess.DEVNULL,
					stdout=stat_log_file,
					stderr=stat_log_file
				)
				self.processes[appliance] = {
					"process": stat_process,
					"log_file": stat_log_file
				}


	def stop(self):
		if not self.processes:
			print("No monitors running.")
			return


		for appliance, data in self.processes.items():
			data["process"].terminate()
			data["process"].wait()
			data["log_file"].close()                     # important — flush and close the file
			print(f"Stopped {appliance}")

		print(f"Stopped {len(self.processes)} monitors.")
		self.processes = {}
