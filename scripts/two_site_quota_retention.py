"""Strict quota history classification and explicitly certified C4c maintenance.

An ACK is not pruning authority. Runtime readers are observational and deny
retired identities; only the separately pinned Root release can certify the two
enumerated receipt edges. This module never grants execution or replay.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

_B = Path(".local/two-site-refactor")
_A = _B / "autopilot"
_P = _B / "provider-broker"
_INTENT = "quota-history-retirement.json"
_COMPLETE = "quota-history-retirement-complete.json"
_HEX = re.compile(r"[0-9a-f]{64}")
_ROLE_FILES = ("task.json", "result.json", "status.json", "prompt.txt", "stream.jsonl",
               "stderr.txt", "response-schema.json", "transport-completion.json")
_LIMITS = {"quota-wait.json": 16384, "quota-resume-claim.json": 16384,
           "quota-resume-lineage-snapshot.json": 131072,
           "quota-resume-submission.json": 16384, "broker-reservation.json": 16384,
           "preparation-startup.json": 4096, "accepted-lineage.json": 16384,
           "final-ack.json": 16384, "result.json": 187000}
_FP_KEYS = ("dev", "ino", "mode", "uid", "nlink", "size", "mtime_ns", "ctime_ns")


class QuotaRetentionError(ValueError):
    """History cannot be authenticated, is referenced, or is not releasable."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fp(st: os.stat_result) -> tuple:
    return (st.st_dev, st.st_ino, st.st_mode, st.st_uid, st.st_nlink,
            st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def _same(a: object, b: object) -> bool:
    return type(a) is type(b) and _canonical(a) == _canonical(b)


def _utc(value: object) -> datetime:
    from scripts.two_site_quota_wait_schedule import _parse_utc_iso
    return _parse_utc_iso(value)


def _root(root: Path) -> Path:
    from scripts.two_site_provider_broker import canonical_root
    if not isinstance(root, Path) or canonical_root(root) != root:
        raise QuotaRetentionError("history root must be its exact canonical Path")
    return root


def _relative(value: object) -> Path:
    if type(value) is not str:
        raise QuotaRetentionError("relative path must be a string")
    path = Path(value)
    if path.is_absolute() or str(path) != value or not path.parts or ".." in path.parts:
        raise QuotaRetentionError("noncanonical relative path")
    return path


class _Pins:
    """Pin before the first byte; retain descriptors through final custody."""
    def __init__(self):
        self.files = {}
        self.directories = {}
        self.stack = ExitStack()

    def directory(self, path: Path, *, private: bool = False) -> None:
        if path in self.directories:
            return
        before = path.lstat()
        if (not stat.S_ISDIR(before.st_mode) or before.st_uid != os.geteuid()
                or path.resolve(strict=True) != path
                or private and stat.S_IMODE(before.st_mode) != 0o700):
            raise QuotaRetentionError("unsafe history directory: " + str(path))
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        self.stack.callback(os.close, fd)
        if not self._same_directory(path, _fp(os.fstat(fd)), _fp(before)):
            raise QuotaRetentionError("history directory changed before open")
        self.directories[path] = (fd, _fp(before))

    def read(self, base: Path, relative: str, limit: int, *, canonical: bool = True) -> tuple:
        path = base / _relative(relative)
        if path in self.files:
            raw = self.files[path][2]
            if len(raw) > limit:
                raise QuotaRetentionError("cached history leaf exceeds requested read bound")
            return raw, self.decode(raw) if canonical else None
        if path.resolve(strict=True) != path:
            raise QuotaRetentionError("history path contains symlink")
        for parent in reversed(path.parents):
            if parent == base or base in parent.parents:
                self.directory(parent, private=parent.is_relative_to(base / _B)
                               and parent != base / _B / "assignments"
                               and "assignments" not in parent.parts)
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_mode & 0o022
                or not 0 <= before.st_size <= limit):
            raise QuotaRetentionError("unsafe history leaf: " + relative)
        if (path.is_relative_to(base / _B) and "assignments" not in path.parts
                and path.name != "worktrees.yaml"):
            mode = stat.S_IMODE(before.st_mode)
            if mode != 0o600 and not (path == base / _A / "status.json" and mode == 0o644):
                raise QuotaRetentionError("private history leaf is not 0600")
        parent_fd = self.directories[path.parent][0]
        if _fp(os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)) != _fp(before):
            raise QuotaRetentionError("history parent/path mismatch")
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent_fd)
        self.stack.callback(os.close, fd)
        if _fp(os.fstat(fd)) != _fp(before):
            raise QuotaRetentionError("history descriptor identity mismatch")
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        raw = b"".join(chunks)
        if len(raw) != before.st_size or len(raw) > limit:
            raise QuotaRetentionError("history read exceeded bound or changed size")
        self.files[path] = (fd, _fp(before), raw)
        self.verify()
        return raw, self.decode(raw) if canonical else None

    @staticmethod
    def decode(raw: bytes) -> dict:
        from scripts.two_site_proposal_runner import unique_json
        value = unique_json(raw.decode("utf-8"))
        if type(value) is not dict or _canonical(value) != raw:
            raise QuotaRetentionError("history record must be strict canonical JSON")
        return value

    @staticmethod
    def _same_directory(path: Path, current: tuple, expected: tuple) -> bool:
        # The existing ordinary Broker may atomically refresh status while IDLE.
        # Its queues/receipt directories remain separately pinned; the boundary
        # reader independently rechecks LIVE IDLE identity before every effect.
        if path.name == "provider-broker" and path.parent.name == "two-site-refactor":
            return current[:5] == expected[:5]
        return current == expected

    def verify(self) -> None:
        for path, (fd, fingerprint) in self.directories.items():
            if (not self._same_directory(path, _fp(os.fstat(fd)), fingerprint)
                    or not self._same_directory(path, _fp(path.lstat()), fingerprint)):
                raise QuotaRetentionError("history directory custody drift")
        for path, (fd, fingerprint, raw) in self.files.items():
            if _fp(os.fstat(fd)) != fingerprint or _fp(path.lstat()) != fingerprint:
                raise QuotaRetentionError("history leaf custody drift")

    def __enter__(self):
        return self

    def __exit__(self, typ, error, tb):
        custody_error = None
        try:
            self.verify()
        except BaseException as exc:
            custody_error = exc
        try:
            self.stack.close()
        except BaseException as exc:
            custody_error = custody_error or exc
        if custody_error is not None:
            if error is not None:
                raise custody_error from error
            raise custody_error
        return False


def _run_relative(cid: str, name: str) -> str:
    from scripts.two_site_provider_broker import CYCLE_RUN_RE
    if type(cid) is not str or not CYCLE_RUN_RE.fullmatch(cid):
        raise QuotaRetentionError("invalid historical controller ID")
    return (_A / "runs" / cid / name).as_posix()


def _historical_scope(root: Path, pins: _Pins, repository: dict):
    from scripts import two_site_assignment_scope as assignments
    from scripts import two_site_codex_autopilot as ap
    serialized = repository["assignment_scope"]
    if type(serialized) is not dict:
        raise QuotaRetentionError("historical serialized Scope is absent")
    shared = Path(assignments._resolve_shared_checkout(str(root)))
    relative = (_B / "assignments" / (serialized["assignment_id"] + ".md")).as_posix()
    raw, _ = pins.read(shared, relative, 262144, canonical=False)
    scope = assignments.parse_assignment_scope_bytes(
        raw, expected_assignment_id=serialized["assignment_id"], integration_branch=ap.BRANCH)
    if not _same(ap.assignment_scope_context(scope), serialized):
        raise QuotaRetentionError("original Markdown does not reproduce recorded Scope")
    return scope, shared, relative, raw


def _historical_repository(root: Path, repository: dict) -> dict:
    from scripts import two_site_codex_autopilot as ap
    recorded = ap._validate_persisted_repository(repository)
    if recorded["status"] != "" or set(recorded["control_blobs"]) != {
            path.as_posix() for path in ap.CONTROL_PATHS}:
        raise QuotaRetentionError("historical repository lacks the exact clean control frame")
    if set(recorded["protected_refs"]) != set(ap.PROTECTED_REFS):
        raise QuotaRetentionError("historical protected-ref frame is incomplete")
    head = recorded["head"]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise QuotaRetentionError("invalid historical source commit")
    _git_query(root, "cat-file", "-e", head + "^{commit}")
    for path, oid in recorded["control_blobs"].items():
        if _git_query(root, "rev-parse", head + ":" + path) != oid:
            raise QuotaRetentionError("historical control blob differs from immutable Git")
    for oid in recorded["protected_refs"].values():
        if oid is not None:
            _git_query(root, "cat-file", "-e", oid + "^{commit}")
    return recorded


def _git_query(root: Path, *arguments: str) -> str:
    from scripts.two_site_maintenance_transition_git import _run_git_query
    return _run_git_query(root, arguments, timeout=10, output_limit=65536).decode("utf-8").strip()


def _pin_actor(root: Path, pins: _Pins, run_id: str) -> dict:
    from scripts.two_site_codex_autopilot import proposal_run_dir
    directory = proposal_run_dir(root, run_id)
    for name in _ROLE_FILES:
        pins.read(root, (directory / name).relative_to(root).as_posix(), _actor_limit(name),
                  canonical=False)
    delta = directory / "delta-result.json"
    if os.path.lexists(delta):
        pins.read(root, delta.relative_to(root).as_posix(), 187000)
    from scripts.two_site_proposal_runner import unique_json
    raw, _ = pins.read(root, (directory / "task.json").relative_to(root).as_posix(), 3 * 187000, canonical=False)
    return unique_json(raw.decode("utf-8"))


