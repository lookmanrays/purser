"""Recover the observed paid invocations, published decisions and participant cutoff."""

from experiments.e1.measurement import same
from experiments.e1.report import require

from .observations import TARGET, WORK

KINDS = {'plan', 'context', 'edit', 'check', 'submit', 'observe', 'decide', 'effect', 'reconcile'}


def journal_rows(value):
    require(type(value) is list, 'journal is unavailable')
    previous = 0
    for index, row in enumerate(value, 1):
        require(type(row['seq']) is int and row['seq'] == index and type(row['offset_ns']) is int
                and row['offset_ns'] >= previous and type(row['payload']) is dict
                and row['kind'] in {'action', 'fault', 'external', 'oracle', 'public_state', 'terminal'},
                'journal has a chronology gap or an unknown row')
        previous = row['offset_ns']
    return value


class Public:
    def __init__(self, arm, evidence, rows, sources):
        self.actions, self.decisions = {}, []
        self.complete = True
        self.sources, self.arm = sources, arm
        action_rows = [r['payload'] for r in rows if r['kind'] == 'action']
        pairs = list(evidence.get('actions', []))
        for p in action_rows:
            if p.get('request') and all(p['request'] != item.get('request') for item in pairs):
                pairs.append({'request': p['request'], 'response': p.get('response')})
        for pair in pairs:
            self.complete &= bool(sources.read('original public invocation', lambda: self._call(pair)))
        ordered = sorted(self.actions.values(), key=lambda call: call['attempt'])
        if [call['attempt'] for call in ordered] != list(range(1, len(ordered) + 1)):
            self.complete = False
            sources.problem('public invocation sequence has an unobserved occurrence')
        for a, b in zip(ordered, ordered[1:]):
            if a['issued'] > b['issued'] or (a['finished'] is not None and a['finished'] > b['issued']):
                self.complete = False
                sources.problem('original public call intervals disagree with their issue order')
        for p in action_rows:
            call = self.actions.get(p['action_id'])
            if not call or not all(same(p.get(k), v) for k, v in call['request'].items()):
                self.complete = False
                sources.problem('original caller input and journal disagree at ' + str(p['action_id']))
            elif call['finished'] is not None and not same(p.get('return'), call['return']):
                self.complete = False
                sources.problem('public return and journal disagree at ' + p['action_id'])
        if {p['action_id'] for p in action_rows} != set(self.actions):
            self.complete = False
            sources.problem('public journal does not cover all original invocations')
        pointers = list(evidence.get('declarations', []))
        states = [r['payload'] for r in rows if r['kind'] == 'public_state']
        for p in states:
            if p.get('declaration') and p['declaration'] not in pointers:
                pointers.append(p['declaration'])
        seen = set()
        for ref in pointers:
            declaration = sources.read('published decision', lambda: self._decision(sources.objects.item(ref)))
            if declaration is None:
                self.complete = False
            elif declaration['action_id'] not in seen:
                self.decisions.append(declaration)
                seen.add(declaration['action_id'])
        for call in self.actions.values():
            result = call['return'] or {}
            if call['kind'] == 'decide' and call['action_id'] not in seen and 'published_ns' in result:
                declaration = sources.read('returned decision publication', lambda: self._decision(result))
                if declaration:
                    self.decisions.append(declaration)
                    seen.add(call['action_id'])
        self.decisions.sort(key=lambda d: d['published_ns'])
        for p in states:
            declaration = next((d for d in self.decisions if d['action_id'] == p['action_id']), None)
            if not declaration or (p['from'], p['to'], p.get('original_report'), p['candidate'], p['subject']) != (
                    declaration['previous'], declaration['choice'], declaration['original_report'], TARGET, WORK):
                self.complete = False
                sources.problem('public state and original decision differ')
        if seen != {p['action_id'] for p in states}:
            self.complete = False
            sources.problem('public journal does not cover all published decisions')

    def _call(self, pair):
        request = self.sources.objects.item(pair['request'])
        action_id, index, issued, kind = (request[k] for k in ('action_id', 'attempt', 'issued_monotonic_ns', 'action'))
        require(type(index) is int and index > 0 and action_id == f'action-{index}' and request['arm'] == self.arm
                and request['subject'] == WORK and request['candidate'] == TARGET and kind in KINDS
                and type(request['input']) is dict and type(issued) is int, 'invalid original action subject')
        response = self.sources.item(pair['response'], 'original return ' + action_id) if pair.get('response') else None
        finished = response['finished_monotonic_ns'] if response else None
        require(response is None or (response['action_id'] == action_id and type(finished) is int and finished >= issued
                                     and type(response['return']) is dict), 'response belongs to a different public invocation')
        call = {'request': request, 'action_id': action_id, 'attempt': index, 'kind': kind, 'issued': issued,
                'finished': finished, 'finished_utc': response.get('finished_utc') if response else None,
                'actor': request['actor'], 'input': request['input'],
                'return': response['return'] if response else None}
        require(action_id not in self.actions or same(call, self.actions[action_id]), 'conflicting public invocation identity')
        self.actions[action_id] = call
        return True

    def _decision(self, value):
        call = self.actions[value['action_id']]
        fields = {key: val for key, val in value.items() if key not in ('action_id', 'published_ns', 'published_utc')}
        require(call['kind'] == 'decide' and call['actor'] == 'executor' and same(call['input'], fields)
                and call['issued'] <= value['published_ns'] and value['original_report'] in ('unknown', 'landed', 'unlanded')
                and value['choice'] in ('complete', 'retry', 'pending') and (call['finished'] is None or (
                    value['published_ns'] <= call['finished'] and same(call['return'], value))),
                'decision is not bound to its original publication')
        return {**value, 'issued': call['issued']}


