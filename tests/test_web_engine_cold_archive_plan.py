"""Pure cold-engine planning tests, using owned public control-schema fixtures."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

from deploy.maintenance import web_engine_current_profile as C
from tests import test_web_engine_current_profile as fixtures

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('cold_plan_fixture',ROOT/'deploy/maintenance/web_engine_cold_archive_plan.py')
M = importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)


class ColdArchivePlanTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','os.system','os.kill','os.killpg','socket.socket',
                'socket.create_connection','socket.getaddrinfo','urllib.request.urlopen'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient_host_or_network_forbidden')))
        docs=fixtures.CurrentProfileTests().fixture();self.buffers={k:fixtures.raw(v) for k,v in docs.items()}
        self.stack.enter_context(patch.object(C,'CONTROL_SHA256',{k:sha256(v).hexdigest() for k,v in self.buffers.items()}))
        self.profile=C.derive_current_profile(self.buffers)
        self.sizes={p:1024**3 for p in M.COLD_INPUTS}
        self.target={'schema':'web_engine_cold_destination/1','host':'65.109.214.203','port':22,'user':'root',
            'root':'/srv/trading-bot/security-backups','directory_binding':{'device':2049,'inode':123,'uid':0,'gid':0,'mode':0o40700},
            'host_key_fingerprint':'SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs',
            'mount':{'target':'/','source':'/dev/sda1','fstype':'ext4','uuid':'ce366399-3430-441e-8a6f-6f5621f0c7a2'}}

    def build(self,**kwargs):
        return M.build_cold_archive_plan(self.profile,self.buffers,input_sizes=self.sizes,
            web_free_bytes=45_000_000_000,offhost_free_bytes=303_641_874_432,
            destination_receipt=fixtures.raw(self.target),destination_receipt_sha256=sha256(fixtures.raw(self.target)).hexdigest(),**kwargs)

    def test_fresh_finland_identity_is_preserved_without_native_uuid_or_authority(self):
        with patch('builtins.open',side_effect=AssertionError('file_io_forbidden')):result=self.build()
        self.assertEqual(result['offhost_identity'],self.target)
        self.assertEqual(result['capacity']['archive_max_bytes'],M.MAX_ARCHIVE)
        self.assertEqual(result['cold_archive_inputs'],sorted(M.COLD_INPUTS))
        self.assertNotIn(self.profile['hot_backup_data']['artifact']['path'],result['cold_archive_inputs'])
        self.assertFalse(result['hot_dump_embedded_in_cold_archive'])
        self.assertTrue(result['database_and_cold_archive_are_separate_required_artifacts'])
        for key in ('current_runtime_rechecked','cold_engine_archive_exists','maintenance_authorized',
                'security_accepted','deployment_eligible','reboot_authorized'):self.assertIs(result[key],False)
        self.assertNotIn('deadline_utc',result)

    def test_small_destination_limits_attempt_without_claiming_actual_compression_fit(self):
        remote=M.RESERVE+M.CONTROL_ALLOWANCE+M.CIPHER_OVERHEAD+1_770_000_000
        result=M.capacity_budget(45_000_000_000,remote)
        self.assertEqual(result['archive_max_bytes'],1_770_000_000)
        self.assertEqual(result['offhost_required_bytes'],remote)
        self.assertFalse(result['actual_cold_cipher_fit_verified']);self.assertFalse(result['compression_ratio_assumed'])
        self.assertFalse(result['capacity_reserved'])

    def test_web_budget_retains_both_plaintext_and_ciphertext_plus_full_reserve(self):
        web=M.RESERVE+M.CONTROL_ALLOWANCE+M.CIPHER_OVERHEAD+200
        result=M.capacity_budget(web,100*1024**3)
        self.assertEqual(result['archive_max_bytes'],100);self.assertEqual(result['web_required_bytes'],web)
        for value in (True,-1,1.5):
            with self.subTest(value=value),self.assertRaisesRegex(M.ColdPlanError,'capacity_type'):M.capacity_budget(value,100*1024**3)
        with self.assertRaisesRegex(M.ColdPlanError,'capacity_insufficient'):
            M.capacity_budget(M.RESERVE+M.CONTROL_ALLOWANCE+M.CIPHER_OVERHEAD,100*1024**3)

    def test_scope_refuses_entire_root_extra_data_or_a_missing_required_store(self):
        original=dict(self.sizes)
        for sizes in ({**original,'/':1},{**original,'/srv/trading-bot/market-data-staging-shadow':1},
                {k:v for k,v in original.items() if k!='/var/lib/containerd'}):
            self.sizes=sizes
            with self.assertRaisesRegex(M.ColdPlanError,'input_scope'):self.build()

    def test_optional_configs_are_explicit_and_raw_scope_never_assumes_ninety_gigabytes(self):
        self.sizes['/etc/containerd']=123
        result=self.build();self.assertIn('/etc/containerd',result['cold_archive_inputs'])
        self.assertEqual(result['raw_scope_bytes'],4*1024**3+123)
        self.sizes['/var/lib/containerd']=90*1024**3
        with self.assertRaisesRegex(M.ColdPlanError,'raw_scope_bound'):self.build()

    def test_target_identity_rejects_native_relabel_mount_drift_and_false_as_integer(self):
        for key,value in (('host','65.109.216.187'),('port',True),('root','/srv/../etc'),('host_key_fingerprint','untrusted')):
            changed=deepcopy(self.target);changed[key]=value
            with self.subTest(key=key),self.assertRaises(M.ColdPlanError):M.target_identity(changed)
        for key,value in (('uuid',''),('target','/srv/trading-bot/backups'),('fstype','tmpfs')):
            changed=deepcopy(self.target);changed['mount'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(M.ColdPlanError,'target_mount'):M.target_identity(changed)
        for key,value in (('uid',False),('mode',0o40755),('inode',True)):
            changed=deepcopy(self.target);changed['directory_binding'][key]=value
            with self.subTest(key=key),self.assertRaisesRegex(M.ColdPlanError,'target_directory'):M.target_identity(changed)
        changed=deepcopy(self.target);changed['mount']['uuid']='9d19fc8e-f71a-4089-b826-5dddfe83ea28'
        with self.assertRaisesRegex(M.ColdPlanError,'reviewed_mount'):M.target_identity(changed)

    def test_destination_same_buffer_pin_and_duplicate_keys_are_required(self):
        target=fixtures.raw(self.target);digest=sha256(target).hexdigest()
        with self.assertRaisesRegex(M.ColdPlanError,'control_digest'):M.destination_control(target+b' ',digest)
        duplicate=b'{"schema":"a","schema":"b"}'
        with self.assertRaisesRegex(M.ColdPlanError,'duplicate_key'):M.destination_control(duplicate,sha256(duplicate).hexdigest())

    def test_changed_current_profile_or_hot_chain_cannot_enter_planning(self):
        self.profile['containers'][0]['running']=not self.profile['containers'][0]['running']
        with self.assertRaises(C.ProfileError):self.build()

    def test_returned_objects_do_not_mutate_profile_target_or_scope(self):
        before=(deepcopy(self.profile),deepcopy(self.target),dict(self.sizes))
        result=self.build();result['offhost_identity']['mount']['uuid']='changed'
        result['hot_backup_data']['artifact']['path']='/changed'
        self.assertEqual((self.profile,self.target,self.sizes),before)


if __name__=='__main__':unittest.main()
