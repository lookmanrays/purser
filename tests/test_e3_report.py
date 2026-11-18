"""The M0 examples, the independent M4 controls and a fresh call-to-Git archive."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.e1.artifacts import Artifacts, REGISTRATION, ROOT, START, git
from experiments.e1.recovery import FileArchive
from experiments.e1.report import GitPacks, Objects
from experiments.e3 import trial
from experiments.e3.observations import Native, TARGET
from experiments.e3.report import derive_case, inventory
from experiments.e3.scoring import grade, reconstruct
from experiments.e3.suite import schedule
from experiments.e3.summary import aggregate
from tests.test_e3_runner import pilot_pin

CONTROLS = json.loads((ROOT / 'docs/evidence/M4/observer/e3-reference-controls.json').read_bytes())
STRESS = 'inconclusive-observation'
PUBLICATION = {'old': START, 'new': TARGET, 'attempt': 'T1'}


def decision(value, choice, at, basis, attempt='T1'):
    return {'attempt': attempt, 'original_report': value, 'report': value, 'choice': choice,
            'published_ns': at, 'issued': at-1, 'basis': basis, 'reason': 'writer_may_still_apply',
            'next_action': 'Inspect the receipt after the writer exits.' if choice == 'pending' else None}


def parameters(truth):
    unlanded = truth == 'unlanded'
    decisions = [decision(truth, 'retry' if unlanded else 'complete', 10, 'read-1')]
    proof = {'attempt': 'T1', 'value': truth, 'finished': 7}
    releases = [{'attempt': 'T1', 'issued': 1, 'admitted': True, 'authorization': 2, 'prior_dead': None}]
    if unlanded:
        releases.append({'attempt': 'T2', 'issued': 15, 'admitted': True, 'authorization': 16, 'prior_dead': True})
        decisions.append({**decision('unlanded', 'complete', 22, 'read-2', attempt='T2'), 'report': 'landed'})
    return {'track': 'primary', 'truth': truth, 'original': 'T1', 'decisions': decisions, 'delivered': {'read-1': proof},
            'releases': releases, 'publications': [dict(PUBLICATION, attempt='T2' if unlanded else 'T1')],
            'whole': True, 'public_complete': True, 'terminal': 'done', 'steps': 3, 'elapsed': 100,
            'manual': False, 'resolvable': True, 'uncertainty': False}


class FrozenControls(unittest.TestCase):
    def test_all_thirteen_original_outcome_and_order_controls(self):
        landed, absent = parameters('landed'), parameters('unlanded')
        cases = {'lost_ack_then_legitimate_different_writer_advance': landed,
                 'known_absence_original_3002_worked_example': absent,
                 'rejected_duplicate_request_zero_new_transition': dict(landed, steps=landed['steps']+1)}
        wrong = copy.deepcopy(absent)
        wrong['decisions'][0].update(original_report='landed', report='landed')
        cases['right_final_tip_wrong_original'] = wrong
        late = copy.deepcopy(absent)
        late['decisions'][0].update(published_ns=18, issued=17)
        cases['true_declaration_after_new_physical_attempt'] = late
        early = copy.deepcopy(absent)
        early['releases'][1]['authorization'] = 8
        cases['fresh_id_with_only_early_authorization'] = early
        cases['two_actual_publications_another_transport_same_intent'] = dict(landed, releases=absent['releases'],
                                                                             publications=landed['publications']+absent['publications'])
        unsupported = dict(landed, truth='unlanded', publications=[], delivered={'read-1': {'value':'unknown','attempt':'T1','finished':7}})
        cases['tip_without_receipt'] = cases['wrong_receipt'] = unsupported
        pending = dict(absent, truth='unknown', publications=[], terminal='blocked', track=STRESS, resolvable=False,
                       uncertainty=True, decisions=[decision('unknown', 'pending', 10, 'read-1')], releases=absent['releases'][:1],
                       delivered={'read-1': {'attempt': 'T1', 'value': 'unknown', 'finished': 6, 'pending_reason': 'writer_may_still_apply'}})
        cases['live_writer_honest_stress'] = pending
        cases['pending_label_with_real_retry_in_stress'] = dict(pending, releases=absent['releases'])
        cases['ordinary_resolvable_idle_needs_repair_but_none_performed'] = dict(pending, track='primary', truth='unlanded', resolvable=True)
        cases['missing_independent_coverage'] = dict(landed, whole=False, public_complete=False, elapsed=None)
        self.assertEqual(set(cases), set(CONTROLS['per_case']))
        for key, params in cases.items():
            with self.subTest(key=key):
                actual = grade(**params)
                expected = CONTROLS['per_case'][key]['expected']
                self.assertEqual({k: actual[k] for k in expected}, expected)
        worked = next(c['expected_score'] for c in json.loads((REGISTRATION/'worked-examples.json').read_bytes())['cases'] if c['experiment']=='E3')
        answer = grade(**absent)
        self.assertEqual({k:answer[k] for k in worked}, worked)

    def test_all_nine_registered_aggregate_controls_and_rotated_schedule(self):
        gold = {'eligible': True, 'correct_reconciliation': True, 'resolved_by_budget': True, 'duplicate_count': 0,
                'physical_count_lower_bound': 1, 'stuck_requires_manual': False, 'steps': 2}
        def measure(changed):
            records = {(t,s,a): {'facts': gold} for t,s,_,a in schedule()}
            for key, delta in changed.items():
                records[key] = {'facts': dict(gold, **delta) if delta is not None else {}}
            arms, flag = aggregate(records)
            return dict(absolute=arms['purser']['absolute'], original_no_advantage=arms['purser']['original_no_advantage'], clean_overhead=flag)
        first, second, clean = ('primary',3001,'purser'), ('primary',3002,'purser'), ('clean',3001,'purser')
        duplicate = {'physical_count_lower_bound': 2, 'duplicate_count': 1}
        cases = {'twenty_correct_zero_duplicates': {}, 'wrong_original_with_one_effect_and_no_stuck': {first:{'correct_reconciliation':False}},
                 'actual_duplicate': {first:duplicate}, 'manual_needed_in_ordinary': {first:{'resolved_by_budget':False,'stuck_requires_manual':True}},
                 'unknown_coverage': {first:None}, 'known_duplicate_even_with_other_unit_missing': {first:duplicate,second:None},
                 'clean_exactly_double': {clean:{'steps':4}}, 'clean_strictly_over_double':{clean:{'steps':5}},
                 'failed_clean_ineligible':{clean:{'resolved_by_budget':False}}}
        self.assertEqual({key:measure(changes) for key,changes in cases.items()}, CONTROLS['aggregate_facts'])
        self.assertEqual(list(schedule()), [(s['track'],s['seed'],s['rank'],s['arm']) for s in CONTROLS['schedule']])

    def test_native_original_anchor_transitions_receipt_and_other_writer(self):
        instance, checksum, other = '1'*64, '2'*64, '3'*40
        ref = 'refs/purser/receipts/' + checksum
        binding = {'repository_instance': instance}
        def native(entries, receipt=None, receipt_rows=()):
            history = [(START,'purser.init ' + instance), *entries]
            branch = 'refs/heads/notify'
            wire = {'repository_instance':instance,'receipt_ref':ref,'branch':branch,'started_ns':1,'finished_ns':2,
                    'refs': f'{branch}\t{history[-1][0]}\n'+(f'{ref}\t{receipt}\n' if receipt else ''),
                    'branch_log': ''.join(f'{oid}\t{message}\n' for oid,message in reversed(history)),
                    'receipt_log': ''.join(f'{oid}\t{message}\n' for oid,message in reversed(receipt_rows))}
            return Native.read(wire,binding,ref)
        first = (TARGET,'purser.effect '+checksum+' T1')
        later = native([first,(other,'authorized unrelated advance')], TARGET, [first])
        self.assertEqual(later.disposition(checksum,'T1',True),'landed')
        self.assertEqual(len(later.publications(checksum)),1)
        for source in (native([(TARGET,'unrelated actor')]),native([(TARGET,'unrelated actor')],other,[(other,'another receipt')])):
            self.assertEqual((source.disposition(checksum,'T1',True),source.publications(checksum)),('unknown',[]))
        duplicated = native([first,(START,'different writer'),(TARGET,'purser.effect '+checksum+' Other-ID')],TARGET,[first])
        self.assertEqual([p['attempt'] for p in duplicated.publications(checksum)],['T1','Other-ID'])
        self.assertEqual(native([]).disposition(checksum,'T1',False),'unknown')
        self.assertEqual(native([]).disposition(checksum,'T1',True),'unlanded')


class OriginalArchive(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.archive = FileArchive(self.root)
        self.assets = Artifacts(self.archive)
        self.packs = GitPacks(self.root)

    def run_case(self, arm, track, rank):
        folder = self.root / f'{arm}-{track}-{rank}'
        folder.mkdir()
        raw = trial.run_participant(folder,self.assets,pilot_pin(),arm,track,3000+rank,rank)
        self.assertNotEqual(raw['terminal'], 'harness_failure')
        return raw

    def test_real_cases_cold_score_independently_of_saved_answer(self):
        raw = self.run_case('purser','primary',2)
        raw['score'] = {'original_report':'landed','effect_count':15}
        facts = reconstruct(raw,Objects(self.archive),self.packs)
        self.assertEqual((facts['original_truth'],facts['original_report'],facts['correct_reconciliation'],facts['effect_count'],
                          facts['steps'],facts['eligible'],facts['diagnostics']),('unlanded','unlanded',True,1,8,True,[]))
        worked = next(x['expected_score'] for x in json.loads((REGISTRATION/'worked-examples.json').read_bytes())['cases'] if x['experiment']=='E3')
        self.assertEqual(derive_case(raw,self.archive),worked)
        inventory(raw,Objects(self.archive))
        public = facts['delivered']
        self.assertEqual([r['value'] for r in public.values()],['unknown','unlanded','landed','landed'])

    def test_real_stress_and_an_ordinary_unresolved_exit(self):
        raw = self.run_case('loop',STRESS,1)
        facts = reconstruct(raw,Objects(self.archive),self.packs)
        self.assertEqual((facts['original_truth'],facts['stress_held_pending'],facts['effect_count'],facts['manual_repair'],facts['steps']),('unknown',True,0,False,5))
        with patch('experiments.e3.participant.disposition',return_value=('unknown','worker_did_not_act_on_available_evidence')):
            ordinary = self.run_case('loop','primary',2)
        f = reconstruct(ordinary,Objects(self.archive),self.packs)
        self.assertEqual((f['original_truth'],f['original_report'],f['correct_reconciliation'],f['resolved_by_budget'],
                          f['stuck_requires_manual'],f['manual_repair']),('unlanded','unknown',False,False,True,False))

    def test_missing_originals_keep_available_facts_and_public_attempts(self):
        raw = self.run_case('loop','primary',1)
        o = Objects(self.archive)
        case = o.item(raw['artifacts']['platform'])['e3']
        missing = {case['final_native']['path'],case['observations']['action-4']['path']}
        class Missing:
            def read(_, path):
                if path in missing:
                    raise KeyError(path)
                return self.archive.read(path)
            def namelist(_):
                return [p for p in self.archive.namelist() if p not in missing]
        facts = reconstruct(raw,Objects(Missing()),self.packs)
        self.assertEqual((facts['effect_count'], facts['correct_reconciliation'],facts['resolved_by_budget'],facts['steps'],
                          facts['physical_count_lower_bound'],facts['original_truth']),(None,None,None,5,1,'landed'))
        self.assertIsNone(derive_case(raw,Missing()))

    def test_native_original_after_real_later_advancement_and_second_transport(self):
        from experiments.e3.actions import Tools
        from purser.kernel.wire import digest
        observe = Tools._observation
        for duplicate in (False, True):
            def intervene(tools, intent, attempt, delivery):
                repo = Path(tools.repository.binding['git_dir'])
                if duplicate:
                    # Only this disposable fixture rewinds and reapplies the logical task.
                    git(repo, 'update-ref', '-m', 'fixture restores starting policy', intent['resource'], START, TARGET)
                    git(repo, 'update-ref', '-m', 'purser.effect ' + digest('effect', intent) + ' Alternate',
                        intent['resource'], TARGET, START)
                else:
                    tree = git(repo, 'rev-parse', TARGET + '^{tree}').decode().strip()
                    later = git(repo, 'commit-tree', tree, '-p', TARGET, data=b'Independent local advancement\n').decode().strip()
                    git(repo, 'update-ref', '-m', 'different authorized work', intent['resource'], later, TARGET)
                return observe(tools, intent, attempt, delivery)
            with self.subTest(duplicate=duplicate), patch.object(Tools, '_observation', intervene):
                raw = self.run_case('loop_equal_verifier' if duplicate else 'loop', 'primary', 1)
                facts = reconstruct(raw, Objects(self.archive), self.packs)
                self.assertEqual(facts['original_truth'], 'landed')
                self.assertEqual((facts['effect_count'], facts['duplicate_count'], facts['correct_reconciliation']),
                                 (2, 1, False) if duplicate else (1, 0, True))

    def test_rejected_extra_public_attempt_costs_one_and_changes_no_ref(self):
        from experiments.e3.participant import Participant
        normal = Participant.execute
        def run_extra(participant):
            result = normal(participant)
            participant.tools.j.invoke('effect','broker',{'effect':'notify-3001','attempt':'unreleased'},
                                       lambda _: {'status':'refused','reason':'intent_consumed'})
            return result
        with patch.object(Participant,'execute',run_extra):
            raw = self.run_case('loop','primary',1)
        f = reconstruct(raw,Objects(self.archive),self.packs)
        self.assertEqual((f['steps'],f['effect_count'],f['duplicate_count'],f['correct_reconciliation']),(6,1,0,True))


if __name__ == '__main__':
    unittest.main()
