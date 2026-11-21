"""Use actual plan declarations on old Git W2 between the public endpoints of a post-change plan operation."""
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"repo"))
from experiments.e2 import policy,suite

original=policy.PlanArm.revise
observed=[]
def outside_read(arm, label):
    result=subprocess.check_output([sys.executable,"-I","-B","-c",
        "import json,sys;print(json.dumps(json.load(open(sys.argv[1]))['W2']))",str(arm.file)])
    row=json.loads(result)
    observed.append({"label":label,"state":row["state"],"oid":row["candidate"],"premise":row["premise"]["id"]})
def compound(arm,message):
    oid=arm.context("W2")["candidate"]
    outside_read(arm,"incoming already delivered")
    arm.decide("W2",{"missing":[]})
    outside_read(arm,"actual old candidate declared closed")
    arm.submit("W2",oid)
    outside_read(arm,"old candidate returned to submitted")
    return original(arm,message)
with patch.object(policy.PlanArm,"revise",compound):
    suite.collect_units(ROOT/"repo/private-badclosure",[("primary",2001,1,"loop")])
(ROOT/"badclosure-cold-observations.json").write_text(json.dumps(observed,indent=2)+"\n")
