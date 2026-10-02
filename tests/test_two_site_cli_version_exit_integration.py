"""Hermetic integration tests for caller reviewed CLI version proof on all exits."""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from typing import Any, Callable, Iterator

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")
import tests.test_p1_00_characterization_network_isolation as fence
fence.install_p1_00_network_fence()

from scripts.two_site_cli_artifact import (
    CliArtifactError,
    prepare_disposable_cli,
)
from scripts.two_site_cli_exit_pin import CliExitPinError
import scripts.two_site_proposal_runner as runner_mod
from tests.test_two_site_cli_cleanup import _fault_scope
from tests.test_two_site_cli_exit_support import (
    assert_real_lock_contended,
    owned_cli_temporaries,
)
from tests.test_two_site_inode_fixture import hold_original_inode

_ORIGINAL_TEMPDIR_CLS = tempfile.TemporaryDirectory
_UNSET = object()


def _create_cli_fixture(
    directory: pathlib.Path,
    *,
    name: str = "mock_caller_cli",
    version: str = "1.1.27",
    exit_code: int = 0,
) -> pathlib.Path:
    cli_path = directory / name
    script = (
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then\n'
        f"  printf '%s\\n' '{version}'\n"
        f"  exit {exit_code}\n"
        "fi\n"
        "exit 1\n"
    )
    cli_path.write_bytes(script.encode("utf-8"))
    os.chmod(cli_path, 0o500)
    return cli_path


@contextlib.contextmanager
def _scoped_caller_binding(
    *,
    expected_sha256: Any = _UNSET,
    expected_version: Any = _UNSET,
    cli_identity: Any = _UNSET,
) -> Iterator[None]:
    orig_sha = getattr(runner_mod, "EXPECTED_CLI_SHA256", _UNSET)
    orig_ver = getattr(runner_mod, "EXPECTED_CLI_VERSION", _UNSET)
    orig_cb = getattr(runner_mod, "cli_identity", _UNSET)

    if expected_sha256 is not _UNSET:
        runner_mod.EXPECTED_CLI_SHA256 = expected_sha256
    if expected_version is not _UNSET:
        runner_mod.EXPECTED_CLI_VERSION = expected_version

    if cli_identity is not _UNSET:
        if callable(cli_identity):
            def isolated_cb(p: pathlib.Path) -> Any:
                cur_td = tempfile.TemporaryDirectory
                tempfile.TemporaryDirectory = _ORIGINAL_TEMPDIR_CLS
                try:
                    return cli_identity(p)
                finally:
                    tempfile.TemporaryDirectory = cur_td
            runner_mod.cli_identity = isolated_cb
        else:
            runner_mod.cli_identity = cli_identity

    try:
        yield
    finally:
        if orig_sha is not _UNSET:
            runner_mod.EXPECTED_CLI_SHA256 = orig_sha
        elif hasattr(runner_mod, "EXPECTED_CLI_SHA256"):
            delattr(runner_mod, "EXPECTED_CLI_SHA256")

        if orig_ver is not _UNSET:
            runner_mod.EXPECTED_CLI_VERSION = orig_ver
        elif hasattr(runner_mod, "EXPECTED_CLI_VERSION"):
            delattr(runner_mod, "EXPECTED_CLI_VERSION")

        if orig_cb is not _UNSET:
            runner_mod.cli_identity = orig_cb
        elif hasattr(runner_mod, "cli_identity"):
            delattr(runner_mod, "cli_identity")


@contextlib.contextmanager
def _scoped_runner_globals(sha: str, ver: str) -> Iterator[None]:
    orig_sha = getattr(runner_mod, "EXPECTED_CLI_SHA256", _UNSET)
    orig_ver = getattr(runner_mod, "EXPECTED_CLI_VERSION", _UNSET)
    runner_mod.EXPECTED_CLI_SHA256 = sha
    runner_mod.EXPECTED_CLI_VERSION = ver
    try:
        yield
    finally:
        if orig_sha is not _UNSET:
            runner_mod.EXPECTED_CLI_SHA256 = orig_sha
        elif hasattr(runner_mod, "EXPECTED_CLI_SHA256"):
            delattr(runner_mod, "EXPECTED_CLI_SHA256")
        if orig_ver is not _UNSET:
            runner_mod.EXPECTED_CLI_VERSION = orig_ver
        elif hasattr(runner_mod, "EXPECTED_CLI_VERSION"):
            delattr(runner_mod, "EXPECTED_CLI_VERSION")