def _historical_transition(root: Path, pins: _Pins, before: dict, scope, raw_result: dict,
                           task_raw: bytes, continuation: str) -> tuple:
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_assignment_scope import validate_task_scope
    ap.validate_result(raw_result)
    if raw_result["state"] not in {"CONTINUE", "COMPLETE"}:
        raise QuotaRetentionError("history is not an accepted terminal state")
    from scripts.two_site_proposal_runner import unique_json
    original_task = unique_json(task_raw.decode("utf-8"))
    validate_task_scope(scope, original_task)
    after_head = raw_result["commit"] or before["head"]
    changed = after_head != before["head"]
    if not changed:
        if (raw_result["state"] != "COMPLETE" or raw_result["commit"] is not None
                or any(raw_result[key] is not None for key in (
                    "gemini_worker_run_id", "gemini_reviewer_run_id", "proposal_sha256"))):
            raise QuotaRetentionError("no-change acceptance requires COMPLETE and null actors/proposal")
    else:
        worker, reviewer = raw_result["gemini_worker_run_id"], raw_result["gemini_reviewer_run_id"]
        if type(worker) is not str or type(reviewer) is not str or worker == reviewer:
            raise QuotaRetentionError("history lacks distinct final actors")
        worker_task = _pin_actor(root, pins, worker)
        _pin_actor(root, pins, reviewer)
        if not _same(worker_task.get("task"), original_task):
            raise QuotaRetentionError("actor Task differs from immutable original bytes")
        if worker_task.get("source_head") != before["head"]:
            raise QuotaRetentionError("actor source head differs from historical before")
        _validate_actor_sources(root, before, original_task, worker_task.get("sources"))
        proposal = ap.validate_scoped_gemini_chain(root, before["head"], raw_result, scope)
        _git_query(root, "diff", "--name-only", "--no-renames", before["head"], after_head)
        for change in proposal["changes"]:
            blob = _bounded_git_blob(root, after_head, change["path"])
            if blob != change["content"].encode("utf-8"):
                raise QuotaRetentionError("historical proposal bytes differ from bounded Git blob")
        parents = _git_query(root, "rev-list", "--parents", "-n", "1", after_head).split()
        if parents != [after_head, before["head"]]:
            raise QuotaRetentionError("accepted historical commit is not one exact child")
        ap.validate_scoped_committed_proposal(root, before["head"], after_head, proposal, scope)
        after_controls = {path: _git_query(root, "rev-parse", after_head + ":" + path)
                          for path in before["control_blobs"]}
        ap.validate_control_changes_are_proposed(before["control_blobs"], after_controls, proposal)
    # Current clean repository is a separate frame, never substituted for old facts.
    current = ap.validate_repository(root, require_clean=True)
    _git_query(root, "merge-base", "--is-ancestor", after_head, current["head"])
    transition = dict(raw_result, head_before=before["head"], head_after=after_head,
                      head_changed=changed)
    context = {"controller_run_id": continuation, "protected_refs": before["protected_refs"],
               "assignment_id": scope.assignment_id, "assignment_sha256": scope.sha256,
               "current_head": after_head}
    return transition, context


