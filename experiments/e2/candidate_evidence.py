"""Identify case-owned Git snapshots separately from the packs that can supply their bytes."""

import json
import os
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from experiments.e1.accounting import digest
from experiments.e1.artifacts import FIXTURE, START, encoded, git, sha
from experiments.e1.recovery import FileArchive, sync_directory, write_atomic
from experiments.e1.report import require
from .public_sources import Originals

PACK = re.compile(r"objects/([0-9a-f]{64})\.pack\Z")
OBJECT = re.compile(r"objects/([0-9a-f]{64})\.(?:pack|json|bin)\Z")
ERRORS = (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile)


def pack_reference(ref):
    return (type(ref) is dict and digest(ref.get("sha256"), 64)
            and ref.get("path") == f"objects/{ref['sha256']}.pack"
            and type(ref.get("bytes")) is int and ref["bytes"] >= 0)


def valid_entry(oid, entry):
    return (digest(oid, 40) and type(entry) is dict and entry.get("start") == START
            and digest(entry.get("tree"), 40) and "parent" in entry
            and (entry["parent"] is None if oid == START else digest(entry["parent"], 40))
            and pack_reference(entry.get("pack")))


def original_oids(arm, platform, objects, originals):
    """Read the actual original public candidates, including a preserved pre-change head."""
    def subjects(source):
        return {oid for publication in source.publications for state, oid in publication.states.values()
                if digest(oid, 40)}
    result = subjects(originals)
    pause = platform.get("checkpoint_measurement")
    if type(pause) is dict and pause.get("submission_source") and not originals.complete:
        earlier = Originals(arm, dict(platform, treatment_trace=pause["submission_source"],
                                      final_public=pause.get("checkpoint")), objects.item, lambda _: None)
        result.update(subjects(earlier))
    return result


@dataclass
class Pack:
    repo: Path
    reference: dict
    commits: dict
    tip: str | None

    def entry(self, oid):
        tree, parent = self.commits[oid]
        return {"start": START, "tree": tree, "parent": parent, "pack": self.reference}


class Catalog:
    """One reader's content index. Loading bytes alone never enrolls a case member."""

    def __init__(self, objects, packs):
        self.objects, self.packs = objects, packs
        self.loaded = {}

    def load(self, ref):
        require(pack_reference(ref), "invalid Git pack reference")
        key = encoded(ref)
        if key in self.loaded:
            return self.loaded[key]
        repo = self.packs.unpack(ref, self.objects)
        items = git(repo, "cat-file", "--batch-all-objects", "--batch-check=%(objectname) %(objecttype)")
        ids = [line.split()[0] for line in items.decode().splitlines() if line.endswith(" commit")]
        require(START in ids, "Git pack lacks the registered starting commit")
        history = git(repo, "log", "--no-walk", "--format=%H %T %P", *ids).decode().splitlines()
        links = {oid: (tree, parents) for oid, tree, *parents in (line.split() for line in history)}
        require(links[START] == (FIXTURE["tree_sha1"], []), "Git pack has the wrong starting tree or parent")
        # Only the writer's single-parent descendants can supply a candidate.
        commits = {START: (FIXTURE["tree_sha1"], None)}
        for oid in git(repo, "rev-list", "--topo-order", "--reverse", *ids).decode().splitlines():
            tree, parents = links[oid]
            if len(parents) == 1 and parents[0] in commits:
                commits[oid] = (tree, parents[0])
        tips = set(commits) - {parent for _, parent in commits.values()}
        tip = next(iter(tips)) if len(tips) == 1 and len(commits) == len(links) else None
        result = Pack(repo, ref, commits, tip)
        self.loaded[key] = result
        return result

    def reference(self, name):
        match = PACK.fullmatch(name)
        require(match is not None, "invalid pack object path")
        data = self.objects.archive.read(name)
        return {"path": name, "sha256": match[1], "bytes": len(data)}

    def alternative(self, oid, sources):
        return next((pack for pack in sources if oid in pack.commits), None)


def preserve_damaged_objects(assets):
    """Keep misaddressed original bytes without publishing them at a false digest."""
    archive = assets.archive
    if not isinstance(archive, FileArchive):
        return {}
    record = archive.directory / "damaged-objects.json"
    damaged = json.loads(record.read_bytes()) if record.exists() else {}
    for name in archive.namelist():
        match = OBJECT.fullmatch(name)
        if not match:
            continue
        path = archive.directory / name
        data = path.read_bytes()
        if sha(data) != match[1]:
            damaged[name] = assets.put(data, "bin")
            write_atomic(record, encoded(damaged))
            os.replace(path, archive.directory / (".damaged-" + path.name))
            sync_directory(path.parent)
            assets.present.discard(name)
    return damaged


