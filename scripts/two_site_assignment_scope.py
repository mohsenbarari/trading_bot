"""Fail-closed active-Assignment scope module for two-site refactor incident repair."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import fnmatch
import hashlib
import json
import os
import re
import selectors
import stat
import subprocess
import time
from typing import Any, List, Optional, Sequence, Set, Tuple, Union
import yaml

__all__ = [
    "AssignmentScopeError",
    "AssignmentScope",
    "AssignmentFact",
    "ASSIGNMENT_FACT_CODES",
    "TASK_PATH_OUTSIDE_SCOPE",
    "TASK_ASSIGNMENT_TOKEN_MISSING",
    "ASSIGNMENT_EXPIRED",
    "ASSIGNMENT_CONTRACT_INVALID",
    "load_active_assignment_scope",
    "load_expected_assignment_scope",
    "validate_task_scope",
    "validate_changed_paths",
]

MAX_CONTROL_FILE_BYTES = 512 * 1024
MAX_GIT_OUTPUT_BYTES = 4096
MAX_PATH_PATTERNS = 256
GIT_EXECUTABLE = "/usr/bin/git"

TASK_PATH_OUTSIDE_SCOPE = "TASK_PATH_OUTSIDE_SCOPE"
TASK_ASSIGNMENT_TOKEN_MISSING = "TASK_ASSIGNMENT_TOKEN_MISSING"
ASSIGNMENT_EXPIRED = "ASSIGNMENT_EXPIRED"
ASSIGNMENT_CONTRACT_INVALID = "ASSIGNMENT_CONTRACT_INVALID"

ASSIGNMENT_FACT_CODES = frozenset({
    TASK_PATH_OUTSIDE_SCOPE,
    TASK_ASSIGNMENT_TOKEN_MISSING,
    ASSIGNMENT_EXPIRED,
    ASSIGNMENT_CONTRACT_INVALID,
})


def _validate_fact_path(p: Optional[str]) -> Optional[str]:
    if p is None:
        return None
    if not isinstance(p, str) or type(p) is not str:
        raise TypeError("Fact path must be an exact string or None")
    if not p:
        raise ValueError("Fact path cannot be empty")
    if p != p.strip():
        raise ValueError("Leading or trailing whitespace rejected in fact path")
    try:
        encoded = p.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Fact path must be valid UTF-8") from exc
    if len(encoded) > 1024 or len(p) > 1024:
        raise ValueError("Fact path exceeds length limit")
    if "\0" in p or any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise ValueError("Control characters or NUL rejected in fact path")
    if "\\" in p:
        raise ValueError("Backslashes rejected in fact path")
    if p.startswith("/"):
        raise ValueError("Absolute paths rejected in fact path")
    if os.path.splitdrive(p)[0] or re.match(r"^[a-zA-Z]:", p):
        raise ValueError("Drive prefixes rejected in fact path")
    if "@" in p:
        raise ValueError("Credential-like '@' rejected in fact path")
    if re.search(
        r"(?i)(?:password|passwd|secret|token|bearer|credential|api[_-]?key|private[_-]?key)",
        p,
    ):
        raise ValueError("Credential-like content rejected in fact path")
    parts = p.split("/")
    for part in parts:
        if part == "":
            raise ValueError("Empty path segment rejected in fact path")
        if part == ".":
            raise ValueError("Dot '.' segment rejected in fact path")
        if part == "..":
            raise ValueError("Dot-dot '..' traversal segment rejected in fact path")
        if part.lower() == ".git":
            raise ValueError("'.git' segment rejected in fact path")
        if part.startswith("-"):
            raise ValueError("Leading dash in path segment rejected in fact path")
    if any(c in p for c in "*?[]"):
        raise ValueError("Wildcard characters rejected in fact path")
    return p


@dataclass(frozen=True)
class AssignmentFact:
    code: str
    path: Optional[str] = None

    def __init__(self, code: str, path: Optional[str] = None) -> None:
        if type(self) is not AssignmentFact:
            raise TypeError("AssignmentFact cannot be subclassed")
        if not isinstance(code, str) or type(code) is not str:
            raise TypeError("Fact code must be an exact string")
        if code not in ASSIGNMENT_FACT_CODES:
            raise ValueError(f"Unknown assignment fact code: {code!r}")
        clean_path = _validate_fact_path(path)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "path", clean_path)


def _task_path_outside_scope_fact(path: str) -> AssignmentFact:
    try:
        return AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, path)
    except (ValueError, TypeError):
        return AssignmentFact(TASK_PATH_OUTSIDE_SCOPE, None)


class AssignmentScopeError(Exception):
    """Raised when assignment scope loading or validation fails."""

    def __init__(
        self,
        message: str = "",
        fact: Optional[AssignmentFact] = None,
        *extra: Any,
    ) -> None:
        if extra:
            super().__init__(message, fact, *extra)
        elif message:
            super().__init__(message)
        else:
            super().__init__()
        if fact is not None:
            if type(fact) is not AssignmentFact:
                raise TypeError("fact must be an exact AssignmentFact instance")
            self._fact: Optional[AssignmentFact] = fact
        else:
            self._fact = None

    @property
    def fact(self) -> Optional[AssignmentFact]:
        return self._fact

    @property
    def code(self) -> Optional[str]:
        return self._fact.code if self._fact is not None else None

    @property
    def path(self) -> Optional[str]:
        return self._fact.path if self._fact is not None else None

    @property
    def fact_code(self) -> Optional[str]:
        return self._fact.code if self._fact is not None else None

    @property
    def fact_path(self) -> Optional[str]:
        return self._fact.path if self._fact is not None else None


@dataclass(frozen=True)
class AssignmentScope:
    assignment_id: str
    stage_id: str
    allowed_paths: Tuple[str, ...]
    forbidden_paths: Tuple[str, ...]
    expires_at: str
    sha256: str

    def __init__(
        self,
        assignment_id: str,
        stage_id: str,
        allowed_paths: Sequence[str],
        forbidden_paths: Sequence[str],
        expires_at: str,
        sha256: str,
    ) -> None:
        object.__setattr__(self, "assignment_id", str(assignment_id))
        object.__setattr__(self, "stage_id", str(stage_id))
        object.__setattr__(self, "allowed_paths", tuple(allowed_paths))
        object.__setattr__(self, "forbidden_paths", tuple(forbidden_paths))
        object.__setattr__(self, "expires_at", str(expires_at))
        object.__setattr__(self, "sha256", str(sha256))


class UniqueKeySafeLoader(yaml.SafeLoader):
    pass


def _unique_key_mapping_constructor(loader: Any, node: Any, deep: bool = False) -> Any:
    loader.flatten_mapping(node)
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            hash(key)
        except TypeError as exc:
            raise AssignmentScopeError("Unhashable key in YAML mapping") from exc
        if key in mapping:
            raise AssignmentScopeError("Duplicate key in YAML mapping")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _unique_key_mapping_constructor,
)


def _safe_load_yaml(content: Union[str, bytes]) -> Any:
    try:
        return yaml.load(content, Loader=UniqueKeySafeLoader)
    except AssignmentScopeError:
        raise
    except Exception as e:
        raise AssignmentScopeError("Failed to parse YAML document") from e


def _validate_brackets(pattern: str) -> None:
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "]":
            raise AssignmentScopeError("Unmatched closing bracket in pattern")
        if c == "[":
            start = i
            i += 1
            if i < n and pattern[i] in ("!", "^"):
                i += 1
            if i < n and pattern[i] == "]":
                raise AssignmentScopeError("Empty bracket expression in pattern")
            found_close = False
            while i < n:
                if pattern[i] == "[":
                    raise AssignmentScopeError("Nested opening bracket in pattern")
                if pattern[i] == "]":
                    found_close = True
                    break
                i += 1
            if not found_close:
                raise AssignmentScopeError("Unclosed bracket expression in pattern")
            inner = pattern[start + 1 : i]
            if inner.startswith("!") or inner.startswith("^"):
                inner = inner[1:]
            j = 0
            while j < len(inner):
                if inner[j] == "-" and 0 < j < len(inner) - 1:
                    if ord(inner[j - 1]) > ord(inner[j + 1]):
                        raise AssignmentScopeError("Invalid character range in bracket pattern")
                j += 1
        i += 1


def _validate_path_or_pattern(p: str, is_pattern: bool = True) -> str:
    if not isinstance(p, str):
        raise AssignmentScopeError("Path/pattern must be a string")
    if not p:
        raise AssignmentScopeError("Path/pattern cannot be empty")
    if p != p.strip():
        raise AssignmentScopeError("Leading or trailing whitespace rejected in path/pattern")
    if len(p) > 1024:
        raise AssignmentScopeError("Path/pattern exceeds length limit")
    if any(ord(c) < 32 or ord(c) == 127 for c in p):
        raise AssignmentScopeError("Control characters rejected in path/pattern")
    if "\\" in p:
        raise AssignmentScopeError("Backslashes rejected in path/pattern")
    if p.startswith("/"):
        raise AssignmentScopeError("Absolute paths rejected")
    if p.startswith("-"):
        raise AssignmentScopeError("Leading dash rejected in path/pattern")
    if p == "*":
        raise AssignmentScopeError("Bare '*' pattern rejected")
    if p.startswith("**"):
        raise AssignmentScopeError("Leading '**' pattern rejected")

    parts = p.split("/")
    for part in parts:
        if part == "":
            raise AssignmentScopeError("Empty path component rejected")
        if part == ".":
            raise AssignmentScopeError("Dot '.' component rejected")
        if part == "..":
            raise AssignmentScopeError("Dot-dot '..' component rejected")
        if part.lower() == ".git":
            raise AssignmentScopeError("'.git' component rejected")
        if part.startswith("-"):
            raise AssignmentScopeError("Leading dash in path component rejected")

    if is_pattern:
        _validate_brackets(p)
        try:
            regex_str = fnmatch.translate(p)
            re.compile(regex_str)
        except Exception as e:
            raise AssignmentScopeError("Invalid glob pattern") from e
    else:
        if any(c in p for c in "*?[]"):
            raise AssignmentScopeError("Wildcard characters rejected in concrete path")

    return p


def _validate_pattern_list(patterns: Any, name: str) -> Tuple[str, ...]:
    if not isinstance(patterns, (list, tuple)):
        raise AssignmentScopeError(f"{name} must be a list or tuple")
    if len(patterns) == 0:
        raise AssignmentScopeError(f"{name} cannot be empty")
    if len(patterns) > MAX_PATH_PATTERNS:
        raise AssignmentScopeError(f"{name} exceeds maximum pattern count")
    seen: Set[str] = set()
    validated: List[str] = []
    for item in patterns:
        if not isinstance(item, str):
            raise AssignmentScopeError(f"Item in {name} must be a string")
        cleaned = _validate_path_or_pattern(item, is_pattern=True)
        if cleaned in seen:
            raise AssignmentScopeError(f"Duplicate pattern rejected in {name}")
        seen.add(cleaned)
        validated.append(cleaned)
    return tuple(validated)


def _validate_assignment_id(aid: Any) -> str:
    if not isinstance(aid, str):
        raise AssignmentScopeError("Assignment ID must be a string")
    if aid.strip() != aid:
        raise AssignmentScopeError("Assignment ID has leading or trailing whitespace")
    if not aid:
        raise AssignmentScopeError("Assignment ID cannot be empty")
    if len(aid) > 128:
        raise AssignmentScopeError("Assignment ID exceeds length limit")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", aid):
        raise AssignmentScopeError("Invalid characters in Assignment ID")
    return aid


def _validate_stage_id(sid: Any) -> str:
    if not isinstance(sid, str):
        raise AssignmentScopeError("Stage ID must be a string")
    if sid.strip() != sid:
        raise AssignmentScopeError("Stage ID has leading or trailing whitespace")
    if not sid:
        raise AssignmentScopeError("Stage ID cannot be empty")
    if len(sid) > 128:
        raise AssignmentScopeError("Stage ID exceeds length limit")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        raise AssignmentScopeError("Invalid characters in Stage ID")
    return sid


def _safe_resolve_under(base_dir: str, rel_path: str) -> str:
    if os.path.isabs(rel_path):
        raise AssignmentScopeError("Path must be relative")
    base_abs = os.path.abspath(base_dir)
    target_abs = os.path.abspath(os.path.join(base_abs, rel_path))
    try:
        common = os.path.commonpath([base_abs, target_abs])
    except ValueError as e:
        raise AssignmentScopeError("Path traversal escape detected") from e
    if common != base_abs:
        raise AssignmentScopeError("Path escape detected")
    return target_abs


def _check_no_symlinks(base_dir: str, target_path: str) -> None:
    rel = os.path.relpath(target_path, base_dir)
    parts = rel.split(os.sep)
    curr = base_dir
    for part in parts:
        curr = os.path.join(curr, part)
        try:
            st = os.lstat(curr)
        except OSError as e:
            raise AssignmentScopeError(f"Failed to stat path component: {e}") from e
        if stat.S_ISLNK(st.st_mode):
            raise AssignmentScopeError("Symlink detected in path component")


def _safe_read_file_bytes(filepath: str, max_bytes: int = MAX_CONTROL_FILE_BYTES) -> bytes:
    try:
        st1 = os.lstat(filepath)
    except OSError as e:
        raise AssignmentScopeError(f"Failed to lstat control file: {e}") from e

    if stat.S_ISLNK(st1.st_mode):
        raise AssignmentScopeError("Symlink rejected")
    if not stat.S_ISREG(st1.st_mode):
        raise AssignmentScopeError("Not a regular file")
    if st1.st_nlink != 1:
        raise AssignmentScopeError("Hardlinks rejected")
    if hasattr(os, "getuid") and st1.st_uid != os.getuid():
        raise AssignmentScopeError("Wrong file owner")
    if st1.st_size > max_bytes:
        raise AssignmentScopeError("File oversized")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        fd = os.open(filepath, flags)
    except OSError as e:
        raise AssignmentScopeError(f"Failed to open control file: {e}") from e

    try:
        st_fd = os.fstat(fd)
        if not stat.S_ISREG(st_fd.st_mode):
            raise AssignmentScopeError("Not a regular file on open")
        if st_fd.st_nlink != 1:
            raise AssignmentScopeError("Hardlink detected on open")
        if hasattr(os, "getuid") and st_fd.st_uid != os.getuid():
            raise AssignmentScopeError("Wrong owner on open")
        if (
            st_fd.st_ino != st1.st_ino
            or st_fd.st_dev != st1.st_dev
            or st_fd.st_size != st1.st_size
            or st_fd.st_mtime_ns != st1.st_mtime_ns
        ):
            raise AssignmentScopeError("Metadata changed before read")
        if st_fd.st_size > max_bytes:
            raise AssignmentScopeError("File oversized on open")

        data = bytearray()
        remaining = st_fd.st_size
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining + 1))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > max_bytes:
                raise AssignmentScopeError("Read exceeded maximum allowed bytes")
            remaining = st_fd.st_size - len(data)

        if len(data) != st_fd.st_size:
            raise AssignmentScopeError("Byte count mismatch during read")

        extra = os.read(fd, 1)
        if extra:
            raise AssignmentScopeError("File grew during read")

        st_after_fd = os.fstat(fd)
        if (
            st_after_fd.st_ino != st_fd.st_ino
            or st_after_fd.st_dev != st_fd.st_dev
            or st_after_fd.st_size != st_fd.st_size
            or st_after_fd.st_mtime_ns != st_fd.st_mtime_ns
        ):
            raise AssignmentScopeError("Metadata changed during read")

        st_after_path = os.lstat(filepath)
        if (
            st_after_path.st_ino != st_fd.st_ino
            or st_after_path.st_dev != st_fd.st_dev
            or st_after_path.st_size != st_fd.st_size
            or st_after_path.st_mtime_ns != st_fd.st_mtime_ns
        ):
            raise AssignmentScopeError("Path metadata changed during read")

        return bytes(data)
    finally:
        os.close(fd)


def _is_path_in_scope(
    path: str,
    allowed_patterns: Sequence[str],
    forbidden_patterns: Sequence[str],
) -> bool:
    allowed = any(fnmatch.fnmatchcase(path, pat) for pat in allowed_patterns)
    if not allowed:
        return False
    forbidden = any(fnmatch.fnmatchcase(path, fpat) for fpat in forbidden_patterns)
    if forbidden:
        return False
    return True


def _run_git_cmd(args: Sequence[str], cwd: str, timeout: float = 10.0) -> str:
    proc = None
    try:
        proc = subprocess.Popen(
            [GIT_EXECUTABLE, *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (subprocess.SubprocessError, OSError) as e:
        raise AssignmentScopeError(f"Git execution failed: {e}") from e

    stdout_buf = bytearray()
    stderr_buf = bytearray()
    deadline = time.monotonic() + timeout

    def _reap_child() -> None:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2.0)
            except (subprocess.SubprocessError, OSError):
                pass

    sel = selectors.DefaultSelector()
    try:
        assert proc.stdout is not None
        assert proc.stderr is not None
        os.set_blocking(proc.stdout.fileno(), False)
        os.set_blocking(proc.stderr.fileno(), False)
        sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
        sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")

        while sel.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _reap_child()
                raise AssignmentScopeError("Git command timed out")

            events = sel.select(timeout=min(remaining, 0.5))
            for key, _ in events:
                stream_tag = key.data
                stream_obj = key.fileobj
                try:
                    chunk = os.read(stream_obj.fileno(), 4096)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError as e:
                    _reap_child()
                    raise AssignmentScopeError(f"Git read failed: {e}") from e

                if not chunk:
                    sel.unregister(stream_obj)
                    stream_obj.close()
                else:
                    if stream_tag == "stdout":
                        stdout_buf.extend(chunk)
                        if len(stdout_buf) > MAX_GIT_OUTPUT_BYTES:
                            _reap_child()
                            raise AssignmentScopeError("Git command stdout exceeded limit")
                    else:
                        stderr_buf.extend(chunk)
                        if len(stderr_buf) > MAX_GIT_OUTPUT_BYTES:
                            _reap_child()
                            raise AssignmentScopeError("Git command stderr exceeded limit")

            if proc.poll() is not None and not events:
                for key in list(sel.get_map().values()):
                    stream_tag = key.data
                    stream_obj = key.fileobj
                    while True:
                        try:
                            chunk = os.read(stream_obj.fileno(), 4096)
                        except (BlockingIOError, InterruptedError):
                            break
                        except OSError:
                            break
                        if not chunk:
                            break
                        if stream_tag == "stdout":
                            stdout_buf.extend(chunk)
                            if len(stdout_buf) > MAX_GIT_OUTPUT_BYTES:
                                _reap_child()
                                raise AssignmentScopeError("Git command stdout exceeded limit")
                        else:
                            stderr_buf.extend(chunk)
                            if len(stderr_buf) > MAX_GIT_OUTPUT_BYTES:
                                _reap_child()
                                raise AssignmentScopeError("Git command stderr exceeded limit")
                    sel.unregister(stream_obj)
                    stream_obj.close()
                break

        wait_timeout = max(0.1, deadline - time.monotonic())
        proc.wait(timeout=wait_timeout)
    except AssignmentScopeError:
        _reap_child()
        raise
    except Exception as e:
        _reap_child()
        raise AssignmentScopeError(f"Git execution error: {e}") from e
    finally:
        sel.close()
        _reap_child()

    if proc.returncode != 0:
        try:
            err_msg = stderr_buf.decode("utf-8", errors="replace").strip()[:200]
        except Exception:
            err_msg = "unknown error"
        raise AssignmentScopeError(f"Git command failed: {err_msg}")

    try:
        out = stdout_buf.decode("utf-8").strip()
    except UnicodeDecodeError as e:
        raise AssignmentScopeError("Git stdout is not valid UTF-8") from e

    if not out:
        raise AssignmentScopeError("Git command returned empty output")
    return out


def _resolve_shared_checkout(integration_root_abs: str) -> str:
    common_dir = _run_git_cmd(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"],
        cwd=integration_root_abs,
    )
    if not os.path.isabs(common_dir):
        raise AssignmentScopeError("git-common-dir must be an absolute path")
    common_dir_real = os.path.realpath(common_dir)
    if common_dir != common_dir_real:
        raise AssignmentScopeError("git-common-dir is non-canonical or contains symlinks")
    if not (common_dir.endswith("/.git") or common_dir == "/.git") or os.path.basename(common_dir) != ".git":
        raise AssignmentScopeError("git-common-dir must end in .git")

    shared_checkout = os.path.dirname(common_dir)
    shared_checkout_real = os.path.realpath(shared_checkout)
    if shared_checkout != shared_checkout_real:
        raise AssignmentScopeError("shared checkout is non-canonical or contains symlinks")
    if not os.path.isdir(shared_checkout):
        raise AssignmentScopeError("Resolved shared checkout is not a directory")

    return shared_checkout


def _get_current_branch(integration_root_abs: str) -> str:
    branch = _run_git_cmd(
        ["rev-parse", "--abbrev-ref", "HEAD"],
        cwd=integration_root_abs,
    )
    if branch == "HEAD":
        raise AssignmentScopeError("Integration checkout is on detached HEAD")
    return branch


def _find_assignment_markdown(
    shared_checkout: str,
    active_assignment_id: str,
) -> str:
    rel_path = f".local/two-site-refactor/assignments/{active_assignment_id}.md"
    target_path = _safe_resolve_under(shared_checkout, rel_path)
    if not os.path.isfile(target_path):
        raise AssignmentScopeError("Active Assignment Markdown document does not exist")
    _check_no_symlinks(shared_checkout, target_path)
    return target_path


def _validate_stage_in_execution_order(exec_order_data: Any, stage_id: str) -> None:
    if not isinstance(exec_order_data, dict):
        raise AssignmentScopeError("execution-order.yaml root must be a mapping")

    stages_section = exec_order_data.get("stages")
    if stages_section is None:
        stages_section = exec_order_data.get("order") or exec_order_data.get("execution_order")
    if stages_section is None:
        stages_section = exec_order_data

    matching_states: List[str] = []

    if isinstance(stages_section, dict):
        for key, val in stages_section.items():
            if str(key) == stage_id:
                state_val = None
                if isinstance(val, dict):
                    state_val = val.get("state") or val.get("status")
                elif isinstance(val, str):
                    state_val = val
                if state_val is not None:
                    matching_states.append(str(state_val))
                else:
                    matching_states.append("")
    elif isinstance(stages_section, (list, tuple)):
        for item in stages_section:
            if isinstance(item, dict):
                sid = item.get("id") or item.get("stage_id") or item.get("name")
                if sid == stage_id:
                    state_val = item.get("state") or item.get("status")
                    if state_val is not None:
                        matching_states.append(str(state_val))
                    else:
                        matching_states.append("")
            elif isinstance(item, str) and item == stage_id:
                matching_states.append("")
    else:
        raise AssignmentScopeError("Invalid stages structure in execution-order.yaml")

    if len(matching_states) == 0:
        raise AssignmentScopeError(f"Stage '{stage_id}' not found in execution-order")
    if len(matching_states) > 1:
        raise AssignmentScopeError(f"Stage '{stage_id}' appears multiple times in execution-order")

    status = matching_states[0]
    if status != "IN_PROGRESS":
        raise AssignmentScopeError(
            f"Stage '{stage_id}' status in execution-order is '{status}', expected 'IN_PROGRESS'"
        )


def _load_active_assignment_scope(integration_root: Union[str, os.PathLike]) -> AssignmentScope:
    if not integration_root:
        raise AssignmentScopeError("integration_root cannot be empty")
    integration_root_str = str(integration_root)
    if not os.path.isdir(integration_root_str):
        raise AssignmentScopeError("integration_root directory not found")
    integration_root_abs = os.path.abspath(integration_root_str)
    integration_root_real = os.path.realpath(integration_root_abs)
    if integration_root_abs != integration_root_real:
        raise AssignmentScopeError("integration_root is non-canonical or contains symlinks")

    shared_checkout = _resolve_shared_checkout(integration_root_abs)
    current_branch = _get_current_branch(integration_root_abs)

    worktrees_path = _safe_resolve_under(shared_checkout, ".local/two-site-refactor/worktrees.yaml")
    if not os.path.isfile(worktrees_path):
        raise AssignmentScopeError("worktrees.yaml not found")
    _check_no_symlinks(shared_checkout, worktrees_path)
    worktrees_bytes = _safe_read_file_bytes(worktrees_path)
    worktrees_data = _safe_load_yaml(worktrees_bytes.decode("utf-8"))
    if not isinstance(worktrees_data, dict):
        raise AssignmentScopeError("worktrees.yaml root must be a mapping")

    integration_info = worktrees_data.get("integration")
    if not isinstance(integration_info, dict):
        raise AssignmentScopeError("worktrees.yaml missing 'integration' mapping")

    reg_path_raw = integration_info.get("path") or integration_info.get("worktree_path")
    if not reg_path_raw or not isinstance(reg_path_raw, str):
        raise AssignmentScopeError("worktrees.yaml missing valid integration path")
    if os.path.isabs(reg_path_raw):
        reg_path_abs = os.path.abspath(reg_path_raw)
        if reg_path_abs != os.path.realpath(reg_path_abs):
            raise AssignmentScopeError("Registry integration path is non-canonical or contains symlinks")
    else:
        reg_path_abs = _safe_resolve_under(shared_checkout, reg_path_raw)
        if reg_path_abs != os.path.realpath(reg_path_abs):
            raise AssignmentScopeError("Registry integration path is non-canonical or contains symlinks")

    if reg_path_abs != integration_root_abs:
        raise AssignmentScopeError("Registry integration path does not match supplied root")

    reg_branch = integration_info.get("branch") or integration_info.get("current_branch")
    if not reg_branch or not isinstance(reg_branch, str):
        raise AssignmentScopeError("worktrees.yaml missing valid integration branch")
    if reg_branch != current_branch:
        raise AssignmentScopeError("Registry branch does not match integration root branch")

    active_assignment_id_raw = integration_info.get("active_assignment_id")
    if not active_assignment_id_raw:
        raise AssignmentScopeError("worktrees.yaml missing integration.active_assignment_id")
    active_assignment_id = _validate_assignment_id(active_assignment_id_raw)

    assignment_file_path = _find_assignment_markdown(
        shared_checkout=shared_checkout,
        active_assignment_id=active_assignment_id,
    )
    assignment_bytes = _safe_read_file_bytes(assignment_file_path)
    try:
        assignment_text = assignment_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise AssignmentScopeError("Assignment file is not valid UTF-8") from e

    fenced_blocks = re.findall(
        r"^[ \t]*```(?:yaml|yml)?\s*\n(.*?)\n[ \t]*```\s*$",
        assignment_text,
        re.MULTILINE | re.DOTALL,
    )
    if len(fenced_blocks) != 1:
        raise AssignmentScopeError("Assignment Markdown must contain exactly one fenced YAML block")

    assignment_yaml_data = _safe_load_yaml(fenced_blocks[0])
    if not isinstance(assignment_yaml_data, dict):
        raise AssignmentScopeError("Assignment YAML block must be a mapping")

    doc_id = assignment_yaml_data.get("id") or assignment_yaml_data.get("assignment_id")
    if doc_id != active_assignment_id:
        raise AssignmentScopeError("Assignment document id does not match active_assignment_id")

    status = assignment_yaml_data.get("status")
    if status != "READY":
        raise AssignmentScopeError("Assignment status must be READY")

    role = assignment_yaml_data.get("role")
    if role != "WORKER":
        raise AssignmentScopeError("Assignment role must be WORKER")

    doc_branch = assignment_yaml_data.get("integration_branch")
    if doc_branch != current_branch:
        raise AssignmentScopeError("Assignment integration_branch does not match current branch")

    external_access = assignment_yaml_data.get("external_access")
    if external_access != "NONE":
        raise AssignmentScopeError("Assignment external_access must be NONE")

    raw_expires = assignment_yaml_data.get("expires_at")
    if raw_expires is None:
        raise AssignmentScopeError("Assignment document missing 'expires_at'")
    if type(raw_expires) is datetime.date:
        raise AssignmentScopeError("expires_at must include time and timezone information")
    elif isinstance(raw_expires, datetime.datetime):
        if raw_expires.tzinfo is None:
            raise AssignmentScopeError("expires_at must include timezone information")
        expires_dt = raw_expires
    elif isinstance(raw_expires, str):
        expires_at_str_raw = raw_expires.strip()
        if not expires_at_str_raw:
            raise AssignmentScopeError("expires_at cannot be empty")
        try:
            dt_clean = (
                expires_at_str_raw.replace("Z", "+00:00")
                if expires_at_str_raw.endswith("Z")
                else expires_at_str_raw
            )
            expires_dt = datetime.datetime.fromisoformat(dt_clean)
        except Exception as e:
            raise AssignmentScopeError("Invalid expires_at format") from e
        if expires_dt.tzinfo is None:
            raise AssignmentScopeError("expires_at must include timezone information")
    else:
        raise AssignmentScopeError("Unsupported expires_at type")

    utc_dt = expires_dt.astimezone(datetime.timezone.utc)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    if utc_dt <= now_utc:
        raise AssignmentScopeError(
            "Assignment has expired",
            AssignmentFact(ASSIGNMENT_EXPIRED),
        )

    iso_z = utc_dt.isoformat()
    if iso_z.endswith("+00:00"):
        iso_z = iso_z[:-6] + "Z"
    expires_at_str = iso_z

    raw_stage_id = assignment_yaml_data.get("stage_id")
    raw_stages = assignment_yaml_data.get("stages")
    stage_id: Optional[str] = None
    if raw_stages is not None:
        if not isinstance(raw_stages, (list, tuple)):
            raise AssignmentScopeError("Assignment stages must be a list or tuple")
        if len(raw_stages) != 1:
            raise AssignmentScopeError("Assignment stages must have exactly one element")
        item = raw_stages[0]
        stage_from_list = _validate_stage_id(item)
        if raw_stage_id is not None:
            if _validate_stage_id(raw_stage_id) != stage_from_list:
                raise AssignmentScopeError("Mismatch between stage_id and stages[0]")
        stage_id = stage_from_list
    elif raw_stage_id is not None:
        stage_id = _validate_stage_id(raw_stage_id)
    else:
        raise AssignmentScopeError("Assignment must specify exactly one stage_id")

    allowed_paths = _validate_pattern_list(assignment_yaml_data.get("allowed_paths"), "allowed_paths")
    forbidden_paths = _validate_pattern_list(assignment_yaml_data.get("forbidden_paths"), "forbidden_paths")

    exec_order_rel = "docs/refactor/two-server/execution-order.yaml"
    _run_git_cmd(
        ["ls-files", "--error-unmatch", exec_order_rel],
        cwd=integration_root_abs,
    )
    exec_order_path = _safe_resolve_under(integration_root_abs, exec_order_rel)
    if not os.path.isfile(exec_order_path):
        raise AssignmentScopeError("execution-order.yaml not found")
    _check_no_symlinks(integration_root_abs, exec_order_path)
    exec_order_bytes = _safe_read_file_bytes(exec_order_path)
    exec_order_data = _safe_load_yaml(exec_order_bytes.decode("utf-8"))

    _validate_stage_in_execution_order(exec_order_data, stage_id)

    canonical_contract = {
        "allowed_paths": list(allowed_paths),
        "assignment_id": active_assignment_id,
        "expires_at": expires_at_str,
        "external_access": "NONE",
        "forbidden_paths": list(forbidden_paths),
        "integration_branch": current_branch,
        "role": "WORKER",
        "stage_id": stage_id,
        "status": "READY",
    }
    canonical_contract_bytes = json.dumps(
        canonical_contract, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")

    hasher = hashlib.sha256()
    hasher.update(canonical_contract_bytes)
    hasher.update(b"\n")
    hasher.update(assignment_bytes)
    sha256_digest = hasher.hexdigest().lower()

    return AssignmentScope(
        assignment_id=active_assignment_id,
        stage_id=stage_id,
        allowed_paths=allowed_paths,
        forbidden_paths=forbidden_paths,
        expires_at=expires_at_str,
        sha256=sha256_digest,
    )


def load_active_assignment_scope(integration_root: Union[str, os.PathLike]) -> AssignmentScope:
    try:
        return _load_active_assignment_scope(integration_root)
    except AssignmentScopeError as exc:
        if exc.fact is None:
            exc._fact = AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None)
        raise


def load_expected_assignment_scope(
    integration_root: Union[str, os.PathLike],
    expected_assignment_id: str,
    expected_sha256: str,
) -> AssignmentScope:
    try:
        if not isinstance(expected_assignment_id, str):
            raise AssignmentScopeError("expected_assignment_id must be a string")
        expected_id = _validate_assignment_id(expected_assignment_id)

        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
        ):
            raise AssignmentScopeError("expected_sha256 must be a lowercase 64-hex digest")

        scope = load_active_assignment_scope(integration_root)

        if scope.assignment_id != expected_id:
            raise AssignmentScopeError("Active assignment ID does not match expected assignment ID")

        if scope.sha256 != expected_sha256:
            raise AssignmentScopeError("Active assignment sha256 digest does not match expected digest")

        return scope
    except AssignmentScopeError as exc:
        if exc.fact is None:
            exc._fact = AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None)
        raise


def validate_task_scope(scope: AssignmentScope, task: Any) -> None:
    try:
        if not isinstance(scope, AssignmentScope):
            raise AssignmentScopeError("scope must be an AssignmentScope instance")

        if isinstance(task, dict):
            allowed_files = task.get("allowed_files")
            goal = task.get("goal")
        elif hasattr(task, "allowed_files") and hasattr(task, "goal"):
            allowed_files = getattr(task, "allowed_files")
            goal = getattr(task, "goal")
        else:
            raise AssignmentScopeError("task must be a dict or expose 'allowed_files' and 'goal'")

        if not isinstance(allowed_files, (list, tuple)):
            raise AssignmentScopeError("task.allowed_files must be a list or tuple")
        if len(allowed_files) == 0:
            raise AssignmentScopeError("task.allowed_files cannot be empty")
        if len(allowed_files) > MAX_PATH_PATTERNS:
            raise AssignmentScopeError("task.allowed_files exceeds maximum pattern count")
        if len(allowed_files) != len(set(allowed_files)):
            raise AssignmentScopeError("task.allowed_files contains duplicate paths")

        for f in allowed_files:
            clean_f = _validate_path_or_pattern(f, is_pattern=False)
            if not _is_path_in_scope(clean_f, scope.allowed_paths, scope.forbidden_paths):
                raise AssignmentScopeError(
                    "Task allowed_file is outside assignment scope",
                    _task_path_outside_scope_fact(clean_f),
                )

        if not isinstance(goal, str):
            raise AssignmentScopeError("task.goal must be a string")
        if not goal.strip():
            raise AssignmentScopeError("task.goal cannot be empty")

        token_pattern = re.compile(
            r"(?<![A-Za-z0-9_-])" + re.escape(scope.assignment_id) + r"(?![A-Za-z0-9_-])"
        )
        if not token_pattern.search(goal):
            raise AssignmentScopeError(
                "task.goal does not contain assignment ID as a bounded token",
                AssignmentFact(TASK_ASSIGNMENT_TOKEN_MISSING),
            )
    except AssignmentScopeError as exc:
        if exc.fact is None:
            exc._fact = AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None)
        raise


def validate_changed_paths(scope: AssignmentScope, paths: Any) -> None:
    try:
        if not isinstance(scope, AssignmentScope):
            raise AssignmentScopeError("scope must be an AssignmentScope instance")

        if not isinstance(paths, (list, tuple)):
            raise AssignmentScopeError("paths must be a list or tuple")
        if len(paths) == 0:
            raise AssignmentScopeError("paths cannot be empty")
        if len(paths) > MAX_PATH_PATTERNS:
            raise AssignmentScopeError("paths exceeds maximum path count")
        if len(paths) != len(set(paths)):
            raise AssignmentScopeError("paths contains duplicate paths")

        for p in paths:
            clean_p = _validate_path_or_pattern(p, is_pattern=False)
            if not _is_path_in_scope(clean_p, scope.allowed_paths, scope.forbidden_paths):
                raise AssignmentScopeError("Changed path is outside assignment scope")
    except AssignmentScopeError as exc:
        if exc.fact is None:
            exc._fact = AssignmentFact(ASSIGNMENT_CONTRACT_INVALID, None)
        raise
