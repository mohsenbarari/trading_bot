"""Pure scope/budget proposal for a new Web engine transaction; no apply path."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import PurePosixPath
import re

from deploy.maintenance.web_engine_current_profile import validate_current_profile

GIB = 1024**3
RESERVE = 8*GIB
CONTROL_ALLOWANCE = 1024**2
CIPHER_OVERHEAD = 4096
MAX_ARCHIVE = 10*GIB-CIPHER_OVERHEAD
MAX_RAW_SCOPE = 24*GIB
COLD_INPUTS = ('/var/lib/docker','/var/lib/containerd',
    '/srv/trading-bot/market-data-staging-shadow/sessions','/srv/trading-bot/secure/market-data')
OPTIONAL_INPUTS = ('/etc/docker','/etc/containerd','/etc/default/docker','/etc/default/containerd')
OPERATION = 'security-web-engine-20261024-08'


class ColdPlanError(RuntimeError):
    """Static, content-free planning refusal."""


def require(ok, code):
    if not ok: raise ColdPlanError(code)


def integer(value, *, positive=False):
    return type(value) is int and value >= (1 if positive else 0)


def target_identity(value):
    require(type(value) is dict and set(value) == {'schema','host','port','user','root','directory_binding','mount','host_key_fingerprint'},
        'cold_target_shape')
    require(value['schema'] == 'web_engine_cold_destination/1' and value['host'] == '65.109.214.203'
        and type(value['port']) is int and value['port'] == 22 and value['user'] == 'root'
        and value['host_key_fingerprint'] == 'SHA256:bwxz2aeBwy0ZNOMMCVdRhaW//TkeALqt6etTQa3NINs',
        'cold_target_identity')
    root = value['root']
    require(type(root) is str and root == '/srv/trading-bot/security-backups' and str(PurePosixPath(root)) == root
        and '..' not in PurePosixPath(root).parts and not any(c.isspace() for c in root), 'cold_target_path')
    directory = value['directory_binding']
    require(type(directory) is dict and set(directory) == {'device','inode','uid','gid','mode'}
        and all(integer(directory[k],positive=True) for k in ('device','inode'))
        and type(directory['uid']) is type(directory['gid']) is type(directory['mode']) is int
        and directory['uid'] == directory['gid'] == 0 and directory['mode'] == 0o40700, 'cold_target_directory')
    mount = value['mount']
    require(type(mount) is dict and set(mount) == {'target','source','fstype','uuid'}
        and mount['target'] == '/' and mount['fstype'] == 'ext4'
        and type(mount['source']) is str and mount['source'].startswith('/dev/')
        and type(mount['uuid']) is str and re.fullmatch('[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',mount['uuid']), 'cold_target_mount')
    require(mount == {'target':'/','source':'/dev/sda1','fstype':'ext4','uuid':'ce366399-3430-441e-8a6f-6f5621f0c7a2'}
        and directory['device'] == 2049, 'cold_target_reviewed_mount')
    return deepcopy(value)


def destination_control(raw, digest):
    require(type(raw) is bytes and 0 < len(raw) <= 65536 and type(digest) is str
        and re.fullmatch('[a-f0-9]{64}',digest) and sha256(raw).hexdigest() == digest, 'cold_target_control_digest')
    def unique(pairs):
        result = {}
        for key,value in pairs:
            require(key not in result, 'cold_target_duplicate_key'); result[key] = value
        return result
    try: value = json.loads(raw,object_pairs_hook=unique)
    except (ValueError,UnicodeError,RecursionError): raise ColdPlanError('cold_target_control_json') from None
    return target_identity(value)


def capacity_budget(web_free_bytes, offhost_free_bytes):
    require(integer(web_free_bytes) and integer(offhost_free_bytes), 'cold_capacity_type')
    # The compressed plaintext and ciphertext coexist locally until verified
    # offhost durability. Neither sample reserves capacity against other writes.
    web_cap = (web_free_bytes-RESERVE-CONTROL_ALLOWANCE-CIPHER_OVERHEAD)//2
    remote_cap = offhost_free_bytes-RESERVE-CONTROL_ALLOWANCE-CIPHER_OVERHEAD
    archive_cap = min(MAX_ARCHIVE,web_cap,remote_cap)
    require(archive_cap > 0, 'cold_capacity_insufficient')
    return {'archive_max_bytes':archive_cap,'ciphertext_max_bytes':archive_cap+CIPHER_OVERHEAD,
        'web_required_bytes':2*archive_cap+CIPHER_OVERHEAD+RESERVE+CONTROL_ALLOWANCE,
        'offhost_required_bytes':archive_cap+CIPHER_OVERHEAD+RESERVE+CONTROL_ALLOWANCE,
        'reserve_bytes':RESERVE,'control_allowance_bytes':CONTROL_ALLOWANCE,'capacity_reserved':False,
        'compression_ratio_assumed':False,'actual_cold_cipher_fit_verified':False}


def fresh_operand(value, profile):
    require(type(value) is dict and set(value) == {'schema','operation_id','nonce','backup_mode','release_sha','release_tree',
        'image_id','image_input_signature','path','size_bytes','sha256','receipt_sha256','journal_sha256','restore_status','cleanup_status'},
        'cold_fresh_operand_shape')
    require(value['schema'] == 'web_engine_fresh_quiesced_operand/1' and value['operation_id'] == OPERATION
        and value['backup_mode'] == 'quiesced' and value['restore_status'] == value['cleanup_status'] == 'PASS'
        and value['release_sha'] == profile['configuration_source']['release_sha']
        and value['release_tree'] == profile['configuration_source']['release_tree']
        and value['image_id'] == profile['source_image']['image_id']
        and value['image_input_signature'] == profile['source_image']['image_input_signature'], 'cold_fresh_operand_identity')
    nonce = value['nonce']
    require(type(nonce) is str and re.fullmatch('[a-f0-9]{32}',nonce), 'cold_fresh_operand_nonce')
    path = value['path'];parent = PurePosixPath('/srv/trading-bot')/(OPERATION+'-'+nonce)/'market'
    require(type(path) is str and str(PurePosixPath(path)) == path and PurePosixPath(path).parent == parent
        and re.fullmatch('market-archive-before-'+value['release_sha'][:12]+r'-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}\.dump',PurePosixPath(path).name),
        'cold_fresh_operand_path')
    require(integer(value['size_bytes'],positive=True) and all(type(value[k]) is str and re.fullmatch('[a-f0-9]{64}',value[k])
        for k in ('sha256','receipt_sha256','journal_sha256')), 'cold_fresh_operand_artifact')
    return deepcopy(value)


def build_cold_archive_plan(profile, control_buffers, *, input_sizes, web_free_bytes,
        offhost_free_bytes, destination_receipt, destination_receipt_sha256, fresh_quiesced_backup=None):
    """Consume approved profile plus caller-reviewed public sizing/target facts.

    No fresh host facts, key access, authority window, cold archive or transport
    proof is manufactured here. A later finite controller must bind/recheck all.
    """
    validated = validate_current_profile(profile,control_buffers)
    fresh = None if fresh_quiesced_backup is None else fresh_operand(fresh_quiesced_backup,validated)
    required = set(COLD_INPUTS)|({fresh['path']} if fresh is not None else set())
    require(type(input_sizes) is dict and required <= set(input_sizes)
        and set(input_sizes) <= required|set(OPTIONAL_INPUTS)
        and all(integer(value) for value in input_sizes.values()), 'cold_input_scope')
    if fresh is not None:
        require(input_sizes[fresh['path']] >= fresh['size_bytes'], 'cold_fresh_operand_allocation')
    raw_bytes = sum(input_sizes.values())
    require(0 < raw_bytes <= MAX_RAW_SCOPE, 'cold_raw_scope_bound')
    identity = destination_control(destination_receipt,destination_receipt_sha256)
    budget = capacity_budget(web_free_bytes,offhost_free_bytes)
    public_facts = {'input_sizes':dict(sorted(input_sizes.items())),'web_free_bytes':web_free_bytes,
        'offhost_free_bytes':offhost_free_bytes,'offhost_identity':identity}
    facts_hash = sha256(json.dumps(public_facts,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'schema':'web_engine_cold_archive_plan/1','mode':'source_plan_only',
        'status':'requires_fresh_window_transport_and_actual_cold_proof',
        'profile_control_sha256':deepcopy(validated['source_control_sha256']),
        'configuration_source':deepcopy(validated['configuration_source']),'source_image':deepcopy(validated['source_image']),
        'cold_archive_inputs':sorted(input_sizes),'input_allocated_bytes':dict(sorted(input_sizes.items())),
        'raw_scope_bytes':raw_bytes,'raw_scope_limit_bytes':MAX_RAW_SCOPE,
        'tar_metadata_policy':{'one_file_system':True,'acls':True,'xattrs':True,'numeric_owner':True,'sparse':True},
        'hot_backup_data':deepcopy(validated['hot_backup_data']),'hot_dump_embedded_in_cold_archive':False,
        'fresh_quiesced_dump_required':True,'fresh_quiesced_dump_operand':fresh,
        'fresh_quiesced_dump_included':fresh is not None,
        'database_restore_and_cold_store_are_separate_required_proofs':True,'external_market_data_preserved_in_place':True,
        'offhost_identity':identity,'destination_receipt_sha256':destination_receipt_sha256,
        'capacity':budget,'caller_public_facts_sha256':facts_hash,
        'facts_origin':'caller_reviewed_metadata_not_live_rechecked_by_this_helper',
        'required_before_package_writes':['fresh_window_and_restore_reserve','fresh_transport_and_host_mount_identity',
            'fresh_current_runtime_and_storage_checks','explicit_database_backup_age_and_source_schema_admission',
            'owned_quiescence','fresh_quiesced_create_restore_and_verify','fresh_free_space_sample_after_quiesced_dump',
            'cold_archive_create_and_compare',
            'normal_crypto_plaintext_reconciliation','actual_cipher_offhost_fsync_and_independent_reread'],
        'data_before_runtime_recovery':True,'boot_guards_required_before_reboot':True,
        'fresh_measurements_required_at_execution':True,'current_runtime_rechecked':False,
        'cold_engine_archive_exists':False,'maintenance_authorized':False,'security_accepted':False,
        'deployment_eligible':False,'reboot_authorized':False}
