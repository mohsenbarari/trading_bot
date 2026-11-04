"""Pinned public controller capsule outside the model's integration mount.

Install/promote only from a genuine matching accepted business checkpoint.
The fixed host launcher may execute this capsule when live controller imports
break. No secret, environment, Git directory or application data is copied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import subprocess
import sys
import shutil
from contextlib import contextmanager

STORE = Path("/var/lib/two-site-qoder-maintenance")
POLICY = "docs/refactor/two-server/templates/automation-maintenance-policy.json"
CRITICAL = frozenset({"scripts/two_site_qoder_maintenance.py", "scripts/two_site_maintenance_lkg.py",
    "scripts/two_site_qoder_host.py", "scripts/two_site_qoder_business_reconcile.py",
    "scripts/two_site_assignment_scope.py", "scripts/two_site_semantic_attempts.py",
    "scripts/two_site_qoder_authority.py", "scripts/two_site_qoder_author.py",
    "scripts/two_site_qoder_author_resume.py", "scripts/two_site_maintenance_quota.py",
    "scripts/two_site_maintenance_transition_record.py", "scripts/two_site_maintenance_transition_publish.py",
    "scripts/two_site_maintenance_transition_storage.py", "scripts/two_site_maintenance_transition_startup.py"})
CRITICAL = CRITICAL | frozenset({"scripts/two_site_coordinator_sandbox.py",
    "scripts/two_site_review_bridge.py", "scripts/two_site_codex_closeout_review.sh",
    "scripts/two_site_codex_final_review.sh"})
GOLDENS = ("tests/test_two_site_qoder_maintenance.py", "tests/test_two_site_maintenance_lkg.py",
    "tests/test_two_site_qoder_host.py", "tests/test_two_site_qoder_business_reconcile.py",
    "tests/test_two_site_maintenance_transition_record.py", "tests/test_two_site_semantic_attempts.py",
    "tests/test_two_site_qoder_author_resume.py", "tests/test_two_site_maintenance_quota.py",
    "tests/test_two_site_qoder_author.py",
    "tests/test_two_site_qoder_authority.py", "tests/test_two_site_coordinator_sandbox.py",
    "tests/test_two_site_review_bridge.py",
    "tests/test_two_site_autopilot_monitor_preflight.py")
HOST_LAUNCHER = '''"""Fixed public LKG entry; no live-worktree imports or model tools."""
import hashlib,json,os,stat,sys
from pathlib import Path
base=Path(__file__).resolve().parent
def decode(raw):
    def pairs(items):
        value={}
        for key,item in items:
            assert key not in value
            value[key]=item
        return value
    def invalid(value): raise ValueError('LKG_NONFINITE')
    value=json.loads(raw,object_pairs_hook=pairs,parse_constant=invalid)
    assert type(value) is dict
    return value
def read(p,limit):
    assert p.resolve(strict=True)==p
    fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        assert stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and st.st_nlink==1
        assert not st.st_mode&0o022 and 0<st.st_size<=limit
        raw=os.read(fd,limit+1)
        assert len(raw)==st.st_size
        return raw
    finally: os.close(fd)
pointer=decode(read(base/'current.json',65536))
assert set(pointer)=={'schema','head','manifest_sha256'} and pointer['schema']=='QODER_LKG_POINTER_V1'
head=pointer['head']
assert type(head) is str and len(head)==40 and set(head)<=set('0123456789abcdef')
capsule=base/'capsules'/head
manifest_raw=read(capsule/'manifest.json',250000)
assert hashlib.sha256(manifest_raw).hexdigest()==pointer['manifest_sha256']
manifest=decode(manifest_raw)
entry=capsule/'scripts/two_site_maintenance_lkg.py'
entry_raw=read(entry,2500000)
assert hashlib.sha256(entry_raw).hexdigest()==manifest['files']['scripts/two_site_maintenance_lkg.py']['sha256']
os.execv(sys.executable,[sys.executable,'-I','-B',str(entry),*sys.argv[1:]])
'''


def critical_paths(files):
    return sorted(name for name in files if name in CRITICAL
                  or name.startswith("scripts/two_site_maintenance_transition_")
                  or name.startswith("scripts/two_site_quota_"))


class LKGError(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise LKGError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def decode(data):
    def pairs(items):
        value = {}
        for key, item in items:
            need(key not in value, "LKG_DUPLICATE_KEY")
            value[key] = item
        return value
    def invalid(_value):
        raise LKGError("LKG_NONFINITE")
    try:
        value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
        need(type(value) is dict, "LKG_OBJECT")
        return value
    except (ValueError, UnicodeError, RecursionError) as error:
        if isinstance(error, LKGError):
            raise
        raise LKGError("LKG_INVALID_JSON") from None


def raw(path, limit=2500000, *, allow_empty=False):
    path = Path(path)
    need(path.resolve(strict=True) == path, "LKG_ALIAS")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1
             and not before.st_mode & 0o022 and type(allow_empty) is bool
             and (0 <= before.st_size if allow_empty else 0 < before.st_size)
             and before.st_size <= limit, "LKG_CUSTODY")
        out = bytearray()
        while len(out) <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - len(out)))
            if not chunk:
                break
            out.extend(chunk)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        after = os.fstat(fd); current = path.lstat()
        need(len(out) == before.st_size and all(getattr(before, k) == getattr(after, k)
             == getattr(current, k) for k in fields), "LKG_CHANGED")
        return bytes(out)
    finally:
        os.close(fd)


def public_path(name):
    return (type(name) is str and (re.fullmatch(r"scripts/(?:__init__|two_site_[A-Za-z0-9_]+)\.py", name)
        or re.fullmatch(r"scripts/two_site_[A-Za-z0-9_]+\.sh", name)
        or re.fullmatch(r"docs/refactor/two-server/templates/[A-Za-z0-9_.-]+\.(?:json|md)", name)
        or name in GOLDENS))


def verify_capsule(capsule, manifest_sha256):
    capsule = Path(capsule)
    need(capsule.resolve(strict=True) == capsule, "LKG_CAPSULE_ALIAS")
    manifest_raw = raw(capsule / "manifest.json", 250000)
    need(sha(manifest_raw) == manifest_sha256, "LKG_MANIFEST_SHA")
    value = decode(manifest_raw)
    need(type(value) is dict and set(value) == {"schema", "head", "checkpoint_sha256", "files", "critical_paths", "golden_tests"}
         and value["schema"] == "QODER_LKG_CONTROLLER_V1" and type(value["head"]) is str
         and type(value["checkpoint_sha256"]) is str and re.fullmatch("[0-9a-f]{40}", value["head"])
         and re.fullmatch("[0-9a-f]{64}", value["checkpoint_sha256"])
         and type(value["files"]) is dict and 1 <= len(value["files"]) <= 512
         and value["critical_paths"] == critical_paths(value["files"]) and value["golden_tests"] == list(GOLDENS)
         , "LKG_MANIFEST")
    required = {"scripts/two_site_qoder_maintenance.py", "scripts/two_site_maintenance_lkg.py",
        "scripts/two_site_qoder_host.py", "scripts/two_site_qoder_authority.py",
        "scripts/two_site_coordinator_sandbox.py", "scripts/two_site_review_bridge.py",
        "scripts/two_site_codex_closeout_review.sh", "scripts/two_site_codex_final_review.sh",
        "scripts/two_site_qoder_business_reconcile.py", "scripts/two_site_maintenance_quota.py",
        POLICY, *GOLDENS}
    need(required
         <= set(value["files"]), "LKG_INCOMPLETE")
    total = 0
    for name, row in value["files"].items():
        need(public_path(name) and type(row) is dict and set(row) == {"bytes", "sha256"}
             and type(row["bytes"]) is int and 0 < row["bytes"] <= 2500000
             and type(row["sha256"]) is str and re.fullmatch("[0-9a-f]{64}", row["sha256"]), "LKG_FILE_CATALOG")
        data = raw(capsule / name)
        need(len(data) == row["bytes"] and sha(data) == row["sha256"], "LKG_FILE_SHA")
        total += len(data)
        need(total <= 25000000, "LKG_TOTAL_BUDGET")
    actual = {str(path.relative_to(capsule)) for path in capsule.rglob("*") if path.is_file()}
    need(actual == set(value["files"]) | {"manifest.json"}, "LKG_EXTRA_FILE")
    return value


def _write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    need(path.parent.resolve(strict=True) == path.parent, "LKG_PARENT")
    parent = path.parent.stat()
    need(stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.geteuid()
         and not parent.st_mode & 0o022, "LKG_PARENT_CUSTODY")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        view = memoryview(data)
        while view:
            count = os.write(fd, view); need(count > 0, "LKG_SHORT_WRITE"); view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)

    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def _copy_git_file(path, data, *, active=False):
    """Continue an unreferenced exact Git prefix; never alter active code."""
    if not os.path.lexists(path):
        _write_new(path, data)
        return
    need(path.resolve(strict=True) == path, "LKG_PARTIAL_ALIAS")
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
             and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= len(data), "LKG_PARTIAL_CUSTODY")
        existing = bytearray()
        while len(existing) < info.st_size:
            chunk = os.read(fd, min(65536, info.st_size - len(existing)))
            need(chunk, "LKG_PARTIAL_SHORT_READ"); existing.extend(chunk)
        need(data.startswith(existing), "LKG_PARTIAL_NOT_ACCEPTED_GIT_PREFIX")
        if bytes(existing) == data:
            return
        need(not active, "LKG_ACTIVE_CAPSULE_IMMUTABLE")
        now = path.lstat()
        need((now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns, now.st_ctime_ns)
             == (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns), "LKG_PARTIAL_CHANGED")
        view = memoryview(data)[len(existing):]
        while view:
            count = os.write(fd, view); need(count > 0, "LKG_SHORT_WRITE"); view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    need(raw(path) == data, "LKG_PARTIAL_RECHECK")


def _same_or_new(path, data):
    if os.path.lexists(path):
        need(raw(path) == data, "LKG_EXISTING_METADATA_CHANGED")
    else:
        _write_new(path, data)


def _active_engine_heads(root, *, include_completed=False):
    """Only incomplete/uncheckpointed jobs pin old runtime code."""
    from scripts.two_site_qoder_maintenance import JOBS_REL, read, decode as job_json, git_bytes
    from scripts import two_site_codex_autopilot as ap
    parent = Path(root) / JOBS_REL
    if not os.path.lexists(parent):
        return set()
    need(parent.resolve(strict=True) == parent, "LKG_JOB_PARENT")
    entries = list(parent.iterdir()); need(len(entries) <= 128, "LKG_ACTIVE_JOB_BOUND")
    cp = ap._load_accepted_checkpoint(Path(root) / ".local/two-site-refactor/autopilot")
    need(cp is not None, "LKG_CHECKPOINT_REQUIRED")
    heads = set()
    for directory in entries:
        if not re.fullmatch("maint-[0-9a-f]{32}", directory.name):
            continue
        job = job_json(read(root, directory.relative_to(root) / "job.json", 1000000, private=True))
        engine = job.get("trusted_engine")
        if engine is None:
            continue
        need(type(engine) is dict and type(engine.get("head")) is str
             and re.fullmatch("[0-9a-f]{40}", engine["head"]), "LKG_ACTIVE_JOB_ENGINE")
        if include_completed:
            heads.add(engine["head"]); continue
        execution = directory / "execution.json"
        if not os.path.lexists(execution):
            heads.add(engine["head"]); continue
        receipt = job_json(read(root, execution.relative_to(root), 1000000, private=True))
        after = receipt.get("head_after")
        need(type(after) is str and re.fullmatch("[0-9a-f]{40}", after), "LKG_EXECUTION_HEAD")
        try:
            git_bytes(root, ["merge-base", "--is-ancestor", after, cp["repository"]["head"]])
        except ValueError:
            heads.add(engine["head"])
    return heads


def retire_unreferenced_capsules(root, store=STORE):
    """Bound runtime copies; retain hashes/Git provenance, not private data."""
    store = Path(store); capsules = store / "capsules"
    keep = set()
    for name in ("current.json", "previous.json"):
        path = store / name
        if os.path.lexists(path):
            pointer = decode(raw(path, 65536))
            need(type(pointer.get("head")) is str and re.fullmatch("[0-9a-f]{40}", pointer["head"]), "LKG_RETAIN_POINTER")
            verify_capsule(capsules / pointer["head"], pointer["manifest_sha256"])
            keep.add(pointer["head"])
    if os.path.lexists(store / "rollback.json"):
        rollback = decode(raw(store / "rollback.json", 65536))
        need(set(rollback) == {"schema", "heads"} and rollback["schema"] == "QODER_LKG_ROLLBACK_PINS_V1"
             and type(rollback["heads"]) is list and len(rollback["heads"]) <= 4
             and all(type(head) is str and re.fullmatch("[0-9a-f]{40}", head) for head in rollback["heads"]),
             "LKG_ROLLBACK_PINS")
        keep.update(rollback["heads"])
    keep.update(_active_engine_heads(root))
    directories = list(capsules.iterdir()); need(len(directories) <= 64, "LKG_CAPSULE_BOUND")
    retired = []
    for directory in directories:
        need(directory.resolve(strict=True) == directory and directory.is_dir()
             and re.fullmatch("[0-9a-f]{40}", directory.name), "LKG_RETIRE_DIRECTORY")
        if directory.name in keep:
            continue
        manifest_path = directory / "manifest.json"
        if not os.path.lexists(manifest_path):
            # An obsolete interrupted install is not authority. Every retained
            # byte must still be an exact prefix of that public Git source.
            from scripts.two_site_qoder_maintenance import git_bytes
            rows = {}
            for path in directory.rglob("*"):
                need(path.resolve(strict=True) == path, "LKG_PARTIAL_RETIRE_ALIAS")
                if path.is_dir():
                    continue
                name = str(path.relative_to(directory)); need(public_path(name), "LKG_PARTIAL_RETIRE_PATH")
                data = raw(path, allow_empty=True)
                original = git_bytes(root, ["show", directory.name + ":" + name], limit=2500000)
                need(original.startswith(data), "LKG_PARTIAL_RETIRE_NOT_GIT_PREFIX")
                rows[name] = {"bytes": len(data), "sha256": sha(data)}
            _same_or_new(store / "retired-partial" / (directory.name + ".json"), canonical({
                "schema": "QODER_UNPROMOTED_PARTIAL_CAPSULE_RETIRED_V1", "head": directory.name, "files": rows}))
            from scripts.two_site_qoder_maintenance import no_stop
            no_stop(root); shutil.rmtree(directory); retired.append(directory.name)
            continue
        encoded = raw(manifest_path, 250000); manifest = verify_capsule(directory, sha(encoded))
        need(manifest["head"] == directory.name, "LKG_RETIRE_HEAD")
        # Metadata is inert. Historical proof can rehash original public Git
        # bytes without executing/retaining every old controller copy forever.
        _same_or_new(store / "retired" / (directory.name + ".json"), encoded)
        from scripts.two_site_qoder_maintenance import no_stop
        no_stop(root)
        shutil.rmtree(directory)
        retired.append(directory.name)
    # Historical job manifests remain replayable; unrelated cache metadata is
    # bounded too. This never deletes job/failure/reservation/proof artifacts.
    protected_metadata = keep | _active_engine_heads(root, include_completed=True)
    for name in ("retired", "retired-partial"):
        parent = store / name
        if not parent.exists():
            continue
        records = list(parent.iterdir()); need(len(records) <= 512, "LKG_RETIRE_METADATA_BOUND")
        removable = sorted((p for p in records if p.stem not in protected_metadata), key=lambda p: p.stat().st_mtime_ns)
        for path in removable[:max(0, len(records) - 256)]:
            need(path.resolve(strict=True) == path and re.fullmatch("[0-9a-f]{40}\\.json", path.name),
                 "LKG_RETIRE_METADATA_PATH")
            raw(path, 250000); path.unlink()
    return retired


def install(root, store=STORE):
    """Caller holds MON/AP; copy only accepted public Git bytes, then promote."""
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_qoder_maintenance import git_bytes, read, no_stop
    root, store = Path(root).resolve(strict=True), Path(store)
    need(not store.is_relative_to(root) and store.is_absolute(), "LKG_STORE_INSIDE_MODEL_MOUNT")
    no_stop(root)
    before = ap.validate_repository(root, require_clean=True)
    cp = ap._load_accepted_checkpoint(root / ".local/two-site-refactor/autopilot")
    need(cp is not None and ap._accepted_checkpoint_matches_repository(cp, before), "LKG_GENUINE_CURRENT_CPP_REQUIRED")
    cp_raw = read(root, ".local/two-site-refactor/autopilot/accepted_checkpoint.json", 480000, private=True)
    names = git_bytes(root, ["ls-files", "-z", "scripts", "docs/refactor/two-server/templates", *GOLDENS], limit=65536)
    selected = sorted(name.decode() for name in names.split(b"\0") if name and public_path(name.decode()))
    capsule = store / "capsules" / before["head"]
    old_pointer_raw = raw(store / "current.json", 65536) if os.path.lexists(store / "current.json") else None
    old_pointer = decode(old_pointer_raw) if old_pointer_raw else None
    if old_pointer is not None:
        current_capsule(store)  # Never derive an active overwrite decision from corrupt authority.
    active = old_pointer is not None and old_pointer.get("head") == before["head"]
    files = {}; total = 0
    for name in selected:
        data = git_bytes(root, ["show", before["head"] + ":" + name], limit=2500000)
        need(data == read(root, name, 2500000), "LKG_WORKTREE_DIFFERS_FROM_ACCEPTED_GIT")
        total += len(data); need(total <= 25000000, "LKG_TOTAL_BUDGET")
        _copy_git_file(capsule / name, data, active=active); files[name] = {"bytes": len(data), "sha256": sha(data)}
    manifest = {"schema": "QODER_LKG_CONTROLLER_V1", "head": before["head"], "checkpoint_sha256": sha(cp_raw),
        "files": files, "critical_paths": critical_paths(files), "golden_tests": list(GOLDENS)}
    if os.path.lexists(capsule / "manifest.json"):
        encoded = raw(capsule / "manifest.json", 250000)
        previous_manifest = verify_capsule(capsule, sha(encoded))
        need(previous_manifest["head"] == before["head"] and previous_manifest["files"] == files,
             "LKG_EXISTING_CAPSULE_DIFFERS_FROM_GIT")
        # An official next_action-only CPP update does not rewrite old capsule
        # provenance; same accepted code may retain its original genuine CPP pin.
    else:
        encoded = canonical(manifest); _write_new(capsule / "manifest.json", encoded)
    verify_capsule(capsule, sha(encoded))
    need(ap.validate_repository(root, require_clean=True) == before
         and read(root, ".local/two-site-refactor/autopilot/accepted_checkpoint.json", 480000, private=True) == cp_raw,
         "LKG_PROMOTION_DRIFT")
    no_stop(root)
    pointer = canonical({"schema": "QODER_LKG_POINTER_V1", "head": before["head"], "manifest_sha256": sha(encoded)})
    temporary = store / (".current-" + before["head"] + ".json")
    _same_or_new(temporary, pointer)
    current = store / "current.json"
    if os.path.lexists(current):
        raw(current, 65536)  # Never replace an unsafe previous pointer.
    launcher = store / "launcher.py"
    if os.path.lexists(launcher):
        need(raw(launcher, 65536) == HOST_LAUNCHER.encode(), "LKG_HOST_LAUNCHER_CHANGED")
    else:
        _write_new(launcher, HOST_LAUNCHER.encode())
    if old_pointer_raw and old_pointer["head"] != before["head"]:
        previous_temp = store / (".previous-" + before["head"] + ".json")
        _same_or_new(previous_temp, old_pointer_raw)
        os.replace(previous_temp, store / "previous.json")
    os.replace(temporary, current)
    fd = os.open(store, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    retired = retire_unreferenced_capsules(root, store)
    return {"head": before["head"], "manifest_sha256": sha(encoded), "public_files": len(files), "bytes": total,
            "retired_capsules": retired}


def current_capsule(store=STORE):
    store = Path(store)
    value = decode(raw(store / "current.json", 65536))
    need(type(value) is dict and set(value) == {"schema", "head", "manifest_sha256"}
         and value["schema"] == "QODER_LKG_POINTER_V1" and type(value["head"]) is str
         and type(value["manifest_sha256"]) is str and re.fullmatch("[0-9a-f]{40}", value["head"])
         and re.fullmatch("[0-9a-f]{64}", value["manifest_sha256"]), "LKG_POINTER")
    capsule = store / "capsules" / value["head"]
    return capsule, verify_capsule(capsule, value["manifest_sha256"])


def executing_trusted_capsule():
    """No env/argument can turn a live in-worktree module into a trusted entry."""
    here = Path(__file__).resolve(strict=True)
    if not here.is_relative_to(STORE / "capsules"):
        return None
    capsule, manifest = current_capsule()
    need(here == capsule / "scripts/two_site_maintenance_lkg.py", "LKG_EXECUTION_SOURCE")
    return capsule, manifest


def permits_critical_path(path):
    trusted = executing_trusted_capsule()
    return trusted is not None and path in trusted[1]["critical_paths"]


def current_engine_binding():
    trusted = executing_trusted_capsule()
    if trusted is None:
        return None
    capsule, manifest = trusted
    return {"head": manifest["head"], "manifest_sha256": sha(raw(capsule / "manifest.json", 250000)),
            "policy_sha256": manifest["files"][POLICY]["sha256"]}


def verify_engine_binding(binding, *, root=None):
    need(type(binding) is dict and set(binding) == {"head", "manifest_sha256", "policy_sha256"}
         and all(type(value) is str for value in binding.values())
         and re.fullmatch("[0-9a-f]{40}", binding["head"])
         and re.fullmatch("[0-9a-f]{64}", binding["manifest_sha256"])
         and re.fullmatch("[0-9a-f]{64}", binding["policy_sha256"]), "LKG_ENGINE_BINDING")
    capsule = STORE / "capsules" / binding["head"]
    if os.path.lexists(capsule):
        manifest = verify_capsule(capsule, binding["manifest_sha256"])
    else:
        need(root is not None, "LKG_RETIRED_ENGINE_NOT_EXECUTABLE")
        encoded = raw(STORE / "retired" / (binding["head"] + ".json"), 250000)
        need(sha(encoded) == binding["manifest_sha256"], "LKG_RETIRED_MANIFEST_SHA")
        manifest = decode(encoded)
        need(manifest.get("head") == binding["head"] and type(manifest.get("files")) is dict
             and 0 < len(manifest["files"]) <= 512, "LKG_RETIRED_MANIFEST")
        from scripts.two_site_qoder_maintenance import git_bytes
        for name, row in manifest["files"].items():
            need(public_path(name) and type(row) is dict and set(row) == {"bytes", "sha256"}, "LKG_RETIRED_FILE")
            data = git_bytes(root, ["show", binding["head"] + ":" + name], limit=2500000)
            need(len(data) == row["bytes"] and sha(data) == row["sha256"], "LKG_RETIRED_GIT_BYTES")
    need(manifest["files"][POLICY]["sha256"] == binding["policy_sha256"], "LKG_ENGINE_POLICY")
    return capsule, manifest


def refresh_pending_lkg(root):
    """Caller owns AP and MON; no nested lock acquisition or business replay."""
    from scripts.two_site_qoder_maintenance import maintenance_policy_enabled
    if not maintenance_policy_enabled(root):
        return {"state": "LKG_DISABLED"}
    from scripts import two_site_codex_autopilot as ap
    before = ap.validate_repository(Path(root), require_clean=True)
    cp = ap._load_accepted_checkpoint(Path(root) / ".local/two-site-refactor/autopilot")
    need(cp is not None and ap._accepted_checkpoint_matches_repository(cp, before), "LKG_GENUINE_CURRENT_CPP_REQUIRED")
    try:
        _capsule, current = current_capsule()
    except (OSError, LKGError):
        current = None
    if current is not None and current["head"] == before["head"]:
        return {"state": "LKG_CURRENT", "head": before["head"], "reused": True}
    result = install(root)
    return {"state": "LKG_CURRENT", **result}


@contextmanager
def inherited_monitor(root, descriptor):
    """Borrow the monitor's existing OFD; never unlock its parent's lock."""
    import fcntl
    need(type(descriptor) is int and descriptor >= 3, "LKG_MONITOR_FD")
    path = Path(root) / ".local/two-site-refactor/monitor/monitor.lock"
    info = os.fstat(descriptor); current = path.lstat()
    need(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1
         and stat.S_IMODE(info.st_mode) == 0o600 and (info.st_dev, info.st_ino)
         == (current.st_dev, current.st_ino), "LKG_MONITOR_CUSTODY")
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    yield


def trusted_repository(root):
    """Emergency source facts from a genuine CURRENT CPP, without live imports.

    A capsule cannot adopt an unaccepted/different head or bypass expired Scope.
    Normal Source validation remains first choice; this is only the LKG entry.
    """
    need(executing_trusted_capsule() is not None, "LKG_EXECUTION_REQUIRED")
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_assignment_scope import load_active_assignment_scope
    from scripts.two_site_qoder_maintenance import git_bytes
    root = Path(root).resolve(strict=True)
    cp = ap._load_accepted_checkpoint(root / ".local/two-site-refactor/autopilot")
    need(cp is not None, "LKG_GENUINE_CURRENT_CPP_REQUIRED")
    expected = cp["repository"]
    need(git_bytes(root, ["branch", "--show-current"]).decode().strip() == ap.BRANCH
         and git_bytes(root, ["rev-parse", "HEAD"]).decode().strip() == expected["head"]
         and git_bytes(root, ["status", "--porcelain=v1", "--untracked-files=all"]) == b""
         and ap.ref_snapshot(root) == expected["protected_refs"]
         and ap.assignment_scope_context(load_active_assignment_scope(root)) == expected["assignment_scope"],
         "LKG_SOURCE_SCOPE_OR_REFS")
    for name, blob in expected["control_blobs"].items():
        need(git_bytes(root, ["rev-parse", expected["head"] + ":" + name]).decode().strip() == blob
             and git_bytes(root, ["show", expected["head"] + ":" + name], limit=2500000) == raw(root / name),
             "LKG_CURRENT_CONTROL_BYTES")
    return json.loads(json.dumps(expected))


def launch(root, store=STORE, *, inspect_only=False):
    """Called by the fixed host launcher, not a live-worktree Python import."""
    need(type(inspect_only) is bool and Path(root).resolve(strict=True) == Path(root)
         and not Path(store).is_relative_to(Path(root)), "LKG_ROOT")
    capsule, manifest = current_capsule(store)
    # Run a fresh process: no previously imported broken live scripts survive.
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"PYTHONPATH", "PYTHONHOME", "TWO_SITE_AUTOPILOT_RUN_ID", "TWO_SITE_MAINTENANCE_RUN_ID"}}
    environment.update(PYTHONPATH=str(capsule), PYTHONDONTWRITEBYTECODE="1", APP_ENV_FILE="config/unit-test.env.example")
    return subprocess.run([sys.executable, "-I", "-B", str(capsule / "scripts/two_site_qoder_host.py"),
        "--root", str(root), "--inspect" if inspect_only else "--recover"], cwd=capsule, env=environment, check=False).returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--monitor-fd", type=int)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args()
    if args.install or args.refresh:
        from scripts.two_site_qoder_maintenance import monitor_hold
        from scripts import two_site_codex_autopilot as ap
        need(not args.install or not args.refresh, "LKG_CLI_MODE")
        hold = inherited_monitor(args.root, args.monitor_fd) if args.monitor_fd is not None else monitor_hold(args.root)
        with hold, ap.exclusive_supervisor_lock(args.root / ".local/two-site-refactor/autopilot/autopilot.lock"):
            print(json.dumps(refresh_pending_lkg(args.root) if args.refresh else install(args.root)))
        return 0
    need(args.monitor_fd is None, "LKG_MONITOR_FD_MODE")
    return launch(args.root, inspect_only=args.inspect)


if __name__ == "__main__":
    # The installed public launcher needs only this module's stdlib imports.
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
