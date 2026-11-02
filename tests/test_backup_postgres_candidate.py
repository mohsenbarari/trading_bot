"""Public inert controls and named metadata mocks; no host/data operation."""
from contextlib import ExitStack, redirect_stdout
from copy import deepcopy
from hashlib import sha256
import ast
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from scripts import backup_postgres_candidate as P
from scripts import backup_market_pipeline_archive as B
from scripts import run_production_backup as A

REPO=Path(__file__).resolve().parents[1]


def control(site='foreign', *, old=True, new=True):
    names = ['/trading_bot_db'] if site=='foreign' else ['/trading_bot_db','/market-private-pipeline-primary-market-database-1']
    sources={}
    for name in names:
        image=P.BASELINE_CONFIG if site=='foreign' else P.WEB_APP_BASELINE if name=='/trading_bot_db' else P.WEB_MARKET_BASELINE
        sources[name]=([{'mode':'baseline','daemon_image_id':image,'runtime_image_reference':'postgres:15-alpine' if name=='/trading_bot_db' else 'postgres:15-alpine@'+image}] if old else [])+(
            [{'mode':'candidate','daemon_image_id':P.CONFIG,'runtime_image_reference':P.CONFIG}] if new else [])
    return {'schema':'backup_postgres_candidate_identity/1','candidate_config_id':P.CONFIG,
        'recipe_commit':P.RECIPE,'ci_run_id':40142076769,'candidate_receipt_sha256':P.RECEIPT,
        'baseline_public_proof_sha256':P.BASELINE_PROOF,'rootfs_layers':list(P.LAYERS),
        'server_sha256':P.SERVER_SHA,'server_version':'postgres (PostgreSQL) 15.19',
        'raw_counts':{'CRITICAL':0,'HIGH':0,'MEDIUM':0,'LOW':0,'UNKNOWN':1},
        'security_accepted':False,'raw_finding_waived':False,'configuration_values_disclosed':False,
        'sites':{site:{'host':P.SITES[site][0],'port':P.SITES[site][1],'daemon_image_id':P.CONFIG,
            'image_loaded':True,'stable_before_after':True,'configuration_values_disclosed':False,
            'source_bindings':sources,'binding':{'schema':'runtime_image_archive_binding/1',
                'kind':'Docker_config_identity','docker_image_id':P.CONFIG,'scanner_config_id':P.CONFIG,
                'config_sha256':P.CONFIG[7:],'rootfs_layers':list(P.LAYERS),'archive_bytes':303898112,
                'archive_sha256':'a'*64,'security_accepted':False,'findings_waived':False}}}}


def encoded(d):return (json.dumps(d,sort_keys=True,separators=(',',':'))+'\n').encode()


def document(site='foreign', *, candidate=False, restore=False):
    name='/trading_bot_restore_drill_owned' if restore else '/trading_bot_db'
    image=P.CONFIG if candidate or restore else P.BASELINE_CONFIG if site=='foreign' else P.WEB_APP_BASELINE
    return {'Id':'c'*64,'Name':name,'Image':image,'Config':{'Image':image if candidate or restore else 'postgres:15-alpine',
        'Labels':{'com.docker.compose.project':'trading_bot' if site=='foreign' else 'current'}}}