def observe_stage(directory, assets, catalog, claimed, issue):
    """Read complete case-local publications while the collector still owns its stage."""
    refs = {}
    for name in assets.archive.namelist() if isinstance(assets.archive, FileArchive) else ():
        if PACK.fullmatch(name):
            try:
                refs[name] = catalog.reference(name)
                catalog.load(refs[name])
            except ERRORS as error:
                issue(f"case pack {name}", error)

    # The writer indexes this full pack before the atomic case-object write. Its bare
    # repo has no branch refs. Look only in this attempt's own Git pack directory.
    for path in sorted((directory / "private-objects/objects/pack").glob("pack-*.pack")):
        try:
            data = path.read_bytes()
            name = f"objects/{sha(data)}.pack"
            if name not in refs:
                refs[name] = assets.put(data, "pack")
                catalog.load(refs[name])
        except ERRORS as error:
            issue(f"private pack {path.name}", error)

    sources = list(catalog.loaded.values())
    tips = {pack.tip for pack in sources if pack.tip}
    work = directory / "worker"
    if (work / ".git").is_dir():
        try:
            # rev-parse --verify distinguishes a not-yet-installed HEAD from fixture bytes.
            head = git(work, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        except subprocess.CalledProcessError:
            head = None
        for oid in sorted({o for o in (*claimed, head) if digest(o, 40)}):
            if oid in tips or oid != head and catalog.alternative(oid, sources):
                continue
            try:
                data = git(work, "pack-objects", "--stdout", "--revs", data=(oid + "\n").encode())
                ref = assets.put(data, "pack")
                refs[ref["path"]] = ref
                pack = catalog.load(ref)
                require(pack.tip == oid, "working Git copy has no matching candidate tip")
                sources.append(pack)
                tips.add(oid)
            except ERRORS as error:
                issue(f"working Git candidate {oid}", error)

    seals = []
    for ref in refs.values():
        try:
            pack = catalog.load(ref)
            require(pack.tip is not None, "pack has no unique candidate tip with registered ancestry")
            seals.append({"candidate": pack.tip, "pack": ref})
        except ERRORS as error:
            issue(f"case pack {ref['path']}", error)
    return sorted(seals, key=lambda seal: seal["pack"]["path"])


def reconcile(catalog, mapping, seals, named, issue, *, capture=False, references=()):
    """Verify each observation and recover its bytes without giving pool contents case identity."""
    named = {oid for oid in named if digest(oid, 40)}
    declared = {}
    for oid, entry in mapping.items() if type(mapping) is dict else ():
        if valid_entry(oid, entry):
            declared[oid] = entry
        else:
            issue(f"malformed candidate entry {oid}")
    if type(mapping) is not dict:
        issue("malformed candidate map")
    scoped, selected, seal_oids = {}, {}, set()
    legacy = seals is None
    if type(seals) is not list:
        issue("case Git seal inventory unavailable")
        seals = []
        for ref in references if type(references) is list else ():
            if pack_reference(ref):
                try:
                    pack = catalog.load(ref)
                    if pack.tip:
                        seals.append({"candidate": pack.tip, "pack": ref})
                except ERRORS as error:
                    issue(f"referenced case pack: {error}")
    for seal in seals:
        if (type(seal) is not dict or not digest(seal.get("candidate"), 40)
                or not pack_reference(seal.get("pack"))):
            issue("malformed case Git seal")
            continue
        oid, ref = seal["candidate"], seal["pack"]
        try:
            pack = catalog.load(ref)
        except ERRORS as error:
            issue(f"case Git seal {oid}: {error}")
            named.add(oid)
            continue
        if pack.tip != oid:
            issue(f"case Git seal disagrees with original pack tip for {oid}")
            continue
        scoped[ref["path"]] = pack
        seal_oids.add(oid)
        selected[oid] = pack
    named.update(seal_oids)
    for oid, entry in declared.items():
        ref = entry["pack"]
        if not (capture or legacy or oid in named or ref["path"] in scoped):
            issue(f"candidate map {oid} is unsupported by the case Git seals or original publications")
            continue
        try:
            pack = catalog.load(ref)
        except ERRORS as error:
            issue(f"candidate map {oid}: {error}")
            pack = catalog.alternative(oid, scoped.values())
            if pack is None:
                if capture or legacy:
                    named.add(oid)  # A case-bound original entry with presently unavailable bytes.
                continue
        if pack.commits.get(oid) != (entry["tree"], entry["parent"]):
            issue(f"candidate map {oid}: declared Git parent or tree disagrees with original objects")
            continue
        selected[oid] = pack
        named.add(oid)
    for oid in named - selected.keys():
        alternative = catalog.alternative(oid, scoped.values())
        if alternative:
            selected[oid] = alternative
    if not capture and named - selected.keys():
        # Seek content for specific independently bound OIDs. A foreign tip, even in a
        # perfectly valid full pack, cannot add to this case's membership.
        missing = named - selected.keys()
        for name in catalog.objects.archive.namelist():
            if not missing:
                break
            if not PACK.fullmatch(name) or name in scoped:
                continue
            try:
                pack = catalog.load(catalog.reference(name))
            except ERRORS:
                continue
            for oid in missing.intersection(pack.commits):
                selected[oid] = pack
            missing -= selected.keys()

    for oid in sorted(named):
        if oid not in selected:
            issue(f"case candidate Git bytes unavailable for {oid}")
        if not capture and oid not in declared:
            issue(f"candidate map lacks case observation {oid}")
    entries = {oid: pack.entry(oid) for oid, pack in sorted(selected.items())}
    repos = {oid: pack.repo for oid, pack in sorted(selected.items())}
    return named, entries, repos
