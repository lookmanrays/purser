"""Kill after a true public close; recover with the redundant bare store unavailable and all packed objects intact."""
import json
import os
import signal
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"repo"))
from experiments.e1.artifacts import extract_tree,git,read_artifact
from experiments.e1.outcomes import LABELS
from experiments.e2 import measure,policy,suite

dirname=ROOT/"repo/private-pack-only"
unit=[("primary",2001,1,"loop")]
if sys.argv[1]=="die":
    old=policy.PlanArm.decide
    def die(arm,work,audit):
        r=old(arm,work,audit)
        if work=="W-notify" and arm.read()[work]["state"]=="closed":
            (ROOT/"pack-only-witness.json").write_text(json.dumps(arm.read()[work],indent=2)+"\n")
            os.kill(os.getpid(),signal.SIGKILL)
        return r
    with patch.object(policy.PlanArm,"decide",die):
        suite.collect_units(dirname,unit)
elif sys.argv[1]=="recover":
    bare=dirname/".attempt-primary-2001-loop/trial/private-objects"
    assert bare.is_dir()
    bare.rename(ROOT/"quarantined-bare-store")
    suite.collect_units(dirname,unit)
    with zipfile.ZipFile(dirname/"raw.zip") as z:
        row=json.loads(z.read("runs/primary/2001/loop.json"))
        journal=json.loads(read_artifact(z,row["artifacts"]["journal"]))
        candidates=json.loads(read_artifact(z,row["artifacts"]["candidates"]))
        p=json.loads(read_artifact(z,row["artifacts"]["platform"]))
        oid=p["exit_candidate"]["head"]
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)
            git(repo,"init","-q","--bare","--object-format=sha1")
            git(repo,"index-pack","--stdin",data=read_artifact(z,candidates[oid]["pack"]))
            git(repo,"fsck","--full","--no-dangling",oid)
            with extract_tree(repo,oid) as tree:
                old=measure.check(repo,oid,Path(tree),2001,1,1,[label for label in LABELS if label not in measure.REVISED])
                new=measure.check(repo,oid,Path(tree),2001,1,2,measure.REVISED)
        answer={"recovered_candidate":oid,"all_packed_candidates":len(candidates),
                "captured_oracles":sum(e["kind"]=="oracle" for e in journal),
                "independently_reexecuted_from_same_sealed_pack":{k:v["verdict"] for k,v in (old|new).items()},
                "recovery_issues":p["recovery_issues"]}
        (ROOT/"pack-only-reexecution.json").write_text(json.dumps(answer,indent=2)+"\n")
