"""Actual historical evidence custody for full v1.1 main/maintenance transitions.

Caller holds Supervisor and continuous idle Broker custody. This read-only gate
does not publish, dispatch, approve an owner, acquire locks or authorize Start.
Main adoption may reuse exact owner-approved main blobs, but every new resolution
must be in the genuine Worker proposal reviewed on the first parent's sources.
It is not a regular single-parent proposal commit; no ordinary merge waiver.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import re
import stat

from scripts.two_site_main_maintenance_record import parse_main_maintenance_record, validate_main_maintenance_record
from scripts.two_site_main_maintenance_git import verify_main_maintenance_git_chain
from scripts.two_site_maintenance_transition_git import _run_git_query
from scripts.two_site_proposal_pipeline import validate_run, load_verifier_pre_review_receipt
from scripts.two_site_proposal_runner import bounded_file_bytes, unique_json, serialize_json, validate_correction_feedback, relative_file
from scripts.two_site_provider_broker import _validate_claimed_request_shape, MAINTENANCE_RUN_RE

ERROR = "invalid main maintenance evidence"
MAX_FILES, MAX_BYTES = 512, 32000000
RUN = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,60})-worker-([0-2])(?:-retry-[12])?(?:-gf)?\Z")
HEX40 = re.compile(rb"[0-9a-f]{40}\Z")


class MainMaintenanceEvidenceError(ValueError):
    """Fixed ordinary failure; interrupted processes retain their BaseException."""


def _fingerprint(s):
    if (not stat.S_ISREG(s.st_mode) or stat.S_IMODE(s.st_mode) != 0o600
            or s.st_uid != os.geteuid() or s.st_nlink != 1):
        raise ValueError
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


class _Custody:
    def __init__(self, root):
        self.root, self.files, self.size = root, {}, 0

    def read(self, name, limit=160000):
        path = self.root / name
        before = _fingerprint(path.lstat())
        effective_limit = limit
        if name not in self.files:
            if len(self.files) >= MAX_FILES or self.size >= MAX_BYTES:
                raise ValueError
            effective_limit = min(limit, MAX_BYTES - self.size)
        raw = bounded_file_bytes(self.root, path, effective_limit, "historical evidence")
        if _fingerprint(path.lstat()) != before:
            raise ValueError
        bound = (limit, before, hashlib.sha256(raw).hexdigest())
        if name in self.files:
            if self.files[name] != bound:
                raise ValueError
        else:
            if len(self.files) >= MAX_FILES or self.size + len(raw) > MAX_BYTES:
                raise ValueError
            self.files[name] = bound
            self.size += len(raw)
        return raw

    def obj(self, name, limit=160000):
        value = unique_json(self.read(name, limit).decode("utf-8"))
        if type(value) is not dict:
            raise ValueError
        return value

    def recheck(self):
        for name, (limit, _, _) in list(self.files.items()):
            self.read(name, limit)


def _tree(root, head, paths):
    result = {}
    for start in range(0, len(paths), 32):
        batch = paths[start:start+32]
        raw = _run_git_query(root, ["ls-tree", "--full-tree", "-z", head, "--", *batch])
        if type(raw) is not bytes or len(raw) > 65536 or (raw and not raw.endswith(b"\0")):
            raise ValueError
        for entry in raw[:-1].split(b"\0") if raw else []:
            meta, name = entry.split(b"\t", 1)
            mode, kind, digest = meta.split(b" ")
            path = name.decode("utf-8")
            if (path not in batch or path in result or kind != b"blob"
                    or mode not in (b"100644", b"100755") or not HEX40.fullmatch(digest)):
                raise ValueError
            result[path] = (mode, digest)
    return result


def _blob(content):
    if type(content) is not str:
        raise ValueError
    raw = content.encode("utf-8")
    if len(raw) > 160000:
        raise ValueError
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest().encode("ascii")


def _sources(root, parent, sources):
    if type(sources) is not dict or not 1 <= len(sources) <= 64:
        raise ValueError
    for path in sources:
        if type(path) is not str or not 1 <= len(path) <= 1024 or any(c in path for c in ":*?[]{}"):
            raise ValueError
        relative_file(root, path)
    before = _tree(root, parent, sorted(sources))
    for path, source in sources.items():
        if type(source) is not dict or set(source) != {"sha256", "content"}:
            raise ValueError
        if source["content"] is None:
            if source["sha256"] is not None or path in before:
                raise ValueError
        else:
            digest = _blob(source["content"])
            if (hashlib.sha256(source["content"].encode("utf-8")).hexdigest() != source["sha256"]
                    or path not in before or before[path][1] != digest):
                raise ValueError
    return before


def _maintenance(custody, status, prefix, parent, task):
    """Original Broker dispatch pins Assignment identity; never reload today's READY scope.

    Historical Assignment documents may now be COMPLETE. Their dispatch digest
    comes from the actual immutable Broker record, not a reconstructed contract.
    This does not authenticate an arbitrary caller's current Assignment scope.
    """
    controller = status.get("controller_run_id")
    if type(controller) is not str or not MAINTENANCE_RUN_RE.fullmatch(controller):
        raise ValueError
    record = custody.obj(".local/two-site-refactor/provider-broker/maintenance-runs/" + controller + ".json", 4096)
    if set(record) != {"schema_version", "request"} or record["schema_version"] != "1.0":
        raise ValueError
    request = record["request"]
    _validate_claimed_request_shape(request, request.get("request_id"))
    if (request["controller_run_id"] != controller or request["run_prefix"] != prefix
            or request["source_head"] != parent):
        raise ValueError
    raw = custody.read(request["task_path"])
    if (hashlib.sha256(raw).hexdigest() != request["task_sha256"]
            or unique_json(raw.decode("utf-8")) != task
            or not re.search(r"(?<![A-Za-z0-9_-])" + re.escape(request["assignment_id"]) + r"(?![A-Za-z0-9_-])", task["goal"])):
        raise ValueError
    return controller


def _worker(custody, run_id, parent, task, sources, conversations, visiting, controller):
    match = RUN.fullmatch(run_id)
    if not match or run_id in visiting or len(visiting) >= 3:
        raise ValueError
    visiting.add(run_id)
    base = ".local/two-site-refactor/proposals/" + run_id + "/"
    snapshot = custody.obj(base + "task.json", 480000)
    status = custody.obj(base + "status.json")
    raw = custody.read(base + "result.json")
    if status.get("controller_run_id") != controller:
        raise ValueError
    if snapshot.get("source_head") != parent or snapshot.get("task") != task or snapshot.get("sources") != sources:
        raise ValueError
    prior, feedback_sha = None, None
    if "proposal" in snapshot:
        prior_id = status.get("input_worker_run_id")
        prior_match = RUN.fullmatch(prior_id) if type(prior_id) is str else None
        if not prior_match or prior_match[1] != match[1] or int(prior_match[2]) + 1 != int(match[2]):
            raise ValueError
        prior = _worker(custody, prior_id, parent, task, sources, conversations, visiting, controller)
        if snapshot["proposal"] != prior[2]:
            raise ValueError
        feedback_raw = custody.read(".local/two-site-refactor/pipeline/" + match[1] + "/feedback-correction-" + match[2] + ".json")
        feedback_sha = hashlib.sha256(feedback_raw).hexdigest()
        if unique_json(feedback_raw.decode("utf-8")) != snapshot.get("feedback"):
            raise ValueError
        validate_correction_feedback(feedback_raw.decode("utf-8"), prior[1])
    elif match[2] != "0":
        raise ValueError
    result, actual_status, digest, text = validate_run(
        custody.root, custody.root / base, "worker", run_id, parent, task, sources,
        conversations, prior_worker=prior, expected_feedback_sha=feedback_sha)
    if (text.encode("utf-8") != raw or actual_status != status or digest != hashlib.sha256(raw).hexdigest()
            or (prior and (digest == prior[1] or result["changes"] == unique_json(prior[2])["changes"]))):
        raise ValueError
    return (run_id, digest, text, status["conversation_id"])


def _entry(custody, entry, conversations):
    root, worker, reviewer = custody.root, entry["worker_run_id"], entry["reviewer_run_id"]
    main = "parents" in entry
    parent = entry["parents"][0] if main else entry["parent"]
    match = RUN.fullmatch(worker)
    if not match or not re.fullmatch(re.escape(match[1]) + r"-(?:reviewer|verifier)-" + match[2] + r"(?:-retry-[12])?", reviewer):
        raise ValueError
    snapshot = custody.obj(".local/two-site-refactor/proposals/" + worker + "/task.json", 480000)
    task, sources = snapshot["task"], snapshot["sources"]
    before = _sources(root, parent, sources)
    worker_status = custody.obj(".local/two-site-refactor/proposals/" + worker + "/status.json")
    controller = _maintenance(custody, worker_status, match[1], parent, task)
    chain = _worker(custody, worker, parent, task, sources, conversations, set(), controller)
    if chain[1] != entry["proposal_sha256"]:
        raise ValueError
    test_path = ".local/two-site-refactor/pipeline/" + match[1] + "/pre-review-tests-" + match[2] + ".json"
    raw = custody.read(test_path)
    if hashlib.sha256(raw).hexdigest() != entry["test_evidence_sha256"]:
        raise ValueError
    receipt = load_verifier_pre_review_receipt(root, {
        "original_run_prefix":match[1], "correction_count":int(match[2]), "pre_review_test_path":test_path,
        "proposal_sha256":entry["proposal_sha256"], "pre_review_test_sha256":entry["test_evidence_sha256"], "pre_review_test_state":"PASSED"})
    if receipt["selectors"] != task["test_selectors"] or serialize_json(receipt) != raw:
        raise ValueError
    base = ".local/two-site-refactor/proposals/" + reviewer + "/"
    review_snapshot, review_status = custody.obj(base + "task.json", 480000), custody.obj(base + "status.json")
    custody.read(base + "result.json")
    if review_snapshot.get("test_evidence") != receipt or review_status.get("controller_run_id") != controller:
        raise ValueError
    verification, actual_status, _, _ = validate_run(
        root, root / base, "verifier", reviewer, parent, task, sources, conversations,
        prior_worker=chain, expected_test_receipt=receipt, expected_test_evidence_sha=entry["test_evidence_sha256"])
    if verification["status"] != "VERIFIED" or verification["findings"] or actual_status != review_status:
        raise ValueError
    proposal = unique_json(chain[2])
    changes = {c["path"]:c for c in proposal["changes"]}
    paths = set(entry["paths"])
    if (not main and set(changes) != paths) or (main and not set(changes) <= paths):
        raise ValueError
    after = _tree(root, entry["head"], sorted(paths))
    if set(after) != paths:
        raise ValueError
    for path, change in changes.items():
        if (after[path][1] != _blob(change["content"])
                or after[path][0] != (before[path][0] if path in before else b"100644")
                or change["original_sha256"] != sources[path]["sha256"]):
            raise ValueError
    if main:
        reused = sorted(paths - set(changes))
        approved = _tree(root, entry["parents"][1], reused)
        if any(path not in approved or after[path] != approved[path] for path in reused):
            raise ValueError


def verify_main_maintenance_evidence_locked(root: Path, record: object, **bindings) -> None:
    """Prove the whole real graph and all accepted artifact bytes twice, without writes.

    Owner bindings must be the actual owner reader's result. This gate does not
    authenticate a caller-supplied owner object; upper storage must acquire it.
    Original sources are checked by Git blob identity, not unbounded cat-file.
    Up to 512 private artifacts / 32MB total and three successful correction
    ancestors per entry. No failed dispatch replay or outcome inferred from time.
    """
    try:
        normalized = (parse_main_maintenance_record(record, **bindings) if type(record) is bytes
                      else validate_main_maintenance_record(record, **bindings))
        bindings = copy.deepcopy(bindings)
        custody = _Custody(root)
        verify_main_maintenance_git_chain(root, normalized, **bindings)
        conversations = set()
        for entry in normalized["pre_main_commits"] + [normalized["main_integration"]] + normalized["post_main_commits"]:
            _entry(custody, entry, conversations)
        custody.recheck()
        verify_main_maintenance_git_chain(root, normalized, **bindings)
        custody.recheck()
    except Exception:
        raise MainMaintenanceEvidenceError(ERROR) from None
