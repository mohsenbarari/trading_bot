#!/usr/bin/env python3
"""Finite per-site worker; called only by the native fenced Ansible controller.

Resolved Compose/Docker Env is compared inside anonymous memory only. This
worker never prints, hashes, persists, transfers or reports configuration data.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import json
import io
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import tempfile
import time
from types import ModuleType
import zipfile

HERE = Path(__file__).resolve().parent
OPERATION = 'security-consumers-20261024-02'
CONFIRMATION = 'SECURITY-APP-8DA-ONLY-20261024-02'
HELPER_SHA = 'fa2bf85cd50378bb999a7d8aabbd965a174722605c943c59ce6da56f68a74901'



PUBLIC_FAILURE_CODES = frozenset(('acceptance_buffer_invalid', 'acceptance_bytes_changed', 'accepted_evidence_drift', 'active_compose_provenance_changed', 'application_environment_drift', 'automation_not_durably_paused', 'baseline_not_owned', 'candidate_image_provenance_mismatch', 'canonical_successor_checkout_required', 'captured_resource_override_requires_separate_review', 'captured_runtime_override_requires_separate_review', 'command_not_explicit_argv', 'consumed_public_source_not_committed', 'consumer_bind_propagation_drift', 'consumer_command_drift', 'consumer_command_failed', 'consumer_execution_identity_drift', 'consumer_host_policy_drift', 'consumer_logging_drift', 'consumer_mount_drift', 'consumer_network_drift', 'consumer_not_baseline_running', 'consumer_outside_exact_whitelist', 'consumer_port_drift', 'consumer_preflight_failed', 'consumer_privilege_drift', 'consumer_restart_policy_drift', 'consumer_snapshot_failed', 'consumer_ulimit_drift', 'container_name_drift', 'container_refused', 'control_binding_scope', 'control_file_bound', 'control_file_changed', 'coordination_inode_changed', 'current_control_bytes_or_identity_changed', 'duplicate_environment_key', 'existing_private_primary_operation', 'explicit_scoped_apply_required', 'fence_scope', 'five_target_finalcheck_failed', 'global_acceptance_not_minted', 'image_application_defaults_drift', 'image_default_command_drift', 'image_default_identity_drift', 'image_not_immutable_digest', 'image_not_preloaded', 'invalid_consumer_identity', 'invalid_environment_shape', 'market_behavior_not_frozen', 'namespace_mismatch', 'native_coordinator_fences_not_held', 'native_coordinator_reservation_missing', 'native_coordinator_source_missing', 'native_kernel_not_remediated', 'native_root_required', 'override_not_absolute', 'owned_fence_changed', 'owned_recovery_postcheck_failed', 'owned_rollback_failed_fences_retained', 'owned_rollback_outcome_unknown', 'pending_queue_phase', 'preserved_user_document_changed', 'private_configuration_not_persistable', 'private_configuration_not_stageable', 'private_metadata_input_bound', 'private_metadata_output_bound', 'public_asset_staging_failed', 'public_control_source_changed', 'public_source_changed', 'published_main_checkout_required', 'published_main_drift', 'queue_phase_inventory_bound', 'real_public_origin_required', 'redirected_control_path', 'release_control_source_changed', 'resource_override_requires_separate_review', 'runtime_config_changed', 'runtime_config_changed_during_preflight', 'security_payload_binding_missing', 'security_payload_identity_invalid', 'service_identity_mismatch', 'site_directory_not_scoped_workspace', 'site_lock_changed', 'site_worker_source_changed', 'snapshot_not_readonly', 'staged_control_source_changed', 'static_endpoint_requires_separate_review', 'successor_acceptance_forbidden', 'successor_api_not_ready', 'successor_artifact_admission_changed', 'successor_artifact_admission_required', 'successor_authority_required', 'successor_candidate_config', 'successor_candidate_execution_defaults', 'successor_candidate_identity', 'successor_candidate_rootfs', 'successor_checkout_exception', 'successor_ci_image_binding', 'successor_ci_member_bound', 'successor_ci_member_changed', 'successor_ci_zip_changed', 'successor_container_inventory', 'successor_container_state', 'successor_control_binding', 'successor_control_identity', 'successor_control_inventory', 'successor_cow_bound', 'successor_cow_shape', 'successor_current_profile', 'successor_datastore_inventory', 'successor_datastore_kind', 'successor_datastore_not_ready', 'successor_disposition_scope', 'successor_duplicate_key', 'successor_evidence_binding', 'successor_evidence_inventory', 'successor_final_five_candidates_required', 'successor_functional_capture_not_healthy', 'successor_health_shape', 'successor_host_completion_facts', 'successor_identity', 'successor_image_evidence_required', 'successor_image_scope', 'successor_initial_inventory', 'successor_initial_profile_drift', 'successor_initial_projection', 'successor_inventory_changed_during_check', 'successor_json_constant', 'successor_kernel_fact_changed', 'successor_load_measurement_missing', 'successor_loaded_archive_proof', 'successor_loaded_candidate_required', 'successor_loaded_identity', 'successor_loaded_rootfs', 'successor_loader_override', 'successor_mount_shape', 'successor_native_profile', 'successor_network_alias_drift', 'successor_nonselected_runtime_drift', 'successor_normal_imports_failed', 'successor_override_scope', 'successor_owned_target_identity', 'successor_owned_target_image', 'successor_owned_target_policy_drift', 'successor_payload_changed', 'successor_permission_scope', 'successor_phase_image_drift', 'successor_phase_inventory', 'successor_phase_projection', 'successor_projection_bound', 'successor_public_code_cow_changed', 'successor_public_source_drift', 'successor_raw_finding_scope_changed', 'successor_recovery_not_original_images', 'successor_recovery_scope', 'successor_recovery_window', 'successor_restart_drift', 'successor_restart_intent', 'successor_role_launch_drift', 'successor_role_scope', 'successor_rollback_not_owned', 'successor_rollback_requires_known_completed_write', 'successor_runtime_env_delta', 'successor_source_scope', 'successor_static_evidence_changed', 'successor_store_root_changed', 'successor_store_root_scope', 'successor_target_missing', 'successor_target_order', 'successor_target_pid_missing', 'successor_target_pid_namespace', 'successor_unexpected_health_incident', 'successor_unknown_upload_writer', 'successor_unreviewed_image_environment_delta', 'successor_upload_ancestor', 'successor_upload_directory', 'successor_upload_entry', 'successor_upload_metadata_bound', 'successor_upload_metadata_timeout', 'successor_upload_namespace_drift', 'successor_upload_owner_shape', 'successor_upload_roots', 'successor_window_closed', 'successor_window_fields', 'successor_window_timestamp', 'target_daemon_outcome_unknown_fences_retained', 'target_inventory_incomplete', 'target_result_binding', 'target_result_identifier', 'target_result_image', 'target_result_status', 'target_worker_outcome_unknown', 'two_host_operation_pending', 'unapproved_checkout_change', 'unexpected_tmpfs', 'unresolved_compose_environment', 'unresolved_network_shape', 'unreviewed_endpoint_policy', 'unsafe_control_file', 'unsafe_control_parent', 'unsafe_existing_ssh_identity', 'unsafe_public_source', 'unsafe_runtime_env_metadata', 'unsupported_consumer_feature', 'unsupported_port_shape', 'unsupported_restart_intent', 'unsupported_volume_options', 'unsupported_volume_shape', 'wrong_security_consumer_host'))

def require(ok, code):
    if not ok: raise RuntimeError(code)


def secure_parents(path):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()} and not info.st_mode & 0o022, 'unsafe_control_parent')


def read(path, maximum=2 * 1024 * 1024, public=False):
    path = Path(path)
    require(path.is_absolute() and path.resolve(strict=True) == path, 'redirected_control_path')
    secure_parents(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid() and before.st_nlink == 1 and
                not before.st_mode & (0o022 if public else 0o077) and before.st_size <= maximum, 'unsafe_control_file')
        data = bytearray()
        while block := os.read(fd, min(65536, maximum + 1 - len(data))):
            data.extend(block)
            require(len(data) <= maximum, 'control_file_bound')
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_uid, s.st_nlink, s.st_mtime_ns, s.st_ctime_ns)
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()) and len(data) == before.st_size, 'control_file_changed')
        return bytes(data)
    finally: os.close(fd)


def load_module(path, name, expected):
    raw = read(path, maximum=65536, public=True)
    require(sha256(raw).hexdigest() == expected, 'public_control_source_changed')
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def private_json(argv, timeout=30, input_data=None, *, maximum=8*1024*1024, raw_output=False):
    """Bound BEFORE accumulation; sensitive command output never touches disk."""
    require(type(maximum) is int and 0 < maximum <= 8*1024*1024, 'private_metadata_output_bound')
    fd = os.memfd_create('security-consumer-private-json', os.MFD_CLOEXEC)
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE, (maximum, maximum))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    try:
        try:
            require(input_data is None or type(input_data) is bytes and len(input_data) <= 1024**2,
                    'private_metadata_input_bound')
            io = {'stdin': subprocess.DEVNULL} if input_data is None else {'input': input_data}
            subprocess.run(argv, **io, stdout=fd, stderr=subprocess.DEVNULL,
                           timeout=timeout, check=True, preexec_fn=limits,
                           env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                                'DOCKER_HOST': 'unix:///var/run/docker.sock',
                                'DOCKER_CONFIG': str(HERE / 'docker-config')})
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError('private_metadata_command_failed') from None
        require(os.lseek(fd, 0, os.SEEK_END) < maximum, 'private_metadata_output_bound')
        os.lseek(fd, 0, os.SEEK_SET)
        raw = bytearray()
        while block := os.read(fd, 65536): raw.extend(block)
        return bytes(raw) if raw_output else json.loads(raw)
    finally: os.close(fd)


# Exact public payload paths arrive over stdin. Never traverse uploads, audit,
# certificates, configuration or any directory not needed by those paths.
SOURCE_PROBE = r'''
import hashlib,json,os,resource,signal,stat,sys
resource.setrlimit(resource.RLIMIT_CPU,(20,20));resource.setrlimit(resource.RLIMIT_AS,(512*1024**2,)*2)
resource.setrlimit(resource.RLIMIT_CORE,(0,0));signal.signal(signal.SIGALRM,signal.default_int_handler);signal.alarm(20)
raw=sys.stdin.buffer.read(1024**2+1);assert len(raw)<=1024**2
rows=json.loads(raw);assert type(rows)is list and len(rows)==857
expected_uid=int(sys.argv[1]);assert expected_uid>=0
directories={};snapshots={};total=0
def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def folder(parts):
    if parts in directories:return directories[parts]
    if not parts:fd=os.open('/app',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    else:
        parent=folder(parts[:-1]);fd=os.open(parts[-1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        assert identity(os.fstat(fd))==identity(os.stat(parts[-1],dir_fd=parent,follow_symlinks=False))
    s=os.fstat(fd);assert stat.S_ISDIR(s.st_mode) and s.st_uid==expected_uid and not s.st_mode&0o022
    directories[parts]=fd;snapshots[parts]=identity(s);assert len(directories)<=8192
    return fd
try:
    seen=set()
    for row in rows:
        path=row['path'];parts=tuple(path.split('/'));assert path.endswith('.py') and path not in seen
        assert len(parts)<=24 and all(p and p not in {'.','..'} for p in parts) and not path.startswith('/')
        seen.add(path);parent=folder(parts[:-1]);fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
        try:
            s=os.fstat(fd);assert stat.S_ISREG(s.st_mode) and s.st_uid==expected_uid and s.st_nlink==1
            assert stat.S_IMODE(s.st_mode)==int(row['mode'][-3:],8) and s.st_size==row['bytes']<=4*1024**2
            digest=hashlib.sha256();count=0
            while block:=os.read(fd,min(65536,s.st_size+1-count)):
                count+=len(block);assert count<=s.st_size;digest.update(block)
            assert count==s.st_size and digest.hexdigest()==row['sha256']
            assert identity(s)==identity(os.fstat(fd))==identity(os.stat(parts[-1],dir_fd=parent,follow_symlinks=False))
            total+=count;assert total<=64*1024**2
        finally:os.close(fd)
    for parts,fd in directories.items():
        assert identity(os.fstat(fd))==snapshots[parts]
        named=os.stat(parts[-1],dir_fd=directories[parts[:-1]],follow_symlinks=False) if parts else os.stat('/app',follow_symlinks=False)
        assert identity(named)==snapshots[parts]
    assert total==13194027
    metadata=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    print(json.dumps({'schema':'security_consumer_public_source/1','files':857,'bytes':total,'metadata_sha256':metadata,
                      'app_root_verified':True,'application_imported':False,'production_data_read':False},sort_keys=True))
finally:
    for fd in directories.values():os.close(fd)
'''

IMPORT_PROBE = r'''
import os,resource,signal,time,sys
resource.setrlimit(resource.RLIMIT_CPU,(20,20));resource.setrlimit(resource.RLIMIT_CORE,(0,0))
signal.signal(signal.SIGALRM,signal.default_int_handler);signal.alarm(20)
start=time.monotonic()
candidate=sys.argv[1]=='candidate'
assert not candidate or os.environ.get('PYTHONDONTWRITEBYTECODE')=='1'
import ssl,sqlite3,psycopg2,magic,pydantic
assert ssl.create_default_context().get_ca_certs()
import json
print(json.dumps({'schema':'security_consumer_normal_imports/1','passed':True,'elapsed_ms':int((time.monotonic()-start)*1000),
                  'runtime_no_bytecode_verified':candidate,'application_imported':False,'private_environment_output':False},sort_keys=True))
'''


def native(site, contract, record=None, *, recovering=False):
    require(os.geteuid() == 0 and Path('/proc/1/exe').readlink().name == 'systemd', 'native_root_required')
    require(not any(Path(path).exists() for path in ('/.dockerenv', '/run/.containerenv', '/run/systemd/container')), 'container_refused')
    for name in ('pid', 'mnt', 'user'):
        require(os.readlink('/proc/self/ns/' + name) == os.readlink('/proc/1/ns/' + name), 'namespace_mismatch')
    links = private_json(['/usr/sbin/ip', '-j', 'address'])
    require(any(value.get('local') == contract.SITES[site]['host'] for link in links
                for value in link.get('addr_info', [])), 'wrong_security_consumer_host')
    require(record is not None, 'successor_authority_required')
    contract.assert_window(record, datetime.now(timezone.utc), recovering=recovering)
    require(os.uname().release == ('6.8.0-147-generic' if site == 'bot' else '7.0.0-38-generic'),
            'successor_kernel_fact_changed')
    if site == 'bot':
        require(os.uname().release == '6.8.0-147-generic', 'native_kernel_not_remediated')


def native_coordinator_owner(record, b, window_sha256=None):
    """The native worker must observe real held fences, not a claimed PID.

    Web executes through the fixed, authenticated Ansible hop. It cannot inspect
    a remote host's /proc; this is deliberately not a cross-host lease scheme.
    """
    owner = b.document(b.RESERVATION)
    require(record.get('schema') == 'security_consumer_window/2', 'successor_authority_required')
    require(owner.get('schema') == 'security_consumer_release_guard/2' and
            owner.get('operation_id') == OPERATION and owner.get('nonce') == record['nonce'] and
            owner.get('source_commit') == record['source_commit'] and
            type(owner.get('pid')) is int and owner['pid'] > 1 and
            owner.get('inode') == b.RESERVATION.lstat().st_ino, 'native_coordinator_reservation_missing')
    require(type(window_sha256) is str and re.fullmatch('[a-f0-9]{64}', window_sha256)
            and owner.get('window_sha256') == window_sha256
            and owner.get('source_mutex') == record.get('source_mutex')
            and list(b.identity(b.SOURCE.lstat())) == record['source_mutex']
            and b.SOURCE.lstat().st_size == 0, 'native_coordinator_source_missing')
    # /proc is not a regular stable control file. This reviewed command reader
    # bounds public numeric lock metadata before accumulation; no Env is read.
    locks = b.command(['/usr/bin/cat', '/proc/locks'])
    held = set()
    for line in locks.splitlines():
        fields = line.split()
        if len(fields) < 8 or fields[1:4] != ['FLOCK', 'ADVISORY', 'WRITE']:
            continue
        major, minor, inode = fields[5].split(':')
        held.add((int(fields[4]), os.makedev(int(major, 16), int(minor, 16)), int(inode)))
    for path in (b.MARKER, b.SOURCE, b.RESERVATION):
        info = path.lstat()
        require((owner['pid'], info.st_dev, info.st_ino) in held, 'native_coordinator_fences_not_held')
    require(list(b.identity(b.SOURCE.lstat())) == record['source_mutex'], 'native_coordinator_source_missing')


def artifact(path, expected, site=None, raw=None, *, recovering=False):
    raw = read(path) if raw is None else raw
    require(isinstance(raw, bytes) and len(raw) <= 2 * 1024 * 1024, 'acceptance_buffer_invalid')
    require(re.fullmatch('[a-f0-9]{64}', expected) and sha256(raw).hexdigest() == expected, 'acceptance_bytes_changed')
    def unique(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'successor_duplicate_key');value[key] = item
        return value
    data = json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, 'successor_json_constant'))
    require(type(data) is dict and data.get('schema') == 'security_consumer_window/2',
            'successor_authority_required')
    contract = load_module(HERE / 'security_consumer_contract.py', '_window_contract',
                           data['control_sources']['security_consumer_contract.py'])
    contract.assert_window(data, datetime.now(timezone.utc), recovering=recovering)
    evidence = evidence_documents(data, site)
    disposition, source = evidence['finding_dispositions'], evidence['source_closures']
    require(disposition.get('schema') == 'security_app_image_role_finding_disposition/1'
            and disposition.get('image_id') == contract.CANDIDATE
            and disposition.get('raw_report_sha256') == contract.RAW_REPORT_SHA256
            and disposition.get('sbom_sha256') == contract.SBOM_SHA256
            and disposition.get('raw_high_rows') == disposition.get('raw_rows_reconciled') == 44
            and disposition.get('global_security_eligible') is False
            and disposition.get('raw_scanner_gate_waived') is False, 'successor_disposition_scope')
    acl = [row for row in disposition['findings'] if row['cve'] == 'CVE-2026-54369']
    require(len(acl) == 1 and acl[0]['status'] == 'under_investigation'
            and acl[0]['legacy_library_apis_globally_fixed'] is False
            and source.get('all_manifest_python_files') == 857
            and source.get('source_code_executed') is False, 'successor_source_scope')
    admission = evidence['candidate_admission']
    contract.assert_loaded_candidate(data, admission)
    image_evidence = evidence['image_security']
    require(image_evidence.get('schema') == 'security_consumer_image_evidence/2'
            and image_evidence.get('image_id') == contract.CANDIDATE
            and image_evidence.get('evidence_zip_sha256') == contract.EVIDENCE_ZIP_SHA256
            and image_evidence.get('raw_report_sha256') == contract.RAW_REPORT_SHA256
            and image_evidence.get('sbom_sha256') == contract.SBOM_SHA256
            and image_evidence.get('composition_sha256') == contract.COMPOSITION_SHA256
            and image_evidence.get('security_accepted') is False and image_evidence.get('raw_gate_passed') is False,
            'successor_image_evidence_required')
    artifact_admission = evidence['web_artifact_admission']
    admitted_archive = artifact_admission.get('archive_binding') or {}
    require(artifact_admission.get('schema') == 'app8da_candidate_staging_recovery/1'
            and artifact_admission.get('status') == 'recovered_artifact_admitted_not_loaded'
            and artifact_admission.get('zip_sha256') == contract.IMAGE_ZIP_SHA256
            and artifact_admission.get('zip_bytes') == contract.IMAGE_ZIP_BYTES
            and admitted_archive.get('config_id') == contract.CANDIDATE
            and admitted_archive.get('archive_sha256') == contract.SOURCE_TAR_SHA256
            and admitted_archive.get('archive_bytes') == contract.SOURCE_TAR_BYTES
            and admitted_archive.get('physical_graph_closed') is True
            and artifact_admission.get('docker_load_attempted') is False
            and artifact_admission.get('configuration_values_disclosed') is False,
            'successor_artifact_admission_required')
    if site != 'web':verify_ci_evidence(image_evidence, disposition, contract)
    require(evidence['web_engine_completion'].get('schema') == 'web_engine_actual_completion/1'
            and evidence['web_engine_completion'].get('phase') == 'complete'
            and evidence['web_engine_completion'].get('worker_succeeded') is True
            and evidence['web_engine_completion'].get('owned_resources_absent') is True
            and evidence['web_host_maintenance'].get('status') == 'completed'
            and evidence['web_host_maintenance'].get('postboot_result_sha256') == contract.WEB_KERNEL_POSTBOOT_SHA256
            and evidence['web_kernel_postboot'].get('status') == 'postboot_verified'
            and evidence['web_kernel_postboot'].get('active_kernel') == '7.0.0-38-generic', 'successor_host_completion_facts')
    return data


def evidence_documents(record, site=None):
    result = {}
    for name, binding in record['evidence'].items():
        path = HERE / 'evidence' / (name + '.json') if site == 'web' else Path(binding['path'])
        raw = read(path, public=True)
        require(sha256(raw).hexdigest() == binding['sha256'], 'accepted_evidence_drift')
        value = json.loads(raw)
        def public(item):
            if type(item) is dict:
                require(not ({'Env', 'Config', 'HostConfig', 'environment', 'resolved_environment'} & set(item)),
                        'private_configuration_not_stageable')
                for child in item.values():public(child)
            elif type(item) is list:
                for child in item:public(child)
        public(value);result[name] = value
    return result




def verify_ci_evidence(binding, disposition, contract):
    """Reconcile actual immutable CI bytes in RAM; stage only filtered controls."""
    raw = read(Path(binding['evidence_zip_path']), maximum=1024**2, public=True)
    require(len(raw) == contract.EVIDENCE_ZIP_BYTES and sha256(raw).hexdigest() == contract.EVIDENCE_ZIP_SHA256,
            'successor_ci_zip_changed')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        def member(name, pin):
            info = archive.getinfo(name)
            require(0 < info.file_size <= 32*1024**2, 'successor_ci_member_bound')
            payload = archive.read(name)
            require(len(payload) == info.file_size and sha256(payload).hexdigest() == pin, 'successor_ci_member_changed')
            return json.loads(payload)
        report = member('reports/vulnerabilities.json', contract.RAW_REPORT_SHA256)
        member('reports/sbom.cdx.json', contract.SBOM_SHA256)
        composition = member('reports/composition.json', contract.COMPOSITION_SHA256)
    require(report['Metadata']['ImageID'] == composition['image_id'] == contract.CANDIDATE
            and composition['app_and_python_unchanged'] is True and composition['runtime_config_unchanged'] is True
            and composition['libc_unchanged'] is True and composition['security_accepted'] is False
            and composition['raw_scanner_gate_waived'] is False, 'successor_ci_image_binding')
    high = [row for result in report['Results'] for row in result.get('Vulnerabilities') or []
            if row['Severity'] in {'HIGH', 'CRITICAL'}]
    def digest(value):return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
    actual = Counter((row['VulnerabilityID'], row['PkgName'], row['InstalledVersion'], digest(row)) for row in high)
    expected = Counter((finding['cve'], row['package'], row['installed_version'], row['raw_row_sha256'])
                       for finding in disposition['findings'] for row in finding['raw_rows'])
    require(actual == expected and len(high) == 44 and all(row['Severity'] == 'HIGH' for row in high),
            'successor_raw_finding_scope_changed')


def workspace(site, record):
    parent = Path('/root/trading-bot/trading_bot/.local') if site == 'bot' else Path('/srv/trading-bot')
    return parent / ('security-consumers-20261024-02-' + record['nonce'])


def upload_boundary(site, current, *, maximum_entries=20000, expected_uid=0):
    """Metadata only: prove the exact roots and all possible container writers.

    No file content, private child name or path digest is returned or persisted.
    Two bounded traversals compare namespace identity in RAM, including every
    retained directory FD and named attachment.
    """
    end = time.monotonic() + 20
    require(type(expected_uid) is int and expected_uid >= 0, 'successor_upload_owner_shape')
    approved = {'/trading_bot_app', '/trading_bot_bot'} if site == 'bot' else {'/trading_bot_app'}
    roots = {str(Path(m['Source']).resolve(strict=True)) for row in current if row['name'] in approved
             for m in row['mounts'] if m['Destination'] in {'/app/uploads', '/app/audit_trail'} and m['RW'] is True}
    require(len(roots) == 2, 'successor_upload_roots')
    def ancestry(path):
        return {(p.stat().st_dev, p.stat().st_ino) for p in (path, *path.parents)}
    root_ids = {name: ancestry(Path(name)) for name in roots}
    for row in current:
        for mount in row['mounts']:
            require(time.monotonic() < end, 'successor_upload_metadata_timeout')
            if mount['RW'] is not True:continue
            source = Path(mount['Source']).resolve(strict=True);info = source.stat()
            source_ids = ancestry(source);own_id = (info.st_dev, info.st_ino)
            shares = any(own_id in ids or (Path(name).stat().st_dev, Path(name).stat().st_ino) in source_ids
                         for name, ids in root_ids.items())
            require(not shares or row['name'] in approved and str(source) in roots,
                    'successor_unknown_upload_writer')
    def identity(s):return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink)
    def observe(root):
        descriptors = [];seen = [];directories = 0;regular = 0
        try:
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW);descriptors.append(fd)
            for part in Path(root).parts[1:]:
                parent = fd;fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                descriptors.append(fd);info = os.fstat(fd)
                require(info.st_uid in {0, expected_uid} and not info.st_mode & 0o022
                        and identity(info) == identity(os.stat(part, dir_fd=parent, follow_symlinks=False)),
                        'successor_upload_ancestor')
            def visit(folder, relative):
                nonlocal directories, regular
                require(time.monotonic() < end, 'successor_upload_metadata_timeout')
                before = os.fstat(folder);directories += 1
                require(directories <= 2048 and before.st_uid == expected_uid and not before.st_mode & 0o022,
                        'successor_upload_directory')
                with os.scandir(folder) as entries:names = sorted(entry.name for entry in entries)
                for name in names:
                    require(time.monotonic() < end and len(seen) < maximum_entries, 'successor_upload_metadata_bound')
                    info = os.stat(name, dir_fd=folder, follow_symlinks=False);path = relative + (name,)
                    require(info.st_uid == expected_uid and not info.st_mode & 0o022
                            and (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                            'successor_upload_entry')
                    seen.append((path, identity(info)))
                    if stat.S_ISDIR(info.st_mode):
                        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
                        try:
                            require(identity(os.fstat(child)) == identity(info), 'successor_upload_namespace_drift')
                            visit(child, path)
                            require(identity(os.fstat(child)) == identity(os.stat(name, dir_fd=folder, follow_symlinks=False)),
                                    'successor_upload_namespace_drift')
                        finally:os.close(child)
                    else:
                        child = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=folder)
                        try:
                            require(identity(os.fstat(child)) == identity(info) == identity(
                                os.stat(name, dir_fd=folder, follow_symlinks=False)), 'successor_upload_namespace_drift')
                        finally:os.close(child)
                        regular += 1
                require(identity(before) == identity(os.fstat(folder)), 'successor_upload_namespace_drift')
            visit(fd, ())
            # Retained FD traversal and its original named attachments stay identical.
            for index, part in enumerate(Path(root).parts[1:]):
                require(identity(os.fstat(descriptors[index+1])) == identity(os.stat(
                    part, dir_fd=descriptors[index], follow_symlinks=False)), 'successor_upload_namespace_drift')
            return seen, directories, regular
        finally:
            for fd in reversed(descriptors):os.close(fd)
    totals = [0, 0]
    for root in sorted(roots):
        before = observe(root);after = observe(root)
        require(before == after, 'successor_upload_namespace_drift')
        totals[0] += before[1];totals[1] += before[2]
    return {'schema': 'security_consumer_upload_boundary/1', 'roots': 2, 'directories': totals[0],
            'regular_files': totals[1], 'complete_writer_inventory_verified': True,
            'private_names_or_contents_reported': False, 'file_contents_read': False}






def current_public(site, b, *, recovering_name=None):
    """Complete public policy projection; runtime Config/Env never enters it."""
    ids = b.command(['/usr/bin/docker', 'container', 'ls', '-aq', '--no-trunc']).split()
    require(len(ids) == (27 if site == 'web' else 15) and len(set(ids)) == len(ids)
            and all(re.fullmatch('[a-f0-9]{64}', value) for value in ids), 'successor_container_inventory')
    fmt = ('{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},'
        '"running":{{json .State.Running}},"paused":{{json .State.Paused}},"restarting":{{json .State.Restarting}},'
        '"restart_policy":{{json .HostConfig.RestartPolicy}},'
        '"project":{{json (index .Config.Labels "com.docker.compose.project")}},'
        '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
        '"mounts":{{json .Mounts}},"ports":{{json .HostConfig.PortBindings}},'
        '"network_policy":{{json .NetworkSettings.Networks}},"network_mode":{{json .HostConfig.NetworkMode}},'
        '"pid_mode":{{json .HostConfig.PidMode}},"privileged":{{json .HostConfig.Privileged}},'
        '"read_only":{{json .HostConfig.ReadonlyRootfs}},'
        '"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}')
    values = [b.decode(line.encode()) for line in b.command(
        ['/usr/bin/docker', 'inspect', '--format', fmt, *sorted(ids)]).splitlines()]
    require(len(values) == len(ids) and {r.get('id') for r in values} == set(ids), 'successor_container_inventory')
    known = {'f2dbc15b9c9a2d14ef9669d1f6de37096161469a50c47bf6609f0383886b3a27',
             'f0f5ddc3efe1a55cc8a6f800ad59d1f41556a4f896cdc8ed7d20864c9d360e2a'} if site == 'web' else set()
    for row in values:
        recovering = row.get('name') == recovering_name
        require(type(row.get('running')) is bool and row.get('paused') is False
                and type(row.get('restarting')) is bool and (recovering or row['restarting'] is False)
                and type(row.get('network_policy')) is dict, 'successor_container_state')
        health = row.pop('health')
        require(health in {None, 'starting', 'healthy', 'unhealthy'}, 'successor_health_shape')
        if row['running'] and row['id'] not in known and not recovering:
            require(health in {None, 'healthy'}, 'successor_unexpected_health_incident')
        if row['id'] == '6a16c8a1b4cdab3b4bcd2a0d4d4998ced7a455cc6c364255e5e0e3dacd9c2752':
            require(health == 'healthy', 'successor_functional_capture_not_healthy')
        row['ports'] = row['ports'] or {}
        row['network_names'] = sorted(row['network_policy'])
    return sorted(values, key=lambda row: row['name'])


def compose_prefix(site, contract, override=None):
    scope = contract.SITES[site]
    result = ['/usr/bin/docker', 'compose', '--project-name', scope['project'], '--project-directory', scope['workdir']]
    for path in scope['files'] + ([str(override)] if override else []): result += ['-f', path]
    return result


def phase_override(service, image, contract):
    prefix = 'rollback-' if image in contract.OLD_IMAGES.values() else 'security-'
    return HERE / (prefix + service + '.override.json')


def phase_targets(site, contract, projection, *, recovering_service=None):
    names = [name.lstrip('/') for name in contract.SITES[site]['services'].values()]
    values = private_json(['/usr/bin/docker', 'inspect', *names])
    require(type(values) is list and len(values) == len(names), 'target_inventory_incomplete')
    result = {}
    for item in values:
        service = item['Config']['Labels'].get('com.docker.compose.service')
        require(service in contract.SITES[site]['services'] and service not in result, 'service_identity_mismatch')
        entry = projection.get(site + '/' + service)
        override = phase_override(service, entry['image'], contract) if entry else None
        require(contract.assert_target(site, item, managed_override=override,
            running=service != recovering_service) == service, 'service_identity_mismatch')
        result[service] = item
    return result


def private_pid_namespace(item):
    """Kernel namespace metadata only; never /proc environment or arguments."""
    pid = item['State'].get('Pid')
    require(type(pid) is int and pid > 1, 'successor_target_pid_missing')
    path = '/proc/' + str(pid) + '/ns/pid'
    namespace = os.readlink(path)
    require(re.fullmatch(r'pid:\[[0-9]+\]', namespace)
            and namespace != os.readlink('/proc/1/ns/pid')
            and os.readlink(path) == namespace, 'successor_target_pid_namespace')


def successor_phase(site, record, contract, b, *, original=None, projection=None, verify_sources=False,
                    recovering_service=None, recovery=False):
    """Pre/post predicates; neither result nor error carries private configuration."""
    projection = {} if projection is None else projection
    contract.assert_window(record, datetime.now(timezone.utc), recovering=recovery)
    documents = evidence_documents(record, site)
    if site == 'web':
        plan = documents['web_current_profile']
        require(len(plan['store_roots']) == 2 and {r['path'] for r in plan['store_roots']} ==
                {'/var/lib/docker', '/var/lib/containerd'}, 'successor_store_root_scope')
        for binding in plan['store_roots']:
            path = Path(binding['path']);info = path.lstat()
            require(path.resolve(strict=True) == path and stat.S_ISDIR(info.st_mode)
                    and info.st_dev == binding['device'] and info.st_ino == binding['inode']
                    and b.command(['/usr/bin/findmnt', '-n', '-o', 'UUID', '-T', str(path)]) == binding['uuid'],
                    'successor_store_root_changed')
    configuration = config_metadata(site, contract, b)
    recovering_name = contract.SITES[site]['services'].get(recovering_service)
    current = current_public(site, b, recovering_name=recovering_name)
    if original is None:
        profile = documents['web_current_profile' if site == 'web' else 'native_public_baseline']
        contract.assert_fact_profile(site, profile, current)
    else:
        require(configuration == original['config_binding'], 'runtime_config_changed')
        contract.assert_phase_inventory(site, original['all_containers'], current, projection,
                                       recovering_service=recovering_service, candidate_images=record['images'])
    targets = phase_targets(site, contract, projection, recovering_service=recovering_service)
    candidate_image = record['images'][site]
    ids = {contract.OLD_IMAGES[site], candidate_image}
    images = {row['Id']: row for row in private_json(['/usr/bin/docker', 'image', 'inspect', *sorted(ids)])}
    require(set(images) == ids, 'image_not_preloaded')
    contract.assert_candidate(record, images[candidate_image], site=site, admission=documents['candidate_admission'])
    contract.assert_loader_defaults(images[contract.OLD_IMAGES[site]], images[candidate_image])
    rendered = private_json([*compose_prefix(site, contract), 'config', '--format', 'json'])
    source_receipts = {}
    for service, item in targets.items():
        key = site + '/' + service;entry = projection.get(key)
        expected = entry['image'] if entry else contract.OLD_IMAGES[site]
        require(item['Image'] == expected and (entry is None or item['Id'] == entry['id']), 'successor_phase_image_drift')
        policy = item['HostConfig']['RestartPolicy']
        require(policy['Name'] in {'no', 'always', 'unless-stopped'} and policy['MaximumRetryCount'] == 0,
                'unsupported_restart_intent')
        if original is not None:require(policy == original['targets'][service]['restart_policy'], 'successor_restart_drift')
        rendered['services'][service]['restart'] = policy['Name']
        candidate = item['Image'] == candidate_image
        if candidate:
            explicit = rendered['services'][service].setdefault('environment', {})
            explicit.update(contract.RUNTIME_ENV_DELTA)
        override = phase_override(service, expected, contract) if entry else None
        contract.assert_config_parity(site, service, item, rendered, images[expected], images[expected],
                                      managed_override=override, running=service != recovering_service, strict_serving=True)
        contract.assert_role_launch(site, service, item, candidate=candidate)
        if item['State']['Running'] is True:private_pid_namespace(item)
        if candidate and service != recovering_service:
            cow = private_json(['/usr/bin/docker', 'diff', item['Id']], maximum=1024**2, raw_output=True).decode('utf-8')
            # Defer only the /app directory's metadata until the mandatory
            # retained-FD source proof checks its ownership/mode below. Reject
            # all code/interpreter/cache changes before invoking Python.
            contract.assert_public_diff('\n'.join(line for line in cow.splitlines() if line != 'C /app'))
            rows = [{k: row[k] for k in ('path', 'bytes', 'sha256', 'git_blob', 'mode')}
                    for row in documents['payload_manifest']['files'] if row['path'].endswith('.py')]
            rows.sort(key=lambda row: row['path'])
            payload = json.dumps(rows, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
            proof = private_json(['/usr/bin/docker', 'exec', '-i', item['Id'], '/usr/local/bin/python',
                                  '-I', '-B', '-c', SOURCE_PROBE, '0'], timeout=25, input_data=payload)
            require(proof.get('schema') == 'security_consumer_public_source/1' and proof.get('files') == 857
                    and proof.get('bytes') == 13194027 and proof.get('app_root_verified') is True
                    and proof.get('application_imported') is False and proof.get('production_data_read') is False
                    and proof.get('metadata_sha256') == documents['source_closures']['all_python_source_metadata_sha256'],
                    'successor_public_source_drift')
            contract.assert_public_diff(cow, verified_app_root=proof['app_root_verified'])
            source_receipts[service] = proof
            after = private_json(['/usr/bin/docker', 'diff', item['Id']], maximum=1024**2, raw_output=True).decode('utf-8')
            contract.assert_public_diff(after, verified_app_root=True)
    boundary = upload_boundary(site, current)
    require(config_metadata(site, contract, b) == configuration, 'runtime_config_changed_during_preflight')
    require(current_public(site, b, recovering_name=recovering_name) == current, 'successor_inventory_changed_during_check')
    contract.assert_window(record, datetime.now(timezone.utc), recovering=recovery)
    # Full resolved objects are returned ONLY to the in-memory caller. Public
    # serialization below deliberately extracts identity/policy fields first.
    return {'targets_private': targets, 'all_containers': current, 'config_binding': configuration,
            'upload_boundary': boundary, 'source_receipts': source_receipts}


def successor_snapshot(site, record, contract, b):
    checked = successor_phase(site, record, contract, b)
    public = {service: {**contract.public_identity(item), 'restart_policy': item['HostConfig']['RestartPolicy']}
              for service, item in checked['targets_private'].items()}
    return {'schema': 'security_consumer_site_baseline/2', 'operation_id': contract.SUCCESSOR_OPERATION,
            'site': site, 'nonce': record['nonce'], 'source_commit': record['source_commit'],
            'targets': public, 'all_containers': checked['all_containers'], 'config_binding': checked['config_binding'],
            'upload_boundary': checked['upload_boundary'], 'environment_persisted_or_hashed': False,
            'global_security_eligible': False}


def role_smoke(service, item, record, b, *, candidate):
    absolute = record['deadline_utc'] if candidate else record['recovery']['deadline_utc']
    end = min(time.monotonic() + 180,
              time.monotonic() + (datetime.fromisoformat(absolute.replace('Z', '+00:00'))
                                  - datetime.now(timezone.utc)).total_seconds())
    if service == 'app':
        while True:
            try:
                raw = private_json(['/usr/bin/docker', 'exec', item['Id'], '/usr/local/bin/python', '-I', '-B', '-c',
                                    b.CONTAINER_HTTP_PROBE, 'http://127.0.0.1:8000/api/config'], timeout=10,
                                   maximum=1024, raw_output=True)
                require(raw.strip() == b'200', 'successor_api_not_ready')
                break
            except RuntimeError:
                require(time.monotonic() < end, 'successor_api_not_ready')
                time.sleep(1)
    imports = private_json(['/usr/bin/docker', 'exec', item['Id'], '/usr/local/bin/python', '-I', '-B', '-c',
                            IMPORT_PROBE, 'candidate' if candidate else 'rollback'], timeout=25, maximum=4096)
    require(imports.get('schema') == 'security_consumer_normal_imports/1' and imports.get('passed') is True
            and imports.get('runtime_no_bytecode_verified') is candidate
            and imports.get('private_environment_output') is False
            and type(imports.get('elapsed_ms')) is int and 0 <= imports['elapsed_ms'] <= 20000,
            'successor_normal_imports_failed')
    return imports


def datastore_smoke(site, record, current):
    documents = evidence_documents(record, site)
    if site == 'web': probes = documents['web_current_profile']['current_profile']['datastore_probes']
    else:
        probes = [{'id': row['id'], 'kind': 'redis' if row['name'] == '/trading_bot_redis' else 'postgres'}
                  for row in current if row['name'] in {'/trading_bot_db', '/trading_bot_redis'}]
    require(len(probes) == (6 if site == 'web' else 2), 'successor_datastore_inventory')
    for probe in probes:
        if probe['kind'] == 'redis':
            raw = private_json(['/usr/bin/docker', 'exec', probe['id'], 'redis-cli', 'ping'],
                               maximum=1024, raw_output=True)
            require(raw.strip() == b'PONG', 'successor_datastore_not_ready')
        else:
            require(probe['kind'] == 'postgres', 'successor_datastore_kind')
            private_json(['/usr/bin/docker', 'exec', probe['id'], '/bin/sh', '-c',
                          'exec pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
                         maximum=1024, raw_output=True)


def owned_target(site, service, image, contract):
    name = contract.SITES[site]['services'][service].lstrip('/')
    rows = private_json(['/usr/bin/docker', 'inspect', name])
    require(type(rows) is list and len(rows) == 1, 'successor_target_missing')
    item = rows[0]
    contract.assert_target(site, item, managed_override=phase_override(service, image, contract), running=False)
    require(item['Image'] == image, 'successor_owned_target_image')
    return item


def target_action(site, service, record, original, projection, contract, b, lock_fd, *, rollback=False):
    """One selected service. Unknown daemon outcomes retain every fence."""
    key = site + '/' + service
    contract.assert_window(record, datetime.now(timezone.utc), activating=not rollback, recovering=rollback)
    require((site, service) in contract.ROLE_ORDER, 'successor_role_scope')
    successor_phase(site, record, contract, b, original=original, projection=projection,
                    recovering_service=service if rollback else None, recovery=rollback)
    expected = contract.OLD_IMAGES[site] if rollback else record['images'][site]
    override = contract.target_override(site, service, expected,
        original['targets'][service]['restart_policy']['Name'], rollback=rollback, candidate_image=record['images'][site])
    override_path = phase_override(service, expected, contract)
    write(b, override_path, override, exclusive=True)
    intent = HERE / (('rollback-' if rollback else 'activation-') + service + '-intent.json')
    write(b, intent, {'schema': 'security_consumer_target_intent/2', 'operation_id': contract.SUCCESSOR_OPERATION,
        'nonce': record['nonce'], 'site': site, 'service': service,
        'old_id': original['targets'][service]['id'], 'expected_image': expected, 'phase': 'writing'}, exclusive=True)
    result = {'schema': 'security_consumer_target_result/2', 'operation_id': contract.SUCCESSOR_OPERATION,
        'nonce': record['nonce'], 'source_commit': record['source_commit'], 'site': site, 'service': service,
        'status': 'write_outcome_unknown', 'image': expected, 'source_image_id': contract.CANDIDATE, 'id': None,
        'first_failure_stage': None, 'first_failure_code': None,
        'global_security_eligible': False, 'security_accepted': False, 'raw_gate_passed': False, 'raw_gate_waived': False}
    try:
        private_json_command(b, contract.target_apply_argv(site, service, override_path), lock_fd, record=record,
                             recovery=rollback)
    except Exception as error:
        # A killed/reaped Compose client is not daemon cancellation proof.
        result.update(first_failure_stage='compose_write', first_failure_code=target_failure(error))
        return result
    write(b, intent, {'schema': 'security_consumer_target_intent/2', 'operation_id': contract.SUCCESSOR_OPERATION,
        'nonce': record['nonce'], 'site': site, 'service': service,
        'old_id': original['targets'][service]['id'], 'expected_image': expected, 'phase': 'write_completed'})
    item = owned_target(site, service, expected, contract)
    next_projection = {**projection, key: {'id': item['Id'], 'image': expected}}
    try:
        contract.assert_role_launch(site, service, item, candidate=not rollback)
        cow = private_json(['/usr/bin/docker', 'diff', item['Id']], maximum=1024**2, raw_output=True).decode()
        if not rollback:contract.assert_public_diff('\n'.join(line for line in cow.splitlines() if line != 'C /app'))
        checked = successor_phase(site, record, contract, b, original=original, projection=next_projection,
                                  verify_sources=True, recovery=rollback)
        imports = role_smoke(service, item, record, b, candidate=not rollback)
        datastore_smoke(site, record, checked['all_containers'])
        result.update(status='rolled_back' if rollback else 'verified', id=item['Id'], normal_imports=imports,
            upload_boundary=checked['upload_boundary'], source_receipts=checked['source_receipts'])
        return result
    except Exception as error:
        failure = target_failure(error)
        if rollback:
            result.update(status='rollback_failed', id=item['Id'],
                          first_failure_stage='rollback_postcheck', first_failure_code=failure)
            return result
        # The write returned success, so only this known target can be restored.
        # The same site flock stays held throughout its immediate recovery.
        restored = target_action(site, service, record, original, next_projection,
                                 contract, b, lock_fd, rollback=True)
        if restored['status'] == 'rolled_back':restored['status'] = 'rolled_back_after_postcheck_failure'
        restored['first_failure_stage'] = 'target_postcheck'
        restored['first_failure_code'] = failure
        return restored


def target_failure(error):
    """Closed diagnostics only; never reflect arbitrary private exception text."""
    codes = {'successor_window_closed', 'successor_api_not_ready', 'successor_normal_imports_failed',
        'successor_datastore_not_ready', 'successor_datastore_inventory', 'successor_datastore_kind',
        'successor_phase_image_drift', 'successor_role_launch_drift', 'successor_loader_override',
        'successor_public_source_drift', 'successor_public_code_cow_changed', 'successor_cow_shape',
        'successor_initial_profile_drift', 'successor_nonselected_runtime_drift',
        'successor_owned_target_identity', 'successor_owned_target_policy_drift', 'successor_network_alias_drift',
        'successor_unknown_upload_writer', 'successor_upload_namespace_drift', 'successor_upload_entry',
        'successor_upload_ancestor', 'successor_upload_directory', 'successor_upload_metadata_bound',
        'successor_upload_metadata_timeout', 'runtime_config_changed', 'runtime_config_changed_during_preflight',
        'successor_inventory_changed_during_check', 'private_metadata_command_failed',
        'private_metadata_output_bound', 'consumer_command_failed', 'consumer_mount_drift',
        'application_environment_drift', 'unreviewed_endpoint_policy'}
    if isinstance(error, RuntimeError) and len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in codes:
        return error.args[0]
    return 'guard_failed'




def config_metadata(site, contract, b):
    """Public Compose hashes, but ONLY metadata for the existing secret .env."""
    scope = contract.SITES[site]
    result = {'compose': []}
    for alias in scope['files']:
        path = Path(alias).resolve(strict=True)
        raw = b.read(path, private=False)
        info = path.lstat()
        result['compose'].append({'alias': alias, 'resolved_path': str(path), 'inode': info.st_ino,
                                  'size': len(raw), 'sha256': sha256(raw).hexdigest()})
    path = (Path(scope['workdir']) / '.env').resolve(strict=True)
    secure_parents(path)
    fd = os.open(path, os.O_PATH | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)  # O_PATH cannot read even a single configuration byte.
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and info.st_nlink == 1 and
                stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 262144 and
                b.identity(info) == b.identity(path.lstat()), 'unsafe_runtime_env_metadata')
    finally: os.close(fd)
    result['env_metadata_only'] = [str(path), info.st_dev, info.st_ino, info.st_size, info.st_mode,
                                   info.st_uid, info.st_gid, info.st_mtime_ns, info.st_ctime_ns]
    return result










def write(b, path, data, exclusive=False):
    def public(value):
        if type(value) is dict:
            require(not ({'Env', 'Config', 'HostConfig', 'targets_private'} & set(value)), 'private_configuration_not_persistable')
            for key, item in value.items():
                if key == 'environment':require(item == {'PYTHONDONTWRITEBYTECODE': '1'}, 'private_configuration_not_persistable')
                if key in {'security_accepted', 'global_security_eligible', 'deployment_eligible', 'raw_gate_waived'}:
                    require(item is False, 'global_acceptance_not_minted')
                public(item)
        elif type(value) is list:
            for item in value:public(item)
    public(data)
    b.private_parent(path)
    fd, temporary = tempfile.mkstemp(prefix='.security-consumer-', dir=path.parent)
    try:
        b.write_all(fd, (json.dumps(data, sort_keys=True) + '\n').encode())
        os.fsync(fd)
        if exclusive: b.publish_noreplace(temporary, path)
        else:
            read(path)
            os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        os.close(fd)
        Path(temporary).unlink(missing_ok=True)


def site_lock(b, nonce, rollback):
    path = HERE / 'site-operation.lock'
    if os.path.lexists(path):
        raise RuntimeError('existing_site_operation_not_adopted')
    fd, temporary = tempfile.mkstemp(prefix='.site-lock-', dir=HERE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        payload = {'schema': 'security_consumer_site_lock/2', 'operation_id': OPERATION,
                   'nonce': nonce, 'inode': os.fstat(fd).st_ino}
        b.write_all(fd, (json.dumps(payload, sort_keys=True) + '\n').encode())
        os.fsync(fd)
        b.publish_noreplace(temporary, path)
        directory = os.open(HERE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(directory)
        finally: os.close(directory)
        return fd
    except BaseException:
        os.close(fd)
        raise
    finally: Path(temporary).unlink(missing_ok=True)


def release_site_lock(fd, nonce):
    lock = HERE / 'site-operation.lock'
    owned = json.loads(read(lock))
    require(os.fstat(fd).st_ino == lock.lstat().st_ino == owned['inode'] and
            owned['schema'] == 'security_consumer_site_lock/2' and owned['nonce'] == nonce, 'site_lock_changed')
    lock.unlink()
    directory = os.open(HERE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'snapshot', 'target', 'rollback', 'verify', 'recovered_verify'))
    parser.add_argument('--site', choices=('bot', 'web'), required=True)
    parser.add_argument('--service', choices=('app', 'bot', 'sync_worker'))
    parser.add_argument('--projection', default='{}')
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--evidence-sha256', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--primitives', type=Path, required=True)
    parser.add_argument('--apply')
    args = parser.parse_args()
    recovery = args.mode in {'rollback', 'recovered_verify'}
    record = artifact(args.evidence, args.evidence_sha256, args.site, recovering=recovery)
    b = load_module(args.primitives, '_security_consumer_primitives', HELPER_SHA)
    c = load_module(HERE / 'security_consumer_contract.py', '_security_consumer_contract',
                    record['control_sources']['security_consumer_contract.py'])
    require(sha256(read(Path(__file__).resolve(), maximum=131072, public=True)).hexdigest() ==
            record['control_sources']['security_consumer_site.py'], 'site_worker_source_changed')
    native(args.site, c, record, recovering=recovery)
    require(args.directory == HERE == workspace(args.site, record), 'site_directory_not_scoped_workspace')
    require(len(args.projection.encode()) <= 8192, 'successor_projection_bound')
    projection = json.loads(args.projection)
    allowed = {site + '/' + service for site, service in c.ROLE_ORDER}
    require(type(projection) is dict and set(projection) <= allowed, 'successor_phase_projection')
    for key, value in projection.items():
        site = key.split('/')[0]
        require(type(value) is dict and set(value) == {'id', 'image'}
                and type(value['id']) is str and re.fullmatch('[a-f0-9]{64}', value['id'])
                and value['image'] in {record['images'][site], c.OLD_IMAGES[site]}, 'successor_phase_projection')
    if args.mode in {'check', 'snapshot'}:require(not projection, 'successor_initial_projection')
    if args.mode == 'verify':
        require(set(projection) == allowed and all(v['image'] == record['images'][k.split('/')[0]] for k, v in projection.items()),
                'successor_final_five_candidates_required')
    if args.mode == 'recovered_verify':
        require(all(v['image'] == c.OLD_IMAGES[k.split('/')[0]] for k, v in projection.items()),
                'successor_recovery_not_original_images')
    if args.mode in {'target', 'rollback'}:
        require((args.site, args.service) in c.ROLE_ORDER, 'successor_role_scope')
        key = args.site + '/' + args.service
        index = c.ROLE_ORDER.index((args.site, args.service))
        if args.mode == 'target':
            require(set(projection) == {s + '/' + n for s, n in c.ROLE_ORDER[:index]}
                    and all(v['image'] == record['images'][k.split('/')[0]] for k, v in projection.items()), 'successor_target_order')
        else:
            require(key in projection and projection[key]['image'] == record['images'][args.site], 'successor_rollback_not_owned')
            previous = json.loads(read(HERE / ('result-target-' + args.service + '.json')))
            require(previous['status'] == 'verified' and previous['nonce'] == record['nonce']
                    and previous['site'] == args.site and previous['service'] == args.service
                    and previous['id'] == projection[key]['id'] and previous['image'] == record['images'][args.site],
                    'successor_rollback_requires_known_completed_write')
    if args.mode == 'check':
        successor_phase(args.site, record, c, b)
        print(json.dumps({'status': 'preflight_passed', 'site': args.site, 'production_mutated': False,
                          'global_security_eligible': False}, sort_keys=True))
        return 0
    require(args.apply == CONFIRMATION, 'explicit_scoped_apply_required')
    if args.site == 'bot':native_coordinator_owner(record, b, args.evidence_sha256)
    # Never adopt even a matching dead/unknown site lock. Known terminal phases
    # release their exact lock; an unknown daemon outcome requires root review.
    fd = site_lock(b, record['nonce'], rollback=False)
    release = False
    try:
        path = HERE / 'site-baseline.json'
        if args.mode == 'snapshot':
            write(b, path, successor_snapshot(args.site, record, c, b), exclusive=True)
            status = 'snapshot_created';release = True
        else:
            original = json.loads(read(path))
            require(original['schema'] == 'security_consumer_site_baseline/2'
                    and original['nonce'] == record['nonce'] and original['site'] == args.site
                    and original['source_commit'] == record['source_commit'], 'baseline_not_owned')
            if args.mode in {'verify', 'recovered_verify'}:
                checked = successor_phase(args.site, record, c, b, original=original,
                                          projection=projection, verify_sources=True, recovery=recovery)
                for service, item in checked['targets_private'].items():
                    role_smoke(service, item, record, b, candidate=not recovery)
                datastore_smoke(args.site, record, checked['all_containers'])
                status = 'original_images_restored' if recovery else 'site_role_postchecks_verified';release = True
                write(b, HERE / ('site-recovery-final.json' if recovery else 'site-final.json'), {'schema': 'security_consumer_site_final/2',
                    'nonce': record['nonce'], 'site': args.site, 'status': status,
                    'global_security_eligible': False, 'security_accepted': False}, exclusive=True)
            else:
                result = target_action(args.site, args.service, record, original, projection,
                                       c, b, fd, rollback=recovery)
                write(b, HERE / ('result-' + args.mode + '-' + args.service + '.json'), result, exclusive=True)
                status = result['status']
                release = status in {'verified', 'rolled_back', 'rolled_back_after_postcheck_failure'}
        if release:release_site_lock(fd, record['nonce'])
    finally:os.close(fd)
    # A completed worker invocation is not a successful target validation.
    print(json.dumps({'status': status, 'site': args.site, 'service': args.service,
                      'global_security_eligible': False, 'security_accepted': False}, sort_keys=True))
    return 0

def private_json_command(b, argv, lock_fd, record=None, *, recovery=False):
    # Reuse reviewed child-session termination/reaping while inheriting the
    # actual site flock. A killed worker cannot leave an unfenced Compose child.
    saved = [os.dup(1), os.dup(2)]
    null = os.open('/dev/null', os.O_WRONLY)
    try:
        os.dup2(null, 1); os.dup2(null, 2)
        require(record is not None, 'successor_authority_required')
        absolute = datetime.fromisoformat((record['recovery']['deadline_utc'] if recovery else record['deadline_utc']).replace('Z', '+00:00'))
        remaining = (absolute - datetime.now(timezone.utc)).total_seconds()
        require(remaining > 0, 'successor_window_closed')
        deadline = datetime.fromtimestamp(time.time() + min(240, remaining), timezone.utc).isoformat()
        status = b.execute_ansible(argv, {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
                                         'DOCKER_HOST': 'unix:///var/run/docker.sock',
                                         'DOCKER_CONFIG': str(HERE / 'docker-config')},
                                   deadline, lock_fds=(lock_fd,))
    finally:
        os.dup2(saved[0], 1); os.dup2(saved[1], 2)
        for fd in (*saved, null): os.close(fd)
    require(status == 0, 'consumer_command_failed')


if __name__ == '__main__':
    try: raise SystemExit(main())
    except Exception as error:
        print(json.dumps({'status':'failed_closed','code':error.args[0] if isinstance(error,RuntimeError) and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in PUBLIC_FAILURE_CODES else 'guard_failed'}))
        raise SystemExit(1)
