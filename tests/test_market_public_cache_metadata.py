"""Ordinary inert cache headers; no cached code is decoded or executed."""
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
SPEC=importlib.util.spec_from_file_location('public_cache_metadata_fixture',
    ROOT/'scripts/collect_market_public_cache_metadata.py')
M=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(M)
PATH='core/market_intelligence/__pycache__/ordinary.cpython-311.pyc'


class PublicCacheMetadataTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','os.system','os.kill','os.killpg',
                     'socket.socket','socket.create_connection','socket.getaddrinfo',
                     'urllib.request.urlopen','ctypes.CDLL','ctypes.PyDLL',
                     'resource.setrlimit','signal.signal','signal.alarm'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient_host_network_denied')))
        self.stack.enter_context(patch.object(M,'SOURCE_UID',os.getuid()))
        self.stack.enter_context(patch.object(M,'SOURCE_GID',os.getgid()))
        self.stack.enter_context(patch.object(M,'CACHE_COUNT',1))
        self.stack.enter_context(patch.object(M,'sys',SimpleNamespace(
            implementation=SimpleNamespace(name='cpython',cache_tag='cpython-311'),
            version_info=(3,11,17),pycache_prefix=None)))
        self.source={'mtime_seconds':100,'bytes':9,'source_hash':'12'*8}

    def header(self,flags=0,tail=None,magic=None):
        return (M.importlib.util.MAGIC_NUMBER if magic is None else magic)+struct.pack('<I',flags)+(struct.pack('<II',100,9) if tail is None else tail)

    def classify(self,header,source=None,path=PATH,policy='default'):
        return M.classify_header(path,header,self.source if source is None else source,
            cache_tag='cpython-311',magic=M.importlib.util.MAGIC_NUMBER,hash_policy=policy)

    def test_timestamp_rejection_requires_actual_size_or_mtime_mismatch(self):
        self.assertEqual(self.classify(self.header()),'unknown_cache_blocks_candidate')
        self.assertEqual(self.classify(self.header(tail=struct.pack('<II',99,9))),'timestamp_stale')
        self.assertEqual(self.classify(self.header(tail=struct.pack('<II',100,8))),'timestamp_stale')
        source=dict(self.source,mtime_seconds=100+2**32,bytes=9+2**32)
        self.assertEqual(self.classify(self.header(),source),'unknown_cache_blocks_candidate')

    def test_other_interpreter_tag_is_nonselected_and_missing_source_is_distinct(self):
        self.assertEqual(self.classify(b'',path=PATH.replace('311','312')),'nonselected_cache_tag')
        self.assertEqual(M.classify_header(PATH,b'',None,cache_tag='cpython-311',
            magic=M.importlib.util.MAGIC_NUMBER),'source_absent')

    def test_only_header_rejection_is_used_no_cached_payload_needed(self):
        for header in (b'',self.header()[:15],self.header(magic=b'none'),self.header(flags=4)):
            self.assertEqual(self.classify(header),'rejected_header')
        self.assertEqual(self.classify(self.header(flags=2)),'unknown_cache_blocks_candidate')

    def test_unchecked_or_valid_checked_hash_remains_unknown(self):
        self.assertEqual(self.classify(self.header(flags=1,tail=b'wrong123')),'unknown_cache_blocks_candidate')
        self.assertEqual(self.classify(self.header(flags=3,tail=bytes.fromhex('12'*8))),'unknown_cache_blocks_candidate')
        self.assertEqual(self.classify(self.header(flags=3,tail=b'wrong123')),'checked_hash_stale')
        self.assertEqual(self.classify(self.header(flags=3,tail=b'wrong123'),policy='never'),'unknown_cache_blocks_candidate')

    def fixture(self):
        directory=self.stack.enter_context(tempfile.TemporaryDirectory());app=Path(directory)
        files={'core/__init__.py':b'', 'core/market_intelligence/ordinary.py':b'# public\n',
               'config/market_data_sources.v1.json':b'{}','contracts/market_data/fact.json':b'{}',
               'deploy/market-data/migrations/001.sql':b'SELECT 1;',PATH:self.header(tail=struct.pack('<II',1,1))}
        for path,data in files.items():
            target=app/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data);target.chmod(0o644)
        rows,_=M.scan(app,M.time.monotonic()+20)
        value={'schema':'market_security_public_source_fingerprint/1','container_id':'1'*64,'image_id':M.IMAGE,
               'stable_before_after':True,'mount_overlays_absent':True,'application_imported':False,
               'secrets_read':False,'files':rows}
        raw=M.encoded(value)
        return app,raw,sha256(raw).hexdigest()

    def test_retained_public_inventory_is_fully_rechecked_and_receipt_never_accepts_security(self):
        app,raw,digest=self.fixture();result=M.collect(raw,app_root=app,expected_sha256=digest)
        self.assertTrue(result['all_standard_cache_paths_nonselected_or_stale'])
        self.assertEqual(result['cache_count'],1);self.assertEqual(result['caches'][0]['classification'],'timestamp_stale')
        for field in ('cached_code_deserialized','cached_code_executed','application_imported','secrets_read',
                      'runtime_changed','security_accepted','explicit_bytecode_loader_reachability_assessed'):
            self.assertIs(result[field],False)
        self.assertEqual(result['caches'][0]['source_sha256'],sha256(b'# public\n').hexdigest())

    def test_valid_timestamp_is_unknown_even_when_all_source_hashes_match(self):
        app,_,_=self.fixture();source=(app/'core/market_intelligence/ordinary.py').stat()
        (app/PATH).write_bytes(self.header(tail=struct.pack('<II',int(source.st_mtime)&0xffffffff,source.st_size)))
        rows,_=M.scan(app,M.time.monotonic()+20)
        value={'schema':'market_security_public_source_fingerprint/1','container_id':'1'*64,'image_id':M.IMAGE,
               'stable_before_after':True,'mount_overlays_absent':True,'application_imported':False,
               'secrets_read':False,'files':rows};raw=M.encoded(value)
        result=M.collect(raw,app_root=app,expected_sha256=sha256(raw).hexdigest())
        self.assertFalse(result['all_standard_cache_paths_nonselected_or_stale'])
        self.assertEqual(result['status'],'unknown_cache_blocks_candidate')

    def test_pin_checked_before_json_and_changed_or_extra_file_refuses(self):
        app,raw,digest=self.fixture()
        with patch.object(M.json,'loads',side_effect=AssertionError('parsed_before_pin')) as parse:
            with self.assertRaisesRegex(M.CacheError,'control_pin'):M.collect(raw,app_root=app,expected_sha256='0'*64)
        parse.assert_not_called()
        extra = app/'core/market_intelligence/extra.py'
        extra.write_bytes(b'# public\n'); extra.chmod(0o644)
        with self.assertRaisesRegex(M.CacheError,'whole_public_inventory_changed'):M.collect(raw,app_root=app,expected_sha256=digest)

    def test_extra_file_mode_and_inventory_guards_do_not_depend_on_process_umask(self):
        for mask in (0o022, 0o077):
            previous = os.umask(mask)
            try:
                app, raw, digest = self.fixture()
                extra = app/'core/market_intelligence/extra.py'
                extra.write_bytes(b'# public\n'); extra.chmod(0o600)
                with self.assertRaisesRegex(M.CacheError, '^public_file$'):
                    M.collect(raw, app_root=app, expected_sha256=digest)
                extra.chmod(0o644)
                with self.assertRaisesRegex(M.CacheError, '^whole_public_inventory_changed$'):
                    M.collect(raw, app_root=app, expected_sha256=digest)
            finally:
                os.umask(previous)

    def test_second_inventory_change_and_expired_deadline_refuse(self):
        app,raw,digest=self.fixture();original=M.scan;calls=[]
        def scan(root,end):
            calls.append(1)
            if len(calls)==2:(root/'core/market_intelligence/ordinary.py').write_bytes(b'# public changed\n')
            return original(root,end)
        with patch.object(M,'scan',side_effect=scan):
            with self.assertRaisesRegex(M.CacheError,'whole_public_inventory_changed'):M.collect(raw,app_root=app,expected_sha256=digest)
        with self.assertRaisesRegex(M.CacheError,'deadline'):M.scan(app,M.time.monotonic()-1)

    def test_public_path_rules_allow_private_module_name_but_refuse_secret_name(self):
        M.public('private_pipeline_foundation.py')
        for name in ('.env','credentials.json','capture.session','id_ed25519','known_hosts'):
            with self.subTest(name=name),self.assertRaises(M.CacheError):M.public(name)


if __name__=='__main__':unittest.main()
