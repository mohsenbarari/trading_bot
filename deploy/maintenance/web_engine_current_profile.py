"""Pure current-profile derivation from pinned public controls; no execution authority."""
from copy import deepcopy
from hashlib import sha256
import json
import re

CONTROL_SHA256 = {
    'c07_plan':'f8b4759ea648ec0d8b8528f43c74ecc281e68b2d2fd94431398923c62e0f16c5',
    'qa_parking':'d9ddd4d3c1c5771f20014d1d986bae720e82439ff07a1869653c1174ea465e9e',
    'capture06':'22923a5f68f415e86ffd7f1db0563ed8973d7bc3dd6fbce39537e17dcf810dbf',
    'closure':'712af9788ef73e3934252d7b7e50bd0681bbfea13c25088da59a123efabb5dc0',
    'managed_public':'2e13ad0997eacd9ec8cf1f6cd6dc22494b3f81b0216a4ec684559f28e4520f3c',
    'hot_intent':'6426b1f7e6ecbc8690180b3ce8b7fe7928f348fc61336262d8266b28d111b477',
    'hot_complete':'ae5afcc96fe68756af5a607f6ad3bac71a5b0586615d0a716d27f25951723e3b',
    'hot_result':'d4b2e1deffc65dd5a8a458ef837a79a344cce7640519412c7598a2365d25288e',
    'hot_cleanup':'ae7013f4b32a777f4dd54e1359c34ed6991108cac10eefdcc43d86dcd8727d1f',
    'hot_create':'38af1bd0b01ac0d7748b70332923ddd9e10ba8ef0b19124d1bfa9ac89c93232e',
    'hot_journal':'040a87c3558d21bb04845597875bb487036b4b4f33fc4bbeb08d06a92de14851',
    'hot_independent':'8a2c43dfe77573ea3f8e2d4f890cb74ba382a12217fe2f56c5968ed7ed83bb50',
    'hot_encryption':'92622ef041c6a47981ebdef57c1c52c194f8303ba8def80e963b58afc08942a2',
    'hot_dump_budget':'c082592f2811360293227cbfdbfbb2f1d4d7ac8db900341d021169e6b103c25f',
}
MANAGED_RECEIPT = '305de6c1fc3a1997e53f91c689f83d038c37383d27b15d6d4571281dfb233e9c'
OLD_CAPTURE = '9870311065b13646767dfad1e48214d6a231e3898e1f248bb9559edd8926966c'
CAPTURE = '6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'
BASE = 'sha256:2239469ab3dd3e291e16b4571af84d446f776ade05d48552f150b7ec2670fcc6'
FUNCTIONAL = 'sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b'
FUNCTIONAL_LABELS = {'io.gold-trade.capture.replay.baseline':'d8f15f8212b9510e3abb5301d0201b2ba1eca13224f5a01c682d1de6716cb7d5',
    'io.gold-trade.capture.replay.fix-commit':'adca507b261d3e139befc67b4a937c48c96bb949',
    'io.gold-trade.capture.replay.source-sha256':'1b9c611692978edec4c37de217e77abf00f5a0e456006060c936d40c53978929',
    'io.gold-trade.capture.replay.status':'functional_fix_not_security_acceptance'}
CONFIGURATION = {'release_sha':'f3be9ae2c4e9df0da31abaa41e91f327eb9573a2',
    'release_tree':'d87bb5a0f7d9f96edfdad177c66f7afd120e02f7',
    'provenance_kind':'root_verified_public_git_commit_tree','historical_entire_env_contents_verified':False}
IMAGE = {'image_id':'sha256:e46aedbe135117301fb19183ff39587174162ca4a454ac60c7fccb2b302033cd',
    'release_sha':'a6dcd636a1525a3a3d47e2a6083ca887b16080db','release_tree':'259c9c4964864b7acfd92f94c9856e664130d082',
    'image_input_signature':'669ee2267889d54bb36ef4cd8184d7a02c10fadeb4f39609e60ddce9f6f62a69'}
QA_PROJECT = 'three-site-dc32d90392b0-webapp-fi'
KNOWN_HEALTH = {'f2dbc15b9c9a2d14ef9669d1f6de37096161469a50c47bf6609f0383886b3a27',
    'f0f5ddc3efe1a55cc8a6f800ad59d1f41556a4f896cdc8ed7d20864c9d360e2a'}
FALSE_FLAGS = {'security_accepted','security_transition_completed','deployment_eligible','eligible_for_new_deployment',
    'production_services_changed','secrets_disclosed','database_mutated','services_started','plaintext_materialized_offhost'}
