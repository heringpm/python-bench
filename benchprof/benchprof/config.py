"""Config loading for benchprof, mirroring the profiling.json convention
used by profiling.py: a single top-level JSON object (not a sweep list)
with the knobs a single `benchprof run` invocation needs.

Example (see benchprof.template.json at the repo root):

    {
      "run_dir": "/work/home/phering/benchprof_runs",
      "machine_file": "./machinefile.16",
      "elbencho_path": "/work/bin/benchmark_bin/elbencho",
      "agent_path": "./benchprof/agent/benchprof-agent",
      "remote_run_dir": "/tmp/benchprof",
      "use_clush": true
    }

`run_dir` is where the controller assembles runs/<run-id>/ on the local
(controller) disk - made explicit in config rather than hardcoded, since
where this should live depends on the setup (local disk vs. a Lustre dir
that isn't under test).
"""

import json
from pathlib import Path
from typing import Union


REQUIRED_KEYS = ("run_dir", "machine_file", "elbencho_path")


class BenchprofConfig:
    def __init__(self, data: dict):
        missing = [k for k in REQUIRED_KEYS if k not in data]
        if missing:
            raise ValueError(f"benchprof config missing required key(s): {', '.join(missing)}")
        self.run_dir = data["run_dir"]
        self.machine_file = data["machine_file"]
        self.elbencho_path = data["elbencho_path"]
        self.agent_path = data.get("agent_path", "./benchprof/agent/benchprof-agent")
        self.remote_run_dir = data.get("remote_run_dir", "/tmp/benchprof")
        self.use_clush = bool(data.get("use_clush", True))
        self.raw = data

    @classmethod
    def load(cls, path: Union[str, Path]) -> "BenchprofConfig":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("benchprof config file must contain a single JSON object.")
        return cls(data)