@contextlib.contextmanager
def _scoped_sys_module(name: str, mod: types.ModuleType | None) -> Iterator[None]:
    had_entry = name in sys.modules
    prev_entry = sys.modules.get(name)
    if mod is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = mod
    try:
        yield
    finally:
        if had_entry:
            sys.modules[name] = prev_entry
        else:
            sys.modules.pop(name, None)


@contextlib.contextmanager
def _scoped_main_attrs(
    *,
    file_path: Any = _UNSET,
    sha: Any = _UNSET,
    ver: Any = _UNSET,
    cb: Any = _UNSET,
) -> Iterator[None]:
    main_mod = sys.modules.get("__main__")
    orig_file = getattr(main_mod, "__file__", _UNSET) if main_mod is not None else _UNSET
    orig_sha = getattr(main_mod, "EXPECTED_CLI_SHA256", _UNSET) if main_mod is not None else _UNSET
    orig_ver = getattr(main_mod, "EXPECTED_CLI_VERSION", _UNSET) if main_mod is not None else _UNSET
    orig_cb = getattr(main_mod, "cli_identity", _UNSET) if main_mod is not None else _UNSET

    if main_mod is not None:
        if file_path is not _UNSET:
            main_mod.__file__ = file_path
        if sha is not _UNSET:
            main_mod.EXPECTED_CLI_SHA256 = sha
        if ver is not _UNSET:
            main_mod.EXPECTED_CLI_VERSION = ver
        if cb is not _UNSET:
            main_mod.cli_identity = cb

    try:
        yield
    finally:
        if main_mod is not None:
            if orig_file is not _UNSET:
                main_mod.__file__ = orig_file
            elif hasattr(main_mod, "__file__"):
                delattr(main_mod, "__file__")
            if orig_sha is not _UNSET:
                main_mod.EXPECTED_CLI_SHA256 = orig_sha
            elif hasattr(main_mod, "EXPECTED_CLI_SHA256"):
                delattr(main_mod, "EXPECTED_CLI_SHA256")
            if orig_ver is not _UNSET:
                main_mod.EXPECTED_CLI_VERSION = orig_ver
            elif hasattr(main_mod, "EXPECTED_CLI_VERSION"):
                delattr(main_mod, "EXPECTED_CLI_VERSION")
            if orig_cb is not _UNSET:
                main_mod.cli_identity = orig_cb
            elif hasattr(main_mod, "cli_identity"):
                delattr(main_mod, "cli_identity")


