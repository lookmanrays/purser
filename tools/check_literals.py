#!/usr/bin/env python3
"""List string literals in Go test files that appear in no data file.

Usage: python3 -I tools/check_literals.py <test files...> [--data <path>...]

Every quoted string in the given files is checked against the text of the
data paths (default: testdata, contracts, reference/python/docs/contracts,
plus each test file's own package testdata directory). Literals that look
like data (hex digests, IDs with a prefix, timestamps) and are missing from
all of them are printed first under "SUSPECT"; every other missing literal
follows under "review", since it may be a subtest name or an input the test
builds itself. Exit status 1 when any SUSPECT literal is found.
"""
import pathlib
import re
import sys

LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
DATA_LIKE = re.compile(r"[0-9a-f]{16,}|^[a-zA-Z]{1,3}:[^\s]+$|\d{4}-\d{2}-\d{2}T")
DEFAULT_DATA = ["testdata", "contracts", "reference/python/docs/contracts"]


def read_tree(path):
    p = pathlib.Path(path)
    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()]
    out = []
    for f in files:
        try:
            out.append(f.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, OSError):
            continue
    return "\n".join(out)


def main(argv):
    if "--data" in argv:
        i = argv.index("--data")
        tests, data_paths = argv[:i], argv[i + 1:]
    else:
        tests, data_paths = argv, list(DEFAULT_DATA)
    if not tests:
        print(__doc__.strip())
        return 2
    data_paths += sorted({str(pathlib.Path(t).parent / "testdata") for t in tests})
    corpus = "\n".join(read_tree(d) for d in data_paths if pathlib.Path(d).exists())
    suspect = False
    for t in tests:
        src = pathlib.Path(t).read_text(encoding="utf-8")
        missing = sorted({s for s in LITERAL.findall(src) if len(s) >= 3 and s not in corpus})
        bad = [s for s in missing if DATA_LIKE.search(s)]
        rest = [s for s in missing if s not in bad]
        print(f"== {t}")
        for s in bad:
            print(f"  SUSPECT {s}")
        for s in rest:
            print(f"  review  {s}")
        suspect = suspect or bool(bad)
    return 1 if suspect else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
