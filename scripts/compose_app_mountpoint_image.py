"""Hosted-CI-only directory layer on the exact admitted App8da export.

This builds/tests/exports an unaccepted public image. Runtime controls, data,
configuration, vulnerability findings and production are never changed.
"""
from contextlib import ExitStack, contextmanager
from hashlib import sha256
import argparse
import ast
import gzip
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import time
from types import ModuleType
from zipfile import ZipFile

PARENT = 'sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672'
PARENT_RUN = 40127349844
PARENT_RECIPE = '8b02e0fdb283d63b94a73b95300580105332d56f'
IMAGE_ARTIFACT = (12573279010, 183811369, '3424f8f038817e42fd7992761a588039c79bab5a6542571ee73ef2d3338cb807')
EVIDENCE_SHA = 'd05da4a9c4d1c82b37e1fc0ac9e861762c88be862872ea767a8b561db2982716'
COMPOSITION_SHA = '8fbcad3c416a179984ff744cd166f1279cd1af97cb85892ce99c58f8e1b74797'
RAW_SHA = '77e85632e55ded98366e799c0f166a0b6ab434a9e71f2b728aac5e452a9b7192'
SBOM_SHA = '5f05e26c9dc2d5d406398979d750ce71177dbde85033356ac70f186631ab6f80'
LAYERS_SHA = '6762bf592f90b011d8c356fa58051654234aeff72b33f0d2d825f525c3d4c203'
TAR_SHA = '7337d4730d2a99edb9c62d45e499afcb3654c16eba20582318ceb1866515e786'
TAR_BYTES = 451747328
RECIPE_SHA = 'b25a899bfb379ae413fd8607432b8dde7b0bcab3c9eeb6fc6a2392404eb16d2a'
PAYLOAD_SHA = 'cd2e64577576b9053b926d2e65620a98ca77b0ad71cc3100d9145b9409077c7b'
DIRECTORIES = ('app/runtime', 'app/runtime/coin-inference', 'app/runtime/product-estimator')
LABEL_PREFIX = 'io.gold-trade.security.mountpoints.'
HELPERS = {
    'compose_security_os_candidate.py': '10ccbe216fef04e5f7a7ce2ad31ac30eca7eeae090dfaaf8074fa3cc5c789d44',
    'admit_security_component_image_reuse.py': '9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025',
    'compose_market_security_os_candidate.py': 'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9',
    'verify_image_component_presence.py': '36e92fdfd3105a9db6bc20fd75aade1238a5e0b053a581bfb24c7b5e1bf6a6d3',
    'verify_acl_consumer_presence.py': '001e51d270c079153cc38db1be53c723fbbc8a8bb9e981e718e7b657127bc778',
}
MAX_TAR, MAX_JSON = 2 * 1024**3, 2 * 1024**2
COMMON = LEGACY = PRIOR = COMPONENT = None
HELPER_CODES = frozenset()
PHASE = 'source_admission'


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode()


def document(raw):
    def unique(rows):
        value = {}
        for key, item in rows:
            require(key not in value, 'duplicate_json_key')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: require(False, 'invalid_json_constant'))


@contextmanager
def pinned(path, maximum):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'input_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    identity = lambda q: (q.st_dev, q.st_ino, q.st_mode, q.st_uid, q.st_gid, q.st_nlink,
                          q.st_size, q.st_mtime_ns, q.st_ctime_ns)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
                and 0 < before.st_size <= maximum, 'input_metadata')
        require(identity(before) == identity(path.lstat()), 'input_changed')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            yield stream, before.st_size
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'input_changed')
    finally:
        os.close(fd)


def read(path, maximum=MAX_JSON):
    with pinned(path, maximum) as (stream, size):
        raw = stream.read(size + 1)
        require(len(raw) == size, 'input_bound')
        return raw


def digest(stream, size):
    result, total = sha256(), 0
    while block := stream.read(min(65536, size + 1 - total)):
        total += len(block)
        require(total <= size, 'stream_bound')
        result.update(block)
    require(total == size, 'stream_size')
    return result.hexdigest()


def write(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fchmod(stream.fileno(), mode)
        os.fsync(stream.fileno())


def ci(root):
    require(os.geteuid() == 1001 and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and os.environ.get('GITHUB_REPOSITORY') == 'mohsenbarari/trading_bot', 'hosted_ci_only')
    parent = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(root.is_absolute() and root.resolve(strict=True) == root and root.parent == parent
            and root.name.startswith('security-app-mountpoints-') and root.stat().st_uid == 1001
            and stat.S_IMODE(root.stat().st_mode) == 0o700, 'ci_root')
    require(re.fullmatch('[a-f0-9]{40}', os.environ.get('GITHUB_SHA', ''))
            and re.fullmatch('[0-9]+-[0-9]+', owner()), 'ci_identity')
    os.umask(0o077)


def owner():
    return os.environ.get('GITHUB_RUN_ID', '') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '')


def phase(name):
    global PHASE
    PHASE = name


