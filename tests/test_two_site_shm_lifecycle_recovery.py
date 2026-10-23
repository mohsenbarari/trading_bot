"""800R owned synthetic SQLite/SHM regressions; no CLI or real sessions.

The historical C014 subpredicate is unknown. These tests prove the bounded
filesystem contract independently, not a retrospective failure attribution.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from scripts import two_site_proposal_runtime_profile as profile
from tests import test_two_site_profile_direct_agent_preparation as direct


class ShmLifecycleRecoveryContracts(unittest.TestCase):
    CID = "12345678-1234-1234-1234-123456789abc"
    FIELDS = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_nlink",
              "st_size", "st_mtime_ns", "st_ctime_ns")

    @contextmanager
    def owned(self, *, database=False):
        with tempfile.TemporaryDirectory(prefix="800r-owned-shm-") as temporary:
            directory = Path(temporary).resolve(strict=True)
            path = directory / (self.CID + ".db")
            if database:
                with closing(sqlite3.connect(path)) as connection:
                    connection.executescript(
                        "CREATE TABLE trajectory_meta(cascade_id TEXT, trajectory_type INTEGER, source INTEGER);"
                        "CREATE TABLE trajectory_metadata_blob(id TEXT, data BLOB);"
                        "CREATE TABLE steps(value INTEGER);"
                        "CREATE TABLE executor_metadata(value BLOB);")
                    connection.execute("INSERT INTO trajectory_meta VALUES(?,0,0)", (self.CID,))
                    connection.execute("INSERT INTO trajectory_metadata_blob VALUES('main',?)", (b"",))
                    connection.commit()
            shm = Path(str(path) + "-shm")
            shm.write_bytes(b"owned ephemeral index")
            shm.chmod(0o600)
            yield directory, path, shm

    def info(self, current, **changes):
        values = {name: getattr(current, name) for name in self.FIELDS}
        values.update(changes)
        return SimpleNamespace(**values)

    @contextmanager
    def lstat_boundary(self, shm, action, *, when=2):
        original = Path.lstat
        calls = []

        def inspect(path, *args, **kwargs):
            if path == shm:
                calls.append(path)
                if len(calls) == when:
                    return action(original, path, args, kwargs)
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "lstat", autospec=True, side_effect=inspect):
            yield calls

    def inspect(self, shm):
        return profile._inspect_shm(shm, time.monotonic() + 5)

    def pristine(self, directory, database):
        return profile._pristine_initialization_snapshot(
            directory, database.name, time.monotonic() + 5)

    def rejection(self, operation, reason):
        with self.assertRaises(profile.ProposalProfileError) as caught:
            operation()
        self.assertEqual(caught.exception.shm_veto_code, reason)
        return caught.exception

    def test_absent_or_stable_owned_shm_needs_no_content_read(self):
        with self.owned() as (_directory, _database, shm):
            with mock.patch.object(profile.os, "open", side_effect=AssertionError("SHM read")) as opened:
                with mock.patch.object(profile.hashlib, "sha256", side_effect=AssertionError("SHM hash")) as hashed:
                    self.assertIsNone(self.inspect(shm))
                    shm.unlink()
                    self.assertIsNone(self.inspect(shm))
            opened.assert_not_called()
            hashed.assert_not_called()

    def test_bounded_growth_between_lstats_retains_all_six_security_fields(self):
        with self.owned() as (_directory, _database, shm):
            before = shm.lstat()

            def grow(original, path, args, kwargs):
                path.write_bytes(b"x" * 65536)
                return original(path, *args, **kwargs)

            with self.lstat_boundary(shm, grow) as calls:
                self.assertIsNone(self.inspect(shm))
            after = shm.lstat()
            self.assertEqual(len(calls), 2)
            self.assertEqual(profile._identity9(before)[:6], profile._identity9(after)[:6])
            self.assertNotEqual(before.st_size, after.st_size)
            self.assertLessEqual(after.st_size, profile.MAX_DATABASE)

    def test_true_unlink_during_resolve_or_second_lstat_is_confirmed_absence(self):
        for boundary in ("resolve", "second_lstat"):
            with self.subTest(boundary=boundary), self.owned() as (_directory, _database, shm):
                if boundary == "resolve":
                    original = Path.resolve
                    seen = []

                    def resolve(path, *args, **kwargs):
                        if path == shm:
                            seen.append(path)
                            path.unlink()
                        return original(path, *args, **kwargs)

                    with mock.patch.object(Path, "resolve", autospec=True, side_effect=resolve):
                        self.assertIsNone(self.inspect(shm))
                    self.assertEqual(seen, [shm])
                else:
                    def unlink(original, path, args, kwargs):
                        path.unlink()
                        return original(path, *args, **kwargs)
                    with self.lstat_boundary(shm, unlink) as calls:
                        self.assertIsNone(self.inspect(shm))
                    self.assertEqual(len(calls), 3, "fresh lstat must confirm absence")
                self.assertFalse(os.path.lexists(shm))

    def test_initial_unsafe_objects_are_not_absence_even_if_they_could_vanish(self):
        for shape, reason in (("directory", "TYPE"), ("fifo", "TYPE"),
                              ("live_symlink", "TYPE"), ("dangling_symlink", "TYPE"),
                              ("hardlink", "LINKS"), ("oversize", "SIZE"),
                              ("foreign_owner", "OWNER")):
            with self.subTest(shape=shape), self.owned() as (directory, _database, shm):
                if shape == "directory":
                    shm.unlink(); shm.mkdir()
                elif shape == "fifo":
                    shm.unlink(); os.mkfifo(shm, 0o600)
                elif shape.endswith("symlink"):
                    target = directory / "owned-symlink-target"
                    if shape == "live_symlink":
                        target.write_bytes(b"synthetic")
                    shm.unlink(); shm.symlink_to(target)
                elif shape == "hardlink":
                    os.link(shm, directory / "owned-second-link")
                elif shape == "oversize":
                    with shm.open("r+b") as stream:
                        stream.truncate(profile.MAX_DATABASE + 1)
                if shape == "foreign_owner":
                    original = Path.lstat
                    seen = []
                    def foreign(path, *args, **kwargs):
                        info = original(path, *args, **kwargs)
                        if path == shm:
                            seen.append(path)
                            path.unlink()  # Unsafe observed owner is not excused by later absence.
                            return self.info(info, st_uid=info.st_uid + 1)
                        return info
                    with mock.patch.object(Path, "lstat", autospec=True, side_effect=foreign):
                        self.rejection(lambda: self.inspect(shm), reason)
                    self.assertEqual(seen, [shm])
                else:
                    self.rejection(lambda: self.inspect(shm), reason)

    def test_canonical_parent_confinement_rejects_directory_alias(self):
        with self.owned() as (directory, _database, shm):
            real = directory / "real-parent"; real.mkdir()
            moved = real / shm.name; shm.rename(moved)
            alias = directory / "alias-parent"; alias.symlink_to(real, target_is_directory=True)
            self.rejection(lambda: self.inspect(alias / shm.name), "PATH")

    def test_each_security_identity_field_and_size_limit_remain_authoritative(self):
        for field, reason in (("st_dev", "IDENTITY"), ("st_ino", "IDENTITY"),
                              ("st_mode", "IDENTITY"), ("st_uid", "OWNER"),
                              ("st_gid", "IDENTITY"), ("st_nlink", "LINKS"),
                              ("negative_size", "SIZE"), ("oversize", "SIZE")):
            for when in (1, 2):
                with self.subTest(field=field, observation=when), self.owned() as (_directory, _database, shm):
                    def mutate(original, path, args, kwargs):
                        current = original(path, *args, **kwargs)
                        if field in ("negative_size", "oversize"):
                            return self.info(current, st_size=-1 if field == "negative_size" else profile.MAX_DATABASE + 1)
                        changed = (current.st_mode ^ stat.S_IXUSR) if field == "st_mode" else getattr(current, field) + 1
                        return self.info(current, **{field: changed})
                    with self.lstat_boundary(shm, mutate, when=when):
                        self.rejection(lambda: self.inspect(shm), reason)

    def test_actual_replacement_inode_and_late_unsafe_object_still_reject(self):
        for replacement, reason in (("inode", "IDENTITY"), ("directory", "TYPE"),
                                    ("symlink", "TYPE"), ("hardlink", "LINKS")):
            with self.subTest(replacement=replacement), self.owned() as (directory, _database, shm):
                def replace(original, path, args, kwargs):
                    path.rename(directory / "old-owned-inode")
                    if replacement == "directory":
                        path.mkdir()
                    elif replacement == "symlink":
                        path.symlink_to(directory / "absent-target")
                    else:
                        path.write_bytes(b"replacement")
                        path.chmod(0o600)
                        if replacement == "hardlink":
                            os.link(path, directory / "new-second-link")
                    return original(path, *args, **kwargs)
                with self.lstat_boundary(shm, replace):
                    self.rejection(lambda: self.inspect(shm), reason)

    def test_disappearance_then_reappearance_cannot_hide_any_observed_object(self):
        for replacement, reason in (("regular", "REAPPEARED"), ("dangling_symlink", "TYPE")):
            with self.subTest(replacement=replacement), self.owned() as (directory, _database, shm):
                def race(original, path, args, kwargs):
                    path.unlink()
                    if replacement == "regular":
                        path.write_bytes(b"reappeared")
                    else:
                        path.symlink_to(directory / "missing-target")
                    raise FileNotFoundError("synthetic interrupted observation")
                with self.lstat_boundary(shm, race) as calls:
                    self.rejection(lambda: self.inspect(shm), reason)
                self.assertEqual(len(calls), 3)

    def test_permission_errors_are_not_swallowed_as_disappearance(self):
        with self.owned() as (_directory, _database, shm):
            def denied(_original, _path, _args, _kwargs):
                raise PermissionError("synthetic denied stat")
            with self.lstat_boundary(shm, denied):
                with self.assertRaises(PermissionError):
                    self.inspect(shm)

    def test_expired_pre_and_post_deadlines_reject_even_an_absent_shm(self):
        for initial_absent in (False, True):
            for ticks in ((3,), (0, 3)):
                with self.subTest(initial_absent=initial_absent, ticks=ticks), self.owned() as (_directory, _database, shm):
                    if initial_absent:
                        shm.unlink()
                    with mock.patch.object(profile.time, "monotonic", side_effect=ticks):
                        self.rejection(lambda: profile._inspect_shm(shm, 2), "BUDGET")

    def test_real_empty_sqlite_proof_unchanged_by_ephemeral_size_or_disappearance(self):
        for event in ("growth", "unlink"):
            with self.subTest(event=event), self.owned(database=True) as (directory, database, shm):
                before = self.pristine(directory, database)
                def change(original, path, args, kwargs):
                    if event == "growth":
                        path.write_bytes(b"g" * 65536)
                    else:
                        path.unlink()
                    return original(path, *args, **kwargs)
                with self.lstat_boundary(shm, change):
                    after = self.pristine(directory, database)
                self.assertEqual(before, after)
                self.assertEqual(after[1][2:], ([], []))

    def test_shm_specific_enum_propagates_at_before_and_after_sql_boundaries(self):
        for boundary, trigger in (("BEFORE", 1), ("AFTER", 3)):
            with self.subTest(boundary=boundary), self.owned(database=True) as (directory, database, shm):
                original = Path.lstat
                calls = []
                def owner(path, *args, **kwargs):
                    info = original(path, *args, **kwargs)
                    if path == shm:
                        calls.append(path)
                        if len(calls) == trigger:
                            return self.info(info, st_uid=info.st_uid + 1)
                    return info
                with mock.patch.object(Path, "lstat", autospec=True, side_effect=owner):
                    with self.assertRaises(profile.ProposalProfileError) as caught:
                        self.pristine(directory, database)
                self.assertEqual(caught.exception.shm_veto_code, "OWNER")
                self.assertEqual(caught.exception.pristine_veto_code, "SHM_" + boundary + "_OWNER")

    def test_unknown_or_legacy_shm_errors_keep_closed_contract_fallback(self):
        for detail in (None, "UNKNOWN", ["OWNER"], True):
            with self.subTest(detail_type=type(detail).__name__), self.owned(database=True) as (directory, database, _shm):
                error = profile.ProposalProfileError("synthetic legacy veto")
                error.shm_veto_code = detail
                with mock.patch.object(profile, "_inspect_shm", side_effect=error):
                    with self.assertRaises(profile.ProposalProfileError) as caught:
                        self.pristine(directory, database)
                self.assertIs(caught.exception, error)
                self.assertEqual(error.pristine_veto_code, "SHM_BEFORE_CONTRACT")

    def test_fixed_reason_reaches_original_first_error_and_never_starts_second_initializer(self):
        fixture = direct.EmptyStartupRecoveryContracts("runTest")
        original = profile._inspect_shm
        initialize = profile._initialize_profile_once
        inspections = []
        first_errors = []
        def observe_initializer(*args, **kwargs):
            try:
                return initialize(*args, **kwargs)
            except profile.ProposalProfileError as error:
                first_errors.append(error)
                raise
        def oversized(shm, deadline):
            inspections.append(shm)
            if len(inspections) == 4:
                with shm.open("wb") as stream:
                    stream.truncate(profile.MAX_DATABASE + 1)
            return original(shm, deadline)
        with (mock.patch.object(profile, "_inspect_shm", side_effect=oversized),
              mock.patch.object(profile, "_initialize_profile_once", side_effect=observe_initializer)):
            with fixture.lab() as trace:
                fixture.rejected(trace, 1)
                self.assertEqual(len(first_errors), 1)
                self.assertIs(trace.error, first_errors[0])
                self.assertIn("LAST_HINT_INITIAL_METADATA_EMPTY", str(trace.error))
                self.assertIn("phase=PRE_SECOND reason=PRISTINE_RECHECK_SHM_AFTER_SIZE attempts=1", str(trace.error))
                self.assertEqual(len(inspections), 4)
                self.assertEqual(len(trace.commands), 1)
                self.assertEqual(trace.writes, [])

    def test_nonzero_steps_or_executors_still_veto_real_sqlite(self):
        for table, reason in (("steps", "LOGICAL_STEPS_CONTRACT"),
                              ("executor_metadata", "LOGICAL_EXECUTORS_CONTRACT")):
            with self.subTest(table=table), self.owned(database=True) as (directory, database, _shm):
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute("INSERT INTO " + table + " VALUES(?)", (1,))
                    connection.commit()
                with self.assertRaises(profile.ProposalProfileError) as caught:
                    self.pristine(directory, database)
                self.assertEqual(caught.exception.pristine_veto_code, reason)

    def test_persisted_db_and_wal_drift_are_not_normalized_with_shm(self):
        for target, reason in (("database", "PERSISTED_DB_CONTRACT"), ("wal", "PERSISTED_WAL_CONTRACT")):
            with self.subTest(target=target), self.owned(database=True) as (directory, database, shm):
                original = profile._inspect_shm
                calls = []
                def mutate(path, deadline):
                    result = original(path, deadline)
                    calls.append(path)
                    if len(calls) == 2:
                        with (database if target == "database" else Path(str(database) + "-wal")).open("ab") as stream:
                            stream.write(b"owned synthetic mutation")
                    return result
                with mock.patch.object(profile, "_inspect_shm", side_effect=mutate):
                    with self.assertRaises(profile.ProposalProfileError) as caught:
                        self.pristine(directory, database)
                self.assertEqual(caught.exception.pristine_veto_code, reason)
                self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
