"""Content-addressed archive controls independent of the evaluator implementation."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parent
SOURCE = ROOT/"repo/private-assurance/raw.zip"
def canonical(obj):
    return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(",",":")).encode()
def get(files,ref):
    raw=files[ref["path"]]
    assert len(raw)==ref["bytes"] and hashlib.sha256(raw).hexdigest()==ref["sha256"]
    return json.loads(raw)
def put(files,value):
    raw=canonical(value)
    digest=hashlib.sha256(raw).hexdigest()
    path=f"objects/{digest}.json"
    files[path]=raw
    return {"path":path,"sha256":digest,"bytes":len(raw)}
def snapshot():
    with zipfile.ZipFile(SOURCE) as z:
        return {name:z.read(name) for name in z.namelist()}
def save(files,name):
    out=ROOT/name
    out.mkdir(exist_ok=True)
    with zipfile.ZipFile(out/"raw.zip","w",compression=zipfile.ZIP_DEFLATED) as z:
        for n,b in files.items():
            z.writestr(n,b)

def missing_kernel_trace():
    files=snapshot()
    key="runs/clean/2001/purser.json"
    row=json.loads(files[key])
    p=get(files,row["artifacts"]["platform"])
    trace=p["treatment_trace"]
    assert len(get(files,trace)["original_events"])>0
    p["referenced_files"]=[ref for ref in p["referenced_files"] if ref!=trace]
    p["treatment_trace"]=None
    del files[trace["path"]]
    row["artifacts"]["platform"]=put(files,p)
    files[key]=canonical(row)
    save(files,"missing-kernel-trace")

def wrong_closure_receipt():
    files=snapshot()
    key="runs/primary/2001/purser.json"
    row=json.loads(files[key]); journal=get(files,row["artifacts"]["journal"])
    closes=[e["payload"] for e in journal if e["kind"]=="action" and e["payload"]["actor"]=="closure"]
    w3=next(p for p in closes if p["subject"]=="W3")
    w2=next(p for p in closes if p["subject"]=="W2")
    print("swapped receipts",w2["attempt"],w2["proposal"]["proposal_id"],w3["return"]["receipt"]["proposal_id"])
    oldref=w2["response_evidence"]
    w2["return"]["receipt"]=w3["return"]["receipt"]
    evidence=get(files,oldref); evidence["return"]=w2["return"]
    w2["response_evidence"]=put(files,evidence)
    row["artifacts"]["journal"]=put(files,journal)
    files[key]=canonical(row)
    save(files,"wrong-closure-receipt")

if __name__=="__main__":
    missing_kernel_trace()
    wrong_closure_receipt()
