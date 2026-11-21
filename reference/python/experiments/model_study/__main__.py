"""Prospective registration, collection and independent offline reporting."""

import argparse
import sys
from pathlib import Path

from experiments.e1.report import parse
from experiments.model_units import validate_unit

from .registration import registration


def unit(value):
    try:
        experiment, track, seed, arm = value.split("/")
        result = experiment, track, int(seed), arm
        validate_unit(result)
        return result
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a frozen experiment/track/seed/arm") from error


def main():
    parser = argparse.ArgumentParser(prog="python -m experiments.model_study")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("directory", type=Path)
    freeze.add_argument("--conditions", type=Path, required=True)
    freeze.add_argument("--population", choices=("model", "canned_control", "disabled_control"), required=True)
    freeze.add_argument("--budget", type=Path, help="previously enrolled study budget")
    freeze.add_argument("--replicate", type=int, default=1)
    freeze.add_argument("--reason")
    collect = commands.add_parser("collect")
    collect.add_argument("directory", type=Path)
    collect.add_argument("--unit", type=unit, action="append")
    collect.add_argument("--wasi-archives", type=Path)
    report = commands.add_parser("report")
    report.add_argument("directory", type=Path, nargs="?", help="omit to display uncollected original coverage")
    args = parser.parse_args()
    if args.command == "freeze":
        if args.population != "disabled_control" and args.budget is None:
            parser.error("register the existing spending database with --budget")
        registration(args.directory, parse(args.conditions.read_bytes()), population=args.population,
                     budget_path=args.budget, replicate=args.replicate, reason=args.reason)
    elif args.command == "collect":
        from .collector import collect
        collect(args.directory, units=args.unit, runtime_directory=args.wasi_archives)
    else:
        from .report import inspect, render
        sys.stdout.write(render(*inspect(args.directory)) if args.directory else render())


if __name__ == "__main__":
    main()
