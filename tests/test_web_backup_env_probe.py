import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

SPEC=importlib.util.spec_from_file_location('backup_env_probe',Path(__file__).resolve().parents[1]/'deploy/maintenance/web_backup_env_probe.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)

class BackupError(RuntimeError):pass
class ReleaseError(RuntimeError):pass

class BackupEnvProbeTests(unittest.TestCase):
    def result(self,side_effect=None,returned=None):
        backup=SimpleNamespace(BackupError=BackupError,validate_release_env=Mock(side_effect=side_effect,return_value=returned))
        release=SimpleNamespace(ReleaseContractError=ReleaseError)
        with patch('subprocess.run',side_effect=AssertionError('native_forbidden')),patch('subprocess.Popen',side_effect=AssertionError('native_forbidden')):
            proof=M.normal_validation(backup,release,Path('/protected/not-read-by-wrapper'),{'release_sha':'a'*40,'image_id':'sha256:'+'b'*64})
        backup.validate_release_env.assert_called_once_with(Path('/protected/not-read-by-wrapper'),release_sha='a'*40,
            image_id='sha256:'+'b'*64,allow_target_identity_mismatch=True)
        return proof

    def test_success_discards_private_mapping_without_serializing_or_inspecting(self):
        class PrivateMapping(dict):
            def __str__(self):raise AssertionError('private_serialize')
            def __repr__(self):raise AssertionError('private_serialize')
            def items(self):raise AssertionError('private_values_inspected')
            def __iter__(self):raise AssertionError('private_values_inspected')
        self.assertEqual(self.result(returned=PrivateMapping(secret='do-not-emit')),
                         {'validation_passed':True,'reason_code':None})

    def test_two_authoritative_split_codes_remain_distinguishable(self):
        for code in sorted(M.CODES):
            with self.subTest(code=code):
                self.assertEqual(self.result(side_effect=BackupError(code)),{'validation_passed':False,'reason_code':code})

    def test_other_contract_and_hostile_private_error_text_are_redacted(self):
        for error in (BackupError('private credential /path value'),ReleaseError('secret_env_value'),
                      BackupError('backup_release_env_identity_mismatch')):
            proof=self.result(side_effect=error)
            self.assertEqual(proof['reason_code'],'backup_env_other_contract_refusal')
            self.assertNotIn('credential',json.dumps(proof));self.assertNotIn('secret',json.dumps(proof))

    def test_arbitrary_noncontract_exception_is_not_treated_as_a_contract_code(self):
        with self.assertRaises(ValueError):
            self.result(side_effect=ValueError('private-value'))

    def test_unpinned_public_script_refuses_before_any_staging(self):
        with patch.object(M.os.path,'lexists',side_effect=AssertionError('stage_touched')):
            with self.assertRaisesRegex(RuntimeError,'probe_public_source_changed'):
                M.stage({name:b'altered' for name in M.PINS})

if __name__=='__main__':unittest.main()