def query_for(doc, calls):
    def query(argv):
        calls.append(argv)
        if argv[2:4]==['image','inspect']:
            image=argv[-1];return json.dumps({'id':image,'os':'linux','architecture':'amd64',
                'rootfs':{'Type':'layers','Layers':P.LAYERS if image==P.CONFIG else P.LAYERS[:11]}})
        if argv[2]=='exec':
            if argv[-2:]==['sha256sum','/usr/local/bin/postgres']:return P.SERVER_SHA+'  /usr/local/bin/postgres'
            if argv[-2:]==['/usr/local/bin/postgres','--version']:return P.SERVER_VERSION
        if argv[2:4]==['container','inspect']:
            return json.dumps({'id':doc['Id'],'name':doc['Name'],'image':doc['Image'],'reference':doc['Config']['Image']})
        raise AssertionError('unlisted public metadata command')
    return query


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','os.system','socket.create_connection','socket.getaddrinfo',
                     'urllib.request.urlopen','ctypes.CDLL','resource.setrlimit','signal.signal','signal.alarm'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient operation denied')))
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.pg-candidate-',dir=REPO)))
        self.temp.chmod(0o700);self.path=self.temp/'identity.json'

    def save(self,d=None):
        raw=encoded(control() if d is None else d);self.path.write_bytes(raw);self.path.chmod(0o600)
        return sha256(raw).hexdigest()

    def candidate(self,site='foreign'):
        raw=encoded(control(site));pin=sha256(raw).hexdigest();return P.Candidate(P.validate(raw,pin,site),pin,site)

    def test_old_source_and_new_restore_are_distinct_for_both_sites(self):
        for site in ('foreign','iran'):
            with self.subTest(site=site):
                c=self.candidate(site);old=document(site);calls=[]
                proof=c.runtime_check(old,query_for(old,calls));self.assertEqual(proof['source_mode'],'baseline')
                self.assertEqual(proof['source_config_sha256'],P.BASELINE_CONFIG[7:])
                self.assertEqual(proof['daemon_image_id'],P.CONFIG)
                restored=document(site,restore=True)
                proof=c.runtime_check(restored,query_for(restored,calls),restore=True)
                self.assertEqual(proof['source_mode'],'candidate')
                self.assertTrue(all('/usr/local/bin/postgres' in cmd or cmd[2] in ('image','container') for cmd in calls))

    def test_candidate_source_requires_its_immutable_reference_and_explicit_mode(self):
        c=self.candidate();new=document(candidate=True)
        self.assertEqual(c.runtime_check(new,query_for(new,[]))['source_mode'],'candidate')
        new['Config']['Image']='postgres:15-alpine'
        with self.assertRaisesRegex(RuntimeError,'source_not_admitted'):c.runtime_check(new,query_for(new,[]))
        d=control(new=False);raw=encoded(d);c=P.Candidate(P.validate(raw,sha256(raw).hexdigest(),'foreign'),'a'*64,'foreign')
        with self.assertRaisesRegex(RuntimeError,'source_not_admitted'):c.runtime_check(document(candidate=True),query_for(new,[]))

    def test_closed_candidate_scope_types_layers_and_baseline_proof(self):
        for change in (lambda d:d.update(candidate_config_id='sha256:'+'0'*64),
            lambda d:d.update(ci_run_id=True),lambda d:d['raw_counts'].update(UNKNOWN=0),
            lambda d:d['raw_counts'].update(CRITICAL=False),lambda d:d.update(security_accepted=True),
            lambda d:d.update(baseline_public_proof_sha256='0'*64),lambda d:d['rootfs_layers'].reverse(),
            lambda d:d['sites']['foreign']['source_bindings']['/trading_bot_db'][0].update(daemon_image_id=P.WEB_MARKET_BASELINE)):
            d=control();change(d);raw=encoded(d)
            with self.subTest(change=change),self.assertRaises(RuntimeError):P.validate(raw,sha256(raw).hexdigest(),'foreign')

    def test_manifest_identity_needs_exact_config_rootfs_and_false_waiver(self):
        d=control();row=d['sites']['foreign'];actual='sha256:'+'f'*64;row['daemon_image_id']=actual
        row['binding'].update(kind='OCI_manifest_to_config',docker_image_id=actual,manifest_sha256=actual[7:])
        row['source_bindings']['/trading_bot_db']=row['source_bindings']['/trading_bot_db'][:1]
        raw=encoded(d);P.validate(raw,sha256(raw).hexdigest(),'foreign')
        row['binding']['config_sha256']='0'*64;raw=encoded(d)
        with self.assertRaisesRegex(RuntimeError,'archive_binding'):P.validate(raw,sha256(raw).hexdigest(),'foreign')

    def test_public_pin_is_checked_before_decode_and_private_fields_refused(self):
        with self.assertRaisesRegex(RuntimeError,'control_pin'):P.validate(b'not-json','0'*64,'foreign')
        d=control();d['sites']['foreign']['binding']['Env']=['never-persist'];raw=encoded(d)
        with self.assertRaisesRegex(RuntimeError,'private_control'):P.validate(raw,sha256(raw).hexdigest(),'foreign')

    def test_stable_control_fd_refuses_same_bytes_path_replacement_and_links(self):
        pin=self.save()
        with self.assertRaisesRegex(RuntimeError,'control_changed'):
            with P.bound(self.path,pin,'foreign') as c:
                replacement=self.temp/'replacement';replacement.write_bytes(c.raw);replacement.chmod(0o600)
                os.replace(replacement,self.path)
        alias=self.temp/'alias';os.link(self.path,alias)
        with self.assertRaisesRegex(RuntimeError,'control_metadata'):
            with P.bound(self.path,pin,'foreign'):pass

    def test_wrong_runtime_source_name_server_or_full_layer_order_refuses(self):
        c=self.candidate();doc=document();doc['Name']='/other-db'
        with self.assertRaisesRegex(RuntimeError,'runtime_name'):c.runtime_check(doc,query_for(doc,[]))
        doc=document();normal=query_for(doc,[])
        def wrong(argv):return '0'*64+'  /usr/local/bin/postgres' if argv[-2:]==['sha256sum','/usr/local/bin/postgres'] else normal(argv)
        with self.assertRaisesRegex(RuntimeError,'server_bytes'):c.runtime_check(doc,wrong)
        def layers(argv):
            result=normal(argv)
            if argv[2:4]==['image','inspect']:
                d=json.loads(result);d['rootfs']['Layers'].reverse();return json.dumps(d)
            return result
        with self.assertRaisesRegex(RuntimeError,'actual_rootfs'):c.runtime_check(doc,layers)

    def test_bad_public_optin_precedes_any_market_private_env_open(self):
        pin=self.save();self.path.write_bytes(b'changed public control')
        with patch.object(B,'BoundRoleEnv',side_effect=AssertionError('private Env was opened')) as private:
            with self.assertRaisesRegex(RuntimeError,'control_pin'):
                B.create_backup(env_file=self.temp/'never-open',backup_dir=self.temp/'backup',receipt=self.temp/'backup/market-pipeline-backup-receipt.json',
                    release_sha='a'*40,release_tree='b'*40,image_id='sha256:'+'c'*64,image_input_signature='d'*64,
                    postgres_candidate_control=self.path,postgres_candidate_sha256=pin)
        private.assert_not_called()

    def test_app_control_precedes_manifest_and_never_accepts_an_unpaired_option(self):
        pin=self.save();self.path.write_bytes(b'changed public control')
        with patch.object(A,'production_backup_manifest_values',side_effect=AssertionError('private manifest opened')) as private:
            with self.assertRaisesRegex(RuntimeError,'control_pin'):A.main(['--role','foreign','--postgres-candidate-control',str(self.path),'--postgres-candidate-sha256',pin])
            with self.assertRaisesRegex(RuntimeError,'control_required'):A.main(['--postgres-candidate-control',str(self.path)])
        private.assert_not_called()

    def test_market_restore_uses_only_verified_candidate_no_pull(self):
        candidate=self.candidate('iran');candidate.image_check=Mock();candidate.runtime_check=Mock()
        seen=[]
        label='market-pipeline-backup-'+'a'*16
        def command(argv,**kw):
            seen.append(argv)
            if argv[:2]==['docker','run']:raise B.BackupError('owned_fixture_stop')
            if argv[:3]==['docker','volume','inspect']:return label
            return 'owned'
        with patch.object(B,'_run_text',side_effect=command),patch.object(B,'_cleanup_owned_restore_resource'), \
             patch.object(B,'_assert_restore_resource_absent'):
            with self.assertRaisesRegex(B.BackupError,'owned_fixture_stop'):
                B.restore_smoke(self.temp/'unused',{'postgres_candidate':candidate.summary()},postgres_candidate=candidate,
                    resource_binding={'container':'market_pipeline_restore_'+'a'*16,'volume':'market_pipeline_restore_'+'a'*16+'_data','label':label})
        cmd=next(v for v in seen if v[:2]==['docker','run'])
        self.assertEqual(cmd[cmd.index('--pull')+1],'never');self.assertEqual(cmd[-1],P.CONFIG)
        self.assertNotIn(B.POSTGRES_IMAGE,cmd)

    def test_app_shell_seals_source_old_and_restore_new_without_changing_legacy_path(self):
        target=A.HostTarget('foreign',str(REPO),'docker-compose.yml',False)
        legacy=A.build_backup_shell(target,stamp='fixture',backup_dir=A.DEFAULT_BACKUP_DIR,restore_smoke=True)
        raw=encoded(control());pin=sha256(raw).hexdigest()
        current=A.build_backup_shell(target,stamp='fixture',backup_dir=A.DEFAULT_BACKUP_DIR,restore_smoke=True,
            postgres_candidate_control=raw,postgres_candidate_sha256=pin)
        self.assertIn('postgres:15-alpine >/dev/null; then',legacy)
        self.assertNotIn('PG_CANDIDATE',legacy)
        self.assertIn(P.CONFIG+' >/dev/null; then',current)
        self.assertLess(current.index('postgres_candidate_observed='),current.index('database_name='))
        self.assertIn('payload["postgres_candidate"]',current)
        self.assertEqual(current.count("P=ModuleType('_sealed_backup_pg')"),1)
        self.assertLess(len(current.encode()),120*1024)
        self.assertIn('--pull never',current)
        ast.parse(A.candidate_program(raw,pin,'foreign'))

    def test_candidate_create_binds_control_through_real_dump_journal_receipt_and_retained_fd(self):
        from tests.test_backup_market_pipeline_archive import _env_values,_write_env,_invariants
        raw=encoded(control('iran'));pin=sha256(raw).hexdigest();self.path.write_bytes(raw);self.path.chmod(0o600)
        values=_env_values(self.temp);env=self.temp/'synthetic.env';_write_env(env,values)
        pg=Path(values['MARKET_WEB_DATA_ROOT'])/'postgres/pgdata';pg.mkdir(mode=0o700,parents=True)
        (pg/'PG_VERSION').write_bytes(b'15\n')
        source={'container_id':'e'*64,'database':'market_archive','database_size_bytes':12345,
                'database_identity_sha256':'f'*64,'schema_versions':[1,2],'table_count':1,'fact_count':10,
                **_invariants(fact_count=10)}
        observed=[]
        def inspect(*a,**kw):
            c=kw['postgres_candidate'];c.check();observed.append(c.pin)
            return {**source,'postgres_candidate':{**c.summary(),'source_mode':'baseline',
                'source_daemon_image_id':P.WEB_MARKET_BASELINE,'source_config_sha256':P.BASELINE_CONFIG[7:],
                'source_runtime_image_reference':'postgres:15-alpine@'+P.WEB_MARKET_BASELINE}}
        def dump(**kw):kw['destination'].write_bytes(b'public inert custom-dump fixture');kw['destination'].chmod(0o600)
        def restore(path,metadata,**kw):
            c=kw['postgres_candidate'];c.check();self.assertIsNotNone(kw.get('artifact_descriptor'))
            self.assertEqual(os.pread(kw['artifact_descriptor'],64,0),path.read_bytes())
            return {'status':'PASS','cleanup_status':'PASS',**B._invariant_view(source),
                    'postgres_restore_candidate':c.summary()}
        root=self.temp/'backups';receipt=root/'market-pipeline-backup-receipt.json'
        with patch.object(B,'validate_source'),patch.object(P.Candidate,'image_check'), \
             patch.object(B,'inspect_source_database',side_effect=inspect),patch.object(B,'_write_dump',side_effect=dump), \
             patch.object(B,'_dump_archive_is_valid',return_value=True),patch.object(B,'restore_smoke',side_effect=restore):
            result=B.create_backup(env_file=env,backup_dir=root,receipt=receipt,release_sha='a'*40,
                release_tree='b'*40,image_id='sha256:'+'c'*64,image_input_signature='d'*64,
                postgres_candidate_control=self.path,postgres_candidate_sha256=pin)
        self.assertEqual(result['status'],'PASS');self.assertEqual(observed,[pin,pin,pin])
        saved=json.loads(receipt.read_bytes());self.assertEqual(saved['source']['postgres_candidate']['source_mode'],'baseline')
        self.assertEqual(saved['restore_smoke']['postgres_restore_candidate']['daemon_image_id'],P.CONFIG)
        self.assertEqual(saved['schema'],B.RECEIPT_SCHEMA)

    def test_embedded_public_validator_executes_real_old_source_new_restore_checks(self):
        raw=encoded(control());pin=sha256(raw).hexdigest();doc=document();queries=[]
        def popen(argv,**kw):
            queries.append(argv)
            if argv[2:4]==['container','inspect'] and '"Config"' in argv[argv.index('--format')+1]:
                response=json.dumps(doc)
            else:response=query_for(doc,[])(argv)
            os.write(kw['stdout'],response.encode());return SimpleNamespace(wait=lambda timeout:0)
        output=io.StringIO()
        with patch('subprocess.Popen',side_effect=popen),patch('sys.argv',['program','']),redirect_stdout(output):
            exec(compile(A.candidate_program(raw,pin,'foreign'),'<sealed-fixture>','exec'),{})
        result=json.loads(output.getvalue());self.assertEqual(result['source_binding']['source_mode'],'baseline')
        self.assertEqual(result['restore_image_id'],P.CONFIG)
        self.assertFalse(any('load' in v or 'run' in v or 'pg_dump' in v for v in queries))


if __name__=='__main__':unittest.main()
