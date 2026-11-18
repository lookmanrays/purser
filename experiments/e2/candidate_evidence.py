"""Identify case-owned Git snapshots separately from the packs that can supply their bytes."""

import json
import os
import re
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path

from experiments.e1.accounting import WORK, digest, utc_time
from experiments.e1.artifacts import FIXTURE, START, encoded, git, sha
from experiments.e1.capture import response_evidence
from experiments.e1.executor import PARENT
from experiments.e1.measurement import same
from experiments.e1.recovery import FileArchive, sync_directory, write_atomic
from experiments.e1.report import require
from . import attribution

PACK = re.compile(r"objects/([0-9a-f]{64})\.pack\Z")
OBJECT = re.compile(r"objects/([0-9a-f]{64})\.(?:pack|json|bin)\Z")
ERRORS = (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, zipfile.BadZipFile)


def pack_reference(ref):
    return (type(ref) is dict and ref.keys() == {"path", "sha256", "bytes"} and digest(ref.get("sha256"), 64)
            and ref.get("path") == f"objects/{ref['sha256']}.pack"
            and type(ref.get("bytes")) is int and ref["bytes"] >= 0)


def valid_entry(oid, entry):
    return (digest(oid, 40) and type(entry) is dict and entry.get("start") == START
            and digest(entry.get("tree"), 40) and "parent" in entry
            and (entry["parent"] is None and entry["tree"] == FIXTURE["tree_sha1"] if oid == START
                 else digest(entry["parent"], 40) and entry["parent"] != oid)
            and pack_reference(entry.get("pack")))


def checkout_observation(end):
    require(type(end) is dict and digest(end.get("head"), 40) and utc_time(end.get("observed_utc"))
            and end.get("source") in ("after_participant_exit", "stopped_worker_readback"),
            "missing or malformed original checkout observation")
    status = end.get("status_hex")
    require(type(status) is str and re.fullmatch(r"(?:[0-9a-f]{2})*", status) is not None
            and type(end.get("dirty")) is bool and end["dirty"] == bool(status),
            "worktree status and dirty observation disagree")
    if status:
        body = bytes.fromhex(status)
        require(body.endswith(b"\0"), "worktree status lacks its recorded path terminator")
        paths = iter(body[:-1].split(b"\0"))
        for row in paths:
            require(re.fullmatch(rb"(?:[ MADRCUT][ MADRCUT]|\?\?) .+", row, re.DOTALL) and row[:2] != b"  ",
                    "malformed porcelain worktree status")
            if b"R" in row[:2] or b"C" in row[:2]:
                require(bool(next(paths, b"")), "worktree rename or copy lacks its other path")
    return end


def inspected_oids(journal, arm, objects, issue):
    """A successful scoped read has passed WorkerTools' candidate check. A bare action has not."""
    found = set()
    for row in journal:
        action = row.get("payload") if type(row) is dict else None
        if type(action) is not dict or row.get("kind") != "action" or action.get("action") != "context":
            continue
        inputs = action.get("input")
        tool = inputs.get("tool") if type(inputs) is dict else None
        if tool not in ("git", "git_diff", "git_history"):
            continue
        try:
            work, oid = action["subject"], action["candidate"]
            require(action.get("arm") == arm and type(action.get("attempt")) is int and action["attempt"] > 0
                    and work in WORK and inputs == {"work": work, "tool": tool}
                    and attribution.expected(action) == "inspect", "invalid scoped Git request")
            attribution.work_request(action, "inspect")
            if "work_started" not in action:
                continue
            delivered, packet = attribution.delivered(action, objects.item)
            if delivered:
                require(action["delivery"].get("subject") == work, "Git receiver subject differs from the request")
            if delivered and tool == "git_history" and work == PARENT:
                history = packet.get("value") if type(packet) is dict else None
                require(type(history) is list and history and all(digest(o, 40) for o in history)
                        and (oid is None or history[0] == oid), "Git history receiver lacks the selected HEAD")
                found.add(history[0])
            if not digest(oid, 40):
                continue
            if not delivered:
                reply = action.get("return")
                if type(reply) is not dict or "failure" in reply:
                    continue
                require(same(objects.item(action["response_evidence"]), response_evidence(action)),
                        "Git return envelope differs from the scoped invocation")
            found.add(oid)
        except ERRORS as error:
            issue(f"scoped Git read at attempt {action.get('attempt')}: {error}")
    return found


