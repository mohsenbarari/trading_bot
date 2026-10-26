"""Bound a normal Docker-save OCI archive to one observed immutable image.

No commands, downloads, extraction, image loads, or inspected code execution.
The audit caller owns its retained file descriptor, one fallback attempt, common
600-second deadline, database/capacity checks, scanner flags and child bounds.
"""
from hashlib import sha256
import gzip
import io
import json
import math
import os
import re
import tarfile
import time
import zlib

ARCHIVE_BYTES = 512 * 1024**2
EXPANDED_BYTES = 512 * 1024**2
CONTROL_BYTES = 1024**2
REPORT_BYTES = 32 * 1024**2
MAX_LAYERS = 128
DIGEST = re.compile(r'sha256:[a-f0-9]{64}\Z')
BLOB = re.compile(r'blobs/sha256/[a-f0-9]{64}\Z')
OCI_MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
OCI_INDEX = 'application/vnd.oci.image.index.v1+json'
ATTESTATION = 'application/vnd.in-toto+json'
DOCKER_MANIFEST = 'application/vnd.docker.distribution.manifest.v2+json'
MEDIA = {OCI_MANIFEST: ('application/vnd.oci.image.config.v1+json',
                        'application/vnd.oci.image.layer.v1.tar'),
         DOCKER_MANIFEST: ('application/vnd.docker.container.image.v1+json',
                           'application/vnd.docker.image.rootfs.diff.tar')}
LAYER_MEDIA = {'application/vnd.oci.image.layer.v1.tar': False,
               'application/vnd.oci.image.layer.v1.tar+gzip': True,
               'application/vnd.docker.image.rootfs.diff.tar': False,
               'application/vnd.docker.image.rootfs.diff.tar.gzip': True}
FOREIGN_PLATFORMS = frozenset((('arm', 'v6'), ('arm', 'v7'), ('arm64', 'v8'),
                              ('386', None), ('ppc64le', None), ('riscv64', None), ('s390x', None)))
CODES = frozenset(('archive_shape', 'archive_bound', 'archive_identity',
                   'command_timeout', 'observation_shape', 'report_identity'))


class TransportError(RuntimeError):
    """Static refusal code; never include configuration or exception text."""


def require(value, code):
    if not value:
        raise TransportError(code)


def parse(raw, maximum, code):
    require(type(raw) is bytes and 0 < len(raw) <= maximum, code)
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, code)
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: require(False, code))
    except (ValueError, UnicodeError, RecursionError):
        raise TransportError(code) from None


def layers(value, code):
    require(type(value) is list and 0 < len(value) <= MAX_LAYERS
            and all(type(item) is str and DIGEST.fullmatch(item) for item in value), code)
    return list(value)  # Repeated empty-layer references are legitimate.


def observe_image(raw, image_id):
    """Consume only inspect's fixed public Id/RootFS projection, never Config."""
    require(type(image_id) is str and DIGEST.fullmatch(image_id), 'observation_shape')
    value = parse(raw, 65536, 'observation_shape')
    require(type(value) is dict and set(value) == {'id', 'os', 'architecture', 'rootfs'}
            and value['id'] == image_id and type(value['rootfs']) is dict
            and value['os'] == 'linux' and value['architecture'] == 'amd64'
            and set(value['rootfs']) == {'Type', 'Layers'}
            and value['rootfs']['Type'] == 'layers', 'observation_shape')
    return {'image_id': image_id, 'os': 'linux', 'architecture': 'amd64',
            'rootfs_layers': layers(value['rootfs']['Layers'], 'observation_shape')}


