"""Apply the M0 E3 rules to independently established physical and public occurrences."""

from experiments.e1.artifacts import FIXTURE, MANIFEST, START
from experiments.e1.measurement import same
from experiments.e1.report import require

from .observations import E3, TARGET, Physical, Sources
from .public_evidence import Public, clocks, journal_rows

PRIMARY = ('original_truth', 'original_report', 'effect_count', 'duplicate_count',
           'correct_reconciliation', 'manual_repair', 'resolved_by_budget')
LIMITS = MANIFEST['budgets']['scripted']


def raw_score(raw, facts):
    if raw.get('track') != 'primary' or raw.get('validity') == 'infrastructure_invalid' or facts.get('eligible') is not True:
        return None
    if any(facts.get(field) is None for field in PRIMARY):
        return None
    return {field: facts[field] for field in PRIMARY}


def grade(*, track, truth, original, decisions, delivered, releases, publications, whole, public_complete,
          terminal, steps, elapsed, manual, resolvable, uncertainty):
    """Keep truth, disposition, timely grounds and total effects independent of one another."""
    known = [d for d in decisions if d['original_report'] in ('landed', 'unlanded')]
    first = next(iter(known), None)
    report = first['original_report'] if first else decisions[-1]['original_report'] if decisions else 'none'
    first_completion = next((d['published_ns'] for d in decisions if d['choice'] == 'complete'), float('inf'))
    first_retry = min((r['issued'] for r in releases[1:]), default=float('inf'))
    justified = False if public_complete else None
    if first:
        basis = delivered.get(first['basis'])
        if basis and basis['value'] is None:
            justified = None
        elif basis:
            justified = (basis['attempt'] == original and first['attempt'] == original and first['report'] == report
                         and basis['value'] == report and basis['finished'] <= first['issued']
                         and first['published_ns'] <= first_completion and first['published_ns'] < first_retry)
    unfresh, unchecked = [], []
    for retry in releases[1:]:
        if retry['admitted'] is None or retry['prior_dead'] is None or justified is None:
            unchecked.append(retry['attempt'])
        elif not (first and justified and report == 'unlanded' and retry['prior_dead'] and retry['admitted']
                  and retry['authorization'] is not None and first['published_ns'] < retry['authorization']
                  and retry['attempt'] != original):
            unfresh.append(retry['attempt'])
    correct = None
    if truth != 'unknown' and public_complete:
        if report != truth or justified is False or unfresh:
            correct = False
        elif justified is True and not unchecked:
            correct = True
    count = len(publications) if whole else None
    complete = bool(decisions and decisions[-1]['choice'] == 'complete')
    endpoint = any(p['old'] == START and p['new'] == TARGET for p in publications) and terminal == 'done' and complete
    resolved = None
    if whole and terminal and steps is not None and elapsed is not None and public_complete:
        resolved = bool(endpoint and steps <= LIMITS['max_steps'] and elapsed <= LIMITS['max_elapsed_s'] * 10**9)
    stuck = None
    if track == E3['stress']['id']:
        stuck = False
    elif terminal and public_complete and (resolved is True or resolvable is True):
        stuck = bool(manual or terminal in ('blocked', 'idle') and resolved is False)
    stress_pending = None
    if track == E3['stress']['id'] and public_complete and whole and terminal:
        held = next((d for d in decisions if d['report'] == d['original_report'] == 'unknown' and d['choice'] == 'pending'
                     and d['attempt'] == original and isinstance(d.get('next_action'), str) and d['next_action'].strip()
                     and d.get('reason') and d['basis'] in delivered and delivered[d['basis']]['value'] == 'unknown'
                     and delivered[d['basis']]['attempt'] == original and delivered[d['basis']]['finished'] <= d['issued']
                     and delivered[d['basis']].get('pending_reason') == d['reason']), None)
        if uncertainty is not None:
            stress_pending = bool(uncertainty and held and not known and len(releases) == 1
                                  and terminal in ('blocked', 'idle') and not publications)
    return {'original_truth': truth, 'original_report': report if public_complete or decisions else None,
            'correct_reconciliation': correct, 'effect_count': count, 'physical_count_lower_bound': len(publications),
            'duplicate_count': max(0, count - 1) if count is not None else None, 'resolved_by_budget': resolved,
            'manual_repair': manual, 'stuck_requires_manual': stuck, 'stress_held_pending': stress_pending,
            'justified_in_order': justified, 'unfresh_retry': unfresh, 'unverified_retry': unchecked}