def clocks(evidence, platform, rows, public, sources, *, mode='scripted'):
    def read_clock(name, prefix):
        clock = sources.objects.item(evidence[name])
        require(type(clock['at_ns']) is int and clock['at_ns'] >= 0 and isinstance(clock['utc'], str)
                and (platform.get(prefix + '_monotonic_ns'), platform.get(prefix + '_utc')) == (clock['at_ns'], clock['utc']),
                'clock disagrees with its retained observation')
        return clock
    start = sources.read('task release clock', lambda: read_clock('release_clock', 'start'))
    end = sources.read('stopped participant clock', lambda: read_clock('end_clock', 'end'))
    receipt = sources.item(evidence.get('exit'), 'participant exit')
    terminals = [r for r in rows if r['kind'] == 'terminal']
    terminal = terminals[0]['payload'] if len(terminals) == 1 and terminals[0] is rows[-1] else {}
    observed_exit = None
    if receipt and end:
        reasons = {'done', 'blocked', 'idle', 'step_limit', 'time_limit'}
        if mode == 'model':
            reasons.update(('model_limit', 'provider_unavailable'))
        if (same(platform.get('participant_exit'), receipt) and receipt.get('end_monotonic_ns') == end['at_ns']
                and receipt.get('end_utc') == end['utc'] and receipt.get('reason') in reasons):
            observed_exit = receipt['reason']
        else:
            sources.problem('participant exit disagrees with the stopped clock')
    if observed_exit and terminal.get('end_reason') != observed_exit:
        sources.problem('journal endpoint disagrees with the original participant exit')
    manual = None
    if observed_exit and terminal.get('end_reason') == observed_exit and 'human_action' in terminal:
        manual = bool(terminal['human_action'])
    now = end['at_ns'] if end else None
    elapsed = None
    if start and end:
        left, right = start['at_ns'], end['at_ns']
        if (0 <= left <= right and all(left <= c['issued'] <= (c['finished'] or right) <= right for c in public.actions.values())
                and platform.get('setup', {}).get('end_ns', left + 1) <= left
                and platform.get('offline_evidence', {}).get('start_ns', right - 1) >= right):
            elapsed = right - left
        else:
            sources.problem('setup, paid actions, offline evidence, or cutoff crosses the participant clock')
    return {'start': start['at_ns'] if start else None, 'end': now, 'elapsed_ns': elapsed,
            'terminal': observed_exit, 'manual_repair': manual,
            'elapsed_lower_ns': max([0, *(max(c['issued'], c['finished'] or 0) - start['at_ns']
                                        for c in public.actions.values())]) if start else None}