def index_descriptors(values, code):
    """Classify only the observed one-level Linux platform/attestation layout."""
    require(type(values) is list and 1 <= len(values) <= 32, code)
    records, digests, platforms, references = [], set(), set(), set()
    for value in values:
        require(type(value) is dict and set(value) <= {'digest', 'size', 'mediaType', 'platform', 'annotations'}
                and value.get('mediaType') == OCI_MANIFEST
                and type(value.get('digest')) is str and DIGEST.fullmatch(value['digest'])
                and value['digest'] not in digests and type(value.get('size')) is int
                and 0 < value['size'] <= CONTROL_BYTES, code)
        digests.add(value['digest'])
        platform, annotation = value.get('platform'), value.get('annotations', {})
        require(type(platform) is dict and type(annotation) is dict and len(annotation) <= 32
                and all(type(k) is str and type(v) is str and len(k) <= 256 and len(v) <= 2048
                        for k, v in annotation.items()), code)
        reference = annotation.get('vnd.docker.reference.digest')
        if platform == {'os': 'unknown', 'architecture': 'unknown'}:
            require(annotation.get('vnd.docker.reference.type') == 'attestation-manifest'
                    and type(reference) is str and DIGEST.fullmatch(reference) and reference not in references, code)
            references.add(reference); kind = 'attestation'
        else:
            require(not any(k.startswith('vnd.docker.reference.') for k in annotation)
                    and set(platform) in ({'os', 'architecture'}, {'os', 'architecture', 'variant'})
                    and platform.get('os') == 'linux', code)
            architecture, variant = platform.get('architecture'), platform.get('variant')
            require(type(architecture) is str and ('variant' not in platform or type(variant) is str), code)
            target = platform == {'os': 'linux', 'architecture': 'amd64'}
            require(target or (architecture, variant) in FOREIGN_PLATFORMS, code)
            require((architecture, variant) not in platforms, code); platforms.add((architecture, variant))
            kind = 'runtime' if target else 'foreign_runtime'
        records.append({'digest': value['digest'], 'media_type': OCI_MANIFEST, 'size': value['size'],
                        'platform': dict(platform), 'kind': kind, 'reference': reference})
    runtime = {r['digest']: r for r in records if r['kind'] != 'attestation'}
    selected = [r for r in runtime.values() if r['kind'] == 'runtime']
    require(len(selected) == 1 and all(r['reference'] in runtime for r in records if r['kind'] == 'attestation'), code)
    require(sum(r['kind'] == 'attestation' and r['reference'] == selected[0]['digest'] for r in records) == 1, code)
    return records, selected[0]


def validate_index_binding(value, image, manifest, code):
    fields = {'root_index_sha256', 'selected_manifest_sha256', 'descriptors', 'auxiliary_blobs',
              'statement_semantics_validated', 'signatures_verified', 'provenance_trusted'}
    require(type(value) is dict and set(value) == fields and value['root_index_sha256'] == image[7:]
            and value['selected_manifest_sha256'] == manifest
            and all(value[k] is False for k in ('statement_semantics_validated', 'signatures_verified', 'provenance_trusted')), code)
    rows = value['descriptors']; require(type(rows) is list and 1 <= len(rows) <= 32, code)
    inputs = []
    for row in rows:
        require(type(row) is dict and set(row) == {'digest', 'media_type', 'size', 'platform', 'kind', 'reference', 'present'}
                and type(row['present']) is bool, code)
        item = {'digest': row['digest'], 'mediaType': row['media_type'], 'size': row['size'], 'platform': row['platform']}
        if row['reference'] is not None:
            item['annotations'] = {'vnd.docker.reference.type': 'attestation-manifest', 'vnd.docker.reference.digest': row['reference']}
        inputs.append(item)
    canonical, selected = index_descriptors(inputs, code)
    require(selected['digest'] == 'sha256:' + manifest, code)
    for row, expected in zip(rows, canonical):
        require({k: row[k] for k in expected} == expected
                and (row['kind'] != 'runtime' or row['present'])
                and (row['kind'] != 'foreign_runtime' or not row['present'])
                and (row['kind'] != 'attestation' or row['reference'] != selected['digest'] or row['present']), code)
    blobs = value['auxiliary_blobs']; require(type(blobs) is list and len(blobs) <= 32 * 6, code)
    seen = set()
    for row in blobs:
        require(type(row) is dict and set(row) == {'digest', 'media_type', 'size'}
                and type(row['digest']) is str and DIGEST.fullmatch(row['digest']) and row['digest'] not in seen
                and type(row['media_type']) is str and row['media_type'] in {OCI_MANIFEST, MEDIA[OCI_MANIFEST][0], ATTESTATION}
                and type(row['size']) is int and 0 < row['size'] <= CONTROL_BYTES, code)
        seen.add(row['digest'])
    require(all({'digest': row['digest'], 'media_type': OCI_MANIFEST, 'size': row['size']} in blobs
                for row in rows if row['kind'] == 'attestation' and row['present']), code)


