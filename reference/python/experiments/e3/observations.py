"""Read original native ref logs, supervised writers and Purser admission evidence."""

from dataclasses import dataclass
import re
import subprocess

from experiments.e1.accounting import READ_ERRORS, kernel_history, utc_time
from experiments.e1.artifacts import FIXTURE, MANIFEST, START, encoded
from experiments.e1.measurement import same
from experiments.e1.report import parse, require
from purser.effects._git_store import validate_binding
from purser.kernel.effectpolicy import intent_valid
from purser.kernel.wire import digest

E3 = MANIFEST['experiments']['E3']
TARGET = E3['prepared_candidate']['commit_sha1']
BRANCH = 'refs/heads/notify'
WORK = MANIFEST['units']['scored_parent']
ERRORS = (*READ_ERRORS, OSError, subprocess.SubprocessError, UnicodeError)
OID = re.compile(r'[0-9a-f]{40}\Z')


class Sources:
    def __init__(self, objects):
        self.objects = objects
        self.problems = []

    def problem(self, message):
        if message not in self.problems:
            self.problems.append(message)

    def read(self, label, operation, default=None):
        try:
            return operation()
        except ERRORS as error:
            self.problem(f'{label}: {error}')
            return default

    def item(self, ref, label):
        return self.read(label, lambda: self.objects.item(ref))


def subject(raw, evidence):
    intent, binding = evidence['intent'], evidence['binding']
    validate_binding(binding)
    require(intent_valid(intent) and intent['effect'] == f"notify-{raw['seed']}" and intent['work'] == WORK
            and intent['commitment'] == MANIFEST['units']['commitment']
            and intent['repository'] == binding['repository'] and intent['resource'] == binding['resource'] == BRANCH
            and intent['expected_old'] == {'kind': 'git.commit', 'algorithm': 'sha1', 'digest': START}
            and intent['target'] == {'kind': 'git.commit', 'algorithm': 'sha1', 'digest': TARGET}
            and binding['initial_oid'] == START and binding['algorithm'] == 'sha1', 'wrong registered intent or binding')
    checksum = digest('effect', intent)
    require(evidence['receipt_ref'] == 'refs/purser/receipts/' + checksum, 'wrong receipt mapping')
    return checksum


def retained_grant(packet, intent, receipt_ref):
    grant = packet['grant']
    return (same(packet['intent'], intent) and packet['receipt_ref'] == receipt_ref
            and grant['id'] == intent['grant'] and grant['grantee'] == 'executor:script' and grant['expires_at']
            and grant['paths'] == sorted(FIXTURE['public_paths'])
            and all(grant[key] == intent[key] for key in ('work', 'slot', 'assignment', 'generation', 'repository', 'resource')))


def pairs(raw, *, ref_names=False):
    require(isinstance(raw, str), 'native Git output unavailable')
    rows = []
    for line in raw.splitlines():
        left, tab, right = line.partition('\t')
        oid = right if ref_names else left
        require(tab and OID.fullmatch(oid) and (not ref_names or left.startswith('refs/')), 'invalid native Git row')
        rows.append((left, right))
    return rows


