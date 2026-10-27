#!/usr/bin/env python3
"""benchprof CLI entry point.

    benchprof run --hosts client[01-04] --profile full --label "skpi+fscrypt" -- <elbencho args>
    benchprof report runs/<id>
    benchprof compare runs/<idA> runs/<idB>
    benchprof flame runs/<id> --node client04 --from 70 --to 85

Milestone 1 implements `run` (light + full plumbing, full profile's perf
output isn't parsed yet). `report`, `compare`, and `flame` are stubbed with
a clear "not implemented yet" message so the CLI surface matches the final
shape from the start.
"""

import argparse
import sys

from benchprof.config import BenchprofConfig
from benchprof.collect import resolve_run_hosts, run_benchmark


def _split_passthrough(argv):
    """Split argv on the first bare "--", like elbencho/mpirun wrappers do:
    everything after it is passed through verbatim as the elbencho args."""
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], " ".join(argv[i + 1:])
    return argv, ""


def cmd_run(args, elbencho_args: str) -> int:
    config = BenchprofConfig.load(args.config)
    hosts = resolve_run_hosts(config, args.hosts)
    if not hosts:
        print("benchprof run: no hosts resolved (check --hosts or machine_file)", file=sys.stderr)
        return 2
    if not elbencho_args:
        print("benchprof run: no elbencho args given after '--'", file=sys.stderr)
        return 2
    run_benchmark(
        config=config,
        hosts=hosts,
        label=args.label or "",
        profile=args.profile,
        perf_freq=args.perf_freq,
        elbencho_args=elbencho_args,
        dry_run=args.dry_run,
    )
    return 0


def cmd_report(args) -> int:
    from benchprof import report
    report.build(args.run_dir)
    return 0


def cmd_compare(args) -> int:
    print("benchprof compare: not implemented yet (milestone 5)", file=sys.stderr)
    return 1


def cmd_flame(args) -> int:
    print("benchprof flame: not implemented yet (milestone 3/6)", file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchprof")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run an elbencho benchmark with profiling collectors")
    p_run.add_argument("--config", "-c", default="./benchprof.json", help="Path to benchprof config JSON")
    p_run.add_argument("--hosts", default=None, help='pdsh-style host list, e.g. "client[01-04]"')
    p_run.add_argument("--profile", choices=["light", "full"], default="light")
    p_run.add_argument("--perf-freq", type=int, default=99)
    p_run.add_argument("--label", default=None, help='Human label, e.g. "skpi+fscrypt"')
    p_run.add_argument("--dry-run", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="Build report.html / report.xlsx from a run directory")
    p_report.add_argument("run_dir")
    p_report.set_defaults(func=cmd_report)

    p_compare = sub.add_parser("compare", help="Compare two run directories")
    p_compare.add_argument("run_dir_a")
    p_compare.add_argument("run_dir_b")
    p_compare.set_defaults(func=cmd_compare)

    p_flame = sub.add_parser("flame", help="Generate a flame graph for a time window")
    p_flame.add_argument("run_dir")
    p_flame.add_argument("--node", required=True)
    p_flame.add_argument("--from", dest="from_s", type=float, required=True)
    p_flame.add_argument("--to", dest="to_s", type=float, required=True)
    p_flame.set_defaults(func=cmd_flame)

    return parser


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    front, elbencho_args = _split_passthrough(argv)
    parser = build_parser()
    args = parser.parse_args(front)
    if args.command == "run":
        return cmd_run(args, elbencho_args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