def may_fallback(failure_code, already_attempted):
    """Ordinary image-command failure only; timeouts/global faults never qualify."""
    require(type(failure_code) is str and type(already_attempted) is bool, 'observation_shape')
    return failure_code == 'command_failed' and not already_attempted


class ArchiveReader:
    """Borrowed seekable view; validate size/deadline before underlying access."""
    def __init__(self, stream, size, end):
        require(type(size) is int and 0 < size <= ARCHIVE_BYTES and size % 512 == 0,
                'archive_bound')
        require(type(end) in (int, float) and math.isfinite(end)
                and 0 < end - time.monotonic() <= 600, 'command_timeout')
        self.stream, self.size, self.end = stream, size, end

    def tell(self):
        require(time.monotonic() < self.end, 'command_timeout')
        position = self.stream.tell()
        require(type(position) is int and 0 <= position <= self.size, 'archive_bound')
        return position

    def read(self, size):
        position = self.tell()
        require(type(size) is int and 0 <= size <= self.size - position, 'archive_bound')
        raw = self.stream.read(size)
        require(type(raw) is bytes and len(raw) == size, 'archive_bound')
        return raw

    def seek(self, offset, whence=os.SEEK_SET):
        position = self.tell()
        require(type(offset) is int and whence in (os.SEEK_SET, os.SEEK_CUR, os.SEEK_END),
                'archive_bound')
        target = offset + (0 if whence == os.SEEK_SET else position if whence == os.SEEK_CUR else self.size)
        require(0 <= target <= self.size, 'archive_bound')
        return self.stream.seek(target)

    def close(self):
        pass  # The caller's pinned context owns this descriptor.


def digest(reader, size):
    value = sha256()
    while size:
        block = reader.read(min(65536, size))
        value.update(block)
        size -= len(block)
    return value.hexdigest()


def layer_digest(reader, length, compressed, maximum):
    """Hash stored bytes and bounded raw DiffID, without extracting a layer."""
    stored, raw, total = sha256(), sha256(), 0
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if compressed else None
    try:
        while length:
            block = reader.read(min(65536, length)); length -= len(block); stored.update(block)
            if decoder is None:
                require(total + len(block) <= maximum, 'archive_bound')
                raw.update(block); total += len(block)
                continue
            pending = block
            while pending:
                reader.tell()  # Common deadline before each bounded decoder call.
                old = len(pending)
                output = decoder.decompress(pending, min(65536, maximum + 1 - total))
                pending = decoder.unconsumed_tail
                total += len(output); require(total <= maximum, 'archive_bound'); raw.update(output)
                require(not decoder.unused_data, 'archive_shape')
                require(len(pending) < old or output, 'archive_shape')
            if decoder.eof:
                require(length == 0, 'archive_shape')
        if decoder is not None:
            while not decoder.eof:
                reader.tell()
                output = decoder.decompress(b'', min(65536, maximum + 1 - total))
                total += len(output); require(total <= maximum, 'archive_bound'); raw.update(output)
                require(output or decoder.eof, 'archive_shape')
            require(not decoder.unused_data, 'archive_shape')
    except zlib.error:
        raise TransportError('archive_shape') from None
    require(total > 0, 'archive_shape')
    return stored.hexdigest(), raw.hexdigest(), total


