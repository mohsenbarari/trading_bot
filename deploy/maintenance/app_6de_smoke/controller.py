"""Hosted proof for one canonical App image; never activation or acceptance.

The image release and this controller have distinct immutable Git identities.
The old ed59 entrypoint/admission functions are never invoked.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse
import json
import math
import selectors
import subprocess
import os
import re
import stat
import time
import zipfile

MAX_JSON = 2*1024**2
MAX_NATIVE_REPORT = 2*1024**2
MAX_ACL_REPORT = 4*1024**2
EXPORT_CONTROLLER_SHA256 = 'c54334137c97279db2b081c484a48fb502c00ab9d750e3da354f98fa303cc716'
ARCHIVE_INVENTORY_SHA256 = 'cf5e665286429b02e64963620538c339f29e0839b60f97db58347a89764022c3'
INVENTORY_SHA256 = '8b4b0091a1e8d20786e33c97a6bb4b63c28b263180527b313883815c67cfe5dc'
SBOM_SHA256 = '171941abfdb3ec731dbd4b98730ba343eddda6732834213bec7593b95f15eb2f'
CONFIRM = 'RUN-ONLY-6DE-ISOLATED-SMOKE-AND-FLAT-PUBLIC-INVENTORY-20261027-01'
MAX_ARCHIVE = 512*1024**2
OLD_PIN = 'b2c958060d176c0098b86a44d0dc225d4494023120b3253239bef9e2f3f2acb2'
BUILDER_PIN = '9916037a7d2756c3996d04a43ad59655d30d708ae6f5b35cb20789ea190cc555'
VERIFIERS = {
    'verify_image_component_presence.py': ('3cd10d7bc591615723f7b85a87011d0d7029120324f09ef2f099d49137bcfd94', 'security_image_component_presence/1', 65536),
    'verify_acl_consumer_presence.py': ('001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778', 'security_acl_consumer_presence/1', MAX_JSON),
}
EVIDENCE_NAMES = frozenset(('source-identity.json', 'input-manifest.json', 'image-build.json', 'prepare.json', 'finalize.json', 'reports/vulnerabilities.json', 'reports/sbom.cdx.json', 'reports/docker-build-output.json'))
FLAT_FAILURE_CODES = frozenset(('alias_cycle', 'alias_depth', 'archive_bound', 'archive_deadline', 'archive_digest', 'archive_read_bound', 'archive_source', 'archive_source_changed', 'archive_truncated', 'container_receipt', 'directory_changed', 'directory_closed_changed', 'directory_component', 'directory_type', 'elf_dynamic_after_null', 'elf_dynamic_bound', 'elf_dynamic_inventory', 'elf_dynamic_section_required', 'elf_dynamic_shape', 'elf_dynamic_string_binding', 'elf_dynamic_symbol_binding', 'elf_dynamic_symbols_required', 'elf_encoding', 'elf_header', 'elf_header_bound', 'elf_identity', 'elf_inventory_count', 'elf_needed_name', 'elf_null_section', 'elf_program_bound', 'elf_section_bound', 'elf_section_inventory', 'elf_static_string_bound', 'elf_static_symbol_bound', 'elf_string_bound', 'elf_string_encoding', 'elf_symbol_bound', 'elf_symbol_inventory', 'flat_export_bound', 'flat_export_changed', 'flat_export_client', 'flat_export_close', 'flat_export_container', 'flat_export_contract', 'flat_export_metadata', 'flat_export_not_frozen', 'flat_export_pipe_close', 'flat_export_readback', 'flat_export_receipt', 'flat_export_scope', 'flat_export_stage', 'flat_export_stderr_bound', 'flat_export_stdout_bound', 'flat_export_timeout', 'flat_export_unarmed', 'flat_export_write', 'image_root', 'image_root_changed', 'image_root_closed_changed', 'inventory_byte_bound', 'inventory_changed', 'inventory_closed_changed', 'inventory_count_bound', 'inventory_depth', 'inventory_name_bound', 'inventory_timeout', 'isolated_container_root_required', 'metadata_bound', 'metadata_changed', 'metadata_closed_changed', 'metadata_duplicate', 'metadata_file', 'metadata_grew', 'metadata_nonfinite', 'metadata_open_changed', 'metadata_path', 'metadata_short', 'package_count', 'package_duplicate', 'package_identity', 'package_list_count', 'package_list_inventory', 'package_list_missing_or_ambiguous', 'package_list_total', 'package_list_type', 'package_path_total', 'package_source', 'package_status_bound', 'package_status_shape', 'report_bound', 'sbom_component', 'sbom_duplicate', 'sbom_layer', 'sbom_package_type', 'sbom_properties', 'sbom_purl', 'sbom_shape', 'sbom_source', 'sealed_proof', 'sealed_proof_changed', 'sealed_proof_closed_changed', 'sealed_source_path', 'source_identity', 'special_file_count', 'symlink_bound', 'tar_checksum', 'tar_count', 'tar_device', 'tar_duplicate', 'tar_file_count', 'tar_format', 'tar_hardlink', 'tar_header', 'tar_length', 'tar_link', 'tar_name', 'tar_names_bound', 'tar_number', 'tar_padding', 'tar_path', 'tar_pax', 'tar_pax_bound', 'tar_pax_duplicate', 'tar_pax_key', 'tar_pax_time', 'tar_root', 'tar_shadow', 'tar_special_payload', 'tar_tail', 'tar_type', 'tar_whiteout', 'trusted_parser_interface'))
PHASE = 'source_admission'
INVENTORY_STARTED=None
CODES = frozenset(('intake_unarmed','payload_expectation_missing','actual_archive_observation_missing','intake_shape','evidence_shape','evidence_identity','evidence_signature',
    'evidence_reports','export_shape','export_identity','export_binding','archive_observation','canonical_labels',
    'capsule_binding','presence_identity','presence_mounts','presence_exit','smoke_receipt','source_admission','hosted_ci_only',
    'owned_cleanup_unproven','cleanup_report_unproven','outer_keeper_required','inventory_receipt','inventory_input','inventory_bounds','unexpected_failure',
    'inventory_cli_stage','inventory_cli_timeout','inventory_cli_stdout_bound','inventory_cli_stderr_bound','inventory_cli_nonzero','inventory_cli_pipe_close'))

# Genuine new6de source/export/SBOM intake. Independent Root physical measurement
# supplied the exact75member/35legacy observation; hosted graph admission still
# repeats it before disposable LOAD. No oldba938 observation is reused.
CONTROL = {'schema': 'canonical_app_smoke_intake/1', 'source_commit': '1e73f135f87ce37e23ad3e91c1e4c8ba21d4bf55', 'source_tree': 'f4b595abae928fe7a1daa666dc57bd1e41b91beb', 'input_signature': 'b0322182cb5af4f9814eaa0cafa6d07bde759e74254f2f58a9577024a372b5b4', 'image_id': 'sha256:6de16645515f11b041315fafe832025a588f6a131d3cce98b91ebc3fd61e7b04', 'rootfs': {'Layers': ['sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7', 'sha256:535a5d38abecfac4fa49da33fa942b53c53c463bd0d39a860967e46eb03faafe', 'sha256:6972161a9a77e7c1afb997daac15abc54f5b70c7b40b814601f53e038d8c398c', 'sha256:d86e9bd004831a6069b9a02e5caac80b73526315f52719079b4017b099956ed2', 'sha256:850267039a06998f3acf1de36a3a8eb147e4fdb309b1045f99e385530894ef37', 'sha256:83d1f70ea0cc9063a5b89409a1216cc4ed2b4c23642f8f26c2a7b1bf02885b0a', 'sha256:2a8d9e4d2d6172a92552ce2e94bf178ee12b8268aafebe20669abc4557212fc5', 'sha256:416d91c21246e3372967bbadaabc0d05587fb46bd9bec00b2b54a9b37222f132', 'sha256:78da3d988cb1cd3b909d887cdd427e496e60c133921b8906fcb73e1598a0e5a2', 'sha256:c7cb8cdd1c5e7fed7afdd8a9e95221eb8b9715b1f0a48e3e5f3c808e000784df', 'sha256:d2b5c843d5e66c24c51b9b8fc7f9c7ab5691c2fb0ce14d47dbb7271dcb744242', 'sha256:616b27ee4ec1f667498e86b512ecd0a83ea1bdcacff4c68fe4dbd0b31b68ec05', 'sha256:1bfd6bda6d89202ccff5434876fb073be4d85d568222c22626fd03faa1e91902', 'sha256:51c77d8ffaea813a847a55462fad55299e0e0b4d7f3a6caceac8686396c5f44f', 'sha256:63787b9972173c536d429bf9b8f2ecb896f04277729f6b1b7fcfffaaef6b7d2a', 'sha256:64fb558905db7dd209cc68748f30c0c6bc9793b9e74a037d1d66304d78fde761', 'sha256:2df337bd80d7a7652c915843d1ad30a4bf74b00278be7636e371b42f0adde840', 'sha256:fbc12d4fdfa9f422513cc0533c7d0d3f59d03a77c5f9af746175acd7398370dd', 'sha256:b652b91599e872241589666da6fb632eeb7c0cff90f6a7c8bd9084ec41b360b5', 'sha256:ea045eca718e42478dedf4d03f6006464523fae39798035d0fede9e51db6f5a8', 'sha256:b20cf9272c1c9a07eff3571a6d67cc13f679ae945e223d1f60173e6b0ee353d4', 'sha256:bc24d9933e75e88f068a5fd099de5791850e742464379682ae2b3e82c7b63311', 'sha256:0dbc683121ff7ab8bb4b4afb171dbd6e20d9ca7de283ea29cba3260fb9ccaedf', 'sha256:6544b8b5cce3f69c8ee9a47bc4359a8ab9d2f50fadd7ec5a2b9718405a26e978', 'sha256:f46c38ff43e627749a8b829b6237972dc8a0f87be6221e370f32ccb3f9be587b', 'sha256:cd185913a972b0234feccd4d7a02780e43cca92b60521d658fe36c5096dc78d5', 'sha256:1002611c3a120cd9156829299f340ead8d465ac31d48dad6dd0eee1d873402f9', 'sha256:9d1883017fd7f63c9a8ca06411d2cc0a8684e8d5c18c5312f5877d7f960d17e6', 'sha256:258e1ed517b7d2fc7e8a7ed890c637fea8d5132af625110318afa3652ce3d30d', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:96d5edc08fde5b2be03dd2ec39fbb4ac04bd536499ffcbc6eca7658d5242609b', 'sha256:1fb2dc4f3cc5e5a991e12c7bc52f6267209e93539778e41d9d1aadd80f1d677a', 'sha256:427efbd0d5a26c20f7eee6472666a27beb8c212eaec31db7a35aee9d1795b19f'], 'Type': 'layers'}, 'raw_counts': {'CRITICAL': 0, 'HIGH': 44, 'LOW': 82, 'MEDIUM': 68, 'UNKNOWN': 1}, 'run_id': 40446858880, 'run_attempt': 1, 'image_artifact_id': 12708334073, 'image_zip_bytes': 185715342, 'image_zip_sha256': '6f58ba8092666ba5caf658986c9e73efb2d07bedefa3d24407046a4ba1ad4a3f', 'evidence_artifact_id': 12709105562, 'evidence_zip_bytes': 356933, 'evidence_zip_sha256': '2732f7588a588c65aa76b2163d2765fe0ffde1d608bb35c95ee48c414b7dc623', 'archive_observation': {'archive_bytes': 458605568, 'archive_sha256': 'dc839e48eb81654bdaa93a5ad9f7dcb5b0c4d236e61fa81275c6e0a3c54e1fdf', 'expanded_layer_bytes': 458516992, 'member_count': 75, 'manifest_id': 'sha256:80f1e2ee2527c6c5b99643958d946b825afe7b6efc57125cb25e43ef69f38aba', 'index_sha256': 'cfa9236cfccb6e27715adf2ddbeb16d02bf3a0d2283f173157f864157f7ac402', 'legacy_compatibility_configs': 35}}


def need(ok, code):
    if not ok: raise RuntimeError(code)


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)+'\n').encode()


def document(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_JSON, 'evidence_shape')
    def unique(rows):
        out = {}
        for key, value in rows:
            need(key not in out, 'evidence_shape'); out[key] = value
        return out
    def finite(value):
        number = float(value); need(math.isfinite(number), 'evidence_shape'); return number
    return json.loads(raw, object_pairs_hook=unique, parse_float=finite,
        parse_constant=lambda _: need(False, 'evidence_shape'))


def same(a, b):
    if type(a) is not type(b): return False
    if type(a) is dict: return set(a) == set(b) and all(same(a[k], b[k]) for k in a)
    if type(a) is list: return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


ARMED = True
NATIVE_PAYLOAD_PROBE_SHA256 = '078e412950039b0e2055597022f89eab001e63e69eb1b7137e56c12e44d66eae'
NATIVE_PAYLOAD_EXPECTATION_SHA256 = '2fb66fafc72ed0733546506ffd4554ddfde9643122b87aecd205adce271310d9'

def validate_control(value):
    need(ARMED is True, 'intake_unarmed')
    need(all(type(pin) is str and re.fullmatch('[a-f0-9]{64}', pin) for pin in (NATIVE_PAYLOAD_PROBE_SHA256, NATIVE_PAYLOAD_EXPECTATION_SHA256)), 'payload_expectation_missing')
    need(type(value) is dict and set(value) == set(CONTROL), 'intake_shape')
    for key in set(CONTROL)-{'archive_observation'}:
        need(same(value[key], CONTROL[key]), 'intake_shape')
    observed = value['archive_observation']
    need(type(observed) is dict, 'actual_archive_observation_missing')
    need(set(observed) == {'archive_bytes','archive_sha256','expanded_layer_bytes','member_count','manifest_id','index_sha256','legacy_compatibility_configs'}
        and all(type(observed[k]) is int and 0 < observed[k] <= MAX_ARCHIVE for k in ('archive_bytes','expanded_layer_bytes'))
        and observed['archive_bytes'] % 512 == 0 and type(observed['member_count']) is int and 0 < observed['member_count'] < 135
        and type(observed['legacy_compatibility_configs']) is int and observed['legacy_compatibility_configs'] in (0, len(value['rootfs']['Layers']))
        and all(type(observed[k]) is str and re.fullmatch('[a-f0-9]{64}', observed[k]) for k in ('archive_sha256','index_sha256'))
        and type(observed['manifest_id']) is str and re.fullmatch('sha256:[a-f0-9]{64}', observed['manifest_id']), 'intake_shape')
    need(same(observed,CONTROL['archive_observation']), 'intake_shape')
    return value


def hosted(root):
    # Guard before any source read/compile. The frozen Controller repeats the
    # complete hosted root, identity and owner admission independently.
    need(os.geteuid() == 1001 and os.environ.get('GITHUB_ACTIONS') == 'true'
        and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
        and os.environ.get('GITHUB_REPOSITORY') == 'mohsenbarari/trading_bot'
        and os.environ.get('GITHUB_REF') == 'refs/heads/security/remediation-20261023', 'hosted_ci_only')
    parent = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    need(root.is_absolute() and root.resolve(strict=True) == root and root.parent == parent
        and root.name.startswith('security-app-ci-smoke-') and root.stat().st_uid == 1001
        and stat.S_IMODE(root.stat().st_mode) == 0o700 and not parent.stat().st_mode & 0o022, 'hosted_ci_only')


def source_module(root, stack, name, pin):
    path = root/'source'/name
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC); stack.callback(os.close, fd)
    identity = lambda s: (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    before = identity(os.fstat(fd))
    need(before[2:6] == (0o100600, os.geteuid(), os.getegid(), 1) and 0 < before[6] <= 131072, 'source_admission')
    raw = os.pread(fd, before[6]+1, 0)
    need(len(raw) == before[6] and sha256(raw).hexdigest() == pin, 'source_admission')
    def check():
        need(before == identity(os.fstat(fd)) == identity(path.lstat()) and os.pread(fd, len(raw)+1, 0) == raw, 'source_admission')
    check(); module = ModuleType('_canonical_smoke_'+name[:-3]); module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module, check


def canonical_legacy_configs(config, moby, contract):
    """Exact frozen D legacy serializer, with its sole layer limit raised to128.

    Preserve optional historical metadata and ordered repeated DiffIDs. This
    pure serializer does not alter an OCI config, TAR, daemon or old module.
    """
    required = {'architecture', 'created', 'history', 'os', 'rootfs', 'config'}
    need(type(config) is dict and required <= set(config) <= required | {'comment', 'container', 'container_config', 'docker_version', 'author'}
         and config['architecture'] == 'amd64' and config['os'] == 'linux' and type(config['history']) is list, 'legacy_image')
    created = config['created']
    need(type(created) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z', created), 'legacy_created')
    if '.' in created:
        head, fraction = created[:-1].split('.'); fraction = fraction.rstrip('0')
        created = head + ('.' + fraction if fraction else '') + 'Z'
    need(type(config['rootfs']) is dict and set(config['rootfs']) == {'type', 'diff_ids'}
         and config['rootfs']['type'] == 'layers' and type(config['rootfs']['diff_ids']) is list
         and 0 < len(config['rootfs']['diff_ids']) <= 128, 'legacy_rootfs')
    final = {}
    for key in ('comment',):
        need(type(config.get(key, '')) is str, 'legacy_metadata')
        if config.get(key): final[key] = config[key]
    final.update({'created': created})
    for key in ('container',):
        need(type(config.get(key, '')) is str, 'legacy_metadata')
        if config.get(key): final[key] = config[key]
    final['container_config'] = contract.container_config(config.get('container_config'))
    for key in ('docker_version', 'author'):
        need(type(config.get(key, '')) is str, 'legacy_metadata')
        if config.get(key): final[key] = config[key]
    final.update({'config': contract.container_config(config['config']), 'architecture': 'amd64', 'os': 'linux'})
    expected, chain, parent = {}, None, None
    for index, diff in enumerate(config['rootfs']['diff_ids']):
        need(type(diff) is str and contract.DIGEST.fullmatch(diff), 'legacy_rootfs')
        chain = diff if chain is None else 'sha256:' + sha256((chain + ' ' + diff).encode()).hexdigest()
        value = final if index == len(config['rootfs']['diff_ids']) - 1 else {'created': '1970-01-01T00:00:00Z', 'container_config': contract.container_config()}
        identifying = {**value, 'layer_id': chain}
        if parent is not None: identifying['parent'] = 'sha256:' + parent
        raw = b'{' + b','.join(moby.moby_go_json(k) + b':' + moby.moby_go_json(identifying[k]) for k in sorted(identifying)) + b'}'
        identifier = sha256(raw).hexdigest(); saved = {'id': identifier}
        if parent is not None: saved['parent'] = parent
        saved.update(value); saved['os'] = 'linux'; raw = moby.moby_go_json(saved)
        need(len(raw) <= 65536, 'legacy_bound')
        expected['blobs/sha256/' + sha256(raw).hexdigest()] = raw; parent = identifier
    need(len(expected) == len(config['rootfs']['diff_ids']), 'legacy_collision')
    return expected


def admit_evidence(raw_files, control, builder):
    need(set(raw_files) == EVIDENCE_NAMES and all(type(v) is bytes and 0 < len(v) <= MAX_JSON for v in raw_files.values()), 'evidence_shape')
    data = {name: document(raw) for name, raw in raw_files.items()}
    source, inputs, build = (data[name] for name in ('source-identity.json','input-manifest.json','image-build.json'))
    for row in (source, inputs, build):
        need(row.get('source_commit') == control['source_commit'] and row.get('source_tree') == control['source_tree']
            and row.get('security_accepted') is False, 'evidence_identity')
    need(source.get('schema') == 'canonical_app_source/1' and source.get('run_id') == str(control['run_id'])
        and source.get('run_attempt') == str(control['run_attempt']) and source.get('repo') and type(source.get('files')) is list, 'evidence_identity')
    need(build.get('schema') == 'canonical_app_image_build/1' and build.get('image_id') == control['image_id']
        and same(build.get('rootfs'), control['rootfs']) and build.get('run_id') == str(control['run_id'])
        and build.get('run_attempt') == str(control['run_attempt']) and build.get('platform') == 'linux/amd64'
        and build.get('activation_performed') is False and build.get('market_roles_built') is False, 'evidence_identity')
    rows = inputs.get('files'); empty = inputs.get('empty_directories')
    need(inputs.get('schema') == 'canonical_app_inputs/1' and type(rows) is list and 0 < len(rows) <= 20000
        and all(type(r) is dict and type(r.get('path')) is str and type(r.get('sha256')) is str
                and re.fullmatch('[a-f0-9]{64}',r['sha256']) and type(r.get('bytes')) is int and r['bytes'] >= 0 for r in rows)
        and len({r['path'] for r in rows}) == len(rows) and type(empty) is list and all(type(x) is str and x in builder.INPUTS for x in empty), 'evidence_signature')
    need(builder.foreign_signature(rows, empty) == inputs.get('input_signature') == build.get('input_signature') == control['input_signature'], 'evidence_signature')
    scan, sbom = data['reports/vulnerabilities.json'], data['reports/sbom.cdx.json']
    component = sbom.get('metadata',{}).get('component',{})
    need(scan.get('Metadata',{}).get('ImageID') == control['image_id'] and component.get('type') == 'container'
        and component.get('name') == control['image_id'] and [p.get('value') for p in component.get('properties',[]) if p.get('name') == 'aquasecurity:trivy:ImageID'] == [control['image_id']], 'evidence_reports')
    counts = {key:0 for key in control['raw_counts']}
    need(type(scan.get('Results')) is list, 'evidence_reports')
    for row in scan['Results']:
        need(type(row) is dict and (row.get('Vulnerabilities') is None or type(row['Vulnerabilities']) is list), 'evidence_reports')
        for finding in row.get('Vulnerabilities') or []:
            need(type(finding) is dict and finding.get('Severity') in counts, 'evidence_reports'); counts[finding['Severity']] += 1
    need(same(counts, control['raw_counts']) and data['finalize.json'] == {'schema':'canonical_app_finalize/1','status':'exported_not_accepted','security_accepted':False}, 'evidence_reports')
    log=data['reports/docker-build-output.json']
    need(type(log)is dict and log.get('schema')=='canonical_app_docker_build_output/2'
        and type(log.get('actual_command_exit'))is int and log['actual_command_exit']==0
        and log.get('combined_stdout_stderr')is True and log.get('build_accepted')is False
        and log.get('security_accepted')is False,'evidence_reports')
    return {name:{'bytes':len(raw),'sha256':sha256(raw).hexdigest()} for name,raw in raw_files.items()}


def read_zip(stream, names, maximum=MAX_JSON):
    with zipfile.ZipFile(stream) as archive:
        rows = archive.infolist()
        need(len(rows) == len(names) and {r.filename for r in rows} == set(names)
            and all(not r.is_dir() and not r.flag_bits & 1 and stat.S_IFMT(r.external_attr >> 16) in (0,stat.S_IFREG)
                and 0 < r.file_size <= maximum for r in rows), 'evidence_shape')
        return {r.filename:archive.read(r) for r in rows}


def export_record(value, control, evidence):
    need(type(value) is dict and value.get('schema') == 'canonical_app_image_export/1' and value.get('status') == 'exported_not_accepted'
        and value.get('image_id') == value.get('image_config_id') == control['image_id'] and same(value.get('rootfs'),control['rootfs'])
        and value.get('source_commit') == control['source_commit'] and value.get('source_tree') == control['source_tree']
        and value.get('run_id') == str(control['run_id']) and value.get('run_attempt') == str(control['run_attempt'])
        and value.get('input_signature') == control['input_signature'] and value.get('platform') == 'linux/amd64'
        and value.get('full_config_compared_in_ram') is True and value.get('physical_archive_admission_complete') is False
        and value.get('signatures_verified') is False and value.get('activation_performed') is False and value.get('security_accepted') is False
        and same(value.get('raw_counts'),control['raw_counts']), 'export_identity')
    need(same(value.get('inputs'),{name:evidence[name] for name in ('source-identity.json','input-manifest.json','image-build.json')})
        and same(value.get('reports'),{name:evidence['reports/'+name] for name in ('vulnerabilities.json','sbom.cdx.json')}), 'export_binding')
    archive = value.get('archive'); observed = control['archive_observation']
    need(type(archive) is dict and set(archive) == {'format','bytes','sha256','uncompressed_bytes','uncompressed_sha256'}
        and archive['format'] == 'docker-archive+gzip' and type(archive['bytes']) is int and 0 < archive['bytes'] <= MAX_ARCHIVE
        and type(archive['sha256']) is str and re.fullmatch('[a-f0-9]{64}',archive['sha256'])
        and archive['uncompressed_bytes'] == observed['archive_bytes'] and type(archive['uncompressed_bytes']) is int
        and archive['uncompressed_sha256'] == observed['archive_sha256'], 'export_shape')
    return archive


def archive_config(source, size, ctx, control):
    reader = ctx.T.ArchiveReader(source, size, min(ctx.end,time.monotonic()+599))
    members, _ = ctx.T.inventory(reader)
    def raw(name):
        need(name in members and 0 < members[name][1] <= MAX_JSON, 'archive_observation')
        offset,length = members[name]; reader.seek(offset); return reader.read(length)
    manifest = document(raw('manifest.json'))
    need(type(manifest) is list and len(manifest) == 1 and type(manifest[0]) is dict, 'archive_observation')
    config_raw = raw(manifest[0]['Config']); config = document(config_raw)
    need('sha256:'+sha256(config_raw).hexdigest() == control['image_id'] and same(config.get('rootfs'),{'type':'layers','diff_ids':control['rootfs']['Layers']}), 'archive_observation')
    labels = config.get('config',{}).get('Labels',{})
    need(all(labels.get(k) == v for k,v in {'org.opencontainers.image.revision':control['source_commit'],
        'io.gold-trade.release.tree':control['source_tree'],'io.gold-trade.release.input-signature':control['input_signature']}.items()), 'canonical_labels')
    return config


def validate_archive(source, size, ctx, control):
    config = archive_config(source,size,ctx,control)
    data = {'spec':{'profile':'canonical_app','config_id':control['image_id'],'layers':control['rootfs']['Layers'],
        'archive':{'uncompressed_bytes':size}},'image':{'Config':config['config'],'Comment':ctx.D.expected_inspect_comment(config),
            'Author':config.get('author',''),'DockerVersion':config.get('docker_version','')}}
    source.seek(0); proof = ctx.D.validate_archive(source,size,ctx.end,data,ctx.T,ctx.L)
    need(all(same(proof.get(k),v) for k,v in control['archive_observation'].items()),'archive_observation')
    return config, proof


def admit_app(ctx, old, control, evidence):
    global PHASE
    PHASE = 'canonical_artifact_download'; folder = ctx.root/'canonical-image'; folder.mkdir(mode=0o700)
    path = folder/'image.zip'; ctx.download(control['image_artifact_id'],path,control['image_zip_bytes'],control['image_zip_sha256'])
    with ctx.C.pinned(path,control['image_zip_bytes']) as (stream,size):
        need(size == control['image_zip_bytes'] and ctx.C.stream_digest(stream,size) == control['image_zip_sha256'], 'export_identity')
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            rows = archive.infolist()
            need(len(rows) == 2 and {r.filename for r in rows} == {'image-export.json','candidate-image.tar.gz'}
                and all(not r.is_dir() and not r.flag_bits & 1 and stat.S_IFMT(r.external_attr >> 16) in (0,stat.S_IFREG) for r in rows), 'export_shape')
            info = archive.getinfo('image-export.json'); need(0 < info.file_size <= 65536, 'export_shape')
            export_raw = archive.read(info); exported = document(export_raw); gz = export_record(exported,control,evidence)
            info = archive.getinfo('candidate-image.tar.gz'); need(info.file_size == gz['bytes'], 'export_shape')
            ctx.capacity(gz['bytes']+gz['uncompressed_bytes']+MAX_JSON)
            target = folder/'candidate-image.tar.gz'; total = 0; digest = sha256()
            with archive.open(info) as incoming:
                fd = os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'wb') as outgoing:
                    while block := incoming.read(min(65536,gz['bytes']+1-total)):
                        total += len(block); need(total <= gz['bytes'],'export_shape'); ctx.check(); outgoing.write(block); digest.update(block)
                    need(total == gz['bytes'] and digest.hexdigest() == gz['sha256'],'export_identity'); outgoing.flush(); os.fsync(outgoing.fileno())
    tar = folder/'candidate-image.tar'
    with ctx.C.pinned(target,gz['bytes']) as (source,size): ctx.P.decode_gzip(source,size,tar,gz['uncompressed_bytes'])
    PHASE = 'canonical_physical_archive_admission'
    source,size = ctx.stack.enter_context(ctx.C.pinned(tar,gz['uncompressed_bytes']))
    need(size == gz['uncompressed_bytes'],'archive_observation')
    config, proof = validate_archive(source,size,ctx,control)
    ctx.write(ctx.root/'reports/canonical-archive-admission.json',proof)
    ctx.write(ctx.root/'reports/authenticated-export.json',exported)
    PHASE = 'canonical_image_load'
    image, receipt = old.load_exact(ctx,source,control['image_id'],config['config'],control['rootfs']['Layers'],proof['source_identities'])
    ctx.write(ctx.root/'reports/canonical-daemon-admission.json',receipt)
    ctx.check()  # Stack retains this TAR through presence, smoke and cleanup.
    return image


def bind_capsule(capsule, old_image, image):
    before = "'image_config_id':'"+old_image+"'"; after = "'image_config_id':'"+image+"'"
    need(type(capsule) is str and capsule.count(before) == 1 and before != after and capsule.count(after) == 0,'capsule_binding')
    bound = capsule.replace(before,after,1)
    need(bound.replace(after,before,1) == capsule,'capsule_binding'); return bound


def validate_result(value, image, old):
    old.public(value)
    need(type(value) is dict and all(type(value.get(k)) is dict for k in ('runtime','components','migration')),'smoke_receipt')
    need(value.get('schema') == 'app_image_ci_smoke/1' and value.get('status') == 'passed' and value.get('image_config_id') == image
        and same(value['runtime'],{'expected':8,'run':8,'passed':8,'failures':[]})
        and same(value['components'],{'expected':24,'run':24,'passed':24,'failures':[],'errors':0,'skipped':0})
        and same(value['migration'],{'invocation_attempted':True,'child_completed':True,'return_code':0})
        and value.get('runtime_cleanup_failures') == [] and value.get('production_config_guards_active') is True
        and value.get('app_source_masked') is False and value.get('real_provider_used') is False
        and value.get('security_accepted') is False and value.get('full_matrix_accepted') is False,'smoke_receipt')
    return value


def presence_args(old, ctx, name, image, proof, filename, pin, control):
    command = ['/usr/local/bin/python3','-I','-B','/proof/'+filename,'--image-id',image,
        '--source-commit',control['source_commit'],'--verifier-sha256',pin]
    return ['create','--name',name,'--label',old.LABEL+'='+old.owner(),'--pull=never','--network=none','--read-only',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--user=0:0','--memory=512m','--memory-swap=512m',
        '--cpus=1','--pids-limit=64','--ulimit','core=0','--ulimit','fsize=2097153:2097153',
        '--mount','type=bind,source='+str(proof)+',target=/proof,readonly','--entrypoint','/usr/local/bin/python3',image,*command[1:]]


def presence(ctx, old, image, control):
    global PHASE
    PHASE = 'fresh_same_image_presence'; proof = ctx.root/'presence-proof'; proof.mkdir(mode=0o755); proof.chmod(0o755)
    for filename,(pin,_,_) in VERIFIERS.items():
        raw, check = old.sealed(ctx.stack,ctx.root/'source'/filename,pin,131072); ctx.checks.append(check)
        ctx.C.write(proof/filename,raw,0o444)
    for index,(filename,(pin,schema,maximum)) in enumerate(VERIFIERS.items()):
        name = 'app-ci-presence-'+old.owner()+('' if index == 0 else '-acl')
        # Names are explicit and cleanup admits exact owner/name/image before RPC.
        ctx.created.append((name,image)); cid = ctx.docker(presence_args(old,ctx,name,image,proof,filename,pin,control),maximum=256).decode().strip()
        need(re.fullmatch('[a-f0-9]{64}',cid),'presence_identity')
        mounts = old.document(ctx.docker(['container','inspect','--format','{{json .Mounts}}',cid],maximum=8192))
        need(type(mounts) is list and len(mounts) == 1 and mounts[0].get('Type') == 'bind' and mounts[0].get('Source') == str(proof)
            and mounts[0].get('Destination') == '/proof' and mounts[0].get('RW') is False,'presence_mounts')
        raw = ctx.docker(['start','--attach',cid],seconds=90,maximum=maximum)
        need(ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip() == b'0','presence_exit')
        value = document(raw)
        need(value.get('schema') == schema and value.get('image_id') == image and value.get('source_commit') == control['source_commit']
            and value.get('verifier_sha256') == pin and value.get('security_accepted') is False
            and value.get('production_mounts_or_data_used') is False and value.get('scanner_findings_modified') is False,'presence_identity')
        ctx.write(ctx.root/'reports'/('fresh-'+filename[:-3]+'.json'),value)


def native_payload(ctx, old, image, control):
    """New same-image payload proof; does not alter the32 smoke assertions."""
    global PHASE
    PHASE = 'fresh_same_image_native_payload'
    script, script_check = old.sealed(ctx.stack,ctx.root/'source/probe_native_payload.py',NATIVE_PAYLOAD_PROBE_SHA256,131072)
    expectation, expectation_check = old.sealed(ctx.stack,ctx.root/'source/native-expectation.json',NATIVE_PAYLOAD_EXPECTATION_SHA256,MAX_NATIVE_REPORT)
    ctx.checks.extend((script_check,expectation_check))
    expected = document(expectation)
    need(expected.get('schema') == 'image_native_payload_expectation/1' and expected.get('armed') is True
        and expected.get('missing_package_sources') == [] and expected.get('security_accepted') is False,'payload_expectation_missing')
    proof = ctx.root/'native-proof'; proof.mkdir(mode=0o755); proof.chmod(0o755)
    ctx.C.write(proof/'probe_native_payload.py',script,0o444)
    ctx.C.write(proof/'native-expectation.json',expectation,0o444)
    name = 'app-ci-native-payload-'+old.owner()
    argv = presence_args(old,ctx,name,image,proof,'probe_native_payload.py',NATIVE_PAYLOAD_PROBE_SHA256,control)
    argv += ['--collect-expected-native-payload','--expectation-sha256',NATIVE_PAYLOAD_EXPECTATION_SHA256,
             '--expectation-gid',str((proof/'native-expectation.json').lstat().st_gid)]
    ctx.created.append((name,image)); cid = ctx.docker(argv,maximum=256).decode().strip()
    need(re.fullmatch('[a-f0-9]{64}',cid),'presence_identity')
    mounts = old.document(ctx.docker(['container','inspect','--format','{{json .Mounts}}',cid],maximum=8192))
    need(type(mounts) is list and len(mounts) == 1 and mounts[0].get('Type') == 'bind' and mounts[0].get('Source') == str(proof)
        and mounts[0].get('Destination') == '/proof' and mounts[0].get('RW') is False,'presence_mounts')
    raw = ctx.docker(['start','--attach',cid],seconds=90,maximum=MAX_NATIVE_REPORT)
    need(ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip() == b'0','presence_exit')
    value = document(raw)
    expected_scope = {key:control[key] for key in ('image_id','source_commit','source_tree','input_signature')}
    expected_scope.update(platform='linux/amd64',build_run_id=control['run_id'],build_run_attempt=control['run_attempt'])
    need(value.get('schema') == 'image_native_payload_observation/1' and same(value.get('scope'),expected_scope)
        and value.get('verifier_sha256') == NATIVE_PAYLOAD_PROBE_SHA256 and value.get('expectation_sha256') == NATIVE_PAYLOAD_EXPECTATION_SHA256
        and value.get('inventory_complete') is True and value.get('production_mounts_or_data_used') is False
        and value.get('scanner_findings_modified') is False and value.get('security_accepted') is False,'presence_identity')
    ctx.write(ctx.root/'reports/fresh-native-payload.json',value)


def inventory_document(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_ACL_REPORT,'inventory_bounds')
    def unique(pairs):
        result = {}
        for key,value in pairs:
            need(key not in result,'inventory_receipt'); result[key] = value
        return result
    def finite(value):
        result = float(value); need(math.isfinite(result),'inventory_receipt'); return result
    return json.loads(raw,object_pairs_hook=unique,parse_float=finite,
        parse_constant=lambda _:need(False,'inventory_receipt'))


def inventory_args(old, name, image, proof, gid, control):
    return ['create','--name',name,'--label',old.LABEL+'='+old.owner(),'--pull=never',
        '--network=none','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--user=0:0','--memory=512m','--memory-swap=512m','--cpus=1','--pids-limit=64',
        '--ulimit','core=0','--mount','type=bind,source='+str(proof)+',target=/proof,readonly',
        '--entrypoint','/usr/local/bin/python3',image,'-I','-S','-B','/proof/inventory.py',
        '--collect-isolated-public-image','--image-id',control['image_id'],
        '--source-commit',control['source_commit'],'--verifier-sha256',INVENTORY_SHA256,
        '--proof-uid','1001','--proof-gid',str(gid)]


def inventory_container(value, old, name, image, proof):
    need(type(value) is dict and value.get('name') == '/'+name and value.get('image') == image
        and value.get('owner') == old.owner() and value.get('readonly') is True
        and value.get('privileged') is False and value.get('user') == '0:0'
        and value.get('network') == 'none' and type(value.get('networks')) is dict
        and set(value['networks']) <= {'none'}
        and value.get('caps_add') in (None,[]) and value.get('caps_drop') == ['ALL']
        and value.get('security') == ['no-new-privileges'] and value.get('memory') == 512*1024**2
        and value.get('memory_swap') == 512*1024**2 and value.get('nano_cpus') == 10**9
        and value.get('pids') == 64 and value.get('tmpfs') in (None,{})
        and value.get('running') is False and value.get('paused') is False
        and type(value.get('pid')) is int and value['pid'] == 0,'presence_identity')
    mounts=value.get('mounts')
    need(type(mounts) is list and len(mounts) == 1 and mounts[0].get('Type') == 'bind'
        and mounts[0].get('Source') == str(proof) and mounts[0].get('Destination') == '/proof'
        and mounts[0].get('RW') is False,'presence_mounts')
    return value


INVENTORY_STAGES=frozenset(('create','preinspect','start','exit','postinspect'))
INVENTORY_STAGE='none'
INVENTORY_STDERR=8192


def inventory_inspect_template(old):
    return '{"name":{{json .Name}},"image":{{json .Image}},"owner":{{json (index .Config.Labels "'+old.LABEL+'")}},"user":{{json .Config.User}},"readonly":{{json .HostConfig.ReadonlyRootfs}},"privileged":{{json .HostConfig.Privileged}},"network":{{json .HostConfig.NetworkMode}},"networks":{{json .NetworkSettings.Networks}},"caps_add":{{json .HostConfig.CapAdd}},"caps_drop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},"memory":{{json .HostConfig.Memory}},"memory_swap":{{json .HostConfig.MemorySwap}},"nano_cpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"tmpfs":{{json (index .HostConfig "Tmpfs")}},"mounts":{{json .Mounts}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"pid":{{json .State.Pid}}}'


def inventory_cli(ctx,old,stage,*,image,proof,gid,control,cid=None):
    """Only five fixed public Docker commands, with distinct bounded streams."""
    global INVENTORY_STAGE
    need(stage in INVENTORY_STAGES and old.DOCKER==['/usr/bin/docker','--host=unix:///var/run/docker.sock']
        and proof==ctx.root/'whole-acl-proof' and type(gid)is int and 0<=gid<=65535
        and type(image)is str and re.fullmatch('sha256:[a-f0-9]{64}',image),'inventory_cli_stage')
    name='app-ci-whole-acl-'+old.owner()
    if stage=='create':
        need(cid is None,'inventory_cli_stage');argv=inventory_args(old,name,image,proof,gid,control);maximum,seconds=256,30
    else:
        need(type(cid)is str and re.fullmatch('[a-f0-9]{64}',cid),'inventory_cli_stage')
        if stage in ('preinspect','postinspect'):
            argv=['container','inspect','--format',inventory_inspect_template(old),cid];maximum,seconds=16384,30
        elif stage=='start':argv=['start','--attach',cid];maximum,seconds=MAX_ACL_REPORT,150
        else:argv=['inspect','--format','{{.State.ExitCode}}',cid];maximum,seconds=64,30
    INVENTORY_STAGE=stage;ctx.check()
    end=min(time.monotonic()+seconds,ctx.cleanup_end or ctx.end)
    environment={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','DOCKER_CONFIG':str(ctx.root/'docker-config')}
    child=subprocess.Popen([*old.DOCKER,*argv],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env=environment,start_new_session=True,close_fds=True)
    buffers={'stdout':bytearray(),'stderr':bytearray()};counts={'stdout':0,'stderr':0}
    try:
        with selectors.DefaultSelector()as selector:
            for key,pipe in (('stdout',child.stdout),('stderr',child.stderr)):
                os.set_blocking(pipe.fileno(),False);selector.register(pipe.fileno(),selectors.EVENT_READ,key)
            while selector.get_map():
                ctx.check();left=end-time.monotonic();need(left>0,'inventory_cli_timeout')
                for event,_ in selector.select(min(.1,left)):
                    key=event.data;limit=maximum if key=='stdout'else INVENTORY_STDERR
                    block=os.read(event.fd,min(65536,limit-counts[key]+1))
                    if not block:selector.unregister(event.fd);continue
                    counts[key]+=len(block);buffers[key].extend(block[:max(0,limit-len(buffers[key]))])
                    need(counts[key]<=limit,'inventory_cli_stdout_bound'if key=='stdout'else'inventory_cli_stderr_bound')
        try:code=child.wait(timeout=max(.01,end-time.monotonic()))
        except subprocess.TimeoutExpired:raise RuntimeError('inventory_cli_timeout')from None
        ctx.check()
        need(type(code)is int and code==0,'inventory_cli_nonzero');return bytes(buffers['stdout'])
    finally:
        try:ctx.C.cleanup_owned_child(child)
        finally:
            close_error=False
            for pipe in (child.stdout,child.stderr):
                try:pipe.close()
                except BaseException:close_error=True
            actual=child.returncode
            if stage=='start':ctx.C.write(ctx.root/'reports/fresh-whole-image-acl-output.json',bytes(buffers['stdout']))
            ctx.write(ctx.root/'reports'/('inventory-cli-'+stage+'.json'),{
                'schema':'ba938_public_inventory_cli/1','stage':stage,
                'actual_client_returncode':actual if type(actual)is int else None,
                'stdout_bytes_observed':counts['stdout'],'stderr_bytes_observed':counts['stderr'],
                'stderr_text':bytes(buffers['stderr']).decode('utf-8',errors='replace'),
                'stderr_truncated':counts['stderr']>INVENTORY_STDERR,'security_accepted':False})
            ctx.check();need(not close_error,'inventory_cli_pipe_close')


def whole_image_inventory(ctx, old, image, control, evidence_zip):
    """Never-started SAME-image export; no extra caps, modes or omissions."""
    global PHASE,INVENTORY_STARTED
    PHASE='fresh_same_image_whole_acl_inventory';INVENTORY_STARTED=time.monotonic()
    I,check=source_module(ctx.root,ctx.stack,'inventory.py',INVENTORY_SHA256);ctx.checks.append(check)
    E,check=source_module(ctx.root,ctx.stack,'export_controller.py',EXPORT_CONTROLLER_SHA256);ctx.checks.append(check)
    A,check=source_module(ctx.root,ctx.stack,'archive_inventory.py',ARCHIVE_INVENTORY_SHA256);ctx.checks.append(check)
    with ctx.C.pinned(evidence_zip,control['evidence_zip_bytes'])as(stream,size):
        need(size==control['evidence_zip_bytes']and ctx.C.stream_digest(stream,size)==control['evidence_zip_sha256'],'inventory_input')
        stream.seek(0);sbom=read_zip(stream,EVIDENCE_NAMES)['reports/sbom.cdx.json']
    need(sha256(sbom).hexdigest()==SBOM_SHA256,'inventory_input')
    # The ordinary fixed8GiB CI reserve includes the worst-case bounded export
    # and report BEFORE Docker create or the first export-spool byte.
    ctx.capacity(E.MAX_EXPORT+E.MAX_REPORT)
    value=E.whole_image_inventory(ctx,old,image,{**control,'sbom_sha256':SBOM_SHA256},sbom,
        I=I,T=ctx.T,A=A,adapter_sha256=ARCHIVE_INVENTORY_SHA256)
    old.public(value);ctx.check();return value


def run_smoke(ctx, old, image, control):
    """The same public fixtures, dependencies and 32 assertions, on admitted CID."""
    global PHASE
    postgres = old.admit_dependency(ctx,'postgres'); redis = old.admit_dependency(ctx,'redis')
    database = 'coin_intelligence_ci_'+sha256(old.owner().encode()).hexdigest()[:12]
    proof = old.prepare_proof(ctx,database); ctx.network = 'app-ci-net-'+old.owner()
    PHASE = 'disposable_internal_network'
    ctx.docker(['network','create','--internal','--label',old.LABEL+'='+old.owner(),ctx.network],maximum=256)
    pg = old.create(ctx,'app-ci-pg-'+old.owner(),postgres,['postgres','-c','unix_socket_directories=/var/run/postgresql','-c','shared_buffers=64MB','-c','max_connections=20','-c','max_wal_size=128MB'],alias='pg',user='postgres',
        env={'POSTGRES_USER':'ci_public','POSTGRES_PASSWORD':'ci_public_fixture','POSTGRES_DB':database,'PGDATA':'/var/lib/postgresql/data/pgdata'},
        tmpfs=('/var/lib/postgresql/data:rw,nosuid,nodev,size=1g,mode=1777','/tmp:rw,nosuid,nodev,size=64m,mode=1777','/var/run/postgresql:rw,nosuid,nodev,size=16m,uid=70,gid=70,mode=0775'))
    rd = old.create(ctx,'app-ci-redis-'+old.owner(),redis,['redis-server','--save','','--appendonly','no'],alias='redis',user='1000:1000',
        tmpfs=('/data:rw,nosuid,nodev,size=128m,uid=1000,gid=1000','/tmp:rw,nosuid,nodev,size=16m,mode=1777'))
    ctx.docker(['start',pg,rd],maximum=512); PHASE = 'disposable_dependency_readiness'; deadline = time.monotonic()+90
    while True:
        try:
            profile = 'postgres'; old.subphase(profile,'readiness')
            ctx.docker(['exec',pg,'pg_isready','-h','127.0.0.1','-U','ci_public','-d',database],maximum=1024)
            profile = 'redis'; old.subphase(profile,'readiness')
            need(ctx.docker(['exec',rd,'redis-cli','ping'],maximum=64).strip() == b'PONG','redis_readiness'); break
        except RuntimeError as error:
            if time.monotonic() >= deadline:
                old.readiness_failure(ctx,{'postgres':pg,'redis':rd},profile,error); raise RuntimeError('dependency_readiness') from None
            time.sleep(1)
    PHASE = 'installed_application_smoke'
    capsule = bind_capsule(old.CAPSULE,old.APP['image_config_id'],control['image_id'])
    cid = old.create(ctx,'app-ci-api-'+old.owner(),image,['/usr/local/bin/python3','-I','-B','-c',capsule],
        env={'APP_ENV_FILE':'/proof/runtime.env',**old.fixture_env(database)},proof=proof,output_limit=True,
        tmpfs=('/tmp:rw,nosuid,nodev,noexec,size=128m,mode=1777',))
    mounts = old.document(ctx.docker(['container','inspect','--format','{"mounts":{{json .Mounts}},"tmpfs":{{json .HostConfig.Tmpfs}}}',cid],maximum=8192))
    ctx.write(ctx.root/'reports/app-mount-admission.json',old.validate_app_mounts(mounts,proof,ctx.M))
    client_ok = True
    try: ctx.docker(['start','--attach',cid],seconds=600,maximum=65536,public_log=ctx.root/'smoke-output.json')
    except RuntimeError: client_ok = False
    result = document(ctx.C.read(ctx.root/'smoke-output.json',65536)); ctx.write(ctx.root/'reports/actual-smoke.json',result)
    need(client_ok and ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip() == b'0','smoke_container_exit')
    return validate_result(result,control['image_id'],old)


def execute(ctx, old, builder, control):
    global PHASE
    PHASE = 'canonical_evidence_admission'; folder = ctx.root/'evidence'; folder.mkdir(mode=0o700); path = folder/'public-evidence.zip'
    ctx.download(control['evidence_artifact_id'],path,control['evidence_zip_bytes'],control['evidence_zip_sha256'])
    with ctx.C.pinned(path,control['evidence_zip_bytes']) as (source,size):
        need(size == control['evidence_zip_bytes'] and ctx.C.stream_digest(source,size) == control['evidence_zip_sha256'],'evidence_identity')
        source.seek(0); evidence = admit_evidence(read_zip(source,EVIDENCE_NAMES),control,builder)
    ctx.write(ctx.root/'reports/canonical-source-binding.json',{'image_source_commit':control['source_commit'],'image_source_tree':control['source_tree'],
        'controller_commit':os.environ['GITHUB_SHA'],'build_run_id':control['run_id'],'build_run_attempt':control['run_attempt'],
        'image_id':control['image_id'],'input_signature':control['input_signature'],'evidence_artifact_id':control['evidence_artifact_id'],
        'evidence_zip_sha256':control['evidence_zip_sha256'],'raw_counts':control['raw_counts'],'security_accepted':False,'full_matrix_accepted':False})
    image = admit_app(ctx,old,control,evidence)
    whole_image_inventory(ctx,old,image,control,path)
    presence(ctx,old,image,control); native_payload(ctx,old,image,control); return run_smoke(ctx,old,image,control)


def finish_cleanup(ctx, old, primary):
    try: errors = old.cleanup(ctx)
    except BaseException: errors = ['owned_cleanup_unproven']
    try:
        ctx.write(ctx.root/'reports/cleanup.json',{'owned_cleanup_complete':not errors,'closed_causes':errors,
            'primary_failure_retained':primary,'security_accepted':False})
    except BaseException:
        errors = sorted(set(errors)|{'cleanup_report_unproven'})
        if not primary: raise RuntimeError('cleanup_report_unproven') from None
    if errors and not primary: raise RuntimeError('owned_cleanup_unproven')
    return errors


def public_inventory_elapsed():
    if PHASE!='fresh_same_image_whole_acl_inventory'or type(INVENTORY_STARTED)not in(int,float):return None
    elapsed=time.monotonic()-INVENTORY_STARTED
    return round(elapsed,3)if math.isfinite(elapsed)and 0<=elapsed<=3600 else None


def public_failure_code(error, allowed):
    """Only finite reviewed source codes may become public failure metadata."""
    code=error.args[0]if isinstance(error,RuntimeError)and len(error.args)==1 and type(error.args[0])is str else None
    return code if code in allowed else 'unexpected_failure'


def main(argv=None, *, outer_keeper=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--execute-reviewed-6de-proof',action='store_true'); parser.add_argument('--confirm')
    args = parser.parse_args(argv); ctx = None; old = None; errors = []; primary = False
    if not args.execute_reviewed_6de_proof:
        print(json.dumps({'status':'UNARMED','collection_performed':False,'security_accepted':False,'full_matrix_accepted':False})); return 0
    need(args.confirm == CONFIRM,'intake_unarmed')
    try:
        validate_control(CONTROL); hosted(args.root)
        need(outer_keeper is not None and outer_keeper.root == args.root and not outer_keeper.closed,'outer_keeper_required')
        with ExitStack() as stack:
            old, check = source_module(args.root,stack,'run_app_image_ci_smoke.py',OLD_PIN)
            builder, builder_check = source_module(args.root,stack,'prepare_canonical_app_image.py',BUILDER_PIN)
            ctx = old.Controller(args.root,stack); ctx.checks.extend((check,builder_check))
            # Explicit private-instance dependency; no historical/shared module is changed.
            ctx.C.PROOF_KEEPER = outer_keeper; ctx.checks.append(outer_keeper.check)
            # This instance is fresh, private and never shared with old execution.
            ctx.D.legacy_configs = lambda config,moby: canonical_legacy_configs(config,moby,ctx.D)
            try: execute(ctx,old,builder,CONTROL)
            except BaseException: primary = True; raise
            finally:
                errors = finish_cleanup(ctx,old,primary)
            ctx.check()
        outer_keeper.check()  # Catch drift caused by the inner physical closes.
        print(json.dumps({'status':'canonical_image_smoke_passed','tests':32,'fresh_presence_reports':2,'fresh_native_payload_reports':1,'fresh_whole_image_acl_inventory_reports':1,'security_accepted':False,'full_matrix_accepted':False})); return 0
    except BaseException as error:
        code = error.args[0] if isinstance(error,RuntimeError) and len(error.args) == 1 and type(error.args[0]) is str else None
        allowed = set(CODES)|set(FLAT_FAILURE_CODES)
        if old is not None: allowed.update(old.FAILURE_CODES)
        if ctx is not None:
            for module in ctx.modules.values(): allowed.update(getattr(module,'PUBLIC_FAILURE_CODES',())); allowed.update(getattr(module,'PUBLIC_CODES',()))
        known = frozenset(('RuntimeError','Refusal','AssertionError','OSError','FileNotFoundError','PermissionError','TimeoutError',
            'KeyError','TypeError','ValueError','JSONDecodeError'))
        number = error.errno if isinstance(error,OSError) else None
        record = {'status':'canonical_image_smoke_failed','phase':PHASE,
            'inventory_elapsed_seconds':public_inventory_elapsed(),'inventory_stage':INVENTORY_STAGE if PHASE=='fresh_same_image_whole_acl_inventory'and INVENTORY_STAGE in INVENTORY_STAGES else None,'closed_code':public_failure_code(error,allowed),
            'exception_class':type(error).__name__ if type(error).__name__ in known else 'unclassified_exception',
            'os_errno':number if type(number) is int and 0 < number <= 4095 else None,
            'cleanup_causes':errors,'security_accepted':False,'full_matrix_accepted':False}
        if ctx is not None:
            try: ctx.write(ctx.root/'reports/failure.json',record)
            except BaseException: pass
        print(json.dumps(record,sort_keys=True)); return 1


if __name__ == '__main__': raise SystemExit(main())
