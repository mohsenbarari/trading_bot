"""CODEX private SAME-owner Git snapshot; no acceptance/write authority."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import time

from scripts.two_site_accepted_work_evidence import (
    _EvidenceFilesError, _ERRORS as _FILE_ERRORS, _evidence_files, _aux_source_path, _copy_fixed_notes,
)
from scripts.two_site_agent_sandbox import SandboxError, _validate_overlay_path

_REFS = ("refs/heads/main", "refs/remotes/origin/main",
         "refs/heads/candidate/wa-ir-standby-v1")
_ENV = {
    "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "GIT_NO_LAZY_FETCH": "1",
    "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
    "GIT_GRAFT_FILE": "/dev/null",
}
_SHA = re.compile(r"[0-9a-f]{40}", re.ASCII)
_MAX_GIT_OBJECTS = 256
_MAX_GIT_COMMANDS = 512
_QUERY_BYTES = 2_000_000
_CHILD_SECONDS = 30
_CLEANUP_SECONDS = 2
_CHUNK = 65_536
_ZERO = "0" * 40
_DIFF = ("diff-tree", "--no-commit-id", "--raw", "-z", "--no-renames",
         "--no-ext-diff", "--no-textconv", "--no-abbrev", "-r")
_ERRORS = _FILE_ERRORS | {
    "GIT_FAILURE", "UNSUPPORTED_SOURCE", "UNSUPPORTED_COMMIT", "SNAPSHOT_DRIFT",
}
_RECOVERABLE = {"INVALID_KEY", "UNSAFE_PATH", "UNSUPPORTED_SOURCE",
                "UNSUPPORTED_COMMIT", "MISSING_FILE"}


class _EvidenceSnapshotError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and code in _ERRORS else "GIT_FAILURE"
        super().__init__(self.code)


def _fail(code):
    raise _EvidenceSnapshotError(code) from None


def _translated(error, fallback=None):
    pending = _EvidenceSnapshotError(fallback or getattr(error, "code", "GIT_FAILURE"))
    _copy_fixed_notes(error, pending, _ERRORS)
    return pending


def _source_path(value):
    _aux_source_path(value)
    if value.split("/")[0] in {".agents", ".codex", ".cursor", ".aws", ".ssh", ".gnupg", ".config"}:
        _fail("UNSUPPORTED_SOURCE")
    try:
        _validate_overlay_path(value)
    except SandboxError:
        _fail("UNSUPPORTED_SOURCE")
    return value


class _ProofSnapshot:
    def __new__(cls, *args, **kwargs):
        _fail("CLOSED_SNAPSHOT")

    def _initialize(self, root):
        if hasattr(self, "_state"):
            _fail("CLOSED_SNAPSHOT")
        self._state = "NEW"
        self._files = _evidence_files(root)
        self._commands = 0
        self._objects = {}
        self._lookups = {}
        self._commits = {}
        self._changes = {}
        self._ancestors = {}
        self._root_dirs = ()
        self._failure = None

    def _live(self):
        if self._state != "LIVE":
            _fail("CLOSED_SNAPSHOT")
        try:
            for path in self._root_dirs:
                self._files._verify_dir(path)
            self._files._verify_aux()
        except _EvidenceFilesError as error:
            if error.code == "CUSTODY_DRIFT":
                _fail("SNAPSHOT_DRIFT")
            raise
        self._files._verify()

    def _invoke(self, operation, *args):
        try:
            self._live()
            result = operation(*args)
            self._files._check_time()
            return result
        except (_EvidenceFilesError, _EvidenceSnapshotError) as error:
            pending = _translated(error)
            if pending.code not in _RECOVERABLE and self._state != "CLOSED":
                self._poison(pending)
            raise pending from None
        except (OSError, ValueError, UnicodeError, OverflowError, subprocess.SubprocessError) as error:
            pending = _translated(error, "GIT_FAILURE")
            self._poison(pending)
            raise pending from None

    def _poison(self, pending):
        if self._failure is None:
            self._failure = (pending.code, tuple(getattr(pending, "__notes__", ())))
        self._state = "FAILED"

    def __enter__(self):
        if self._state != "NEW":
            _fail("CLOSED_SNAPSHOT")
        try:
            self._files.__enter__()
            self._state = "LIVE"
            self._root = self._files._root
            self._root_dirs = tuple(self._files._dirs)
            self._pin_repository()
            self._identity = self._identity_queries()
            self._head = self._identity[0]
            self._refs = dict(zip(_REFS, self._identity[1:]))
            self._live()
            return self
        except BaseException as original:
            pending = _translated(original)
            self._close(pending)
            raise pending from None

    def __exit__(self, exc_type, original, traceback):
        pending = None
        if original is None:
            if self._failure is not None:
                code, notes = self._failure
                pending = _EvidenceSnapshotError(code)
                for note in notes:
                    pending.add_note(note)
            else:
                try:
                    self._verify()
                except BaseException as error:
                    pending = error
        pending = self._close(original if original is not None else pending)
        if original is not None:
            return False
        if pending is not None:
            raise pending from None
        return False

    def _close(self, original):
        pending = original
        try:
            if self._files._state != "CLOSED":
                self._files._close()
        except _EvidenceFilesError as cleanup:
            if pending is None:
                pending = _EvidenceSnapshotError(cleanup.code)
            else:
                pending.add_note(cleanup.code)
        self._objects.clear()
        self._lookups.clear()
        self._commits.clear()
        self._changes.clear()
        self._ancestors.clear()
        self._root_dirs = ()
        self._state = "CLOSED"
        return pending

    def _sha(self, value):
        if type(value) is not str or _SHA.fullmatch(value) is None:
            _fail("INVALID_KEY")
        self._files._charge_metadata(("sha", value), 40)
        return value

    def _pin_repository(self):
        dotgit = self._root / ".git"
        info = os.stat(dotgit, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            gitdir = dotgit
            self._files._pin_aux_directory(gitdir)
        elif stat.S_ISREG(info.st_mode):
            data = self._files._pin_aux_file(dotgit)
            try:
                line = data.decode("utf-8", errors="strict")
                if not line.startswith("gitdir: ") or not line.endswith("\n") or line.count("\n") != 1:
                    _fail("GIT_FAILURE")
                target = line[8:-1]
                gitdir = Path(os.path.abspath(dotgit.parent / target))
            except (UnicodeError, ValueError):
                _fail("GIT_FAILURE")
            if not any(gitdir.is_relative_to(parent / ".git") for parent in self._root.parents):
                _fail("GIT_FAILURE")
            self._files._pin_aux_directory(gitdir)
        else:
            _fail("GIT_FAILURE")
        common = self._files._pin_aux_file(gitdir / "commondir", optional=True)
        commondir = gitdir
        if common is not None:
            text = common.decode("utf-8", errors="strict")
            if not text.endswith("\n") or text.count("\n") != 1 or not text[:-1]:
                _fail("GIT_FAILURE")
            commondir = Path(os.path.abspath(gitdir / text[:-1]))
            if not gitdir.is_relative_to(commondir) or commondir.name != ".git":
                _fail("GIT_FAILURE")
            self._files._pin_aux_directory(commondir)
            back = self._files._pin_aux_file(gitdir / "gitdir")
            if back != (str(dotgit) + "\n").encode("utf-8"):
                _fail("GIT_FAILURE")
        head = self._files._pin_aux_file(gitdir / "HEAD")
        if head.startswith(b"ref: "):
            ref = head[5:].decode("ascii", errors="strict").removesuffix("\n")
            try:
                _aux_source_path(ref)
            except _EvidenceFilesError:
                _fail("GIT_FAILURE")
            if not ref.startswith("refs/heads/") or " " in ref:
                _fail("GIT_FAILURE")
            self._files._pin_aux_file(commondir / ref, optional=True)
        for name in ("config", "packed-refs") + _REFS:
            self._files._pin_aux_file(commondir / name, optional=True)
        self._files._pin_aux_file(gitdir / "config.worktree", optional=True)
        if self._files._pin_aux_file(commondir / "objects/info/alternates", True) is not None:
            _fail("GIT_FAILURE")

    def _argv(self, args):
        if type(args) is not tuple or any(type(item) is not str for item in args):
            _fail("INVALID_KEY")
        valid = args in (("rev-parse", "--show-toplevel"),
                         ("rev-parse", "--show-object-format"))
        if len(args) == 3:
            valid |= args[:2] == ("rev-parse", "--verify") and args[2] in ("HEAD",) + _REFS
            valid |= args[:2] in (("cat-file", "-t"), ("cat-file", "-s"),
                                 ("cat-file", "commit"), ("cat-file", "blob")) and bool(_SHA.fullmatch(args[2]))
        if len(args) == 5:
            valid |= (args[:2] == ("ls-tree", "-z") and bool(_SHA.fullmatch(args[2]))
                      and args[3] == "--" and _source_path(args[4]) == args[4])
        if len(args) == 4:
            valid |= (args[:2] == ("merge-base", "--is-ancestor")
                      and all(_SHA.fullmatch(item) for item in args[2:]))
        if len(args) == 11:
            valid |= (args[:9] == _DIFF
                      and all(_SHA.fullmatch(item) for item in args[9:]))
        if not valid:
            _fail("INVALID_KEY")
        return ["/usr/bin/git", "--no-pager", "--no-replace-objects", "--literal-pathspecs",
                "-C", str(self._root), "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                "-c", "core.pager=cat", "-c", "protocol.allow=never",
                "-c", "advice.graftFileDeprecated=false", *args]

    def _capture(self, operation_args, allowed_returns=(0,)):
        return self._invoke(self._capture_query, operation_args, allowed_returns)

    def _capture_query(self, operation_args, allowed_returns):
        argv = self._argv(operation_args)
        if allowed_returns != ((0, 1) if operation_args[:2] == ("merge-base", "--is-ancestor") else (0,)):
            _fail("INVALID_KEY")
        self._live()
        if self._commands >= _MAX_GIT_COMMANDS:
            _fail("COUNT_LIMIT")
        self._commands += 1
        deadline = min(self._files._deadline, time.monotonic() + _CHILD_SECONDS)
        work_deadline = deadline - _CLEANUP_SECONDS
        if time.monotonic() >= work_deadline:
            _fail("TIME_LIMIT")
        process, poller, pipes, parts, consumed = None, None, {}, {}, 0
        original, answer = None, None
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=dict(_ENV), close_fds=True, start_new_session=True,
                shell=False)
            poller = select.poll()
            for stream in (process.stdout, process.stderr):
                descriptor = stream.fileno()
                os.set_blocking(descriptor, False)
                pipes[descriptor] = stream
                parts[descriptor] = []
                poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
            out_fd, err_fd = process.stdout.fileno(), process.stderr.fileno()
            while pipes:
                self._files._check_time()
                left = work_deadline - time.monotonic()
                if left <= 0:
                    _fail("TIME_LIMIT")
                events = poller.poll(max(1, min(50, int(left * 1000))))
                for descriptor, event in events:
                    if descriptor not in pipes:
                        continue
                    if event & (select.POLLERR | select.POLLNVAL):
                        _fail("GIT_FAILURE")
                    remaining = min(_QUERY_BYTES - consumed, self._files._remaining_bytes())
                    if remaining == 0:
                        if event & select.POLLIN:
                            _fail("BYTE_LIMIT")
                        if event & select.POLLHUP:
                            poller.unregister(descriptor)
                            del pipes[descriptor]
                        continue
                    try:
                        chunk = os.read(descriptor, min(_CHUNK, remaining))
                    except BlockingIOError:
                        continue
                    self._files._charge_bytes(len(chunk))
                    consumed += len(chunk)
                    if chunk:
                        parts[descriptor].append(chunk)
                    else:
                        poller.unregister(descriptor)
                        del pipes[descriptor]
            while True:
                self._files._check_time()
                if time.monotonic() >= work_deadline:
                    _fail("TIME_LIMIT")
                result = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                if result is not None:
                    if result.si_code != os.CLD_EXITED:
                        _fail("GIT_FAILURE")
                    stdout, stderr = b"".join(parts[out_fd]), b"".join(parts[err_fd])
                    if result.si_status not in allowed_returns or stderr:
                        _fail("GIT_FAILURE")
                    answer = (result.si_status, stdout)
                    break
                time.sleep(min(0.005, max(0, work_deadline - time.monotonic())))
        except BaseException as error:
            original = error
        finally:
            cleanup = None
            if process is not None:
                # Keep leader unreaped until both killpg decisions are complete.
                for action in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(process.pid, action)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        cleanup = "GIT_FAILURE"
                    if action == signal.SIGTERM:
                        time.sleep(min(0.02, max(0, deadline - time.monotonic())))
                safety_deadline = time.monotonic() + _CLEANUP_SECONDS
                for attempt in range(2):
                    remaining = safety_deadline - time.monotonic()
                    if remaining <= 0:
                        cleanup = "GIT_FAILURE"
                        break
                    try:
                        process.wait(timeout=remaining)
                        break
                    except (OSError, subprocess.SubprocessError):
                        cleanup = "GIT_FAILURE"
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except OSError:
                            cleanup = cleanup or "GIT_FAILURE"
                if time.monotonic() >= deadline:
                    if original is None:
                        original = _EvidenceSnapshotError("TIME_LIMIT")
                    elif getattr(original, "code", None) != "TIME_LIMIT":
                        original.add_note("TIME_LIMIT")
            if cleanup:
                if original is None:
                    original = _EvidenceSnapshotError(cleanup)
                else:
                    original.add_note(cleanup)
        if original is not None:
            raise original from None
        return answer

    def _line(self, args):
        code, data = self._capture(args)
        if code != 0 or not data.endswith(b"\n") or data.count(b"\n") != 1:
            _fail("GIT_FAILURE")
        return data[:-1].decode("utf-8", errors="strict")

    def _identity_queries(self):
        if self._line(("rev-parse", "--show-toplevel")) != str(self._root):
            _fail("SNAPSHOT_DRIFT" if hasattr(self, "_identity") else "GIT_FAILURE")
        if self._line(("rev-parse", "--show-object-format")) != "sha1":
            _fail("GIT_FAILURE")
        values = tuple(self._line(("rev-parse", "--verify", ref)) for ref in ("HEAD",) + _REFS)
        if any(_SHA.fullmatch(value) is None for value in values):
            _fail("GIT_FAILURE")
        for value in values:
            self._sha(value)
        return values

    def _object(self, oid, kind):
        oid = self._sha(oid)
        self._live()
        if oid not in self._objects:
            if len(self._objects) >= _MAX_GIT_OBJECTS:
                _fail("COUNT_LIMIT")
            if self._line(("cat-file", "-t", oid)) != kind:
                _fail("GIT_FAILURE")
            size_text = self._line(("cat-file", "-s", oid))
            if not re.fullmatch(r"0|[1-9][0-9]*", size_text, re.ASCII) or len(size_text) > 10:
                _fail("GIT_FAILURE")
            size = int(size_text)
            if size > _QUERY_BYTES:
                _fail("BYTE_LIMIT")
            code, body = self._capture(("cat-file", kind, oid))
            if code != 0 or len(body) != size or hashlib.sha1(
                    kind.encode("ascii") + b" " + str(size).encode("ascii") + b"\0" + body).hexdigest() != oid:
                _fail("GIT_FAILURE")
            self._objects[oid] = {"type": kind, "data": body}
        record = self._objects[oid]
        if record["type"] != kind:
            _fail("GIT_FAILURE")
        return record["data"]

    def _commit(self, sha):
        sha = self._sha(sha)
        if sha not in self._commits:
            body = self._object(sha, "commit")
            headers, separator, _ = body.partition(b"\n\n")
            lines = headers.split(b"\n")
            if not separator or not lines or not lines[0].startswith(b"tree "):
                _fail("GIT_FAILURE")
            tree = lines[0][5:].decode("ascii", errors="strict")
            if _SHA.fullmatch(tree) is None:
                _fail("GIT_FAILURE")
            self._sha(tree)
            parents, finished = [], False
            for line in lines[1:]:
                if line.startswith(b"parent "):
                    parent = line[7:].decode("ascii", errors="strict")
                    if finished or _SHA.fullmatch(parent) is None or len(parents) >= 32:
                        _fail("GIT_FAILURE")
                    parents.append(self._sha(parent))
                else:
                    finished = True
                    if not line or (not line.startswith(b" ") and b" " not in line):
                        _fail("GIT_FAILURE")
            self._commits[sha] = tuple(parents)
        return {"sha": sha, "parents": tuple(self._commits[sha])}

    def _blob(self, commit, path):
        commit, path = self._sha(commit), _source_path(path)
        self._files._charge_metadata(("git-path", commit, path), 40 + len(path.encode("utf-8")))
        key = commit, path
        if key not in self._lookups:
            self._commit(commit)
            code, data = self._capture(("ls-tree", "-z", commit, "--", path))
            if code != 0:
                _fail("GIT_FAILURE")
            if not data:
                self._lookups[key] = None
            else:
                if not data.endswith(b"\0") or data.count(b"\0") != 1:
                    _fail("GIT_FAILURE")
                prefix, separator, actual = data[:-1].partition(b"\t")
                fields = prefix.split(b" ")
                if not separator or len(fields) != 3 or actual != path.encode("utf-8"):
                    _fail("GIT_FAILURE")
                mode, kind, oid = (value.decode("ascii", errors="strict") for value in fields)
                if _SHA.fullmatch(oid) is None:
                    _fail("GIT_FAILURE")
                if mode not in ("100644", "100755") or kind != "blob":
                    _fail("UNSUPPORTED_SOURCE")
                body = self._object(oid, "blob")
                self._lookups[key] = (mode, oid, body)
        record = self._lookups[key]
        if record is None:
            return None
        mode, oid, body = record
        return {"commit": commit, "path": path, "mode": mode, "git_oid": oid,
                "sha256": hashlib.sha256(body).hexdigest(), "data": body}

    def _changed(self, commit):
        commit = self._sha(commit)
        if commit not in self._changes:
            parents = self._commit(commit)["parents"]
            if len(parents) != 1:
                _fail("UNSUPPORTED_COMMIT")
            args = (*_DIFF, parents[0], commit)
            code, body = self._capture(args)
            if code != 0 or (body and not body.endswith(b"\0")):
                _fail("GIT_FAILURE")
            cells = body[:-1].split(b"\0") if body else []
            if len(cells) % 2:
                _fail("GIT_FAILURE")
            rows, paths = [], set()
            for index in range(0, len(cells), 2):
                fields = cells[index].decode("ascii", errors="strict").split(" ")
                if len(fields) != 5 or not fields[0].startswith(":") or fields[4] not in ("A", "M", "D", "T"):
                    _fail("GIT_FAILURE")
                old_mode, new_mode, old_oid, new_oid, status = fields
                old_mode = old_mode[1:]
                if not all(_SHA.fullmatch(value) for value in (old_oid, new_oid)):
                    _fail("GIT_FAILURE")
                valid = {"A": (old_mode == "000000" and old_oid == _ZERO and
                                new_mode in ("100644", "100755") and new_oid != _ZERO),
                         "D": (new_mode == "000000" and new_oid == _ZERO and
                                old_mode in ("100644", "100755") and old_oid != _ZERO),
                         "M": (old_mode in ("100644", "100755") and new_mode in ("100644", "100755")
                                and old_oid != _ZERO and new_oid != _ZERO)}.get(status, False)
                if not valid:
                    if status == "T" or old_mode not in ("000000", "100644", "100755") or new_mode not in ("000000", "100644", "100755"):
                        _fail("UNSUPPORTED_SOURCE")
                    _fail("GIT_FAILURE")
                path = _source_path(cells[index + 1].decode("utf-8", errors="strict"))
                if path in paths:
                    _fail("GIT_FAILURE")
                paths.add(path)
                self._files._charge_metadata(("git-path", commit, path), 40 + len(path.encode("utf-8")))
                for value in (old_oid, new_oid):
                    if value != _ZERO:
                        self._sha(value)
                rows.append({"path": path, "old_mode": old_mode, "new_mode": new_mode,
                             "old_oid": old_oid, "new_oid": new_oid})
            self._changes[commit] = tuple(sorted(rows, key=lambda item: item["path"]))
        return tuple(dict(row) for row in self._changes[commit])

    def _ancestor(self, older, newer):
        key = self._sha(older), self._sha(newer)
        if key not in self._ancestors:
            self._commit(key[0])
            self._commit(key[1])
            code, data = self._capture(("merge-base", "--is-ancestor", *key), (0, 1))
            if code not in (0, 1) or data:
                _fail("GIT_FAILURE")
            self._ancestors[key] = code == 0
        return self._ancestors[key]

    def _read(self, kind, key=None):
        return self._invoke(self._files._read, kind, key)

    def _json_list(self, kind):
        return self._invoke(self._files._json_list, kind)

    def _git_commit(self, sha):
        return self._invoke(self._commit, sha)

    def _git_blob(self, commit, path):
        return self._invoke(self._blob, commit, path)

    def _git_changes(self, commit):
        return self._invoke(self._changed, commit)

    def _is_ancestor(self, older, newer):
        return self._invoke(self._ancestor, older, newer)

    def _metadata(self):
        def result():
            base = self._files._metadata()
            return {"schema_version": 1, "head": self._head, "protected_refs": dict(self._refs),
                    "files": base["files"], "git_objects": [
                        {"git_oid": oid, "sha256": hashlib.sha256(record["data"]).hexdigest(),
                         "size": len(record["data"])} for oid, record in sorted(self._objects.items())],
                    "bytes_read": base["bytes_read"]}
        return self._invoke(result)

    def _verify(self):
        def verify():
            if self._identity_queries() != self._identity:
                _fail("SNAPSHOT_DRIFT")
            self._live()
        return self._invoke(verify)


def _proof_snapshot(root):
    result = object.__new__(_ProofSnapshot)
    result._initialize(root)
    return result