@dataclass
class Native:
    start: int
    end: int
    tip: str
    receipt: str | None
    branch_log: list
    receipt_log: list

    @classmethod
    def read(cls, value, binding, receipt_ref):
        require(value['repository_instance'] == binding['repository_instance'] and value['branch'] == BRANCH
                and value['receipt_ref'] == receipt_ref, 'native snapshot is for another physical subject')
        start, end = value['started_ns'], value['finished_ns']
        require(type(start) is type(end) is int and 0 <= start <= end, 'invalid native observation interval')
        refs = pairs(value['refs'], ref_names=True)
        require(len(refs) == len(dict(refs)), 'ambiguous native ref')
        refs = dict(refs)
        log = pairs(value['branch_log'])[::-1]
        require(log and log[0] == (START, 'purser.init ' + binding['repository_instance']) and refs[BRANCH] == log[-1][0],
                'canonical branch lacks its original instance anchor or full history')
        return cls(start, end, refs[BRANCH], refs.get(receipt_ref), log, pairs(value['receipt_log'])[::-1])

    def changes(self):
        return [{'old': previous[0], 'new': current[0], 'message': current[1]}
                for previous, current in zip(self.branch_log, self.branch_log[1:])]

    def publications(self, checksum):
        few = []
        for index, change in enumerate(self.changes(), 1):
            fields = change['message'].split(' ')
            if len(fields) == 3 and fields[:2] == ['purser.effect', checksum] and change['old'] != change['new']:
                few.append({'index': index, 'attempt': fields[2], **change})
        return few

    def disposition(self, checksum, attempt, quiescent):
        if not quiescent:
            return 'unknown'
        changes = self.publications(checksum)
        marker = f'purser.effect {checksum} {attempt}'
        if (len(changes) == 1 and changes[0]['attempt'] == attempt and changes[0]['old'] == START
                and changes[0]['new'] == TARGET and self.receipt == TARGET and self.receipt_log == [(TARGET, marker)]):
            return 'landed'
        if self.tip == START and self.receipt is None and not self.receipt_log and not changes:
            return 'unlanded'
        return 'unknown'


@dataclass
class Writer:
    attempt: str
    action: str
    pid: int
    command: dict
    events: dict

    @classmethod
    def read(cls, value, attempt, arm, intent):
        command = value['command']
        require(command['arm'] == arm and command['attempt'] == attempt and same(command['intent'], intent),
                'physical command belongs to a different intent, arm or attempt')
        events = {event['event']: event for event in value['events']}
        times = [event['at_ns'] for event in value['events']]
        require(all(type(n) is int and n >= 0 for n in times) and times == sorted(times)
                and len(events) == len(times), 'invalid physical process history')
        launched, started = events['launched'], events['started']
        pid = launched['writer_pid']
        require(type(pid) is int and pid > 0 and launched['action_id'] == command['action_id']
                and started['parent_pid'] == launched['recorder_pid'] and started['writer_pid'] == pid,
                'writer process and launching supervisor differ')
        for name, event in events.items():
            if name in ('launched', 'reaped', 'held_at_return', 'exit_check', 'teardown_reaped'):
                require(event['recorder_pid'] == launched['recorder_pid'] and event['writer_pid'] == pid,
                        'supervisor observed a different writer')
            else:
                require(event['recorder_pid'] == pid and event['at_ns'] >= started['at_ns'], 'writer event has another origin')
            if name in ('authorized', 'written'):
                require(event['attempt'] == attempt and event['effect'] == intent['effect'], 'callback for another release')
            if name in ('reaped', 'teardown_reaped'):
                require(type(event['returncode']) is int and event['at_ns'] >= started['at_ns'], 'invalid writer termination')
        return cls(attempt, command['action_id'], pid, command, events)

    def at(self, name):
        event = self.events.get(name)
        return event['at_ns'] if event else None

    def dead_by(self, when):
        return when is not None and any(self.at(k) is not None and self.at(k) <= when for k in ('reaped', 'teardown_reaped'))

    def held_at(self, when):
        return when is not None and self.at('held_at_return') is not None and self.at('held_at_return') <= when and not self.dead_by(when)

    def quiescence_at(self, when):
        if self.dead_by(when):
            return True
        if when is None:
            return None
        check = self.events.get('exit_check')
        if self.held_at(when) and check and check['returncode'] is None and check['at_ns'] >= when:
            return False
        if self.at('started') <= when and any(self.at(k) is not None and self.at(k) >= when
                                             for k in ('authorized', 'written', 'reply')):
            return False
        return None

    def live_at_cutoff(self, cutoff, final):
        check = self.events.get('exit_check')
        if not check or not final or cutoff is None:
            return None
        return (self.held_at(cutoff) and check['returncode'] is None and cutoff <= check['at_ns'] <= final.start
                and self.at('teardown_reaped') is not None and self.at('teardown_reaped') >= final.end)

    def loop_permission(self, packet, checksum, instance):
        checked = self.events.get('permission_checked')
        if not checked or self.at('authorized') is None:
            return False
        return (same(checked['grant'], packet['grant']) and checked['preparation'] == {
            'intent_digest': checksum, 'repository_instance': instance, 'changed_paths': sorted(FIXTURE['public_paths'])}
            and checked['authorization'] == {'disposition': 'admitted', 'details': {
                'effect': packet['intent']['effect'], 'attempt': self.attempt, 'intent_digest': checksum, 'state': 'dispatching'}}
            and checked['at_ns'] <= self.at('authorized'))


