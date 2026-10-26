"""New normal-Compose App activation contract; pure checks, never deployment.

No historical consumer/COW controller or probe is imported. Missing actual host
LOAD, backup/restore, schema or normal-prefix evidence leaves this UNARMED.
"""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import PurePosixPath
import io
import json
import math
import re
import stat
import zipfile

OPERATION = 'security-canonical-app-activation-20261025-01'
CONFIRM = 'ACTIVATE-ONLY-CANONICAL-APP-665220A2-20261025-01'
ROLE_ORDER = (('web','app'),('web','sync_worker'),('bot','app'),('bot','bot'),('bot','sync_worker'))
SOURCE_FILES = ('canonical_app_activation_contract.py','canonical_app_activation_site.py','canonical_app_activation_coordinator.py')
SITES = {'bot':{'project':'trading_bot','workdir':'/root/trading-bot/trading_bot','roles':('app','bot','sync_worker')},
         'web':{'project':'current','workdir':'/srv/trading-bot/current','roles':('app','sync_worker')}}
READER_UNITS = frozenset(base+suffix for base in ('coin-intelligence-staging-market-input-bridge',
    'coin-intelligence-staging-snapshot-publish','coin-intelligence-staging-snapshot-relay',
    'coin-private-shadow-legacy-estimator-bridge') for suffix in ('.service','.timer'))
MAX_JSON = 8*1024**2
MAX_CONTROL = 128*1024
PUBLIC_ENV_DEFAULT_KEYS = frozenset(('PYTHON_VERSION','PYTHON_SHA256'))
PROVENANCE_LABELS = ('org.opencontainers.image.revision','io.gold-trade.release.tree','io.gold-trade.release.input-signature')
SOURCE_ID = [2064,1835555,33152,0,0,1,0,1787336928392178187,1788695466612272194]
READER_POLICY_FIELDS = frozenset(('Id','LoadState','UnitFileState','FragmentPath','DropInPaths','NeedDaemonReload','RefuseManualStart'))
UNIT_PUBLIC_FIELDS = READER_POLICY_FIELDS | {'ActiveState','SubState','ConditionResult','MainPID','ControlPID'}
# Exact665 Dockerfile4406 bytes, authenticated actual input-manifest SHA:
DOCKERFILE_SHA = '3f6541a5dd38c5316fd8ca37c3ea9c85dc80a6f74c10186f7c39f11bf1c9677c'
IMAGE_SOURCE_PATHS = frozenset('/app/'+name for name in ('requirements.txt','api','bot','core','src','migrations','models',
    'templates','fonts','alembic.ini','main.py','manage.py','run_bot.py','schemas.py','seed_fake_data.py',
    'trading_settings.json','scripts','mini_app_dist'))
SMOKE = {'run_id':40196208692,'run_attempt':1,'controller_commit':'200f5c718a7bad0ce0860f37ada66a7baa05a887',
    'artifact_id':12599936628,'zip_bytes':392398,'zip_sha256':'5cd1ab49b517bcd04c7adc1e883ecf351020f96b16ccfec70278037c5c013e99'}
SMOKE_MEMBERS = {'evidence/public-evidence.zip': {'bytes': 336678,
                                  'sha256': 'eac66d11595ae9bd7a4478f5b126ac77e4d36b81dab25613f0d951c833d7b653'},
 'reports/actual-smoke.json': {'bytes': 860,
                               'sha256': 'd9f730ea31f91db251a6734e384719fc2b9e99183ce7e4b60e9b63df680693cb'},
 'reports/app-mount-admission.json': {'bytes': 167,
                                      'sha256': '24a85233df3afe5ae2082cb2e80ef4b6764a1ff0501e5c990e9e4d7bafd0dad8'},
 'reports/authenticated-export.json': {'bytes': 4226,
                                       'sha256': '147b280174a6718ea3e222b47cce1868a7d37093dbdebb0c1a25e3da863ae0e9'},
 'reports/canonical-archive-admission.json': {'bytes': 3631,
                                              'sha256': '19be8ba43f40763af628a3c68ddabaab8cbfed4c62256d8bd13651ddc29738db'},
 'reports/canonical-daemon-admission.json': {'bytes': 389,
                                             'sha256': '1c39a924689ee061d7c3d75f4f335fac549a7f6bcf1bf3bb7d7e2e2907543441'},
 'reports/canonical-source-binding.json': {'bytes': 662,
                                           'sha256': '3a987920c791767d705947606fd5449e69fdc7aed1c85c73413970c0323fe4a7'},
 'reports/cleanup.json': {'bytes': 110,
                          'sha256': 'a0ad3cc662ef09c3a5aeb6c7c21e3a178a7dd00ee49835869dafbe2fd81c1089'},
 'reports/fresh-verify_acl_consumer_presence.json': {'bytes': 276889,
                                                     'sha256': 'a027f0cf40af9aa950ce70b359fe53f7d70aaed5f294031b96d51b359fb78f33'},
 'reports/fresh-verify_image_component_presence.json': {'bytes': 1550,
                                                        'sha256': '90c62aded376737e24d8cff73b126461a564146a462ae95f63e90d0498f4496b'},
 'reports/postgres-admission.json': {'bytes': 2550,
                                     'sha256': 'a94da56b5c1432f9f6cf937e9694ec6689e679f05f9f8f5b3e66d1a3682faafd'},
 'reports/redis-admission.json': {'bytes': 2247,
                                  'sha256': 'e8d370abc3cd07db04780db4b858ebb4deb4abd29093050777a0e8309cd8159c'},
 'reports/source-identity.json': {'bytes': 2781,
                                  'sha256': '4074a67c49ba047c8b8c344c2e0ff1fa74df6a7f4b67bf984d552ef22d606f20'}}
