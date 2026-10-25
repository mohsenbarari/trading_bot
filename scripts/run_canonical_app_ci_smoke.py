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
MAX_ARCHIVE = 512*1024**2
OLD_PIN = '09221619f6929df66f2622de8911913e8bf3770c96001ae5e06d77a9851abb62'
BUILDER_PIN = 'e62ca64c27f81f5de79e096c9acf18da135c6644641607e1b1e6d44373b415fc'
VERIFIERS = {
    'verify_image_component_presence.py': ('36e92fdfd3105a9db6bc20fd75aade1238a5e0b053a581bfb24c7b5e1bf6a6d3', 'security_image_component_presence/1', 65536),
    'verify_acl_consumer_presence.py': ('001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778', 'security_acl_consumer_presence/1', MAX_JSON),
}
EVIDENCE_NAMES = frozenset(('source-identity.json','input-manifest.json','image-build.json','prepare.json','finalize.json',
    'reports/vulnerabilities.json','reports/sbom.cdx.json'))
PHASE = 'source_admission'
CODES = frozenset(('actual_archive_observation_missing','intake_shape','evidence_shape','evidence_identity','evidence_signature',
    'evidence_reports','export_shape','export_identity','export_binding','archive_observation','canonical_labels',
    'capsule_binding','presence_identity','presence_mounts','presence_exit','smoke_receipt','source_admission','hosted_ci_only',
    'owned_cleanup_unproven','cleanup_report_unproven','unexpected_failure'))

# Actual official artifact metadata and Web observation aed34797d49f45d1973e846cbe98d73c.
# Measurement is not admission: the hosted parser still proves the whole graph,
# exact legacy bytes and complete configuration before any disposable LOAD.
CONTROL = {'archive_observation': {'archive_bytes':452719104,
                                   'archive_sha256':'e10090370762baf289dd55457f648b5c78b05e8e398c724397336b1d89006c67',
                                   'expanded_layer_bytes':452635136,
                                   'member_count':71,
                                   'manifest_id':'sha256:2f609ac53add114037016e54482486cfb455e41b1d79c6625c2772b07bb81908',
                                   'index_sha256':'fa9481689c7165671348103769538e9e17b522f53b3ae51d5b38bbd3aedc022f',
                                   'legacy_compatibility_configs':33},
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


def validate_control(value):
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
    image = admit_app(ctx,old,control,evidence); presence(ctx,old,image,control); return run_smoke(ctx,old,image,control)


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--root',type=Path,required=True)
    args = parser.parse_args(argv); ctx = None; old = None; errors = []; primary = False
    try:
        hosted(args.root); validate_control(CONTROL)
        with ExitStack() as stack:
            old, check = source_module(args.root,stack,'run_app_image_ci_smoke.py',OLD_PIN)
            builder, builder_check = source_module(args.root,stack,'prepare_canonical_app_image.py',BUILDER_PIN)
            ctx = old.Controller(args.root,stack); ctx.checks.extend((check,builder_check))
            # This instance is fresh, private and never shared with old execution.
            ctx.D.legacy_configs = lambda config,moby: canonical_legacy_configs(config,moby,ctx.D)
            try: execute(ctx,old,builder,CONTROL)
            except BaseException: primary = True; raise
            finally:
                errors = finish_cleanup(ctx,old,primary)
            ctx.check()
        print(json.dumps({'status':'canonical_image_smoke_passed','tests':32,'fresh_presence_reports':2,'security_accepted':False,'full_matrix_accepted':False})); return 0
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
