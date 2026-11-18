import hashlib, json, subprocess
from pathlib import Path
from purser.kernel.artifacts import Artifacts
from tests.test_work import WorkTests, OWNER
from tests.test_decisions import premise, decision, link, observation, observer

h = WorkTests()
h.setUp()
store = Artifacts(h.store.path)
out = {}
def qualify(key, label, payload):
    c = {'kind':'artifact.bytes', 'algorithm':'sha256','digest':store.put(payload)}
    result = h.submit(key,label,candidate=c)
    h.run_verification('O-'+key,result,label,candidate=c,report=b'observed exact object '+payload)
    h.close(key)

def snapshot(label):
    v = h.view()
    work = {w:{'state':v.fields(w)['state'],'generation':v.fields(w)['generation'],
               'outcome':v.outcome(w),'closed':v.closed(w),'recorded_closure':v.fields(w).get('closed_outcome')}
            for w in ['W9','W-aggregate','W-consumer','W-unrelated']}
    out[label] = {'revision':h.base,'objects':work,'consumer_criterion':v.qualification('O-W-consumer'),
                  'consumer_actions':[a['action'] for a in v.answers()['q10'] if a['subject']=='W-consumer']}
try:
    h.child('W-aggregate')
    h.child('W9','W-aggregate')
    h.child('W-consumer')
    h.child('W-unrelated')
    h.coverage('W-aggregate')
    h.send([premise('A1'),decision(),link('W9','D-old'),link('W-consumer','W-aggregate')])
    for w in ['W9','W-consumer','W-unrelated']:
        h.send(h.criterion(w,'O-'+w),OWNER)
    qualify('W9','old',b'linear')
    h.close('W-aggregate')
    qualify('W-consumer','consumer',b'client of the original aggregate')
    qualify('W-unrelated','unrelated',b'unrelated')
    snapshot('old')
    h.send(observation(),observer())
    snapshot('invalidated')
    h.send(decision('D-new',support=(),triggers=(),supersedes='D-old'))
    qualify('W9','new',b'exponential')
    h.close('W-aggregate')
    snapshot('reclosed_support')
    out['downstream_reclose'] = h.close('W-consumer',allowed=False)
    snapshot('after')
    out['candidate']=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    blob=json.dumps(out,indent=2,sort_keys=True).encode()+b'\n'
    path=Path(__file__).with_name('trace.json')
    path.write_bytes(blob)
    print(path,hashlib.sha256(blob).hexdigest())
    before=out['old']['objects']; after=out['after']['objects']
    print('aggregate outcomes',before['W-aggregate']['outcome'],after['W-aggregate']['outcome'])
    print('dependent',[(p,out[p]['objects']['W-consumer']['state'],out[p]['consumer_criterion'][0],out[p]['consumer_actions']) for p in ('old','invalidated','reclosed_support','after')])
    print('same dependent outcome and generation, reclose',before['W-consumer']['outcome']==after['W-consumer']['outcome'],before['W-consumer']['generation']==after['W-consumer']['generation'],out['downstream_reclose']['disposition'])
finally:
    h.doCleanups()
