"""Read-only independent scripted E1 archive audit. Never imports or runs the experiment/arm."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from zipfile import ZipFile
from collections import Counter, defaultdict
from witness import DEFINITION, FIXTURE, candidate_tree, git

HOME_DIR=Path(__file__).resolve().parent
SOURCE=Path('/Users/lookman/.codex/worktrees/purser-delivery/e1-qualified-replication')
SERIES=SOURCE/'experiments/results/e1-scripted-v2'
REG='0338c47968ad757e4b70dcc9fcd92e6cd5ae6247'
CAND='e3a155633058cd20274ce5e9cfaca224b41f5b5b'
def sha(x):return hashlib.sha256(x).hexdigest()
def encoded(d):return json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()
def pos(row):
 oid=row['candidate']
 return (row['state'],oid['digest'] if isinstance(oid,dict) else oid)
def truth():
 rawblob=(SERIES/'raw.zip').read_bytes(); raw_sha=sha(rawblob)
 reg=git(SOURCE,['show',REG+':experiments/results/e1-scripted-v2/freeze.json'])
 assert git(SOURCE,['rev-parse',REG+'^']).decode().strip()==CAND
 assert (SERIES/'freeze.json').read_bytes()==reg
 manifest=json.loads((FIXTURE/'manifest.json').read_bytes())
 start=manifest['fixture']['commit_sha1']
 arms=manifest['units']['arm_order']
 e=manifest['experiments']['E1']
 expected=[(track,seed,arm) for track,seeds in [('primary',e['seeds']),('clean',[e['clean_seed']])] for seed in seeds for arm in arms[(seed-1001)%3:] + arms[:(seed-1001)%3]]
 report=(SERIES/'report.md').read_text()
 data={};pairs=[];start_keys=set();actual_start_order=[];raws=[];entries={};seed_oids=set();observation_count=0
 with ZipFile(SERIES/'raw.zip') as z, tempfile.TemporaryDirectory() as tmp:
  assert z.testzip() is None and z.read('freeze.json')==reg
  pin=json.loads(reg)
  assert pin['source_commit']==CAND and pin['replicate']==2
  assert all(pin[ref]==sha((FIXTURE/n).read_bytes()) for ref,n in [('manifest_sha256','manifest.json'),('raw_schema_sha256','raw.schema.json'),('protocol_sha256','protocol.md')])
  assert all(sha(git(SOURCE,['show',CAND+':'+f]))==h for f,h in pin['source_files'].items())
  def resolve(ref):
   body=z.read(ref['path']);assert len(body)==ref['bytes'] and sha(body)==ref['sha256'];return body
  def item(ref):return json.loads(resolve(ref))
  names=z.namelist();assert len(names)==len(set(names))
  for key in names:
   if key.startswith('objects/'):
    assert sha(z.read(key))==Path(key).stem
   if key.startswith('started/'):
    m=json.loads(z.read(key));assert m['rank']==m['seed']-1000
    ident=(m['track'],m['seed'],m['arm'])
    assert key=='started/%s/%s/%s.json'%ident
    actual_start_order.append(ident)
    start_keys.add(ident)
   if not key.startswith('runs/'):continue
   r=json.loads(z.read(key));assert (r['inputs']['worktree_head'],r['replicate'],r['experiment'],r['mode'])==(CAND,2,'E1','scripted')
   ident=(r['track'],r['seed'],r['arm'])
   assert key=='runs/%s/%s/%s.json'%ident and ident in start_keys
   raws.append((ident,r));assert r['validity']=='complete' and r['terminal']=='done'
   assert r['artifacts']['model'] is None and all(r['cost'][n]==0 for n in ('model_calls','input_tokens','output_tokens','spend_usd_micros'))
   ca=item(r['artifacts']['candidates'])
   for oid,v in ca.items():
    assert v['start']==start
    if oid in entries:assert v==entries[oid]
    entries[oid]=v;seed_oids.add((r['seed'],oid))
  assert actual_start_order==expected and [k for k,_ in raws]==expected
  repo=Path(tmp)/'git';subprocess.run(['git','init','-q',str(repo)],check=True)
  for oid,entry in entries.items():
   git(repo,['index-pack','--strict','--stdin'],resolve(entry['pack']))
   assert git(repo,['show','-s','--format=%T %P',oid]).decode().split()==[entry['tree']]+([entry['parent']] if entry['parent'] else [])
  assert git(repo,['rev-parse',start+'^{tree}']).decode().strip()==DEFINITION['tree_sha1']
  for f in DEFINITION['files']: assert sha(git(repo,['cat-file','blob',start+':'+f['path']]))==f['sha256']
  orig=candidate_tree(repo,start);allowed=set(DEFINITION['public_paths'])
  tests={};env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(repo))
  for oid,entry in sorted(entries.items()):
   tree=candidate_tree(repo,oid);scope=all(tree.get(p)==orig.get(p) for p in (set(tree)|set(orig))-allowed) and all(tree.get(p,(None,))[0]=='100644' for p in allowed)
   git(repo,['checkout','-q','--detach',oid])
   public=subprocess.run([sys.executable,'-B','-m','unittest','discover','-s',str(FIXTURE/'input/tests')],cwd=repo,env=env,capture_output=True)
   for seed,o in sorted(seed_oids):
    if o!=oid:continue
    cmd=[sys.executable,'-B',str(HOME_DIR/'witness.py'),'probe',str(seed)]
    p=subprocess.run(cmd,cwd=repo,env=env,capture_output=True,check=True,text=True)
    tests[seed,oid]={**json.loads(p.stdout),'legacy/compat':public.returncode==0,'scope/paths':scope}
  for ident,r in raws:
   track,seed,arm=ident;j=item(r['artifacts']['journal']);platform=item(r['artifacts']['platform']);trace=item(platform['treatment_trace'])
   assert [x['seq'] for x in j]==list(range(1,len(j)+1))
   assert len([x for x in j if x['kind']=='terminal'])==1 and j[-1]['kind']=='terminal'
   assert platform['participant_exit']['reason']==platform['budget_reason']==j[-1]['payload']['end_reason']=='done'
   assert r['cost']['elapsed_ns']==platform['end_monotonic_ns']-platform['start_monotonic_ns']
   actions=[x['payload'] for x in j if x['kind']=='action']
   assert [a['attempt'] for a in actions]==list(range(1,r['cost']['steps']+1)) and r['cost']['steps']<=96 and r['cost']['elapsed_ns']<=60*10**9
   result={'first':None,'closures':[],'cost':{'steps':len(actions),'elapsed_ns':r['cost']['elapsed_ns']}}
   fs=[x for x in j if x['kind']=='fault'];target=None
   if track=='clean':assert not fs
   else:
    assert len(fs)==1;f=fs[0];rank0=seed-1001
    variant=('skipped_retry_check','outside_scope','unchanged_module')[rank0%3]
    target=('W2/retry','scope/paths',['W1/preferences','W2/retry','W3/delivery'][(rank0//3)%3])[rank0%3]
    fp=f['payload'];assert (fp['intervention'],fp['target'])==(variant,target)
    fault_oid=fp['candidate'];assert not tests[seed,fault_oid][target]
    result['fault']=[target,fault_oid]
   if arm=='purser':
    original=[json.loads(s) for s in trace['original_events']]
    assert trace['snapshot']['revision']==len(original)
    final_record=next(t['fields'] for t in trace['snapshot']['projection']['records'] if t['id']=='W-notify')
   else:final_record=trace['plan']['W-notify']
   last=item(platform['initial_public']['view'])
   first_release=None;target_exposed=False;repair=False;last_ck=platform['initial_public']
   checks={}
   for ev in j:
    p=ev['payload']
    if ev['kind']=='oracle':
     identity=(p['candidate'],p['criterion']);assert identity not in checks;checks[identity]=p['verdict']
     assert (p['verdict']=='pass')==tests[seed,p['candidate']][p['criterion']]
     observation_count+=1
    if ev['kind']!='action':continue
    assert p['before']==last_ck
    before=item(p['before']['view']);after=item(p['after']['view']);assert before==last
    if arm=='purser':
     for c in ('before','after'):assert original[p[c]['revision']-1]['hash']==p[c]['head_hash']
    else:
     assert sha(encoded(before))==p['before']['plan_sha256'] and sha(encoded(after))==p['after']['plan_sha256']
    released=item(p['response_evidence'])
    assert released=={k:p[k] for k in ('attempt','actor','subject','candidate','return')}
    reply=released['return'];subject=p['subject'];who=p['actor']
    if track=='primary' and ev['seq']>f['seq']:
     if p['action']=='submit' and subject==fp['work'] and p['candidate']==fault_oid and pos(after[subject])==('submitted',fault_oid):target_exposed=True
     if target_exposed and p['action']=='edit' and p['input'].get('repair') and subject in (fp['work'],'W-notify'):
      if reply.get('edited')==subject or reply.get('removed'):repair=True
     if target_exposed and not repair and first_release is None:
      labels=[]
      if who=='plan_reviewer':labels+=reply['missing']
      if who in ('closure','independent_verifier'):labels+=reply.get('diagnosis',[])
      if who=='executor' and p['action']=='context':
       labels.extend(l for l,v in reply.get('verified',{}).items() if v in ('fail','inconclusive'))
       labels.extend(o['title'] for o in reply.get('obligations',[]) if o.get('state') in ('failed','inconclusive','conflict'))
      if labels:
       oid=p['candidate'];assert pos(before[subject])==('submitted',oid)
       assert not tests[seed,oid][target]; git(repo,['merge-base','--is-ancestor',fault_oid,oid])
       recorded=[e for e in j if e['kind']=='public_state' and e['payload']['subject']==subject and e['payload'].get('source_action')==p['attempt']]
       assert len(recorded)==1 and sorted(recorded[0]['payload']['diagnosis'])==sorted(set(labels))
       first_release=[p['attempt'],subject,oid,sorted(set(labels))];result['first']=first_release
    old=pos(before['W-notify']);new=pos(after['W-notify'])
    if new[0] in ('done','closed') and (old[0] not in ('done','closed') or old[1]!=new[1]):
     oid=new[1]; corresponding=[e['payload'] for e in j if e['kind']=='public_state' and e['payload']['subject']=='W-notify' and e['payload'].get('source_action')==p['attempt']]
     assert any(s['candidate']==oid and s['from']==old[0] and s['to']==new[0] for s in corresponding)
     if arm=='purser':
      slice_=original[p['before']['revision']:p['after']['revision']]
      assert any(o['type']=='WorkClosed' and o['data']['id']=='W-notify' for o in slice_)
     result['closures'].append([p['attempt'],oid,all(tests[seed,oid].values())])
    last=after;last_ck=p['after']
   assert last_ck==platform['final_public'] and pos(final_record)==pos(last['W-notify'])
   assert len(result['closures'])==1 and result['closures'][0][2] and result['closures'][0][1]==pos(final_record)[1]
   if target:
    assert first_release
    roll={'false_parent_closure':any(not right for _,_,right in result['closures']),
          'correct_parent_closure':any(right for _,_,right in result['closures']),
          'missing_named':target in first_release[3],
          'extra_diagnoses':len(set(first_release[3])-{target}),
          'caught':target in first_release[3] and all(tests[seed,oid][target] for _,oid,_ in result['closures'])}
    assert r['score']==roll
    result['score']=roll
   else:assert r['score'] is None
   data[ident]=result
  counts={}
  fields=['false_parent_closure','missing_named','caught','correct_parent_closure','extra_diagnoses']
  for arm in arms:
   present=[d for (t,s,a),d in data.items() if t=='primary' and a==arm];n=len(present)
   counts[arm]={f:sum(d['score'][f] for d in present) for f in fields};assert n==20
   expected_row=f"| {arm} | {n} | 0 | 0 | "+' | '.join(f"{counts[arm][f]}/{n}" for f in fields)+' |'
   assert expected_row in report
  for s in e['seeds']:
   p=data['primary',s,'purser'];values=[]
   for arm in ('loop','loop_equal_verifier'):
    q=data['primary',s,arm]
    diff=[int(p['score'][k])-int(q['score'][k]) for k in ('false_parent_closure','caught')]+[p['cost']['steps']-q['cost']['steps'],(p['cost']['elapsed_ns']-q['cost']['elapsed_ns'])/10**9]
    values.append(f'{diff[0]:+d} / {diff[1]:+d} / {diff[2]:+d} / {diff[3]:+.3f} s')
   line=f'| {s} | '+' | '.join(values)+' |';assert line in report
   pairs.append(line)
  assert 'absolute E1 threshold for Purser was **met**' in report and 'original no-advantage threshold for the plain loop was **met**' in report
  clean={a:data['clean',1001,a]['cost'] for a in arms}
  assert {a:v['steps'] for a,v in clean.items()}=={'loop':24,'purser':42,'loop_equal_verifier':42}
  assert 'Purser used 42 clean steps and the plain loop used 24' in report
  assert raw_sha==sha((SERIES/'raw.zip').read_bytes())
  return {'verdict':'PASS','registration':REG,'source_candidate':CAND,'raw_sha256':raw_sha,'freeze_sha256':sha(reg),'report_sha256':sha((SERIES/'report.md').read_bytes()),'cases':len(data),'primary_cases':60,'clean_cases':3,'registered_start_order':True,'unique_git_candidates':len(entries),'independently_probed_seed_candidates':len(seed_oids),'individual_archived_criteria_checked':observation_count,'primary':counts,'both_comparison_columns_reconciled_for_seeds':len(pairs),'clean':clean,'case_witnesses':{'/'.join(map(str,k)):v for k,v in data.items()}}
if __name__=='__main__':
 result=truth();dest=HOME_DIR/'replicate-2-independent-readback.json';dest.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='case_witnesses'},indent=2));print('evidence',sha(dest.read_bytes()))
