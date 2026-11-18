import argparse
import json
from pathlib import Path

from .report import render, verify_candidates
from .suite import collect, freeze_series


def main():
    parser = argparse.ArgumentParser(description="Preregister, collect and reconstruct the scripted E1 series.")
    parser.add_argument("command", choices=("freeze", "collect", "report", "verify"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "freeze":
        freeze_series(args.directory)
    elif args.command == "collect":
        collect(args.directory)
    elif args.command == "verify":
        print(json.dumps(verify_candidates(args.directory), sort_keys=True))
    else:
        print(render(args.directory), end="")


if __name__ == "__main__":
    main()