FIELDS = ('id','name','image','running','restart_policy','project','network_mode','port_bindings')
INVARIANTS = ('schema_versions','table_count','fact_count','table_row_counts','sequence_values',
    'schema_catalog_sha256','schema_objects_sha256')


class ProfileError(RuntimeError):
    """Closed reason only; callers never need to emit public control contents."""


def require(ok, code):
    if not ok: raise ProfileError(code)


def equal(a, b):
    if type(a) is not type(b): return False
    if type(b) is dict: return set(a) == set(b) and all(equal(a[k],v) for k,v in b.items())
    if type(b) is list: return len(a) == len(b) and all(equal(x,y) for x,y in zip(a,b))
    return a == b


def fields(value, expected):
    require(type(value) is dict and all(k in value and equal(value[k],v) for k,v in expected.items()), 'profile_control_contract')


def false_flags(value):
    if type(value) is dict:
        for key,item in value.items():
            if key in FALSE_FLAGS: require(item is False, 'profile_global_acceptance_forbidden')
            false_flags(item)
    elif type(value) is list:
        for item in value: false_flags(item)


def parse_controls(buffers):
    require(type(buffers) is dict and set(buffers) == set(CONTROL_SHA256), 'profile_control_inventory')
    def unique(pairs):
        result = {}
        for key,value in pairs:
            require(key not in result, 'profile_duplicate_json_key'); result[key] = value
        return result
    result = {}
    for name,raw in buffers.items():
        require(type(raw) is bytes and 0 < len(raw) <= 2*1024**2 and sha256(raw).hexdigest() == CONTROL_SHA256[name], 'profile_control_digest')
        try: value = json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: require(False,'profile_json_constant'))
        except (ValueError, UnicodeError, RecursionError): raise ProfileError('profile_control_json') from None
        require(type(value) is dict, 'profile_control_json'); false_flags(value); result[name] = value
    return result


def canonical(row):
    require(type(row) is dict and type(row.get('running')) is bool and type(row.get('restart_policy')) is dict, 'profile_container_shape')
    require(type(row.get('id')) is str and re.fullmatch('[a-f0-9]{64}',row['id'])
        and type(row.get('image')) is str and re.fullmatch('sha256:[a-f0-9]{64}',row['image']), 'profile_container_identity')
    policy = row['restart_policy']
    require(set(policy) == {'Name','MaximumRetryCount'} and policy['Name'] in {'always','unless-stopped','no','on-failure'}
        and type(policy['MaximumRetryCount']) is int and policy['MaximumRetryCount'] >= 0, 'profile_restart_policy')
    mounts = [{key:m[key] for key in ('Destination','Mode','Propagation','RW','Source','Type')} for m in row['mounts']]
    require(len({m['Destination'] for m in mounts}) == len(mounts) and all(type(m['RW']) is bool for m in mounts), 'profile_mount_shape')
    return {**{k:deepcopy(row[k]) for k in FIELDS},'restarting':False,
        'network_names':sorted(row['network_names'] or {}),'mounts':sorted(mounts,key=lambda m:m['Destination'])}