IMAGE = {'archive_observation': {'archive_bytes': 452719104,
                         'archive_sha256': 'e10090370762baf289dd55457f648b5c78b05e8e398c724397336b1d89006c67',
                         'expanded_layer_bytes': 452635136,
                         'index_sha256': 'fa9481689c7165671348103769538e9e17b522f53b3ae51d5b38bbd3aedc022f',
                         'legacy_compatibility_configs': 33,
                         'manifest_id': 'sha256:2f609ac53add114037016e54482486cfb455e41b1d79c6625c2772b07bb81908',
                         'member_count': 71},
 'evidence_artifact_id': 12598516295,
 'evidence_zip_bytes': 336678,
 'evidence_zip_sha256': 'eac66d11595ae9bd7a4478f5b126ac77e4d36b81dab25613f0d951c833d7b653',
 'image_artifact_id': 12598511333,
 'image_id': 'sha256:e1a727de49c45befbcef82f2b93721b780c06b64643d749290ade390215cc9f1',
 'image_zip_bytes': 183170277,
 'image_zip_sha256': '46b1e1f0d826493db7d18b7835acccdb75af4282ac9d504dc21d842e8608b8d6',
 'input_signature': 'a63abda5a1b5fd889575be09d0cdff0412ad0c0cd062f85336fedf0237cf87a2',
 'raw_counts': {'CRITICAL': 0, 'HIGH': 44, 'LOW': 82, 'MEDIUM': 68, 'UNKNOWN': 1},
 'rootfs': {'Layers': ['sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7',
                       'sha256:535a5d38abecfac4fa49da33fa942b53c53c463bd0d39a860967e46eb03faafe',
                       'sha256:6972161a9a77e7c1afb997daac15abc54f5b70c7b40b814601f53e038d8c398c',
                       'sha256:d86e9bd004831a6069b9a02e5caac80b73526315f52719079b4017b099956ed2',
                       'sha256:3e204cd21b2edfd2cf9d77a1d2032725307c8f01043e2eca16f35e801742f66b',
                       'sha256:8de59326698e4655f63fd4e5532c474f3abafda7e32edded25050d5f9eea5820',
                       'sha256:a842894c2b3256a955801a4f3d634ade21c3a93f5f9b5aaf58193576d16655e0',
                       'sha256:c9da23d02bc39c438741e196f1950f1a30107f71bb7192c6af91450ea696f764',
                       'sha256:d31cd1c8922519c5e60c0396430645de0b71973b82136600b382783d11c0b0c8',
                       'sha256:457975dd4444c78af6aac81f1d4b44585052c94aa52a5d942acbdb8561154142',
                       'sha256:8183b6021d6775b488b267abaa469f2dc86e7b73b0a5dd5d3daef9172d0e8074',
                       'sha256:ab4640e9ff51275ad55673a6ba12dd1c7f61512e5d3a5f0c524c7ecbf8130467',
                       'sha256:f66767c7dd83565dcccbbaa6d524faa79a0aa4cee3de2978e2c77e9ac0799b26',
                       'sha256:9539b4fb7d1f332da6d1d041718d0ea6c0312f272fe2b37b0121274363ea35d9',
                       'sha256:d365532c6abd0b186bf0b5b510bb0dffe2ca8c912e6ef7f15b19dcb5d39bad20',
                       'sha256:aba3d55fbc0cd22b6762952e92c7a46892ca2d889c07665719c7fe04225479cf',
                       'sha256:ab7840770e7d2a526cb62900bc9e5dcdfa2454dbf6c6531c13da84e70544bf4d',
                       'sha256:093ccb908184c22066df90e52fc388e3f26f6cd8ec435b9a7b7be2f0cb9ec0d1',
                       'sha256:29b52a15e9e6677ffad8799b8eec656f250035ea1a7403c336cd652aad6cdc8b',
                       'sha256:b06c2c3dd56d631de5deda8744e78b95360c6f31edabfef65643d78f709a0ffc',
                       'sha256:9f19d62d62ec6c2b527d73594454648dbd772bc40114fdcf019ed9605b5085bb',
                       'sha256:a4cd6d8ce7054acdf3fea36cbc0ee6243337b50b5ef7502727cba8c4bd9dc329',
                       'sha256:d29d88236a8b8f0f46a61014304fdeb828ab7a9802c6105048a32f2b1760cede',
                       'sha256:db5de29cb9291c166a771b93fd64dc577f1e5de6e935e7178bf645becd7ae175',
                       'sha256:00aff1708af51ca851723d52ee94b49c13bf2596c6948ceb344ba51d1fe89f8c',
                       'sha256:2014b2e5edc6c1820bc23801c6f6020fdb0b53abe1170d7edbe3ac24d5393b3c',
                       'sha256:b8edb16e8cc1f34718d93054e59e40fafcf727807affaecffc9d10309d2d7108',
                       'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef',
                       'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef',
                       'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef',
                       'sha256:d11e8eb630dda8fb072612381737f8e056c58bd8a412d3274c7694d55239e7a5',
                       'sha256:4c632541a034234eb926259de0f177216f0bbc41516e730a9e7b7d38e0106200',
                       'sha256:b05a2827ff6e9540948e7eda99ae0410a3364373a4cfb5285ac9422c4f0d8180'],
            'Type': 'layers'},
 'run_attempt': 1,
 'run_id': 40191605131,
 'schema': 'canonical_app_smoke_intake/1',
 'source_commit': '665220a2b5086597daa4565eef6450a37aaf47f4',
 'source_tree': 'f522d42f754599bb9ebd0ea0e284a338ec1dcfbf'}


def require(ok, code):
    if not ok: raise RuntimeError(code)


def same(left, right):
    if type(left) is not type(right): return False
    if type(left) is dict: return set(left) == set(right) and all(same(left[k],right[k]) for k in left)
    if type(left) is list: return len(left) == len(right) and all(same(a,b) for a,b in zip(left,right))
    return left == right


def document(raw, maximum=MAX_JSON):
    require(type(raw) is bytes and 0 < len(raw) <= maximum, 'json_bound')
    def unique(rows):
        value = {}
        for key,item in rows: require(key not in value,'duplicate_json_key'); value[key] = item
        return value
    def finite(value):
        result = float(value); require(math.isfinite(result),'nonfinite_json'); return result
    return json.loads(raw,object_pairs_hook=unique,parse_float=finite,
        parse_constant=lambda _:require(False,'nonfinite_json'))


def public(value):
    if type(value) is dict:
        require(not set(value)&{'Config','Env','environment','resolved_environment','runtime_config','access_token',
            'refresh_token','otp_code','session_string','private_configuration'},'private_output')
        for item in value.values(): public(item)
    elif type(value) is list:
        for item in value: public(item)
    elif type(value) is float: require(math.isfinite(value),'nonfinite_json')
    else: require(type(value) in (str,int,bool,type(None)),'output_type')
    return value


