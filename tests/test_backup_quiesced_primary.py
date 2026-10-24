"""Additive PRIMARY contracts; only public temporary fixtures and named mocks."""
from contextlib import ExitStack
from copy import deepcopy
import ctypes
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest import mock

DENIALS = ExitStack()
for module, names in ((subprocess, ('Popen', 'run', 'call', 'check_call', 'check_output')),
        (socket, ('socket', 'create_connection', 'getaddrinfo')), (ctypes, ('CDLL',)),
        (os, ('system', 'popen', 'fork', 'posix_spawn', 'posix_spawnp'))):
    for name in names:
        if hasattr(module, name):
            DENIALS.enter_context(mock.patch.object(module, name, side_effect=AssertionError('ambient_execution_denied')))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests import test_backup_market_pipeline_archive as fixtures
from scripts import backup_market_pipeline_archive as B


class QuiescedPrimaryTests(unittest.TestCase):
    tearDown = fixtures.BackupMarketPipelineArchiveTests.tearDown
    _limits = fixtures.BackupMarketPipelineArchiveTests._limits

    def setUp(self):
        fixtures.BackupMarketPipelineArchiveTests.setUp(self)
        self.values.update(MARKET_PIPELINE_PROJECT_NAME='market-private-pipeline-primary',
            MARKET_PIPELINE_FEED_MODE='PRIVATE_PRIMARY', MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY='1',
            MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE='PRIVATE_PRIMARY')
        fixtures._write_env(self.env_file, self.values)
        pgdata = self.postgres_root/'pgdata'; pgdata.mkdir(mode=0o700)
        (pgdata/'PG_VERSION').write_text('15\n')
        self.source = {'container_id': 'e'*64, 'database': 'market_archive', 'database_size_bytes': 12345,
            'database_identity_sha256': 'f'*64, 'schema_versions': [1, 2], 'table_count': 1,
            'fact_count': 10, **fixtures._invariants(fact_count=10)}
        self.resources = {'container': 'market_pipeline_restore_'+'1'*16,
            'volume': 'market_pipeline_restore_'+'1'*16+'_data', 'label': 'market-pipeline-backup-'+'1'*16}
        self.limits = self._limits()
        self.common = dict(env_file=self.env_file, receipt=self.receipt, release_sha=fixtures.RELEASE_SHA,
            release_tree=fixtures.RELEASE_TREE, image_id=fixtures.IMAGE_ID,
            image_input_signature=fixtures.IMAGE_SIGNATURE, execution_limits=self.limits)

    def create(self, *, sources=None, restore=None):
        def dump(**arguments):
            self.assertIs(arguments['execution_limits'], self.limits)
            arguments['destination'].write_bytes(b'ordinary public synthetic custom dump')
            arguments['destination'].chmod(0o600)
        restored = {'status': 'PASS', 'cleanup_status': 'PASS', **B._invariant_view(self.source)}
        def restore_call(artifact, source, **kwargs):
            self.assertEqual(kwargs['resource_binding'], self.resources)
            self.assertIs(kwargs['execution_limits'], self.limits)
            self.assertEqual(os.fstat(kwargs['artifact_descriptor']).st_size, artifact.stat().st_size)
            return restore(artifact, source, **kwargs) if restore else deepcopy(restored)
        with mock.patch.object(B, '_running_project_services', return_value=['market-database']), \
                mock.patch.object(B, 'inspect_source_database', side_effect=sources or [deepcopy(self.source) for _ in range(4)]) as inspect, \
                mock.patch.object(B, '_write_dump', side_effect=dump), \
                mock.patch.object(B, '_dump_archive_is_valid', return_value=True), \
                mock.patch.object(B, 'restore_smoke', side_effect=restore_call):
            result = B.create_quiesced_primary_backup(**self.common, backup_dir=self.backup_dir,
                                                      restore_resource_binding=self.resources)
        self.assertTrue(all(call.kwargs['require_quiesce'] is True for call in inspect.call_args_list))
        self.assertTrue(all(call.kwargs['execution_limits'] is self.limits for call in inspect.call_args_list))
        return result

    def verify_arguments(self):
        return {**self.common, 'receipt_sha256': B.file_digest(self.receipt),
            'journal_sha256': B.file_digest(B._journal_path(self.backup_dir)), 'maximum_age_seconds': 3600}

    def test_real_create_and_strict_verify_use_owned_restore_and_bounded_current_sources(self):
        created = self.create()
        self.assertEqual(created['schema'], B.RECEIPT_SCHEMA)
        self.assertEqual(created['backup_mode'], 'quiesced')
        self.assertEqual(created['release_sha'], self.values['MARKET_PIPELINE_RELEASE_SHA'])
        self.assertEqual(created['image_id'], self.values['MARKET_PIPELINE_IMAGE'])
        journal = json.loads(B._journal_path(self.backup_dir).read_bytes())
        self.assertEqual(journal['schema'], B.JOURNAL_SCHEMA)
        self.assertEqual(journal['restore_resources'], self.resources)
        with mock.patch.object(B, 'inspect_source_database', return_value=deepcopy(self.source)) as inspect, \
                mock.patch.object(B, 'restore_smoke', side_effect=AssertionError('no_second_restore')):
            verified = B.verify_quiesced_primary_receipt(**self.verify_arguments())
        self.assertEqual(verified, created); self.assertEqual(inspect.call_count, 2)
        self.assertTrue(all(call.kwargs['require_quiesce'] is True for call in inspect.call_args_list))
        self.assertTrue(all(call.kwargs['execution_limits'] is self.limits for call in inspect.call_args_list))
        self.assertNotIn('fresh_restore_verified', verified)

    def test_running_writer_refuses_before_dump_restore_or_receipt(self):
        with mock.patch.object(B, '_running_project_services', return_value=['market-capture-account1', 'market-database']), \
                mock.patch.object(B, '_write_dump', side_effect=AssertionError('writer_must_refuse')), \
                mock.patch.object(B, 'restore_smoke', side_effect=AssertionError('writer_must_refuse')):
            with self.assertRaisesRegex(B.BackupError, 'writer_workloads_not_quiesced'):
                B.create_quiesced_primary_backup(**self.common, backup_dir=self.backup_dir,
                                                 restore_resource_binding=self.resources)
        self.assertFalse(self.receipt.exists())

    def test_create_requires_new_namespace_before_env_or_host_actions(self):
        self.backup_dir.mkdir(mode=0o700); (self.backup_dir/'old-public-artifact').write_bytes(b'old')
        with mock.patch.object(B, 'BoundRoleEnv', side_effect=AssertionError('no_env_adoption')):
            with self.assertRaisesRegex(B.BackupError, 'fresh_required'):
                B.create_quiesced_primary_backup(**self.common, backup_dir=self.backup_dir,
                                                 restore_resource_binding=self.resources)

    def test_invalid_limits_resources_and_identity_refuse_before_env(self):
        for limits, resources, identity in ((None, self.resources, {}), ({}, self.resources, {}),
                (self.limits, None, {}), (self.limits, {}, {}),
                (self.limits, self.resources, {'release_tree': True})):
            with self.subTest(limits=limits, resources=resources, identity=identity), \
                    mock.patch.object(B, 'BoundRoleEnv', side_effect=AssertionError('controls_before_env')):
                arguments = {**self.common, 'execution_limits': limits, **identity}
                with self.assertRaises(B.BackupError):
                    B.create_quiesced_primary_backup(**arguments, backup_dir=self.backup_dir,
                                                     restore_resource_binding=resources)

    def test_only_exact_primary_lane_and_configuration_identity_are_accepted(self):
        for changes in ({'MARKET_PIPELINE_RELEASE_SHA': '9'*40}, {'MARKET_PIPELINE_IMAGE': 'sha256:'+'9'*64},
                {'MARKET_PIPELINE_FEED_MODE': 'PRIVATE_SHADOW', 'MARKET_PIPELINE_ALLOW_PRIVATE_PRIMARY': '0',
                 'MARKET_PIPELINE_EXPECTED_SNAPSHOT_LANE': 'PRIVATE_SHADOW'}):
            fixtures._write_env(self.env_file, {**self.values, **changes})
            with self.subTest(changes=changes), mock.patch.object(B, '_running_project_services',
                    side_effect=AssertionError('identity_before_command')):
                with self.assertRaises(B.BackupError):
                    B.create_quiesced_primary_backup(**self.common, backup_dir=self.backup_dir,
                                                     restore_resource_binding=self.resources)

    def test_restore_timeout_stays_failure_and_does_not_emit_complete_receipt(self):
        def unknown(*args, **kwargs): raise B.BackupError('backup_command_timeout')
        with self.assertRaisesRegex(B.BackupError, 'backup_command_timeout'): self.create(restore=unknown)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(json.loads(B._journal_path(self.backup_dir).read_bytes())['status'], 'RESTORE_RUNNING')

    def test_create_rejects_row_schema_and_sequence_drift(self):
        for key, value in (('table_row_counts', {'market_facts': 11}),
                ('schema_catalog_sha256', '9'*64),
                ('sequence_values', {'market_facts_id_seq': {'last_value': 102, 'is_called': True}})):
            with self.subTest(key=key):
                changed = {**deepcopy(self.source), key: value}
                with self.assertRaisesRegex(B.BackupError, 'source_changed'):
                    self.create(sources=[deepcopy(self.source), changed])
                for path in self.backup_dir.iterdir(): path.unlink()

    def test_verify_rejects_current_row_schema_sequence_and_database_identity_drift(self):
        self.create()
        for key, value in (('table_row_counts', {'market_facts': 11}),
                ('schema_objects_sha256', '9'*64), ('container_id', '9'*64),
                ('sequence_values', {'market_facts_id_seq': {'last_value': 102, 'is_called': True}})):
            changed = {**deepcopy(self.source), key: value}
            with self.subTest(key=key), mock.patch.object(B, 'inspect_source_database', return_value=changed):
                with self.assertRaisesRegex(B.BackupError, 'current_source_mismatch'):
                    B.verify_quiesced_primary_receipt(**self.verify_arguments())

    def test_verify_checks_source_again_after_artifact_hash(self):
        self.create(); changed = deepcopy(self.source)
        changed['sequence_values']['market_facts_id_seq']['last_value'] = 102
        with mock.patch.object(B, 'inspect_source_database', side_effect=[deepcopy(self.source), changed]):
            with self.assertRaisesRegex(B.BackupError, 'current_source_mismatch'):
                B.verify_quiesced_primary_receipt(**self.verify_arguments())

    def test_verify_real_quiescence_guard_refuses_running_writer_before_metadata_commands(self):
        self.create()
        with mock.patch.object(B, '_running_project_services',
                return_value=['market-capture-account1', 'market-database']):
            with self.assertRaisesRegex(B.BackupError, 'writer_workloads_not_quiesced'):
                B.verify_quiesced_primary_receipt(**self.verify_arguments())

    def test_verify_rejects_resealed_stored_restore_row_drift(self):
        self.create()
        for path, field in ((self.receipt, 'restore_smoke'), (B._journal_path(self.backup_dir), 'restore_smoke')):
            value = json.loads(path.read_bytes())
            value[field]['fact_count'] = 11
            value[field]['table_row_counts']['market_facts'] = 11
            B._write_receipt(path, value)
        with mock.patch.object(B, 'inspect_source_database', side_effect=AssertionError('bad_evidence_before_source')):
            with self.assertRaisesRegex(B.BackupError, 'source_restore_metadata_invalid'):
                B.verify_quiesced_primary_receipt(**self.verify_arguments())

    def test_env_aba_during_owned_restore_refuses(self):
        original = self.env_file.read_bytes(); info = self.env_file.stat()
        def aba(*args, **kwargs):
            self.env_file.write_bytes(original+b'# ordinary public fixture\n'); self.env_file.write_bytes(original)
            os.utime(self.env_file, ns=(info.st_atime_ns, info.st_mtime_ns))
            return {'status': 'PASS', 'cleanup_status': 'PASS', **B._invariant_view(self.source)}
        with self.assertRaises(B.BackupError): self.create(restore=aba)
        self.assertFalse(self.receipt.exists())

    def test_verify_keeps_artifact_fd_and_detects_equal_byte_path_replacement(self):
        created = self.create(); artifact = Path(created['backup']['path']); calls = 0
        def inspect(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                replacement = artifact.with_suffix('.replacement'); replacement.write_bytes(artifact.read_bytes())
                replacement.chmod(0o600); replacement.replace(artifact)
            return deepcopy(self.source)
        with mock.patch.object(B, 'inspect_source_database', side_effect=inspect):
            with self.assertRaisesRegex(B.BackupError, 'artifact_drifted'):
                B.verify_quiesced_primary_receipt(**self.verify_arguments())

    def test_verify_keeps_control_fds_and_detects_receipt_aba(self):
        self.create(); calls = 0
        def inspect(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                original = self.receipt.read_bytes(); self.receipt.write_bytes(original)
            return deepcopy(self.source)
        with mock.patch.object(B, 'inspect_source_database', side_effect=inspect):
            with self.assertRaisesRegex(B.BackupError, 'evidence_identity_mismatch'):
                B.verify_quiesced_primary_receipt(**self.verify_arguments())

    def test_hot_legacy_and_pin_drift_refuse_before_env(self):
        self.create()
        original = {path: path.read_bytes() for path in (self.receipt, B._journal_path(self.backup_dir))}
        for mode in ('hot', 'legacy', 'pin-drift'):
            for path, raw in original.items(): path.write_bytes(raw)
            arguments = self.verify_arguments()
            if mode != 'pin-drift':
                for path in original:
                    value = json.loads(path.read_bytes())
                    if mode == 'hot': value['backup_mode'] = 'hot'
                    else: value['schema'] = (B.METADATA_LEGACY_RECEIPT_SCHEMA if path == self.receipt
                                             else B.METADATA_LEGACY_JOURNAL_SCHEMA)
                    B._write_receipt(path, value)
                arguments = self.verify_arguments()
            else: self.receipt.write_bytes(self.receipt.read_bytes()+b' ')
            with self.subTest(mode=mode), mock.patch.object(B, 'BoundRoleEnv', side_effect=AssertionError('mode_before_env')):
                with self.assertRaises(B.BackupError): B.verify_quiesced_primary_receipt(**arguments)

    def test_verify_requires_finite_age_and_limits_before_env(self):
        for changes in ({'maximum_age_seconds': None}, {'maximum_age_seconds': True},
                {'maximum_age_seconds': 86401}, {'execution_limits': None}, {'receipt_sha256': []}):
            with self.subTest(changes=changes), mock.patch.object(B, 'BoundRoleEnv', side_effect=AssertionError('controls_before_env')):
                arguments = {**self.common, 'maximum_age_seconds': 3600,
                    'receipt_sha256': 'a'*64, 'journal_sha256': 'b'*64, **changes}
                with self.assertRaises(B.BackupError): B.verify_quiesced_primary_receipt(**arguments)

    def test_ordinary_primary_permission_is_not_widened(self):
        with mock.patch.object(B, 'BoundRoleEnv', side_effect=AssertionError('ordinary_primary_still_refused')):
            with self.assertRaisesRegex(B.BackupError, 'primary_source_permission_invalid'):
                B.create_backup(**self.common, backup_dir=self.backup_dir,
                                allow_private_primary_source=True, restore_resource_binding=self.resources)


def tearDownModule(): DENIALS.close()


if __name__ == '__main__': unittest.main()
