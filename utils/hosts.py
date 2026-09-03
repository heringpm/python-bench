"""Shared helper for resolving client hosts from a machinefile."""

from typing import List, NamedTuple


class ResolvedHosts(NamedTuple):
    hosts: List[str]        # first `client_count` hosts from the machinefile
    total_available: int    # total number of hosts in the machinefile


def resolve_hosts(machinefile: str, client_count: int) -> ResolvedHosts:
    """
    Read a machinefile and return the first `client_count` hosts, along with
    the total number of hosts available in the file.

    Raises ValueError if the machinefile doesn't contain enough hosts to
    satisfy the requested client count.
    """
    with open(machinefile, "r") as f:
        lines = f.readlines()

    line_count = len(lines)
    if client_count > line_count:
        raise ValueError(
            f"ERROR - Not enough clients in machinefile! Requested {client_count}, only {line_count} available."
        )

    hosts = [line.strip() for line in lines[:client_count]]
    return ResolvedHosts(hosts=hosts, total_available=line_count)