def original_oids(arm, platform, objects, originals, journal, start, issue):
    result = originals.candidate_oids() | inspected_oids(journal, arm, objects, issue)
    end = platform.get("exit_candidate")
    if end is not None:
        try:
            require(type(start) is int and start >= 0, "checkout observation lacks participant release")
            result.add(checkout_observation(end)["head"])
        except ERRORS as error:
            issue(f"exit candidate: {error}")
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


def observe_stage(directory, assets, catalog, claimed, issue, *, original_map=None):
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

    sources = [catalog.loaded[encoded(ref)] for ref in refs.values() if encoded(ref) in catalog.loaded]
    tips = {pack.tip for pack in sources if pack.tip}
    work = directory / "worker"
    if (work / ".git").is_dir():
        try:
            # rev-parse --verify distinguishes a not-yet-installed HEAD from fixture bytes.
            head = git(work, "rev-parse", "--verify", "HEAD^{commit}").decode().strip()
        except subprocess.CalledProcessError:
            head = None
        original = map_entries(original_map, "original candidate map", issue) if original_map is not None else {}
        for oid in sorted({o for o in (*original, *claimed, head) if digest(o, 40)}):
            if oid in tips or oid != head and catalog.alternative(oid, sources):
                continue
            try:
                if oid in original and oid != head and oid not in claimed:
                    tree, *parents = git(work, "show", "-s", "--format=%T %P", oid).decode().split()
                    entry = original[oid]
                    expected = [entry["parent"]] if entry["parent"] else []
                    require(tree == entry["tree"] and parents == expected,
                            "working Git history contradicts original map tree or parent")
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


def map_entries(mapping, source, issue):
    if type(mapping) is not dict:
        issue("malformed candidate map" if source == "candidate" else f"malformed {source}")
        return {}
    entries = {}
    for oid, entry in mapping.items():
        if valid_entry(oid, entry):
            entries[oid] = entry
        else:
            issue(f"malformed {source} entry {oid}")
    return entries


def case_packs(catalog, seals, references, expect_seals, issue):
    """Read the two case-local pack inventories independently, including partial inventories."""
    tips, scoped, claimed, absent = {}, {}, {}, {}
    sealed, referenced = set(), set()
    if not expect_seals and seals is not None:
        issue("case Git seal inventory is outside this registered producer")
        seals = []
    elif type(seals) is not list:
        if expect_seals:
            issue("case Git seal inventory unavailable or malformed")
        seals = []
    for seal in seals:
        if (type(seal) is not dict or seal.keys() != {"candidate", "pack"}
                or not digest(seal["candidate"], 40) or not pack_reference(seal["pack"])):
            issue(f"malformed case Git seal: {seal}")
            continue
        oid, ref = seal["candidate"], seal["pack"]
        key = encoded(ref)
        if key in claimed:
            issue(f"duplicate or conflicting case Git seal for {ref['path']}: {sorted(claimed[key])}, {oid}")
        claimed.setdefault(key, set()).add(oid)
        try:
            pack = catalog.load(ref)
            require(pack.tip == oid, f"asserted {oid} disagrees with original pack tip {pack.tip}")
        except ERRORS as error:
            issue(f"case Git seal {oid} at {ref['path']}: {error}")
            if ref["path"] not in catalog.objects.archive.namelist():
                absent[key] = claimed[key]
            continue
        scoped[key], tips[oid] = pack, pack
        sealed.add((oid, key))

    if references is not None and type(references) is not list:
        issue("malformed case referenced-files inventory")
    for ref in references if type(references) is list else ():
        if type(ref) is not dict or type(ref.get("path")) is not str:
            issue("malformed entry in case referenced-files inventory")
            continue
        if not ref["path"].endswith(".pack"):
            continue
        key = encoded(ref)
        referenced.add(key)
        try:
            pack = catalog.load(ref)
            require(pack.tip is not None, "no unique candidate tip with registered ancestry")
        except ERRORS as error:
            issue(f"referenced case pack {ref['path']}: {error}")
            continue
        scoped[key], tips[pack.tip] = pack, pack
        if expect_seals and (pack.tip, key) not in sealed:
            issue(f"case Git seal inventory lacks referenced pack for {pack.tip} at {ref['path']}")
    if type(references) is list:
        for oid, key in sorted(sealed):
            if key not in referenced:
                issue(f"case referenced-files inventory lacks sealed pack for {oid} at {scoped[key].reference['path']}")
    return tips, scoped, absent