@contextmanager
def sources(root):
    global COMMON, LEGACY, PRIOR, COMPONENT, HELPER_CODES
    with ExitStack() as stack:
        identity = document(read(root / 'source/identity.json', 65536))
        names = {Path(__file__).name, 'Dockerfile', 'payload_manifest.json', *HELPERS}
        require(identity.get('recipe_commit') == os.environ['GITHUB_SHA']
                and set(identity.get('files', {})) == names, 'source_inventory')
        modules = {}
        declared_codes = set()
        for name in sorted(names):
            stream, size = stack.enter_context(pinned(root / 'source' / name, 512*1024 if name == 'payload_manifest.json' else 131072))
            raw = stream.read(size + 1)
            require(len(raw) == size and identity['files'][name] == {'bytes': size, 'sha256': sha256(raw).hexdigest()},
                    'sealed_source_changed')
            if name == Path(__file__).name:
                require(raw == read(Path(__file__).resolve(strict=True), 131072), 'controller_source_changed')
            elif name == 'Dockerfile':
                require(sha256(raw).hexdigest() == RECIPE_SHA, 'recipe_changed')
            elif name == 'payload_manifest.json':
                require(len(raw) == 330066 and sha256(raw).hexdigest() == PAYLOAD_SHA, 'payload_manifest_changed')
            else:
                require(sha256(raw).hexdigest() == HELPERS[name], 'helper_changed')
                module = ModuleType('_mountpoints_' + name[:-3])
                module.__file__ = str(root / 'source' / name)
                exec(compile(raw, module.__file__, 'exec'), module.__dict__)
                modules[name] = module
                # Static literals from SHA-pinned public helpers only. Preserve
                # their precise refusal without emitting exception operands.
                declared_codes.update(q.args[1].value for q in ast.walk(ast.parse(raw))
                    if isinstance(q, ast.Call) and isinstance(q.func, ast.Name) and q.func.id == 'require'
                    and len(q.args) >= 2 and isinstance(q.args[1], ast.Constant) and isinstance(q.args[1].value, str))
        COMMON = modules['compose_security_os_candidate.py']
        LEGACY = modules['admit_security_component_image_reuse.py']
        PRIOR = modules['compose_market_security_os_candidate.py']
        COMPONENT = modules['verify_image_component_presence.py']
        HELPER_CODES = frozenset(declared_codes)
        yield identity


def admit_evidence(path):
    raw = read(path, 248816)
    require(len(raw) == 248816 and sha256(raw).hexdigest() == EVIDENCE_SHA, 'parent_evidence_zip')
    with ZipFile(io.BytesIO(raw)) as archive:
        rows = archive.infolist()
        require(len(rows) == 21 and len({q.filename for q in rows}) == 21, 'parent_evidence_inventory')
        require(all(not q.is_dir() and not q.flag_bits & 1 and q.compress_type in (0, 8)
                    and stat.S_IFMT(q.external_attr >> 16) in (0, stat.S_IFREG)
                    and 0 < q.file_size <= 32 * 1024**2 for q in rows), 'parent_evidence_member')
        selected = {}
        for name, pin in (('reports/composition.json', COMPOSITION_SHA),
                          ('reports/vulnerabilities.json', RAW_SHA), ('reports/sbom.cdx.json', SBOM_SHA)):
            info = archive.getinfo(name)
            require(info.file_size <= MAX_JSON, 'parent_evidence_bound')
            value = archive.read(name)
            require(len(value) == info.file_size and sha256(value).hexdigest() == pin, 'parent_evidence_hash')
            selected[name] = document(value)
        composition = selected['reports/composition.json']
        require(composition.get('image_id') == PARENT and composition.get('recipe', {}).get('recipe_commit') == PARENT_RECIPE
                and composition.get('security_accepted') is False and composition.get('deployment_performed') is False
                and composition.get('app_and_python_unchanged') is True and composition.get('runtime_config_unchanged') is True,
                'parent_composition')
        for name, pin in composition['evidence_sha256'].items():
            info = archive.getinfo('reports/' + name)
            require(0 < info.file_size <= 32 * 1024**2, 'parent_evidence_bound')
            content = archive.read(info)
            require(len(content) == info.file_size and sha256(content).hexdigest() == pin, 'parent_evidence_hash')
        cleanup = document(archive.read('reports/base-alias-cleanup.json'))
        require(cleanup.get('status') == 'removed_owned_alias', 'parent_alias_not_cleaned')
    config = selected['reports/vulnerabilities.json']['Metadata']['ImageConfig']
    layers = config['rootfs']['diff_ids']
    require(selected['reports/vulnerabilities.json']['Metadata']['ImageID'] == PARENT
            and len(layers) == 32 and sha256(json.dumps(layers, separators=(',', ':')).encode()).hexdigest() == LAYERS_SHA
            and composition['candidate_layers'] == layers, 'parent_layers')
    return {'composition': composition, 'scanner_config': config}


def decode_gzip(source, size, target, expected_size):
    require(type(expected_size) is int and 0 < expected_size <= MAX_TAR, 'gzip_bound')
    end = time.monotonic() + 180
    limited = PRIOR.SliceReader(source, 0, size, end)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output, gzip.GzipFile(fileobj=limited) as decoded:
        total = 0
        while block := decoded.read(min(65536, expected_size + 1 - total)):
            total += len(block)
            require(time.monotonic() < end and total <= expected_size, 'gzip_bound')
            output.write(block)
        require(total == expected_size, 'gzip_size')
        output.flush()
        os.fsync(output.fileno())


def layer_directories(stream, parent_app):
    """Physical last layer: directories only, bounded before decompression/read."""
    prefix = stream.read(2)
    stream.seek(0)
    decoded = gzip.GzipFile(fileobj=stream) if prefix == b'\x1f\x8b' else stream
    total, seen, end = 0, set(), time.monotonic() + 30
    def block(size):
        nonlocal total
        require(0 <= size <= 65536 and total + size <= 1024**2 and time.monotonic() < end, 'directory_layer_bound')
        raw = decoded.read(size)
        require(len(raw) == size, 'directory_layer_short')
        total += size
        return raw
    try:
        while True:
            raw = block(512)
            if not any(raw):
                require(not any(block(512)), 'directory_layer_trailer')
                while tail := decoded.read(min(65536, 1024**2 + 1 - total)):
                    total += len(tail)
                    require(total <= 1024**2 and time.monotonic() < end and not any(tail), 'directory_layer_trailer')
                break
            member = tarfile.TarInfo.frombuf(raw, 'utf-8', 'strict')
            name = member.name.rstrip('/')
            require(member.type == tarfile.DIRTYPE and member.size == 0 and name not in seen
                    and name in {*DIRECTORIES, 'app'} and len(seen) < 4, 'directory_layer_outside_scope')
            require(not member.pax_headers and not member.linkname, 'directory_layer_metadata')
            expected = parent_app if name == 'app' else [0o755, 0, 0]
            require([member.mode, member.uid, member.gid] == expected, 'directory_layer_mode')
            seen.add(name)
    finally:
        if decoded is not stream:
            decoded.close()
    require(set(DIRECTORIES) <= seen, 'directory_layer_incomplete')
    return {'new_directories': list(DIRECTORIES), 'entries': len(seen), 'uncompressed_bytes': total,
            'non_directory_entries': 0, 'outside_scope_changes': False}


