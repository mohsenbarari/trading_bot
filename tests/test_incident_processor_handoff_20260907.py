import copy
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location(
    "processor_handoff",
    Path(__file__).resolve().parents[1] / "scripts/incident_processor_handoff_20260907.py",
)
handoff = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handoff)


class ProcessorHandoffGuards(unittest.TestCase):
    def prior(self):
        return ({'Image':handoff.OLD_IMAGE,'RestartCount':34,
                 'State':{'Status':'restarting','ExitCode':1,'OOMKilled':False}},
                {'release_sha':handoff.OLD,'schema':'market_processor/4.0',
                 'mode':'live','shadow_only':True,'status':'live-shadow-ready',
                 'updated_at_utc':datetime.fromtimestamp(time.time()-1800,timezone.utc).isoformat(),
                 'counters':{'archive_rejected':0}})

    def test_known_degraded_prior_can_be_repaired_without_claiming_health(self):
        with patch.object(handoff,'KNOWN_DEGRADED_RELEASE',handoff.OLD):
            result=handoff.validate_prior(*self.prior())
        self.assertTrue(result['degraded'])
        self.assertEqual(result['restart_count'],34)

    def test_follow_up_requires_healthy_prior_instead_of_reusing_degraded_waiver(self):
        old,health=self.prior()
        with self.assertRaisesRegex(RuntimeError,'prior_failure_not_known_restart_loop'):
            handoff.validate_prior(old,health)
        old['RestartCount']=0
        old['State'].update(Status='running',ExitCode=0)
        health['updated_at_utc']=datetime.fromtimestamp(time.time()-1,timezone.utc).isoformat()
        self.assertFalse(handoff.validate_prior(old,health)['degraded'])

    def test_unknown_old_image_failure_or_contract_is_refused(self):
        for change in ('image','exit','oom','release','schema'):
            old,health=self.prior()
            if change=='image': old['Image']='unapproved-image'
            elif change=='exit': old['State']['ExitCode']=137
            elif change=='oom': old['State']['OOMKilled']=True
            elif change=='release': health['release_sha']='unapproved-release'
            else: health['schema']='unknown'
            with self.subTest(change=change),self.assertRaises(RuntimeError):
                handoff.validate_prior(old,health)

    def configs(self):
        old = {
            "services": {
                handoff.ROLE: {
                    "image": handoff.OLD_IMAGE,
                    "restart": "unless-stopped",
                    "environment": {"MARKET_PIPELINE_RELEASE_SHA": handoff.OLD,
                                    "MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE":"500"},
                    "labels": {"org.opencontainers.image.revision": handoff.OLD},
                    "volumes": [{"source": "/retained/state", "target": "/state"}],
                    "mem_limit": "384m",
                },
                "market-fact-sync-worker": {"image": "untouched-sender"},
                "market-capture-account2": {"image": "untouched-capture"},
            },
            "secrets": {"mounted-key": {"file": "/retained/key"}},
            "networks": {"private": {"internal": True}},
        }
        new = copy.deepcopy(old)
        target = new["services"][handoff.ROLE]
        target["image"] = handoff.NEW_IMAGE
        target["restart"] = "unless-stopped"
        target["environment"]["MARKET_PIPELINE_RELEASE_SHA"] = handoff.NEW
        target["environment"]["MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE"] = handoff.EXPORT_LIMIT
        target["labels"]["org.opencontainers.image.revision"] = handoff.NEW
        return old, new

    def test_only_exact_processor_delta_is_allowed(self):
        handoff.validate_config(*self.configs())

    def test_rollback_preserves_existing_export_capacity_override(self):
        prior = handoff.compose()
        candidate = handoff.compose(True)
        self.assertIn(
            '/srv/trading-bot/incident-recovery/20260907-processor-export-capacity/processor-export-capacity.override.json',
            prior,
        )
        self.assertEqual(candidate, prior + ['-f', str(handoff.OVERRIDE)])
        self.assertNotIn(str(handoff.OVERRIDE), prior)

    def test_image_only_followup_preserves_prior_capacity_and_restart_policy(self):
        old, new = self.configs()
        prior = old['services'][handoff.ROLE]
        target = new['services'][handoff.ROLE]
        self.assertEqual(prior['restart'], target['restart'])
        self.assertEqual(
            prior['environment']['MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE'],
            target['environment']['MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE'],
        )
        self.assertNotEqual(handoff.OLD_IMAGE, handoff.NEW_IMAGE)
        handoff.validate_config(old, new)

    def test_export_capacity_cannot_expand_beyond_exact_reviewed_bound(self):
        old,new=self.configs()
        new['services'][handoff.ROLE]['environment']['MARKET_PROCESSOR_MAX_FACT_EXPORTS_PER_CYCLE']='5000'
        with self.assertRaisesRegex(RuntimeError,'unexpected_target_config_drift'):
            handoff.validate_config(old,new)

    def test_both_capture_and_sender_are_protected(self):
        for role in ("market-fact-sync-worker", "market-capture-account2"):
            with self.subTest(role=role):
                old, new = self.configs()
                new["services"][role]["image"] = "changed"
                with self.assertRaisesRegex(RuntimeError, "unrelated_service_drift"):
                    handoff.validate_config(old, new)

    def test_processor_mount_environment_and_resource_drift_are_rejected(self):
        for field, value in (("volumes", []), ("mem_limit", "4g"),
                             ("environment", {}), ("restart", "always")):
            with self.subTest(field=field):
                old, new = self.configs()
                new["services"][handoff.ROLE][field] = value
                with self.assertRaisesRegex(RuntimeError, "unexpected_target_config_drift"):
                    handoff.validate_config(old, new)

    def test_secrets_and_networks_are_protected(self):
        for field in ("secrets", "networks"):
            with self.subTest(field=field):
                old, new = self.configs()
                new[field] = {}
                with self.assertRaisesRegex(RuntimeError, "infrastructure_config_drift"):
                    handoff.validate_config(old, new)

    def test_service_addition_is_rejected(self):
        old, new = self.configs()
        new["services"]["new-owner"] = {}
        with self.assertRaisesRegex(RuntimeError, "service_set_drift"):
            handoff.validate_config(old, new)

    def test_parent_lock_cannot_have_two_owners_and_bytes_are_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.lock"
            handoff.atomic(path, {"retain": True})
            before = path.read_bytes()
            with handoff.held(path, os.getuid()):
                with self.assertRaises(BlockingIOError):
                    with handoff.held(path, os.getuid()):
                        pass
            self.assertEqual(path.read_bytes(), before)

    def test_hardlinks_and_symlinks_cannot_substitute_owner_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owner.lock"
            handoff.atomic(path, {"retain": True})
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaisesRegex(RuntimeError, "symlink_path"):
                with handoff.held(link, os.getuid()):
                    pass
            link.unlink()
            os.link(path, link)
            with self.assertRaisesRegex(RuntimeError, "unsafe_file_metadata"):
                with handoff.held(path, os.getuid()):
                    pass


if __name__ == "__main__":
    unittest.main()
