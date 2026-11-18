"""Read the frozen E3 archive and verify each published number against its original sources."""

from pathlib import Path
import re
import tempfile
import zipfile

from experiments.e1.artifacts import MANIFEST, REGISTRATION, sha
from experiments.e1.measurement import same
from experiments.e1.recovery import input_identity
from experiments.e1.report import GitPacks, Objects, parse, require, wire_valid

from .observations import ERRORS
from .scoring import raw_score, reconstruct


def derive_case(raw, archive):
    """Return the v1 primary score while producing or reading a single case."""
    with tempfile.TemporaryDirectory(prefix='purser-e3-score-') as tmp:
        facts = reconstruct(raw, Objects(archive), GitPacks(Path(tmp)))
    return raw_score(raw, facts)


def inventory(raw, objects):
    artifacts = raw['artifacts']
    platform = objects.item(artifacts['platform'])
    journal = objects.item(artifacts['journal'])
    candidates = objects.item(artifacts['candidates'])
    evidence = platform.get('e3', {})
    nested = [evidence, journal, candidates]
    if evidence.get('kernel'):
        nested.append(objects.item(evidence['kernel']))
    catalog = platform.get('referenced_files')
    require(type(catalog) is list, 'referenced-file manifest unavailable')
    declared = {r['path']: r for r in catalog}
    require(len(declared) == len(catalog), 'duplicate referenced-file entry')
    for ref in objects.references(nested):
        require(same(declared.get(ref['path']), ref), 'original artifact missing from manifest: ' + ref['path'])
    for ref in catalog:
        objects.blob(ref)
    offline = platform.get('offline_scoring')
    if offline and offline.get('end_ns') is not None:
        original_ref = offline['input_platform']
        original = objects.item(original_ref)
        require(same(declared.get(original_ref['path']), original_ref)
                and same(original, {**{k:v for k,v in platform.items() if k != 'offline_scoring'},
                                    'referenced_files': [r for r in catalog if r != original_ref]}),
                'offline scoring input differs from the published case evidence')
        if platform.get('clock_stopped'):
            require(platform['end_monotonic_ns'] <= offline['start_ns'] <= offline['end_ns'],
                    'offline scoring overlaps the participant clock')


def inspect_series(directory, *, strict=False):
    from .suite import schedule, validate_freeze
    directory = Path(directory)
    schema = parse((REGISTRATION / 'raw.schema.json').read_bytes())
    records, disputes = {}, []
    with zipfile.ZipFile(directory / 'raw.zip') as archive, tempfile.TemporaryDirectory(prefix='purser-e3-readback-') as tmp:
        files = archive.namelist()
        require(len(set(files)) == len(files), 'duplicate archive member')
        registration = archive.read('freeze.json')
        detached = directory / 'freeze.json'
        if detached.exists():
            require(detached.read_bytes() == registration, 'detached and archived registration differ')
        freeze = parse(registration)
        replicate = validate_freeze(freeze)
        units = list(schedule())
        keys = [f'{track}/{seed}/{arm}.json' for track, seed, _, arm in units]
        for name in files:
            prefix, _, tail = name.partition('/')
            if name == 'freeze.json':
                continue
            if prefix in ('started', 'runs'):
                require(tail in keys, 'unregistered E3 archive unit ' + name)
            else:
                require(re.fullmatch(r'objects/[0-9a-f]{64}\.(json|bin|pack)', name), 'invalid object member ' + name)
                if strict:
                    require(sha(archive.read(name)) == tail.split('.')[0], 'invalid content address ' + name)
        started = [name[8:] for name in files if name.startswith('started/')]
        require(started == [key for key in keys if key in started], 'registered E3 trials started out of order')
        objects, packs = Objects(archive), GitPacks(Path(tmp))
        for (track, seed, rank, arm), key in zip(units, keys, strict=True):
            member = 'runs/' + key
            if member not in files and key not in started:
                continue
            issues = []
            raw, facts = {}, {}
            try:
                require(member in files, 'started trial has no result')
                raw = parse(archive.read(member))
                identity = [raw[k] for k in ('suite', 'experiment', 'mode', 'track', 'seed', 'arm', 'replicate')]
                require(same(identity, [MANIFEST['id'], 'E3', 'scripted', track, seed, arm, replicate]), 'raw unit identity mismatch')
                facts = reconstruct(raw, objects, packs)
                if not wire_valid(raw, schema, schema):
                    issues.append('raw record violates the M0 schema')
                if not same(raw.get('score'), raw_score(raw, facts)):
                    issues.append('published score differs from independent reconstruction')
                for k in ('steps', 'elapsed_ns'):
                    if facts[k] is not None and not same(facts[k], raw['cost'].get(k)):
                        issues.append(f'published {k} differs from original measurement')
                if raw['validity'] == 'complete':
                    if facts['eligible'] is not True or not facts['public_complete'] or not facts['external_complete']:
                        issues.append('claimed complete run lacks the registered exposure, public history or physical endpoint')
                    if track == 'primary' and raw_score(raw, facts) is None:
                        issues.append('completed primary run lacks an attributable score')
                    issues.extend(facts['diagnostics'])
                if not same(raw.get('inputs'), input_identity(freeze, arm)):
                    facts['eligible'] = None
                    issues.append('case inputs differ from the precollection registration')
                marker = {'track': track, 'seed': seed, 'rank': rank, 'arm': arm}
                if key not in started or not same(parse(archive.read('started/' + key)), marker):
                    facts['eligible'] = None
                    issues.append('original start marker unavailable or mismatched')
                if strict:
                    inventory(raw, objects)
            except ERRORS as error:
                issues.append(str(error))
            records[track, seed, arm] = {'raw': raw, 'facts': facts, 'issues': issues}
            if issues:
                disputes.append(f"{key}: {' | '.join(issues)}")
        count = sum('runs/' + key in files for key in keys)
        if strict and replicate == 1:
            require(count == len(units), f'official E3 replicate 1 requires all {len(units)} scheduled records')
        if strict and disputes:
            raise ValueError(' | '.join(disputes))
        stats = {'registered': len(units), 'started': len(started), 'records': count, 'replicate': replicate,
                 'missing_registered': len(units) - count, 'git_packs': len(packs.repos), 'source_commit': freeze['source_commit'],
                 'registration_sha256': sha(registration), 'replication_reason': freeze.get('replication_reason')}
    return records, stats


def render(directory):
    from .summary import render_summary
    cases, stats = inspect_series(directory)
    return render_summary(cases, stats)


def verify_candidates(directory):
    from .summary import render_summary
    cases, stats = inspect_series(directory, strict=True)
    published = Path(directory) / 'report.md'
    if published.exists():
        require(published.read_bytes() == render_summary(cases, stats).encode(), 'published report differs from original evidence')
    return stats