class TestTwoSiteCliVersionExitIntegration(unittest.TestCase):
    """Verification matrix for real version proof on all normal, error, and cancellation exits."""

    def test_scenario1_recognized_all_exits_single_probe_and_lock_contention(self) -> None:
        with owned_cli_temporaries() as outer:
            root = outer / "worktree_root"
            root.mkdir(parents=True, exist_ok=True)
            cli = _create_cli_fixture(outer, version="1.1.27")
            cli_sha = runner_mod.file_sha256(cli)
            lock_path = root / ".local/two-site-refactor/proposals/.runner.lock"

            saved_real_identity = runner_mod.cli_identity
            probe_calls: list[pathlib.Path] = []

            def spy_identity(target: pathlib.Path) -> dict[str, str]:
                probe_calls.append(target)
                self.assertTrue(lock_path.is_file())
                assert_real_lock_contended(lock_path)
                self.assertTrue(target.is_file())
                self.assertNotEqual(target, cli)
                return saved_real_identity(target)

            test_cases: list[tuple[str, BaseException | None]] = [
                ("normal", None),
                ("runtime_error", RuntimeError("test body failure")),
                ("proposal_error", runner_mod.ProposalError("custom proposal failure")),
                ("timeout", subprocess.TimeoutExpired(["fake"], 10.0)),
                ("keyboard_interrupt", KeyboardInterrupt("body cancel")),
                ("system_exit", SystemExit(42)),
                ("generator_exit", GeneratorExit()),
            ]

            real_unlink = os.unlink
            real_rmdir = os.rmdir

            for case_label, body_exc in test_cases:
                with self.subTest(case=case_label):
                    probe_calls.clear()
                    unlink_lock_checked = []
                    rmdir_lock_checked = []

                    def spy_unlink(p: Any, *args: Any, **kwargs: Any) -> Any:
                        p_str = os.fspath(p)
                        if cli.name in p_str and lock_path.is_file():
                            assert_real_lock_contended(lock_path)
                            unlink_lock_checked.append(True)
                        return real_unlink(p, *args, **kwargs)

                    def spy_rmdir(p: Any, *args: Any, **kwargs: Any) -> Any:
                        p_str = os.fspath(p)
                        if "disposable_cli_" in p_str and lock_path.is_file():
                            assert_real_lock_contended(lock_path)
                            rmdir_lock_checked.append(True)
                        return real_rmdir(p, *args, **kwargs)

                    with _scoped_caller_binding(
                        expected_sha256=cli_sha,
                        expected_version="1.1.27",
                        cli_identity=spy_identity,
                    ):
                        orig_bytes = cli.read_bytes()
                        disp_path_holder: list[pathlib.Path] = []

                        os.unlink = spy_unlink
                        os.rmdir = spy_rmdir
                        try:
                            raised_exc: BaseException | None = None
                            try:
                                with runner_mod.proposal_cli_context(root, cli=cli) as (
                                    local, disp, evidence
                                ):
                                    disp_path_holder.append(disp)
                                    self.assertEqual(evidence["source_path"], str(cli.resolve()))
                                    self.assertEqual(evidence["sha256"], cli_sha)
                                    self.assertEqual(len(probe_calls), 0)
                                    if body_exc is not None:
                                        raise body_exc
                            except BaseException as exc:
                                raised_exc = exc
                        finally:
                            os.unlink = real_unlink
                            os.rmdir = real_rmdir

                        if body_exc is None:
                            self.assertIsNone(raised_exc)
                        else:
                            self.assertIs(raised_exc, body_exc)

                        self.assertEqual(len(probe_calls), 1)
                        self.assertTrue(len(unlink_lock_checked) >= 1)
                        self.assertTrue(len(rmdir_lock_checked) >= 1)
                        for p in disp_path_holder:
                            self.assertFalse(p.exists())
                            self.assertFalse(p.parent.exists())

                        self.assertEqual(cli.read_bytes(), orig_bytes)
                        desc, _ = runner_mod.acquire_proposal_lock(root)
                        runner_mod.release_proposal_lock(desc)

    def test_scenario2_wrong_version_callback_errors_and_priorities(self) -> None:
        with owned_cli_temporaries() as outer:
            root = outer / "worktree_root"
            root.mkdir(parents=True, exist_ok=True)
            lock_path = root / ".local/two-site-refactor/proposals/.runner.lock"

            wrong_cli = _create_cli_fixture(outer, name="wrong_cli", version="9.9.99")
            wrong_sha = runner_mod.file_sha256(wrong_cli)

            for body_exc in (None, RuntimeError("ordinary body error")):
                with self.subTest(subcase="wrong_version_stdout", body_exc=body_exc):
                    with _scoped_caller_binding(
                        expected_sha256=wrong_sha,
                        expected_version="1.1.27",
                        cli_identity=runner_mod.cli_identity,
                    ):
                        with self.assertRaises(CliArtifactError) as ctx:
                            with runner_mod.proposal_cli_context(root, cli=wrong_cli):
                                if body_exc is not None:
                                    raise body_exc
                        self.assertEqual(
                            str(ctx.exception),
                            "Disposable CLI identity validation failed",
                        )

            good_cli = _create_cli_fixture(outer, name="good_cli", version="1.1.27")
            good_sha = runner_mod.file_sha256(good_cli)

            for probe_err in (
                runner_mod.ProposalError("probe failed"),
                subprocess.TimeoutExpired(["cli"], 10.0),
            ):
                def failing_probe(_p: pathlib.Path) -> dict[str, str]:
                    raise probe_err

                with self.subTest(subcase="probe_ordinary_failure", err=type(probe_err)):
                    with _scoped_caller_binding(
                        expected_sha256=good_sha,
                        expected_version="1.1.27",
                        cli_identity=failing_probe,
                    ):
                        with self.assertRaises(CliArtifactError) as ctx:
                            with runner_mod.proposal_cli_context(root, cli=good_cli):
                                pass
                        self.assertEqual(
                            str(ctx.exception),
                            "Disposable CLI identity validation failed",
                        )

            cancel_triplet = [
                (KeyboardInterrupt, KeyboardInterrupt("probe cancel")),
                (SystemExit, SystemExit(42)),
                (GeneratorExit, GeneratorExit()),
            ]

            for cancel_cls, cancel_obj in cancel_triplet:
                probe_attempted = []

                def cancel_probe(_p: pathlib.Path) -> dict[str, str]:
                    probe_attempted.append(True)
                    raise cancel_obj

                with self.subTest(subcase="probe_cancellation", cancel=cancel_cls):
                    probe_attempted.clear()
                    with _scoped_caller_binding(
                        expected_sha256=good_sha,
                        expected_version="1.1.27",
                        cli_identity=cancel_probe,
                    ):
                        with self.assertRaises(cancel_cls) as ctx:
                            with runner_mod.proposal_cli_context(root, cli=good_cli):
                                pass
                        self.assertIs(ctx.exception, cancel_obj)
                        self.assertEqual(len(probe_attempted), 1)
                    desc, _ = runner_mod.acquire_proposal_lock(root)
                    runner_mod.release_proposal_lock(desc)

                with self.subTest(subcase="mixed_ordinary_body_and_probe_cancel", cancel=cancel_cls):
                    probe_attempted.clear()
                    with _scoped_caller_binding(
                        expected_sha256=good_sha,
                        expected_version="1.1.27",
                        cli_identity=cancel_probe,
                    ):
                        with self.assertRaises(cancel_cls) as ctx:
                            with runner_mod.proposal_cli_context(root, cli=good_cli):
                                raise RuntimeError("ordinary body error")
                        self.assertIs(ctx.exception, cancel_obj)
                        self.assertEqual(len(probe_attempted), 1)
                    desc, _ = runner_mod.acquire_proposal_lock(root)
                    runner_mod.release_proposal_lock(desc)

            body_cancels = [
                (KeyboardInterrupt, KeyboardInterrupt("body cancel wins")),
                (SystemExit, SystemExit(99)),
                (GeneratorExit, GeneratorExit()),
            ]

            for cancel_cls, body_cancel in body_cancels:
                probe_attempted = []

                def cancel_probe_too(_p: pathlib.Path) -> dict[str, str]:
                    probe_attempted.append(True)
                    raise KeyboardInterrupt("secondary probe cancel")

                with self.subTest(subcase="body_cancel_wins_priority", cancel=cancel_cls):
                    probe_attempted.clear()
                    with _scoped_caller_binding(
                        expected_sha256=good_sha,
                        expected_version="1.1.27",
                        cli_identity=cancel_probe_too,
                    ):
                        with self.assertRaises(cancel_cls) as ctx:
                            with runner_mod.proposal_cli_context(root, cli=good_cli):
                                raise body_cancel
                        self.assertIs(ctx.exception, body_cancel)
                        self.assertEqual(len(probe_attempted), 1)
                    desc, _ = runner_mod.acquire_proposal_lock(root)
                    runner_mod.release_proposal_lock(desc)

            with self.subTest(subcase="mixed_body_and_cleanup_fault"):
                with _scoped_caller_binding(
                    expected_sha256=good_sha,
                    expected_version="1.1.27",
                    cli_identity=runner_mod.cli_identity,
                ):
                    with _fault_scope(
                        source_name=good_cli.name,
                        unlink_exc=OSError(errno.EIO, "disk failure"),
                    ) as (records, _tds, _paths):
                        with self.assertRaises(CliArtifactError) as ctx:
                            with runner_mod.proposal_cli_context(root, cli=good_cli):
                                raise RuntimeError("body ordinary failure")
                        self.assertEqual(str(ctx.exception), "Disposable CLI cleanup failed")
                        self.assertEqual(records, ["unlink", "td_cleanup"])

    def test_scenario3_postprobe_mutation_and_inode_drift_rejected(self) -> None:
        with owned_cli_temporaries() as outer:
            root = outer / "worktree_root"
            root.mkdir(parents=True, exist_ok=True)
            cli = _create_cli_fixture(outer, version="1.1.27")
            cli_sha = runner_mod.file_sha256(cli)
            saved_real_identity = runner_mod.cli_identity

            def mutating_probe(path: pathlib.Path) -> dict[str, str]:
                mapping = saved_real_identity(path)
                with path.open("ab") as f:
                    f.write(b"# drift\n")
                return mapping

            with self.subTest(subcase="mutate_copy_bytes_after_probe"):
                with _scoped_caller_binding(
                    expected_sha256=cli_sha,
                    expected_version="1.1.27",
                    cli_identity=mutating_probe,
                ):
                    with self.assertRaises(CliArtifactError) as ctx:
                        with runner_mod.proposal_cli_context(root, cli=cli):
                            pass
                    self.assertEqual(
                        str(ctx.exception),
                        "Disposable CLI identity validation failed",
                    )

            def inode_drift_probe(path: pathlib.Path) -> dict[str, str]:
                mapping = saved_real_identity(path)
                with hold_original_inode(path):
                    data = path.read_bytes()
                    mode = os.stat(path).st_mode
                    path.unlink()
                    path.write_bytes(data)
                    os.chmod(path, mode)
                return mapping

            with self.subTest(subcase="same_bytes_new_inode_drift"):
                with _scoped_caller_binding(
                    expected_sha256=cli_sha,
                    expected_version="1.1.27",
                    cli_identity=inode_drift_probe,
                ):
                    with self.assertRaises(CliArtifactError) as ctx:
                        with runner_mod.proposal_cli_context(root, cli=cli):
                            pass
                    self.assertEqual(
                        str(ctx.exception),
                        "Disposable CLI identity validation failed",
                    )

            desc, _ = runner_mod.acquire_proposal_lock(root)
            runner_mod.release_proposal_lock(desc)

    def test_scenario4_unrecognized_invalid_caller_contract_and_binding_stability(self) -> None:
        with owned_cli_temporaries() as outer:
            root = outer / "worktree_root"
            root.mkdir(parents=True, exist_ok=True)
            cli = _create_cli_fixture(outer, version="1.1.27")
            cli_sha = runner_mod.file_sha256(cli)
            saved_real_identity = runner_mod.cli_identity

            probe_calls: list[pathlib.Path] = []

            def tracking_probe(p: pathlib.Path) -> dict[str, str]:
                probe_calls.append(p)
                return saved_real_identity(p)

            with self.subTest(subcase="unrecognized_fixture_no_probe"):
                with _scoped_caller_binding(
                    expected_sha256="0" * 64,
                    expected_version="1.1.27",
                    cli_identity=tracking_probe,
                ):
                    with runner_mod.proposal_cli_context(root, cli=cli) as (_loc, _d, ev):
                        self.assertEqual(ev["sha256"], cli_sha)
                    self.assertEqual(len(probe_calls), 0)

            for bad_ver, bad_cb in [
                ("", saved_real_identity),
                ("   ", saved_real_identity),
                (None, saved_real_identity),
                ("1.1.27", None),
                ("1.1.27", "not_callable"),
            ]:
                with self.subTest(subcase="invalid_contract", ver=bad_ver, cb=type(bad_cb)):
                    with _scoped_caller_binding(
                        expected_sha256=cli_sha,
                        expected_version=bad_ver,
                        cli_identity=bad_cb,
                    ):
                        with _fault_scope(source_name=cli.name) as (records, _tds, _paths):
                            with self.assertRaises(CliArtifactError) as ctx:
                                with prepare_disposable_cli(cli, expected_sha256=cli_sha):
                                    self.fail("Body must not be reached")
                            self.assertEqual(
                                str(ctx.exception),
                                "Disposable CLI identity validation failed",
                            )
                            self.assertEqual(records, ["unlink", "td_cleanup"])

            dummy_mod = types.ModuleType("two_site_proposal_runner")
            dummy_mod.EXPECTED_CLI_SHA256 = cli_sha
            dummy_mod.EXPECTED_CLI_VERSION = "2.0.0"
            dummy_mod.cli_identity = saved_real_identity
            with _scoped_sys_module("two_site_proposal_runner", dummy_mod):
                with _scoped_caller_binding(
                    expected_sha256=cli_sha,
                    expected_version="1.1.27",
                    cli_identity=saved_real_identity,
                ):
                    with self.assertRaises(CliArtifactError) as ctx:
                        with prepare_disposable_cli(cli, expected_sha256=cli_sha):
                            self.fail("Body must not be reached")
                    self.assertEqual(
                        str(ctx.exception),
                        "Disposable CLI identity validation failed",
                    )

            probe_invocations: list[str] = []
            fake_calls: list[pathlib.Path] = []

            def original_saved_probe(p: pathlib.Path) -> dict[str, str]:
                probe_invocations.append("saved")
                return saved_real_identity(p)

            def fake_replacement_probe(p: pathlib.Path) -> dict[str, str]:
                fake_calls.append(p)
                return {"path": str(p), "version": "fake", "sha256": "fake"}

            with self.subTest(subcase="saved_callback_pointer_stability"):
                with _scoped_caller_binding(
                    expected_sha256=cli_sha,
                    expected_version="1.1.27",
                    cli_identity=original_saved_probe,
                ):
                    with runner_mod.proposal_cli_context(root, cli=cli):
                        runner_mod.cli_identity = fake_replacement_probe
                self.assertEqual(probe_invocations, ["saved"])
                self.assertEqual(len(fake_calls), 0)

            sibling = pathlib.Path(runner_mod.__file__).resolve()

            with self.subTest(subcase="simulation_unrelated_main_ignored"):
                with _scoped_main_attrs(
                    file_path=str(outer / "other_main.py"),
                    sha=cli_sha,
                    ver="1.1.27",
                    cb=tracking_probe,
                ):
                    with _scoped_sys_module("scripts.two_site_proposal_runner", None):
                        probe_calls.clear()
                        with prepare_disposable_cli(cli, expected_sha256=cli_sha) as (_p, ev):
                            self.assertEqual(ev["sha256"], cli_sha)
                        self.assertEqual(len(probe_calls), 0)

            with self.subTest(subcase="simulation_directmain_positive"):
                with _scoped_main_attrs(
                    file_path=str(sibling),
                    sha=cli_sha,
                    ver="1.1.27",
                    cb=tracking_probe,
                ):
                    with _scoped_sys_module("scripts.two_site_proposal_runner", None):
                        with _scoped_runner_globals(cli_sha, "1.1.27"):
                            probe_calls.clear()
                            with prepare_disposable_cli(cli, expected_sha256=cli_sha):
                                pass
                            self.assertEqual(len(probe_calls), 1)

            with self.subTest(subcase="simulation_directmain_invalid_version_negative"):
                with _scoped_main_attrs(
                    file_path=str(sibling),
                    sha=cli_sha,
                    ver="",
                    cb=tracking_probe,
                ):
                    with _scoped_sys_module("scripts.two_site_proposal_runner", None):
                        with _scoped_runner_globals(cli_sha, "1.1.27"):
                            with self.assertRaises(CliArtifactError) as ctx:
                                with prepare_disposable_cli(cli, expected_sha256=cli_sha):
                                    self.fail("Body must not be reached")
                            self.assertEqual(
                                str(ctx.exception),
                                "Disposable CLI identity validation failed",
                            )

            with self.subTest(subcase="simulation_fallback_module_positive"):
                fallback_mod = types.ModuleType("two_site_proposal_runner")
                fallback_mod.EXPECTED_CLI_SHA256 = cli_sha
                fallback_mod.EXPECTED_CLI_VERSION = "1.1.27"
                fallback_mod.cli_identity = tracking_probe
                with _scoped_sys_module("scripts.two_site_proposal_runner", None):
                    with _scoped_sys_module("two_site_proposal_runner", fallback_mod):
                        with _scoped_runner_globals(cli_sha, "1.1.27"):
                            probe_calls.clear()
                            with prepare_disposable_cli(cli, expected_sha256=cli_sha):
                                pass
                            self.assertEqual(len(probe_calls), 1)