def verify_trees(mapping, packs, objects):
    expected = {START: (FIXTURE['tree_sha1'], None), TARGET: (E3['prepared_candidate']['tree_sha1'], START)}
    for oid, (tree, parent) in expected.items():
        entry = mapping[oid]
        require((entry['tree'], entry['parent']) == (tree, parent), 'registered candidate parent or tree differs')
        packs.restore(oid, entry, objects)
    return True


def cohort(raw, physical, public, clock, rows, truth, candidate):
    """Qualify the study input and actual delivered fault independently of its scheduled label."""
    if not candidate or not physical.subject_ok or not physical.grant_ok or not physical.by_order:
        return None
    first = physical.by_order[0]
    action = public.actions.get(first.action)
    if not action or action['finished'] is None:
        return None
    packet = [c for c in public.actions.values() if c['kind'] == 'context' and c['finished']
              and c['finished'] <= action['issued'] and (c['return'] or {}).get('packet') == physical.packet]
    bound = [c for c in public.actions.values() if c['kind'] == 'submit' and c['input'].get('intent') == physical.intent
             and c['finished'] and c['finished'] <= action['issued'] and (c['return'] or {}).get('disposition') == 'admitted']
    if not packet or not bound or physical.permission(first) is not True:
        return None
    for writer in physical.by_order:
        call = public.actions.get(writer.action)
        if not call or not call['finished']:
            return None
        following = [c for c in public.actions.values() if c['issued'] >= call['finished']]
        automatic = next((c for c in sorted(following, key=lambda item: item['issued'])), None)
        if raw['arm'] != 'loop' and (not automatic or automatic['kind'] != 'reconcile'
                or automatic['actor'] != 'independent_observer' or automatic['input'] != {
                    'delivery': 'automatic', 'attempt': writer.attempt, 'effect': physical.intent['effect']}):
            return None
    if raw['arm'] == 'loop' and any(c['kind'] == 'reconcile' and (c['actor'] != 'executor'
            or c['input'].get('delivery') != 'explicit') for c in public.actions.values()):
        return False
    faults = [row['payload'] for row in rows if row['kind'] == 'fault']
    if raw['track'] == 'clean':
        return not faults and first.command['cut'] is None and action['return'].get('status') == 'acknowledged' and truth == 'landed'
    cycle = E3['fault_cycle']
    expected = E3['stress']['id'] if raw['track'] == E3['stress']['id'] else cycle[E3['seeds'].index(raw['seed']) % len(cycle)]
    if len(faults) != 1:
        return None
    fault = faults[0]
    if (fault.get('action_id') != first.action or fault.get('attempt') != first.attempt or fault.get('candidate') != TARGET
            or fault.get('intervention') != expected or fault.get('seed') != raw['seed'] or first.command.get('cut') != expected
            or action['return'] != {'status': 'timeout'} or fault.get('released') != action['return']):
        return False
    if expected == E3['stress']['id']:
        return first.live_at_cutoff(clock['end'], physical.final)
    if truth == 'unknown':
        return None
    return (truth == ('landed' if expected == 'landed_ack_lost' else 'unlanded')
            and first.dead_by(action['finished']) and first.events['reaped']['returncode'] != 0)