def encoded(value):
    public(value); raw = (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    require(len(raw) <= MAX_JSON,'output_bound'); return raw


def digest_record(raw): return {'bytes':len(raw),'sha256':sha256(raw).hexdigest()}


def control_binding(control):
    """Only the public authority. NEVER call this with private Config/Env."""
    return sha256(encoded(control)).hexdigest()


def scope_seconds(value):
    """Only finite, positive systemd display durations within the fixed cap."""
    from decimal import Decimal
    require(type(value) is str and len(value) <= 64 and re.fullmatch(
        r'[0-9]+(?:\.[0-9]+)?(?:h|min|s|ms|us)(?: +[0-9]+(?:\.[0-9]+)?(?:h|min|s|ms|us))*',value),'finite_scope')
    units = {'h':3600,'min':60,'s':1,'ms':Decimal('.001'),'us':Decimal('.000001')}
    total = sum(Decimal(n)*units[u] for n,u in re.findall(r'([0-9]+(?:\.[0-9]+)?)(h|min|s|ms|us)',value))
    require(0 < total <= 7200,'finite_scope'); return total

def hex64(value): return type(value) is str and re.fullmatch('[a-f0-9]{64}',value) is not None

def image_id(value): return type(value) is str and re.fullmatch('sha256:[a-f0-9]{64}',value) is not None


def metadata(value, *, empty=False):
    require(type(value) is list and len(value) == 9 and all(type(v) is int for v in value)
        and value[0] > 0 and value[1] > 0 and value[2:6] == [0o100600,0,0,1]
        and value[6] >= 0 and value[7] >= 0 and value[8] >= 0 and (not empty or value[6] == 0),'metadata_binding')
    return value


def absolute(value):
    require(type(value) is str and value.startswith('/') and '\x00' not in value and '\n' not in value
        and '\\' not in value and '..' not in PurePosixPath(value).parts and str(PurePosixPath(value)) == value,'path_binding')
    return value


def file_binding(value):
    require(type(value) is dict and set(value) == {'name','bytes','sha256'} and type(value['name']) is str
        and re.fullmatch('[a-zA-Z0-9_.-]{1,100}',value['name']) and type(value['bytes']) is int
        and 0 < value['bytes'] <= MAX_JSON and hex64(value['sha256']),'file_binding')
    return value


def unarmed_control():
    """A truthful preparation skeleton; never invents evidence or authority."""
    return {'schema':'canonical_app_activation/1','operation':OPERATION,'nonce':None,'controller_commit':None,
        'source_files':None,'issued_at':None,'forward_deadline':None,'recovery_deadline':None,
        'image':deepcopy(IMAGE),'runtime_env_delta':{},'sites':{'bot':None,'web':None},
        'gates':{'canonical_smoke':{'name':'canonical-smoke.zip','bytes':SMOKE['zip_bytes'],'sha256':SMOKE['zip_sha256']},
                 'host_loads':{'bot':None,'web':None},'backup_restore':None,'schema_compatibility':None},
        'writer_change_permitted':False,'dns_change_permitted':False,'security_accepted':False,'full_matrix_accepted':False}


def validate_control(value, now, *, recovery=False):
    required = set(unarmed_control())
    require(type(value) is dict and set(value) == required and value['schema'] == 'canonical_app_activation/1'
        and value['operation'] == OPERATION and same(value['image'],IMAGE),'control_scope')
    require(type(value['nonce']) is str and re.fullmatch('[a-f0-9]{32}',value['nonce'])
        and type(value['controller_commit']) is str and re.fullmatch('[a-f0-9]{40}',value['controller_commit']),'unarmed')
    require(type(value['source_files']) is dict and set(value['source_files']) == set(SOURCE_FILES),'source_inventory')
    for name,binding in value['source_files'].items():
        require(type(binding) is dict and set(binding) == {'bytes','sha256'} and type(binding['bytes']) is int
            and 0 < binding['bytes'] <= 131072 and hex64(binding['sha256']),'source_binding')
    start,forward,end = (value[k] for k in ('issued_at','forward_deadline','recovery_deadline'))
    require(all(type(v) is int for v in (start,forward,end)) and start <= now < (end if recovery else forward)
        and start < forward <= start+5400 and forward < end <= forward+1800,'authority_window')
    require(value['writer_change_permitted'] is False and value['dns_change_permitted'] is False
        and value['security_accepted'] is False and value['full_matrix_accepted'] is False,'authority_scope')
    delta = value['runtime_env_delta']
    require(same(delta,{}) or same(delta,{'PYTHONDONTWRITEBYTECODE':'1'}),'runtime_delta')
    require(type(value['sites']) is dict and set(value['sites']) == set(SITES),'site_scope')
    for site,scope in value['sites'].items():
        require(type(scope) is dict and set(scope) == {'machine_id','project','workdir','compose_files','env_files',
            'environment_file_label','compose_version','docker_version','fences','roles','reader_units','legacy_units','public_image_env_delta'}
            and scope['project'] == SITES[site]['project'] and scope['workdir'] == SITES[site]['workdir']
            and type(scope['machine_id']) is str and re.fullmatch('[a-f0-9]{32}',scope['machine_id']),'unarmed_site')
        files = scope['compose_files']; require(type(files) is list and 2 <= len(files) <= 16,'compose_prefix')
        require(len({r['path'] for r in files}) == len(files),'compose_prefix')
        for row in files:
            require(type(row) is dict and set(row) == {'path','bytes','sha256'} and type(row['bytes']) is int
                and 0 < row['bytes'] <= MAX_CONTROL and hex64(row['sha256']),'compose_prefix'); absolute(row['path'])
            require(row['path'].endswith(('.yml','.yaml')),'compose_prefix')
        base = scope['workdir']+('/docker-compose.yml' if site == 'bot' else '/docker-compose.iran.yml')
        require(files[0]['path'] == base,'compose_prefix')
        require(type(scope['env_files']) is list and 0 < len(scope['env_files']) <= 8
            and len({r['path'] for r in scope['env_files']}) == len(scope['env_files']),'env_references')
        for row in scope['env_files']:
            require(type(row) is dict and set(row) == {'path','target','identity','aliases','cli'} and type(row['cli']) is bool,'env_references')
            absolute(row['path']); absolute(row['target']); metadata(row['identity'])
            require(type(row['aliases']) is list and len(row['aliases']) <= 8,'env_references')
            for alias in row['aliases']:
                require(type(alias) is dict and set(alias) == {'path','identity'},'env_references'); absolute(alias['path'])
                ident = alias['identity']
                require(type(ident) is list and len(ident) == 9 and all(type(v) is int for v in ident)
                    and ident[0] > 0 and ident[1] > 0 and ident[2:6] == [0o120777,0,0,1]
                    and 0 < ident[6] <= 4096,'env_references')
        require(any(r['cli'] for r in scope['env_files']) or any(r['path'] == scope['workdir']+'/.env' for r in scope['env_files']),'default_env_reference')
        require(scope['environment_file_label'] is None or type(scope['environment_file_label']) is str,'env_references')
        require(type(scope['compose_version']) is str and re.fullmatch(r'2\.[0-9]+\.[0-9]+(?:[+a-zA-Z0-9.-]*)?',scope['compose_version'])
            and type(scope['docker_version']) is str and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',scope['docker_version']),'engine_version')
        require(type(scope['fences']) is dict and set(scope['fences']) == ({'marker','source'} if site == 'bot' else {'marker'}),'fence_binding')
        for name,row in scope['fences'].items(): metadata(row,empty=name == 'source')
        if site == 'bot':require(same(scope['fences']['source'],SOURCE_ID),'original_source_identity')
        require(type(scope['roles']) is dict and set(scope['roles']) == set(SITES[site]['roles']),'role_scope')
        for name,row in scope['roles'].items():
            require(type(row) is dict and set(row) == {'container_id','image_id','started','restarts','pid'}
                and hex64(row['container_id']) and image_id(row['image_id']) and type(row['started']) is str
                and type(row['restarts']) is int and row['restarts'] >= 0 and type(row['pid']) is int and row['pid'] > 0,'role_baseline')
        require(type(scope['reader_units']) is dict and set(scope['reader_units']) == (READER_UNITS if site == 'bot' else set()),'reader_units')
        require(type(scope['legacy_units']) is dict and scope['legacy_units'],'legacy_units')
        for name,policy in {**scope['reader_units'],**scope['legacy_units']}.items():
            require(type(name) is str and re.fullmatch(r'[a-zA-Z0-9_.@-]+\.(?:service|timer)',name)
                and type(policy) is dict and policy and all(type(k) is str and type(v) is str for k,v in policy.items()),'unit_binding')
            require(set(policy) <= UNIT_PUBLIC_FIELDS,'private_unit_property')
        for policy in scope['reader_units'].values():
            require(set(policy) <= READER_POLICY_FIELDS and {'LoadState','UnitFileState','NeedDaemonReload'} <= set(policy)
                and policy['LoadState'] == 'loaded' and policy['UnitFileState'] and policy['NeedDaemonReload'] == 'no','reader_unit_policy')
        for policy in scope['legacy_units'].values():
            require(policy.get('ActiveState') in {'inactive','failed'} and policy.get('MainPID') == '0'
                and policy.get('ControlPID') == '0' and policy.get('UnitFileState') in {'disabled','masked','not-found'}
                and policy.get('NeedDaemonReload') == 'no','legacy_owner_policy')
        change = scope['public_image_env_delta']
        require(type(change) is dict and set(change) <= PUBLIC_ENV_DEFAULT_KEYS,'image_default_delta')
        for key,pair in change.items():
            require(type(pair) is dict and set(pair) == {'old','new'} and all(type(v) is str and 0 < len(v) <= 256 for v in pair.values())
                and pair['old'] != pair['new'],'image_default_delta')
    gates = value['gates']
    require(type(gates) is dict and set(gates) == {'canonical_smoke','host_loads','backup_restore','schema_compatibility'},'gate_inventory')
    require(same(gates['canonical_smoke'],{'name':'canonical-smoke.zip','bytes':SMOKE['zip_bytes'],'sha256':SMOKE['zip_sha256']}),'smoke_binding')
    require(type(gates['host_loads']) is dict and set(gates['host_loads']) == set(SITES),'load_gate_missing')
    for binding in gates['host_loads'].values(): file_binding(binding)
    file_binding(gates['backup_restore']); file_binding(gates['schema_compatibility'])
    public(value); return value


def canonical_smoke(raw):
    """Admit the ACTUAL hosted32 artifact, not a caller's synthetic assertion."""
    require(type(raw) is bytes and len(raw) == SMOKE['zip_bytes'] and sha256(raw).hexdigest() == SMOKE['zip_sha256'],'smoke_artifact')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        rows = archive.infolist(); require(len(rows) == len(SMOKE_MEMBERS) and {r.filename for r in rows} == set(SMOKE_MEMBERS)
            and all(not r.is_dir() and not r.flag_bits & 1 and stat.S_IFMT(r.external_attr >> 16) in (0,stat.S_IFREG) for r in rows),'smoke_inventory')
        values = {}
        for row in rows:
            fact = SMOKE_MEMBERS[row.filename]; require(row.file_size == fact['bytes'] <= MAX_JSON,'smoke_member')
            body = archive.read(row); require(same(digest_record(body),fact),'smoke_member')
            if row.filename.endswith('.json'): values[row.filename] = document(body)
    result = values['reports/actual-smoke.json']
    require(result.get('schema') == 'app_image_ci_smoke/1' and result.get('status') == 'passed'
        and result.get('image_config_id') == IMAGE['image_id']
        and same(result.get('runtime'),{'expected':8,'run':8,'passed':8,'failures':[]})
        and same(result.get('components'),{'expected':24,'run':24,'passed':24,'failures':[],'errors':0,'skipped':0})
        and same(result.get('migration'),{'invocation_attempted':True,'child_completed':True,'return_code':0})
        and result.get('migration_head') == 'ff6c7d8e9f01' and result.get('runtime_cleanup_failures') == []
        and result.get('production_config_guards_active') is True and result.get('real_provider_used') is False
        and result.get('app_source_masked') is False and result.get('security_accepted') is False
        and result.get('full_matrix_accepted') is False,'smoke_result')
    archive = values['reports/canonical-archive-admission.json']; measured = IMAGE['archive_observation']
    require(archive.get('config_id') == IMAGE['image_id'] and same(archive.get('rootfs_layers'),IMAGE['rootfs']['Layers'])
        and all(same(archive.get(k),v) for k,v in measured.items()) and archive.get('physical_graph_closed') is True
        and archive.get('repo_tags_empty') is True and archive.get('configuration_values_disclosed') is False
        and archive.get('security_accepted') is False,'smoke_archive')
    daemon = values['reports/canonical-daemon-admission.json']
    require(daemon.get('daemon_image_id') == daemon.get('image_config_id') == IMAGE['image_id']
        and daemon.get('rootfs_layers') == 33 and daemon.get('whole_config_compared_in_ram') is True
        and daemon.get('configuration_values_disclosed') is False and daemon.get('security_accepted') is False,'smoke_daemon')
    cleanup = values['reports/cleanup.json']
    require(cleanup.get('owned_cleanup_complete') is True and cleanup.get('closed_causes') == []
        and cleanup.get('primary_failure_retained') is False,'smoke_cleanup')
    for name,schema in (('fresh-verify_image_component_presence','security_image_component_presence/1'),
                        ('fresh-verify_acl_consumer_presence','security_acl_consumer_presence/1')):
        row = values['reports/'+name+'.json']
        require(row.get('schema') == schema and row.get('image_id') == IMAGE['image_id']
            and row.get('source_commit') == IMAGE['source_commit'] and row.get('production_mounts_or_data_used') is False
            and row.get('scanner_findings_modified') is False and row.get('security_accepted') is False,'smoke_presence')
    return {'schema':'canonical_app_actual_ci_gate/1','run_id':SMOKE['run_id'],'artifact_id':SMOKE['artifact_id'],
        'zip_sha256':SMOKE['zip_sha256'],'image_config_id':IMAGE['image_id'],'actual_tests_passed':32,
        'physical_graph_closed':True,'whole_config_compared_in_ram':True,'raw_gate_passed':False,
        'acl_legacy_callers_remain':True,'security_accepted':False,'full_matrix_accepted':False}


def environment(rows):
    require(type(rows) is list,'environment_shape'); values = {}
    for row in rows:
        require(type(row) is str and '=' in row,'environment_shape'); key,value = row.split('=',1)
        require(re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',key) and key not in values,'environment_shape'); values[key] = value
    return values


def image_defaults(old, new, allowed):
    require(type(old) is dict and type(new) is dict and type(allowed) is dict,'image_defaults')
    before,after = environment(old.get('Env',[])),environment(new.get('Env',[]))
    require(set(before) == set(after),'image_defaults')
    delta = {k:{'old':before[k],'new':after[k]} for k in before if before[k] != after[k]}
    require(set(delta) <= PUBLIC_ENV_DEFAULT_KEYS and same(delta,allowed),'image_defaults')
    require(all(before.get(k) == after.get(k) for k in ('PATH','LANG','GPG_KEY')),'image_defaults')
    a,b = deepcopy(old),deepcopy(new); a['Env'] = b['Env'] = []
    la,lb = a.pop('Labels',None) or {},b.pop('Labels',None) or {}
    require(type(la) is dict and type(lb) is dict,'image_labels')
    require(same({k:v for k,v in la.items() if k not in PROVENANCE_LABELS},
                 {k:v for k,v in lb.items() if k not in PROVENANCE_LABELS}),'image_labels')
    expected = dict(zip(PROVENANCE_LABELS,(IMAGE['source_commit'],IMAGE['source_tree'],IMAGE['input_signature'])))
    require(all(lb.get(k) == v for k,v in expected.items()) and same(a,b),'image_defaults')


def rendered_delta(old,new,roles,image,delta):
    require(type(old) is dict and type(new) is dict and type(old.get('services')) is dict,'normal_render')
    expected = deepcopy(old)
    if type(image) is dict:require(set(image) == set(roles) and all(image_id(v) for v in image.values()),'normal_render_delta')
    for service in roles:
        require(service in expected['services'],'normal_render'); expected['services'][service]['image'] = image[service] if type(image) is dict else image
        if delta: expected['services'][service].setdefault('environment',{}).update(delta)
    require(same(expected,new),'normal_render_delta'); return new


def healthcheck(value):
    if value is None: return None
    require(type(value) is dict and set(value) <= {'test','interval','timeout','start_period','start_interval','retries','disable'},'healthcheck')
    if value.get('disable') is True:return {'Test':['NONE']}
    def duration(text):
        require(type(text) is str and re.fullmatch(r'(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|ms|s|m|h))+',text),'healthcheck')
        scales={'ns':1,'us':1000,'ms':1000000,'s':1000000000,'m':60000000000,'h':3600000000000}
        from decimal import Decimal
        number=sum(Decimal(n)*scales[u] for n,u in re.findall(r'([0-9]+(?:\.[0-9]+)?)(ns|us|ms|s|m|h)',text))
        require(number == int(number) and 0 <= number <= 86400*1000000000,'healthcheck'); return int(number)
    result = {'Test':value.get('test'),'Interval':duration(value.get('interval','0s')),
        'Timeout':duration(value.get('timeout','0s')),'StartPeriod':duration(value.get('start_period','0s')),
        'StartInterval':duration(value.get('start_interval','0s')),'Retries':value.get('retries',0)}
    require(type(result['Test']) is list and all(type(v) is str for v in result['Test'])
        and type(result['Retries']) is int and result['Retries'] >= 0,'healthcheck'); return result


def normalize_health(value):
    if value is None:return None
    require(type(value) is dict and set(value) <= {'Test','Interval','Timeout','StartPeriod','StartInterval','Retries'},'healthcheck')
    return {**{'Test':None,'Interval':0,'Timeout':0,'StartPeriod':0,'StartInterval':0,'Retries':0},**value}


def mounts_model(model, service):
    rows = model['services'][service].get('volumes',[]); require(type(rows) is list,'mount_model'); expected = {}
    for row in rows:
        require(type(row) is dict and set(row) <= {'type','source','target','read_only','bind','volume','consistency'}
            and row.get('type') in {'bind','volume'} and type(row.get('source')) is str and type(row.get('target')) is str,'mount_model')
        target = absolute(row['target']); require(target not in expected,'mount_model')
        require(target != '/' and not any(target == root or target.startswith(root+'/') or root.startswith(target+'/')
            for root in IMAGE_SOURCE_PATHS | {'/usr','/lib','/lib64','/bin','/sbin'}),'source_mount')
        if row['type'] == 'bind': source = absolute(row['source'])
        else:
            volume = model.get('volumes',{}).get(row['source']); require(type(volume) is dict and type(volume.get('name')) is str,'mount_model'); source = volume['name']
        options = row.get('bind') or {}; require(type(options) is dict and set(options) <= {'propagation','create_host_path','selinux','recursive'},'mount_model')
        expected[target] = {'type':row['type'],'source':source,'readonly':row.get('read_only',False),
            'propagation':options.get('propagation','rprivate') if row['type'] == 'bind' else None}
        require(type(expected[target]['readonly']) is bool,'mount_model')
    return expected


def check_mounts(model,service,actual):
    expected = mounts_model(model,service); require(type(actual) is list and len(actual) == len(expected),'mounts')
    seen = set()
    for row in actual:
        require(type(row) is dict and type(row.get('Destination')) is str and row['Destination'] not in seen,'mounts'); target = row['Destination']; seen.add(target)
        require(target in expected,'mounts'); item = expected[target]
        require(row.get('Type') == item['type'] and row.get('RW') is (not item['readonly']),'mounts')
        if item['type'] == 'bind':require(row.get('Source') == item['source'] and row.get('Propagation') == item['propagation'],'mounts')
        else:require(row.get('Name') == item['source'] and row.get('Driver') == 'local','mounts')
    require(seen == set(expected),'mounts')

CONFIG_FIELDS = frozenset(('Hostname','Domainname','User','AttachStdin','AttachStdout','AttachStderr','ExposedPorts','Tty',
    'OpenStdin','StdinOnce','Env','Cmd','Healthcheck','ArgsEscaped','Image','Volumes','WorkingDir','Entrypoint',
    'NetworkDisabled','MacAddress','OnBuild','Labels','StopSignal','StopTimeout','Shell'))
COMPOSE_FIELDS = frozenset(('project','service','version','container-number','oneoff','project.working_dir',
    'project.config_files','project.environment_file','image','config-hash','depends_on','replace'))
SERVICE_FIELDS = frozenset(('image','container_name','command','entrypoint','environment','env_file','labels','logging',
    'restart','volumes','ports','expose','ulimits','depends_on','extra_hosts','networks','healthcheck','user','working_dir',
    'hostname','stop_signal','read_only','privileged','pid','userns_mode','cap_add','cap_drop','security_opt','group_add',
    'init','cpus','mem_limit','memswap_limit','pids_limit','pull_policy'))


def no_dependencies_label(labels):
    """Creation metadata only; never a dependency-readiness assertion."""
    return type(labels) is dict and labels.get('com.docker.compose.depends_on') == ''


def normal_labels(labels,model,service,scope,files,image,*,replacement=False,creation_no_dependencies=False):
    require(type(labels) is dict and all(type(k) is str and type(v) is str for k,v in labels.items()),'labels')
    prefix = 'com.docker.compose.'
    require(all(k[len(prefix):] in COMPOSE_FIELDS for k in labels if k.startswith(prefix)),'compose_labels')
    expected = {'project':scope['project'],'service':service,'version':scope['compose_version'],'container-number':'1',
        'oneoff':'False','project.working_dir':scope['workdir'],'project.config_files':','.join(files),'image':image}
    for key,value in expected.items():require(labels.get(prefix+key) == value,'compose_labels')
    reference = scope['environment_file_label']
    require((prefix+'project.environment_file' not in labels) if reference is None
        else labels.get(prefix+'project.environment_file') == reference,'compose_labels')
    # The normal Compose operation generates this public label. Never run
    # config --hash or independently hash private model/Env to reproduce it.
    require(hex64(labels.get(prefix+'config-hash')),'compose_labels')
    dependencies = model['services'][service].get('depends_on',{})
    require(type(dependencies) is dict and type(creation_no_dependencies) is bool,'compose_labels')
    expected_dependencies = {name+':'+row.get('condition','service_started')+':'+str(row.get('restart',False)).lower()
        for name,row in dependencies.items()}
    actual = labels.get(prefix+'depends_on','').split(',') if labels.get(prefix+'depends_on') else []
    # Compose --no-deps removes the selected service's dependency references
    # before generating this creation label. Keep the complete normal model;
    # admit only an explicitly classified, present, exact-empty label here.
    # Source: docker/compose v5.5.0 cmd/compose/up.go and pkg/compose/create.go.
    if creation_no_dependencies:
        require(prefix+'depends_on' in labels and labels[prefix+'depends_on'] == '', 'compose_labels')
    else:
        require(len(actual) == len(set(actual)) and set(actual) == expected_dependencies,'compose_labels')
    if prefix+'replace' in labels:
        require(replacement and labels[prefix+'replace'] == model['services'][service]['container_name'],'compose_labels')


def _command(value):
    require(value is None or type(value) is list and all(type(v) is str for v in value),'normal_command'); return value


def normal_baseline(model,service,row,image,scope,files,*,replacement=False,creation_no_dependencies=False,runtime_restart_policy=None):
    """Account for the normal model, then keep the COMPLETE actual operands RAM-only."""
    require(type(model) is dict and type(model.get('services')) is dict and service in model['services'],'normal_render')
    spec = model['services'][service]; cfg = row['config']; host = row['host']; defaults = image['Config']
    require(type(spec) is dict and set(spec) <= SERVICE_FIELDS,'normal_feature_scope')
    require('pull_policy' not in spec or type(spec['pull_policy']) is str and spec['pull_policy'] == 'never','normal_pull_policy')
    require(type(cfg) is dict and set(cfg) <= CONFIG_FIELDS and {'Env','Cmd','Image','Labels','Hostname','User','WorkingDir'} <= set(cfg)
        and type(host) is dict and image_id(image['Id']) and row['image'] == image['Id']
        and cfg['Image'] == spec['image'] and row['name'] == '/'+spec['container_name'],'role_identity')
    env = environment(defaults.get('Env',[])); explicit = spec.get('environment',{})
    require(type(explicit) is dict and all(type(k) is str and type(v) is str for k,v in explicit.items()),'normal_environment')
    env.update(explicit); require(same(environment(cfg['Env']),env),'normal_environment')
    require(explicit.get('TRADING_BOT_SERVICE') == ('api' if service == 'app' else service),'role_policy')
    require(same(cfg.get('Cmd'),_command(spec.get('command',defaults.get('Cmd'))))
        and same(cfg.get('Entrypoint'),_command(spec.get('entrypoint',defaults.get('Entrypoint'))))
        and cfg['User'] == spec.get('user',defaults.get('User') or '')
        and cfg['WorkingDir'] == spec.get('working_dir',defaults.get('WorkingDir') or '')
        and cfg['Hostname'] == spec.get('hostname',row['id'][:12]),'normal_execution')
    expected_health = healthcheck(spec.get('healthcheck')) if 'healthcheck' in spec else defaults.get('Healthcheck')
    require(same(normalize_health(cfg.get('Healthcheck')),normalize_health(expected_health)),'normal_healthcheck')
    static_config = {'Domainname':'','AttachStdin':False,'AttachStdout':True,'AttachStderr':True,'Tty':False,
        'OpenStdin':False,'StdinOnce':False,'ArgsEscaped':False,'NetworkDisabled':False,'MacAddress':'','OnBuild':None,'Shell':None}
    for key,value in static_config.items(): require(same(cfg.get(key,value),value),'normal_execution')
    require(same(cfg.get('Volumes'),defaults.get('Volumes')) and cfg.get('StopSignal','') == spec.get('stop_signal',defaults.get('StopSignal','')),'normal_execution')
    timeout = defaults.get('StopTimeout')
    require(timeout is None or type(timeout) is int and timeout >= 0,'normal_execution')
    require(same(cfg.get('StopTimeout'),timeout),'normal_execution')
    labels = {**(defaults.get('Labels') or {}),**spec.get('labels',{})}
    require(same({k:v for k,v in cfg['Labels'].items() if not k.startswith('com.docker.compose.')},labels),'normal_labels')
    normal_labels(cfg['Labels'],model,service,scope,files,image['Id'],replacement=replacement,
        creation_no_dependencies=creation_no_dependencies)
    check_mounts(model,service,row['mounts'])
    require(host.get('Privileged') is spec.get('privileged',False) and host.get('ReadonlyRootfs') is spec.get('read_only',False)
        and host.get('AutoRemove') is False and (host.get('PidMode') or '') == spec.get('pid','')
        and (host.get('UsernsMode') or '') == spec.get('userns_mode',''),'normal_host')
    for name,key in (('CapAdd','cap_add'),('CapDrop','cap_drop'),('SecurityOpt','security_opt'),('GroupAdd','group_add')):
        require(same(host.get(name) or [],spec.get(key) or []),'normal_host')
    restart = spec.get('restart','no').split(':')
    expected_restart = {'Name':restart[0],'MaximumRetryCount':int(restart[1]) if len(restart) == 2 else 0}
    if runtime_restart_policy is not None:
        # Only the independently admitted normal always/0 policy may account
        # for a historical temporary no-restart creation override. Callers must
        # authenticate that policy separately; the full model stays untouched.
        require(type(runtime_restart_policy) is dict and set(runtime_restart_policy)=={'Name','MaximumRetryCount'}
            and type(runtime_restart_policy['Name']) is str and runtime_restart_policy['Name']=='always'
            and type(runtime_restart_policy['MaximumRetryCount']) is int and runtime_restart_policy['MaximumRetryCount']==0
            and expected_restart in ({'Name':'no','MaximumRetryCount':0},{'Name':'always','MaximumRetryCount':0}),'normal_restart')
        expected_restart = runtime_restart_policy
    require(same(host.get('RestartPolicy'),expected_restart),'normal_restart')
    logging = spec.get('logging',{})
    require(same(host.get('LogConfig'),{'Type':logging.get('driver','json-file'),'Config':logging.get('options',{})}),'normal_logging')
    ports = {}
    for port in spec.get('ports',[]):
        require(type(port) is dict and type(port.get('target')) is int and port.get('protocol','tcp') in {'tcp','udp'},'normal_ports')
        key = str(port['target'])+'/'+port.get('protocol','tcp')
        ports.setdefault(key,[]).append({'HostIp':port.get('host_ip',''),'HostPort':str(port.get('published',''))})
    require(same(host.get('PortBindings') or {},ports),'normal_ports')
    exposed = {**(defaults.get('ExposedPorts') or {}),**{key:{} for key in ports}}
    for name in spec.get('expose',[]):
        require(type(name) is str and re.fullmatch('[0-9]+(?:/(?:tcp|udp))?',name),'normal_ports')
        exposed[name if '/' in name else name+'/tcp'] = {}
    require(same(cfg.get('ExposedPorts') or {},exposed),'normal_ports')
    normal_host(spec,host)
    networks = spec.get('networks',{})
    require(type(networks) is dict and networks and not spec.get('network_mode'),'normal_network')
    names = {model['networks'][name]['name'] for name in networks}
    require(type(row['networks']) is dict and set(row['networks']) == names and host.get('NetworkMode') in names,'normal_network')
    return row


def normal_host(spec,host):
    """The five normal services' policy; unsupported nonzero API features refuse."""
    from decimal import Decimal
    expected = {'Init':spec.get('init'),'IpcMode':'private','CgroupnsMode':'private','Runtime':'runc','ShmSize':67108864,
        'ConsoleSize':[0,0],'Memory':spec.get('mem_limit',0),'MemorySwap':spec.get('memswap_limit',2*spec.get('mem_limit',0)),
        'PidsLimit':spec.get('pids_limit'),'NanoCpus':int(Decimal(str(spec.get('cpus',0)))*1000000000)}
    for name in ('Memory','MemorySwap','NanoCpus'):
        require(type(expected[name]) is int and expected[name] >= 0,'normal_resource')
    require(expected['Init'] is None or type(expected['Init']) is bool,'normal_resource')
    require(expected['PidsLimit'] is None or type(expected['PidsLimit']) is int,'normal_resource')
    for key,value in expected.items():require(same(host.get(key),value),'normal_resource')
    def host_pair(name,address):
        require(type(name) is str and type(address) is str and name and address
            and not any(c in name for c in ':=\r\n') and not any(c in address for c in '=\r\n'),'normal_hosts')
        return name+':'+address
    extras = spec.get('extra_hosts',{}); require(type(extras) in (dict,list),'normal_hosts'); expected_hosts = []
    if type(extras) is dict:
        for name,addresses in extras.items():
            addresses = [addresses] if type(addresses) is str else addresses
            require(type(name) is str and name and type(addresses) is list and addresses
                and all(type(a) is str and a for a in addresses),'normal_hosts')
            expected_hosts.extend(host_pair(name,address) for address in addresses)
    else:
        # Current Compose emits its documented short form (host=address);
        # earlier normal renders used a mapping. Docker retains host:address.
        # Only serialization changes here; preserve exact names/addresses.
        for item in extras:
            require(type(item) is str and ('=' in item or ':' in item),'normal_hosts')
            name,address = item.split('=' if '=' in item else ':',1)
            expected_hosts.append(host_pair(name,address))
    require(len(expected_hosts)==len(set(expected_hosts)),'normal_hosts')
    actual_hosts = host.get('ExtraHosts') or []
    require(type(actual_hosts) is list and len(set(actual_hosts)) == len(actual_hosts)
        and sorted(actual_hosts) == sorted(expected_hosts),'normal_hosts')
    limits = spec.get('ulimits',{}); require(type(limits) is dict,'normal_ulimits'); expected_limits = []
    for name,row in limits.items():
        require(type(row) is dict and set(row) == {'soft','hard'} and all(type(v) is int for v in row.values()),'normal_ulimits')
        expected_limits.append({'Name':name,'Soft':row['soft'],'Hard':row['hard']})
    require(same(sorted(host.get('Ulimits') or [],key=lambda r:r['Name']),sorted(expected_limits,key=lambda r:r['Name'])),'normal_ulimits')
    accounted = set(expected)|{'ExtraHosts','Ulimits','Privileged','ReadonlyRootfs','AutoRemove','PidMode','UsernsMode',
        'CapAdd','CapDrop','SecurityOpt','GroupAdd','RestartPolicy','LogConfig','PortBindings','NetworkMode','Binds','Mounts',
        'MaskedPaths','ReadonlyPaths'}
    for key,value in host.items():
        if key in accounted:continue
        require(value is None or type(value) is bool and value is False or type(value) is int and value == 0
            or type(value) is str and value == '' or type(value) in (list,dict) and not value,'normal_host_feature')
    masked = host.get('MaskedPaths'); readonly = host.get('ReadonlyPaths')
    require(type(masked) is list and type(readonly) is list and masked and readonly
        and all(type(p) is str and p.startswith('/') for p in [*masked,*readonly]),'normal_proc_policy')


def configuration_parity(old_model,new_model,service,before,after,old_image,new_image,scope,old_files,new_files,delta,*,stopped=False):
    """No Config/Env/Host hashing or persistence; every unlisted field is exact."""
    normal_baseline(old_model,service,before,old_image,scope,old_files,replacement=True,
        creation_no_dependencies=no_dependencies_label(before['config'].get('Labels')))
    normal_baseline(new_model,service,after,new_image,scope,new_files,replacement=True,
        creation_no_dependencies=no_dependencies_label(after['config'].get('Labels')))
    image_defaults(old_image['Config'],new_image['Config'],scope['public_image_env_delta'])
    a,b = deepcopy(before['config']),deepcopy(after['config'])
    require(set(a) == set(b),'complete_config_fields')
    a['Image'] = b['Image']; a['Hostname'] = b['Hostname']
    # Full unique-key Env is checked against each complete normal model above.
    # Business values remain identical; only measured image defaults and the
    # one explicit existing bytecode policy may account for a difference.
    ea,eb = environment(a.pop('Env')),environment(b.pop('Env'))
    expected_env = dict(ea)
    for key,pair in scope['public_image_env_delta'].items():
        if key not in old_model['services'][service].get('environment',{}):
            require(ea.get(key) == pair['old'],'image_defaults'); expected_env[key] = pair['new']
    expected_env.update(delta); require(same(expected_env,eb),'business_environment')
    la,lb = a.pop('Labels'),b.pop('Labels')
    expected_labels = {k:v for k,v in la.items() if not k.startswith('com.docker.compose.')}
    defaults = new_image['Config'].get('Labels') or {}
    for key in PROVENANCE_LABELS: expected_labels[key] = defaults[key]
    require(same(expected_labels,{k:v for k,v in lb.items() if not k.startswith('com.docker.compose.')}),'business_labels')
    require(same(a,b) and same(unordered_host(before['host']),unordered_host(after['host'])),'complete_configuration')
    require(same(sorted(before['mounts'],key=lambda r:r['Destination']),sorted(after['mounts'],key=lambda r:r['Destination'])),'complete_mounts')
    network_parity(old_model,service,before,after,stopped=stopped)
    return True


def unordered_host(value):
    """Compose iterates mount maps; retain every value, reject duplicate targets."""
    result = deepcopy(value)
    if 'OomKillDisable' in result:
        require(result['OomKillDisable'] is None or type(result['OomKillDisable']) is bool,'normal_host')
        if result['OomKillDisable'] is None:result['OomKillDisable'] = False
    if result.get('Binds') is not None:
        require(type(result['Binds']) is list and all(type(v) is str for v in result['Binds'])
            and len(set(result['Binds'])) == len(result['Binds']),'mounts'); result['Binds'].sort()
    if result.get('Mounts') is not None:
        rows = result['Mounts']; require(type(rows) is list and all(type(r) is dict and type(r.get('Target')) is str for r in rows)
            and len({r['Target'] for r in rows}) == len(rows),'mounts'); rows.sort(key=lambda r:r['Target'])
    return result


def network_parity(model,service,before,after,*,stopped):
    import ipaddress
    expected = deepcopy(before['networks']); current = after['networks']
    require(type(expected) is dict and type(current) is dict and set(expected) == set(current),'network_policy')
    by_name = {model['networks'][key]['name']:(options or {}) for key,options in model['services'][service]['networks'].items()}
    require(set(by_name) == set(expected),'network_policy')
    generated = {'EndpointID','MacAddress','NetworkID','Gateway','IPAddress','IPPrefixLen','IPv6Gateway',
        'GlobalIPv6Address','GlobalIPv6PrefixLen','DNSNames'}
    for name,old in expected.items():
        new = current[name]; require(type(new) is dict and set(old) == set(new),'network_policy')
        if stopped:
            require(after.get('running') is False and after.get('pid') == 0,'stopped_candidate')
            for key in generated & set(new):
                zero = 0 if key in {'IPPrefixLen','GlobalIPv6PrefixLen'} else None if key == 'DNSNames' else ''
                require(same(new[key],zero) or key == 'DNSNames' and same(new[key],[]),'stopped_network')
                old[key] = new[key]
        else:
            require(hex64(new.get('EndpointID')) and type(new.get('MacAddress')) is str
                and re.fullmatch('(?:[a-f0-9]{2}:){5}[a-f0-9]{2}',new['MacAddress'])
                and int(new['MacAddress'][:2],16)&1 == 0,'network_policy')
            for field,configured,family in (('IPAddress','ipv4_address',4),('GlobalIPv6Address','ipv6_address',6)):
                value = new.get(field,''); fixed = by_name[name].get(configured)
                if fixed is not None:require(value == fixed,'network_policy')
                elif value:
                    try: parsed = ipaddress.ip_address(value)
                    except ValueError:raise RuntimeError('network_policy') from None
                    prefix_key = 'IPPrefixLen' if family == 4 else 'GlobalIPv6PrefixLen'
                    prefix = old.get(prefix_key); original = old.get(field)
                    require(parsed.version == family and type(original) is str and original
                        and type(prefix) is int and 0 < prefix <= (32 if family == 4 else 128),'network_policy')
                    try: subnet = ipaddress.ip_network(original+'/'+str(prefix),strict=False)
                    except ValueError:raise RuntimeError('network_policy') from None
                    require(parsed in subnet,'network_policy')
                else:require(old.get(field,'') == '','network_policy')
                if field in old:old[field] = value
            for field in ('EndpointID','MacAddress'):old[field] = new[field]
            if 'DNSNames' in old:
                require(type(old['DNSNames']) is list and type(new['DNSNames']) is list,'network_policy')
                old['DNSNames'] = [after['id'][:12] if item == before['id'][:12] else item for item in old['DNSNames']]
        require(same(old,new),'network_policy')


def other_containers(before,after,old_id,new_id=None):
    require(type(before) is dict and type(after) is dict and old_id in before,'other_inventory')
    expected = set(before)-{old_id}; actual = set(after)-({new_id} if new_id else {old_id})
    require(expected == actual and all(same(before[key],after[key]) for key in expected),'other_containers')
    return True


LOAD_CONTROLLER = 'd7ca623be49facc51a58a58dd38bfa06760287a46b2b30e9db0b73d53e7f19e6'
LOAD_CATALOG = 'bebdd67daf4ef7950b2c4278cd338ec0d11130ea84df0a7fd8c41d4a9ddc13d0'
LOAD_ENGINE = '1041cc2d04100e2f6aebb95dca30c2339de41342b350ef44522c05277e9c146e'
LOAD_HEALTH_POLICY = '2e520dcb1ac3ab9b0da96959caad3035170d792284bf3df531f128f43a3f9c86'
LOAD_HEALTH_ADAPTER = '5017b7546a3f117e74fdd6314582e19e39395ae1ac183149be924ba720d68aac'
LOAD_V3_CONTROLLER = 'fdbdb4901f05844e4a5ec7b3b11413e0be139f3b4708ecba14cdc1f92a110eab'
LOAD_RESOURCE_CONTRACT = 'b99ecf8c8d6618cde38580eba197c509da8ae900cf99d6f81ef7b11b47fadaeb'
LOAD_RESOURCE_SOURCE = '1e27f45bf3e629a774a84e99979ceae2bd30b89db8f98144bdf02772459a68e0'


def load_observation_history(value,site):
    """Closed image-store history only; this grants no serving health exception."""
    fields = {'schema','operation','operation_sha256','health_policy_sha256','nonce','site',
        'container_id','image_id','pid','started','restarts','baseline_health','observed_health','observed_at',
        'runtime_health_issues_open','security_accepted'}
    for name,record_site in (('runtime_health_observations',site),('peer_runtime_health_observations','web')):
        rows = value.get(name,[]) if name.startswith('peer_') else value.get(name)
        require(type(rows) is list and len(rows) <= 128 and (site == 'bot' or not name.startswith('peer_') or not rows),'host_load_gate')
        ids = []
        for row in rows:
            require(type(row) is dict and set(row) == fields,'host_load_gate')
            require(all(same(row.get(k),v) for k,v in {
                'schema':'runtime_image_store_health_observation/1','operation':value['operation'],
                'operation_sha256':value['operation_sha256'],'health_policy_sha256':LOAD_HEALTH_POLICY,
                'nonce':value['nonce'],'site':record_site,'runtime_health_issues_open':True,'security_accepted':False}.items()),'host_load_gate')
            require(hex64(row['container_id']) and row['container_id'] != '6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752'
                and image_id(row['image_id']) and type(row['pid']) is int and row['pid'] > 0
                and type(row['restarts']) is int and row['restarts'] >= 0
                and type(row['observed_at']) is int and row['observed_at'] >= 0
                and type(row['baseline_health']) is str and type(row['observed_health']) is str
                and {row['baseline_health'],row['observed_health']} == {'healthy','unhealthy'}
                and type(row['started']) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',row['started']), 'host_load_gate')
            try: valid_start = datetime.fromisoformat(row['started'].replace('Z','+00:00')).tzinfo == timezone.utc
            except ValueError: valid_start = False
            require(valid_start,'host_load_gate'); ids.append(row['container_id'])
        require(ids == sorted(set(ids)),'host_load_gate')


def host_load(value,site,scope):
    """Exact historical /2 or resource-bound /3 App LOAD; no activation authority."""
    versions = {('canonical_app_preloaded_image/2',LOAD_CONTROLLER),
        ('canonical_app_preloaded_image/3',LOAD_V3_CONTROLLER)}
    require(type(value) is dict and type(site) is str and site in SITES
        and type(value.get('schema')) is str and type(value.get('controller_sha256')) is str
        and (value['schema'],value['controller_sha256']) in versions,'host_load_gate')
    if value['schema'] == 'canonical_app_preloaded_image/3':
        resource = {'resource_contract_sha256':LOAD_RESOURCE_CONTRACT}
        web = {'resource_ram_floor_bytes':3021996032,'aggregate_client_memory_max_bytes':536870912,
            'metadata_stream_allowance_bytes':268435456,'actual_tmpfs_payload_write_bytes':0,
            'daemon_heap_quota_claimed':False,'resource_contract_accepted':False}
        if site == 'bot':
            resource['native_resource_policy_unchanged'] = True
            require(not set(web).intersection(value),'host_load_gate')
        else:
            resource.update(web)
            require('native_resource_policy_unchanged' not in value,'host_load_gate')
        require(all(same(value.get(k),v) for k,v in resource.items()),'host_load_gate')
    fixed = {'schema':value['schema'],
        'operation':'security-canonical-app-candidate-images-20261025-01',
        'site':'native' if site == 'bot' else 'web','host_machine_id':scope['machine_id'],
        'host':'65.109.216.187' if site == 'bot' else '65.109.220.59',
        'catalog_sha256':LOAD_CATALOG,'controller_sha256':value['controller_sha256'],'engine_sha256':LOAD_ENGINE,
        'health_policy_sha256':LOAD_HEALTH_POLICY,'health_adapter_sha256':LOAD_HEALTH_ADAPTER,
        'ci_run_id':IMAGE['run_id'],'image_artifact_sha256':IMAGE['image_zip_sha256'],
        'actual_ci_run_id':SMOKE['run_id'],'actual_ci_artifact_sha256':SMOKE['zip_sha256'],
        'role':'app','profile':'canonical_app','candidate_config_id':IMAGE['image_id'],
        'rootfs':IMAGE['rootfs']['Layers'],'source_archive_bytes':IMAGE['archive_observation']['archive_bytes'],
        'source_archive_sha256':IMAGE['archive_observation']['archive_sha256'],
        'archive_and_config_association_verified':True,'whole_typed_config_compared_in_ram':True,
        'physical_graph_closed':True,'runtime_policy_preserved':True,'serving_app_roles_unchanged':True,
        'serving_roles_activated':[],'serving_validation':'NOT_TESTED','configuration_values_disclosed':False,
        'runtime_health_issues_open':True,'capture_issue_open':True,'security_accepted':False}
    require(type(value) is dict and all(same(value.get(k),v) for k,v in fixed.items()),'host_load_gate')
    identities = {IMAGE['image_id']:{'kind':'Docker_config_identity'},
        IMAGE['archive_observation']['manifest_id']:{'kind':'OCI_manifest_to_config',
            'media_type':'application/vnd.oci.image.manifest.v1+json','size':5254},
        'sha256:'+IMAGE['archive_observation']['index_sha256']:{'kind':'OCI_index_to_manifest_to_config',
            'media_type':'application/vnd.oci.image.index.v1+json','size':241}}
    require(same(value.get('source_identities'),identities)
        and value.get('candidate_local_image_id') in identities
        and hex64(value.get('source_admission_sha256')) and hex64(value.get('operation_sha256'))
        and type(value.get('nonce')) is str and re.fullmatch('[a-f0-9]{32}',value['nonce']),'host_load_gate')
    load_observation_history(value,site)
    public(value); return value['candidate_local_image_id']


def fresh_gate(value,schema,control,now,maximum_age):
    require(type(value) is dict and value.get('schema') == schema
        and value.get('source_commit') == IMAGE['source_commit'] and value.get('image_config_id') == IMAGE['image_id']
        and same(value.get('machine_ids'),{s:r['machine_id'] for s,r in control['sites'].items()})
        and type(value.get('completed_at')) is int and 0 <= now-value['completed_at'] <= maximum_age
        and value.get('security_accepted') is False,'fresh_gate')
    public(value); return value


def evidence(control,read,now):
    """read(binding) must retain the authenticated public file FD through closure."""
    gate = control['gates']; canonical_smoke(read(gate['canonical_smoke']))
    local = {s:host_load(document(read(gate['host_loads'][s])),s,control['sites'][s]) for s in SITES}
    backup = fresh_gate(document(read(gate['backup_restore'])),'canonical_app_backup_restore/1',control,now,14400)
    require(same(backup.get('sites'),{s:{'database_backup_complete':True,'uploads_backup_complete':True,
        'isolated_database_restore_passed':True,'isolated_uploads_restore_passed':True,
        'durable_receipt_verified':True} for s in SITES}),'backup_restore_gate')
    compatibility = fresh_gate(document(read(gate['schema_compatibility'])),
        'canonical_app_schema_compatibility/1',control,now,86400)
    require(compatibility.get('init_db_create_all_acknowledged') is True
        and compatibility.get('mandatory_channel_rollout_acknowledged') is True
        and compatibility.get('candidate_on_restored_database_passed') is True
        and compatibility.get('old_image_on_same_restored_database_passed') is True
        and compatibility.get('rollback_data_compatible') is True
        and compatibility.get('implicit_migration_permitted') is False
        and type(compatibility.get('database_heads')) is dict and set(compatibility['database_heads']) == set(SITES)
        and all(type(v) is str and re.fullmatch('[a-f0-9]{12,40}',v) for v in compatibility['database_heads'].values()),'schema_gate')
    return local
