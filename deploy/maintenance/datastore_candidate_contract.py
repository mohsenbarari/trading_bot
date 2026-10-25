#!/usr/bin/env python3
"""Fixed PostgreSQL/Redis public artifact contracts; never activate a service.

Image Config is an anonymous in-memory comparison operand. Only public digests,
counts and typed archive identities may leave this module.
"""
from collections import Counter
from contextlib import contextmanager
from hashlib import sha256
import io
import gzip
import json
import os
from pathlib import Path
import re
import stat
import time
from types import ModuleType
import zipfile

MAX_JSON = 2 * 1024**2
SEVERITIES = ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN')
HELPERS = {
    'runtime_image_archive_transport.py': '20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2',
    'admit_security_component_image_reuse.py': '9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025',
}
HEX = re.compile('[a-f0-9]{64}')
DIGEST = re.compile('sha256:[a-f0-9]{64}')
MEDIA = {'application/vnd.oci.image.layer.v1.tar': False,
         'application/vnd.oci.image.layer.v1.tar+gzip': True,
         'application/vnd.docker.image.rootfs.diff.tar': False,
         'application/vnd.docker.image.rootfs.diff.tar.gzip': True}
OCI_MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
OCI_INDEX = 'application/vnd.oci.image.index.v1+json'


class Refusal(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Refusal(code)


def error_code(error):
    return error.args[0] if isinstance(error, RuntimeError) and len(error.args) == 1 \
        and type(error.args[0]) is str and error.args[0] in PUBLIC_FAILURE_CODES else 'unexpected_failure'


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def document(raw):
    need(type(raw) is bytes and 0 < len(raw) <= MAX_JSON, 'control_bound')
    def unique(rows):
        result = {}
        for key, value in rows:
            need(key not in result, 'duplicate_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda _: need(False, 'json_constant'))


def same(a, b):
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return set(a) == set(b) and all(same(a[k], b[k]) for k in a)
    if type(a) is list:
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


@contextmanager
def held(path, maximum, pin=None, size=None, *, expected_uid=0, expected_gid=0):
    path = Path(path)
    need(path.is_absolute() and path.resolve(strict=True) == path, 'input_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(type(expected_uid) is int and expected_uid >= 0 and type(expected_gid) is int and expected_gid >= 0
             and stat.S_ISREG(before.st_mode) and before.st_uid == expected_uid and before.st_gid == expected_gid
             and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600
             and 0 < before.st_size <= maximum and (size is None or before.st_size == size), 'input_metadata')
        def check():
            need(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'input_changed')
        check()
        if pin is not None:
            need(HEX.fullmatch(pin) and digest_fd(fd, before.st_size, time.monotonic() + 600) == pin, 'input_sha256')
        check()
        yield fd, before.st_size, check
        check()
    finally:
        os.close(fd)


def digest_fd(fd, size, end):
    value, offset = sha256(), 0
    while offset < size:
        need(time.monotonic() < end, 'deadline')
        raw = os.pread(fd, min(65536, size - offset), offset)
        need(raw, 'input_short')
        value.update(raw); offset += len(raw)
    need(not os.pread(fd, 1, size), 'input_growth')
    return value.hexdigest()


def read(path, maximum=MAX_JSON, pin=None):
    with held(path, maximum, pin) as (fd, size, check):
        raw = os.pread(fd, size + 1, 0)
        need(len(raw) == size, 'input_short'); check()
        return raw


def sealed(path, pin):
    raw = read(path, 131072, pin)
    module = ModuleType('_sealed_datastore_' + Path(path).stem)
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def counters(value):
    need(type(value) is dict and set(value) <= set(SEVERITIES)
         and all(type(v) is int and 0 <= v <= 1000000 for v in value.values()), 'raw_counter')
    return {k: value.get(k, 0) for k in SEVERITIES}


def export_record(value, spec):
    keys = {'archive', 'candidate_receipt_sha256', 'evidence_sha256', 'full_role_matrix',
            'gosu_dependency_security_overlay', 'gosu_windows_advisory_scope', 'image_id',
            'libc_vendor_security_revision', 'parent_config_id', 'parent_reference',
            'production_data_used', 'production_installed', 'profile', 'raw_counts',
            'reports_sha256', 'scanner_gate_waived', 'schema', 'security_accepted', 'status'}
    need(type(value) is dict and set(value) == keys, 'export_fields')
    need(value['schema'] == 'alpine_datastore_security_export/1' and value['status'] == 'exported_not_accepted'
         and value['image_id'] == spec['config_id'] and value['profile'] == spec['profile']
         and value['candidate_receipt_sha256'] == spec['candidate_sha256']
         and value['full_role_matrix'] == 'NOT_TESTED'
         and all(value[k] is False for k in ('production_data_used', 'production_installed',
                                            'scanner_gate_waived', 'security_accepted')), 'export_identity')
    need(counters(value['raw_counts']) == spec['raw_counts'], 'export_counter')
    need(same(value['archive'], spec['archive']), 'export_archive')
    for mapping in (value['reports_sha256'], value['evidence_sha256']):
        need(type(mapping) is dict and mapping and all(type(k) is str and '/' not in k
             and type(v) is str and HEX.fullmatch(v) for k, v in mapping.items()), 'export_report_bindings')
    return value


def evidence(raw, spec):
    """Consume only a pinned public CI ZIP; return Config solely in RAM."""
    need(type(raw) is bytes and len(raw) == spec['evidence_bytes']
         and sha256(raw).hexdigest() == spec['evidence_sha256'], 'evidence_zip')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        need(len(infos) <= 64 and len({i.filename for i in infos}) == len(infos)
             and all(not i.is_dir() and i.file_size <= 4*MAX_JSON for i in infos), 'evidence_inventory')
        def item(name, pin=None):
            info = archive.getinfo(name)
            need(info.file_size <= MAX_JSON, 'evidence_member_bound')
            data = archive.read(info)
            need(len(data) == info.file_size and (pin is None or sha256(data).hexdigest() == pin), 'evidence_member_sha')
            return document(data), sha256(data).hexdigest()
        exported, _ = item('image-export.json', spec['export_sha256'])
        export_record(exported, spec)
        candidate, _ = item('reports/candidate.json', spec['candidate_sha256'])
        image, _ = item('reports/candidate-image.json', spec['candidate_image_sha256'])
        report, _ = item('reports/vulnerabilities.json', spec['raw_sha256'])
        item('reports/sbom.cdx.json', spec['sbom_sha256'])
        source, _ = item('source/identity.json')
        need(source.get('source_commit', source.get('commit', source.get('recipe_commit')))
             == 'ecfb667ee1583168f2777a6fa5f54766a63ef787', 'evidence_recipe')
        for mapping in (exported['evidence_sha256'], exported['reports_sha256']):
            for name, pin in mapping.items():
                info = archive.getinfo('reports/' + name)
                need(info.file_size <= 4*MAX_JSON and sha256(archive.read(info)).hexdigest() == pin, 'evidence_report_sha')
        need(candidate['schema'] == 'alpine_datastore_security_candidate/1'
             and candidate['status'] == 'built_tested_not_accepted' and candidate['profile'] == spec['profile']
             and candidate['image_id'] == image['Id'] == spec['config_id']
             and candidate['server_bytes_unchanged'] is True and candidate['whole_runtime_config_unchanged'] is True
             and candidate['full_role_matrix'] == 'NOT_TESTED'
             and candidate['production_data_used'] is False and candidate['production_installed'] is False
             and candidate['security_accepted'] is False and candidate['scanner_gate_waived'] is False,
             'candidate_receipt')
        need(image['Os'] == 'linux' and image['Architecture'] == 'amd64'
             and same(image['RootFS'], {'Type': 'layers', 'Layers': spec['layers']})
             and same(candidate['candidate_layers'], spec['layers'])
             and same(image['Config'], candidate['runtime_config']), 'candidate_image')
        rows = [row for result in report['Results'] for row in result.get('Vulnerabilities') or []]
        need(report['Metadata']['ImageID'] == spec['config_id']
             and counters(dict(Counter(row['Severity'] for row in rows))) == spec['raw_counts'], 'raw_report')
        need(not rows if spec['profile'] == 'redis' else len(rows) == 1
             and rows[0]['VulnerabilityID'] == 'CVE-2026-39824'
             and rows[0]['PkgName'] == 'golang.org/x/sys' and rows[0]['InstalledVersion'] == 'v0.1.0'
             and rows[0]['Severity'] == 'UNKNOWN', 'raw_scope')
        return {'spec': spec, 'image': image, 'export': exported}


def container_config(value=None):
    """Moby v28.5.1 struct order/defaults, including absent optional API keys."""
    defaults = {'Hostname': '', 'Domainname': '', 'User': '', 'AttachStdin': False,
        'AttachStdout': False, 'AttachStderr': False, 'ExposedPorts': None, 'Tty': False,
        'OpenStdin': False, 'StdinOnce': False, 'Env': None, 'Cmd': None, 'Healthcheck': None,
        'ArgsEscaped': False, 'Image': '', 'Volumes': None, 'WorkingDir': '', 'Entrypoint': None,
        'NetworkDisabled': False, 'MacAddress': '', 'OnBuild': None, 'Labels': None,
        'StopSignal': '', 'StopTimeout': None, 'Shell': None}
    value = {} if value is None else value
    need(type(value) is dict and set(value) <= set(defaults), 'config_fields')
    data = {**defaults, **value}
    for name in ('Hostname', 'Domainname', 'User', 'Image', 'WorkingDir', 'MacAddress', 'StopSignal'):
        need(type(data[name]) is str, 'config_type')
    for name in ('AttachStdin', 'AttachStdout', 'AttachStderr', 'Tty', 'OpenStdin', 'StdinOnce', 'ArgsEscaped', 'NetworkDisabled'):
        need(type(data[name]) is bool, 'config_type')
    for name in ('Env', 'Cmd', 'Entrypoint', 'OnBuild', 'Shell'):
        need(data[name] is None or type(data[name]) is list and all(type(v) is str for v in data[name]), 'config_type')
    for name in ('ExposedPorts', 'Volumes'):
        need(data[name] is None or type(data[name]) is dict and all(type(k) is str and type(v) is dict and not v for k, v in data[name].items()), 'config_type')
    need(data['Labels'] is None or type(data['Labels']) is dict and all(type(k) is str and type(v) is str for k, v in data['Labels'].items()), 'config_type')
    need(data['Healthcheck'] is None and (data['StopTimeout'] is None or type(data['StopTimeout']) is int and 0 <= data['StopTimeout'] <= 60), 'config_type')
    optional = {'ExposedPorts', 'Healthcheck', 'ArgsEscaped', 'NetworkDisabled', 'MacAddress', 'StopSignal', 'StopTimeout', 'Shell'}
    result = {}
    for key, value in data.items():
        if key in optional and (value is None or value is False or value == '' or type(value) in (list, dict) and not value):
            continue
        result[key] = dict(sorted(value.items())) if type(value) is dict else value
    return result


def legacy_configs(config, moby):
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
         and 0 < len(config['rootfs']['diff_ids']) <= 32, 'legacy_rootfs')
    final = {}
    for key in ('comment',):
        need(type(config.get(key, '')) is str, 'legacy_metadata')
        if config.get(key): final[key] = config[key]
    final.update({'created': created})
    for key in ('container',):
        need(type(config.get(key, '')) is str, 'legacy_metadata')
        if config.get(key): final[key] = config[key]
    final['container_config'] = container_config(config.get('container_config'))
    for key in ('docker_version', 'author'):
        need(type(config.get(key, '')) is str, 'legacy_metadata')
        if config.get(key): final[key] = config[key]
    final.update({'config': container_config(config['config']), 'architecture': 'amd64', 'os': 'linux'})
    expected, chain, parent = {}, None, None
    for index, diff in enumerate(config['rootfs']['diff_ids']):
        need(type(diff) is str and DIGEST.fullmatch(diff), 'legacy_rootfs')
        chain = diff if chain is None else 'sha256:' + sha256((chain + ' ' + diff).encode()).hexdigest()
        value = final if index == len(config['rootfs']['diff_ids']) - 1 else {'created': '1970-01-01T00:00:00Z', 'container_config': container_config()}
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


def expected_inspect_comment(config):
    """Moby v28.5.1 ImageInspect's exact Comment projection, not OCI mutation.

    https://github.com/moby/moby/blob/v28.5.1/daemon/images/image_inspect.go#L41
    Nonempty img.Comment wins; only its empty value falls back to the LAST
    history entry's Comment. No earlier matching entry can establish identity.
    """
    need(type(config) is dict, 'archive_image_metadata')
    comment, history = config.get('comment', ''), config.get('history', [])
    need(type(comment) is str and type(history) is list
         and all(type(row) is dict and type(row.get('comment', '')) is str for row in history), 'archive_image_metadata')
    return comment if comment else history[-1].get('comment', '') if history else ''


def validate_archive(stream, size, end, data, transport, moby):
    """Authenticate source archive bytes; no daemon observation is invented."""
    spec, image = data['spec'], data['image']
    need(size == spec['archive']['uncompressed_bytes'], 'archive_size')
    reader = transport.ArchiveReader(stream, size, min(end, time.monotonic() + 599))
    members, tail = transport.inventory(reader)
    def raw(name, maximum=MAX_JSON):
        need(name in members and 0 < members[name][1] <= maximum, 'archive_control')
        offset, length = members[name]; reader.seek(offset)
        return reader.read(length)
    def control(name): return document(raw(name))
    def blob(descriptor):
        need(type(descriptor) is dict and type(descriptor.get('digest')) is str and DIGEST.fullmatch(descriptor['digest'])
             and type(descriptor.get('size')) is int and descriptor['size'] > 0, 'archive_descriptor')
        name = 'blobs/sha256/' + descriptor['digest'][7:]
        need(name in members and members[name][1] == descriptor['size'], 'archive_descriptor')
        reader.seek(members[name][0]); need(transport.digest(reader, descriptor['size']) == descriptor['digest'][7:], 'archive_blob_sha')
        return name
    index_raw = raw('index.json'); index = document(index_raw)
    need(control('oci-layout') == {'imageLayoutVersion': '1.0.0'} and type(index) is dict
         and type(index.get('schemaVersion')) is int and index['schemaVersion'] == 2
         and index.get('mediaType', OCI_INDEX) == OCI_INDEX and type(index.get('manifests')) is list
         and len(index['manifests']) == 1, 'archive_index')
    entry = index['manifests'][0]
    need(entry.get('mediaType') == OCI_MANIFEST, 'archive_manifest_media')
    manifest_name = blob(entry); manifest_raw = raw(manifest_name); manifest = document(manifest_raw)
    need(type(manifest) is dict and manifest.get('schemaVersion') == 2 and type(manifest['schemaVersion']) is int
         and manifest.get('mediaType') == OCI_MANIFEST and manifest['config'].get('mediaType') == 'application/vnd.oci.image.config.v1+json', 'archive_manifest')
    config_name = blob(manifest['config']); config_raw = raw(config_name); config = document(config_raw)
    need('sha256:' + sha256(config_raw).hexdigest() == spec['config_id']
         and config['architecture'] == 'amd64' and config['os'] == 'linux'
         and same(config['rootfs'], {'type': 'layers', 'diff_ids': spec['layers']})
         and same(container_config(config['config']), container_config(image['Config'])), 'archive_config')
    need(type(image.get('Comment', '')) is str and expected_inspect_comment(config) == image.get('Comment', '')
         and all(type(config.get(source, '')) is str and type(image.get(target, '')) is str
                 and config.get(source, '') == image.get(target, '')
                 for source, target in (('author', 'Author'), ('docker_version', 'DockerVersion'))), 'archive_image_metadata')
    descriptors = manifest['layers']; need(type(descriptors) is list and len(descriptors) == len(spec['layers']), 'archive_layers')
    allowed = {'manifest.json', 'index.json', 'oci-layout', manifest_name, config_name}
    names, checked, expanded = [], {}, 0
    for expected, descriptor in zip(spec['layers'], descriptors):
        media = descriptor.get('mediaType'); need(media in MEDIA, 'archive_layer_media')
        name = blob(descriptor); names.append(name); allowed.add(name)
        if (name, media) not in checked:
            offset, length = members[name]; reader.seek(offset)
            stored, diff, length = transport.layer_digest(reader, length, MEDIA[media], 512*1024**2 - expanded)
            need(stored == descriptor['digest'][7:], 'archive_layer_sha'); checked[(name, media)] = (diff, length)
        diff, length = checked[(name, media)]; expanded += length
        need(expected == 'sha256:' + diff and expanded <= 512*1024**2, 'archive_diff_id')
    docker = control('manifest.json')
    need(type(docker) is list and len(docker) == 1 and type(docker[0]) is dict
         and {'Config', 'Layers'} <= set(docker[0]) <= {'Config', 'Layers', 'RepoTags', 'LayerSources'}
         and docker[0]['Config'] == config_name and docker[0]['Layers'] == names
         and docker[0].get('RepoTags') in (None, []), 'archive_docker_manifest')
    extras = set(members) - allowed - {'blobs', 'blobs/sha256'}
    legacy = legacy_configs(config, moby)
    if extras:
        need(extras == set(legacy), 'archive_closed_inventory')
        for name, value in legacy.items(): need(raw(name, 65536) == value, 'legacy_bytes')
        allowed.update(legacy)
    need(set(members) - {'blobs', 'blobs/sha256'} == allowed, 'archive_closed_inventory')
    transport._tail(stream, tail, size, end)
    reader.seek(0); archive_sha = transport.digest(reader, size)
    identities = {spec['config_id']: {'kind': 'Docker_config_identity'},
        entry['digest']: {'kind': 'OCI_manifest_to_config', 'media_type': OCI_MANIFEST, 'size': len(manifest_raw)},
        'sha256:' + sha256(index_raw).hexdigest(): {'kind': 'OCI_index_to_manifest_to_config', 'media_type': OCI_INDEX, 'size': len(index_raw)}}
    return {'schema': 'datastore_candidate_archive_admission/1', 'profile': spec['profile'],
        'config_id': spec['config_id'], 'rootfs_layers': spec['layers'], 'archive_sha256': archive_sha,
        'archive_bytes': size, 'expanded_layer_bytes': expanded, 'member_count': len(members),
        'legacy_compatibility_configs': len(extras), 'manifest_id': entry['digest'],
        'index_sha256': sha256(index_raw).hexdigest(),
        'source_identities': identities, 'physical_graph_closed': True, 'repo_tags_empty': True,
        'daemon_identity_measured': False, 'configuration_values_disclosed': False, 'security_accepted': False}


class Output:
    """One exclusive retained output; an interrupted path is never adopted."""
    def __init__(self, path, maximum, *, expected_uid=0, expected_gid=0):
        need(type(maximum) is int and maximum > 0 and type(expected_uid) is int and expected_uid >= 0
             and type(expected_gid) is int and expected_gid >= 0, 'output_parameters')
        self.path, self.maximum = Path(path), maximum
        self.expected_uid, self.expected_gid = expected_uid, expected_gid
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try: self.before = identity(os.fstat(self.fd))
        except BaseException:
            os.close(self.fd)
            raise

    def check(self):
        need(self.before == identity(os.fstat(self.fd)) == identity(self.path.lstat()), 'output_changed')
        need(self.before[2] == 0o100600 and self.before[3] == self.expected_uid and self.before[4] == self.expected_gid
             and self.before[5] == 1, 'output_metadata')

    def write(self, raw):
        need(type(raw) is bytes and len(raw) <= 65536, 'output_block_bound')
        self.check(); need(self.before[6] + len(raw) <= self.maximum, 'output_bound')
        view = memoryview(raw)
        while view:
            self.check(); count = os.write(self.fd, view)
            after = identity(os.fstat(self.fd))
            need(count > 0 and after[:6] == self.before[:6] and after[6] == self.before[6] + count
                 and after == identity(self.path.lstat()), 'output_short_or_changed')
            self.before = after; view = view[count:]

    def finish(self):
        self.check(); os.fsync(self.fd); self.check()
        return {'path': str(self.path), 'identity': list(self.before)}

    def close(self):
        os.close(self.fd)


class CompressedReader:
    """A borrowed gzip source, bounded before each underlying allocation/read."""
    def __init__(self, fd, size, end):
        self.fd, self.size, self.end, self.offset = fd, size, end, 0

    def read(self, count=-1):
        need(time.monotonic() < self.end and type(count) is int, 'deadline')
        count = min(65536, self.size - self.offset, self.size if count < 0 else count)
        raw = os.pread(self.fd, count, self.offset)
        need(len(raw) == count, 'compressed_short')
        self.offset += len(raw); return raw


def unpack_image(fd, size, output, data, transport, moby, end, capacity):
    """Pinned ZIP -> pinned gzip -> pinned TAR, with no extraction or LOAD."""
    spec = data['spec']
    need(size == spec['image_zip_bytes'] and digest_fd(fd, size, end) == spec['image_zip_sha256'], 'image_zip_identity')
    with os.fdopen(os.dup(fd), 'rb') as stream, zipfile.ZipFile(stream) as archive:
        infos = archive.infolist()
        need(len(infos) == 2 and {i.filename for i in infos} == {'image-export.json', 'candidate-image.tar.gz'}
             and all(not i.is_dir() and not i.flag_bits & 1
                     and stat.S_IFMT(i.external_attr >> 16) in (0, stat.S_IFREG) for i in infos), 'image_zip_inventory')
        info = archive.getinfo('image-export.json')
        need(info.file_size == spec['export_bytes'], 'image_export_bound')
        raw = archive.read(info)
        need(sha256(raw).hexdigest() == spec['export_sha256'], 'image_export_sha')
        export_record(document(raw), spec)
        info = archive.getinfo('candidate-image.tar.gz')
        need(info.file_size == spec['archive']['size_bytes'], 'image_gzip_bound')
        capacity(spec['archive']['size_bytes'] + spec['archive']['uncompressed_bytes'] + MAX_JSON)
        compressed = Output(Path(output) / 'candidate-image.tar.gz', info.file_size)
        try:
            value, total = sha256(), 0
            with archive.open(info) as source:
                while True:
                    need(time.monotonic() < end, 'deadline')
                    raw = source.read(min(65536, info.file_size + 1 - total))
                    if not raw: break
                    total += len(raw); need(total <= info.file_size, 'image_gzip_bound')
                    capacity(info.file_size - total + spec['archive']['uncompressed_bytes'] + MAX_JSON)
                    compressed.write(raw); value.update(raw)
            need(total == info.file_size and value.hexdigest() == spec['archive']['sha256'], 'image_gzip_sha')
            compressed.finish()
            tar = Output(Path(output) / 'candidate-image.tar', spec['archive']['uncompressed_bytes'])
            try:
                reader = CompressedReader(compressed.fd, total, end)
                with gzip.GzipFile(fileobj=reader, mode='rb') as decoder:
                    length = 0
                    while True:
                        need(time.monotonic() < end, 'deadline')
                        raw = decoder.read(min(65536, spec['archive']['uncompressed_bytes'] + 1 - length))
                        if not raw: break
                        length += len(raw); need(length <= spec['archive']['uncompressed_bytes'], 'tar_expansion_bound')
                        capacity(spec['archive']['uncompressed_bytes'] - length + MAX_JSON); tar.write(raw)
                need(length == spec['archive']['uncompressed_bytes'], 'tar_expansion_short')
                tar_binding = tar.finish(); compressed.check()
                with os.fdopen(os.dup(tar.fd), 'rb') as source:
                    proof = validate_archive(source, length, end, data, transport, moby)
                tar.check(); compressed.check()
                need(digest_fd(fd, size, end) == spec['image_zip_sha256'], 'image_zip_post_sha')
                tar_binding['sha256'] = proof['archive_sha256']
                return proof, tar_binding
            finally: tar.close()
        finally: compressed.close()


# Exact public CI identities; no runtime Config/Env values are embedded.
PROFILES = {'postgres': {'profile': 'postgres',
              'run_id': 40142076769,
              'recipe_commit': 'ecfb667ee1583168f2777a6fa5f54766a63ef787',
              'config_id': 'sha256:49a811b28ed9289d4e981c8f6cd36e1cf05259653a858e40df0cb6d6413c9aa1',
              'layers': ['sha256:34884abbe92863fce933ed7c39c0e045631af0ed86d5cc0dfbdf9fdca426ce3c',
                         'sha256:66b9f7f9f6d3928185e0c7cad648b4b841fdc2bc80eb2cb0029a9bc4bfe6a4de',
                         'sha256:a7e41a59116f2957c93dc08ecef69269070b27f237bd63fc0c0a6db072716237',
                         'sha256:9b19de80e7618a2353a2498eacd0c3aa775b8656d947745a8752ec1c03bd7739',
                         'sha256:9658b7b1393f1afecbea689d5c12ed9701dbbbcfe74c7bb10152e40cd3bd9aa1',
                         'sha256:0cde6740c33cf0ccc77b12e6961dc3c4a4d001117a1db9a96723bfb2895465ec',
                         'sha256:b1cd71796a42a83279af8905bd54a8733f05508355ddd81ad04e2a4e5b59b90f',
                         'sha256:daaf5f9bd329b43986a264faefa8563443b4810d9380ccbcff3f41cce961d683',
                         'sha256:ff7b413583a0d05e5dec20adf8e38685da7c0337fa8bc5fb8af4333323f5df43',
                         'sha256:ea2d2c2012fff2d0532da738c475ee0e9d335d135531275b8e3844ea5f15d385',
                         'sha256:363da9133d416185d21029bc178708bdf472186a612acab540f800233cd01cdb',
                         'sha256:e350ec1404ae4e743ceca8e08674a0cb15a6d798387ca50fe8e85dfea99cdc2f'],
              'raw_counts': {'CRITICAL': 0, 'HIGH': 0, 'LOW': 0, 'MEDIUM': 0, 'UNKNOWN': 1},
              'archive': {'format': 'docker-archive+gzip',
                          'name': 'candidate-image.tar.gz',
                          'sha256': 'b3b3cdf220b9bb373308ce5199b5eb13335cf39d5465cab96ce1bc69182186f3',
                          'size_bytes': 115441880,
                          'uncompressed_bytes': 303898112},
              'image_artifact_id': 12578982553,
              'image_zip_bytes': 114543515,
              'image_zip_sha256': '7d22f13f6cb225efe325c297e777536973f7de1082da12629ca2df44a1f601da',
              'evidence_bytes': 529854,
              'evidence_sha256': '5a56ff8c901fb7553b596cbab6d706360163e1de07b895a61a5baa4b964b361d',
              'export_sha256': '6e68ee177520cd4f1e2a7f1bab2c91ed8f9017d44f3a5e4d477cfc056f208e45',
              'export_bytes': 5302,
              'candidate_sha256': '7995bb380b621119b56e58c7b7ba063884ecb888e5d323c8fffbf5a85fcbdb60',
              'candidate_image_sha256': 'c0e60113f5599531bb9feec8f29297440fb74187aab685af583ceb378ad43729',
              'raw_sha256': '7ea249beb0c1d242257798b28b1d5bf12eb4796a869d37d1168f31a02a8f6891',
              'sbom_sha256': 'a6ef38a853ae3503bba099c3dc8bb7d2a5743b32b72d4ee9faea57df5dfd9d2c'},
 'redis': {'profile': 'redis',
           'run_id': 40142214416,
           'recipe_commit': 'ecfb667ee1583168f2777a6fa5f54766a63ef787',
           'config_id': 'sha256:98c4c0abd245b4b65178494e4727c69c63a28ed604cfb7dbf74205203a987015',
           'layers': ['sha256:7211d01aba8fa2e9eb71ad34395cd313df5fc51a6b69b92d2dfad32cc37759cb',
                      'sha256:a0caa8bc04fc2a6bc37012dfcb190e6e1711e18bb1bfdf3768bafe736da6001b',
                      'sha256:2898dc82d6a9e325f70948c4da63e430a8a57b5b556024653d4291db83077b58',
                      'sha256:a6f8bf15c6b1bd407aaaeb094b75e619c8a91905c6f4d7f2f6a07cabaf542875',
                      'sha256:fd2bae83d0a7a6d58ed98af0be51b6468c5a989bf457c4bc289bf64a72acfda5',
                      'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef',
                      'sha256:96bf91f53b18ebaf5f197fb649a488a4fc428521cbe86e6e73fe7eefda68a15d',
                      'sha256:eb6c47e3ebae287f79aeb070e14e61d5696acf049b9a71080a9f376ddb7c7b74'],
           'raw_counts': {'CRITICAL': 0, 'HIGH': 0, 'LOW': 0, 'MEDIUM': 0, 'UNKNOWN': 0},
           'archive': {'format': 'docker-archive+gzip',
                       'name': 'candidate-image.tar.gz',
                       'sha256': '59a62cfd9d2a00b6bde7eaa484a0c2187922456bd72351aab2f599e3a1033d9a',
                       'size_bytes': 18534734,
                       'uncompressed_bytes': 46384128},
           'image_artifact_id': 12578882888,
           'image_zip_bytes': 18444160,
           'image_zip_sha256': '2faa867c2f3863740e823f0e20f8dc867d9ce04f33367083580fdbfd4392b97d',
           'evidence_bytes': 29295,
           'evidence_sha256': '8d0624e966874d9da5c99a34eea50e061e2a68352263a9ef217e7efff46dfbc7',
           'export_sha256': 'c5e1e46600771119ad45904ffee872b3df998c1716d53db6237e721a18cb2a64',
           'export_bytes': 3485,
           'candidate_sha256': 'b8eaafe14f84d955b0ef804f05924b8734f86a34ded775b5baa85b096c4e9999',
           'candidate_image_sha256': '6cf1d65502f314761fb9a7aab01fc4ad79df821f56d63101980f87bb2289eb8e',
           'raw_sha256': '24baad7df9f74821f1cd2208a4872aed4cf4a7ba1e4810e8510395a76f261cb1',
           'sbom_sha256': 'e3c7580923150ead911a06696cc2a666863b0e6c25a6161a0f11f62a5255f342'}}


# Closed diagnostic labels from these pinned implementations; never exception values.
PUBLIC_FAILURE_CODES = frozenset(('admission_runtime_changed', 'admitted_source_required', 'archive_blob_sha', 'archive_bound', 'archive_closed_inventory', 'archive_config', 'archive_control', 'archive_descriptor', 'archive_diff_id', 'archive_docker_manifest', 'archive_identity', 'archive_image_metadata', 'archive_index', 'archive_layer_media', 'archive_layer_sha', 'archive_layers', 'archive_manifest', 'archive_manifest_media', 'archive_shape', 'archive_size', 'artifact_stream_extra', 'attempt_preexisting', 'baseline_binding', 'baseline_bindings', 'baseline_inventory', 'candidate_image', 'candidate_preexisting_or_map_changed', 'candidate_receipt', 'capacity_floor', 'client_outcome_unknown', 'command_timeout', 'compressed_short', 'config_fields', 'config_type', 'container_inventory_changed', 'container_projection', 'container_public_policy_changed', 'container_state', 'control_bound', 'control_changed', 'control_constant', 'control_metadata', 'control_path', 'control_sha256', 'data_root_changed', 'deadline', 'docker_store_changed', 'download_size_or_sha', 'duplicate_control_key', 'duplicate_key', 'evidence_inventory', 'evidence_member_bound', 'evidence_member_sha', 'evidence_recipe', 'evidence_report_sha', 'evidence_zip', 'existing_image_or_tag_changed', 'export_archive', 'export_counter', 'export_fields', 'export_identity', 'export_report_bindings', 'fence_changed', 'fence_fields', 'fence_identity', 'frame_bound', 'fresh_post_app_legacy_baseline_required', 'helper_pin', 'host_identity', 'image_export_bound', 'image_export_sha', 'image_gzip_bound', 'image_gzip_sha', 'image_map', 'image_zip_identity', 'image_zip_inventory', 'image_zip_post_sha', 'initial_image_map_changed', 'input_changed', 'input_growth', 'input_metadata', 'input_path', 'input_pin', 'input_sha256', 'input_short', 'json_constant', 'known_health_scope', 'legacy_bound', 'legacy_bytes', 'legacy_collision', 'legacy_created', 'legacy_image', 'legacy_metadata', 'legacy_rootfs', 'load_stdin_short', 'loaded_config_or_rootfs', 'loaded_descriptor_required', 'loaded_identity_unknown', 'native_attempt_not_adopted', 'native_audit_active', 'native_baseline_changed', 'native_candidate_preexisting', 'native_capacity', 'native_capacity_pressure', 'native_changed_before_request', 'native_changed_during_transfer', 'native_changed_during_web_load', 'native_config_or_rootfs', 'native_descriptor', 'native_existing_tags_changed', 'native_fences', 'native_host', 'native_image_map_changed', 'native_intent', 'native_inventory', 'native_launcher_required', 'native_load_client_failed', 'native_loaded_identity_or_runtime', 'native_mount', 'native_public_daemon_contract', 'native_public_temp_directive', 'native_root_capacity_pressure', 'native_root_required', 'native_root_temp_changed', 'native_root_temp_filesystem', 'native_root_temp_metadata', 'native_scope', 'native_source_scope', 'native_storage', 'native_store', 'native_store_identity', 'native_stream_sha', 'new_health_failure', 'new_image_identity', 'observation_shape', 'operation_changed', 'operation_clock', 'operation_constant', 'operation_duplicate_key', 'operation_expired', 'operation_metadata', 'operation_path', 'operation_scope', 'operation_sha256', 'output_block_bound', 'output_bound', 'output_changed', 'output_metadata', 'output_name', 'output_parameters', 'output_short_or_changed', 'owned_control_bound', 'owned_control_changed', 'owned_directory_changed', 'phase_not_verified', 'phase_scope', 'private_output_refused', 'public_command_failed', 'public_command_timeout', 'public_site_binding', 'public_unit_changed', 'public_unit_metadata', 'raw_counter', 'raw_report', 'raw_scope', 'report_identity', 'reservation_changed', 'reservation_cleanup_unknown', 'reservation_preexisting', 'reuse_archive_identity', 'reuse_archive_schema', 'reuse_config_digest', 'reuse_config_identity', 'reuse_docker_manifest_invalid', 'reuse_docker_manifest_missing', 'reuse_duplicate_json_key', 'reuse_export_clock', 'reuse_export_identity', 'reuse_export_schema', 'reuse_frontend_identity', 'reuse_gzip_expansion', 'reuse_gzip_identity', 'reuse_image_labels', 'reuse_image_platform', 'reuse_image_tags_invalid', 'reuse_input_changed', 'reuse_input_metadata', 'reuse_input_path', 'reuse_layer_diff_id', 'reuse_layer_inventory', 'reuse_layer_missing', 'reuse_layer_uncompressed_bound', 'reuse_legacy_chain_collision', 'reuse_legacy_config_bound', 'reuse_legacy_config_projection', 'reuse_legacy_image_projection', 'reuse_legacy_image_timestamp', 'reuse_legacy_layer_path', 'reuse_legacy_rootfs', 'reuse_oci_blob_identity', 'reuse_oci_descriptor', 'reuse_oci_image_manifest', 'reuse_oci_index', 'reuse_oci_index_depth', 'reuse_oci_index_missing', 'reuse_oci_layout', 'reuse_output_exists', 'reuse_output_parent', 'reuse_output_path', 'reuse_provenance_identity', 'reuse_provenance_schema', 'reuse_repositories_bound', 'reuse_rootfs_invalid', 'reuse_stream_bound', 'reuse_stream_size', 'reuse_tar_closed_inventory', 'reuse_tar_json_invalid', 'reuse_tar_member_bound', 'reuse_tar_member_invalid', 'reuse_tar_stream_bound', 'reuse_tar_trailer_missing', 'reuse_tar_trailing_payload', 'reuse_zip_expansion', 'reuse_zip_identity', 'reuse_zip_inventory', 'reuse_zip_member', 'root_scope', 'runtime_changed_during_load', 'scope_bound', 'scope_duration', 'scope_lifetime_required', 'scope_required', 'source_admission', 'source_archive_binding', 'source_changed', 'source_client_failed', 'source_footer', 'source_frame_bound', 'source_header', 'source_image_map_changed', 'source_metadata', 'source_mutex_changed', 'source_post_load_sha', 'source_scope', 'source_sha256', 'source_short', 'source_stream_short', 'source_stream_trailing', 'source_tar_changed', 'source_tar_identity', 'source_web_profile', 'source_web_proof', 'stage_root', 'stage_source_scope', 'storage_device', 'storage_mount_changed', 'stream_source_changed', 'tar_expansion_bound', 'tar_expansion_short', 'unexpected_failure', 'web_load_result', 'web_loaded_identity_changed', 'web_loaded_receipt', 'web_loaded_required', 'web_receipt_pin', 'web_stage_result'))
