"""Bound a normal Docker-save OCI archive to one observed immutable image.

No commands, downloads, extraction, image loads, or inspected code execution.
The audit caller owns its retained file descriptor, one fallback attempt, common
600-second deadline, database/capacity checks, scanner flags and child bounds.
"""
from hashlib import sha256
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
DOCKER_MANIFEST = 'application/vnd.docker.distribution.manifest.v2+json'
MEDIA = {OCI_MANIFEST: ('application/vnd.oci.image.config.v1+json',
                        'application/vnd.oci.image.layer.v1.tar'),
         DOCKER_MANIFEST: ('application/vnd.docker.container.image.v1+json',
                           'application/vnd.docker.image.rootfs.diff.tar')}
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
    require(type(value) is dict and set(value) == {'id', 'rootfs'}
            and value['id'] == image_id and type(value['rootfs']) is dict
            and set(value['rootfs']) == {'Type', 'Layers'}
            and value['rootfs']['Type'] == 'layers', 'observation_shape')
    return {'image_id': image_id,
            'rootfs_layers': layers(value['rootfs']['Layers'], 'observation_shape')}


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


def inventory(reader):
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
        require(name not in members and
                ((directory and name in {'blobs', 'blobs/sha256'} and item.size == 0)
                 or (item.isreg() and (name in {'manifest.json', 'index.json', 'oci-layout'}
                                      or BLOB.fullmatch(name)) and 0 < item.size <= ARCHIVE_BYTES)),
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
    generalizes image identities, not arbitrary archive formats: classic legacy
    compatibility metadata and unknown layer compression refuse pending review.
    """
    require(type(observation) is dict and set(observation) == {'image_id', 'rootfs_layers'},
            'observation_shape')
    image = observation['image_id']
    require(type(image) is str and DIGEST.fullmatch(image), 'observation_shape')
    expected_layers = layers(observation['rootfs_layers'], 'observation_shape')
    reader = ArchiveReader(stream, size, end)
    members, tail = inventory(reader)
    def control(name):
        require(name in members, 'archive_shape')
        offset, length = members[name]
        require(length <= CONTROL_BYTES, 'archive_bound')
        reader.seek(offset)
        raw = reader.read(length)
        return raw, parse(raw, CONTROL_BYTES, 'archive_shape')
    def blob(value):
        require(type(value) is str and DIGEST.fullmatch(value), 'archive_identity')
        return 'blobs/sha256/' + value[7:]
    index_raw, index = control('index.json')
    require(type(index) is dict and type(index.get('schemaVersion')) is int
            and index['schemaVersion'] == 2 and type(index.get('manifests')) is list
            and len(index['manifests']) == 1, 'archive_identity')
    entry = index['manifests'][0]
    require(type(entry) is dict and type(entry.get('mediaType')) is str
            and entry['mediaType'] in MEDIA, 'archive_identity')
    manifest_id = entry.get('digest')
    manifest_raw, manifest = control(blob(manifest_id))
    require(sha256(manifest_raw).hexdigest() == manifest_id[7:]
            and type(entry.get('size')) is int and entry['size'] == len(manifest_raw)
            and type(manifest) is dict and type(manifest.get('schemaVersion')) is int
            and manifest['schemaVersion'] == 2 and manifest.get('mediaType') == entry['mediaType'],
            'archive_identity')
    descriptor = manifest.get('config')
    config_media, layer_media = MEDIA[entry['mediaType']]
    require(type(descriptor) is dict and descriptor.get('mediaType') == config_media, 'archive_identity')
    config_id = descriptor.get('digest')
    config_raw, config = control(blob(config_id))
    require(sha256(config_raw).hexdigest() == config_id[7:]
            and type(descriptor.get('size')) is int and descriptor['size'] == len(config_raw)
            and type(config) is dict and config.get('rootfs') == {'type': 'layers', 'diff_ids': expected_layers},
            'archive_identity')
    require(image in (config_id, manifest_id), 'archive_identity')
    descriptors = manifest.get('layers')
    require(type(descriptors) is list and len(descriptors) == len(expected_layers), 'archive_identity')
    checked, layer_names, blob_ids, expanded = {}, [], [], 0
    gzip_media = layer_media + ('+gzip' if entry['mediaType'] == OCI_MANIFEST else '.gzip')
    for value, descriptor in zip(expected_layers, descriptors):
        require(type(descriptor) is dict, 'archive_identity')
        stored_id = descriptor.get('digest'); name = blob(stored_id)
        require(name in members, 'archive_identity')
        offset, length = members[name]
        media = descriptor.get('mediaType')
        require(type(descriptor.get('size')) is int and descriptor['size'] == length
                and media in (layer_media, gzip_media),
                'archive_identity')
        key = (name, media)
        if key not in checked:
            reader.seek(offset)
            stored_hash, raw_hash, raw_size = layer_digest(reader, length, media == gzip_media, EXPANDED_BYTES - expanded)
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
    files = {'manifest.json', 'index.json', 'oci-layout', blob(manifest_id), blob(config_id), *layer_names}
    require(set(members) - {'blobs', 'blobs/sha256'} == files, 'archive_shape')
    _tail(stream, tail, size, end)
    reader.seek(0)
    archive_hash = digest(reader, size)
    kind = ('OCI_manifest_to_config' if entry['mediaType'] == OCI_MANIFEST else 'Docker_manifest_to_config') if image == manifest_id else 'Docker_config_identity'
    return {'schema': 'runtime_image_archive_binding/1', 'kind': kind,
            'manifest_media_type': entry['mediaType'],
            'docker_image_id': image, 'scanner_config_id': config_id,
            'rootfs_layers': expected_layers, 'layer_blob_digests': blob_ids,
            'expanded_layer_bytes': expanded, 'archive_bytes': size, 'archive_sha256': archive_hash,
            'manifest_sha256': manifest_id[7:], 'config_sha256': config_id[7:],
            'docker_manifest_sha256': sha256(docker_raw).hexdigest(),
            'index_sha256': sha256(index_raw).hexdigest(),
            'security_accepted': False, 'findings_waived': False}


def validate_report(raw, proof):
    """Validate untouched raw report identity; return only public binding fields."""
    keys = {'schema', 'kind', 'manifest_media_type', 'docker_image_id', 'scanner_config_id', 'rootfs_layers',
            'layer_blob_digests', 'expanded_layer_bytes',
            'archive_bytes', 'archive_sha256', 'manifest_sha256', 'config_sha256',
            'docker_manifest_sha256', 'index_sha256', 'security_accepted', 'findings_waived'}
    require(type(proof) is dict and set(proof) == keys
            and proof['schema'] == 'runtime_image_archive_binding/1'
            and proof['security_accepted'] is False and proof['findings_waived'] is False,
            'report_identity')
    expected = layers(proof['rootfs_layers'], 'report_identity')
    stored_layers = layers(proof['layer_blob_digests'], 'report_identity')
    require(len(stored_layers) == len(expected) and type(proof['expanded_layer_bytes']) is int
            and 0 < proof['expanded_layer_bytes'] <= EXPANDED_BYTES, 'report_identity')
    for key in ('archive_sha256', 'manifest_sha256', 'config_sha256', 'docker_manifest_sha256', 'index_sha256'):
        require(type(proof[key]) is str and re.fullmatch('[a-f0-9]{64}', proof[key]), 'report_identity')
    require(type(proof['archive_bytes']) is int and 0 < proof['archive_bytes'] <= ARCHIVE_BYTES
            and proof['archive_bytes'] % 512 == 0
            and proof['scanner_config_id'] == 'sha256:' + proof['config_sha256']
            and type(proof['manifest_media_type']) is str and proof['manifest_media_type'] in MEDIA
            and ((proof['kind'] in ('OCI_manifest_to_config', 'Docker_manifest_to_config')
                  and proof['kind'] == ('OCI_manifest_to_config' if proof['manifest_media_type'] == OCI_MANIFEST else 'Docker_manifest_to_config')
                  and proof['docker_image_id'] == 'sha256:' + proof['manifest_sha256'])
                 or (proof['kind'] == 'Docker_config_identity' and proof['docker_image_id'] == proof['scanner_config_id'])),
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
            'identity_binding': {key: list(proof[key]) if key in ('rootfs_layers','layer_blob_digests') else proof[key] for key in keys},
            'security_accepted': False, 'findings_waived': False}
