"""Sanitized public control schemas; no host, credential or artifact reads."""
from copy import deepcopy
from contextlib import ExitStack
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('current_profile_fixture', ROOT/'deploy/maintenance/web_engine_current_profile.py')
M = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(M)


def raw(value): return (json.dumps(value,sort_keys=True,separators=(',',':'))+'\n').encode()


class CurrentProfileTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name in ('subprocess.run','subprocess.Popen','os.system','socket.socket','socket.create_connection',
                'socket.getaddrinfo','urllib.request.urlopen','os.kill','os.killpg'):
            self.stack.enter_context(patch(name,side_effect=AssertionError('ambient_host_or_network_forbidden')))
        self.docs=self.fixture();self.buffers={k:raw(v) for k,v in self.docs.items()}
        self.pins={k:sha256(v).hexdigest() for k,v in self.buffers.items()}
        self.stack.enter_context(patch.object(M,'CONTROL_SHA256',self.pins))

    def fixture(self):
        rows=[]
        groups=[('market-private-pipeline-primary',['market-capture-account1-1','market-capture-account2-1',
            'market-processor-1','estimator-snapshot-receiver-1','market-archive-1','market-backfill-1','market-database-1']),
            ('current',['app','db','redis','sync']),('trading_bot_staging_iran',['app-1','db-1','redis-1','sync-1','migration-1']),
            ('three-site-727c1f337b75-webapp-fi',['db-1']),(M.QA_PROJECT,['api-1','db-1','redis-1',*[f'worker{i}-1' for i in range(7)]])]
        for project,names in groups:
            for name in names:
                ident=f'{len(rows)+1:064x}';image='sha256:'+f'{len(rows)+101:064x}'
                if name=='market-capture-account1-1':image=M.IMAGE['image_id']
                if name=='market-capture-account2-1':ident,image=M.OLD_CAPTURE,M.BASE
                if name=='market-processor-1':ident=sorted(M.KNOWN_HEALTH)[1]
                if name=='estimator-snapshot-receiver-1':ident=sorted(M.KNOWN_HEALTH)[0]
                full='/trading_bot_'+name if project=='current' else '/'+project+'-'+name
                rows.append({'id':ident,'name':full,'image':image,'project':project,'running':name!='migration-1',
                    'restart_policy':{'Name':'no' if name=='migration-1' else 'always','MaximumRetryCount':0},
                    'mounts':[{'Destination':'/data','Mode':'rw','Propagation':'rprivate','RW':True,
                        'Source':'/srv/synthetic/'+ident,'Type':'bind'}],
                    'network_names':['synthetic-network'],'network_mode':'synthetic-network','port_bindings':{}})
        parked=sorted(r['id'] for r in rows if r['project']==M.QA_PROJECT)
        probes=[]
        for row in rows:
            if row['name'] in {'/trading_bot_db','/trading_bot_redis'} or row['name'].endswith(('-db-1','-market-database-1','-redis-1')):
                redis='redis' in row['name']
                probes.append({'id':row['id'],'kind':'redis' if redis else 'postgres',
                    **({} if redis else {'user':'market_data','database':'market_archive'})})
        roots=[{'path':p,'device':2049,'inode':i,'uuid':'synthetic-root-uuid'} for i,p in enumerate(('/var/lib/docker','/var/lib/containerd'),100)]
        plan={'schema':'web_engine_transaction_plan/1','operation_id':'security-web-engine-20261023-07',
            'nonce':'304b2ec68d304738a43794f5a666b6c8',
            'containers':rows,'datastore_probes':probes,'store_roots':roots,'deadline_utc':'2026-10-24T07:00:00Z'}
        plan_sha=sha256(raw(plan)).hexdigest()
        volumes={name:{'backing_kind':'local_bind','backing_identity':{'path':'/srv/attached/'+name,'device':2065},
            'record':{'driver':'local','options':{'type':'none','o':'bind','device':'/srv/attached/'+name}}}
            for name in ('audit','postgres','redis','uploads')}
        park={'schema':'web_qa_parking/1','original_plan_sha256':plan_sha,'phase':'qa_parked_production_incident_open',
            'qa_parking_verified':True,'parked_count':10,'retained_count':17,'parked_ids':parked,'public_8443_closed':True,
            'data_deleted':False,'security_transition_completed':False,'preserved_resources':{'volumes':volumes,
                'parent_mount':{'uuid':'e7f47458-ffc1-4181-81e1-58c6fc4519a2','device':2065},'secret_metadata':{}}}
        park_sha=sha256(raw(park)).hexdigest()
        capture={'schema':'capture2_observability_activation/1','original_plan_sha256':plan_sha,
            'parking_audit_sha256':park_sha,'phase':'diagnostic_image_active_incident_open','new_capture_id':M.CAPTURE,
            'candidate_image':M.FUNCTIONAL,'all_other_26_configuration_unchanged':True,'candidate_configuration_accepted':True,
            'base_image':M.BASE,'original_c07_capture_id':M.OLD_CAPTURE,'source_sha256':M.FUNCTIONAL_LABELS['io.gold-trade.capture.replay.source-sha256'],
            'capture_functional_repair_accepted':False,'security_transition_completed':False}
        closure={'schema':'web_c07_terminal_closure/1','original_plan_sha256':plan_sha,
            'phase':'aborted_before_install_fences_released','owned_source_and_reservation_released':True,
            'accepted_capture06_audit_sha256':sha256(raw(capture)).hexdigest(),'qa_parking_audit_sha256':park_sha,
            'retained_count':17,'retained_expected_running':16,'parked_count':10,'qa_port_8443_closed':True,
            'known_market_incident_ids':sorted(M.KNOWN_HEALTH),'known_market_incidents_status':'open','security_transition_completed':False}
        current=deepcopy(rows)
        for row in current:
            row['restarting']=False
            if row['id']==M.OLD_CAPTURE:row.update(id=M.CAPTURE,image=M.FUNCTIONAL)
            if row['id'] in parked:row.update(running=False,restart_policy={'Name':'no','MaximumRetryCount':0})
        current.sort(key=lambda row:row['id'])
        images={M.IMAGE['image_id']:{'id':M.IMAGE['image_id'],**{k:v for k,v in M.IMAGE.items() if k!='image_id'}},
            M.BASE:{'id':M.BASE,'layers':['sha256:'+'1'*64]},
            M.FUNCTIONAL:{'id':M.FUNCTIONAL,'layers':['sha256:'+'1'*64,'sha256:'+'2'*64,'sha256:'+'3'*64],
                'functional':deepcopy(M.FUNCTIONAL_LABELS)}}
        proof={'containers':current,'configuration_source':deepcopy(M.CONFIGURATION),'source_image':deepcopy(M.IMAGE),'capture_lineage':images}
        proof_sha=sha256(raw(proof)).hexdigest();database=next(r for r in rows if r['name'].endswith('-market-database-1'))
        nonce='1'*16+'2'*16
        resources=[{'container':'market_pipeline_restore_'+n,'volume':'market_pipeline_restore_'+n+'_data',
            'label':'market-pipeline-backup-'+n} for n in (nonce[:16],nonce[16:])]
        intent={'schema':'security_web_market_hot3_intent/1','operation_id':'security-web-market-hot3-20261024-01',
            'attempt_id':'security-web-market-hot3-20261024-01-attempt03','nonce':nonce,'deadline_utc':'2026-10-24T20:08:55.730262Z',
            'managed_receipt_sha256':M.MANAGED_RECEIPT,'public_source_evidence_sha256':proof_sha,'native_capacity_reserved':False,
            'database':{'container_id':database['id'],'image':database['image'],'user':'market_data','database':'market_archive'},
            'restores':resources,'dump_max_bytes':10000,'max_cipher_bytes':14096}
        intent_sha=sha256(raw(intent)).hexdigest();binding={'intent_sha256':intent_sha}
        inventory={'schema_versions':[1,2],'table_count':1,'fact_count':5,'table_row_counts':{'market_facts':5},
            'sequence_values':{'fact_seq':{'last_value':5,'is_called':True}},'schema_catalog_sha256':'a'*64,'schema_objects_sha256':'b'*64}
        source={'container_id':database['id'],'database':'market_archive','database_identity_sha256':'d'*64,
            'database_size_bytes':1000,**inventory}
        restore={'status':'PASS','cleanup_status':'PASS',**inventory}
        artifact={'path':'/srv/synthetic/archive.dump','size_bytes':1234,'sha256':'e'*64}
        common={'backup_mode':'hot','release_sha':M.CONFIGURATION['release_sha'],'release_tree':M.CONFIGURATION['release_tree'],
            'image_id':M.IMAGE['image_id'],'image_input_signature':M.IMAGE['image_input_signature']}
        create={'schema':'market_pipeline_backup_restore/1.4','status':'PASS','off_host_copy_required':True,**common,
            'backup_run_id':'3'*16,'created_at_utc':'2026-10-24T18:30:00Z','role_env_binding':{'path':'/synthetic/metadata-only.env'},
            'source':source,'source_after':deepcopy(source),'backup':artifact,'restore_smoke':restore,
            'database_mutated':False,'services_started':False,'secrets_disclosed':False}
        journal={'schema':'market_pipeline_backup_journal/1.2','status':'COMPLETE','backup_status':'PASS',**common,
            'run_id':create['backup_run_id'],'created_at_utc':create['created_at_utc'],'role_env_binding':create['role_env_binding'],
            'source_before':source,'source_after':create['source_after'],'backup':artifact,'restore_smoke':restore,
            'restore_resources':resources[0],'artifact_path':artifact['path'],'secrets_disclosed':False}
        verify={**binding,'status':'PASS','backup_mode':'hot','receipt':deepcopy(create),'fresh_restore_verified':True,
            'current_environment_verified':True,'eligible_for_new_deployment':False,'off_host_copy_required':True,
            'restore_resources':resources[1],'independent_restore':deepcopy(restore),'current_source':deepcopy(source),
            'current_source_after':deepcopy(source),'secrets_disclosed':False}
        budget={**binding,'schema':'security_web_market_hot3_dump_budget/1','native_capacity_reserved':False,'effective_dump_max_bytes':9000,
            'native_capacity':{'reserve_bytes':8*1024**3,'control_allowance_bytes':1024**2,'device':2064,
                'root':'/srv/trading-bot/backups','files_created':False,'plaintext_materialized_offhost':False,
                'free_bytes':8*1024**3+1024**2+14096,'max_cipher_bytes':14096}}
        crypt={'schema':'market_pipeline_backup_encryption/1.1','plaintext_sha256':artifact['sha256'],
            'plaintext_size_bytes':artifact['size_bytes'],'ciphertext_sha256':'f'*64,'ciphertext_size_bytes':1248,
            'plaintext_materialized_offhost':False,'secrets_disclosed':False}
        offhost={'status':'verified','manifest_sha256':intent_sha,'operation_id':intent['operation_id'],'nonce':nonce,
            'deadline_utc':intent['deadline_utc'],'ciphertext_sha256':crypt['ciphertext_sha256'],'ciphertext_size_bytes':1248,
            'fsync_completed':True,'plaintext_materialized_offhost':False}
        result={**binding,'phase':'hot_backup_offhost_verified_not_accepted','create_receipt_sha256':sha256(raw(create)).hexdigest(),
            'create_journal_sha256':sha256(raw(journal)).hexdigest(),'independent_restore_sha256':sha256(raw(verify)).hexdigest(),
            'encryption_receipt_sha256':sha256(raw(crypt)).hexdigest(),'dump_budget_sha256':sha256(raw(budget)).hexdigest(),
            'managed_receipt_sha256':M.MANAGED_RECEIPT,'public_source_evidence_sha256':proof_sha,
            'configuration_source':deepcopy(M.CONFIGURATION),'source_image':deepcopy(M.IMAGE),'offhost':offhost,
            'ciphertext_sha256':crypt['ciphertext_sha256'],'ciphertext_size_bytes':1248,
            'security_accepted':False,'deployment_eligible':False,'production_services_changed':False}
        cleanup={**binding,'all_owned_resources_absent':True,'worker_succeeded':True,'original_marker_retained':True,'pending_requests_adopted':False}
        complete={**binding,'result_sha256':sha256(raw(result)).hexdigest(),'cleanup_sha256':sha256(raw(cleanup)).hexdigest(),
            'phase':result['phase'],'security_accepted':False,'deployment_eligible':False,'production_services_changed':False}
        return dict(c07_plan=plan,qa_parking=park,capture06=capture,closure=closure,managed_public=proof,
            hot_intent=intent,hot_complete=complete,hot_result=result,hot_cleanup=cleanup,hot_dump_budget=budget,
            hot_create=create,hot_journal=journal,hot_independent=verify,hot_encryption=crypt)

    def repin(self,name):
        self.buffers[name]=raw(self.docs[name]);self.pins[name]=sha256(self.buffers[name]).hexdigest()

    def rechain_result(self):
        for key,name in (('create_receipt_sha256','hot_create'),('create_journal_sha256','hot_journal'),
                ('independent_restore_sha256','hot_independent'),('encryption_receipt_sha256','hot_encryption'),('dump_budget_sha256','hot_dump_budget')):
            self.docs['hot_result'][key]=self.pins[name]
        self.repin('hot_result')
        self.docs['hot_complete']['result_sha256']=self.pins['hot_result']
        self.docs['hot_complete']['cleanup_sha256']=self.pins['hot_cleanup'];self.repin('hot_complete')

    def test_exact_current_profile_preserves_27_with_six_id_bound_stores_and_hot_data_only(self):
        profile=M.derive_current_profile(self.buffers)
        self.assertEqual(len(profile['containers']),27);self.assertEqual(len(profile['retained_ids']),17)
        self.assertEqual(sum(r['running'] for r in profile['containers']),16);self.assertEqual(len(profile['datastore_probes']),6)
        self.assertEqual({r['id'] for r in profile['containers']},({r['id'] for r in self.docs['c07_plan']['containers']}-{M.OLD_CAPTURE})|{M.CAPTURE})
        self.assertTrue(all('name' not in p for p in profile['datastore_probes']))
        self.assertEqual(profile['store_roots'],self.docs['c07_plan']['store_roots'])
        self.assertEqual(profile['parked_resources'],self.docs['qa_parking']['preserved_resources'])
        self.assertEqual(profile['known_health_incidents']['status'],'open')
        self.assertFalse(profile['maintenance_authorized']);self.assertFalse(profile['cold_engine_archive_exists'])
        self.assertFalse(profile['current_runtime_rechecked']);self.assertEqual(profile['hot_backup_data']['backup_mode'],'hot')
        self.assertNotEqual(profile['configuration_source']['release_sha'],profile['source_image']['release_sha'])

    def test_profile_validation_refuses_qa_resurrection_extra_id_and_changed_mount_network_policy(self):
        profile=M.derive_current_profile(self.buffers)
        changes=[]
        qa=next(i for i,r in enumerate(profile['containers']) if r['id'] in profile['parked_ids'])
        for key,value in (('running',True),('restart_policy',{'Name':'always','MaximumRetryCount':0}),('network_mode','foreign')):
            changed=deepcopy(profile);changed['containers'][qa][key]=value;changes.append(changed)
        changed=deepcopy(profile);changed['containers'].append(deepcopy(changed['containers'][0]));changes.append(changed)
        changed=deepcopy(profile);changed['containers'][0]['mounts'][0]['Source']='/different';changes.append(changed)
        for changed in changes:
            with self.subTest(changed=changed['containers'][qa]),self.assertRaisesRegex(M.ProfileError,'current_mismatch'):
                M.validate_current_profile(changed,self.buffers)

    def test_managed_provenance_cannot_reflect_config_into_oci_or_assume_historical_env_proof(self):
        for key,value in (('configuration_source',deepcopy(M.IMAGE)),('source_image',deepcopy(M.CONFIGURATION))):
            with self.subTest(key=key):
                original=self.docs['managed_public'][key];self.docs['managed_public'][key]=value;self.repin('managed_public')
                with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)
                self.docs['managed_public'][key]=original;self.repin('managed_public')
        self.docs['managed_public']['configuration_source']['historical_entire_env_contents_verified']=True;self.repin('managed_public')
        with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)

    def test_false_flags_require_json_booleans_even_when_all_hashes_are_resealed(self):
        self.docs['hot_result']['security_accepted']=0;self.repin('hot_result');self.rechain_result()
        with self.assertRaisesRegex(M.ProfileError,'global_acceptance_forbidden'):M.derive_current_profile(self.buffers)

    def test_public_proof_cannot_resurrect_parked_qa_or_invent_an_extra_container(self):
        before=deepcopy(self.docs['managed_public'])
        qa=next(r for r in self.docs['managed_public']['containers'] if r['project']==M.QA_PROJECT)
        qa['running']=True;self.repin('managed_public')
        with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)
        self.docs['managed_public']=before
        self.docs['managed_public']['containers'].append(deepcopy(before['containers'][0]));self.repin('managed_public')
        with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)

    def test_old_receipt_or_quiesced_mode_is_not_reinterpreted_as_hot_data(self):
        self.docs['hot_create']['schema']='market_pipeline_backup_restore/1.2'
        self.repin('hot_create');self.rechain_result()
        with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)
        self.docs['hot_create']['schema']='market_pipeline_backup_restore/1.4'
        self.docs['hot_create']['backup_mode']='quiesced';self.repin('hot_create');self.rechain_result()
        with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)

    def test_budget_requires_full_native_reserve_and_integer_effective_cap(self):
        self.docs['hot_dump_budget']['native_capacity']['reserve_bytes']-=1
        self.repin('hot_dump_budget');self.rechain_result()
        with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)
        self.docs['hot_dump_budget']['native_capacity']['reserve_bytes']+=1
        self.docs['hot_dump_budget']['effective_dump_max_bytes']=True
        self.repin('hot_dump_budget');self.rechain_result()
        with self.assertRaisesRegex(M.ProfileError,'dump_budget'):M.derive_current_profile(self.buffers)

    def test_pure_derivation_never_opens_files_or_claims_a_new_window(self):
        with patch('builtins.open',side_effect=AssertionError('file_reads_forbidden')):
            result=M.derive_current_profile(self.buffers)
        self.assertNotIn('deadline_utc',result)
        for key in ('maintenance_authorized','security_accepted','deployment_eligible','cold_engine_archive_exists','reboot_authorized'):
            self.assertIs(result[key],False)

    def test_dirty_cleanup_and_unchained_receipt_sha_cannot_pass(self):
        self.docs['hot_cleanup']['all_owned_resources_absent']=False;self.repin('hot_cleanup');self.rechain_result()
        with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)
        self.docs['hot_cleanup']['all_owned_resources_absent']=True;self.repin('hot_cleanup');self.rechain_result()
        self.docs['hot_complete']['result_sha256']='0'*64;self.repin('hot_complete')
        with self.assertRaises(M.ProfileError):M.derive_current_profile(self.buffers)

    def test_hot_mode_and_two_actual_independent_restore_inventories_are_required(self):
        self.docs['hot_independent']['independent_restore']['table_row_counts']['market_facts']=6
        self.repin('hot_independent');self.rechain_result()
        with self.assertRaisesRegex(M.ProfileError,'restore_inventory'):M.derive_current_profile(self.buffers)

    def test_control_buffers_are_bounded_pinned_duplicate_safe_and_no_io_is_available(self):
        broken=dict(self.buffers);broken['hot_complete']+=b' '
        with self.assertRaisesRegex(M.ProfileError,'control_digest'):M.derive_current_profile(broken)
        broken=dict(self.buffers);broken['hot_complete']=b'x'*(2*1024**2+1)
        with self.assertRaises(M.ProfileError):M.derive_current_profile(broken)
        self.buffers['hot_complete']=b'{"status":false,"status":false}'
        self.pins['hot_complete']=sha256(self.buffers['hot_complete']).hexdigest()
        with self.assertRaisesRegex(M.ProfileError,'duplicate_json_key'):M.derive_current_profile(self.buffers)

    def test_derivation_does_not_mutate_controls_or_share_output_objects(self):
        before=deepcopy(self.docs);profile=M.derive_current_profile(self.buffers)
        profile['containers'][0]['mounts'][0]['Source']='/changed-output'
        profile['parked_resources']['parent_mount']['uuid']='changed-output'
        self.assertEqual(self.docs,before);self.assertNotEqual(M.derive_current_profile(self.buffers),profile)


if __name__=='__main__':unittest.main()
