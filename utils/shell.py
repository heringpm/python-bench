"""Shared helper for running (or, in dry-run mode, just printing) shell commands."""

import subprocess
from typing import Optional


class DryRunResult:
    """Stand-in for subprocess.CompletedProcess when a command is skipped."""

    def __init__(self):
        self.returncode = 0


def run_cmd(cmd: str, dry_run: bool = False, **kwargs):
    """
    Run `cmd` via bash -c, or print it if dry_run is True.

    Any kwargs are forwarded to subprocess.run (e.g. stdout, stderr, stdin).
    Returns a DryRunResult (returncode=0) when dry_run is True so callers
    that check `.returncode` don't need special-casing.
    """
    if dry_run:
        print(f"[DRY-RUN] {cmd}")
        return DryRunResult()
    return subprocess.run(["bash", "-c", cmd], **kwargs)


def popen_cmd(cmd: str, dry_run: bool = False, **kwargs) -> Optional[subprocess.Popen]:
    """
    Start `cmd` via bash -c with Popen, or print it if dry_run is True.

    Returns None when dry_run is True.
    """
    if dry_run:
        print(f"[DRY-RUN] {cmd}")
        return None
    return subprocess.Popen(["bash", "-c", cmd], **kwargs)