def _read_final_ack_history_locked_unframed(root: Path, original: str, *,
                                  pipeline_dir: Path = _B / 'pipeline') -> dict | None:
    """Validate an immutable accepted history, or return None for a wait-only run.

    None is not admission of an active wait. Partial resume evidence is an error.
    Retired histories use their explicit supersession certificate, never a
    successful pretend-read of a deleted terminal receipt.
    """
    _root(root)
    if pipeline_dir not in {_B, _B / "pipeline"}:
        raise QuotaRetentionError("unsupported explicit historical semantic profile")
    directory = root / _A / "runs" / original
    _run_relative(original, "quota-wait.json")
    names = set(_bounded_names(directory, 128, "historical run"))
    resume_names = {"quota-resume-claim.json", "quota-resume-lineage-snapshot.json",
                    "quota-resume-submission.json", _INTENT, _COMPLETE}
    if not names.intersection(resume_names):
        if "accepted-lineage.json" in names or "final-ack.json" in names:
            raise QuotaRetentionError("orphan continuation evidence is not wait-only")
        return None
    if _INTENT in names or _COMPLETE in names:
        return _read_retired_history(root, original)
    with _Pins() as pins:
        snapshot_rel = _run_relative(original, "quota-resume-lineage-snapshot.json")
        _, candidate = pins.read(root, snapshot_rel, 131072)
        request = candidate.get("expected_request")
        if type(request) is not dict:
            raise QuotaRetentionError("prequeue snapshot has no request envelope")
        continuation = request.get("controller_run_id")
        for cid, names_to_pin in (
                (original, ("preparation-startup.json", "broker-reservation.json", "quota-wait.json",
                            "quota-resume-claim.json", "quota-resume-submission.json")),
                (continuation, ("result.json", "accepted-lineage.json", "final-ack.json"))):
            for name in names_to_pin:
                pins.read(root, _run_relative(cid, name), _LIMITS[name], canonical=name != "result.json")
        for kind, request_id in (("terminal-quota", request.get("original_request_id")),
                                 ("terminal-continuation", request.get("request_id"))):
            if type(request_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", request_id):
                raise QuotaRetentionError("snapshot request hint is invalid")
            pins.read(root, (_P / kind / (request_id + ".json")).as_posix(), _receipt_limit(kind))
        expected = candidate.get("expected_wait")
        if type(expected) is not dict:
            raise QuotaRetentionError("prequeue snapshot has no expected wait")
        before = _historical_repository(root, expected["repository"])
        scope, shared, assignment_relative, assignment_raw = _historical_scope(root, pins, before)
        current_repository = _pin_current_repository(root, pins)
        from scripts import two_site_provider_broker as broker
        from scripts.two_site_quota_wait_expected import derive_initial_wait_expected
        from scripts.two_site_quota_wait_storage import read_initial_wait_record
        reservation = broker.read_cycle_reservation(root, original)
        orig_raw = broker.read_terminal_quota_bytes(root, reservation["request_id"])
        receipt = broker.validate_terminal_quota_payload(root, reservation["request_id"], orig_raw)
        independently_derived = derive_initial_wait_expected(
            reservation, receipt, orig_raw, before,
            expected["rescope_epoch"], expected["rescope_attempt"])
        if not _same(independently_derived, expected):
            raise QuotaRetentionError("historical expected wait differs from independent sources")
        from scripts.two_site_codex_autopilot import rescope_epoch_basename
        if expected["rescope_epoch"] != rescope_epoch_basename(before):
            raise QuotaRetentionError("historical epoch does not bind historical repository")
        wait = read_initial_wait_record(root, original, independently_derived)
        task_relative = reservation["reservation"]["task_path"]
        task_raw, _ = pins.read(root, task_relative, 160000, canonical=False)
        if _sha(task_raw) != reservation["reservation"]["task_sha256"]:
            raise QuotaRetentionError("original Task bytes differ from reservation")
        from scripts.two_site_proposal_runner import unique_json
        from scripts.two_site_semantic_attempts import read_existing_semantic_attempt
        task = unique_json(task_raw.decode("utf-8"))
        semantic = read_existing_semantic_attempt(root, pipeline_dir, task,
                                                  reservation["reservation"]["run_prefix"])
        if semantic["fingerprint"] != wait["semantic_task_fingerprint"]:
            raise QuotaRetentionError("historical semantic reservation binding mismatch")
        semantic_relative = (pipeline_dir / "semantic-attempts" / (semantic["fingerprint"] + ".json")).as_posix()
        pins.read(root, semantic_relative, 4096, canonical=False)
        other_dir = _B if pipeline_dir == _B / "pipeline" else _B / "pipeline"
        other_relative = (other_dir / "semantic-attempts" / (semantic["fingerprint"] + ".json")).as_posix()
        if os.path.lexists(root / other_relative):
            pins.read(root, other_relative, 4096, canonical=False)
            other = read_existing_semantic_attempt(root, other_dir, task,
                                                   reservation["reservation"]["run_prefix"])
            if not _same(other, semantic):
                raise QuotaRetentionError("conflicting semantic reservation stores")
        quota_status = (_B / "proposals" / wait["quota_attempt"]["run_id"] / "status.json").as_posix()
        pins.read(root, quota_status, 16384, canonical=False)
        from scripts.two_site_quota_final_acceptance_lineage import collect_final_acceptance_upstream_lineage_from_snapshot_locked
        upstream = collect_final_acceptance_upstream_lineage_from_snapshot_locked(root, original)
        result_raw, _ = pins.read(root, _run_relative(continuation, "result.json"), 187000, canonical=False)
        from scripts.two_site_proposal_runner import unique_json
        raw_result = unique_json(result_raw.decode("utf-8"))
        transition, context = _historical_transition(root, pins, before, scope, raw_result,
                                                   task_raw, continuation)
        from scripts.two_site_quota_final_ack_storage import read_final_ack_record_candidate
        ack = read_final_ack_record_candidate(root, original, continuation, transition, context, upstream)
        if _utc(ack["acknowledged_at_utc"]) >= _utc(scope.expires_at):
            raise QuotaRetentionError("business Scope had expired before final acceptance")
        if not _same(_pin_current_repository(root, pins), current_repository):
            raise QuotaRetentionError("current repository drift during historical read")
        retained = [{"relative_path": str(path.relative_to(root)), "sha256": _sha(raw)}
                    for path, (_, _, raw) in pins.files.items() if path.is_relative_to(root)]
        return {"state": "ACKNOWLEDGED", "ack": copy.deepcopy(ack), "upstream": upstream,
                "transition": transition, "context": context, "wait": wait,
                "repository": before, "retained": retained,
                "semantic_directory": pipeline_dir.as_posix(),
                "historical_assignment": {"relative_path": assignment_relative,
                                          "sha256": _sha(assignment_raw)}}



def _scan_runs(root: Path) -> list[Path]:
    directory = root / _A / "runs"
    if not os.path.lexists(directory):
        return []
    from scripts.two_site_provider_broker import CYCLE_RUN_RE
    with _Pins() as pins:
        pins.directory(directory, private=True)
        names = _bounded_names(directory, 512, "quota history")
        result = []
        for name in sorted(names):
            if not CYCLE_RUN_RE.fullmatch(name):
                raise QuotaRetentionError("unexpected run entry")
            path = directory / name
            pins.directory(path, private=any(os.path.lexists(path / leaf) for leaf in (_INTENT, _COMPLETE)))
            _bounded_names(path, 128, "run")
            result.append(path)
        return result


def has_retirement_records(root: Path) -> bool:
    root = _root(root)
    return any(os.path.lexists(path / name) for path in _scan_runs(root)
               for name in (_INTENT, _COMPLETE))


def assert_not_retired(root: Path, *, request: object = None,
                       fingerprint: str | None = None) -> None:
    """Deny exact retired IDs and semantic aliases BEFORE downstream effects."""
    root = _root(root)
    for directory in _scan_runs(root):
        if not any(os.path.lexists(directory / name) for name in (_INTENT, _COMPLETE)):
            continue
        history = _read_retired_history(root, directory.name)
        intent = history["retirement"]
        bound = {key: intent[key] for key in (
            "original_controller_run_id", "continuation_controller_run_id",
            "original_request_id", "resume_request_id", "semantic_task_fingerprint")}
        if fingerprint is not None and fingerprint == bound["semantic_task_fingerprint"]:
            raise QuotaRetentionError("retired semantic identity remains permanently denied")
        if type(request) is dict:
            if (request.get("request_id") in {bound["original_request_id"], bound["resume_request_id"]}
                    or request.get("original_request_id") == bound["original_request_id"]
                    or request.get("controller_run_id") in {
                        bound["original_controller_run_id"], bound["continuation_controller_run_id"]}
                    or request.get("original_controller_run_id") == bound["original_controller_run_id"]
                    or request.get("semantic_task_fingerprint") == bound["semantic_task_fingerprint"]):
                raise QuotaRetentionError("retired quota identity is execution-denied")
            task_path = request.get("task_path")
            if type(task_path) is str:
                with _Pins() as pins:
                    raw, _ = pins.read(root, task_path, 160000, canonical=False)
                    from scripts.two_site_proposal_runner import unique_json
                    from scripts.two_site_semantic_attempts import extract_semantic_identity
                    identity = extract_semantic_identity(unique_json(raw.decode("utf-8")))
                    if identity["fingerprint"] == bound["semantic_task_fingerprint"]:
                        raise QuotaRetentionError("renamed task aliases retired semantic identity")


_INTENT_KEYS = frozenset({
    "schema_version", "record_kind", "original_controller_run_id", "continuation_controller_run_id",
    "original_request_id", "resume_request_id", "semantic_task_fingerprint", "acknowledged_at_utc",
    "certified_at_utc", "release_sha256", "retained_anchor_sha256", "accepted_binding",
    "retained", "collectible", "superseded_edges", "retirement_authority"})
_ID_KEYS = ("original_controller_run_id", "continuation_controller_run_id",
            "original_request_id", "resume_request_id", "semantic_task_fingerprint")
_AUTHORITY_ROLES = {"RELEASE", "RETIREMENT_ASSIGNMENT", "RETIREMENT_PREACTION"}
_EDGES = [
    ["WAIT", "/terminal_receipt_sha256", "ORIG_RECEIPT.raw"],
    ["WAIT", "/response_sha256", "ORIG_RECEIPT.response"],
    ["SNAPSHOT", "/expected_wait/terminal_receipt_sha256", "ORIG_RECEIPT.raw"],
    ["SNAPSHOT", "/expected_wait/response_sha256", "ORIG_RECEIPT.response"],
    ["LINEAGE", "/upstream/terminal_receipt_sha256", "RESUME_RECEIPT.raw"],
    ["LINEAGE", "/upstream/response_sha256", "RESUME_RECEIPT.response"],
    ["ACK", "/terminal_receipt_sha256", "RESUME_RECEIPT.raw"],
]


def _base_anchor(retained: list) -> str:
    return _sha(_canonical([row for row in retained if row["role"] not in _AUTHORITY_ROLES]))


def _marker(raw: bytes) -> dict:
    prefix = "ROOT_QUOTA_RETIREMENT_PREACTION_V1:"
    text = raw.decode("utf-8")
    if text.count("ROOT_QUOTA_RETIREMENT_PREACTION_") != 1:
        raise QuotaRetentionError("missing, duplicate, or unsupported retirement authority marker")
    rows = [line[len(prefix):].strip() for line in text.splitlines() if line.startswith(prefix)]
    if len(rows) != 1:
        raise QuotaRetentionError("malformed Root retirement preaction marker")
    from scripts.two_site_proposal_runner import unique_json
    value = unique_json(rows[0])
    if (type(value) is not dict or set(value) != {"relative_path", "sha256"}
            or _canonical(value).decode("utf-8") != rows[0]
            or not _HEX.fullmatch(value["sha256"])
            or not re.fullmatch(r"\.local/two-site-refactor/reviews/[A-Za-z0-9][A-Za-z0-9._-]*\.json",
                                value["relative_path"])):
        raise QuotaRetentionError("invalid Root retirement preaction marker")
    return value


def _authority(root: Path, pins: _Pins, intent: dict, *, constructor: bool) -> list[dict]:
    from scripts import two_site_assignment_scope as assignments
    from scripts import two_site_codex_autopilot as ap
    authority = intent["retirement_authority"]
    if (type(authority) is not dict or set(authority) != {"assignment_id", "assignment_sha256"}
            or not _HEX.fullmatch(authority["assignment_sha256"])):
        raise QuotaRetentionError("invalid retirement authority binding")
    shared = Path(assignments._resolve_shared_checkout(str(root)))
    relative = (_B / "assignments" / (authority["assignment_id"] + ".md")).as_posix()
    raw, _ = pins.read(shared, relative, assignments.MAX_CONTROL_FILE_BYTES, canonical=False)
    scope = assignments.parse_assignment_scope_bytes(raw, expected_assignment_id=authority["assignment_id"],
                                                      integration_branch=ap.BRANCH)
    if scope.sha256 != authority["assignment_sha256"]:
        raise QuotaRetentionError("retirement Assignment typed SHA mismatch")
    certified = _utc(intent["certified_at_utc"])
    if certified >= _utc(scope.expires_at):
        raise QuotaRetentionError("Root maintenance Scope was not valid at certification")
    if constructor:
        # Existing resolver derives actual registry selection. No caller Scope.
        for path in (shared / _B / "worktrees.yaml",):
            pins.read(shared, str(path.relative_to(shared)), 262144, canonical=False)
        actual = assignments.load_active_assignment_scope(root)
        if actual != scope:
            raise QuotaRetentionError("Root maintenance Scope is not actually registry-selected")
    marker = _marker(raw)
    pre_raw, pre = pins.read(root, marker["relative_path"], 4096)
    pre_keys = {"schema_version", "record_kind", "assignment_id", "maintenance_head",
                *_ID_KEYS, "final_ack_sha256", "retained_anchor_sha256", "release",
                "authorized_at_utc"}
    if (_sha(pre_raw) != marker["sha256"] or set(pre) != pre_keys
            or pre["schema_version"] != "1.0" or pre["record_kind"] != "ROOT_QUOTA_RETIREMENT_PREACTION"
            or pre["assignment_id"] != scope.assignment_id):
        raise QuotaRetentionError("invalid Root retirement preaction")
    _validate_maintenance_base(raw, scope.assignment_id, pre["maintenance_head"])
    if any(pre[key] != intent[key] for key in _ID_KEYS):
        raise QuotaRetentionError("Root preaction history IDs disagree")
    if (pre["retained_anchor_sha256"] != intent["retained_anchor_sha256"]
            or pre["final_ack_sha256"] != _retained_by_role(intent, "ACK")["sha256"]
            or _utc(pre["authorized_at_utc"]) > certified
            or not re.fullmatch(r"[0-9a-f]{40}", pre["maintenance_head"])):
        raise QuotaRetentionError("Root preaction ACK/anchor/time/head mismatch")
    _git_query(root, "cat-file", "-e", pre["maintenance_head"] + "^{commit}")
    _git_query(root, "merge-base", "--is-ancestor", intent["accepted_binding"]["head_after"],
           pre["maintenance_head"])
    if constructor and ap.validate_repository(root)["head"] != pre["maintenance_head"]:
        raise QuotaRetentionError("Root maintenance head is not the actual clean head")
    release_ref = pre["release"]
    if (type(release_ref) is not dict or set(release_ref) != {"relative_path", "sha256"}
            or not re.fullmatch(r"\.local/two-site-refactor/reviews/[A-Za-z0-9][A-Za-z0-9._-]*\.json",
                                release_ref["relative_path"])):
        raise QuotaRetentionError("invalid release reference")
    release_raw, release = pins.read(root, release_ref["relative_path"], 4096)
    release_keys = {"schema_version", "record_kind", *_ID_KEYS, "final_ack_sha256",
                    "retained_anchor_sha256", "archived_stop", "incident_disposition",
                    "authorized_at_utc"}
    if (set(release) != release_keys or release["schema_version"] != "1.0"
            or release["record_kind"] != "ROOT_QUOTA_RETIREMENT_RELEASE"
            or release["incident_disposition"] != "CLEAR_NO_EXISTING_HOLD"
            or _sha(release_raw) != release_ref["sha256"]
            or _sha(release_raw) != intent["release_sha256"]
            or any(release[key] != pre[key] for key in (*_ID_KEYS, "final_ack_sha256",
                                                       "retained_anchor_sha256"))
            or _utc(release["authorized_at_utc"]) > _utc(pre["authorized_at_utc"])):
        raise QuotaRetentionError("Root release is unbound, held, or temporally invalid")
    archived = release["archived_stop"]
    archive = _retained_by_role(intent, "STOP_ARCHIVE")
    transition = _retained_by_role(intent, "STOP_TRANSITION")
    if (type(archived) is not dict or set(archived) != {"relative_path", "sha256", "fingerprint"}
            or archived["relative_path"] != archive["relative_path"]
            or archived["sha256"] != archive["sha256"]):
        raise QuotaRetentionError("Root release STOP archive binding mismatch")
    archive_raw, _ = pins.read(root, archive["relative_path"], 4096, canonical=False)
    if _sha(archive_raw) != archive["sha256"]:
        raise QuotaRetentionError("owned STOP archive drift")
    actual_fp = dict(zip(_FP_KEYS, pins.files[root / archive["relative_path"]][1]))
    if not _same(actual_fp, archived["fingerprint"]):
        raise QuotaRetentionError("owned STOP archive fingerprint mismatch")
    transition_raw, _ = pins.read(root, transition["relative_path"], 4096, canonical=False)
    from scripts.two_site_proposal_runner import unique_json
    transition_record = unique_json(transition_raw.decode("utf-8"))
    if _sha(transition_raw) != transition["sha256"]:
        raise QuotaRetentionError("STOP transition raw SHA mismatch")
    _validate_stop_transition(transition_record, archived)
    return [{"role": "RETIREMENT_ASSIGNMENT", "relative_path": relative, "sha256": _sha(raw)},
            {"role": "RETIREMENT_PREACTION", "relative_path": marker["relative_path"],
             "sha256": _sha(pre_raw)},
            {"role": "RELEASE", "relative_path": release_ref["relative_path"],
             "sha256": _sha(release_raw)}]


def _retained_by_role(intent: dict, role: str) -> dict:
    rows = [row for row in intent["retained"] if row["role"] == role]
    if len(rows) != 1:
        raise QuotaRetentionError("missing or duplicate retained role: " + role)
    return rows[0]



def _role_layout(history: dict) -> dict[str, str]:
    wait, result = history["wait"], history["transition"]
    original = history["ack"]["original_controller_run_id"]
    continuation = history["ack"]["continuation_controller_run_id"]
    paths = {
        "STARTUP": _run_relative(original, "preparation-startup.json"),
        "RESERVATION": _run_relative(original, "broker-reservation.json"),
        "WAIT": _run_relative(original, "quota-wait.json"),
        "CLAIM": _run_relative(original, "quota-resume-claim.json"),
        "SNAPSHOT": _run_relative(original, "quota-resume-lineage-snapshot.json"),
        "SUBMISSION": _run_relative(original, "quota-resume-submission.json"),
        "RESULT": _run_relative(continuation, "result.json"),
        "LINEAGE": _run_relative(continuation, "accepted-lineage.json"),
        "ACK": _run_relative(continuation, "final-ack.json"),
        "TASK": wait["reservation"]["task_path"],
        "ASSIGNMENT": (_B / "assignments" /
                       (wait["reservation"]["assignment_id"] + ".md")).as_posix(),
        "QUOTA_STATUS": (_B / "proposals" / wait["quota_attempt"]["run_id"] / "status.json").as_posix(),
    }
    for label, run_id in (("W", result["gemini_worker_run_id"]),
                          ("V", result["gemini_reviewer_run_id"])):
        if run_id is None:
            continue
        suffixes = dict(zip(("TASK", "RESULT", "STATUS", "PROMPT", "STREAM", "STDERR",
                             "SCHEMA", "COMPLETION"), _ROLE_FILES))
        for suffix, filename in suffixes.items():
            paths[label + "_" + suffix] = (_B / "proposals" / run_id / filename).as_posix()
    return paths


def _validate_intent_shape(value: dict) -> None:
    if (type(value) is not dict or set(value) != _INTENT_KEYS
            or value["schema_version"] != "1.0"
            or value["record_kind"] != "QUOTA_HISTORY_RETIREMENT_INTENT"
            or not _same(value["superseded_edges"], _EDGES)
            or type(value["retained"]) is not list or not 1 <= len(value["retained"]) <= 48
            or type(value["collectible"]) is not list or len(value["collectible"]) != 2):
        raise QuotaRetentionError("invalid retirement certificate shape")
    from scripts.two_site_provider_broker import CYCLE_RUN_RE
    for key in ("original_controller_run_id", "continuation_controller_run_id"):
        if type(value[key]) is not str or not CYCLE_RUN_RE.fullmatch(value[key]):
            raise QuotaRetentionError("invalid retirement controller ID")
    if value["original_controller_run_id"] == value["continuation_controller_run_id"]:
        raise QuotaRetentionError("retirement controller IDs are not distinct")
    for key in ("original_request_id", "resume_request_id"):
        if type(value[key]) is not str or not re.fullmatch(r"[0-9a-f]{32}", value[key]):
            raise QuotaRetentionError("invalid retirement request ID")
    for key in ("semantic_task_fingerprint", "release_sha256", "retained_anchor_sha256"):
        if type(value[key]) is not str or not _HEX.fullmatch(value[key]):
            raise QuotaRetentionError("invalid retirement digest")
    if value["original_request_id"] == value["resume_request_id"]:
        raise QuotaRetentionError("retirement requests are not distinct")
    if (_utc(value["certified_at_utc"]) < _utc(value["acknowledged_at_utc"]) + timedelta(days=10)):
        raise QuotaRetentionError("retirement is younger than ten days")
    rows = value["retained"]
    if rows != sorted(rows, key=lambda row: (row["role"], row["relative_path"])):
        raise QuotaRetentionError("retained closure is not canonically ordered")
    roles = set()
    for row in rows:
        if (type(row) is not dict or set(row) != {"role", "relative_path", "sha256"}
                or type(row["role"]) is not str or row["role"] in roles
                or not _HEX.fullmatch(row["sha256"])):
            raise QuotaRetentionError("invalid or duplicate retained role")
        roles.add(row["role"])
        _relative(row["relative_path"])
    if _base_anchor(rows) != value["retained_anchor_sha256"]:
        raise QuotaRetentionError("retained base anchor mismatch")
    binding = value["accepted_binding"]
    if (type(binding) is not dict or set(binding) != {
            "source_head", "head_after", "task_sha256", "assignment_sha256", "accepted_state"}
            or binding["accepted_state"] not in {"CONTINUE", "COMPLETE"}):
        raise QuotaRetentionError("invalid accepted business binding")
    expected_receipts = {
        "ORIG_RECEIPT": (_P / "terminal-quota" / (value["original_request_id"] + ".json")).as_posix(),
        "RESUME_RECEIPT": (_P / "terminal-continuation" / (value["resume_request_id"] + ".json")).as_posix(),
    }
    if [row.get("role") for row in value["collectible"]] != sorted(expected_receipts):
        raise QuotaRetentionError("collectible roles differ from exact two receipts")
    for row in value["collectible"]:
        if (type(row) is not dict or set(row) != {
                "role", "relative_path", "sha256", "response_sha256", "fingerprint"}
                or row["relative_path"] != expected_receipts[row["role"]]
                or not _HEX.fullmatch(row["sha256"]) or not _HEX.fullmatch(row["response_sha256"])
                or type(row["fingerprint"]) is not dict or set(row["fingerprint"]) != set(_FP_KEYS)
                or any(type(v) is not int for v in row["fingerprint"].values())):
            raise QuotaRetentionError("invalid collectible receipt binding")
    if len(_canonical(value)) > 16384:
        raise QuotaRetentionError("retirement closure exceeds 16384 bytes; KEEP")


def _read_retired_history_unframed(root: Path, original: str) -> dict:
    """Read explicit supersession; never call a missing receipt a successful read."""
    from scripts import two_site_assignment_scope as assignments
    from scripts.two_site_proposal_runner import unique_json
    with _Pins() as pins:
        raw, intent = pins.read(root, _run_relative(original, _INTENT), 16384)
        _validate_intent_shape(intent)
        if intent["original_controller_run_id"] != original:
            raise QuotaRetentionError("retirement certificate stored under the wrong run")
        shared = Path(assignments._resolve_shared_checkout(str(root)))
        for row in intent["retained"]:
            base = shared if row["role"] in {"ASSIGNMENT", "RETIREMENT_ASSIGNMENT"} else root
            leaf, _ = pins.read(base, row["relative_path"], _retained_limit(row["role"]), canonical=False)
            if _sha(leaf) != row["sha256"]:
                raise QuotaRetentionError("retained anchor drift: " + row["role"])
        authority_rows = _authority(root, pins, intent, constructor=False)
        if sorted(authority_rows, key=lambda row: (row["role"], row["relative_path"])) != [
                row for row in intent["retained"] if row["role"] in _AUTHORITY_ROLES]:
            raise QuotaRetentionError("authority rows differ from independently resolved chain")
        def record(role):
            row = _retained_by_role(intent, role)
            data, _ = pins.read(root, row["relative_path"], 187000, canonical=False)
            return data, unique_json(data.decode("utf-8"))
        _, wait = record("WAIT")
        before = _historical_repository(root, wait["repository"])
        scope, _, _, _ = _historical_scope(root, pins, before)
        task_row = _retained_by_role(intent, "TASK")
        task_raw, _ = pins.read(root, task_row["relative_path"], 160000, canonical=False)
        if _sha(task_raw) != wait["reservation"]["task_sha256"]:
            raise QuotaRetentionError("retired Task/reservation mismatch")
        result_raw, result = record("RESULT")
        continuation = intent["continuation_controller_run_id"]
        transition, context = _historical_transition(root, pins, before, scope, result, task_raw, continuation)
        _, snapshot = record("SNAPSHOT")
        from scripts.two_site_quota_wait_storage import read_initial_wait_record
        from scripts.two_site_quota_resume_claim_storage import read_known_reset_resume_claim
        from scripts.two_site_quota_resume_lineage_snapshot_storage import read_expected_lineage_snapshot
        expected_wait, expected_claim = snapshot["expected_wait"], snapshot["expected_claim"]
        actual_wait = read_initial_wait_record(root, original, expected_wait)
        claim = read_known_reset_resume_claim(root, original, expected_claim)
        strict = read_expected_lineage_snapshot(root, original, actual_wait, claim)
        if not _same(strict, snapshot) or not _same(expected_wait["repository"], before):
            raise QuotaRetentionError("retired wait/claim/snapshot closure mismatch")
        from scripts.two_site_quota_resume_submission_intent import read_quota_resume_submission_intent
        request = strict["expected_request"]
        submission = read_quota_resume_submission_intent(root, original, request)
        lineage_raw, lineage = record("LINEAGE")
        _, ack = record("ACK")
        upstream = copy.deepcopy(lineage["upstream"])
        joins = {
            "original_controller_run_id": original, "continuation_controller_run_id": continuation,
            "original_request_id": wait["request_id"], "resume_request_id": request["request_id"],
            "semantic_task_fingerprint": wait["semantic_task_fingerprint"],
            "original_wait_sha256": _retained_by_role(intent, "WAIT")["sha256"],
            "claim_sha256": _retained_by_role(intent, "CLAIM")["sha256"],
            "expected_lineage_snapshot_sha256": _retained_by_role(intent, "SNAPSHOT")["sha256"],
            "submission_intent_sha256": _retained_by_role(intent, "SUBMISSION")["sha256"],
        }
        for key in ("task_id", "assignment_id", "source_head", "task_sha256", "assignment_sha256"):
            joins[key] = request[key]
        from scripts.two_site_quota_resume_submission_intent import compute_canonical_request_sha256
        joins["request_sha256"] = compute_canonical_request_sha256(request)
        for key, expected in joins.items():
            if not _same(upstream.get(key), expected):
                raise QuotaRetentionError("retired upstream binding mismatch: " + key)
        receipts = {row["role"]: row for row in intent["collectible"]}
        if (wait["terminal_receipt_sha256"] != receipts["ORIG_RECEIPT"]["sha256"]
                or wait["response_sha256"] != receipts["ORIG_RECEIPT"]["response_sha256"]
                or expected_wait["terminal_receipt_sha256"] != receipts["ORIG_RECEIPT"]["sha256"]
                or expected_wait["response_sha256"] != receipts["ORIG_RECEIPT"]["response_sha256"]
                or upstream["terminal_receipt_sha256"] != receipts["RESUME_RECEIPT"]["sha256"]
                or upstream["response_sha256"] != receipts["RESUME_RECEIPT"]["response_sha256"]
                or ack["terminal_receipt_sha256"] != receipts["RESUME_RECEIPT"]["sha256"]):
            raise QuotaRetentionError("superseded receipt edges do not match certification")
        from scripts.two_site_quota_accepted_lineage_record import validate_accepted_lineage_record
        from scripts.two_site_quota_final_ack_record import validate_final_ack_candidate
        validate_accepted_lineage_record(lineage, transition, context, upstream, _sha(result_raw))
        validate_final_ack_candidate(ack, lineage, _sha(lineage_raw))
        expected_binding = {"source_head": before["head"], "head_after": transition["head_after"],
                            "task_sha256": wait["reservation"]["task_sha256"],
                            "assignment_sha256": scope.sha256, "accepted_state": ack["accepted_state"]}
        if not _same(intent["accepted_binding"], expected_binding):
            raise QuotaRetentionError("retirement certificate changed the business acceptance")
        if intent["acknowledged_at_utc"] != ack["acknowledged_at_utc"]:
            raise QuotaRetentionError("retirement certificate ACK time mismatch")
        history = {"wait": wait, "transition": transition, "ack": ack}
        layout = _role_layout(history)
        allowed = set(layout) | _AUTHORITY_ROLES | {"STOP_ARCHIVE", "STOP_TRANSITION",
                   "SEMANTIC_QUOTA", "SEMANTIC_PIPELINE", "W_DELTA", "V_DELTA"}
        for row in intent["retained"]:
            if row["role"] not in allowed or row["role"] in layout and row["relative_path"] != layout[row["role"]]:
                raise QuotaRetentionError("unknown or noncanonical retained role")
        if not set(layout).issubset({row["role"] for row in intent["retained"]}):
            raise QuotaRetentionError("incomplete retained business closure")
        # Explicit profiles, not ambient ContextVars and not a fallback search.
        from scripts.two_site_semantic_attempts import read_existing_semantic_attempt
        task_value = unique_json(task_raw.decode("utf-8"))
        present_roles = {row["role"] for row in intent["retained"]}
        if "SEMANTIC_PIPELINE" not in present_roles:
            raise QuotaRetentionError("retired NORMAL semantic denial anchor is missing")
        semantic_records = []
        for role, directory in (("SEMANTIC_QUOTA", _B), ("SEMANTIC_PIPELINE", _B / "pipeline")):
            if role in present_roles:
                row = _retained_by_role(intent, role)
                expected_path = (directory / "semantic-attempts" /
                                 (intent["semantic_task_fingerprint"] + ".json")).as_posix()
                if row["relative_path"] != expected_path:
                    raise QuotaRetentionError("retired semantic role path mismatch")
                semantic_records.append(read_existing_semantic_attempt(
                    root, directory, task_value, wait["reservation"]["run_prefix"]))
        if any(not _same(value, semantic_records[0]) for value in semantic_records[1:]):
            raise QuotaRetentionError("retired semantic stores conflict")
        for role, run_id in (("W_DELTA", result["gemini_worker_run_id"]),
                             ("V_DELTA", result["gemini_reviewer_run_id"])):
            if role in present_roles:
                if not run_id or _retained_by_role(intent, role)["relative_path"] != (
                        _B / "proposals" / run_id / "delta-result.json").as_posix():
                    raise QuotaRetentionError("retired actor delta path mismatch")
        if _retained_by_role(intent, "STOP_TRANSITION")["relative_path"] != _STOP_TRANSITION_REL:
            raise QuotaRetentionError("unrecognized owned STOP transition path")
        for row in intent["collectible"]:
            target = root / row["relative_path"]
            if os.path.lexists(target):
                leaf, _ = pins.read(root, row["relative_path"], _receipt_limit(row["relative_path"]), canonical=False)
                if (_sha(leaf) != row["sha256"] or dict(zip(
                        _FP_KEYS, pins.files[target][1])) != row["fingerprint"]):
                    raise QuotaRetentionError("pending collectible receipt drift")
        state = "CERTIFIED_RETIRED_PENDING"
        complete_path = root / _run_relative(original, _COMPLETE)
        if os.path.lexists(complete_path):
            _, completion = pins.read(root, _run_relative(original, _COMPLETE), 2048)
            expected_deleted = [{key: row[key] for key in ("role", "relative_path", "sha256")}
                                for row in intent["collectible"]]
            if (set(completion) != {"schema_version", "record_kind", "intent_sha256",
                                   "completed_at_utc", "deleted_receipts"}
                    or completion["schema_version"] != "1.0"
                    or completion["record_kind"] != "QUOTA_HISTORY_RETIREMENT_COMPLETE"
                    or completion["intent_sha256"] != _sha(raw)
                    or not _same(completion["deleted_receipts"], expected_deleted)
                    or _utc(completion["completed_at_utc"]) < _utc(intent["certified_at_utc"])):
                raise QuotaRetentionError("invalid retirement completion")
            if any(os.path.lexists(root / row["relative_path"]) for row in intent["collectible"]):
                raise QuotaRetentionError("completed retirement receipt reappeared")
            state = "CERTIFIED_RETIRED_COMPLETE"
        return {"state": state, "ack": ack, "retirement": intent, "intent_sha256": _sha(raw)}


_STOP_TRANSITION_REL = (_B / "reviews" /
    "AE_REQUIRED_COMPLETION_ROOT_MAINTENANCE_OBSERVED_20261020.json").as_posix()


def _validate_stop_transition(record: object, archived: dict) -> None:
    keys = {"kind", "date_utc", "head", "decision_sha256", "independent_review", "archive",
            "broker_start", "a2_first_build", "preserved", "readonly_invoker_failures_preserved"}
    if (type(record) is not dict or set(record) != keys
            or record["kind"] != "ROOT_AE_CLOSEOUT_REAL_MAINTENANCE_EFFECTS_OBSERVED"):
        raise QuotaRetentionError("unknown owned STOP transition observation schema")
    archive = record["archive"]
    if (type(archive) is not dict or set(archive) != {"actual_exit_code", "actual_chunk", "path",
            "sha256", "mark_strings", "source_absent", "leaf_and_both_directory_fsync"}
            or type(archive["actual_exit_code"]) is not int or archive["actual_exit_code"] != 0
            or type(archive["actual_chunk"]) is not str or not archive["actual_chunk"]
            or archive["source_absent"] is not True or archive["leaf_and_both_directory_fsync"] is not True
            or type(archive["mark_strings"]) is not list or len(archive["mark_strings"]) != 9
            or any(type(x) is not str or not re.fullmatch("0|[1-9][0-9]*", x)
                   for x in archive["mark_strings"])):
        raise QuotaRetentionError("owned STOP transition is incomplete or unsuccessful")
    numbers = [int(value) for value in archive["mark_strings"]]
    fingerprint = dict(zip(_FP_KEYS, numbers[:4] + numbers[5:]))
    if (archived["relative_path"] != (_B / "reviews" / archive["path"]).as_posix()
            or "/" in archive["path"] or archive["sha256"] != archived["sha256"]
            or not _same(fingerprint, archived["fingerprint"])):
        raise QuotaRetentionError("owned STOP transition path/SHA/stat mismatch")


def _selected_authority_hints(root: Path) -> dict:
    """Discover only from the actual registry-selected Root Assignment."""
    from scripts import two_site_assignment_scope as assignments
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_proposal_runner import unique_json
    with _Pins() as pins:
        shared = Path(assignments._resolve_shared_checkout(str(root)))
        pins.read(shared, (_B / "worktrees.yaml").as_posix(), 262144, canonical=False)
        registry_raw, _ = pins.read(shared, (_B / "worktrees.yaml").as_posix(), 262144, canonical=False)
        registry = assignments._safe_load_yaml(registry_raw)
        hint = assignments._validate_assignment_id(registry["integration"]["active_assignment_id"])
        relative = (_B / "assignments" / (hint + ".md")).as_posix()
        raw, _ = pins.read(shared, relative, assignments.MAX_CONTROL_FILE_BYTES, canonical=False)
        pins.read(root, "docs/refactor/two-server/execution-order.yaml", 262144, canonical=False)
        actual = assignments.load_active_assignment_scope(root)
        described = assignments.parse_assignment_scope_bytes(
            raw, expected_assignment_id=hint, integration_branch=ap.BRANCH)
        if described != actual:
            raise QuotaRetentionError("selected Root Scope differs from pinned Markdown")
        marker = _marker(raw)
        pre_raw, pre = pins.read(root, marker["relative_path"], 4096)
        if _sha(pre_raw) != marker["sha256"] or type(pre.get("release")) is not dict:
            raise QuotaRetentionError("Root preaction discovery binding mismatch")
        release_raw, release = pins.read(root, pre["release"]["relative_path"], 4096)
        if _sha(release_raw) != pre["release"]["sha256"]:
            raise QuotaRetentionError("Root release discovery binding mismatch")
        observation_raw, _ = pins.read(root, _STOP_TRANSITION_REL, 4096, canonical=False)
        observation = unique_json(observation_raw.decode("utf-8"))
        archived = release.get("archived_stop")
        _validate_stop_transition(observation, archived)
        return {
            "retirement_authority": {"assignment_id": actual.assignment_id,
                                     "assignment_sha256": actual.sha256},
            "release_sha256": _sha(release_raw), "release": release,
            "authority_rows": [
                {"role": "RETIREMENT_ASSIGNMENT", "relative_path": relative, "sha256": _sha(raw)},
                {"role": "RETIREMENT_PREACTION", "relative_path": marker["relative_path"], "sha256": _sha(pre_raw)},
                {"role": "RELEASE", "relative_path": pre["release"]["relative_path"], "sha256": _sha(release_raw)}],
            "stop_rows": [
                {"role": "STOP_ARCHIVE", "relative_path": archived["relative_path"], "sha256": archived["sha256"]},
                {"role": "STOP_TRANSITION", "relative_path": _STOP_TRANSITION_REL,
                 "sha256": _sha(observation_raw)}],
        }



def _reference_values(value: dict) -> list:
    """Only fixed schema edges, never prose/string search or arbitrary recursion."""
    fields = ("controller_run_id", "original_controller_run_id", "continuation_controller_run_id",
              "request_id", "original_request_id", "resume_request_id", "semantic_task_fingerprint",
              "fingerprint", "terminal_receipt_sha256", "response_sha256", "evidence",
              "evidence_path", "run_id", "original_wait_sha256")
    result = [value[key] for key in fields if key in value]
    for envelope in ("request", "reservation", "upstream", "expected_wait", "expected_claim",
                     "expected_request", "quota_attempt", "result"):
        child = value.get(envelope)
        if type(child) is dict:
            result.extend(child[key] for key in fields if key in child)
    return result


def build_quota_reference_index_locked(root: Path, intent: dict) -> dict:
    """Fresh bounded typed index. Unknown or unbound evidence means KEEP."""
    from scripts import two_site_provider_broker as broker
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_proposal_runner import unique_json
    from scripts.two_site_quota_continuation_terminal_storage import read_terminal_continuation_record
    original, continuation = intent["original_controller_run_id"], intent["continuation_controller_run_id"]
    owned = {row["relative_path"] for row in intent["retained"]
             if row["role"] not in {"ASSIGNMENT", "RETIREMENT_ASSIGNMENT"}}
    owned |= {row["relative_path"] for row in intent["collectible"]}
    owned |= {_run_relative(original, _INTENT), _run_relative(original, _COMPLETE)}
    targets = {intent[key] for key in _ID_KEYS}
    targets |= {row[key] for row in intent["collectible"] for key in ("sha256", "response_sha256")}
    targets |= {str(root / _A / "runs" / cid) for cid in (original, continuation)}
    references = []
    requests = {}
    with _Pins() as pins:
        def read(relative, limit, *, canonical=False):
            raw, _ = pins.read(root, relative, limit, canonical=canonical)
            return raw, unique_json(raw.decode("utf-8"))
        def observe(relative, value):
            if relative not in owned and any(item in targets for item in _reference_values(value)
                                             if type(item) is str):
                references.append(relative)
        for family in ("requests", "claimed"):
            directory = root / _P / family
            if not os.path.lexists(directory):
                continue
            pins.directory(directory, private=True)
            names = _bounded_names(directory, 32, "request reference index")
            for name in names:
                if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
                    raise QuotaRetentionError("unknown queue reference name")
                relative = (_P / family / name).as_posix()
                _, value = read(relative, broker.MAX_REQUEST_BYTES)
                broker._validate_claimed_request_shape(value, name[:-5])
                if name[:-5] in requests:
                    raise QuotaRetentionError("request exists in both queue roots")
                requests[name[:-5]] = value
                observe(relative, value)
        for family in ("terminal-quota", "terminal-continuation"):
            directory = root / _P / family
            if not os.path.lexists(directory):
                continue
            pins.directory(directory, private=True)
            names = _bounded_names(directory, 64, "terminal reference index")
            for name in names:
                if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
                    raise QuotaRetentionError("unknown terminal reference name")
                relative = (_P / family / name).as_posix()
                _, candidate = read(relative, _receipt_limit(relative), canonical=True)
                if relative in owned:
                    continue  # Whole unretired oracle or certified retained proof validated this edge.
                value = (broker.read_terminal_quota_record(root, name[:-5]) if family == "terminal-quota"
                         else read_terminal_continuation_record(root, name[:-5],
                                                               expected_request=candidate["request"]))
                req = value["request"]
                if req["request_id"] in requests and not _same(requests[req["request_id"]], req):
                    raise QuotaRetentionError("conflicting authoritative request")
                requests[req["request_id"]] = req
                observe(relative, value)
        responses = root / _P / "responses"
        if os.path.lexists(responses):
            pins.directory(responses, private=True)
            names = _bounded_names(responses, 32, "response reference index")
            for name in names:
                if not re.fullmatch(r"[0-9a-f]{32}\.json", name):
                    raise QuotaRetentionError("unknown response reference name")
                relative = (_P / "responses" / name).as_posix()
                _, value = read(relative, broker.MAX_RESPONSE_BYTES)
                request = requests.get(name[:-5])
                if request is None:
                    raise QuotaRetentionError("unbound response reference")
                broker.validate_response(request, value)
                observe(relative, value)
        validated_continuations = set()
        directories = _scan_runs(root)
        for directory in directories:
            wait_relative = (directory / "quota-wait.json").relative_to(root).as_posix()
            if wait_relative not in owned and os.path.lexists(directory / "quota-wait.json"):
                other = read_final_ack_history_locked(root, directory.name)
                if other is None:
                    raise QuotaRetentionError("unresolved outside wait retains quota receipts")
                validated_continuations.add(other["ack"]["continuation_controller_run_id"])
        for directory in directories:
            names = _bounded_names(directory, 128, "run lifecycle")
            lifecycle = {"quota-wait.json", "quota-resume-claim.json",
                         "quota-resume-lineage-snapshot.json", "quota-resume-submission.json",
                         "accepted-lineage.json", "final-ack.json", _INTENT, _COMPLETE}
            outside = {name for name in names if name in lifecycle
                       and (directory / name).relative_to(root).as_posix() not in owned}
            if outside:
                # An orphan continuation cannot self-authorize by field discovery.
                # A complete original history must pass the independent cold oracle;
                # a continuation is accepted only through that validated original.
                if "quota-wait.json" in outside:
                    other = read_final_ack_history_locked(root, directory.name)
                    if other is None:
                        raise QuotaRetentionError("unresolved outside wait retains quota receipts")
                elif directory.name not in validated_continuations:
                    raise QuotaRetentionError("unbound outside quota lifecycle retains receipts")
            for name in names:
                relative = (directory / name).relative_to(root).as_posix()
                if relative in owned:
                    continue
                if name in {"prompt.txt", "stream.jsonl", "stderr.txt", "codex-version.stdout",
                            "codex-version.stderr", "codex-login.stdout", "codex-login.stderr"}:
                    pins.read(root, relative, 8 * 1024 * 1024, canonical=False)
                    continue
                if name == "preparation-startup.json":
                    from scripts.two_site_preparation_contract import PreparationStartup
                    _, value = read(relative, 4096)
                    PreparationStartup.from_mapping(value)
                elif name == "result.json":
                    _, value = read(relative, 187000)
                    ap.validate_result(value)
                elif name in {"quota-wait.json", "quota-resume-claim.json",
                              "quota-resume-lineage-snapshot.json", "quota-resume-submission.json",
                              "accepted-lineage.json", "final-ack.json", _INTENT, _COMPLETE}:
                    # Validate each OTHER history once, then use only typed edges.
                    if name == "quota-wait.json":
                        other = read_final_ack_history_locked(root, directory.name)
                        if other is None:
                            raise QuotaRetentionError("unresolved outside wait retains quota receipts")
                    _, value = read(relative, 131072, canonical=True)
                elif name == "broker-reservation.json":
                    _, value = read(relative, 16384, canonical=True)
                    broker.read_cycle_reservation(root, directory.name)
                elif re.fullmatch(r"quota-unknown-health-[1-5]\.json", name):
                    raise QuotaRetentionError("outside UNKNOWN observation requires active-wait custody")
                elif name == "status.json":
                    _, value = read(relative, 187000)
                    if (type(value) is not dict or value.get("state") not in {
                            "RUNNING", "CONTINUE", "COMPLETE", "HALT", "RESCOPE", "BLOCKED",
                            "FAILED", "ERROR", "STOPPED", "WAITING_QUOTA"}):
                        raise QuotaRetentionError("unknown outside lifecycle status")
                    if value.get("state") in {"RUNNING", "WAITING_QUOTA", "BLOCKED", "FAILED", "ERROR"}:
                        raise QuotaRetentionError("outside lifecycle is active, incident-held, or uncertain")
                else:
                    raise QuotaRetentionError("unknown run lifecycle leaf: " + name)
                observe(relative, value)
        state = root / _A
        if os.path.lexists(state):
            pins.directory(state, private=True)
            state_names = _bounded_names(state, 512, "state reference index")
            for name in state_names:
                if re.fullmatch(r"rescope-[0-9a-f]{64}", name):
                    epoch = state / name
                    pins.directory(epoch, private=True)
                    names = _bounded_names(epoch, 6, "rescope epoch")
                    for child in names:
                        relative = (epoch / child).relative_to(root).as_posix()
                        if child in {"rescope_intent_1", "rescope_intent_2", "rescope_intent_3"}:
                            raw, _ = pins.read(root, relative, 64, canonical=False)
                            if raw != (child + "\n").encode("ascii"):
                                raise QuotaRetentionError("invalid rescope intent")
                        elif child in {"rescope_receipt.json", "rescope_receipt_2.json", "rescope_receipt_3.json"}:
                            raw, candidate = read(relative, 16384)
                            value = ap.parse_rescope_receipt(raw, candidate["repository"])
                            observe(relative, value["result"])
                        else:
                            raise QuotaRetentionError("unknown rescope epoch reference")
                elif name == "adoptions":
                    directory = state / name
                    pins.directory(directory, private=True)
                    names = _bounded_names(directory, 512, "adoption reference index")
                    from scripts.two_site_cold_start_adoption_record import _TERMINAL_ADOPTION_KEYS
                    for child in names:
                        if not re.fullmatch(r"rescope-[0-9a-f]{64}\.json", child):
                            raise QuotaRetentionError("unknown adoption reference name")
                        relative = (directory / child).relative_to(root).as_posix()
                        _, value = read(relative, 16384)
                        if set(value) != _TERMINAL_ADOPTION_KEYS:
                            raise QuotaRetentionError("unknown adoption reference schema")
                        epoch_name = value["old_epoch_basename"]
                        if type(epoch_name) is not str or not re.fullmatch(r"rescope-[0-9a-f]{64}", epoch_name):
                            raise QuotaRetentionError("invalid adoption old epoch")
                        old_relative = (_A / epoch_name / "rescope_receipt_3.json").as_posix()
                        old_raw, old_candidate = read(old_relative, 16384)
                        old = ap.parse_rescope_receipt(old_raw, old_candidate["repository"])
                        if old["terminal"] is not True:
                            raise QuotaRetentionError("adoption lacks terminal old epoch")
                        from scripts.two_site_cold_start_adoption_record import validate_terminal_adoption_record
                        value = validate_terminal_adoption_record(value, value["new_repository"],
                            {"epoch_basename": epoch_name, "repository": old["repository"],
                             "terminal_receipt_sha256": _sha(old_raw)}, value["prior_checkpoint_sha256"])
                        observe(relative, value)
                elif name == "accepted_checkpoint.json":
                    from scripts.two_site_cold_start_adoption_storage import _load_and_audit_accepted_checkpoint
                    relative = (_A / name).as_posix()
                    pins.read(root, relative, 262144, canonical=False)
                    value, _ = _load_and_audit_accepted_checkpoint(pins.directories[state][0], state)
                    observe(relative, value)
                elif name == "status.json":
                    relative = (_A / name).as_posix()
                    _, value = read(relative, 187000)
                    if (value.get("state") != "STOPPED" or value.get("ambiguous_change") is True
                            or value.get("blockers") not in (None, [])):
                        raise QuotaRetentionError("Supervisor is active, incident-held, or uncertain")
                    observe(relative, value)
                elif name not in {"runs", "autopilot.lock", "supervisor.lock"}:
                    raise QuotaRetentionError("unknown state reference root: " + name)
        for directory in (_B / "semantic-attempts", _B / "pipeline" / "semantic-attempts"):
            path = root / directory / (intent["semantic_task_fingerprint"] + ".json")
            if os.path.lexists(path):
                relative = path.relative_to(root).as_posix()
                raw, value = read(relative, 4096)
                from scripts.two_site_semantic_attempts import _KEYS
                if set(value) != _KEYS or value.get("state") != "RESERVED":
                    raise QuotaRetentionError("unknown semantic denial record")
        return {"state": "REFERENCED" if references else "UNREFERENCED",
                "references": sorted(set(references))}



def _maintenance_boundary(root: Path, expected: dict | None = None) -> dict:
    from scripts import two_site_codex_autopilot as ap
    from scripts import two_site_provider_broker as broker
    from scripts.two_site_proposal_runner import unique_json
    import math
    import time
    if os.path.lexists(root / "STOP") or os.path.lexists(root / _A / "STOP"):
        raise QuotaRetentionError("a new STOP blocks quota maintenance")
    with _Pins() as pins:
        raw, _ = pins.read(root, (_A / "status.json").as_posix(), 187000, canonical=False)
        status = unique_json(raw.decode("utf-8"))
        if (status.get("state") != "STOPPED" or status.get("ambiguous_change") is True
                or status.get("blockers") not in (None, [])):
            raise QuotaRetentionError("Supervisor is not durably stopped without incident uncertainty")
        broker_status = broker._read_broker_status(root, root / _P / "status.json")
        pid, started = broker_status.get("pid"), broker_status.get("started_at")
        updated = broker_status.get("updated_at")
        if (broker_status.get("state") != "IDLE" or type(pid) is not int or pid <= 0
                or type(started) not in (int, float) or not math.isfinite(started)
                or type(updated) not in (int, float) or not math.isfinite(updated)
                or not 0 <= time.time() - updated <= broker.STATUS_MAX_AGE_SECONDS):
            raise QuotaRetentionError("Broker is not fresh LIVE IDLE")
        os.kill(pid, 0)
        repository = ap.validate_repository(root, require_clean=True)
        frame = {"repository": ap.validate_accepted_repository(repository),
                 "broker_identity": [pid, started], "status_sha256": _sha(raw)}
        if expected is not None and not _same(frame, expected):
            raise QuotaRetentionError("maintenance ownership/repository/status drift")
        return frame


@contextmanager
def _maintenance_holds(root: Path):
    from scripts import two_site_codex_autopilot as ap
    from scripts.two_site_cold_start_broker_quiescence import hold_idle_broker_client_lock_for_cold_adoption
    from scripts.two_site_proposal_pipeline import pipeline_lock
    from scripts.two_site_proposal_runner import acquire_proposal_lock, release_proposal_lock
    root = _root(root)
    monitor = root / _B / "monitor" / "monitor.lock"
    locks = (monitor, root / _A / "autopilot.lock", root / _P / "client.lock",
             root / _B / "pipeline" / ".pipeline.lock", root / _B / "proposals" / ".runner.lock")
    with ap.exclusive_supervisor_lock(monitor):
        with ap.exclusive_supervisor_lock(locks[1]):
            with hold_idle_broker_client_lock_for_cold_adoption(root):
                with pipeline_lock(root):
                    descriptor, _ = acquire_proposal_lock(root)
                    primary = None
                    try:
                        with _held_sync_lock_pins(locks) as verify_locks:
                            frame = _maintenance_boundary(root)
                            def check():
                                verify_locks()
                                _maintenance_boundary(root, frame)
                            check()
                            yield check
                            check()
                    except BaseException as exc:
                        primary = exc
                        raise
                    finally:
                        try:
                            release_proposal_lock(descriptor)
                        except BaseException:
                            if primary is None:
                                raise


def _build_retirement_intent_locked(root: Path, original: str, now_utc: str) -> dict:
    observed_now = _now_utc()
    if _utc(now_utc) > observed_now or observed_now - _utc(now_utc) > timedelta(seconds=30):
        raise QuotaRetentionError("uncertain or caller-future maintenance clock")
    history = read_final_ack_history_locked(root, original, pipeline_dir=_B / "pipeline")
    if history is None or history["state"] != "ACKNOWLEDGED":
        raise QuotaRetentionError("no unretired independently acknowledged closure")
    ack, wait = history["ack"], history["wait"]
    if _utc(now_utc) < _utc(ack["acknowledged_at_utc"]) + timedelta(days=10):
        raise QuotaRetentionError("ACK is younger than ten days")
    hints = _selected_authority_hints(root)
    layout = _role_layout(history)
    from scripts import two_site_assignment_scope as assignments
    shared = Path(assignments._resolve_shared_checkout(str(root)))
    retained = []
    collectible = []
    with _Pins() as pins:
        for role, relative in layout.items():
            base = shared if role == "ASSIGNMENT" else root
            raw, _ = pins.read(base, relative, _retained_limit(role), canonical=False)
            retained.append({"role": role, "relative_path": relative, "sha256": _sha(raw)})
        fingerprint = wait["semantic_task_fingerprint"]
        for role, directory in (("SEMANTIC_QUOTA", _B), ("SEMANTIC_PIPELINE", _B / "pipeline")):
            relative = (directory / "semantic-attempts" / (fingerprint + ".json")).as_posix()
            if os.path.lexists(root / relative):
                raw, _ = pins.read(root, relative, 4096, canonical=False)
                retained.append({"role": role, "relative_path": relative, "sha256": _sha(raw)})
            elif role == "SEMANTIC_PIPELINE":
                raise QuotaRetentionError("actual NORMAL producer reservation is missing")
        for role, run_id in (("W_DELTA", history["transition"]["gemini_worker_run_id"]),
                             ("V_DELTA", history["transition"]["gemini_reviewer_run_id"])):
            if run_id:
                relative = (_B / "proposals" / run_id / "delta-result.json").as_posix()
                if os.path.lexists(root / relative):
                    raw, _ = pins.read(root, relative, 187000, canonical=False)
                    retained.append({"role": role, "relative_path": relative, "sha256": _sha(raw)})
        for row in hints["stop_rows"]:
            raw, _ = pins.read(root, row["relative_path"], 4096, canonical=False)
            if _sha(raw) != row["sha256"]:
                raise QuotaRetentionError("Root-owned STOP core anchor drift")
            retained.append(row)
        retained.extend(hints["authority_rows"])
        retained.sort(key=lambda row: (row["role"], row["relative_path"]))
        for role, family, request_id in (
                ("ORIG_RECEIPT", "terminal-quota", wait["request_id"]),
                ("RESUME_RECEIPT", "terminal-continuation", ack["resume_request_id"])):
            relative = (_P / family / (request_id + ".json")).as_posix()
            raw, value = pins.read(root, relative, _receipt_limit(relative))
            fp = dict(zip(_FP_KEYS, pins.files[root / relative][1]))
            collectible.append({"role": role, "relative_path": relative, "sha256": _sha(raw),
                                "response_sha256": value["response_sha256"], "fingerprint": fp})
        intent = {
            "schema_version": "1.0", "record_kind": "QUOTA_HISTORY_RETIREMENT_INTENT",
            "original_controller_run_id": original,
            "continuation_controller_run_id": ack["continuation_controller_run_id"],
            "original_request_id": wait["request_id"], "resume_request_id": ack["resume_request_id"],
            "semantic_task_fingerprint": fingerprint, "acknowledged_at_utc": ack["acknowledged_at_utc"],
            "certified_at_utc": now_utc, "release_sha256": hints["release_sha256"],
            "retained_anchor_sha256": _base_anchor(retained),
            "accepted_binding": {"source_head": history["repository"]["head"], "head_after": ack["head_after"],
                "task_sha256": wait["reservation"]["task_sha256"], "assignment_sha256": ack["assignment_sha256"],
                "accepted_state": ack["accepted_state"]},
            "retained": retained, "collectible": collectible, "superseded_edges": copy.deepcopy(_EDGES),
            "retirement_authority": hints["retirement_authority"],
        }
        _validate_intent_shape(intent)
        _authority(root, pins, intent, constructor=True)
        index = build_quota_reference_index_locked(root, intent)
        if index["state"] != "UNREFERENCED":
            raise QuotaRetentionError("outside references retain receipts: " + repr(index["references"]))
        return intent


def classify_quota_retention_locked(root: Path, original: str, now_utc: str) -> dict:
    """No writes. Return a reasoned KEEP or a fully derived eligible certificate."""
    try:
        if has_retirement_records(root) and os.path.lexists(root / _run_relative(original, _INTENT)):
            history = _read_retired_history(root, original)
            return {"state": history["state"], "intent_sha256": history["intent_sha256"]}
        intent = _build_retirement_intent_locked(root, original, now_utc)
        return {"state": "ELIGIBLE", "intent": intent}
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        return {"state": "KEEP", "reason": type(exc).__name__ + ": " + str(exc)[:512]}


def _sync_run(root: Path, original: str) -> None:
    from scripts.two_site_provider_broker import _open_existing_cycle_run_directory
    with _open_existing_cycle_run_directory(root, original) as (_, fd, verify):
        verify()
        os.fsync(fd)
        verify()


def _unlink_certified_receipt(root: Path, row: dict, check) -> None:
    path = root / row["relative_path"]
    check()
    with _Pins() as pins:
        pins.directory(path.parent, private=True)
        fd = pins.directories[path.parent][0]
        try:
            raw, _ = pins.read(root, row["relative_path"], _receipt_limit(row["relative_path"]), canonical=False)
        except FileNotFoundError:
            os.fsync(fd)  # Reconcile only this exact listed intended deletion.
            return
        if (_sha(raw) != row["sha256"]
                or dict(zip(_FP_KEYS, pins.files[path][1])) != row["fingerprint"]):
            raise QuotaRetentionError("collectible receipt identity changed")
        check()
        pins.verify()
        parent_identity = _fp(os.fstat(fd))
        primary = None
        try:
            os.unlink(path.name, dir_fd=fd)
        except BaseException as exc:
            primary = exc
            raise
        finally:
            # A failed unlink may still have taken effect; always try durability.
            try:
                os.fsync(fd)
            except BaseException:
                if primary is None:
                    raise
            if not os.path.lexists(path):
                pins.files.pop(path, None)
                after = _fp(os.fstat(fd))
                if after[:5] != parent_identity[:5]:
                    raise QuotaRetentionError("receipt parent changed during unlink")
                pins.directories[path.parent] = (fd, after)
        check()


def prune_quota_receipts(root: Path, original: str, now_utc: str) -> dict:
    """Root-only explicit maintenance entry; self-own the existing five holds.

    No CLI/Monitor/Provider calls this function. Root must first publish the
    selected immutable Scope -> preaction -> release binding. No age override,
    leaf list, held-lock boolean, automatic release, replay, or budget reset.
    """
    _root(root)
    now = _utc(now_utc)
    if now > _now_utc() or _now_utc() - now > timedelta(seconds=30):
        raise QuotaRetentionError("caller time is not the current trusted maintenance clock")
    from scripts.two_site_quota_wait_storage import _publish_cycle_record_bytes
    with _maintenance_holds(root) as check:
        path = root / _run_relative(original, _INTENT)
        check()
        if os.path.lexists(path):
            _sync_run(root, original)
            history = _read_retired_history(root, original)
            intent = history["retirement"]
            if now < _utc(intent["certified_at_utc"]):
                raise QuotaRetentionError("maintenance clock regressed")
            if history["state"] == "CERTIFIED_RETIRED_COMPLETE":
                check()
                return {"state": history["state"], "deleted": []}
        else:
            intent = _build_retirement_intent_locked(root, original, now_utc)
            check()
            # Storage provides file fsync, no-replace publication, directory
            # fsync and strict readback. Any post-link failure escapes BEFORE unlink.
            _publish_cycle_record_bytes(root, original, _canonical(intent), _INTENT)
            history = _read_retired_history(root, original)
        intent_sha = _sha(_canonical(intent))
        if history["intent_sha256"] != intent_sha:
            raise QuotaRetentionError("retirement intent changed before maintenance")
        deleted = []
        for row in intent["collectible"]:
            check()
            if _now_utc() < _utc(intent["certified_at_utc"]):
                raise QuotaRetentionError("maintenance clock regressed before unlink")
            fresh = _read_retired_history(root, original)
            if fresh["intent_sha256"] != intent_sha:
                raise QuotaRetentionError("retirement intent drift")
            if build_quota_reference_index_locked(root, intent)["state"] != "UNREFERENCED":
                raise QuotaRetentionError("new outside reference blocks further unlink")
            _unlink_certified_receipt(root, row, check)
            deleted.append(row["relative_path"])
        check()
        if any(os.path.lexists(root / row["relative_path"]) for row in intent["collectible"]):
            raise QuotaRetentionError("retirement leaves are not all absent")
        completion = {
            "schema_version": "1.0", "record_kind": "QUOTA_HISTORY_RETIREMENT_COMPLETE",
            "intent_sha256": intent_sha, "completed_at_utc": now_utc,
            "deleted_receipts": [{key: row[key] for key in ("role", "relative_path", "sha256")}
                                 for row in intent["collectible"]],
        }
        if len(_canonical(completion)) > 2048:
            raise QuotaRetentionError("completion exceeds 2048 bytes")
        _publish_cycle_record_bytes(root, original, _canonical(completion), _COMPLETE)
        final = _read_retired_history(root, original)
        check()
        if final["state"] != "CERTIFIED_RETIRED_COMPLETE":
            raise QuotaRetentionError("retirement completion did not strictly read back")
        return {"state": final["state"], "deleted": deleted}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_git_blob(root: Path, commit: str, path: str) -> bytes:
    from scripts.two_site_agent_sandbox import _run_bounded_process
    from scripts.two_site_maintenance_transition_git import _GIT_ENV
    process = _run_bounded_process(
        ["/usr/bin/git", "--no-replace-objects", "-C", str(root), "show", commit + ":" + path],
        cwd=root, env=_GIT_ENV, timeout=10, output_limit=160000)
    if process.returncode != 0:
        raise QuotaRetentionError("bounded historical Git blob query failed")
    return process.stdout


def _actor_limit(filename: str) -> int:
    from scripts.two_site_proposal_runner import MAX_CONTEXT, MAX_OUTPUT
    if filename in {"stream.jsonl", "stderr.txt"}:
        return MAX_OUTPUT
    if filename == "task.json":
        return 3 * MAX_CONTEXT
    if filename == "prompt.txt":
        return 2 * MAX_CONTEXT
    return MAX_CONTEXT


def _retained_limit(role: str) -> int:
    if role.startswith(("W_", "V_")):
        suffix = role.split("_", 1)[1]
        filename = dict(zip(("TASK", "RESULT", "STATUS", "PROMPT", "STREAM", "STDERR",
                              "SCHEMA", "COMPLETION", "DELTA"),
                             (*_ROLE_FILES, "delta-result.json")))[suffix]
        return _actor_limit(filename)
    if role in {"ASSIGNMENT", "RETIREMENT_ASSIGNMENT"}:
        from scripts.two_site_assignment_scope import MAX_CONTROL_FILE_BYTES
        return MAX_CONTROL_FILE_BYTES
    return 187000


def _pin_current_repository(root: Path, pins: _Pins) -> dict:
    from scripts import two_site_assignment_scope as assignments
    from scripts import two_site_codex_autopilot as ap
    shared = Path(assignments._resolve_shared_checkout(str(root)))
    raw, _ = pins.read(shared, (_B / "worktrees.yaml").as_posix(), 262144, canonical=False)
    registry = assignments._safe_load_yaml(raw)
    hint = assignments._validate_assignment_id(registry["integration"]["active_assignment_id"])
    pins.read(shared, (_B / "assignments" / (hint + ".md")).as_posix(),
              assignments.MAX_CONTROL_FILE_BYTES, canonical=False)
    for relative in ap.CONTROL_PATHS:
        pins.read(root, relative.as_posix(), assignments.MAX_CONTROL_FILE_BYTES, canonical=False)
    pins.read(root, "docs/refactor/two-server/execution-order.yaml", 262144, canonical=False)
    return ap.validate_accepted_repository(ap.validate_repository(root, require_clean=True))


def _receipt_limit(value: str) -> int:
    family = value if value in {"terminal-quota", "terminal-continuation"} else Path(value).parent.name
    if family == "terminal-quota":
        from scripts.two_site_provider_broker import MAX_RECORD_BYTES
        return MAX_RECORD_BYTES
    if family == "terminal-continuation":
        from scripts.two_site_quota_continuation_terminal_record import MAX_TERMINAL_CONTINUATION_RECORD_BYTES
        return MAX_TERMINAL_CONTINUATION_RECORD_BYTES
    raise QuotaRetentionError("unknown bounded receipt family")


def _bounded_names(directory: Path, limit: int, label: str) -> list[str]:
    """Bound observation before allocating/traversing an excess entry."""
    names = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if len(names) == limit:
                raise QuotaRetentionError(label + " exceeds " + str(limit) + " entries")
            names.append(entry.name)
    return names


@contextmanager
def _current_repository_guard(root: Path):
    """Current custody is independent framing, never historical approval."""
    _root(root)
    with _Pins() as pins:
        expected = _pin_current_repository(root, pins)
        primary = None
        try:
            yield expected
        except BaseException as exc:
            primary = exc
            raise
        finally:
            try:
                pins.verify()
                if not _same(_pin_current_repository(root, pins), expected):
                    raise QuotaRetentionError("current repository drift during history read")
            except BaseException as custody:
                if primary is not None:
                    raise custody from primary
                raise


def read_final_ack_history_locked(root: Path, original: str, *,
                                  pipeline_dir: Path = _B / "pipeline") -> dict | None:
    """Pin current custody before any historical SDK reader, through all exits."""
    _root(root)
    directory = root / _A / "runs" / original
    _run_relative(original, "quota-wait.json")
    with _Pins() as discovery:
        discovery.directory(directory, private=True)
        names = set(_bounded_names(directory, 128, "historical run"))
        resume = {"quota-resume-claim.json", "quota-resume-lineage-snapshot.json",
                  "quota-resume-submission.json", _INTENT, _COMPLETE}
        if not names.intersection(resume):
            # No historical SDK is read for this observational wait-only result.
            return _read_final_ack_history_locked_unframed(root, original, pipeline_dir=pipeline_dir)
        with _current_repository_guard(root):
            return _read_final_ack_history_locked_unframed(root, original, pipeline_dir=pipeline_dir)


def _read_retired_history(root: Path, original: str) -> dict:
    _root(root)
    with _current_repository_guard(root):
        return _read_retired_history_unframed(root, original)


def _validate_maintenance_base(raw: bytes, assignment_id: str, maintenance_head: str) -> None:
    from scripts import two_site_assignment_scope as assignments
    from scripts import two_site_codex_autopilot as ap
    header, _ = assignments._parse_assignment_header(raw, assignment_id, ap.BRANCH)
    base = header.get("base_sha")
    if type(base) is not str or not re.fullmatch(r"[0-9a-f]{40}", base) or base != maintenance_head:
        raise QuotaRetentionError("Root Assignment base_sha differs from maintenance_head")


def _validate_actor_sources(root: Path, before: dict, original_task: dict, sources: object) -> None:
    if type(sources) is not dict or set(sources) != set(original_task["allowed_files"] + original_task.get("context_files", [])):
        raise QuotaRetentionError("actor Sources domain differs from Task")
    for path in original_task["allowed_files"] + original_task.get("context_files", []):
        expected = sources[path]
        if type(expected) is not dict or set(expected) != {"sha256", "content"}:
            raise QuotaRetentionError("actor Sources require the exact Runner snapshot schema")
        metadata = _git_query(root, "ls-tree", "--full-tree", before["head"], "--", path)
        if not metadata:
            _validate_historical_absence(original_task, path, expected)
            continue
        fields = metadata.split("\t")
        if (len(fields) != 2 or fields[1] != path or len(fields[0].split()) != 3
                or fields[0].split()[0] not in {"100644", "100755"}
                or fields[0].split()[1] != "blob"):
            raise QuotaRetentionError("historical source is not a regular exact Git blob")
        actual = _bounded_git_blob(root, before["head"], path)
        if (type(expected["content"]) is not str or type(expected["sha256"]) is not str
                or not _HEX.fullmatch(expected["sha256"])
                or expected["content"].encode("utf-8") != actual
                or expected["sha256"] != _sha(actual)):
            raise QuotaRetentionError("actor Sources differ from historical Git")


@contextmanager
def _held_sync_lock_pins(paths):
    """Only synchronization-lock timestamps may change after a losing open."""
    descriptors = {}
    primary = None
    def identity(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise QuotaRetentionError("held lock is not a trusted regular single-link 0600 inode")
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink)
    def check():
        for path, (fd, expected) in descriptors.items():
            if (path.resolve(strict=True) != path or identity(path.lstat()) != expected
                    or identity(os.fstat(fd)) != expected):
                raise QuotaRetentionError("held lock FD/path identity drift")
    try:
        for path in paths:
            before = identity(path.lstat())
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
            descriptors[path] = (fd, before)
            check()
        yield check
        check()
    except BaseException as exc:
        primary = exc
        raise
    finally:
        failure = None
        try:
            check()
        except BaseException as exc:
            failure = exc
        for fd, _ in descriptors.values():
            try: os.close(fd)
            except BaseException as exc: failure = failure or exc
        if failure is not None:
            if primary is not None: raise failure from primary
            raise failure


def _validate_historical_absence(original_task, path, source):
    if path in original_task.get("context_files", []):
        raise QuotaRetentionError("actor context source is historically absent")
    if path not in original_task["allowed_files"] or not _same(source, {"sha256": None, "content": None}):
        raise QuotaRetentionError("actor declares bytes for historically absent source")
