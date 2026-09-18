import subprocess
from pathlib import Path

from utils.shell import run_cmd


class IO500Benchmark:
    """Drives io500 via its own io500.sh wrapper script (built via io500's
    prepare.sh, which statically links its own pinned ior/mdtest/pfind - it
    can't reuse externally-built ior/mdtest binaries). io500.sh manages its
    own MPI launch internally (its io500_mpirun/io500_mpiargs variables) and
    can optionally do its own directory setup/striping in its setup() hook,
    so tools.io500 should point at that wrapper script, not the raw io500
    binary. This class just generates the .ini file io500 reads its config
    from, then invokes io500_path <ini> directly."""

    def __init__(self, params, io500_path, data_path_root, log_path, runid_base, fname, dry_run=False):
        self.params = params
        self.io500_path = io500_path
        self.data_path_root = data_path_root
        self.log_path = log_path
        self.runid_base = runid_base
        self.fname = fname
        self.dry_run = dry_run

    def _bool(self, value) -> str:
        return "TRUE" if value else "FALSE"

    def _build_sections(self, data_path: Path, results_dir: Path) -> dict:
        p = self.params

        sections = {
            "global": {
                "datadir": str(data_path),
                "resultdir": str(results_dir),
                "timestamp-datadir": "FALSE",
                "timestamp-resultdir": "FALSE",
                "api": p.get("api", "POSIX"),
                "drop-caches": self._bool(p.get("drop_caches", 0)),
                "verbosity": p.get("verbosity", 1),
            },
            "debug": {
                "stonewall-time": p.get("stonewall_time", 300),
            },
            "ior-easy": {
                "transferSize": p.get("ior_easy_transfer_size", "2m"),
                "blockSize": p.get("ior_easy_block_size", "9920000m"),
                "filePerProc": self._bool(p.get("ior_easy_file_per_proc", 1)),
                "run": self._bool(p.get("run_ior_easy", 1)),
            },
            "mdtest-easy": {
                "n": p.get("mdtest_easy_n", 1000000),
                "run": self._bool(p.get("run_mdtest_easy", 1)),
            },
            "find-easy": {
                "run": self._bool(p.get("run_find_easy", 1)),
            },
            "ior-hard": {
                "segmentCount": p.get("ior_hard_segment_count", 10000000),
                "run": self._bool(p.get("run_ior_hard", 1)),
            },
            "mdtest-hard": {
                "n": p.get("mdtest_hard_n", 1000000),
                "run": self._bool(p.get("run_mdtest_hard", 1)),
            },
            "find": {
                "run": self._bool(p.get("run_find", 1)),
            },
        }

        ## Free-form escape hatch for any ini section/key not covered above,
        ## e.g. {"ior-hard": {"collective": "TRUE"}, "mdworkbench": {"run": "TRUE"}}.
        for section, kv in (p.get("extra_ini") or {}).items():
            sections.setdefault(section, {}).update(kv)

        return sections

    def _build_ini(self, sections: dict) -> str:
        lines = []
        for section, kv in sections.items():
            lines.append(f"[{section}]")
            for key, value in kv.items():
                lines.append(f"{key} = {value}")
            lines.append("")
        return "\n".join(lines)

    def run(self):
        results_dir = Path(f"{self.log_path}/io500/{self.runid_base}")

        ## EXTERNAL / PRE-BUILT INI FILE
        ## When remote_ini is set, skip all of the local striping + ini-section
        ## generation below entirely and just point io500 at the given ini path.
        if self.params.get("remote_ini"):
            ini_path = self.params.get("ini_path")
            if not ini_path:
                raise ValueError("io500: remote_ini is set but ini_path is empty")
            ini_path = Path(ini_path)

            if self.dry_run:
                print(f"[DRY-RUN] mkdir -p {results_dir}")
            else:
                results_dir.mkdir(parents=True, exist_ok=True)

            return self._launch(ini_path, results_dir)

        ## DATA PATH
        ## Striping/directory setup is left to io500.sh's own setup() hook
        ## rather than done here - this just computes the path so it can be
        ## written into the generated ini's [global] datadir.
        if self.params["pools"] != "default":
            data_path = Path(f"{self.data_path_root}/io500/{self.params['pools']}")
        else:
            data_path = Path(f"{self.data_path_root}/io500")

        ## GENERATE THE .ini FILE
        sections = self._build_sections(data_path, results_dir)
        ini_contents = self._build_ini(sections)
        ini_path = Path(f"{self.log_path}/io500/{self.runid_base}/{self.fname}.ini")

        if self.dry_run:
            print(f"[DRY-RUN] write io500 ini to {ini_path}:\n{ini_contents}")
        else:
            results_dir.mkdir(parents=True, exist_ok=True)
            with open(ini_path, "w") as f:
                f.write(ini_contents)

        self._launch(ini_path, results_dir)

    def _launch(self, ini_path: Path, results_dir: Path):
        ## io500.sh manages its own MPI launch internally (its
        ## io500_mpirun/io500_mpiargs variables), so it's invoked directly
        ## here rather than wrapped in our own mpirun call.
        cmd = f'{self.io500_path} {ini_path} --timestamp {self.runid_base}'

        if self.params.get("extra_args"):
            cmd += f' {self.params["extra_args"]}'

        if self.dry_run:
            run_cmd(cmd, dry_run=True)
        else:
            ## io500.sh runs can take a long time, so stream its output to
            ## the console live (in addition to the log file) rather than
            ## only printing a summary after it finishes.
            ##
            ## io500 uses '\r' (not '\n') for its in-place progress updates
            ## (e.g. "CMP ..."/"TIME: ..."), so we split on either '\r' or
            ## '\n' ourselves and echo each fragment with the same
            ## terminator - otherwise those never hit a newline and get
            ## buffered/concatenated into one unreadable line. The raw bytes
            ## are still written to the log file untouched.
            log_path = f"{self.log_path}/io500/{self.runid_base}/{self.fname}"
            with open(log_path, "wb") as log_file:
                process = subprocess.Popen(
                    ["bash", "-c", cmd],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=0,
                )
                buf = b""
                while True:
                    chunk = process.stdout.read(1)
                    if not chunk:
                        break
                    log_file.write(chunk)
                    if chunk in (b"\n", b"\r"):
                        print(f"       {buf.decode(errors='replace')}", end=chunk.decode(), flush=True)
                        buf = b""
                    else:
                        buf += chunk
                if buf:
                    print(f"       {buf.decode(errors='replace')}")
                process.wait()

    def stop(self):
        print("stopping BM")
