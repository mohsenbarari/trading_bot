"""Apply only exact, independently approved proposal bytes; never commit Git."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import two_site_cycle_packet as packet
from scripts import two_site_cycle_tools as ct
from scripts import two_site_proposal_runner as runner
from scripts import two_site_qoder_review as final_review


class ApplyReviewedError(ValueError):
    def __init__(self, code, applied=(), attempted=()):
        super().__init__(code)
        self.applied_paths = list(applied)
        self.attempted_paths = list(attempted)
        self.requires_reconciliation = bool(self.attempted_paths)


def _need(ok, code):
    if not ok:
        raise ApplyReviewedError(code)


def _parent(root, target):
    current = root
    for part in target.parent.relative_to(root).parts:
        current = current / part
        if not os.path.lexists(current):
            current.mkdir(mode=0o755)
        info = current.lstat()
        _need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
              and info.st_uid == os.geteuid() and not info.st_mode & 0o022, "APPLY_UNSAFE_PARENT")


def _observe(root, target, is_new):
    if is_new:
        _need(not os.path.lexists(target), "APPLY_NEW_TARGET_EXISTS")
        return None
    before = target.lstat()
    _need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
          and not before.st_mode & 0o022 and not before.st_mode & 0o7000, "APPLY_UNSAFE_SOURCE")
    raw = runner.bounded_file_bytes(root, target, runner.MAX_CONTEXT, "approved original source")
    after = target.lstat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
    _need(all(getattr(before, key) == getattr(after, key) for key in fields), "APPLY_SOURCE_CHANGED")
    return {"identity": tuple(getattr(after, key) for key in fields),
            "sha256": hashlib.sha256(raw).hexdigest(), "mode": stat.S_IMODE(after.st_mode)}


def _stage(target, raw, mode):
    temporary = target.parent / (".qoder-apply-" + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(descriptor, view); _need(count > 0, "APPLY_SHORT_WRITE"); view = view[count:]
        os.fchmod(descriptor, mode); os.fsync(descriptor)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)
    return temporary


def _publish(root, target, temporary, observation):
    _need(_observe(root, target, observation is None) == observation, "APPLY_SOURCE_COMPARE_FAILED")
    if observation is None:
        os.link(temporary, target, follow_symlinks=False)  # Atomic no-replace for a new file.
        temporary.unlink()
    else:
        os.replace(temporary, target)
    descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def apply_reviewed(root, task_path, worker, reviewer, sha):
    root = Path(root).resolve(strict=True); task_path = Path(task_path)
    staged = []; applied = []; attempted = []
    try:
        prepared = packet._prepare_proposal(root, task_path, worker, reviewer, sha)
        final_review.validate_review_receipt(root, task_path, worker, reviewer, sha,
            expected_source_head=prepared[0]["head"], require_approve=True)
        # Revalidate the original bytes immediately before any write.
        _need(packet._prepare_proposal(root, task_path, worker, reviewer, sha) == prepared,
              "APPLY_BINDINGS_CHANGED")
        targets = []
        for item in prepared[4]:
            target = runner.relative_file(root, item["path"])
            _parent(root, target)
            observation = _observe(root, target, item["is_new"])
            _need((observation is None and item["orig_sha"] is None)
                  or observation is not None and observation["sha256"] == item["orig_sha"],
                  "APPLY_ORIGINAL_SHA_MISMATCH")
            targets.append((item, target, observation))
        for item, target, observation in targets:
            temporary = _stage(target, item["new_content"].encode("utf-8"),
                               0o644 if observation is None else observation["mode"])
            staged.append((item, target, observation, temporary))
        for item, target, observation, temporary in staged:
            attempted.append(item["path"])
            _publish(root, target, temporary, observation)
            applied.append(item["path"])
        checked = ct.review(root, task_path if task_path.is_absolute() else root / task_path,
                            worker, reviewer, sha, applied=True)
        _need(checked["state"] == "BYTES_VALID", "APPLY_POSTCHECK_FAILED")
        return {"state": "APPLIED_BYTES_VERIFIED", "changed_paths": applied, "git_committed": False,
                "applied_review": "PASSED", "next_action": "stage_exact_paths_then_commit",
                "approval_receipt": str(final_review.REVIEW_REL / (sha + ".json")), "review": checked}
    except BaseException as error:
        code = str(error) if type(error) is ApplyReviewedError else type(error).__name__
        raise ApplyReviewedError(code, applied, attempted) from error
    finally:
        for _item, _target, _observation, temporary in staged:
            temporary.unlink(missing_ok=True)


def main():
    args = final_review.arguments(__doc__).parse_args()
    try:
        print(json.dumps(apply_reviewed(args.root, args.task, args.worker, args.reviewer, args.sha), sort_keys=True))
        return 0
    except ApplyReviewedError as error:
        print(json.dumps({"state": "APPLY_FAILED", "code": str(error), "applied_paths": error.applied_paths,
            "attempted_paths": error.attempted_paths, "requires_reconciliation": error.requires_reconciliation,
            "git_committed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
