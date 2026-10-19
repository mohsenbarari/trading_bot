"""CODEX-authored private evidence FILE custody; no acceptance or write authority.

738L continues the unimplemented735H dependency, not its Provider execution.
Future Git/proof/cost phases must compose this SAME live invocation and budget.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

_TOTAL_BYTES = 8_000_000
_MAX_OBSERVATIONS = 128
_MAX_METADATA = 65_536
_SECONDS = 120
_CHUNK = 65_536
_MAX_DEPTH = 32
_MAX_RECORDS = 2_000
_LIMITS = {
    "ledger": 2_000_000, "metrics": 2_000_000, "receipt": 2_000_000,
    "proposal_snapshot": 480_000, "task": 160_000, "worker_result": 160_000,
    "native": 160_000, "reviewer_result": 64_000, "proposal_status": 64_000,
    "root_result": 64_000, "root_status": 64_000,
}
# Valid N-node JSON needs at least2N-1 wire bytes.
_METRICS_MAX_NODES = _LIMITS["metrics"] // 2 + 1
_ERRORS = frozenset({
    "INVALID_ROOT", "INVALID_KEY", "UNSAFE_PATH", "UNSAFE_FILE", "MISSING_FILE",
    "CUSTODY_DRIFT", "BYTE_LIMIT", "COUNT_LIMIT", "TIME_LIMIT", "JSON_INVALID",
    "CLOSED_SNAPSHOT",
})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)
_RECEIPT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,249}\.json", re.ASCII)


class _EvidenceFilesError(ValueError):
    """Only a fixed enum is carried; original body exceptions are not redacted."""

    def __init__(self, code):
        self.code = code if type(code) is str and code in _ERRORS else "UNSAFE_FILE"
        super().__init__(self.code)


def _fail(code):
    raise _EvidenceFilesError(code) from None


def _copy_fixed_notes(source, target, allowed):
    for note in getattr(source, "__notes__", ()):
        if type(note) is str and note in allowed:
            target.add_note(note)


def _file_mark(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _dir_mark(info):
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
            stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)


def _safe_id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail("INVALID_KEY")
    return value


def _aux_source_path(value):
    if type(value) is not str:
        _fail("INVALID_KEY")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        _fail("UNSAFE_PATH")
    parts = value.split("/")
    if (not encoded or len(encoded) > 1024 or value.startswith("/")
            or any(not p or p in (".", "..") or p.startswith("-")
                   or p.lower() == ".git" for p in parts)
            or any(c in value for c in "\\\0:*?[]{}")
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        _fail("UNSAFE_PATH")
    return value


def _artifact(kind, key):
    if type(kind) is not str or kind not in _LIMITS:
        _fail("INVALID_KEY")
    if kind in ("ledger", "metrics"):
        if key is not None:
            _fail("INVALID_KEY")
        path = ("docs/refactor/two-server/section-1/00-stage-ledger.md"
                if kind == "ledger" else ".local/two-site-refactor/autopilot/metrics.json")
    elif kind == "receipt":
        if type(key) is not str or _RECEIPT.fullmatch(key) is None:
            _fail("INVALID_KEY")
        path = "docs/refactor/two-server/section-1/inventory/" + key
    elif kind == "native":
        if (type(key) is not tuple or len(key) != 2 or type(key[1]) is not int
                or not 0 <= key[1] <= 1999):
            _fail("INVALID_KEY")
        path = ".local/two-site-refactor/pipeline/" + _safe_id(key[0])
        path += "/pre-review-tests-" + str(key[1]) + ".json"
    elif kind in ("root_result", "root_status"):
        if (type(key) is not tuple or len(key) != 2 or type(key[0]) is not str
                or key[0] not in ("runs", "flat")):
            _fail("INVALID_KEY")
        identifier = _safe_id(key[1])
        path = ".local/two-site-refactor/autopilot/"
        path += ("runs/" if key[0] == "runs" else "") + identifier
        path += "/result.json" if kind == "root_result" else "/status.json"
    else:
        identifier = _safe_id(key)
        if kind == "task":
            path = ".local/two-site-refactor/tasks/" + identifier + ".json"
        else:
            leaf = {"proposal_snapshot": "task.json", "proposal_status": "status.json",
                    "worker_result": "result.json", "reviewer_result": "result.json"}[kind]
            path = ".local/two-site-refactor/proposals/" + identifier + "/" + leaf
    parts = path.split("/")
    if (path.startswith("/") or any(not part or part in (".", "..", ".git")
            or part.startswith("-") for part in parts)
            or any(char in path for char in "\\\0:*?[]{}")
            or any(ord(char) < 32 or ord(char) == 127 for char in path)):
        _fail("UNSAFE_PATH")
    return path, _LIMITS[kind]


class _EvidenceFiles:
    """Factory-owned context; private Python internals are not a security sandbox."""

    def __new__(cls, *args, **kwargs):
        _fail("CLOSED_SNAPSHOT")

    def _initialize(self, root):
        if hasattr(self, "_state"):
            _fail("CLOSED_SNAPSHOT")
        self._root = root
        self._state = "NEW"
        self._deadline = time.monotonic() + _SECONDS
        self._handles = set()
        self._dirs = {}
        self._records = {}
        self._aux_records = {}
        self._aux_dirs = set()
        self._seen = set()
        self._metadata_keys = set()
        self._bytes_read = 0
        self._metadata_bytes = 0

    def _check_time(self):
        if time.monotonic() >= self._deadline:
            _fail("TIME_LIMIT")

    def _ensure_live(self):
        if self._state != "LIVE":
            _fail("CLOSED_SNAPSHOT")
        self._check_time()

    def __enter__(self):
        if self._state != "NEW":
            _fail("CLOSED_SNAPSHOT")
        self._state = "LIVE"
        try:
            self._check_time()
            root = self._root
            if (type(root) is not type(Path()) or not root.is_absolute()
                    or root != Path(os.path.abspath(root)) or root.parent == root
                    or root.resolve(strict=True) != root or not root.is_dir()):
                _fail("INVALID_ROOT")
            anchor = Path(root.anchor)
            self._open_dir(anchor, None, None, False)
            current = anchor
            for component in root.parts[1:]:
                self._check_time()
                parent = current
                current = current / component
                self._open_dir(current, parent, component, current == root)
            self._verify()
            return self
        except (OSError, RuntimeError):
            error = _EvidenceFilesError("INVALID_ROOT")
            self._finish_failed_enter(error)
            raise error from None
        except BaseException as error:
            self._finish_failed_enter(error)
            raise

    def _finish_failed_enter(self, original):
        try:
            self._close()
        except _EvidenceFilesError as cleanup:
            original.add_note(cleanup.code)

    def __exit__(self, exc_type, original, traceback):
        pending = None
        if original is None:
            try:
                self._verify()
            except BaseException as error:
                pending = error
        try:
            self._close()
        except _EvidenceFilesError as cleanup:
            if original is not None:
                original.add_note(cleanup.code)
            elif pending is not None:
                pending.add_note(cleanup.code)
            else:
                pending = cleanup
        if original is not None:
            return False
        if pending is not None:
            raise pending from None
        return False

    def _drop_handle(self, descriptor):
        self._handles.discard(descriptor)
        try:
            os.close(descriptor)
        except OSError:
            _fail("UNSAFE_FILE")

    def _release_unretained(self, descriptor):
        original = sys.exception()
        try:
            self._drop_handle(descriptor)
        except _EvidenceFilesError as cleanup:
            if original is None:
                raise
            original.add_note(cleanup.code)

    def _open_dir(self, absolute, parent, name, secure=True):
        self._check_time()
        if absolute in self._dirs:
            self._verify_dir(absolute)
            return self._dirs[absolute]["fd"]
        descriptor = None
        retained = False
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            descriptor = (os.open(absolute, flags) if parent is None
                          else os.open(name, flags, dir_fd=self._dirs[parent]["fd"]))
            self._handles.add(descriptor)
            info = os.fstat(descriptor)
            if (not stat.S_ISDIR(info.st_mode) or (secure and
                    (info.st_uid != os.geteuid() or info.st_mode & 0o022))):
                _fail("UNSAFE_PATH")
            record = {"fd": descriptor, "mark": _dir_mark(info), "parent": parent, "name": name}
            self._dirs[absolute] = record
            retained = True
            self._verify_dir(absolute)
            return descriptor
        except OSError:
            _fail("UNSAFE_PATH")
        finally:
            if descriptor is not None and not retained:
                self._release_unretained(descriptor)

    def _verify_dir(self, absolute):
        self._check_time()
        record = self._dirs[absolute]
        try:
            actual = os.fstat(record["fd"])
            named = (os.stat(absolute, follow_symlinks=False) if record["parent"] is None
                     else os.stat(record["name"], dir_fd=self._dirs[record["parent"]]["fd"],
                                  follow_symlinks=False))
            if _dir_mark(actual) != record["mark"] or _dir_mark(named) != record["mark"]:
                _fail("CUSTODY_DRIFT")
        except OSError:
            _fail("CUSTODY_DRIFT")

    def _parent(self, path):
        current = self._root
        for component in path.split("/")[:-1]:
            parent = current
            current = current / component
            self._open_dir(current, parent, component)
        return current

    def _root_parent(self, path):
        # Root families alone allow a guarded absent descendant of autopilot.
        parts = path.split("/")
        current = self._parent("/".join(parts[:3]) + "/anchor")
        for component in parts[3:-1]:
            parent = current
            current = current / component
            try:
                os.stat(component, dir_fd=self._dirs[parent]["fd"], follow_symlinks=False)
            except FileNotFoundError:
                self._records[path] = {"fd": None, "mark": None, "data": None,
                    "missing": True, "parent": parent, "name": component}
                self._verify()
                _fail("MISSING_FILE")
            except OSError:
                _fail("UNSAFE_PATH")
            self._open_dir(current, parent, component)
        return current

    def _pin_aux_directory(self, path):
        # Trusted wrapper only: named repository chain, never an artifact API.
        current = Path(path.anchor)
        for component in path.parts[1:]:
            parent = current
            current = current / component
            if current not in self._dirs:
                self._aux_dirs.add(current)
            self._open_dir(current, parent, component)

    def _pin_aux_file(self, path, optional=False):
        if path in self._aux_records:
            record = self._aux_records[path]
            self._verify_record(record)
            return record["data"]
        current = Path(path.anchor)
        for component in path.parts[1:-1]:
            parent = current
            current = current / component
            try:
                os.stat(component, dir_fd=self._dirs[parent]["fd"], follow_symlinks=False)
            except FileNotFoundError:
                if not optional:
                    _fail("UNSAFE_PATH")
                path, current = current, parent
                break
            if current not in self._dirs:
                self._aux_dirs.add(current)
            self._open_dir(current, parent, component)
        self._charge_metadata(("repo", str(path)), len(str(path).encode("utf-8")))
        descriptor, retained = None, False
        try:
            try:
                descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC |
                                     os.O_NONBLOCK, dir_fd=self._dirs[current]["fd"])
            except FileNotFoundError:
                if not optional:
                    _fail("UNSAFE_FILE")
                record = {"fd": None, "mark": None, "data": None, "missing": True,
                          "parent": current, "name": path.name}
                self._aux_records[path] = record
                self._verify_record(record)
                return None
            self._handles.add(descriptor)
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o022):
                _fail("UNSAFE_FILE")
            record = {"fd": descriptor, "mark": _file_mark(info), "data": None,
                      "missing": False, "parent": current, "name": path.name}
            self._aux_records[path] = record
            retained = True
            self._verify_record(record)
            if info.st_size > 2_000_000:
                _fail("BYTE_LIMIT")
            parts, size = [], 0
            while size < info.st_size:
                remaining = self._remaining_bytes()
                if not remaining:
                    _fail("BYTE_LIMIT")
                chunk = os.read(descriptor, min(_CHUNK, info.st_size - size, remaining))
                self._charge_bytes(len(chunk))
                if not chunk:
                    _fail("CUSTODY_DRIFT")
                parts.append(chunk)
                size += len(chunk)
            self._verify_record(record)
            record["data"] = b"".join(parts)
            return record["data"]
        except OSError:
            _fail("UNSAFE_FILE")
        finally:
            if descriptor is not None and not retained:
                self._release_unretained(descriptor)

    def _verify_aux(self):
        for path in self._aux_dirs:
            self._verify_dir(path)
        for record in self._aux_records.values():
            self._verify_record(record)

    def _remaining_bytes(self):
        self._ensure_live()
        return _TOTAL_BYTES - self._bytes_read

    def _charge_bytes(self, count):
        # Account returned bytes BEFORE a post-read deadline failure.
        if self._state != "LIVE":
            _fail("CLOSED_SNAPSHOT")
        if type(count) is not int or count < 0:
            _fail("INVALID_KEY")
        if count > _TOTAL_BYTES - self._bytes_read:
            _fail("BYTE_LIMIT")
        self._bytes_read += count
        self._check_time()

    def _charge_metadata(self, key, charge):
        self._ensure_live()
        if type(charge) is not int or charge < 0:
            _fail("INVALID_KEY")
        try:
            known = key in self._metadata_keys
        except TypeError:
            _fail("INVALID_KEY")
        if not known:
            if self._metadata_bytes + charge > _MAX_METADATA:
                _fail("COUNT_LIMIT")
            self._metadata_keys.add(key)
            self._metadata_bytes += charge

    def _observe(self, path, key):
        if path not in self._seen:
            charge = len(path.encode("utf-8")) + len(json.dumps(
                key, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
            if len(self._seen) >= _MAX_OBSERVATIONS:
                _fail("COUNT_LIMIT")
            self._charge_metadata(("file", path), charge)
            self._seen.add(path)

    def _verify_record(self, record):
        self._check_time()
        parent_fd = self._dirs[record["parent"]]["fd"]
        try:
            named = os.stat(record["name"], dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            if record["missing"]:
                return
            _fail("CUSTODY_DRIFT")
        except OSError:
            _fail("CUSTODY_DRIFT")
        if record["missing"]:
            _fail("CUSTODY_DRIFT")
        try:
            if (_file_mark(named) != record["mark"]
                    or _file_mark(os.fstat(record["fd"])) != record["mark"]):
                _fail("CUSTODY_DRIFT")
        except OSError:
            _fail("CUSTODY_DRIFT")

    def _read(self, kind, key=None):
        self._ensure_live()
        path, cap = _artifact(kind, key)
        self._observe(path, key)
        self._verify()
        record = self._records.get(path)
        if record is None:
            parent = (self._root_parent(path) if kind in ("root_result", "root_status")
                      else self._parent(path))
            name = path.split("/")[-1]
            parent_fd = self._dirs[parent]["fd"]
            descriptor = None
            retained = False
            try:
                try:
                    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW |
                        os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=parent_fd)
                except FileNotFoundError:
                    try:
                        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        record = {"fd": None, "mark": None, "data": None, "missing": True,
                                  "parent": parent, "name": name}
                        self._records[path] = record
                    else:
                        _fail("CUSTODY_DRIFT")
                    self._verify()
                    _fail("MISSING_FILE")
                self._handles.add(descriptor)
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.geteuid() or info.st_mode & 0o022):
                    _fail("UNSAFE_FILE")
                record = {"fd": descriptor, "mark": _file_mark(info), "data": None,
                          "missing": False, "parent": parent, "name": name}
                self._records[path] = record
                retained = True
                self._verify_record(record)
            except OSError:
                _fail("UNSAFE_FILE")
            finally:
                if descriptor is not None and not retained:
                    self._release_unretained(descriptor)
        if record["missing"]:
            self._verify_record(record)
            _fail("MISSING_FILE")
        self._verify_record(record)
        if record["mark"][6] > cap:
            _fail("BYTE_LIMIT")
        if record["data"] is not None:
            if len(record["data"]) > cap:
                _fail("BYTE_LIMIT")
            self._verify()
            return record["data"]
        parts, size = [], 0
        try:
            os.lseek(record["fd"], 0, os.SEEK_SET)
            while True:
                self._check_time()
                remaining = _TOTAL_BYTES - self._bytes_read
                if remaining == 0:
                    if size == record["mark"][6]:
                        break
                    _fail("BYTE_LIMIT")
                amount = min(_CHUNK, cap + 1 - size, remaining)
                chunk = os.read(record["fd"], amount)
                self._charge_bytes(len(chunk))
                if size + len(chunk) > cap:
                    _fail("BYTE_LIMIT")
                if not chunk:
                    break
                parts.append(chunk)
                size += len(chunk)
        except OSError:
            _fail("UNSAFE_FILE")
        self._verify()
        if size != record["mark"][6]:
            _fail("CUSTODY_DRIFT")
        body = b"".join(parts)
        record["data"] = body
        return body

    def _json_list(self, kind):
        self._ensure_live()
        if type(kind) is not str or kind != "metrics":
            _fail("INVALID_KEY")
        body = self._read("metrics")
        try:
            text = body.decode("utf-8", errors="strict")
            depth, nodes, quoted, escaped, scalar = 0, 0, False, False, False
            for index, character in enumerate(text):
                if index % 1024 == 0:
                    self._check_time()
                if quoted:
                    if escaped:
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == '"':
                        quoted = False
                    continue
                if character == '"':
                    quoted, scalar = True, False
                    nodes += 1
                elif character in "[{":
                    depth += 1
                    nodes += 1
                    scalar = False
                    if depth > _MAX_DEPTH:
                        _fail("JSON_INVALID")
                elif character in "]}":
                    depth -= 1
                    scalar = False
                elif character in ",:" or character.isspace():
                    scalar = False
                elif not scalar:
                    scalar = True
                    nodes += 1
                if nodes > _METRICS_MAX_NODES:
                    _fail("JSON_INVALID")
            self._check_time()
            value = json.loads(text, object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)
            if type(value) is not list or len(value) > _MAX_RECORDS:
                _fail("JSON_INVALID")
            pending, nodes = [value], 0
            while pending:
                self._check_time()
                item = pending.pop()
                nodes += 1
                if type(item) is dict:
                    nodes += len(item)
                    for key in item:
                        key.encode("utf-8", errors="strict")
                    pending.extend(item.values())
                elif type(item) is list:
                    pending.extend(item)
                elif type(item) is str:
                    item.encode("utf-8", errors="strict")
                elif type(item) is float and not math.isfinite(item):
                    _fail("JSON_INVALID")
                if nodes > _METRICS_MAX_NODES:
                    _fail("JSON_INVALID")
            self._verify()
            return value
        except _EvidenceFilesError:
            raise
        except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError, MemoryError):
            _fail("JSON_INVALID")

    def _metadata(self):
        self._verify()
        files = []
        for path, record in sorted(self._records.items()):
            self._check_time()
            if record["data"] is not None:
                files.append({"path": path, "sha256": hashlib.sha256(record["data"]).hexdigest(),
                              "size": len(record["data"])})
        self._check_time()
        return {"schema_version": 1, "files": files, "bytes_read": self._bytes_read}

    def _verify(self):
        self._ensure_live()
        for absolute in self._dirs:
            self._verify_dir(absolute)
        for record in self._records.values():
            self._verify_record(record)
        self._verify_aux()
        self._check_time()

    def _close(self):
        if self._state == "CLOSED":
            _fail("CLOSED_SNAPSHOT")
        problem = None
        for descriptor in tuple(self._handles):
            try:
                self._check_time()
            except _EvidenceFilesError as error:
                problem = problem or error.code
            try:
                self._drop_handle(descriptor)
            except _EvidenceFilesError as error:
                problem = problem or error.code
        try:
            self._check_time()
        except _EvidenceFilesError as error:
            problem = problem or error.code
        self._records.clear()
        self._aux_records.clear()
        self._aux_dirs.clear()
        self._dirs.clear()
        self._seen.clear()
        self._metadata_keys.clear()
        self._state = "CLOSED"
        if problem is not None:
            _fail(problem)


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail("JSON_INVALID")
        value[key] = item
    return value


def _bad_constant(_value):
    _fail("JSON_INVALID")


def _evidence_files(root):
    """Return a private single-use context; no caller policy/caps or callbacks."""
    owner = object.__new__(_EvidenceFiles)
    owner._initialize(root)
    return owner
