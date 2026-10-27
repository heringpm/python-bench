#!/usr/bin/env python3
"""Thin entry point for the benchprof CLI, run from the repo root so
`benchprof.benchprof.*` (the elbencho run profiler package, under
benchprof/benchprof/) can import the repo's existing utils.hosts /
utils.shell helpers alongside its own modules.

Usage mirrors profiling.py / main.py:
    python3 benchprof.py run --hosts client[01-04] --profile light -- <elbencho args>
    python3 benchprof.py report runs/<id>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "benchprof"))

from benchprof.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