def validate_archive(stream, size, image, scanner=None, *, last_layer=None):
    """Closed physical save archive; derivative of the reviewed Market parser.

    Uses the App-specific exact Moby18-field legacy serializer, not the Market
    seven-field runtime projection. No sealed helper global is modified.
    """
    require(type(size) is int and 1024 <= size <= MAX_TAR and size % 512 == 0, 'archive_size')
    end = time.monotonic() + 300
    whole = PRIOR.SliceReader(stream, 0, size, end)
    members, offset = {}, 0
    while True:
        require(offset + 512 <= size, 'archive_trailer')
        whole.seek(offset)
        header = whole.read(512)
        if not any(header):
            break
        entry = tarfile.TarInfo.frombuf(header, 'utf-8', 'strict')
        name = entry.name.rstrip('/') if entry.isdir() else entry.name
        require(len(members) < 512 and PRIOR.safe_name(name) and name not in members and not entry.linkname
                and entry.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
                and 0 <= entry.size <= size - offset - 512 and (entry.size == 0 or entry.isfile()), 'archive_member')
        offset += 512
        members[name] = (offset, entry.size, entry.isdir())
        offset += (entry.size + 511) // 512 * 512
    require(size - offset >= 1024, 'archive_trailer')
    whole.seek(offset)
    while block := whole.read(min(65536, size - whole.tell())):
        require(not any(block), 'archive_trailing_payload')
    def member(name):
        require(name in members and not members[name][2], 'archive_missing')
        start, length, _ = members[name]
        return PRIOR.SliceReader(stream, start, length, end), length
    def data(name, maximum=MAX_JSON):
        source, length = member(name)
        require(length <= maximum, 'archive_json_bound')
        return source.read(length)
    def stored(name):
        source, length = member(name)
        return digest(source, length)
    manifest = document(data('manifest.json'))
    require(type(manifest) is list and len(manifest) == 1 and type(manifest[0]) is dict
            and {'Config', 'Layers'} <= set(manifest[0]) <= {'Config', 'Layers', 'RepoTags', 'LayerSources'}, 'archive_manifest')
    row = manifest[0]
    config_name, layers = row['Config'], row['Layers']
    require(re.fullmatch('sha256:[a-f0-9]{64}', image) and config_name in (image[7:] + '.json', 'blobs/sha256/' + image[7:])
            and stored(config_name) == image[7:], 'archive_config_identity')
    config = document(data(config_name))
    require(config.get('os') == 'linux' and config.get('architecture') == 'amd64'
            and config.get('rootfs', {}).get('type') == 'layers', 'archive_platform')
    projection = None
    if scanner is not None:
        projection = PRIOR.compare_scanner_config(config, scanner)
        projection['original_nanosecond_config_sha256'] = image[7:]
    diffs = config['rootfs']['diff_ids']
    require(type(layers) is list and 0 < len(layers) == len(diffs) <= 128
            and all(PRIOR.safe_name(n) for n in layers) and len(set(layers)) == len(layers), 'archive_layers')
    tags = row.get('RepoTags')
    require(tags is None or type(tags) is list and len(tags) <= 8
            and all(type(q) is str and re.fullmatch(r'[A-Za-z0-9_./:@-]{1,256}', q) for q in tags), 'archive_tags')
    allowed, expanded, layer_proof = {'manifest.json', config_name, *layers}, 0, None
    for index, (name, diff) in enumerate(zip(layers, diffs)):
        source, length = member(name)
        compressed = source.read(min(2, length)) == b'\x1f\x8b'
        source.seek(0)
        decoded = gzip.GzipFile(fileobj=source) if compressed else source
        hashed = sha256()
        try:
            while block := decoded.read(min(65536, MAX_TAR + 1 - expanded)):
                expanded += len(block)
                require(expanded <= MAX_TAR, 'archive_expansion')
                hashed.update(block)
        finally:
            if decoded is not source:
                decoded.close()
        require(diff == 'sha256:' + hashed.hexdigest(), 'archive_layer_diff_id')
        if last_layer is not None and index == len(layers) - 1:
            source.seek(0)
            layer_proof = layer_directories(source, last_layer)
    manifests = []
    if 'index.json' in members:
        require(document(data('oci-layout')) == {'imageLayoutVersion': '1.0.0'}, 'oci_layout')
        allowed.update(('index.json', 'oci-layout'))
        def descriptor(value):
            require(type(value) is dict and type(value.get('digest')) is str
                    and re.fullmatch('sha256:[a-f0-9]{64}', value['digest'])
                    and type(value.get('size')) is int and value['size'] > 0, 'oci_descriptor')
            name = 'blobs/sha256/' + value['digest'][7:]
            require(name in members and members[name][1] == value['size'] and stored(name) == value['digest'][7:], 'oci_blob')
            allowed.add(name)
            return name
        index = document(data('index.json'))
        for _ in range(4):
            require(type(index) is dict and index.get('schemaVersion') == 2 and type(index.get('manifests')) is list
                    and len(index['manifests']) == 1, 'oci_index')
            wire = index['manifests'][0]
            selected = document(data(descriptor(wire)))
            if 'manifests' in selected:
                index = selected
                continue
            require(selected.get('schemaVersion') == 2 and descriptor(selected.get('config')) == config_name
                    and type(selected.get('layers')) is list and [descriptor(q) for q in selected['layers']] == layers, 'oci_manifest')
            manifests.append(wire['digest'])
            break
        else:
            require(False, 'oci_depth')
    else:
        require('oci-layout' not in members and all(n.endswith('/layer.tar') and not n.startswith('blobs/') for n in layers), 'classic_layout')
        for name, raw in zip(layers, LEGACY.moby_legacy_configs(config).values()):
            prefix = name[:-len('layer.tar')]
            for suffix in ('VERSION', 'json'):
                if prefix + suffix in members:
                    if suffix == 'VERSION':
                        require(data(prefix + suffix, 32) in (b'1.0', b'1.0\n'), 'classic_version')
                    else:
                        expected = document(raw)
                        require(prefix == expected['id'] + '/' and document(data(prefix + suffix, 65536)) == expected, 'classic_metadata')
                    allowed.add(prefix + suffix)
        if 'repositories' in members:
            require(type(document(data('repositories'))) is dict, 'classic_repositories')
            allowed.add('repositories')
    directories = {str(q) for name in allowed for q in PurePosixPath(name).parents if str(q) != '.'}
    extras = set(members) - allowed - directories
    if extras and 'index.json' in members:
        legacy = LEGACY.moby_legacy_configs(config)
        require(extras == set(legacy), 'archive_closed_inventory')
        for name, raw in legacy.items():
            require(data(name, 65536) == raw, 'legacy_bytes')
        allowed.update(legacy)
    require(set(members) <= allowed | directories
            and all(isdir == (name in directories) for name, (_, _, isdir) in members.items()), 'archive_closed_inventory')
    whole.seek(0)
    return {'config_id': image, 'config': config, 'layers': len(layers), 'oci_manifest_ids': manifests,
            'archive_bytes': size, 'archive_sha256': digest(whole, size), 'scanner_projection': projection,
            'new_layer': layer_proof, 'physical_graph_closed': True}


