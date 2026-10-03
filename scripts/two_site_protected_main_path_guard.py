"""Protected main parent directory custody guard.

The yielded file descriptor is an implementation resource, NOT an approval or
Writer authority. Future reader must verify leaf and all bindings independently.
"""

from contextlib import contextmanager
import os
import pathlib
import stat
from typing import Iterator, NamedTuple, Optional

__all__ = ["ProtectedMainPathGuardError", "pinned_protected_main_parent"]


class ProtectedMainPathGuardError(ValueError):
    """Raised when the protected main directory chain is invalid or compromised."""

    def __init__(self, message: str = "invalid protected main path") -> None:
        super().__init__(message)


class _ChainNode(NamedTuple):
    parent_fd: Optional[int]
    name: str
    fd: int
    dev: int
    ino: int
    mode: int
    uid: int
    role: str


def _role_for_index(node_idx: int, root_len: int) -> str:
    target_root_idx = root_len - 1
    if node_idx < target_root_idx:
        return "ancestor"
    if node_idx == target_root_idx:
        return "root"
    if node_idx == target_root_idx + 1:
        return "local"
    if node_idx == target_root_idx + 2:
        return "intermediate"
    if node_idx == target_root_idx + 3:
        return "autopilot"
    raise ProtectedMainPathGuardError()


def _verify_node(st: os.stat_result, fst: os.stat_result, role: str) -> None:
    if st.st_dev != fst.st_dev or st.st_ino != fst.st_ino:
        raise ProtectedMainPathGuardError()
    if not stat.S_ISDIR(st.st_mode) or not stat.S_ISDIR(fst.st_mode):
        raise ProtectedMainPathGuardError()
    if stat.S_ISLNK(st.st_mode) or stat.S_ISLNK(fst.st_mode):
        raise ProtectedMainPathGuardError()
    if role == "ancestor":
        return
    euid = os.geteuid()
    if st.st_uid != euid or fst.st_uid != euid:
        raise ProtectedMainPathGuardError()
    if role in ("root", "intermediate"):
        if (st.st_mode & 0o022) != 0 or (fst.st_mode & 0o022) != 0:
            raise ProtectedMainPathGuardError()
    elif role in ("local", "autopilot"):
        if stat.S_IMODE(st.st_mode) != 0o700 or stat.S_IMODE(fst.st_mode) != 0o700:
            raise ProtectedMainPathGuardError()
    else:
        raise ProtectedMainPathGuardError()


def _revalidate_chain(chain: list[_ChainNode]) -> None:
    for node in chain:
        fst = os.fstat(node.fd)
        if node.parent_fd is None:
            st = os.stat("/", follow_symlinks=False)
        else:
            st = os.stat(node.name, dir_fd=node.parent_fd, follow_symlinks=False)
        if (
            st.st_dev != node.dev
            or fst.st_dev != node.dev
            or st.st_ino != node.ino
            or fst.st_ino != node.ino
            or st.st_mode != node.mode
            or fst.st_mode != node.mode
            or st.st_uid != node.uid
            or fst.st_uid != node.uid
        ):
            raise ProtectedMainPathGuardError()
        _verify_node(st, fst, node.role)


def _close_all(fds: list[int]) -> bool:
    all_ok = True
    for fd in reversed(fds):
        try:
            os.close(fd)
        except Exception:
            all_ok = False
    return all_ok


@contextmanager
def pinned_protected_main_parent(root: pathlib.Path) -> Iterator[int]:
    if not isinstance(root, pathlib.Path) or not root.is_absolute():
        raise ProtectedMainPathGuardError("invalid protected main path") from None
    raw = str(root)
    if "//" in raw or ".." in root.parts or ".." in raw.split("/"):
        raise ProtectedMainPathGuardError("invalid protected main path") from None

    root_parts = list(root.parts)
    comps = root_parts[1:] + [".local", "two-site-refactor", "autopilot"]

    acquired_fds: list[int] = []
    chain: list[_ChainNode] = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

    try:
        st = os.stat("/", follow_symlinks=False)
        fd = os.open("/", flags)
        acquired_fds.append(fd)
        fst = os.fstat(fd)
        role = _role_for_index(0, len(root_parts))
        _verify_node(st, fst, role)
        chain.append(_ChainNode(None, "/", fd, fst.st_dev, fst.st_ino, fst.st_mode, fst.st_uid, role))

        parent_fd = fd
        for idx, comp in enumerate(comps):
            node_idx = idx + 1
            role = _role_for_index(node_idx, len(root_parts))
            st = os.stat(comp, dir_fd=parent_fd, follow_symlinks=False)
            fd = os.open(comp, flags, dir_fd=parent_fd)
            acquired_fds.append(fd)
            fst = os.fstat(fd)
            _verify_node(st, fst, role)
            chain.append(_ChainNode(parent_fd, comp, fd, fst.st_dev, fst.st_ino, fst.st_mode, fst.st_uid, role))
            parent_fd = fd

        _revalidate_chain(chain)
    except Exception:
        _close_all(acquired_fds)
        raise ProtectedMainPathGuardError("invalid protected main path") from None
    except BaseException:
        _close_all(acquired_fds)
        raise

    yielded_fd = chain[-1].fd
    body_exc = None
    try:
        yield yielded_fd
    except BaseException as exc:
        body_exc = exc

    val_ok = True
    try:
        _revalidate_chain(chain)
    except Exception:
        val_ok = False
    finally:
        close_ok = _close_all(acquired_fds)

    if body_exc is not None:
        raise body_exc
    if not val_ok or not close_ok:
        raise ProtectedMainPathGuardError("invalid protected main path") from None
