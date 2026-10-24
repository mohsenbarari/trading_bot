"""CI-only admission of the one exported App image for component evidence.

This does not rebuild, scan, accept or deploy an image. No caller-selected
artifact, image, source identity or verifier is admitted.
"""
from contextlib import contextmanager
from hashlib import sha256
import argparse
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
from types import ModuleType
from zipfile import ZipFile

ARTIFACT_ID = 12552928309
RUN_ID = 40075556242
ZIP_BYTES = 180449870
ZIP_SHA256 = 'a737731a99ddf2d8cb43bcfaeb0e8490c56cdb7ac751c4ea5fbdf1cde327959b'
IMAGE = 'sha256:3b4f58b071f3a7c51778e1f6a1ae66d03beaa401fdefee6f8fd2af69999512cf'
SOURCE = '19d4c2da778f8f1edc567db4c9616c2d153980c9'
SOURCE_TREE = '7a45e7ed73bcaeccc5172152c116a415b630e7fb'
BASELINE = 'ba1f7616ade57558f51bb3a7fbb903243a33aab4'
PAYLOAD_TREE = '02553c4ba0b6ee8032e6f1ad1083c9df6cebb70c'
MANIFEST_SHA256 = 'cd2e64577576b9053b926d2e65620a98ca77b0ad71cc3100d9145b9409077c7b'
PROBE_SHA256 = '36e92fdfd3105a9db6bc20fd75aade1238a5e0b053a581bfb24c7b5e1bf6a6d3'
MAX_TAR = 2 * 1024**3
MAX_GZIP = MAX_TAR + 16 * 1024**2
MAX_JSON = 2 * 1024**2
FILES = {'candidate-image.tar.gz', 'image-export.json'}
HEX = re.compile(r'[a-f0-9]{64}')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def pinned(path, maximum):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'reuse_input_path')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and before.st_nlink == 1 and not before.st_mode & 0o077
                and 0 < before.st_size <= maximum, 'reuse_input_metadata')
        def check():
            require(identity(before) == identity(os.fstat(descriptor)) == identity(path.lstat()),
                    'reuse_input_changed')
        check()
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            yield stream, before.st_size
        check()
    finally:
        os.close(descriptor)


def digest(stream, size):
    value, count = sha256(), 0
    while block := stream.read(min(1024**2, size + 1 - count)):
        count += len(block)
        require(count <= size, 'reuse_stream_bound')
        value.update(block)
    require(count == size, 'reuse_stream_size')
    return value.hexdigest()