class Kernel:
    def __init__(self, trace, objects):
        kernel_history(trace, set())  # Canonical SQLite batch reader and projection, including snapshot identity.
        self.events = [parse(value.encode()) for value in trace['original_events']]
        self.receipts = {(r['proposal_id'], r['actor_id']): r for r in trace['snapshot']['projection']['receipts']}
        self.artifacts = {key: objects.blob(ref) for key, ref in trace['kernel_artifacts'].items() if ref['sha256'] == key}

    def event(self, name, action, actor):
        events = [e for e in self.events if e['type'] == name and e['proposal_id'] == action and e['actor_id'] == actor]
        return events[0] if len(events) == 1 else None

    def admitted(self, writer, intent, binding, packet):
        auth = self.event('EffectDispatchAuthorized', writer.action, 'broker:git')
        if not auth:
            return False
        earlier = [e for e in self.events if e['revision'] < auth['revision']]
        grants = [e['data'] for e in earlier if e['type'] == 'GrantIssued' and e['data']['id'] == intent['grant']]
        bounds = [e['data'] for e in earlier if e['type'] == 'EffectIntentBound' and e['data']['id'] == intent['effect']]
        require(len(grants) == len(bounds) == 1, 'scoped prior grant or submitted kernel intent missing')
        grant, bound, data = grants[0], bounds[0], auth['data']
        return (same({k: grant.get(k) for k in packet['grant']}, packet['grant']) and bound['intent'] == intent
                and bound['repository_instance'] == binding['repository_instance'] and bound['changed_paths'] == sorted(FIXTURE['public_paths'])
                and data['effect'] == intent['effect'] and data['id'] == writer.attempt and data['broker'] == 'broker:git'
                and data['authorized_at'] == auth['at'] < grant['expires_at'] and data['commitment'] == intent['commitment']
                and not any(e['type'] == 'GrantRevoked' and e['data']['id'] == intent['grant'] for e in earlier))

    def observed(self, action, attempt, effect, response):
        event = self.event('GitEffectObserved', action, 'observer:git')
        receipt = self.receipts.get((action, 'observer:git'))
        if not event or not receipt or not same(receipt, response.get('receipt')):
            return False
        data = event['data']
        return (data['effect'] == effect and data['attempt'] == attempt and data['state'] == response['report']['state']
                and data['reason'] == response['report']['reason'] and self.artifacts.get(data['artifact_sha256']) == encoded(response['report']))