def current_rows(d):
    plan,park,capture,closure,proof = (d[k] for k in ('c07_plan','qa_parking','capture06','closure','managed_public'))
    fields(plan,{'schema':'web_engine_transaction_plan/1','operation_id':'security-web-engine-20261023-07',
        'nonce':'304b2ec68d304738a43794f5a666b6c8','deadline_utc':'2026-10-24T07:00:00Z'})
    old = plan['containers']; require(type(old) is list and len(old) == 27 and len({r['id'] for r in old}) == 27, 'profile_original_inventory')
    require(sum(r['running'] is True for r in old) == 26, 'profile_original_intent')
    parked = {r['id'] for r in old if r['project'] == QA_PROJECT}
    fields(park,{'schema':'web_qa_parking/1','original_plan_sha256':CONTROL_SHA256['c07_plan'],
        'phase':'qa_parked_production_incident_open','qa_parking_verified':True,'parked_count':10,'retained_count':17,
        'parked_ids':sorted(parked),'public_8443_closed':True,'data_deleted':False,'security_transition_completed':False})
    require(len(parked) == 10, 'profile_parking_scope')
    fields(capture,{'schema':'capture2_observability_activation/1','original_plan_sha256':CONTROL_SHA256['c07_plan'],
        'parking_audit_sha256':CONTROL_SHA256['qa_parking'],'phase':'diagnostic_image_active_incident_open',
        'new_capture_id':CAPTURE,'candidate_image':FUNCTIONAL,'all_other_26_configuration_unchanged':True,
        'candidate_configuration_accepted':True,'capture_functional_repair_accepted':False,'security_transition_completed':False,
        'base_image':BASE,'original_c07_capture_id':OLD_CAPTURE,'source_sha256':FUNCTIONAL_LABELS['io.gold-trade.capture.replay.source-sha256']})
    fields(closure,{'schema':'web_c07_terminal_closure/1','original_plan_sha256':CONTROL_SHA256['c07_plan'],
        'phase':'aborted_before_install_fences_released','owned_source_and_reservation_released':True,
        'accepted_capture06_audit_sha256':CONTROL_SHA256['capture06'],'qa_parking_audit_sha256':CONTROL_SHA256['qa_parking'],
        'retained_count':17,'retained_expected_running':16,'parked_count':10,'qa_port_8443_closed':True,
        'known_market_incident_ids':sorted(KNOWN_HEALTH),'known_market_incidents_status':'open','security_transition_completed':False})
    require(KNOWN_HEALTH <= {r['id'] for r in old}-parked, 'profile_known_incident_identity')
    rows = []
    for before in old:
        row = canonical(before)
        if row['id'] == OLD_CAPTURE:
            require(row['image'] == BASE, 'profile_original_capture'); row.update(id=CAPTURE,image=FUNCTIONAL)
        if row['id'] in parked: row.update(running=False,restart_policy={'Name':'no','MaximumRetryCount':0})
        rows.append(row)
    rows.sort(key=lambda r:r['id'])
    require(sum(r['running'] for r in rows) == 16 and CAPTURE in {r['id'] for r in rows} and OLD_CAPTURE not in {r['id'] for r in rows}, 'profile_current_intent')
    fields(proof,{'containers':rows,'configuration_source':CONFIGURATION,'source_image':IMAGE})
    require(sum(r['name'] == '/market-private-pipeline-primary-market-capture-account1-1' and r['image'] == IMAGE['image_id']
        and r['running'] is True for r in rows) == 1, 'profile_source_anchor')
    images = proof['capture_lineage']; anchor = images[IMAGE['image_id']]
    fields(anchor,{'id':IMAGE['image_id'],**{k:v for k,v in IMAGE.items() if k != 'image_id'}})
    layers,derived = images[BASE]['layers'],images[FUNCTIONAL]['layers']
    fields(images[BASE],{'id':BASE}); fields(images[FUNCTIONAL],{'id':FUNCTIONAL,'functional':FUNCTIONAL_LABELS})
    require(type(layers) is list and layers and type(derived) is list and len(derived) == len(layers)+2
        and derived[:len(layers)] == layers, 'profile_capture_lineage')
    resources = park['preserved_resources']; fields(resources['parent_mount'],{'uuid':'e7f47458-ffc1-4181-81e1-58c6fc4519a2'})
    require(type(resources['volumes']) is dict and len(resources['volumes']) == 4, 'profile_backing_inventory')
    for value in resources['volumes'].values():
        fields(value,{'backing_kind':'local_bind'}); fields(value['record'],{'driver':'local'})
        options = value['record']['options']; fields(options,{'type':'none','o':'bind','device':value['backing_identity']['path']})
        require(value['backing_identity']['device'] == resources['parent_mount']['device'], 'profile_backing_filesystem')
    require(type(plan['store_roots']) is list and {v['path'] for v in plan['store_roots']} == {'/var/lib/docker','/var/lib/containerd'}, 'profile_engine_roots')
    running = {r['id'] for r in rows if r['running']}
    stores = {r['id'] for r in rows if r['id'] in running and (r['name'] in {'/trading_bot_db','/trading_bot_redis'}
        or r['name'].endswith(('_db-1','-db-1','-market-database-1','_redis-1','-redis-1')))}
    probes = [deepcopy(p) for p in plan['datastore_probes'] if p['id'] not in parked]
    require(len(probes) == len(stores) == 6 and {p['id'] for p in probes} == stores, 'profile_datastore_scope')
    for p in probes:
        require(set(p) == ({'id','kind'} if p.get('kind') == 'redis' else {'id','kind','user','database'})
            and p['kind'] in {'redis','postgres'} and all(type(p[k]) is str and re.fullmatch('[A-Za-z0-9_]{1,63}',p[k])
            for k in ('user','database') if k in p), 'profile_datastore_shape')
    return rows,sorted(parked),sorted(probes,key=lambda p:p['id'])


