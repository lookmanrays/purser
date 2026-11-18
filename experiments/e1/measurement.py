"""Launch raw observations on supplied immutable candidate bytes."""
import json
import subprocess
import sys
from pathlib import Path

from .artifacts import REGISTRATION, encoded


def probe(root, *, cases=None, legacy=False):
    spec = {"root": str(root), "mode": "legacy" if legacy else "queue",
            "tests": str(REGISTRATION / "input/tests"), "cases": cases}
    result = subprocess.run([sys.executable, "-I", "-B", str(Path(__file__).with_name("probe.py"))],
                            input=encoded(spec), capture_output=True, timeout=5)
    if result.returncode:
        return {"process_exit": result.returncode, "stderr": result.stderr.decode(errors="replace")[-2000:]}
    return json.loads(result.stdout)
