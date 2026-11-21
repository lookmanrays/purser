"""Independent read-only observation of the frozen explain-work criterion."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,platform,shutil,sqlite3,subprocess,sys,sysconfig,tempfile
B='70097e0a2b933713d4fe91e6eddb5c2132951f89'; A='dfde81b3643da7f5924634d1391e972b5d679760'
def sha(data):return hashlib.sha256(data).hexdigest()
def enc(x):return json.dumps(x,ensure_ascii=False,separators=(',',':'),sort_keys=True).encode()
def H(tag,x):return sha(b'purser/'+tag.encode()+b'/v1\0'+enc(x))
def must(pred,what):
    if not pred:raise AssertionError(what)
def ro(p):
    return sqlite3.connect(Path(p).as_uri()+'?mode=ro',uri=True)
def original_events(p):
    with ro(p) as cx:return [data for (data,) in cx.execute('SELECT body FROM events ORDER BY revision')]
def copied(src,dst):
    with ro(src) as cx,sqlite3.connect(dst) as cy:cx.backup(cy)
def projection_sha(p):
    keys={'records':'id','receipts':'actor_id,proposal_id','edges':'kind,from_id,to_id'}
    with ro(p) as cx: projection={k:[json.loads(x[0]) for x in cx.execute('SELECT body FROM '+k+' ORDER BY '+order)] for k,order in keys.items()}
    return H('projection',projection)
repo,control,rerun,defs,out=map(lambda x:Path(x).resolve(),sys.argv[1:])
env={'PATH':'/usr/bin:/bin','LC_ALL':'C','TZ':'UTC'}
commands=[]
def run(args):
    r=subprocess.run(args,cwd=repo,env=env,capture_output=True,check=True,timeout=20)
    commands.append({'argv':args,'exit':r.returncode,'stdout_sha256':sha(r.stdout),'stderr_sha256':sha(r.stderr)})
    return r.stdout
def git(*args):return run(['git',*args])
def cli(db,*args):return json.loads(run([sys.executable,'-B','-m','purser','--db',str(db),*map(str,args)]))
frozen={'criterion-v1.json':'5db22ec09aa83be86a516400a411bd964e6f2967d42204b6aa8771344e56b097',
        'environment-v1.json':'990f0aed7d4d427fc420648304c03e1f965c873b04d691f63db7ef1cb2a69c49',
        'fixture-v1.json':'31ffc7e5e9a76fce881010e6febe6a6c41e66e92e01f3e5465856600ce30bb27'}
for name,want in frozen.items():
    for p in (defs/name,repo/'docs/evidence/M2/observer'/name):
        must(sha(p.read_bytes())==want,str(p))
e=json.loads((defs/'environment-v1.json').read_bytes())
must((platform.system(),platform.release(),platform.machine(),platform.python_version(),sqlite3.sqlite_version)==(e['system'],e['release'],e['machine'],e['python'],e['sqlite']),'environment change')
for name,file in {'python':Path(sys.executable).resolve(),'libpython':Path(sysconfig.get_config_var('LIBDIR'))/sysconfig.get_config_var('LDLIBRARY'),'git':Path(shutil.which('git')).resolve()}.items():
    must(sha(file.read_bytes())==e['binary_sha256'][name],name)
must(git('rev-parse','HEAD').decode().strip()==B and not git('status','--porcelain'),'checkout')
objs={}
for label,sha1 in [('A',A),('B',B)]:
    obj=git('cat-file','commit',sha1)
    must(hashlib.sha1(b'commit '+str(len(obj)).encode()+b'\0'+obj).hexdigest()==sha1,'Git commit identity')
    objs[label]={'commit':sha1,'tree':git('rev-parse',sha1+'^{tree}').decode().strip(),'commit_bytes_sha256':sha(obj)}
must(objs['A']['tree']!=objs['B']['tree'],'distinct actual Git trees')
metadata=json.loads((control/'observations.json').read_bytes())
fix=metadata['subjects']; C=fix['commitment'];W=fix['child'];root=fix['root'];O=fix['obligation'];asof=metadata['as_of']
events_raw=original_events(control/'controlled.sqlite3')
must(events_raw==original_events(rerun/'controlled.sqlite3'),'closed fixture creator process reproduced entire original event log')
events=[json.loads(x) for x in events_raw]
prev='0'*64;end=0;batch=None
for n,(raw,event) in enumerate(zip(events_raw,events),1):
    must(raw==enc(event) and event['revision']==n and event['prev_hash']==prev,'wire originals')
    if n>end:batch=event;end=n+event['batch_size']-1;must(event['batch_start']==n,'batch start')
    must(all(event[k]==batch[k] for k in ('batch_start','batch_size','actor_id','proposal_id','at')),'batch identity')
    prev=H('event',{k:v for k,v in event.items() if k!='hash'})
    must(event['hash']==prev,'event hash')
must(len(events)==end==37,'complete final batch')
receipts={}
for row in metadata['journal']:
    prop,bind,r=row['proposal'],row['binding'],row['receipt'];rid=(bind['actor_id'],prop['proposal_id'])
    physical=events[r['revision']-1]; data=physical['data']
    must(physical['type']=='ProposalReceipted' and data['actor_id']==rid[0] and data['proposal_id']==rid[1] and
         r['digest']==H('proposal',{'actor_id':rid[0],'proposal':prop})==data['digest'] and
         {k:v for k,v in r.items() if k!='revision'}==data,'proposal provenance')
    receipts[r['revision']]={'op':prop['ops'][0],'receipt':r}
must(receipts[31]['op']=={'op':'work.close','id':W} and receipts[31]['receipt']['code']=='closure_blocked' and
     receipts[31]['receipt']['disposition']=='rejected' and receipts[37]['op']=={'op':'work.close','id':W} and
     receipts[37]['receipt']['disposition']=='admitted','closure ordering and receipt')
must(receipts[30]['op']['run']=='Run-A' and receipts[30]['op']['verdict']=='pass' and receipts[35]['op']['run']=='Run-B' and receipts[35]['op']['verdict']=='pass','delayed old pass and subsequent current pass')
lineage={}
for label,sha1,gen,st in [('A',A,1,(17,19,27,29)),('B',B,2,(23,25,32,34))]:
    sourced=[events[n-1] for n in st]
    res,opened,observed,evidence=[r['data'] for r in sourced]
    must([x['type'] for x in sourced]==['ResultSubmitted','RunOpened','ObservationRecorded','EvidenceRecorded'],'typed original events')
    typed={'kind':'git.commit','algorithm':'sha1','digest':sha1}
    must(opened['work']==W and opened['commitment']==C and opened['obligation']==O and opened['criterion_version']==1 and
         opened['candidate']==res['candidate']==typed and opened['generation']==res['generation']==gen and
         opened['assignment']==res['assignment'] and opened['result']==res['id'] and opened['producer']==res['producer'] and
         opened['id']==observed['run']==evidence['run'] and observed['id']==evidence['observation'] and
         observed['observer']==opened['observer']==sourced[2]['actor_id'] and
         evidence['verifier']==opened['verifier']==sourced[3]['actor_id'] and
         res['producer'] not in (opened['observer'],opened['verifier']) and evidence['verdict']=='pass','complete trustworthy run and actor binding')
    file=control/('observation-'+label+'.json');bytes_=file.read_bytes();content=json.loads(bytes_)
    dig=sha(bytes_);store=control/'controlled.sqlite3.artifacts'
    must((store/dig).read_bytes()==bytes_ and observed['artifact_sha256']==dig and content['candidate']==typed,'artifact bytes')
    readme=git('show',sha1+':README.md')
    must(content['readme_sha256']==sha(readme) and content['first_line']==readme.splitlines()[0].decode()=='# Purser','observed Git feature')
    for named,field in [('definition.txt','definition_sha256'),('environment.json','environment_sha256')]:
        blob=(control/named).read_bytes(); must(sha(blob)==opened[field] and (store/sha(blob)).read_bytes()==blob,'nested fixture bytes')
    lineage[label]={'raw_event_revisions':st,'result':res,'run':opened,'observation':observed,'evidence':evidence}
checked={}
with tempfile.TemporaryDirectory(prefix='cold-copy-',dir=out.parent) as td:
    for phase,rev in [('missing',26),('late',31),('qualified',37)]:
        p=Path(td)/(phase+'.sqlite3');c=metadata['checkpoints'][phase];origin=control/(phase+'.sqlite3')
        copied(origin,p); raw=original_events(p)
        must(raw==events_raw[:rev]==original_events(rerun/(phase+'.sqlite3')),'snapshot chronology')
        ph=projection_sha(p)
        def trio():return {name:cli(p,*args,'--revision',rev,'--as-of',asof) for name,args in [('status',['status']),('child',['why',W]),('root',['why',root])]}
        before=trio();status=before['status'];answers=status['answers']
        must(status['revision']==rev and status['head_hash']==events[rev-1]['hash'] and
             status['projection_hash']==ph and status['as_of']==asof and set(answers)=={'q01','q02','q07','q08','q09','q10'},'bounded status, digest and continuity')
        part=next(x for x in answers['q02'] if x['work']==W);rootstate=next(x for x in answers['q02'] if x['work']==root)
        obligation=next(x for x in answers['q07'] if x['obligation']==O)
        reason=lambda work:next(x['reasons'] for x in answers['q08'] if x['work']==work)
        must(part['candidate']==metadata['B']=={'kind':'git.commit','algorithm':'sha1','digest':B} and
             part['generation']==2 and obligation['version']==1 and obligation['work']==W and rootstate['state']!='closed','current B, qualifier and unclosed root')
        if phase!='qualified':
            finding={'code':'missing_evidence','refs':[O]}
            must(part['state']!='closed' and obligation['state']=='open' and obligation['evidence']==[] and
                 finding in reason(W) and finding in before['child']['reasons'],'missing on B in status and why')
        else:
            waiting={'code':'awaiting_acceptance','refs':[root]}
            must(part['state']=='closed' and obligation['state']=='passed' and obligation['evidence']==['E-B'] and
                 waiting in reason(root) and waiting in before['root']['reasons'] and
                 any(x['commitment']==C and x['root']==root and waiting in x['missing'] for x in answers['q09']) and
                 {'action':'principal_accept','subject':root} in answers['q10'],'current evidence closes only child; explicit root acceptance needed')
        with sqlite3.connect(p) as cx:
            for name in ('records','receipts','edges'):cx.execute('DELETE FROM '+name)
        recovered=cli(p,'replay','--as-of',asof)
        after=trio()
        must(recovered==status and after==before and original_events(p)==raw and projection_sha(p)==ph,'cold rebuild in fresh independent processes')
        checked[phase]={'revision':rev,'head_hash':status['head_hash'],'projection_hash':ph,'status_and_why':before,
                        'replay_matched':True,'original_event_bytes_unchanged_after_cold_replay':True}
must(not any(x['type']=='RootAccepted' or x['type']=='WorkAccepted' or (x['type']=='WorkClosed' and x['data']['id']==root) for x in events),'root remains pending')
record={'schema':'purser.independent.m2-feature-observation.v1','verdict':'pass','observed_at':datetime.now(timezone.utc).isoformat(),
        'observer':'observer:independent-m2','subject':{'commitment':'C-m2-explain-work','work':'W-cold-reasons','obligation':'O-cold-reasons','criterion_version':1,'run':'V-real-cold-1','candidate':metadata['B']},
        'frozen_source_sha256':frozen,'environment_observed':e,'command_environment':env,'independent_checker_sha256':sha(Path(__file__).read_bytes()),
        'Git_objects':objs,'fixture_logical_artifacts_sha256':{f.name:sha(f.read_bytes()) for f in (control/'controlled.sqlite3.artifacts').iterdir() if f.is_file()},
        'controller_handoff_sha256':sha((control/'observations.json').read_bytes()),'controlled_subjects':fix,
        'fixture_regen':'Fresh offline fixture creator exited before the observer read the same 37 original event payloads from the delivered and regenerated databases.',
        'checkpoints':checked,'lineage':lineage,'late_close':receipts[31],'qualified_close':receipts[37],'commands':commands,
        'scope':'Independent candidate-specific observation of the frozen real development criterion. The fixture controller owns its synthetic setup. This observation makes no decision for the real commitment principal.'}
out.write_bytes((json.dumps(record,sort_keys=True,indent=2)+'\n').encode());print('PASS',sha(out.read_bytes()),len(out.read_bytes()),len(commands),{k:v['revision'] for k,v in checked.items()})