def admit_parent(path, root, evidence):
    with pinned(path, IMAGE_ARTIFACT[1]) as (stream, size):
        require(size == IMAGE_ARTIFACT[1] and digest(stream, size) == IMAGE_ARTIFACT[2], 'parent_image_zip')
        stream.seek(0)
        with ZipFile(stream) as archive:
            rows = archive.infolist()
            require(len(rows) == 2 and {q.filename for q in rows} == {'candidate-image.tar.gz', 'image-export.json'}, 'parent_zip_inventory')
            require(all(not q.is_dir() and not q.flag_bits & 1 and q.compress_type in (0, 8)
                        and stat.S_IFMT(q.external_attr >> 16) in (0, stat.S_IFREG)
                        and 0 < q.file_size <= (MAX_JSON if q.filename == 'image-export.json' else MAX_TAR) for q in rows),
                    'parent_zip_member')
            export = document(archive.read('image-export.json'))
            require(export.get('schema') == 'security_os_composition_image_export/1'
                    and export.get('status') == 'exported_not_accepted' and export.get('image_id') == PARENT
                    and export.get('composition_sha256') == COMPOSITION_SHA
                    and export.get('reports_sha256') == {'vulnerabilities.json': RAW_SHA, 'sbom.cdx.json': SBOM_SHA}
                    and export.get('evidence_sha256') == evidence['composition']['evidence_sha256']
                    and export.get('security_accepted') is False and export.get('deployment_performed') is False,
                    'parent_export')
            record = export['archive']
            require(set(record) == {'name', 'format', 'size_bytes', 'sha256', 'uncompressed_bytes'}
                    and record['name'] == 'candidate-image.tar.gz' and record['format'] == 'docker-archive+gzip'
                    and type(record['size_bytes']) is int and 0 < record['size_bytes'] <= MAX_TAR
                    and re.fullmatch('[a-f0-9]{64}', record['sha256']) and record['uncompressed_bytes'] == TAR_BYTES
                    and archive.getinfo(record['name']).file_size == record['size_bytes'], 'parent_gzip_record')
            with archive.open(record['name']) as source:
                fd = os.open(root / 'parent.tar.gz', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as output:
                    total = 0
                    while block := source.read(min(65536, record['size_bytes'] + 1 - total)):
                        total += len(block)
                        require(total <= record['size_bytes'], 'parent_zip_expansion')
                        output.write(block)
                    require(total == record['size_bytes'], 'parent_zip_expansion')
                    output.flush()
                    os.fsync(output.fileno())
    with pinned(root / 'parent.tar.gz', MAX_TAR) as (stream, size):
        require(size == record['size_bytes'] and digest(stream, size) == record['sha256'], 'parent_gzip_identity')
        stream.seek(0)
        decode_gzip(stream, size, root / 'parent.tar', TAR_BYTES)
    with pinned(root / 'parent.tar', MAX_TAR) as (stream, size):
        proof = validate_archive(stream, size, PARENT, evidence['scanner_config'])
    require(proof['layers'] == 32 and proof['archive_bytes'] == TAR_BYTES and proof['archive_sha256'] == TAR_SHA, 'parent_archive_identity')
    return proof


def inspect_binding(root, operand, proof):
    rows = document(COMMON.docker(root, ['image', 'inspect', operand], maximum=MAX_JSON))
    require(type(rows) is list and len(rows) == 1, 'daemon_image_shape')
    value = rows[0]
    actual = value.get('Id')
    require(actual in {proof['config_id'], *proof['oci_manifest_ids']}
            and value.get('Os') == 'linux' and value.get('Architecture') == 'amd64'
            and value.get('RootFS') == {'Type': 'layers', 'Layers': proof['config']['rootfs']['diff_ids']}, 'daemon_identity')
    require(PRIOR.same_json_value(PRIOR.oci_runtime_config(value.get('Config')), PRIOR.oci_runtime_config(proof['config']['config'])),
            'daemon_config_identity')
    require(value['Config'].get('User', '') in ('', '0', 'root') and not value['Config'].get('OnBuild'), 'parent_user_onbuild')
    return value


def signature(identity, layers):
    value = {'schema': 'app_mountpoint_inputs/1', 'recipe_commit': identity['recipe_commit'],
             'dockerfile_sha256': RECIPE_SHA, 'parent_config_id': PARENT, 'parent_layers': layers,
             'directories': [{'path': '/' + name, 'mode': '0755', 'uid': 0, 'gid': 0,
                              'empty': name != 'app/runtime',
                              'children': ['coin-inference', 'product-estimator'] if name == 'app/runtime' else []}
                             for name in DIRECTORIES]}
    return value, sha256(encoded(value)).hexdigest()


def expected_config(before, commit, input_sha):
    require(re.fullmatch('[a-f0-9]{40}', commit) and re.fullmatch('[a-f0-9]{64}', input_sha), 'input_signature')
    labels = before.get('Labels')
    additions = {LABEL_PREFIX + 'parent-config': PARENT, LABEL_PREFIX + 'source-commit': commit,
                 LABEL_PREFIX + 'input-signature': input_sha, LABEL_PREFIX + 'status': 'packaging_candidate_not_accepted'}
    require(type(labels) is dict and not set(labels) & set(additions), 'parent_label_collision')
    return {**before, 'Labels': {**labels, **additions}}


def compare_images(before, after, commit, input_sha):
    expected = expected_config(before['Config'], commit, input_sha)
    require(after['Id'] != before['Id'] and PRIOR.same_json_value(after['Config'], expected), 'whole_runtime_config_changed')
    old, new = before['RootFS']['Layers'], after['RootFS']['Layers']
    require(len(old) == 32 and len(new) == 33 and new[:32] == old, 'exact_layer_prefix_changed')


FILES = r'''
import hashlib,json,os,pathlib,stat,time
end=time.monotonic()+90;rows={};count=total=0
for root in ('/app','/usr/local'):
 for folder,dirs,files in os.walk(root,followlinks=False):
  dirs.sort();files.sort()
  for name in sorted(dirs+files):
   path=pathlib.Path(folder)/name;s=path.lstat();count+=1
   assert time.monotonic()<end and count<=60000
   value=[stat.S_IMODE(s.st_mode),s.st_uid,s.st_gid]
   if stat.S_ISDIR(s.st_mode):value=['dir',*value]
   elif stat.S_ISLNK(s.st_mode):value=['link',*value,os.readlink(path)]
   else:
    assert stat.S_ISREG(s.st_mode);total+=s.st_size;assert total<=768*1024**2
    h=hashlib.sha256()
    with path.open('rb') as source:
     while block:=source.read(65536):h.update(block)
    value=['file',*value,s.st_size,h.hexdigest()]
   rows[str(path)]=value
app=pathlib.Path('/app').lstat();assert stat.S_ISDIR(app.st_mode)
print(json.dumps({'entries':rows,'app_root':[stat.S_IMODE(app.st_mode),app.st_uid,app.st_gid]},sort_keys=True))
'''


def compare_files(before, after):
    left, right = before['entries'], after['entries']
    new = {'/' + name for name in DIRECTORIES}
    require(before['app_root'] == after['app_root'] and not set(left) & new
            and set(right) == set(left) | new and all(PRIOR.same_json_value(right[name], row) for name, row in left.items())
            and all(right[name] == ['dir', 0o755, 0, 0] for name in new), 'effective_filesystem_changed')
    return {'complete_existing_entries': len(left), 'complete_existing_entries_equal': True,
            'app_root_unchanged': True, 'new_empty_directories': sorted(new),
            'regular_files_equal': sum(q[0] == 'file' for q in left.values()),
            'python_sources_equal': sum(name.startswith('/app/') and name.endswith('.py') and q[0] == 'file' for name, q in left.items())}


def verify_payload(raw, files):
    require(len(raw) == 330066 and sha256(raw).hexdigest() == PAYLOAD_SHA, 'payload_manifest_changed')
    manifest = document(raw)
    require(manifest.get('schema') == 'security_consumer_payload/1'
            and manifest.get('payload_tree') == '02553c4ba0b6ee8032e6f1ad1083c9df6cebb70c'
            and manifest.get('source_commit') == COMMON.SOURCE and manifest.get('baseline_commit') == COMMON.BASELINE
            and len(manifest.get('files', [])) == 1450, 'payload_manifest_identity')
    # The sealed manifest describes BUILD INPUTS. Frontend source, recipes,
    # .dockerignore and bootstrap requirements are not serving image files.
    # Preserve their manifest identity without pretending they are installed.
    roots = {'api', 'bot', 'core', 'src', 'migrations', 'models', 'templates', 'fonts', 'scripts'}
    direct = {'requirements.txt', 'alembic.ini', 'main.py', 'manage.py', 'run_bot.py',
              'schemas.py', 'seed_fake_data.py', 'trading_settings.json'}
    installed = [q for q in manifest['files'] if q['path'].split('/')[0] in roots or q['path'] in direct]
    source = [q for q in installed if q['path'].endswith('.py')]
    require(len(source) == 857 and sum(q['bytes'] for q in source) == 13194027, 'payload_python_inventory')
    for row in installed:
        actual = files['entries'].get('/app/' + row['path'])
        require(type(actual) is list and len(actual) == 6 and actual[0] == 'file'
                and actual[4:] == [row['bytes'], row['sha256']], 'payload_file_mismatch')
    return {'manifest_sha256': PAYLOAD_SHA, 'sealed_build_input_files': 1450, 'installed_source_files_verified': len(installed),
            'python_source_files_verified': 857, 'python_source_bytes': 13194027,
            'build_inputs_not_installed': 1450 - len(installed), 'frontend_dist_preserved_by_complete_filesystem_parity': True}


def evidence_hashes(root):
    names = ('parent-admission.json', 'input-signature.json', 'baseline-inventory.json', 'candidate-inventory.json',
             'baseline-files.json', 'candidate-files.json', 'filesystem-parity.json', 'focused-tests.json',
             'component-presence.json', 'acl-consumer-presence.json', 'base-alias-plan.json',
             'before-run.log', 'after-run.log', 'files-before-run.log', 'files-after-run.log', 'native-run.log',
             'focused-run.log', 'build.log', 'builder-inspect.log', 'acl-run.log')
    return {name: sha256(read(root / 'reports' / name, 32 * 1024**2)).hexdigest() for name in names}


def compose(root):
    ci(root)
    with sources(root) as identity:
        for name in ('inputs', 'reports', 'context', 'tests', 'docker-config'):
            (root / name).mkdir(mode=0o700)
        available = os.statvfs(root)
        require(available.f_bavail * available.f_frsize >= 8 * 1024**3, 'runner_space')
        phase('parent_evidence')
        COMMON.fetch_artifact(12573199226, root / 'inputs/evidence.zip', 248816, EVIDENCE_SHA)
        evidence = admit_evidence(root / 'inputs/evidence.zip')
        phase('parent_image_admission')
        COMMON.fetch_artifact(IMAGE_ARTIFACT[0], root / 'inputs/image.zip', IMAGE_ARTIFACT[1], IMAGE_ARTIFACT[2])
        parent = admit_parent(root / 'inputs/image.zip', root, evidence)
        write(root / 'reports/parent-admission.json', encoded({k: v for k, v in parent.items() if k != 'config'}))
        phase('parent_load')
        with pinned(root / 'parent.tar', MAX_TAR) as (stream, size):
            require(size == TAR_BYTES and digest(stream, size) == TAR_SHA, 'parent_archive_changed')
            stream.seek(0)
            COMMON.docker(root, ['image', 'load'], seconds=180, input_stream=stream)
        phase('parent_inspect')
        before = inspect_binding(root, PARENT, parent)
        inputs, input_sha = signature(identity, before['RootFS']['Layers'])
        write(root / 'reports/input-signature.json', encoded(inputs))
        alias = 'security-app-mountpoints-' + owner() + ':ci'
        plan = {'schema': 'app_mountpoint_alias/1', 'name': alias, 'image_id': before['Id'], 'parent_config_id': PARENT, 'owner': owner()}
        write(root / 'reports/base-alias-plan.json', encoded(plan))
        phase('owned_alias')
        COMMON.docker(root, ['image', 'tag', before['Id'], alias])
        require(inspect_binding(root, alias, parent)['Config'] == before['Config'], 'parent_alias_changed')
        phase('builder')
        COMMON.inspect_builder(root)
        write(root / 'context/Dockerfile', read(root / 'source/Dockerfile', 131072), 0o644)
        phase('build')
        COMMON.docker(root, ['build', '--builder', 'default', '--pull=false', '--no-cache', '--network=none',
                            '--build-arg', 'APP_SECURITY_PARENT=' + alias,
                            '--build-arg', 'SECURITY_MOUNTPOINT_SOURCE_COMMIT=' + identity['recipe_commit'],
                            '--build-arg', 'SECURITY_MOUNTPOINT_INPUT_SIGNATURE=' + input_sha,
                            '--iidfile', str(root / 'candidate.id'), str(root / 'context')],
                      seconds=600, maximum=32 * 1024**2, combine_stderr=True, log_path=root / 'reports/build.log')
        image = read(root / 'candidate.id', 256).decode().strip()
        require(re.fullmatch('sha256:[a-f0-9]{64}', image), 'candidate_image_id')
        phase('candidate_config')
        after = COMMON.image_record(COMMON.docker(root, ['image', 'inspect', image], maximum=MAX_JSON), image)
        compare_images(before, after, identity['recipe_commit'], input_sha)
        inventories, files = [], []
        for candidate, purpose in ((before['Id'], 'before'), (image, 'after')):
            phase('inventory_' + purpose)
            inventory = document(COMMON.isolated(root, candidate, COMMON.AUDIT, purpose).splitlines()[-1])
            write(root / 'reports' / ('baseline-inventory.json' if purpose == 'before' else 'candidate-inventory.json'), encoded(inventory))
            inventories.append(inventory)
            manifest = document(COMMON.isolated(root, candidate, FILES, 'files-' + purpose, maximum=16 * 1024**2).splitlines()[-1])
            write(root / 'reports' / ('baseline-files.json' if purpose == 'before' else 'candidate-files.json'), encoded(manifest))
            files.append(manifest)
        require(inventories[0]['inventory'] == inventories[1]['inventory']
                and inventories[0]['libc_sha256'] == inventories[1]['libc_sha256']
                and inventories[0]['bytes'] == inventories[1]['bytes']
                and inventories[1]['files'] == inventories[0]['files'] + 3, 'package_or_runtime_changed')
        phase('complete_filesystem_parity')
        parity = compare_files(*files)
        parity['payload'] = verify_payload(read(root / 'source/payload_manifest.json', 512*1024), files[1])
        write(root / 'reports/filesystem-parity.json', encoded(parity))
        phase('native_checks')
        COMMON.isolated(root, image, COMMON.NATIVE, 'native')
        phase('historical_tests')
        COMMON.fetch_artifact(COMMON.EVIDENCE[0], root / 'inputs/tests.zip', COMMON.EVIDENCE[1], COMMON.EVIDENCE[2])
        proof = COMMON.admit_test_proof(root / 'inputs/tests.zip')
        COMMON.fetch_tests(root / 'tests')
        (root / 'tests').chmod(0o755)
        write(root / 'test-sources.json', proof, 0o644)
        focused = COMMON.isolated(root, image, COMMON.FOCUSED, 'focused',
                                  mounts=((root / 'tests', '/app/tests'), (root / 'test-sources.json', '/proof/test-sources.json')))
        result = document(focused.splitlines()[-1])
        require(result.get('image_id') == image and result.get('tests') == 56 and result.get('modules') == 5
                and result.get('status') == 'passed', 'historical_tests')
        write(root / 'reports/focused-tests.json', encoded(result))
        phase('component_presence')
        component = COMPONENT.in_container(image, COMMON.SOURCE, read(root / 'source/verify_image_component_presence.py'), COMMON.PROBE_SHA)
        COMMON.validate_component(component, image)
        write(root / 'reports/component-presence.json', component)
        phase('acl_presence')
        COMMON.acl_consumer_probe(root, image, identity['recipe_commit'])
        phase('bind_composition')
        record = {'schema': 'app_mountpoint_composition/1', 'status': 'tested_not_accepted', 'image_id': image,
                  'parent_config_id': PARENT, 'parent_daemon_id': before['Id'], 'recipe': identity,
                  'image_input_signature': input_sha, 'parent_layers': before['RootFS']['Layers'],
                  'candidate_layers': after['RootFS']['Layers'], 'app_root': files[0]['app_root'],
                  'directory_inclusive_audit_before': inventories[0]['app_and_python_sha256'],
                  'directory_inclusive_audit_after': inventories[1]['app_and_python_sha256'],
                  'historical_tests': 56, 'full_runtime_matrix': 'NOT_TESTED', 'physical_new_layer_verified': False,
                  'runtime_guards_changed': False, 'runtime_data_used': False, 'app_recompiled': False,
                  'security_accepted': False, 'findings_waived': False, 'deployment_performed': False,
                  'evidence_sha256': evidence_hashes(root)}
        write(root / 'reports/composition.json', encoded(record))
        for name in ('vulnerabilities.json', 'sbom.cdx.json'):
            write(root / 'reports' / name, b'')
        write(root / 'empty-trivy.yaml', b'{}\n')
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
            output.write('image_id=' + image + '\n')


def finalize(root):
    ci(root)
    with sources(root) as identity:
        original = read(root / 'reports/composition.json')
        record = document(original)
        require(record.get('schema') == 'app_mountpoint_composition/1' and record.get('recipe') == identity
                and record.get('security_accepted') is False and record.get('physical_new_layer_verified') is False
                and record.get('evidence_sha256') == evidence_hashes(root), 'composition_state')
        phase('recheck_parent_archive')
        parent_evidence = admit_evidence(root / 'inputs/evidence.zip')
        with pinned(root / 'parent.tar', MAX_TAR) as (stream, size):
            parent_proof = validate_archive(stream, size, PARENT, parent_evidence['scanner_config'])
        require({k: v for k, v in parent_proof.items() if k != 'config'} == document(read(root / 'reports/parent-admission.json')),
                'parent_archive_changed')
        before = inspect_binding(root, record['parent_daemon_id'], parent_proof)
        after = COMMON.image_record(COMMON.docker(root, ['image', 'inspect', record['image_id']], maximum=MAX_JSON), record['image_id'])
        compare_images(before, after, identity['recipe_commit'], record['image_input_signature'])
        require(after['RootFS']['Layers'] == record['candidate_layers'], 'image_drift')
        phase('raw_report_identity')
        reports = {name: read(root / 'reports' / name, 32 * 1024**2) for name in ('vulnerabilities.json', 'sbom.cdx.json')}
        raw, sbom = (document(reports[name]) for name in ('vulnerabilities.json', 'sbom.cdx.json'))
        require(raw.get('SchemaVersion') == 2 and raw.get('ArtifactType') == 'container_image'
                and raw.get('Metadata', {}).get('ImageID') == record['image_id']
                and type(raw.get('Results')) is list and raw['Results'], 'raw_report_identity')
        component = sbom.get('metadata', {}).get('component', {})
        require(component.get('type') == 'container' and component.get('name') == record['image_id']
                and [q.get('value') for q in component.get('properties', []) if q.get('name') == 'aquasecurity:trivy:ImageID'] == [record['image_id']],
                'sbom_image_identity')
        counts = {key: 0 for key in ('UNKNOWN', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')}
        for result in raw['Results']:
            for finding in result.get('Vulnerabilities') or []:
                require(finding.get('Severity') in counts, 'raw_severity')
                counts[finding['Severity']] += 1
        phase('immutable_export')
        archive = COMMON.export_image(root, record['image_id'])
        with pinned(root / 'candidate-image.tar.gz', MAX_TAR + 16 * 1024**2) as (stream, size):
            require(size == archive['size_bytes'] and digest(stream, size) == archive['sha256'], 'export_changed')
            stream.seek(0)
            decode_gzip(stream, size, root / 'export-check.tar', archive['uncompressed_bytes'])
        phase('physical_directory_layer')
        with pinned(root / 'export-check.tar', MAX_TAR) as (stream, size):
            exported = validate_archive(stream, size, record['image_id'], raw['Metadata']['ImageConfig'], last_layer=record['app_root'])
        require(exported['layers'] == 33 and exported['config']['rootfs']['diff_ids'] == record['candidate_layers']
                and PRIOR.same_json_value(PRIOR.oci_runtime_config(exported['config']['config']), PRIOR.oci_runtime_config(after['Config']))
                and record['evidence_sha256'] == evidence_hashes(root), 'export_scope')
        phase('bind_export')
        write(root / 'reports/export-scope.json', encoded({k: v for k, v in exported.items() if k != 'config'}))
        write(root / 'image-export.json', encoded({'schema': 'app_mountpoint_image_export/1', 'status': 'exported_not_accepted',
            'image_id': record['image_id'], 'parent_config_id': PARENT, 'parent_run_id': PARENT_RUN,
            'recipe': identity, 'image_input_signature': record['image_input_signature'],
            'composition_sha256': sha256(original).hexdigest(), 'evidence_sha256': record['evidence_sha256'],
            'reports_sha256': {name: sha256(value).hexdigest() for name, value in reports.items()},
            'export_scope_sha256': sha256(read(root / 'reports/export-scope.json')).hexdigest(),
            'archive': archive, 'raw_counts': counts, 'historical_tests': 56,
            'new_directories_only': True, 'exact_32_layer_prefix': True, 'complete_existing_files_equal': True,
            'physical_new_layer_verified': True, 'runtime_guards_changed': False, 'runtime_data_used': False,
            'full_runtime_matrix': 'NOT_TESTED', 'security_accepted': False, 'findings_waived': False,
            'deployment_eligible': False, 'deployment_performed': False}))


def cleanup(root):
    ci(root)
    with sources(root):
        path = root / 'reports/base-alias-plan.json'
        if not path.exists():
            require(not (root / 'reports/composition.json').exists() and not (root / 'image-export.json').exists(), 'alias_plan_missing')
            return
        plan = document(read(path, 65536))
        require(plan.get('schema') == 'app_mountpoint_alias/1' and plan.get('owner') == owner()
                and plan.get('name') == 'security-app-mountpoints-' + owner() + ':ci'
                and plan.get('parent_config_id') == PARENT, 'alias_cleanup_scope')
        parent_evidence = admit_evidence(root / 'inputs/evidence.zip')
        with pinned(root / 'parent.tar', MAX_TAR) as (stream, size):
            parent = validate_archive(stream, size, PARENT, parent_evidence['scanner_config'])
        before = inspect_binding(root, plan['name'], parent)
        require(before['Id'] == plan['image_id'] and plan['name'] in (before.get('RepoTags') or []), 'alias_cleanup_identity')
        COMMON.docker(root, ['image', 'rm', '--no-prune', plan['name']])
        # Last-tag removal can also retire the parent's local image record.
        # No consumer remains; require only this exact alias's absence. Do not
        # reload, retag, prune or claim the parent's image record was retained.
        remaining = COMMON.docker(root, ['image', 'ls', '--all', '--quiet', '--no-trunc',
                                        '--filter', 'reference=' + plan['name']], maximum=1024)
        require(not remaining.strip(), 'alias_cleanup_incomplete')
        write(root / 'reports/base-alias-cleanup.json', encoded({'schema': 'app_mountpoint_alias_cleanup/1',
            'status': 'removed_exact_owned_alias', 'name': plan['name'], 'parent_config_id': PARENT, 'security_accepted': False}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('compose', 'finalize', 'cleanup', 'gate'))
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == 'gate':
            ci(args.root)
            record = document(read(args.root / 'image-export.json'))
            require(record.get('schema') == 'app_mountpoint_image_export/1' and record.get('security_accepted') is False, 'raw_gate_receipt')
            require(record['raw_counts']['CRITICAL'] == record['raw_counts']['HIGH'] == 0, 'raw_gate_failed')
        else:
            {'compose': compose, 'finalize': finalize, 'cleanup': cleanup}[args.action](args.root)
        return 0
    except Exception as error:
        # Only declared literal codes are public; never exception operands/config.
        allowed = PUBLIC_CODES | HELPER_CODES | (COMMON.PUBLIC_FAILURE_CODES if COMMON is not None else frozenset())
        code = error.args[0] if type(error) is RuntimeError and len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in allowed else 'unexpected_failure'
        failure = {'schema': 'app_mountpoint_failure/1', 'action': args.action, 'phase': PHASE, 'code': code,
                   'security_accepted': False, 'deployment_performed': False}
        print(json.dumps(failure), file=sys.stderr)
        try:
            ci(args.root)
            write(args.root / 'reports' / ('failure-' + args.action + '.json'), encoded(failure))
        except Exception:
            pass
        return 1


# Explicit static inventory, audited by tests before publication.
PUBLIC_CODES = frozenset({
    'duplicate_json_key', 'invalid_json_constant', 'input_path', 'input_metadata', 'input_changed', 'input_bound',
    'stream_bound', 'stream_size', 'hosted_ci_only', 'ci_root', 'ci_identity', 'source_inventory', 'sealed_source_changed',
    'controller_source_changed', 'recipe_changed', 'helper_changed', 'parent_evidence_zip', 'parent_evidence_inventory',
    'parent_evidence_member', 'parent_evidence_bound', 'parent_evidence_hash', 'parent_composition', 'parent_alias_not_cleaned',
    'parent_layers', 'gzip_bound', 'gzip_size', 'directory_layer_bound', 'directory_layer_short', 'directory_layer_trailer',
    'directory_layer_outside_scope', 'directory_layer_metadata', 'directory_layer_mode', 'directory_layer_incomplete',
    'archive_size', 'archive_trailer', 'archive_member', 'archive_trailing_payload', 'archive_missing', 'archive_json_bound',
    'archive_manifest', 'archive_config_identity', 'archive_platform', 'archive_layers', 'archive_tags', 'archive_expansion',
    'archive_layer_diff_id', 'oci_layout', 'oci_descriptor', 'oci_blob', 'oci_index', 'oci_manifest', 'oci_depth',
    'classic_layout', 'classic_version', 'classic_metadata', 'classic_repositories', 'archive_closed_inventory', 'legacy_bytes',
    'parent_image_zip', 'parent_zip_inventory', 'parent_zip_member', 'parent_export', 'parent_gzip_record',
    'parent_zip_expansion', 'parent_gzip_identity', 'parent_archive_identity', 'daemon_image_shape', 'daemon_identity',
    'daemon_config_identity', 'parent_user_onbuild', 'input_signature', 'parent_label_collision', 'whole_runtime_config_changed',
    'exact_layer_prefix_changed', 'effective_filesystem_changed', 'runner_space', 'parent_archive_changed', 'parent_alias_changed',
    'candidate_image_id', 'package_or_runtime_changed', 'historical_tests', 'composition_state', 'image_drift', 'raw_report_identity',
    'sbom_image_identity', 'raw_severity', 'export_changed', 'export_scope', 'alias_cleanup_scope', 'alias_cleanup_identity',
    'alias_cleanup_incomplete', 'raw_gate_receipt', 'raw_gate_failed', 'unexpected_failure',
    'alias_plan_missing',
    'payload_manifest_changed', 'payload_manifest_identity', 'payload_python_inventory', 'payload_file_mismatch',
})

if __name__ == '__main__':
    raise SystemExit(main())