def case_members(catalog, mapping, seals, named, issue, *, capture, references, expect_seals, original_map):
    """Settle participation from case sources before searching pooled contents."""
    members = {oid for oid in named if digest(oid, 40)}
    tips, scoped, absent = case_packs(catalog, seals, references, expect_seals, issue)
    members.update(tips)
    declared = map_entries(mapping, "candidate", issue)
    original = declared if capture else {}
    if original_map is not None:
        original = declared if original_map is mapping else map_entries(original_map, "original candidate map", issue)
    unverified_metadata = {}
    paths = set(catalog.objects.archive.namelist()) if original else set()
    for oid, entry in original.items():
        # This map was obtained from the case's progress BEFORE the collector rebuilt it.
        # Check any surviving local content for a contradiction, even if its own pack was lost.
        ref = entry["pack"]
        dedicated = scoped.get(encoded(ref))
        if dedicated is None and ref["path"] in paths:
            try:
                dedicated = catalog.load(ref)
            except ERRORS as error:
                issue(f"original candidate map {oid} at {ref['path']}: {error}")
                continue
        pack = dedicated or catalog.alternative(oid, scoped.values())
        if dedicated and dedicated.tip != oid:
            issue(f"original candidate map {oid}: its case pack has a different original tip {dedicated.tip}")
        elif pack and pack.commits.get(oid) != (entry["tree"], entry["parent"]):
            issue(f"original candidate map {oid}: declared Git parent or tree disagrees with case objects")
        elif oid not in members:
            members.add(oid)
            if pack is None:
                unverified_metadata[oid] = entry
    for oid, entry in declared.items():
        if oid in members:
            continue
        ref = entry["pack"]
        if absent.get(encoded(ref)) == {oid}:
            # A surviving scoped descendant can corroborate the writer's map plus its
            # matching original seal after their dedicated pack has gone missing.
            cover = catalog.alternative(oid, scoped.values())
            if cover and cover.commits[oid] == (entry["tree"], entry["parent"]):
                members.add(oid)
                continue
        issue(f"candidate map {oid} is unsupported by independent case evidence "
              f"(tree {entry['tree']}, parent {entry['parent']}, {ref['path']}, {ref['bytes']} bytes)")
    return members, declared, tips, scoped, unverified_metadata


def reconcile(catalog, mapping, seals, named, issue, *, capture=False, references=None, expect_seals=True,
              original_map=None, unavailable=None):
    members, declared, selected, scoped, unverified_metadata = case_members(
        catalog, mapping, seals, named, issue, capture=capture, references=references, expect_seals=expect_seals,
        original_map=original_map)
    available_packs = (catalog.loaded.values() if capture and isinstance(catalog.objects.archive, FileArchive)
                       else scoped.values())
    for oid in sorted(members):
        entry = declared.get(oid)
        if entry is not None:
            try:
                pack = catalog.load(entry["pack"])
                require(pack.commits.get(oid) == (entry["tree"], entry["parent"]),
                        "declared Git parent or tree disagrees with original objects")
                selected[oid] = pack
            except ERRORS as error:
                missing_at_capture = capture and entry["pack"]["path"] not in catalog.objects.archive.namelist()
                report = (unavailable or issue) if missing_at_capture else issue
                report(f"candidate map {oid}: {error}")
        if oid not in selected:
            cover = catalog.alternative(oid, available_packs)
            if cover:
                selected[oid] = cover
    missing = members - selected.keys()
    if missing and not capture:
        for name in catalog.objects.archive.namelist():
            if not missing:
                break
            if not PACK.fullmatch(name):
                continue
            try:
                pack = catalog.load(catalog.reference(name))
            except ERRORS:
                continue
            for oid in missing.intersection(pack.commits):
                selected[oid] = pack
            missing -= selected.keys()

    # A missing pre-capture pack can leave the original map as the only binding. If
    # recovered content disproves its metadata, keep the source dispute, not a graded slot.
    for oid, entry in unverified_metadata.items():
        if oid in selected and selected[oid].commits[oid] != (entry["tree"], entry["parent"]):
            issue(f"original candidate map {oid}: recovered Git parent or tree contradicts the sole case binding")
            members.remove(oid)
            del selected[oid]
    for oid in sorted(members):
        if oid not in selected:
            (unavailable or issue)(f"case candidate Git bytes unavailable for {oid}")
        if not capture and oid not in declared:
            (issue if oid in selected else unavailable or issue)(f"candidate map lacks case observation {oid}")
    entries = {oid: pack.entry(oid) for oid, pack in sorted(selected.items())}
    repos = {oid: pack.repo for oid, pack in sorted(selected.items())}
    return members, entries, repos
