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
import os
import re
import stat
import time
import zipfile

MAX_JSON = 2*1024**2
MAX_NATIVE_REPORT = 2*1024**2
MAX_ACL_REPORT = 4*1024**2
INVENTORY_SHA256 = '1ec1e29c2a673eb14f1e8734014ea49a2b04a78ffce928b60aa447b5bd8c5469'
SBOM_SHA256 = '5e6a6930bad65191881b6c8366ef58a67c414318c14c0ca28f8dc0570952867c'
CONFIRM = 'RUN-ONLY-BA938-ISOLATED-SMOKE-AND-PUBLIC-INVENTORY-20261026-03'
MAX_ARCHIVE = 512*1024**2
OLD_PIN = '0555ffd794a6db2cb13c7119145bd8f1b5e09348576b6cb9d514239837c27a58'
BUILDER_PIN = '9916037a7d2756c3996d04a43ad59655d30d708ae6f5b35cb20789ea190cc555'
VERIFIERS = {
    'verify_image_component_presence.py': ('3cd10d7bc591615723f7b85a87011d0d7029120324f09ef2f099d49137bcfd94', 'security_image_component_presence/1', 65536),
    'verify_acl_consumer_presence.py': ('001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778', 'security_acl_consumer_presence/1', MAX_JSON),
}
EVIDENCE_NAMES = frozenset(('source-identity.json','input-manifest.json','image-build.json','prepare.json','finalize.json',
    'reports/vulnerabilities.json','reports/sbom.cdx.json'))
PHASE = 'source_admission'
CODES = frozenset(('intake_unarmed','payload_expectation_missing','actual_archive_observation_missing','intake_shape','evidence_shape','evidence_identity','evidence_signature',
    'evidence_reports','export_shape','export_identity','export_binding','archive_observation','canonical_labels',
    'capsule_binding','presence_identity','presence_mounts','presence_exit','smoke_receipt','source_admission','hosted_ci_only',
    'owned_cleanup_unproven','cleanup_report_unproven','outer_keeper_required','inventory_receipt','inventory_input','inventory_bounds','unexpected_failure'))