def inventory(reader, permitted_files=None):
    """Read bounded standard TAR headers; extensions/links/unknown names refuse.

    Docker's short OCI blob names need no PAX/GNU extension. Inspecting each
    physical header before any payload read also prevents hidden extension
    records from escaping the closed member inventory.
    """
    members = {}
    reader.seek(0)
    while True:
        position = reader.tell()
        header = reader.read(512)
        if header == bytes(512):
            require(reader.size - position >= 1024, 'archive_shape')
            return members, position
        require(len(members) < MAX_LAYERS + 7, 'archive_bound')
        try:
            item = tarfile.TarInfo.frombuf(header, 'utf-8', 'strict')
        except (tarfile.TarError, ValueError, UnicodeError, OverflowError):
            raise TransportError('archive_shape') from None
        directory = item.isdir()
        name = item.name.rstrip('/') if directory else item.name
        allowed_file = (name in {'manifest.json', 'index.json', 'oci-layout'} or BLOB.fullmatch(name)) \
                       if permitted_files is None else name in permitted_files
        require(name not in members and
                ((permitted_files is None and directory and name in {'blobs', 'blobs/sha256'} and item.size == 0)
                 or (item.isreg() and allowed_file and 0 < item.size <= ARCHIVE_BYTES)),
                'archive_shape')
        start = reader.tell()
        padded = ((item.size + 511) // 512) * 512
        require(start + padded <= reader.size, 'archive_bound')
        members[name] = (start, item.size)
        reader.seek(start + padded)


def _tail(stream, start, size, end):
    require(time.monotonic() < end, 'command_timeout')
    stream.seek(start)
    left, total = size - start, 0
    while True:
        require(time.monotonic() < end, 'command_timeout')
        raw = stream.read(min(65536, left + 1 - total))
        require(type(raw) is bytes, 'archive_bound')
        if not raw:
            break
        total += len(raw)
        require(total <= left, 'archive_bound')
        require(not raw.strip(b'\0'), 'archive_shape')
    require(total == left, 'archive_bound')


def validate_archive(stream, size, end, observation):
    """Verify same-image config/manifest identity and every ordered raw layer.

    Supported export is the normal OCI layout with Docker manifest.json. This
    supports the observed single-manifest and one-level OCI-index exports.
    Unknown platform layouts, nested indexes, legacy metadata and compression
    outside the explicit raw/gzip set refuse pending review.
    """
    require(type(observation) is dict and set(observation) == {'image_id', 'os', 'architecture', 'rootfs_layers'}
            and observation['os'] == 'linux' and observation['architecture'] == 'amd64',
            'observation_shape')
    image = observation['image_id']
    require(type(image) is str and DIGEST.fullmatch(image), 'observation_shape')
    expected_layers = layers(observation['rootfs_layers'], 'observation_shape')
    reader = ArchiveReader(stream, size, end)
    members, tail = inventory(reader)
    def control(name, *, interpret=True):
        require(name in members, 'archive_shape')
        offset, length = members[name]
        require(length <= CONTROL_BYTES, 'archive_bound')
        reader.seek(offset)
        raw = reader.read(length)
        return raw, parse(raw, CONTROL_BYTES, 'archive_shape') if interpret else None
    def blob(value):
        require(type(value) is str and DIGEST.fullmatch(value), 'archive_identity')
        return 'blobs/sha256/' + value[7:]
    index_raw, index = control('index.json')
    require(type(index) is dict and type(index.get('schemaVersion')) is int
            and index['schemaVersion'] == 2 and type(index.get('manifests')) is list
            and len(index['manifests']) == 1, 'archive_identity')
    entry = index['manifests'][0]
    require(type(entry) is dict and type(entry.get('mediaType')) is str
            and entry['mediaType'] in {*MEDIA, OCI_INDEX}, 'archive_identity')
    index_binding, additional_files = None, set()
    if entry['mediaType'] == OCI_INDEX:
        root_id = entry.get('digest')
        root_raw, root_index = control(blob(root_id))
        require(root_id == image and sha256(root_raw).hexdigest() == root_id[7:]
                and type(entry.get('size')) is int and entry['size'] == len(root_raw)
                and type(root_index) is dict and type(root_index.get('schemaVersion')) is int
                and root_index['schemaVersion'] == 2 and root_index.get('mediaType') == OCI_INDEX,
                'archive_identity')
        records, selected = index_descriptors(root_index.get('manifests'), 'archive_identity')
        auxiliary = []
        additional_files.add(blob(root_id))
        def auxiliary_control(descriptor, media, *, interpret=True):
            require(type(descriptor) is dict and descriptor.get('mediaType') == media
                    and type(descriptor.get('size')) is int and 0 < descriptor['size'] <= CONTROL_BYTES,
                    'archive_identity')
            name = blob(descriptor.get('digest'))
            raw, value = control(name, interpret=interpret)
            require(len(raw) == descriptor['size'] and sha256(raw).hexdigest() == descriptor['digest'][7:], 'archive_identity')
            additional_files.add(name)
            row = {'digest': descriptor['digest'], 'media_type': media, 'size': len(raw)}
            require(row not in auxiliary, 'archive_identity'); auxiliary.append(row)
            return value
        for record in records:
            record['present'] = blob(record['digest']) in members
            # Other platform bytes were absent in both observed exports. Do
            # not silently extend the runtime proof to new present platforms.
            require(record['kind'] != 'foreign_runtime' or not record['present'], 'archive_identity')
            if record['kind'] != 'attestation':
                require(record['kind'] != 'runtime' or record['present'], 'archive_identity')
                continue
            if not record['present']:
                require(record['reference'] != selected['digest'], 'archive_identity')
                continue  # Only an explicitly declared foreign reference.
            descriptor = {'digest': record['digest'], 'size': record['size'], 'mediaType': OCI_MANIFEST}
            attestation = auxiliary_control(descriptor, OCI_MANIFEST)
            require(type(attestation) is dict and type(attestation.get('schemaVersion')) is int
                    and attestation['schemaVersion'] == 2 and attestation.get('mediaType') == OCI_MANIFEST,
                    'archive_identity')
            config = auxiliary_control(attestation.get('config'), MEDIA[OCI_MANIFEST][0])
            require(type(config) is dict, 'archive_identity')
            statements = attestation.get('layers')
            require(type(statements) is list and 1 <= len(statements) <= 4, 'archive_identity')
            for descriptor in statements:
                # Filesystem auditing authenticates the auxiliary bytes and
                # index reference, not statement schemas, subjects or trust.
                # Never interpret this content as runtime layers or code.
                auxiliary_control(descriptor, ATTESTATION, interpret=False)
        index_binding = {'root_index_sha256': root_id[7:], 'selected_manifest_sha256': selected['digest'][7:],
                         'descriptors': records, 'auxiliary_blobs': auxiliary,
                         'statement_semantics_validated': False, 'signatures_verified': False, 'provenance_trusted': False}
        validate_index_binding(index_binding, image, selected['digest'][7:], 'archive_identity')
        entry = {'digest': selected['digest'], 'size': selected['size'], 'mediaType': OCI_MANIFEST}
    manifest_id = entry.get('digest')
    manifest_raw, manifest = control(blob(manifest_id))
    require(sha256(manifest_raw).hexdigest() == manifest_id[7:]
            and type(entry.get('size')) is int and entry['size'] == len(manifest_raw)
            and type(manifest) is dict and type(manifest.get('schemaVersion')) is int
            and manifest['schemaVersion'] == 2 and manifest.get('mediaType') == entry['mediaType'],
            'archive_identity')
    descriptor = manifest.get('config')
    config_media, _ = MEDIA[entry['mediaType']]
    require(type(descriptor) is dict and descriptor.get('mediaType') == config_media, 'archive_identity')
    config_id = descriptor.get('digest')
    config_raw, config = control(blob(config_id))
    require(sha256(config_raw).hexdigest() == config_id[7:]
            and type(descriptor.get('size')) is int and descriptor['size'] == len(config_raw)
            and type(config) is dict and config.get('rootfs') == {'type': 'layers', 'diff_ids': expected_layers}
            and config.get('os') == observation['os'] and config.get('architecture') == observation['architecture'],
            'archive_identity')
    require(image in (config_id, manifest_id) or index_binding is not None, 'archive_identity')
    descriptors = manifest.get('layers')
    require(type(descriptors) is list and len(descriptors) == len(expected_layers), 'archive_identity')
    checked, layer_names, blob_ids, expanded = {}, [], [], 0
    for value, descriptor in zip(expected_layers, descriptors):
        require(type(descriptor) is dict, 'archive_identity')
        stored_id = descriptor.get('digest'); name = blob(stored_id)
        require(name in members, 'archive_identity')
        offset, length = members[name]
        media = descriptor.get('mediaType')
        require(type(descriptor.get('size')) is int and descriptor['size'] == length
                and type(media) is str and media in LAYER_MEDIA,
                'archive_identity')
        key = (name, media)
        if key not in checked:
            reader.seek(offset)
            stored_hash, raw_hash, raw_size = layer_digest(reader, length, LAYER_MEDIA[media], EXPANDED_BYTES - expanded)
            require(stored_hash == stored_id[7:], 'archive_identity')
            checked[key] = (raw_hash, raw_size)
        raw_hash, raw_size = checked[key]
        require(raw_hash == value[7:], 'archive_identity')
        expanded += raw_size; require(expanded <= EXPANDED_BYTES, 'archive_bound')
        layer_names.append(name); blob_ids.append(stored_id)
    docker_raw, docker = control('manifest.json')
    require(type(docker) is list and len(docker) == 1 and type(docker[0]) is dict
            and docker[0].get('Config') == blob(config_id) and docker[0].get('Layers') == layer_names,
            'archive_identity')
    _, layout = control('oci-layout')
    require(layout == {'imageLayoutVersion': '1.0.0'}, 'archive_shape')
    files = {'manifest.json', 'index.json', 'oci-layout', blob(manifest_id), blob(config_id), *layer_names, *additional_files}
    require(set(members) - {'blobs', 'blobs/sha256'} == files, 'archive_shape')
    _tail(stream, tail, size, end)
    reader.seek(0)
    archive_hash = digest(reader, size)
    kind = ('OCI_manifest_to_config' if entry['mediaType'] == OCI_MANIFEST else 'Docker_manifest_to_config') if image == manifest_id else 'Docker_config_identity'
    if index_binding is not None:
        kind = 'OCI_index_to_manifest_to_config'
    proof = {'schema': 'runtime_image_archive_binding/1', 'kind': kind,
            'manifest_media_type': entry['mediaType'],
            'docker_image_id': image, 'scanner_config_id': config_id,
            'rootfs_layers': expected_layers, 'layer_blob_digests': blob_ids,
            'expanded_layer_bytes': expanded, 'archive_bytes': size, 'archive_sha256': archive_hash,
            'manifest_sha256': manifest_id[7:], 'config_sha256': config_id[7:],
            'docker_manifest_sha256': sha256(docker_raw).hexdigest(),
            'index_sha256': sha256(index_raw).hexdigest(),
            'security_accepted': False, 'findings_waived': False}
    if index_binding is not None:
        proof['index_binding'] = index_binding
    return proof


def normalize_scan_archive(source, size, end, observation, target):
    """Write one bounded classic Docker transport with unchanged config/raw layers.

    Both streams are borrowed. The caller exclusively creates, retains, guards
    and removes its output FD. This changes only the outer transport wrapper;
    every inner TAR byte, including headers and padding, is copied verbatim.
    """
    source_proof = validate_archive(source, size, end, observation)
    require(target.seek(0, os.SEEK_END) == 0 and target.tell() == 0, 'archive_identity')
    reader = ArchiveReader(source, size, end)
    members, _ = inventory(reader)
    def control(name):
        require(name in members, 'archive_shape')
        offset, length = members[name]
        require(length <= CONTROL_BYTES, 'archive_bound')
        reader.seek(offset)
        raw = reader.read(length)
        return raw, parse(raw, CONTROL_BYTES, 'archive_shape')
    config_raw, _ = control('blobs/sha256/' + source_proof['config_sha256'])
    manifest_raw, manifest = control('blobs/sha256/' + source_proof['manifest_sha256'])
    require(sha256(config_raw).hexdigest() == source_proof['config_sha256']
            and sha256(manifest_raw).hexdigest() == source_proof['manifest_sha256'], 'archive_identity')
    config_name = source_proof['config_sha256'] + '.json'
    layer_names = ['layers/' + str(index).zfill(3) + '/layer.tar'
                   for index in range(len(source_proof['rootfs_layers']))]
    docker_value = [{'Config': config_name, 'RepoTags': None, 'Layers': layer_names}]
    docker_raw = json.dumps(docker_value, sort_keys=True, separators=(',', ':')).encode()

    class Output:
        count = 0
        def write(self, raw):
            require(time.monotonic() < end, 'command_timeout')
            require(type(raw) is bytes and len(raw) <= ARCHIVE_BYTES - self.count, 'archive_bound')
            remaining = raw
            while remaining:
                require(time.monotonic() < end, 'command_timeout')
                written = target.write(remaining)
                require(type(written) is int and 0 < written <= len(remaining), 'archive_bound')
                self.count += written; remaining = remaining[written:]
            return len(raw)
        def flush(self):
            target.flush()

    class Slice:
        def __init__(self, offset, length):
            self.offset, self.length, self.position = offset, length, 0
        def read(self, count=-1):
            require(type(count) is int, 'archive_bound')
            count = min(65536, self.length-self.position, count if count >= 0 else 65536)
            reader.seek(self.offset+self.position)
            raw = reader.read(count); self.position += len(raw)
            return raw
        def tell(self):
            return self.position

    class RawLayer:
        def __init__(self, inner, maximum):
            self.inner, self.maximum, self.count, self.hash = inner, maximum, 0, sha256()
        def read(self, count):
            require(time.monotonic() < end, 'command_timeout')
            require(type(count) is int and 0 <= count <= 65536, 'archive_bound')
            raw = self.inner.read(count)
            require(type(raw) is bytes and self.count + len(raw) <= self.maximum, 'archive_bound')
            self.count += len(raw); self.hash.update(raw)
            return raw

    def add(archive, name, length, stream):
        item = tarfile.TarInfo(name)
        item.size, item.mode, item.uid, item.gid, item.mtime = length, 0o600, 0, 0, 0
        archive.addfile(item, stream)

    writer, expanded = Output(), 0
    try:
        with tarfile.open(fileobj=writer, mode='w|', format=tarfile.USTAR_FORMAT) as archive:
            add(archive, config_name, len(config_raw), io.BytesIO(config_raw))
            add(archive, 'manifest.json', len(docker_raw), io.BytesIO(docker_raw))
            for name, diff_id, descriptor in zip(layer_names, source_proof['rootfs_layers'], manifest['layers']):
                offset, length = members['blobs/sha256/' + descriptor['digest'][7:]]
                compressed = LAYER_MEDIA[descriptor['mediaType']]
                if compressed:
                    reader.seek(offset)
                    stored, uncompressed, raw_size = layer_digest(reader, length, True, EXPANDED_BYTES-expanded)
                    require(stored == descriptor['digest'][7:] and uncompressed == diff_id[7:], 'archive_identity')
                else:
                    raw_size = length
                expanded += raw_size; require(expanded <= EXPANDED_BYTES, 'archive_bound')
                part = Slice(offset, length)
                inner = gzip.GzipFile(fileobj=part, mode='rb') if compressed else part
                try:
                    layer = RawLayer(inner, raw_size)
                    add(archive, name, raw_size, layer)
                    require(not layer.read(1) and layer.count == raw_size and layer.hash.hexdigest() == diff_id[7:], 'archive_identity')
                finally:
                    if compressed: inner.close()
        writer.flush()
    except (OSError, EOFError, tarfile.TarError, zlib.error):
        raise TransportError('archive_shape') from None
    require(expanded == source_proof['expanded_layer_bytes'], 'archive_identity')
    output_size = writer.count
    output = ArchiveReader(target, output_size, end)
    allowed = {config_name, 'manifest.json', *layer_names}
    physical, tail = inventory(output, allowed)
    require(set(physical) == allowed, 'archive_shape')
    for name, expected in ((config_name, config_raw), ('manifest.json', docker_raw)):
        offset, length = physical[name]
        require(length == len(expected), 'archive_identity'); output.seek(offset)
        require(output.read(length) == expected, 'archive_identity')
    for name, expected in zip(layer_names, source_proof['rootfs_layers']):
        offset, length = physical[name]; output.seek(offset)
        require(digest(output, length) == expected[7:], 'archive_identity')
    _tail(target, tail, output_size, end)
    output.seek(0); output_sha256 = digest(output, output_size)
    reader.seek(0)
    require(digest(reader, size) == source_proof['archive_sha256'], 'archive_identity')
    return source_proof, {'schema': 'runtime_image_scan_transport/1', 'kind': 'all_raw_docker_archive',
        'source_archive_sha256': source_proof['archive_sha256'], 'source_docker_image_id': source_proof['docker_image_id'],
        'scanner_config_id': source_proof['scanner_config_id'], 'rootfs_layers': list(source_proof['rootfs_layers']),
        'archive_sha256': output_sha256, 'archive_bytes': output_size, 'raw_layer_bytes': expanded,
        'layer_members': layer_names, 'config_bytes_preserved': True, 'all_raw_layer_bytes_preserved': True,
        'inner_headers_or_payload_rewritten': False, 'layers_reordered': False,
        'security_accepted': False, 'findings_waived': False}


def validate_report(raw, proof):
    """Validate untouched raw report identity; return only public binding fields."""
    keys = {'schema', 'kind', 'manifest_media_type', 'docker_image_id', 'scanner_config_id', 'rootfs_layers',
            'layer_blob_digests', 'expanded_layer_bytes',
            'archive_bytes', 'archive_sha256', 'manifest_sha256', 'config_sha256',
            'docker_manifest_sha256', 'index_sha256', 'security_accepted', 'findings_waived'}
    require(type(proof) is dict, 'report_identity')
    indexed = proof.get('kind') == 'OCI_index_to_manifest_to_config'
    if indexed:
        keys.add('index_binding')
    require(set(proof) == keys
            and proof['schema'] == 'runtime_image_archive_binding/1'
            and proof['security_accepted'] is False and proof['findings_waived'] is False,
            'report_identity')
    expected = layers(proof['rootfs_layers'], 'report_identity')
    stored_layers = layers(proof['layer_blob_digests'], 'report_identity')
    require(len(stored_layers) == len(expected) and type(proof['expanded_layer_bytes']) is int
            and 0 < proof['expanded_layer_bytes'] <= EXPANDED_BYTES, 'report_identity')
    for key in ('archive_sha256', 'manifest_sha256', 'config_sha256', 'docker_manifest_sha256', 'index_sha256'):
        require(type(proof[key]) is str and re.fullmatch('[a-f0-9]{64}', proof[key]), 'report_identity')
    require(type(proof['docker_image_id']) is str and DIGEST.fullmatch(proof['docker_image_id']), 'report_identity')
    if indexed:
        validate_index_binding(proof['index_binding'], proof['docker_image_id'], proof['manifest_sha256'], 'report_identity')
    require(type(proof['archive_bytes']) is int and 0 < proof['archive_bytes'] <= ARCHIVE_BYTES
            and proof['archive_bytes'] % 512 == 0
            and proof['scanner_config_id'] == 'sha256:' + proof['config_sha256']
            and type(proof['manifest_media_type']) is str and proof['manifest_media_type'] in MEDIA
            and ((proof['kind'] in ('OCI_manifest_to_config', 'Docker_manifest_to_config')
                  and proof['kind'] == ('OCI_manifest_to_config' if proof['manifest_media_type'] == OCI_MANIFEST else 'Docker_manifest_to_config')
                  and proof['docker_image_id'] == 'sha256:' + proof['manifest_sha256'])
                 or (proof['kind'] == 'Docker_config_identity' and proof['docker_image_id'] == proof['scanner_config_id'])
                 or (indexed and proof['manifest_media_type'] == OCI_MANIFEST)),
            'report_identity')
    value = parse(raw, REPORT_BYTES, 'report_identity')
    require(type(value) is dict and type(value.get('SchemaVersion')) is int
            and value['SchemaVersion'] == 2 and value.get('ArtifactType') == 'container_image'
            and type(value.get('Metadata')) is dict, 'report_identity')
    metadata = value['Metadata']
    require(metadata.get('ImageID') == proof['scanner_config_id']
            and type(metadata.get('ImageConfig')) is dict
            and metadata['ImageConfig'].get('rootfs') == {'type': 'layers', 'diff_ids': expected}, 'report_identity')
    return {'image_id': proof['docker_image_id'], 'docker_image_id': proof['docker_image_id'],
            'scanner_image_id': proof['scanner_config_id'], 'transport': 'docker_save_archive',
            'identity_binding': json.loads(json.dumps(proof)),
            'security_accepted': False, 'findings_waived': False}