def document(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'reuse_duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def validate_export(record):
    require(type(record) is dict and set(record) == {
        'schema', 'status', 'image_id', 'provenance', 'archive', 'elapsed_seconds',
        'runtime_data_included', 'secrets_disclosed', 'deployment_performed'}, 'reuse_export_schema')
    expected = {'schema': 'security_consumer_image_export/1', 'status': 'exported_not_accepted',
                'image_id': IMAGE, 'runtime_data_included': False,
                'secrets_disclosed': False, 'deployment_performed': False}
    require(all(type(record.get(key)) is type(value) and record[key] == value
                for key, value in expected.items()), 'reuse_export_identity')
    provenance = record['provenance']
    require(type(provenance) is dict and set(provenance) == {
        'image_id', 'baseline_commit', 'source_commit', 'source_tree', 'payload_tree',
        'payload_manifest_sha256', 'frontend_dist_sha256', 'deployment_performed'}, 'reuse_provenance_schema')
    expected = {'image_id': IMAGE, 'baseline_commit': BASELINE, 'source_commit': SOURCE,
                'source_tree': SOURCE_TREE, 'payload_tree': PAYLOAD_TREE,
                'payload_manifest_sha256': MANIFEST_SHA256, 'deployment_performed': False}
    require(all(type(provenance.get(key)) is type(value) and provenance[key] == value
                for key, value in expected.items()), 'reuse_provenance_identity')
    require(type(provenance['frontend_dist_sha256']) is str
            and HEX.fullmatch(provenance['frontend_dist_sha256']), 'reuse_frontend_identity')
    archive = record['archive']
    require(type(archive) is dict and set(archive) == {
        'name', 'format', 'size_bytes', 'sha256', 'uncompressed_bytes'}, 'reuse_archive_schema')
    require(archive['name'] == 'candidate-image.tar.gz' and archive['format'] == 'docker-archive+gzip'
            and type(archive['size_bytes']) is int and 0 < archive['size_bytes'] <= MAX_GZIP
            and type(archive['uncompressed_bytes']) is int and 0 < archive['uncompressed_bytes'] <= MAX_TAR
            and type(archive['sha256']) is str and HEX.fullmatch(archive['sha256']), 'reuse_archive_identity')
    require(type(record['elapsed_seconds']) in {int, float} and 0 <= record['elapsed_seconds'] <= 300,
            'reuse_export_clock')
    return archive


def safe_member(name):
    path = PurePosixPath(name)
    return bool(name and not path.is_absolute() and str(path) == name
                and '..' not in path.parts and '\\' not in name
                and all(ord(char) >= 32 for char in name))


def unexpected_blob_metadata(bundle, members, unexpected):
    """Read small public exporter metadata only; never emit config values."""
    records = []
    for name in sorted(unexpected)[:32]:
        if not re.fullmatch(r'blobs/sha256/[a-f0-9]{64}', name):
            continue
        member = members[name]
        record = {'name': name, 'size': member.size, 'sha256': None, 'sha256_matches_name': None,
                  'keys': None, 'id': None, 'parent': None, 'created': None, 'os': None}
        records.append(record)
        if not member.isfile() or not 0 < member.size <= 64 * 1024:
            continue
        with bundle.extractfile(member) as source:
            raw = source.read(member.size + 1)
        if len(raw) != member.size:
            continue
        content_sha256 = sha256(raw).hexdigest()
        record['sha256_matches_name'] = content_sha256 == name.removeprefix('blobs/sha256/')
        if record['sha256_matches_name']:
            # The full content digest is already in the name; retain an
            # explicit verification result without repeating those 64 bytes.
            del record['sha256']
        else:
            record['sha256'] = content_sha256
        try:
            value = document(raw)
        except (ValueError, TypeError, RuntimeError):
            continue
        if type(value) is not dict:
            continue
        record['keys'] = [key[:64] for key in sorted(value)[:32]]
        for key in ('id', 'parent'):
            item = value.get(key)
            if type(item) is str and re.fullmatch(r'(?:sha256:)?[a-f0-9]{64}', item):
                record[key] = item
        if type(value.get('created')) is str and re.fullmatch(r'[0-9TZ:+.\-]{1,64}', value['created']):
            record['created'] = value['created']
        if value.get('os') in ('linux', 'windows', 'freebsd'):
            record['os'] = value['os']
    return records


def closed_inventory_diagnostic(unexpected, wrong_directory_type, blob_metadata=None):
    """Bound public names/metadata only; admission still refuses afterward."""
    names = {'unexpected_names': sorted(unexpected),
             'wrong_directory_type_names': sorted(wrong_directory_type)}
    record = {'code': 'reuse_tar_closed_inventory',
              'unexpected_count': len(unexpected), 'unexpected_names': [],
              'wrong_directory_type_count': len(wrong_directory_type),
              'wrong_directory_type_names': [], 'names_or_characters_omitted': False}
    if blob_metadata is not None:
        record['unexpected_blob_metadata'] = []
        record['blob_metadata_omitted'] = False
    def encode():
        return json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    if blob_metadata is not None:
        for value in blob_metadata[:32]:
            record['unexpected_blob_metadata'].append(value)
            if len(encode().encode('ascii')) + 1 > 16 * 1024:
                record['unexpected_blob_metadata'].pop()
                break
        candidates = sum(bool(re.fullmatch(r'blobs/sha256/[a-f0-9]{64}', name)) for name in unexpected)
        record['blob_metadata_omitted'] = len(record['unexpected_blob_metadata']) < candidates
    full = False
    for index in range(32):
        for key, values in names.items():
            if index >= len(values):
                continue
            record[key].append(values[index][:256])
            if len(encode().encode('ascii')) + 1 > 16 * 1024:
                record[key].pop()
                full = True
                break
        if full:
            break
    record['names_or_characters_omitted'] = any(
        len(record[key]) < len(values) or any(len(value) > 256 for value in values[:len(record[key])])
        for key, values in names.items())
    sys.stderr.write(encode() + '\n')


def validate_docker_archive(path):
    """Check one classic Docker or OCI-backed docker-save image, without extraction."""
    with pinned(path, MAX_TAR) as (stream, size), tarfile.open(fileobj=stream, mode='r:') as bundle:
        members, end = {}, 0
        for member in bundle:
            require(len(members) < 512, 'reuse_tar_member_bound')
            name = member.name.rstrip('/') if member.isdir() else member.name
            require(safe_member(name) and name not in members and not member.sparse
                    and (member.isfile() or member.isdir()) and 0 <= member.size <= size,
                    'reuse_tar_member_invalid')
            members[name] = member
            end = max(end, member.offset_data + (member.size + 511) // 512 * 512)
        require('manifest.json' in members, 'reuse_docker_manifest_missing')
        require(end <= size, 'reuse_tar_member_invalid')
        stream.seek(end)
        tail = 0
        while block := stream.read(min(65536, size - end + 1 - tail)):
            tail += len(block)
            require(tail <= size - end, 'reuse_tar_stream_bound')
            require(not any(block), 'reuse_tar_trailing_payload')
        require(tail >= 1024, 'reuse_tar_trailer_missing')
        allowed = {'manifest.json'}
        def read_json(name):
            require(name in members and members[name].isfile() and members[name].size <= MAX_JSON,
                    'reuse_tar_json_invalid')
            return document(bundle.extractfile(members[name]).read(MAX_JSON + 1))
        def hash_member(name, *, unpack=False):
            require(name in members and members[name].isfile(), 'reuse_layer_missing')
            with bundle.extractfile(members[name]) as value:
                if unpack and value.peek(2)[:2] == b'\x1f\x8b':
                    with gzip.GzipFile(fileobj=value) as decoded:
                        result, total = sha256(), 0
                        while block := decoded.read(min(1024**2, MAX_TAR + 1 - total)):
                            total += len(block)
                            require(total <= MAX_TAR, 'reuse_layer_uncompressed_bound')
                            result.update(block)
                        return result.hexdigest()
                return digest(value, members[name].size)
        manifest = read_json('manifest.json')
        require(type(manifest) is list and len(manifest) == 1 and type(manifest[0]) is dict,
                'reuse_docker_manifest_invalid')
        item = manifest[0]
        require({'Config', 'Layers'} <= set(item) <= {'Config', 'RepoTags', 'Layers', 'LayerSources'},
                'reuse_docker_manifest_invalid')
        config_name, layers = item['Config'], item['Layers']
        require(type(config_name) is str and safe_member(config_name)
                and config_name in {IMAGE[7:] + '.json', 'blobs/sha256/' + IMAGE[7:]}, 'reuse_config_identity')
        require(type(layers) is list and 0 < len(layers) <= 128
                and all(type(name) is str and safe_member(name) for name in layers), 'reuse_layer_inventory')
        tags = item.get('RepoTags')
        require(tags is None or type(tags) is list and len(tags) <= 8
                and all(type(tag) is str and re.fullmatch(r'[A-Za-z0-9_./:@-]{1,256}', tag) for tag in tags),
                'reuse_image_tags_invalid')
        require(hash_member(config_name) == IMAGE[7:], 'reuse_config_digest')
        config = read_json(config_name)
        require(type(config) is dict and config.get('os') == 'linux' and config.get('architecture') == 'amd64',
                'reuse_image_platform')
        rootfs = config.get('rootfs')
        require(type(rootfs) is dict and rootfs.get('type') == 'layers'
                and type(rootfs.get('diff_ids')) is list and len(rootfs['diff_ids']) == len(layers),
                'reuse_rootfs_invalid')
        labels = (config.get('config') or {}).get('Labels') or {}
        expected_labels = {'org.opencontainers.image.revision': BASELINE,
            'io.gold-trade.release.tree': PAYLOAD_TREE, 'io.gold-trade.security.overlay.revision': SOURCE,
            'io.gold-trade.security.overlay.tree': SOURCE_TREE,
            'io.gold-trade.security.payload.sha256': MANIFEST_SHA256}
        require(type(labels) is dict and all(labels.get(key) == value for key, value in expected_labels.items()),
                'reuse_image_labels')
        allowed.update([config_name, *layers])
        for name, expected in zip(layers, rootfs['diff_ids']):
            require(type(expected) is str and re.fullmatch(r'sha256:[a-f0-9]{64}', expected)
                    and hash_member(name, unpack=True) == expected[7:], 'reuse_layer_diff_id')
        if 'index.json' in members:
            require('oci-layout' in members and read_json('oci-layout') == {'imageLayoutVersion': '1.0.0'},
                    'reuse_oci_layout')
            allowed.update({'index.json', 'oci-layout'})
            def descriptor(row):
                require(type(row) is dict and type(row.get('digest')) is str
                        and re.fullmatch(r'sha256:[a-f0-9]{64}', row['digest'])
                        and type(row.get('size')) is int and row['size'] > 0, 'reuse_oci_descriptor')
                name = 'blobs/sha256/' + row['digest'][7:]
                require(name in members and members[name].size == row['size']
                        and hash_member(name) == row['digest'][7:], 'reuse_oci_blob_identity')
                allowed.add(name)
                return name
            index = read_json('index.json')
            for depth in range(4):
                require(type(index) is dict and index.get('schemaVersion') == 2
                        and type(index.get('manifests')) is list and len(index['manifests']) == 1,
                        'reuse_oci_index')
                selected = read_json(descriptor(index['manifests'][0]))
                if 'manifests' in selected:
                    index = selected
                    continue
                require(selected.get('schemaVersion') == 2 and descriptor(selected.get('config')) == config_name
                        and type(selected.get('layers')) is list
                        and [descriptor(row) for row in selected['layers']] == layers, 'reuse_oci_image_manifest')
                break
            else:
                raise RuntimeError('reuse_oci_index_depth')
        else:
            require('oci-layout' not in members and all(not name.startswith('blobs/') for name in layers),
                    'reuse_oci_index_missing')
            for name in layers:
                require(name.endswith('/layer.tar'), 'reuse_legacy_layer_path')
                prefix = name[:-len('layer.tar')]
                allowed.update({prefix + 'VERSION', prefix + 'json'} & set(members))
            if 'repositories' in members:
                require(members['repositories'].size <= MAX_JSON, 'reuse_repositories_bound')
                allowed.add('repositories')
        directories = {str(parent) for name in allowed for parent in PurePosixPath(name).parents if str(parent) != '.'}
        unexpected = set(members) - allowed - directories
        wrong_directory_type = {name for name, member in members.items()
                                if member.isdir() != (name in directories)}
        if unexpected or wrong_directory_type:
            closed_inventory_diagnostic(unexpected, wrong_directory_type,
                                        unexpected_blob_metadata(bundle, members, unexpected))
            raise RuntimeError('reuse_tar_closed_inventory')
        stream.seek(0)
        archive_sha = digest(stream, size)
        return {'image_id': IMAGE, 'layers': len(layers), 'docker_archive_sha256': archive_sha,
                'docker_archive_bytes': size, 'layout': 'oci-docker' if 'index.json' in members else 'docker'}


def admit_image(zip_path, output):
    require(output.is_absolute() and output.parent.resolve(strict=True) == output.parent,
            'reuse_output_path')
    parent = output.parent.stat()
    require(parent.st_uid == os.geteuid() and stat.S_IMODE(parent.st_mode) == 0o700, 'reuse_output_parent')
    require(not os.path.lexists(output), 'reuse_output_exists')
    with pinned(zip_path, ZIP_BYTES) as (stream, size):
        require(size == ZIP_BYTES and digest(stream, size) == ZIP_SHA256, 'reuse_zip_identity')
        stream.seek(0)
        with ZipFile(stream) as archive:
            entries = archive.infolist()
            require(len(entries) == 2 and {row.filename for row in entries} == FILES,
                    'reuse_zip_inventory')
            for row in entries:
                mode = row.external_attr >> 16
                maximum = MAX_JSON if row.filename == 'image-export.json' else MAX_GZIP
                require(not row.is_dir() and not row.flag_bits & 1 and row.compress_type in {0, 8}
                        and stat.S_IFMT(mode) in {0, stat.S_IFREG}
                        and 0 < row.file_size <= maximum, 'reuse_zip_member')
            output.mkdir(mode=0o700)
            for row in entries:
                fd = os.open(output / row.filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, 'wb') as target, archive.open(row) as source:
                    total = 0
                    while block := source.read(min(65536, row.file_size + 1 - total)):
                        total += len(block)
                        require(total <= row.file_size, 'reuse_zip_expansion')
                        target.write(block)
                    require(total == row.file_size, 'reuse_zip_expansion')
                    target.flush()
                    os.fsync(target.fileno())
        with pinned(output / 'image-export.json', MAX_JSON) as (value, size):
            record = document(value.read(size + 1))
        expected = validate_export(record)
        with pinned(output / 'candidate-image.tar.gz', MAX_GZIP) as (compressed, size):
            require(size == expected['size_bytes'] and digest(compressed, size) == expected['sha256'],
                    'reuse_gzip_identity')
            compressed.seek(0)
            fd = os.open(output / 'candidate-image.tar', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'wb') as target, gzip.GzipFile(fileobj=compressed) as decoded:
                total = 0
                while block := decoded.read(min(65536, expected['uncompressed_bytes'] + 1 - total)):
                    total += len(block)
                    require(total <= expected['uncompressed_bytes'], 'reuse_gzip_expansion')
                    target.write(block)
                require(total == expected['uncompressed_bytes'], 'reuse_gzip_expansion')
                target.flush()
                os.fsync(target.fileno())
        result = validate_docker_archive(output / 'candidate-image.tar')
        return {'schema': 'security_component_image_reuse_admission/1', 'status': 'admitted_for_component_probe_only',
                'artifact_id': ARTIFACT_ID, 'artifact_run': RUN_ID, 'artifact_zip_sha256': ZIP_SHA256,
                'source_commit': SOURCE, 'export_provenance': record['provenance'], **result,
                'image_rebuilt': False, 'security_accepted': False, 'deployment_performed': False,
                'scanner_findings_modified': False, 'production_mounts_or_data_used': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-zip', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = admit_image(args.artifact_zip, args.output)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"refused","code":"component_image_reuse_admission_failed"}', file=sys.stderr)
        raise SystemExit(1) from None