# Actual official artifacts plus bounded public archive measurement by the source
# agent, receipt770e2521ddaaa20c360ba28ac76b5260d54d673a72ea6507c05d6464d4447278.
# Measurement is not admission: the hosted parser still proves the whole graph,
# exact legacy bytes and complete configuration before any disposable LOAD.
CONTROL = {'schema': 'canonical_app_smoke_intake/1', 'run_id': 40368532303, 'run_attempt': 1, 'source_commit': '6d257bf4a92ce196e90fdbe138d59f48eddd95c0', 'source_tree': '5f8b6fe5dfec9195890362965c462db9342c772e', 'input_signature': '0643a2666f834c50b2b6fd61f7915310678466847cbb711b596bfdad86fb029e', 'image_id': 'sha256:ba938a7dd1ab1a0d9ebacdae768861a45276ba7caacead40ed319fa8e1bef218', 'rootfs': {'Layers': ['sha256:a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7', 'sha256:535a5d38abecfac4fa49da33fa942b53c53c463bd0d39a860967e46eb03faafe', 'sha256:6972161a9a77e7c1afb997daac15abc54f5b70c7b40b814601f53e038d8c398c', 'sha256:d86e9bd004831a6069b9a02e5caac80b73526315f52719079b4017b099956ed2', 'sha256:527a9067ddf670896f7606e5e92fcd51977ae2e4985997d17d11210a226324b1', 'sha256:834ad64ff1785fedaad099b7ec541790db463a19d675af1c857a47e68d29cf16', 'sha256:595529aa591aad12a24ebac1a130dd22688c41fcc446676ba79aab8dd4fbb83d', 'sha256:10da766cbcaa149431f1fd6f5b75cffeb4e4c29da92440b9ef280a31c6ffdda9', 'sha256:6ed1d7db2ebcc3c04852fae2c09fc4b498b4018f65409e5536f8c4629652db1a', 'sha256:724b9dfe116deb20bfc9cd5a863c65de5a8518b3d8aea11a5befbddd51745f35', 'sha256:7a999fc33e0fbaeb8a9b2b67b0c29fed7eb7aad550b7e9f5f1ea99bed4a868aa', 'sha256:e98354d95950da475052b4566ab44fd74aadc55e1ebb72b05e9e1d4ee8c59067', 'sha256:b67e52481727e1e8ff1ed2278cadd327bc218b2dcfdd86fe2b684a4d5626fee5', 'sha256:4672339e6ff351d70ae801e733d7b46ec32f3baa9e7f735568c28d1f9fd4e6d9', 'sha256:db9e8a25cb28dfde8d05c1ec5f537fc72d2f8a661bdc9691f5d9a54fa1b09670', 'sha256:1b35f80f271ab3a45acfd34063b8700432923de6b8d5cfc3a108b398aef32601', 'sha256:95a232658d304e02d9fbfc6e2cca3e050fce1bd383c36cd74791254d40998b7d', 'sha256:45c35139f316e64bdf1c9d8fc88e63e4cdbabcfb95845609c1da146b0e0178a7', 'sha256:f00940e28d93d5491bb8c29d5c2b10a9790c4d9dff387700693535e4566fe600', 'sha256:5e80cffe5797cdcdd7f4e7630a77680a6501dea44d1bccfe3d47ac402a55fc21', 'sha256:fd7496a4c1923edfe50f5e231b7c2719407265df1009f560345c8f61daf7bbe0', 'sha256:2c8ac6d2b806b0aa92ea9bcc1101c022ab533dc7a4bc097e7dea64c3abcadbbc', 'sha256:1c6d141530cbd5c6a91ca35f5731abc08927d2378736907f412429e78a2b4d0a', 'sha256:921287bccf008e4f8ff2de41d59e06e4e0ad23c8a16d18ff9f7363ebe101efc8', 'sha256:3c41629f3af2d25a1de330f6b37c1f96e6c4457eeb953cb6aab30c061464e48d', 'sha256:58871072bae5801219df9c84c1434f821dbb7b136215ce0a21fae93bdec6f621', 'sha256:0a942d279782cfbe2045f0ad490a7ed2b33d289d116919ee23dd8bdfeb07168d', 'sha256:0e04f2aad5d5cdb6a1d9ff34ee61c72c4953432a8681e24857d56279731b4c05', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef', 'sha256:6ed1fb3f4563a7e7672173d40685f25a08c6ddb5b7c870177bbf37d0cff02a36', 'sha256:9d56dc0554657e240531deb22f8b902195b95c8187957b375aa06fb3c982f611', 'sha256:c1a0f2b714440ba48e83100b26aa8449e38527eaaad125f8590669ae77048aa5'], 'Type': 'layers'}, 'raw_counts': {'CRITICAL': 0, 'HIGH': 44, 'LOW': 82, 'MEDIUM': 68, 'UNKNOWN': 1}, 'image_artifact_id': 12671093012, 'image_zip_bytes': 184269506, 'image_zip_sha256': 'b648a9aa7bb0850256faec333c45986c0f457749d959831f8f4433f9b5894f91', 'evidence_artifact_id': 12672440161, 'evidence_zip_bytes': 337659, 'evidence_zip_sha256': 'a6d87e04b78bd48fe725777a860d339bf0a491e3cd779bb71762b31162d7608d', 'archive_observation': {'archive_bytes': 455000064, 'archive_sha256': 'b381ad881e0a43f7be3ddc416468668f09cde7deb246b1607ce4e6350c510814', 'expanded_layer_bytes': 454914048, 'member_count': 73, 'manifest_id': 'sha256:5c4bde25113d0ed8d37ede5c6bbd297e4792a9d112e4aee11dd7f716a1fffb46', 'index_sha256': '1c6ed2b403ac0de53e0d2aa90a4d3027480dc22d223e377525f66c5ca8cb08bf', 'legacy_compatibility_configs': 34}}


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
NATIVE_PAYLOAD_EXPECTATION_SHA256 = '9a2f97514d33b62a6c787ee94776f2856e5cba861570e4a6c28603664e9969b3'

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


def whole_image_inventory(ctx, old, image, control, evidence_zip):
    """Independent public ELF/source inventory FIRST, never a caller-safety gate."""
    global PHASE
    PHASE = 'fresh_same_image_whole_acl_inventory'
    script,check = old.sealed(ctx.stack,ctx.root/'source/inventory.py',INVENTORY_SHA256,131072)
    ctx.checks.append(check)
    with ctx.C.pinned(evidence_zip,control['evidence_zip_bytes']) as (stream,size):
        need(size == control['evidence_zip_bytes'] and ctx.C.stream_digest(stream,size) == control['evidence_zip_sha256'],'inventory_input')
        stream.seek(0); sbom=read_zip(stream,EVIDENCE_NAMES)['reports/sbom.cdx.json']
    need(sha256(sbom).hexdigest() == SBOM_SHA256,'inventory_input')
    proof=ctx.root/'whole-acl-proof'; proof.mkdir(mode=0o755); proof.chmod(0o755)
    ctx.C.write(proof/'inventory.py',script,0o444); ctx.C.write(proof/'sbom.cdx.json',sbom,0o444)
    gid=(proof/'inventory.py').lstat().st_gid
    need((proof/'inventory.py').lstat().st_uid == (proof/'sbom.cdx.json').lstat().st_uid == 1001
        and (proof/'sbom.cdx.json').lstat().st_gid == gid,'inventory_input')
    name='app-ci-whole-acl-'+old.owner();ctx.created.append((name,image))
    cid=ctx.docker(inventory_args(old,name,image,proof,gid,control),maximum=256).decode().strip()
    need(re.fullmatch('[a-f0-9]{64}',cid),'presence_identity')
    template='{"name":{{json .Name}},"image":{{json .Image}},"owner":{{json (index .Config.Labels "'+old.LABEL+'")}},"user":{{json .Config.User}},"readonly":{{json .HostConfig.ReadonlyRootfs}},"privileged":{{json .HostConfig.Privileged}},"network":{{json .HostConfig.NetworkMode}},"networks":{{json .NetworkSettings.Networks}},"caps_add":{{json .HostConfig.CapAdd}},"caps_drop":{{json .HostConfig.CapDrop}},"security":{{json .HostConfig.SecurityOpt}},"memory":{{json .HostConfig.Memory}},"memory_swap":{{json .HostConfig.MemorySwap}},"nano_cpus":{{json .HostConfig.NanoCpus}},"pids":{{json .HostConfig.PidsLimit}},"tmpfs":{{json .HostConfig.Tmpfs}},"mounts":{{json .Mounts}},"running":{{json .State.Running}},"paused":{{json .State.Paused}},"pid":{{json .State.Pid}}}'
    def admit(): return inventory_container(document(ctx.docker(['container','inspect','--format',template,cid],maximum=16384)),old,name,image,proof)
    admit();raw=ctx.docker(['start','--attach',cid],seconds=150,maximum=MAX_ACL_REPORT)
    need(ctx.docker(['inspect','--format','{{.State.ExitCode}}',cid],maximum=64).strip() == b'0','presence_exit');admit()
    value=inventory_document(raw);old.public(value)
    need(value.get('schema') == 'image_acl_import_inventory/1' and value.get('image_id') == control['image_id']
        and value.get('source_commit') == control['source_commit'] and value.get('verifier_sha256') == INVENTORY_SHA256
        and value.get('sbom_sha256') == SBOM_SHA256 and type(value.get('inventory')) is dict
        and all(value.get(key) is False for key in ('snapshot_atomic','directory_symlinks_followed','inspected_code_executed',
            'static_or_obfuscated_code_completely_analyzed','dlopen_or_ctypes_reachability_analyzed',
            'acl_flags_or_callsite_safety_proven','whole_image_acl_closed','production_mounts_or_data_used',
            'scanner_findings_modified','security_accepted')),'inventory_receipt')
    # ctx.write has the prior two-MiB bound; this separately reviewed inventory
    # permits four MiB and remains subject to the same recursive public gate.
    report=encode(value);need(len(report) <= MAX_ACL_REPORT,'inventory_bounds')
    ctx.C.write(ctx.root/'reports/fresh-whole-image-acl-inventory.json',report)
    ctx.check();return value


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


def main(argv=None, *, outer_keeper=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--execute-reviewed-ba938-proof',action='store_true'); parser.add_argument('--confirm')
    args = parser.parse_args(argv); ctx = None; old = None; errors = []; primary = False
    if not args.execute_reviewed_ba938_proof:
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
        allowed = set(CODES)
        if old is not None: allowed.update(old.FAILURE_CODES)
        if ctx is not None:
            for module in ctx.modules.values(): allowed.update(getattr(module,'PUBLIC_FAILURE_CODES',())); allowed.update(getattr(module,'PUBLIC_CODES',()))
        known = frozenset(('RuntimeError','Refusal','AssertionError','OSError','FileNotFoundError','PermissionError','TimeoutError',
            'KeyError','TypeError','ValueError','JSONDecodeError'))
        number = error.errno if isinstance(error,OSError) else None
        record = {'status':'canonical_image_smoke_failed','phase':PHASE,'closed_code':code if code in allowed else 'unexpected_failure',
            'exception_class':type(error).__name__ if type(error).__name__ in known else 'unclassified_exception',
            'os_errno':number if type(number) is int and 0 < number <= 4095 else None,
            'cleanup_causes':errors,'security_accepted':False,'full_matrix_accepted':False}
        if ctx is not None:
            try: ctx.write(ctx.root/'reports/failure.json',record)
            except BaseException: pass
        print(json.dumps(record,sort_keys=True)); return 1


if __name__ == '__main__': raise SystemExit(main())
