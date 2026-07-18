import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
HELPER = (
    ROOT
    / "deploy"
    / "writer-witness"
    / "writer-witness-matrix-campaign.py"
)
RUNNER = ROOT / "scripts" / "run_writer_witness_real_host_matrix.py"
COMMIT = "a" * 40
NONCE = "b" * 32
PREFLIGHT = "c" * 64


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


campaign = load_module("writer_witness_matrix_campaign_test", HELPER)


class WriterWitnessMatrixCampaignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="wwm-campaign-")
        self.base = Path(self.temporary.name)
        self.state_root = self.base / "state"
        self.not_after = (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat().replace("+00:00", "Z")

    def tearDown(self):
        self.temporary.cleanup()

    def identity(self, tag: str = "wwm_0123456789ab", scenario: str = "RH-001"):
        return {
            "tag": tag,
            "expected_commit": COMMIT,
            "scenario": scenario,
            "not_after": self.not_after,
        }

    def run_cli(
        self,
        command: str,
        *,
        tag: str = "wwm_0123456789ab",
        scenario: str = "RH-001",
        failpoint: str | None = None,
        nonce: str | None = None,
        preflight: str | None = None,
        expect: str | None = None,
        check: bool = True,
        state_root: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "-X",
            "utf8",
            "-X",
            "pycache_prefix=/dev/null",
            str(HELPER),
            "--test-mode",
            "--state-root",
            str(state_root or self.state_root),
        ]
        if failpoint:
            arguments.extend(("--test-failpoint", failpoint))
        arguments.extend(
            (
                command,
                "--tag",
                tag,
                "--expected-commit",
                COMMIT,
                "--scenario",
                scenario,
                "--not-after",
                self.not_after,
            )
        )
        if nonce is not None:
            arguments.extend(("--authorization-nonce", nonce))
        if preflight is not None:
            arguments.extend(("--preflight-sha256", preflight))
        if expect is not None:
            arguments.extend(("--expect", expect))
        completed = subprocess.run(arguments, capture_output=True, text=True)
        if check and completed.returncode != 0:
            self.fail(completed.stderr)
        return completed

    def test_claim_assert_and_release_use_only_complete_regular_records(self):
        identity = self.identity()
        with campaign.CampaignStore(self.state_root) as store:
            claimed = store.claim(**identity)
            self.assertEqual(claimed["status"], "claimed")
            self.assertEqual(store.claim(**identity)["status"], "already_claimed")
            self.assertEqual(
                store.assert_state(**identity, expect="active")["expected_state"],
                "active",
            )

        active = self.state_root / "active.json"
        metadata = active.lstat()
        self.assertTrue(stat.S_ISREG(metadata.st_mode))
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(metadata.st_nlink, 1)
        self.assertFalse((self.state_root / "active").exists())

        with campaign.CampaignStore(self.state_root) as store:
            with self.assertRaisesRegex(campaign.CampaignError, "different campaign identity"):
                store.assert_state(
                    tag=identity["tag"],
                    expected_commit="d" * 40,
                    scenario=identity["scenario"],
                    not_after=identity["not_after"],
                    expect="active",
                )
            self.assertEqual(store.release(**identity)["status"], "released")
            self.assertEqual(store.release(**identity)["status"], "already_released")
            store.assert_state(**identity, expect="released")
            with self.assertRaisesRegex(campaign.CampaignError, "cannot be claimed again"):
                store.claim(**identity)

        self.assertFalse(active.exists())
        tombstone = self.state_root / "releases" / f"{identity['tag']}.json"
        self.assertTrue(tombstone.is_file())
        self.assertEqual(stat.S_IMODE(tombstone.stat().st_mode), 0o600)

    def test_server_clock_expiry_blocks_authorization_but_not_cleanup_release(self):
        identity = self.identity()
        with campaign.CampaignStore(self.state_root) as store:
            store.claim(**identity)

        real_datetime = datetime

        class ExpiredDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = real_datetime.now(timezone.utc) + timedelta(hours=2)
                return value if tz is not None else value.replace(tzinfo=None)

        with mock.patch.object(campaign, "datetime", ExpiredDateTime):
            with campaign.CampaignStore(self.state_root) as store:
                with self.assertRaisesRegex(campaign.CampaignError, "has expired"):
                    store.assert_state(**identity, expect="active")
                store.assert_state(**identity, expect="active-cleanup")
                with self.assertRaisesRegex(campaign.CampaignError, "has expired"):
                    store.consume(
                        **identity,
                        authorization_nonce=NONCE,
                        preflight_sha256=PREFLIGHT,
                    )
                self.assertEqual(store.release(**identity)["status"], "released")

    def test_claim_caps_campaign_to_exact_900_server_seconds(self):
        server_now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)

        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return server_now if tz is not None else server_now.replace(tzinfo=None)

        accepted = self.identity(tag="wwm_111111111111")
        accepted["not_after"] = (
            server_now + timedelta(seconds=900)
        ).isoformat().replace("+00:00", "Z")
        rejected = self.identity(tag="wwm_222222222222")
        rejected["not_after"] = (
            server_now + timedelta(seconds=901)
        ).isoformat().replace("+00:00", "Z")
        with mock.patch.object(campaign, "datetime", FrozenDateTime):
            with campaign.CampaignStore(self.base / "accepted") as store:
                self.assertEqual(store.claim(**accepted)["status"], "claimed")
            with campaign.CampaignStore(self.base / "rejected") as store:
                with self.assertRaisesRegex(campaign.CampaignError, "900 seconds"):
                    store.claim(**rejected)

    def test_claim_hard_kill_before_and_after_atomic_publication_is_recoverable(self):
        before_root = self.base / "claim-before"
        before = self.run_cli(
            "claim",
            failpoint="claim_before_publish",
            state_root=before_root,
            check=False,
        )
        self.assertEqual(before.returncode, 91)
        self.assertFalse((before_root / "active.json").exists())
        self.assertFalse((before_root / "active").exists())
        self.assertEqual(len(list(before_root.glob(".campaign-write.*.tmp"))), 1)
        retried = self.run_cli("claim", state_root=before_root)
        self.assertEqual(json.loads(retried.stdout)["status"], "claimed")
        self.assertEqual(list(before_root.glob(".campaign-write.*.tmp")), [])

        after_root = self.base / "claim-after"
        after = self.run_cli(
            "claim",
            failpoint="claim_after_publish",
            state_root=after_root,
            check=False,
        )
        self.assertEqual(after.returncode, 92)
        self.assertTrue((after_root / "active.json").is_file())
        self.assertFalse((after_root / "active").exists())
        retried = self.run_cli("claim", state_root=after_root)
        self.assertEqual(json.loads(retried.stdout)["status"], "already_claimed")

    def test_release_hard_kill_after_atomic_rename_reconciles_from_tombstone(self):
        self.run_cli("claim")
        killed = self.run_cli(
            "release",
            failpoint="release_after_rename",
            check=False,
        )
        self.assertEqual(killed.returncode, 96)
        self.assertFalse((self.state_root / "active.json").exists())
        self.assertFalse((self.state_root / "active").exists())
        self.assertTrue(
            (self.state_root / "releases" / "wwm_0123456789ab.json").is_file()
        )
        retried = self.run_cli("release")
        self.assertEqual(json.loads(retried.stdout)["status"], "already_released")
        self.run_cli("assert", expect="released")

    def test_post_publication_failpoints_follow_parent_fsync(self):
        publish_root = self.base / "publish-order"
        publish_root.mkdir(mode=0o700)
        target = publish_root / "record.json"
        events: list[tuple[str, str]] = []

        def record_fsync(path: Path) -> None:
            events.append(("fsync", str(path)))

        def record_kill(_requested: str | None, point: str, _status: int) -> None:
            events.append(("kill", point))

        with (
            mock.patch.object(campaign, "_fsync_directory", side_effect=record_fsync),
            mock.patch.object(campaign, "_kill_at", side_effect=record_kill),
        ):
            campaign._publish_record(
                target,
                {"complete": True},
                uid=os.geteuid(),
                gid=os.getegid(),
                before_point="before_publish",
                after_point="after_publish",
            )
        self.assertTrue(target.is_file())
        self.assertLess(
            events.index(("fsync", str(publish_root))),
            events.index(("kill", "after_publish")),
        )

        release_root = self.base / "release-order"
        identity = self.identity()
        with campaign.CampaignStore(release_root) as store:
            store.claim(**identity)
            events.clear()
            with (
                mock.patch.object(campaign, "_fsync_directory", side_effect=record_fsync),
                mock.patch.object(campaign, "_kill_at", side_effect=record_kill),
            ):
                store.release(**identity)
        release_parent = release_root / "releases"
        kill_index = events.index(("kill", "release_after_rename"))
        self.assertLess(events.index(("fsync", str(release_parent))), kill_index)
        self.assertLess(events.index(("fsync", str(release_root))), kill_index)

    def test_new_state_and_managed_directory_names_are_parent_fsynced(self):
        existing_parent = self.base / "anchor"
        existing_parent.mkdir(mode=0o700)
        state_root = existing_parent / "nested" / "state"
        original_fsync = campaign._fsync_directory
        synced: list[Path] = []

        def tracked_fsync(path: Path) -> None:
            synced.append(path)
            original_fsync(path)

        with mock.patch.object(campaign, "_fsync_directory", side_effect=tracked_fsync):
            with campaign.CampaignStore(state_root):
                pass

        # nested is anchored in the pre-existing parent, state in nested, and
        # every fixed managed directory is anchored in the state root.
        self.assertIn(existing_parent, synced)
        self.assertIn(existing_parent / "nested", synced)
        self.assertGreaterEqual(synced.count(state_root), len(campaign.MANAGED_DIRECTORIES))

    def test_structured_inspect_and_absent_assertion_do_not_parse_text(self):
        first = self.identity("wwm_111111111111", "RH-001")
        second = self.identity("wwm_222222222222", "RH-002")
        with campaign.CampaignStore(self.state_root):
            pass
        absent = self.run_cli(
            "inspect",
            tag=first["tag"],
            scenario=first["scenario"],
        )
        self.assertEqual(json.loads(absent.stdout)["state"], "absent")
        self.run_cli(
            "assert",
            tag=first["tag"],
            scenario=first["scenario"],
            expect="absent",
        )

        self.run_cli("claim", tag=first["tag"], scenario=first["scenario"])
        exact = self.run_cli(
            "inspect",
            tag=first["tag"],
            scenario=first["scenario"],
        )
        self.assertEqual(json.loads(exact.stdout)["state"], "active_exact")
        foreign = self.run_cli(
            "inspect",
            tag=second["tag"],
            scenario=second["scenario"],
        )
        foreign_payload = json.loads(foreign.stdout)
        self.assertEqual(foreign_payload["state"], "active_foreign")
        self.assertEqual(foreign_payload["active_relation"], "foreign")
        not_absent = self.run_cli(
            "assert",
            tag=second["tag"],
            scenario=second["scenario"],
            expect="absent",
            check=False,
        )
        self.assertEqual(not_absent.returncode, 1)
        self.assertIn("active_foreign", not_absent.stderr)

        self.run_cli("release", tag=first["tag"], scenario=first["scenario"])
        released = self.run_cli(
            "inspect",
            tag=first["tag"],
            scenario=first["scenario"],
        )
        self.assertEqual(json.loads(released.stdout)["state"], "released_exact")
        released_foreign = self.run_cli(
            "inspect",
            tag=first["tag"],
            scenario="RH-005",
        )
        self.assertEqual(
            json.loads(released_foreign.stdout)["state"],
            "released_foreign",
        )
        self.run_cli("claim", tag=second["tag"], scenario=second["scenario"])
        released_with_foreign_active = self.run_cli(
            "inspect",
            tag=first["tag"],
            scenario=first["scenario"],
        )
        combined = json.loads(released_with_foreign_active.stdout)
        self.assertEqual(combined["state"], "released_exact")
        self.assertEqual(combined["active_relation"], "foreign")
        self.assertEqual(combined["release_relation"], "exact")

    def test_inspect_and_assert_are_strictly_read_only_and_preserve_crash_residue(self):
        identity = self.identity()
        with campaign.CampaignStore(self.state_root):
            pass

        def snapshot() -> dict[str, tuple[int, int, int, int, int, bytes | None]]:
            result = {}
            for path in sorted((self.state_root, *self.state_root.rglob("*"))):
                metadata = path.lstat()
                result[str(path.relative_to(self.state_root))] = (
                    metadata.st_mode,
                    metadata.st_uid,
                    metadata.st_gid,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    path.read_bytes() if stat.S_ISREG(metadata.st_mode) else None,
                )
            return result

        before = snapshot()
        inspected = self.run_cli("inspect")
        self.assertEqual(json.loads(inspected.stdout)["state"], "absent")
        self.run_cli("assert", expect="absent")
        self.assertEqual(snapshot(), before)

        residue = self.state_root / (".campaign-write.123." + "d" * 32 + ".tmp")
        residue.write_text("durable crash evidence\n", encoding="utf-8")
        residue.chmod(0o600)
        residue_before = residue.read_bytes()
        rejected = self.run_cli("inspect", check=False)
        self.assertEqual(rejected.returncode, 1)
        self.assertIn("requires recovery", rejected.stderr)
        self.assertEqual(residue.read_bytes(), residue_before)

        missing = self.base / "missing-read-only-root"
        rejected_missing = self.run_cli(
            "inspect",
            state_root=missing,
            check=False,
        )
        self.assertNotEqual(rejected_missing.returncode, 0)
        self.assertFalse(missing.exists())

    def test_consume_resumes_after_each_durable_hard_kill_boundary(self):
        failpoints = (
            ("consume_after_intent", 93),
            ("consume_after_approval", 94),
            ("consume_after_preflight", 95),
        )
        for index, (failpoint, expected_status) in enumerate(failpoints, start=1):
            with self.subTest(failpoint=failpoint):
                tag = f"wwm_{index:012x}"
                scenario = f"RH-{index:03d}"
                nonce = f"{index:032x}"
                preflight = f"{index:064x}"
                state_root = self.base / f"consume-{index}"
                self.run_cli(
                    "claim",
                    tag=tag,
                    scenario=scenario,
                    state_root=state_root,
                )
                killed = self.run_cli(
                    "consume",
                    tag=tag,
                    scenario=scenario,
                    failpoint=failpoint,
                    nonce=nonce,
                    preflight=preflight,
                    state_root=state_root,
                    check=False,
                )
                self.assertEqual(killed.returncode, expected_status)
                resumed = self.run_cli(
                    "consume",
                    tag=tag,
                    scenario=scenario,
                    nonce=nonce,
                    preflight=preflight,
                    state_root=state_root,
                )
                self.assertIn(
                    json.loads(resumed.stdout)["status"],
                    {"consumed", "already_consumed"},
                )
                repeated = self.run_cli(
                    "consume",
                    tag=tag,
                    scenario=scenario,
                    nonce=nonce,
                    preflight=preflight,
                    state_root=state_root,
                )
                self.assertEqual(
                    json.loads(repeated.stdout)["status"],
                    "already_consumed",
                )
                for path in (
                    state_root / "authorization-intents" / f"{tag}.json",
                    state_root / "authorizations" / f"{tag}.json",
                    state_root / "consumed-approvals" / f"{nonce}.json",
                    state_root / "consumed-preflights" / f"{preflight}.json",
                ):
                    self.assertTrue(path.is_file(), path)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_each_partial_intent_reserves_both_values_across_campaigns(self):
        failpoints = (
            ("consume_after_intent", 93),
            ("consume_after_approval", 94),
            ("consume_after_preflight", 95),
        )
        for index, (failpoint, expected_status) in enumerate(failpoints, start=1):
            with self.subTest(failpoint=failpoint):
                state_root = self.base / f"global-reservation-{index}"
                first_tag = f"wwm_{index:012x}"
                nonce = f"{index:032x}"
                preflight = f"{index:064x}"
                self.run_cli(
                    "claim",
                    tag=first_tag,
                    scenario="RH-001",
                    state_root=state_root,
                )
                killed = self.run_cli(
                    "consume",
                    tag=first_tag,
                    scenario="RH-001",
                    failpoint=failpoint,
                    nonce=nonce,
                    preflight=preflight,
                    state_root=state_root,
                    check=False,
                )
                self.assertEqual(killed.returncode, expected_status)

                # Do not resume the first campaign here: every following
                # rejection must be proved solely by the partial durable state
                # left at this exact kill boundary.
                self.run_cli(
                    "release",
                    tag=first_tag,
                    scenario="RH-001",
                    state_root=state_root,
                )

                second_tag = f"wwm_{index + 3:012x}"
                self.run_cli(
                    "claim",
                    tag=second_tag,
                    scenario="RH-002",
                    state_root=state_root,
                )
                reused_nonce = self.run_cli(
                    "consume",
                    tag=second_tag,
                    scenario="RH-002",
                    nonce=nonce,
                    preflight=f"{index + 100:064x}",
                    state_root=state_root,
                    check=False,
                )
                self.assertEqual(reused_nonce.returncode, 1)
                self.assertIn("different campaign identity", reused_nonce.stderr)
                self.assertFalse(
                    (
                        state_root
                        / "authorization-intents"
                        / f"{second_tag}.json"
                    ).exists()
                )
                self.run_cli(
                    "release",
                    tag=second_tag,
                    scenario="RH-002",
                    state_root=state_root,
                )

                third_tag = f"wwm_{index + 6:012x}"
                self.run_cli(
                    "claim",
                    tag=third_tag,
                    scenario="RH-003",
                    state_root=state_root,
                )
                reused_preflight = self.run_cli(
                    "consume",
                    tag=third_tag,
                    scenario="RH-003",
                    nonce=f"{index + 100:032x}",
                    preflight=preflight,
                    state_root=state_root,
                    check=False,
                )
                self.assertEqual(reused_preflight.returncode, 1)
                self.assertIn("different campaign identity", reused_preflight.stderr)
                self.assertFalse(
                    (
                        state_root
                        / "authorization-intents"
                        / f"{third_tag}.json"
                    ).exists()
                )
                self.run_cli(
                    "release",
                    tag=third_tag,
                    scenario="RH-003",
                    state_root=state_root,
                )

                fourth_tag = f"wwm_{index + 9:012x}"
                self.run_cli(
                    "claim",
                    tag=fourth_tag,
                    scenario="RH-004",
                    state_root=state_root,
                )
                reused_pair = self.run_cli(
                    "consume",
                    tag=fourth_tag,
                    scenario="RH-004",
                    nonce=nonce,
                    preflight=preflight,
                    state_root=state_root,
                    check=False,
                )
                self.assertEqual(reused_pair.returncode, 1)
                self.assertIn("different campaign identity", reused_pair.stderr)

    def test_mixed_nonce_and_preflight_from_separate_partial_intents_fail_closed(self):
        for index, failpoint in enumerate(
            (
                "consume_after_intent",
                "consume_after_approval",
                "consume_after_preflight",
            ),
            start=1,
        ):
            with self.subTest(failpoint=failpoint):
                state_root = self.base / f"mixed-reservation-{index}"
                first = {
                    "tag": "wwm_111111111111",
                    "scenario": "RH-001",
                    "nonce": "1" * 32,
                    "preflight": "1" * 64,
                }
                second = {
                    "tag": "wwm_222222222222",
                    "scenario": "RH-002",
                    "nonce": "2" * 32,
                    "preflight": "2" * 64,
                }
                for values in (first, second):
                    self.run_cli(
                        "claim",
                        tag=values["tag"],
                        scenario=values["scenario"],
                        state_root=state_root,
                    )
                    killed = self.run_cli(
                        "consume",
                        tag=values["tag"],
                        scenario=values["scenario"],
                        failpoint=failpoint,
                        nonce=values["nonce"],
                        preflight=values["preflight"],
                        state_root=state_root,
                        check=False,
                    )
                    self.assertEqual(
                        killed.returncode,
                        {
                            "consume_after_intent": 93,
                            "consume_after_approval": 94,
                            "consume_after_preflight": 95,
                        }[failpoint],
                    )
                    self.run_cli(
                        "release",
                        tag=values["tag"],
                        scenario=values["scenario"],
                        state_root=state_root,
                    )

                mixed_cases = (
                    ("wwm_333333333333", "RH-003", first["nonce"], second["preflight"]),
                    ("wwm_444444444444", "RH-004", second["nonce"], first["preflight"]),
                )
                for tag, scenario, nonce, preflight in mixed_cases:
                    self.run_cli(
                        "claim",
                        tag=tag,
                        scenario=scenario,
                        state_root=state_root,
                    )
                    rejected = self.run_cli(
                        "consume",
                        tag=tag,
                        scenario=scenario,
                        nonce=nonce,
                        preflight=preflight,
                        state_root=state_root,
                        check=False,
                    )
                    self.assertEqual(rejected.returncode, 1)
                    self.assertIn("different campaign identity", rejected.stderr)
                    self.assertFalse(
                        (state_root / "authorization-intents" / f"{tag}.json").exists()
                    )
                    self.run_cli(
                        "release",
                        tag=tag,
                        scenario=scenario,
                        state_root=state_root,
                    )

    def test_nonce_and_preflight_are_globally_one_time_across_campaigns(self):
        first = self.identity("wwm_111111111111", "RH-001")
        with campaign.CampaignStore(self.state_root) as store:
            store.claim(**first)
            store.consume(
                **first,
                authorization_nonce=NONCE,
                preflight_sha256=PREFLIGHT,
            )
            store.release(**first)

            second = self.identity("wwm_222222222222", "RH-002")
            store.claim(**second)
            with self.assertRaisesRegex(campaign.CampaignError, "different campaign identity"):
                store.consume(
                    **second,
                    authorization_nonce=NONCE,
                    preflight_sha256="d" * 64,
                )
            store.release(**second)

            third = self.identity("wwm_333333333333", "RH-003")
            store.claim(**third)
            with self.assertRaisesRegex(campaign.CampaignError, "different campaign identity"):
                store.consume(
                    **third,
                    authorization_nonce="e" * 32,
                    preflight_sha256=PREFLIGHT,
                )

    def test_lock_and_legacy_state_are_fail_closed(self):
        with campaign.CampaignStore(self.state_root):
            pass
        lock = self.state_root / campaign.LOCK_NAME
        lock.unlink()
        foreign = self.base / "foreign-lock"
        foreign.write_text("foreign", encoding="utf-8")
        lock.symlink_to(foreign)
        with self.assertRaisesRegex(campaign.CampaignError, "lock cannot be securely opened"):
            with campaign.CampaignStore(self.state_root):
                pass
        self.assertEqual(foreign.read_text(encoding="utf-8"), "foreign")

        legacy_root = self.base / "legacy"
        legacy_active = legacy_root / "active"
        legacy_active.mkdir(mode=0o700, parents=True)
        legacy_root.chmod(0o700)
        with self.assertRaisesRegex(campaign.CampaignError, "legacy active"):
            with campaign.CampaignStore(legacy_root):
                pass

        wrong_mode_root = self.base / "wrong-mode"
        with campaign.CampaignStore(wrong_mode_root):
            pass
        (wrong_mode_root / campaign.LOCK_NAME).chmod(0o640)
        with self.assertRaisesRegex(campaign.CampaignError, "lock is not one owner-safe"):
            with campaign.CampaignStore(wrong_mode_root):
                pass

        hardlink_root = self.base / "hardlink"
        with campaign.CampaignStore(hardlink_root):
            pass
        lock = hardlink_root / campaign.LOCK_NAME
        os.link(lock, self.base / "second-lock-link")
        with self.assertRaisesRegex(campaign.CampaignError, "lock is not one owner-safe"):
            with campaign.CampaignStore(hardlink_root):
                pass

        fifo_root = self.base / "fifo-state"
        identity = self.identity()
        with campaign.CampaignStore(fifo_root):
            pass
        os.mkfifo(fifo_root / "active.json", mode=0o600)
        with campaign.CampaignStore(fifo_root) as store:
            with self.assertRaisesRegex(campaign.CampaignError, "owner-safe regular file"):
                store.assert_state(**identity, expect="active")

    def test_legacy_directory_consumption_cannot_be_reused(self):
        identity = self.identity()
        with campaign.CampaignStore(self.state_root) as store:
            store.claim(**identity)
            legacy = self.state_root / "consumed-approvals" / NONCE
            legacy.mkdir(mode=0o700)
            with self.assertRaisesRegex(campaign.CampaignError, "legacy global"):
                store.consume(
                    **identity,
                    authorization_nonce=NONCE,
                    preflight_sha256=PREFLIGHT,
                )

    def test_runner_zero_byte_journal_is_a_documented_followup_regression(self):
        runner = load_module("writer_witness_runner_zero_journal_test", RUNNER)
        journal_root = self.base / "runner-journals"
        journal_root.mkdir(mode=0o700)
        journal = journal_root / "wwm_0123456789ab.json"
        journal.touch(mode=0o600)

        # This captures finding 5 without changing the existing runner in this
        # isolated helper delta.  The follow-up fix should invert this contract:
        # initial journal publication must never expose this zero-byte state.
        with self.assertRaises(runner.MatrixError):
            runner.CampaignJournal.load(journal)


if __name__ == "__main__":
    unittest.main()
