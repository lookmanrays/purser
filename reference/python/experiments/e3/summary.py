"""Registered E3 thresholds, paired costs and complete scheduled coverage."""

from experiments.e1.artifacts import MANIFEST, START

from .observations import E3, TARGET

ARMS = MANIFEST['units']['arm_order']
SEEDS = E3['seeds']
COMPARATORS = ('loop', 'loop_equal_verifier')


def qualified(record):
    facts = (record or {}).get('facts', {})
    return facts if facts.get('eligible') is True else {}


def aggregate(records):
    result = {}
    for arm in ARMS:
        primary = [qualified(records.get(('primary', seed, arm))) for seed in SEEDS]
        positive = any((f.get('physical_count_lower_bound') or 0) > 1 or (f.get('duplicate_count') or 0) > 0 for f in primary)
        adverse = any(f.get('stuck_requires_manual') is True for f in primary)
        wrong = any(f.get('correct_reconciliation') is False or f.get('resolved_by_budget') is False for f in primary)
        absolute_known = all(all(f.get(k) is not None for k in ('duplicate_count', 'correct_reconciliation', 'resolved_by_budget')) for f in primary)
        comparison_known = all(all(f.get(k) is not None for k in ('duplicate_count', 'stuck_requires_manual')) for f in primary)
        result[arm] = {'absolute': False if positive or wrong else True if absolute_known else None,
                       'original_no_advantage': True if positive or adverse else False if comparison_known else None,
                       'primary': primary}
    clean = [qualified(records.get(('clean', E3['clean_seed'], a))) for a in ('purser', 'loop')]
    healthy = all(f.get('resolved_by_budget') is True and f.get('duplicate_count') == 0 and f.get('steps') is not None for f in clean)
    overhead = clean[0]['steps'] > 2 * clean[1]['steps'] if healthy else None
    return result, overhead


def shown(value):
    if value is None:
        return '?'
    if type(value) is bool:
        return 'yes' if value else 'no'
    return str(value)


def rate(primary, field, *, total=False):
    values = [r[field] for r in primary if r.get(field) is not None]
    numerator = sum(values) if total else sum(bool(v) for v in values)
    return f'{numerator} / {len(values)} / {len(primary)}'


def difference(first, other, key):
    if first.get(key) is None or other.get(key) is None:
        return None
    return first[key] - other[key]