def reconstruct(raw, objects, packs):
    sources = Sources(objects)
    refs = raw.get('artifacts') or {}
    platform = sources.item(refs.get('platform'), 'platform manifest') or {}
    e3 = platform.get('e3') or {}
    journal = sources.read('chronological journal', lambda: journal_rows(objects.item(refs['journal'])))
    rows = journal or []
    candidate = sources.read('registered Git objects', lambda: verify_trees(objects.item(refs['candidates']), packs, objects))
    public = Public(raw['arm'], e3, rows, sources)
    clock = clocks(e3, platform, rows, public, sources)
    physical = Physical(raw, e3, rows, public, clock, sources)
    truth = physical.original(clock['end'])
    original = physical.by_order[0].attempt if physical.by_order else None
    public_complete = bool(journal is not None and public.complete and platform.get('public_history_complete') is True
                           and clock['terminal'] and all(c['finished'] is not None for c in public.actions.values()))
    steps = len(public.actions) if journal is not None and public.complete and clock['terminal'] else None
    delivered, releases = {}, []
    for call in public.actions.values():
        if call['kind'] in ('effect', 'reconcile'):
            value = sources.read('returned observation ' + call['action_id'], lambda: physical.reading(call))
            delivered[call['action_id']] = {'attempt': call['input'].get('attempt'), 'value': value, 'finished': call['finished'],
                                            'pending_reason': ((call['return'] or {}).get('report') or {}).get('reason')
                                            if ((call['return'] or {}).get('report') or {}).get('state') == 'pending' else None}
    for i, writer in enumerate(physical.by_order):
        prior = physical.by_order[i-1] if i else None
        call = public.actions.get(writer.action)
        issued = call['issued'] if call else writer.at('launched')
        admission = sources.read('current authority ' + writer.attempt, lambda: physical.permission(writer))
        releases.append({'attempt': writer.attempt, 'issued': issued, 'authorization': writer.at('permission_checked')
                         if writer.command['arm'] != 'purser' else writer.at('authorized'), 'admitted': admission,
                         'prior_dead': prior.quiescence_at(issued) if prior else None})
    resolvable = uncertainty = None
    if original:
        writer, post = physical.writers[original], physical.post.get(original)
        if post and clock['end'] is not None:
            resolvable = post.disposition(physical.checksum, original, writer.dead_by(post.start)) in ('landed', 'unlanded')
            uncertainty = writer.live_at_cutoff(clock['end'], physical.final) if writer.at('held_at_return') else any(
                c['kind'] == 'reconcile' and c['input'].get('attempt') == original and delivered[c['action_id']]['value'] == 'unknown'
                and not resolvable for c in public.actions.values())
    decisions = [d for d in public.decisions if d['effect'] == physical.intent.get('effect')]
    facts = grade(track=raw['track'], truth=truth, original=original, decisions=decisions, delivered=delivered,
                  releases=releases, publications=physical.publications, whole=physical.external_complete,
                  public_complete=public_complete and bool(candidate), terminal=clock['terminal'], steps=steps,
                  elapsed=clock['elapsed_ns'], manual=clock['manual_repair'], resolvable=resolvable, uncertainty=uncertainty)
    qualified = sources.read('actual registered exposure', lambda: cohort(raw, physical, public, clock, rows, truth, candidate))
    if qualified is not True:
        sources.problem('registered starting input, permission or actual fault exposure unestablished')
    if clock['terminal'] and raw.get('validity') != 'infrastructure_invalid' and clock['terminal'] != raw.get('terminal'):
        sources.problem('raw terminal differs from the original participant exit')
    facts.update(terminal=clock['terminal'], steps=steps, observed_steps=len(public.actions), elapsed_ns=clock['elapsed_ns'],
                 elapsed_lower_ns=clock['elapsed_lower_ns'], public_complete=public_complete, external_complete=bool(physical.external_complete), publications=physical.publications,
                 decisions=decisions, releases=releases, delivered=delivered, candidate_verified=candidate is True, eligible=qualified,
                 diagnostics=sources.problems, scope=physical.binding.get('repository_instance'), intent_digest=physical.checksum)
    return facts