def hot_data(d, rows, probes):
    intent,complete,result,cleanup,budget,create,journal,verify,crypt = (d[k] for k in
        ('hot_intent','hot_complete','hot_result','hot_cleanup','hot_dump_budget','hot_create','hot_journal','hot_independent','hot_encryption'))
    binding = {'intent_sha256':CONTROL_SHA256['hot_intent']}
    for value in (complete,result,cleanup,budget,verify): fields(value,binding)
    fields(complete,{'result_sha256':CONTROL_SHA256['hot_result'],'cleanup_sha256':CONTROL_SHA256['hot_cleanup'],
        'phase':'hot_backup_offhost_verified_not_accepted','security_accepted':False,'deployment_eligible':False,'production_services_changed':False})
    fields(cleanup,{'all_owned_resources_absent':True,'worker_succeeded':True,'original_marker_retained':True,'pending_requests_adopted':False})
    fields(result,{'phase':'hot_backup_offhost_verified_not_accepted','create_receipt_sha256':CONTROL_SHA256['hot_create'],
        'create_journal_sha256':CONTROL_SHA256['hot_journal'],'independent_restore_sha256':CONTROL_SHA256['hot_independent'],
        'encryption_receipt_sha256':CONTROL_SHA256['hot_encryption'],'dump_budget_sha256':CONTROL_SHA256['hot_dump_budget'],
        'managed_receipt_sha256':MANAGED_RECEIPT,'public_source_evidence_sha256':CONTROL_SHA256['managed_public'],
        'configuration_source':CONFIGURATION,'source_image':IMAGE,'security_accepted':False,'deployment_eligible':False,'production_services_changed':False})
    fields(intent,{'schema':'security_web_market_hot3_intent/1','operation_id':'security-web-market-hot3-20261024-01',
        'attempt_id':'security-web-market-hot3-20261024-01-attempt03','native_capacity_reserved':False,
        'managed_receipt_sha256':MANAGED_RECEIPT,'public_source_evidence_sha256':CONTROL_SHA256['managed_public']})
    fields(budget,{'schema':'security_web_market_hot3_dump_budget/1','native_capacity_reserved':False})
    require(type(budget['effective_dump_max_bytes']) is type(intent['dump_max_bytes']) is int
        and 0 < budget['effective_dump_max_bytes'] <= intent['dump_max_bytes'] <= 64*1024**3, 'profile_dump_budget')
    native = budget['native_capacity']
    fields(native,{'reserve_bytes':8*1024**3,'control_allowance_bytes':1024**2,'device':2064,
        'root':'/srv/trading-bot/backups','files_created':False,'plaintext_materialized_offhost':False})
    require(type(native['free_bytes']) is type(native['max_cipher_bytes']) is int
        and native['max_cipher_bytes'] == max(0,native['free_bytes']-8*1024**3-1024**2)
        and budget['effective_dump_max_bytes'] <= native['max_cipher_bytes']-4096, 'profile_dump_budget')
    database = next(r for r in rows if r['name'].endswith('-market-database-1') and r['project'] == 'market-private-pipeline-primary')
    probe = next(p for p in probes if p['id'] == database['id'])
    fields(intent['database'],{'container_id':database['id'],'image':database['image'],'user':probe['user'],'database':probe['database']})
    common = {'backup_mode':'hot','release_sha':CONFIGURATION['release_sha'],'release_tree':CONFIGURATION['release_tree'],
        'image_id':IMAGE['image_id'],'image_input_signature':IMAGE['image_input_signature']}
    fields(create,{'schema':'market_pipeline_backup_restore/1.4','status':'PASS','off_host_copy_required':True,
        'database_mutated':False,'services_started':False,'secrets_disclosed':False,**common})
    fields(journal,{'schema':'market_pipeline_backup_journal/1.2','status':'COMPLETE','backup_status':'PASS',**common})
    for ck,jk in (('backup_run_id','run_id'),('created_at_utc','created_at_utc'),('role_env_binding','role_env_binding'),
            ('source','source_before'),('source_after','source_after'),('backup','backup'),('restore_smoke','restore_smoke')):
        require(equal(create[ck],journal[jk]), 'profile_create_journal_mismatch')
    fields(verify,{'status':'PASS','backup_mode':'hot','receipt':create,'fresh_restore_verified':True,
        'current_environment_verified':True,'eligible_for_new_deployment':False,'off_host_copy_required':True,'secrets_disclosed':False})
    restores = intent['restores']; require(type(restores) is list and len(restores) == 2, 'profile_restore_bindings')
    require(set(restores[0]) == set(restores[1]) == {'container','volume','label'}
        and all(restores[0][k] != restores[1][k] for k in restores[0]), 'profile_restore_independence')
    nonce = intent['nonce']; require(type(nonce) is str and re.fullmatch('[a-f0-9]{32}',nonce) and nonce[:16] != nonce[16:], 'profile_restore_bindings')
    require(equal(restores,[{'container':'market_pipeline_restore_'+n,'volume':'market_pipeline_restore_'+n+'_data',
        'label':'market-pipeline-backup-'+n} for n in (nonce[:16],nonce[16:])]), 'profile_restore_bindings')
    fields(journal,{'restore_resources':restores[0]}); fields(verify,{'restore_resources':restores[1]})
    for restore in (create['restore_smoke'],verify['independent_restore']): fields(restore,{'status':'PASS','cleanup_status':'PASS'})
    for key in INVARIANTS: require(equal(create['restore_smoke'][key],verify['independent_restore'][key]), 'profile_restore_inventory')
    source = create['source_after']
    for observed in (create['source'],source,verify['current_source'],verify['current_source_after']):
        fields(observed,{k:source[k] for k in ('container_id','database','database_identity_sha256','schema_versions','schema_catalog_sha256','schema_objects_sha256')})
        fields(observed,{'container_id':database['id'],'database':probe['database']})
    artifact = create['backup']; require(type(artifact['size_bytes']) is int and 0 < artifact['size_bytes'] <= budget['effective_dump_max_bytes']
        and re.fullmatch('[a-f0-9]{64}',artifact['sha256']), 'profile_backup_artifact')
    fields(journal,{'artifact_path':artifact['path']})
    fields(crypt,{'schema':'market_pipeline_backup_encryption/1.1','plaintext_sha256':artifact['sha256'],'plaintext_size_bytes':artifact['size_bytes'],
        'ciphertext_sha256':result['ciphertext_sha256'],'ciphertext_size_bytes':result['ciphertext_size_bytes'],
        'plaintext_materialized_offhost':False,'secrets_disclosed':False})
    require(type(crypt['ciphertext_size_bytes']) is int and 0 < crypt['ciphertext_size_bytes'] <= intent['max_cipher_bytes']
        and type(crypt['ciphertext_sha256']) is str and re.fullmatch('[a-f0-9]{64}',crypt['ciphertext_sha256']), 'profile_cipher_identity')
    fields(result['offhost'],{'status':'verified','manifest_sha256':CONTROL_SHA256['hot_intent'],'operation_id':intent['operation_id'],
        'nonce':intent['nonce'],'deadline_utc':intent['deadline_utc'],'ciphertext_sha256':crypt['ciphertext_sha256'],
        'ciphertext_size_bytes':crypt['ciphertext_size_bytes'],'fsync_completed':True,'plaintext_materialized_offhost':False})
    return {'backup_mode':'hot','control_sha256':{k:v for k,v in CONTROL_SHA256.items() if k.startswith('hot_')},
        'artifact':deepcopy(artifact),'created_at_utc':create['created_at_utc'],'ciphertext_sha256':crypt['ciphertext_sha256'],
        'ciphertext_size_bytes':crypt['ciphertext_size_bytes'],'artifact_bytes_rechecked':False,'current_source_rechecked':False}