def render_summary(records, stats):
    by_arm, overhead = aggregate(records)
    p = by_arm['purser']
    out = ['# E3 scripted · Recovery of an ambiguous Git update', '',
           f"Replicate {stats['replicate']} retains {stats['records']} of {stats['registered']} scheduled records. "
           f"The prepared commit is `{TARGET}`. The frozen source is `{stats['source_commit']}`.", '',
           f"Purser’s registered absolute threshold result is **{shown(p['absolute'])}**. "
           f"The original no-advantage predicate is **{shown(p['original_no_advantage'])}**. "
           'The absolute threshold requires all 20 original outcomes to be reconciled correctly, with zero duplicates and an effective update by the budget. '
           'Any Purser duplicate or ordinary stuck state requiring manual repair triggers the original no-advantage predicate. The paired tables give the outcomes for the two comparisons.', '',
           f'Each case binds `{START}` on `refs/heads/notify` to a fresh repository instance and a seed-specific intent. '
           'The protected branch reflog gives actual transitions. The named receipt, its creation history and the supervised writer establish what the first attempt did. '
           'The participant’s original call return and published decision establish what it knew before completion or another dispatch. '
           'Purser’s authority and observer artifacts also replay from its original events. Each trial’s case record names the evidence. [Raw trial archive](raw.zip)', '',
           '## Primary outcomes', '',
           'Each entry gives the numerator, number of measurable cases and 20 scheduled seeds. Duplicate transitions counts every publication after the first. '
           'A question mark denotes an unavailable measurement or inconclusive predicate.', '',
           '| Arm | Correct reconciliation | Resolved by budget | Cases with duplicates | Duplicate transitions | Stuck needing repair | Manual repair performed | Incomplete or missing records |',
           '| --- | --- | --- | --- | --- | --- | --- | ---: |']
    for arm in ARMS:
        primary = by_arm[arm]['primary']
        fields = [rate(primary, k) for k in ('correct_reconciliation', 'resolved_by_budget', 'duplicate_count')]
        fields += [rate(primary, 'duplicate_count', total=True), rate(primary, 'stuck_requires_manual'), rate(primary, 'manual_repair')]
        incomplete = sum((records.get(('primary', s, arm)) or {}).get('raw', {}).get('validity') != 'complete' for s in SEEDS)
        out.append('| ' + ' | '.join([arm, *fields, str(incomplete)]) + ' |')
    if stats.get('replication_reason'):
        out.extend(['', 'Replication reason: ' + stats['replication_reason']])
    for arm in ARMS:
        out.extend(['', f'## Primary coverage · {arm}', '',
                    '| Seed | Validity / exit | Original actual / declared | Justified before retry or completion | Correct | Effects | Duplicates | Resolved | Stuck | Manual | Steps | Elapsed ns |',
                    '| ---: | --- | --- | --- | --- | ---: | ---: | --- | --- | --- | ---: | ---: |'])
        for seed in SEEDS:
            r = records.get(('primary', seed, arm)) or {}
            raw, facts = r.get('raw', {}), r.get('facts', {})
            exit_text = f"{raw.get('validity', 'missing')} / {facts.get('terminal') or raw.get('terminal', '?')}"
            if r.get('issues'):
                exit_text += ' (disputed)'
            original = f"{shown(facts.get('original_truth'))} / {shown(facts.get('original_report'))}"
            if facts.get('decisions'):
                original += ' (' + str(facts['decisions'][0]['basis']) + ')'
            elapsed = shown(facts.get('elapsed_ns'))
            if elapsed == '?' and facts.get('elapsed_lower_ns') is not None:
                elapsed = '≥' + str(facts['elapsed_lower_ns'])
            effects = shown(facts.get('effect_count'))
            if effects == '?' and (facts.get('physical_count_lower_bound') or 0) > 0:
                effects = f"≥{facts['physical_count_lower_bound']}"
            steps = shown(facts.get('steps'))
            if steps == '?' and facts.get('observed_steps'):
                steps = f"≥{facts['observed_steps']}"
            out.append('| ' + ' | '.join([str(seed), exit_text, original, shown(facts.get('justified_in_order')),
                       shown(facts.get('correct_reconciliation')), effects, shown(facts.get('duplicate_count')),
                       shown(facts.get('resolved_by_budget')), shown(facts.get('stuck_requires_manual')),
                       shown(facts.get('manual_repair')), steps, elapsed]) + ' |')
    out.extend(['', '## Paired public costs', '',
                'Each difference subtracts the comparator from Purser for the same track and seed. Positive values mean Purser used more public calls or elapsed time.', '',
                '| Track / seed | Steps vs loop | Elapsed ns vs loop | Steps vs equal verifier | Elapsed ns vs equal verifier |',
                '| --- | ---: | ---: | ---: | ---: |'])
    for track, seed in [*(('primary', seed) for seed in SEEDS), ('clean', E3['clean_seed']), (E3['stress']['id'], E3['stress']['seed'])]:
        treatment = qualified(records.get((track, seed, 'purser')))
        differences = [shown(difference(treatment, qualified(records.get((track, seed, arm))), field))
                       for arm in COMPARATORS for field in ('steps', 'elapsed_ns')]
        out.append(f'| {track} / {seed} | ' + ' | '.join(differences) + ' |')
    out.extend(['', '## Paired primary outcomes', '',
                'The differences give Purser minus each comparator for a correct original disposition, duplicate transitions and resolution by budget. A true outcome counts as one.', '',
                '| Seed | Correct vs loop | Duplicates vs loop | Resolved vs loop | Correct vs equal verifier | Duplicates vs equal verifier | Resolved vs equal verifier |',
                '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |'])
    for seed in SEEDS:
        treatment = qualified(records.get(('primary', seed, 'purser')))
        differences = [shown(difference(treatment, qualified(records.get(('primary',seed,arm))), field))
                       for arm in COMPARATORS for field in ('correct_reconciliation','duplicate_count','resolved_by_budget')]
        out.append(f'| {seed} | ' + ' | '.join(differences) + ' |')
    out.extend(['', '## Matched clean control', '',
                '| Arm | Exit | Acknowledged clean exposure | Effective updates | Resolved | Steps | Elapsed ns |',
                '| --- | --- | --- | ---: | --- | ---: | ---: |'])
    for arm in ARMS:
        f = (records.get(('clean', E3['clean_seed'], arm)) or {}).get('facts', {})
        out.append('| ' + ' | '.join([arm, *(shown(f.get(k)) for k in ('terminal', 'eligible', 'effect_count', 'resolved_by_budget', 'steps', 'elapsed_ns'))]) + ' |')
    out.extend(['', 'Purser exceeded twice the plain loop’s steps on the eligible clean pair: **' + shown(overhead) + '**.', '',
                '## Inconclusive-observation stress', '',
                '| Arm | Exit | Original actual / declared | Actionable pending held | Additional physical attempts | Effects | Remaining uncertainty and public next action | Steps | Elapsed ns |',
                '| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: |'])
    for arm in ARMS:
        f = (records.get((E3['stress']['id'], E3['stress']['seed'], arm)) or {}).get('facts', {})
        last = (f.get('decisions') or [{}])[-1]
        detail = ' · '.join(str(v) for v in (last.get('reason'), last.get('next_action')) if v) or '?'
        out.append('| ' + ' | '.join([arm, shown(f.get('terminal')), f"{shown(f.get('original_truth'))} / {shown(f.get('original_report'))}",
                   shown(f.get('stress_held_pending')), shown(max(0, len(f['releases'])-1) if f.get('external_complete') else None),
                   shown(f.get('effect_count')), detail, shown(f.get('steps')), shown(f.get('elapsed_ns'))]) + ' |')
    notes = []
    for key, r in records.items():
        facts = r.get('facts', {})
        details = list(dict.fromkeys([*r['issues'], *facts.get('diagnostics', [])]))
        if facts.get('unfresh_retry'):
            details.append('no properly ordered fresh permission for ' + ', '.join(facts['unfresh_retry']))
        if facts.get('unverified_retry'):
            details.append('retry permission unverified for ' + ', '.join(facts['unverified_retry']))
        if details:
            notes.append('- ' + ' / '.join(map(str, key)) + ' — ' + ' · '.join(details))
    if notes:
        out.extend(['', '## Unavailable or conflicting evidence', '', *notes])
    out.extend(['', 'The scripted budget permits 96 public steps, 60 elapsed seconds and zero model calls. The scripted series uses zero model tokens and spend. Real-model outcomes require a separate series.', ''])
    return '\n'.join(out)