class Physical:
    """Native history at the original attempt, each return and the participant cutoff."""

    def __init__(self, raw, e3, journal, public, clock, sources):
        self.sources, self.calls = sources, public.actions
        self.intent, self.binding = e3.get('intent') or {}, e3.get('binding') or {}
        self.checksum = sources.read('registered physical subject', lambda: subject(raw, e3))
        self.receipt = e3.get('receipt_ref')
        self.writers, self.post, self.observations = {}, {}, {}
        self.external_complete = self.subject_ok = bool(self.checksum)
        self.packet = sources.item(e3.get('task'), 'original task and scoped grant')
        self.grant_ok = bool(sources.read('released scope', lambda: retained_grant(self.packet, self.intent, self.receipt)))
        for attempt, ref in (e3.get('writers') or {}).items():
            writer = sources.read('protected writer ' + attempt,
                                  lambda: Writer.read(sources.objects.item(ref), attempt, raw['arm'], self.intent))
            if writer:
                self.writers[attempt] = writer
        self.kernel = None
        if raw['arm'] == 'purser':
            self.kernel = sources.read('original Purser event log', lambda: Kernel(sources.objects.item(e3['kernel']), sources.objects))
        elif e3.get('kernel') is not None:
            sources.problem('unexpected Purser history for a plain-loop treatment')
        self.initial = self.native(e3.get('initial_native'), 'native starting branch')
        self.final = self.native(e3.get('final_native'), 'native branch at participant exit')
        self.external_complete &= bool(self.initial and self.final and self.initial.tip == START and len(self.initial.branch_log) == 1
                                       and self.initial.receipt is None and not self.initial.receipt_log)
        for label, ref in (e3.get('native_snapshots') or {}).items():
            if label.startswith('after-'):
                self.post[label[6:]] = self.native(ref, 'native dispatch ' + label)
        for action, ref in (e3.get('observations') or {}).items():
            self.observations[action] = self.native(ref, 'native observation ' + action)
        self.externals = [r['payload'] for r in journal if r['kind'] == 'external']
        for row in self.externals:
            ok = sources.read('physical dispatch ' + str(row.get('attempt')), lambda: self.external(row, public))
            if not ok:
                self.external_complete = False
        reads = sorted((s for s in [self.initial, *self.post.values(), *self.observations.values(), self.final] if s),
                       key=lambda snap: (snap.start, snap.end))
        self.prefix_ok = all(a.end <= b.start and a.branch_log == b.branch_log[:len(a.branch_log)] for a, b in zip(reads, reads[1:]))
        if not self.prefix_ok:
            sources.problem('independent native reads lost their retained chronology')
        self.external_complete &= bool(self.prefix_ok and self.initial and self.final and reads[0] is self.initial
                                      and reads[-1] is self.final and clock['end'] is not None
                                      and self.final.start >= clock['end'] and clock['start'] is not None
                                      and self.initial.end <= clock['start'])
        # A post-interruption read may prove an earlier positive publication by its retained position,
        # but it does not date an additional publication to before the participant cutoff.
        witnessed = [s for s in reads if s is not self.final and (clock['end'] is None or s.end <= clock['end'])]
        self.publications = max((s.publications(self.checksum) for s in witnessed), key=len, default=[])
        if self.external_complete:
            final = self.final.publications(self.checksum)
            if len(final) == len(self.publications):
                self.publications = final
            else:
                self.external_complete = False
                sources.problem('final native history contains a publication with unestablished cutoff timing')
        self.by_order = []
        for writer in sorted(self.writers.values(), key=lambda w: w.at('launched')):
            call = public.actions.get(writer.action)
            linked = bool(call and call['kind'] == 'effect' and call['actor'] == 'broker'
                          and call['input'] == {'effect': self.intent['effect'], 'attempt': writer.attempt})
            if not linked:
                self.external_complete = False
                sources.problem('writer has no matching original public effect invocation')
            if (writer.at('authorized') is not None or writer.at('written') is not None
                    or any(c['attempt'] == writer.attempt for c in self.publications)):
                self.by_order.append(writer)
        issued = [c for c in public.actions.values() if c['kind'] == 'effect']
        for call in issued:
            if not any(w.action == call['action_id'] for w in self.writers.values()):
                # A real guard refusal costs a step but never releases a physical writer.
                answer = call['return'] or {}
                if answer.get('status') != 'refused' and answer.get('disposition') != 'rejected':
                    self.external_complete = False
                    sources.problem('public effect has no settled physical writer witness')
        if any(not w.dead_by(clock['end']) and not (w.held_at(clock['end']) and self.final
                   and not w.dead_by(self.final.end)) for w in self.writers.values()):
            self.external_complete = False

    def native(self, ref, label):
        return self.sources.read(label, lambda: Native.read(self.sources.objects.item(ref), self.binding, self.receipt))

    def external(self, row, public):
        attempt = row['attempt']
        current = self.native(row['native'], 'retained external after ' + attempt)
        prior = self.native(row['prior_native'], 'retained external before ' + attempt)
        if not current or not prior:
            return False
        if self.post.get(attempt) is None:
            self.post[attempt] = current
        require(self.post[attempt] == current and prior.end <= current.start
                and prior.branch_log == current.branch_log[:len(prior.branch_log)], 'inconsistent native dispatch history')
        changes = current.changes()[len(prior.branch_log)-1:]
        writer, call = self.writers[attempt], public.actions[row['action_id']]
        require(row['intent'] == self.intent['effect'] and row['expected_old'] == START and row['target'] == TARGET
                and row['writer'] == writer.pid and writer.action == call['action_id'] and call['issued'] <= writer.at('launched')
                and writer.at('launched') <= current.start and (call['finished'] is None or current.end <= call['finished'])
                and same(changes, row['transitions']) and same(writer.dead_by(current.start), row['writer_quiescent']),
                'journal dispatch is inconsistent with the original writer or native transition')
        return True

    def original(self, when):
        if not self.subject_ok or not self.by_order or not self.prefix_ok:
            return 'unknown'
        writer = self.by_order[0]
        actual = [p for p in self.publications if p['attempt'] == writer.attempt]
        if any(p['old'] == START and p['new'] == TARGET for p in actual):
            return 'landed'
        post = self.post.get(writer.attempt)
        if post and writer.dead_by(post.start) and (when is None or post.end <= when):
            return 'unlanded'
        return 'unknown'

    def permission(self, writer):
        if not self.grant_ok:
            return None
        if writer.command['arm'] == 'purser':
            if not self.kernel:
                return None
            return self.kernel.admitted(writer, self.intent, self.binding, self.packet)
        call = self.calls.get(writer.action)
        if not call:
            return None
        checked = writer.at('permission_checked')
        if checked is None or checked < call['issued'] or (call['finished'] is not None and checked > call['finished']):
            return False
        deadline = utc_time(self.packet['grant']['expires_at'])
        if call.get('finished_utc') and (not deadline or utc_time(call['finished_utc']) >= deadline):
            return None
        return writer.loop_permission(self.packet, self.checksum, self.binding['repository_instance'])

    def reading(self, call):
        from purser.kernel.effectpolicy import observes, proof_valid

        attempt = call['input'].get('attempt')
        writer = self.writers.get(attempt)
        if not writer or call['finished'] is None:
            return None
        response = call['return']
        if call['kind'] == 'effect':
            if response.get('status') != 'acknowledged':
                return 'unknown'
            event = writer.events.get('reply')
            native = self.post.get(attempt)
            if not native or not event:
                return None
            return 'landed' if (event['public'] == response == {'status': 'acknowledged', 'effect': self.intent['effect'],
                    'attempt': attempt, 'target': TARGET, 'receipt_ref': self.receipt} and event['at_ns'] <= call['finished']
                    and native.end <= call['finished'] and native.disposition(self.checksum, attempt, writer.dead_by(native.start)) == 'landed') else 'unknown'
        if call['kind'] != 'reconcile':
            return 'unknown'
        native = self.observations.get(call['action_id'])
        if native is None or not self.prefix_ok:
            return None
        delivery = call['input'].get('delivery')
        if (call['input'].get('effect') != self.intent['effect'] or delivery not in ('explicit', 'automatic')
                or call['actor'] != ('executor' if delivery == 'explicit' else 'independent_observer')
                or delivery == 'automatic' and writer.command['arm'] == 'loop'):
            return 'unknown'
        report = response.get('report')
        if (not proof_valid('git_observation', report) or not observes(report, {'intent': self.intent, 'intent_digest': self.checksum,
                        'repository_instance': self.binding['repository_instance']}, attempt)
                or not call['issued'] <= native.start <= native.end <= call['finished']):
            return 'unknown'
        if writer.command['arm'] == 'purser':
            if not self.kernel:
                return None
            if not self.kernel.observed(call['action_id'], attempt, self.intent['effect'], response):
                return None
        if report['state'] == 'pending':
            return 'unknown'
        quiescent = writer.quiescence_at(native.start)
        if quiescent is None:
            return None
        actual = native.disposition(self.checksum, attempt, quiescent)
        declared = {'confirmed_happened': 'landed', 'confirmed_absent': 'unlanded'}[report['state']]
        return actual if declared == actual and report['branch_tip'] == native.tip and report['receipt_target'] == native.receipt else 'unknown'