def derive_current_profile(buffers):
    """Validate frozen public evidence; caller must separately recheck actual runtime/storage."""
    try:
        controls = parse_controls(buffers); rows,parked,probes = current_rows(controls)
        hot = hot_data(controls,rows,probes)
        return {'schema':'web_engine_current_profile/1','mode':'current_profile_from_pinned_controls',
            'containers':rows,'parked_ids':parked,'retained_ids':sorted(r['id'] for r in rows if r['id'] not in parked),
            'expected_running':16,'datastore_probes':probes,'configuration_source':deepcopy(CONFIGURATION),'source_image':deepcopy(IMAGE),
            'store_roots':deepcopy(controls['c07_plan']['store_roots']),'parked_resources':deepcopy(controls['qa_parking']['preserved_resources']),
            'known_health_incidents':{'ids':sorted(KNOWN_HEALTH),'status':'open','acceptance_waived':False},'hot_backup_data':hot,
            'source_control_sha256':dict(CONTROL_SHA256),'current_runtime_rechecked':False,'maintenance_authorized':False,
            'security_accepted':False,'deployment_eligible':False,'cold_engine_archive_exists':False,'reboot_authorized':False}
    except (KeyError,TypeError,ValueError,StopIteration,RecursionError):
        raise ProfileError('profile_control_shape') from None


def validate_current_profile(profile, buffers):
    expected = derive_current_profile(buffers)
    require(equal(profile,expected), 'profile_current_mismatch')
    return expected
