"""Ordinary public archive metadata; no native commands or inspected code."""
from contextlib import ExitStack
from copy import deepcopy
from hashlib import sha256
import importlib.util
import gzip
import io
import json
import os
from pathlib import Path
import tarfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('archive_transport_fixture',
    ROOT / 'deploy/maintenance/runtime_image_archive_transport.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def blob(value):
    return 'blobs/sha256/' + value.removeprefix('sha256:')


def ordinary_layer(name, body):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        item = tarfile.TarInfo(name)
        item.size = len(body)
        archive.addfile(item, io.BytesIO(body))
    return stream.getvalue()


# Public format facts from the bounded ordinary F79 save; layer payloads in the
# corresponding regression are synthetic, and contain no image Config values.
ACTUAL_F79_FORMAT = {
    'image_id': 'sha256:f79cdda21c192c52536f4ccfecadc7f724453a9ea4abfae96b1efe540ebbc61b',
    'config_id': 'sha256:1004633961dc344bf7720831835fd27e3a7c9899f3214ac6d67e2734bc77a2e4',
    'manifest_media_type': 'application/vnd.oci.image.manifest.v1+json',
    'archive_member_count': 15,
    'layer_media_types': ['application/vnd.oci.image.layer.v1.tar'] * 6 +
                         ['application/vnd.docker.image.rootfs.diff.tar.gzip'] * 2,
}


def fixture(*, identity='manifest', repeated=False, docker_media=False, compressed=False, stored_layer=None,
            layer_media_types=None):
    media = M.DOCKER_MANIFEST if docker_media else M.OCI_MANIFEST
    config_media, layer_media = M.MEDIA[media]
    media_types = list(layer_media_types) if layer_media_types is not None else [
        layer_media + ('+gzip' if media == M.OCI_MANIFEST else '.gzip') if compressed else layer_media, layer_media]
    payloads = [ordinary_layer('ordinary-one', b'public file one'),
                ordinary_layer('ordinary-two', b'public file two')]
    if layer_media_types is not None:
        payloads = [ordinary_layer('ordinary-'+str(index), ('public file '+str(index)).encode())
                    for index in range(len(media_types))]
    layer_ids = ['sha256:' + sha256(value).hexdigest() for value in payloads]
    first = payloads[0] if stored_layer is None else stored_layer
    payloads[0] = first
    contents = [gzip.compress(raw,mtime=0) if kind in (
        'application/vnd.oci.image.layer.v1.tar+gzip', 'application/vnd.docker.image.rootfs.diff.tar.gzip') else raw
        for raw,kind in zip(payloads,media_types)]
    blob_ids = ['sha256:' + sha256(value).hexdigest() for value in contents]
    if repeated:
        layer_ids.append(layer_ids[0])
        blob_ids.append(blob_ids[0])
    config = {'architecture': 'amd64', 'os': 'linux',
              'rootfs': {'type': 'layers', 'diff_ids': layer_ids},
              'config': {'Env': ['PUBLIC_TEST_DEFAULT=not-for-output']}}
    config_raw = encode(config)
    config_id = 'sha256:' + sha256(config_raw).hexdigest()
    data = dict(zip(blob_ids, contents))
    if repeated:media_types.append(media_types[0])
    manifest = {'schemaVersion': 2, 'mediaType': media,
        'config': {'mediaType': config_media, 'digest': config_id, 'size': len(config_raw)},
        'layers': [{'mediaType': kind, 'digest': value, 'size': len(data[value])} for value,kind in zip(blob_ids,media_types)]}
    manifest_raw = encode(manifest)
    manifest_id = 'sha256:' + sha256(manifest_raw).hexdigest()
    index = {'schemaVersion': 2, 'manifests': [{'mediaType': media,
             'digest': manifest_id, 'size': len(manifest_raw)}]}
    entries = {'manifest.json': encode([{'Config': blob(config_id), 'RepoTags': None,
                                          'Layers': [blob(value) for value in blob_ids]}]),
               'index.json': encode(index), 'oci-layout': encode({'imageLayoutVersion': '1.0.0'}),
               blob(config_id): config_raw, blob(manifest_id): manifest_raw,
               **{blob(value): raw for value, raw in data.items()}}
    image = manifest_id if identity == 'manifest' else config_id
    observation = {'image_id': image, 'os': 'linux', 'architecture': 'amd64', 'rootfs_layers': layer_ids}
    return entries, observation, config_id


def archive_bytes(entries, *, extra=None, directory_entries=True):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        if directory_entries:
            for name in ('blobs/', 'blobs/sha256/'):
                item = tarfile.TarInfo(name)
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
        for name, raw in [*entries.items(), *(extra or [])]:
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            archive.addfile(item, io.BytesIO(raw))
    return stream.getvalue()


def indexed_fixture(*, foreign=False, missing_selected=False):
    """Synthetic payloads, with actual994(25 members) / fe(16 child) shapes."""
    count = 11 if foreign else 14
    types = ['application/vnd.oci.image.layer.v1.tar'] * (count-3) + ['application/vnd.oci.image.layer.v1.tar+gzip'] * 3
    entries, observation, config_id = fixture(layer_media_types=types)
    runtime_id = observation['image_id']; runtime_raw = entries[blob(runtime_id)]
    children = [{'mediaType': M.OCI_MANIFEST, 'digest': runtime_id, 'size': len(runtime_raw),
                 'platform': {'os': 'linux', 'architecture': 'amd64'},
                 'annotations': {'org.opencontainers.image.source': 'public-fixture'}}]
    config = encode({'architecture': 'unknown', 'os': 'unknown', 'rootfs': {'type': 'layers', 'diff_ids': []}})
    auxiliary_config = 'sha256:' + sha256(config).hexdigest(); entries[blob(auxiliary_config)] = config
    statements = []
    for index in range(2 if foreign else 1):
        raw = encode({'_type': 'https://in-toto.io/Statement/v0.1', 'predicateType': 'https://slsa.dev/provenance/v0.2',
                      'subject': [{'name': 'public-fixture', 'digest': {'sha256': runtime_id[7:]}}],
                      'predicate': {'public_fixture_index': index}})
        digest = 'sha256:' + sha256(raw).hexdigest(); entries[blob(digest)] = raw
        statements.append({'mediaType': M.ATTESTATION, 'digest': digest, 'size': len(raw)})
    attestation = encode({'schemaVersion': 2, 'mediaType': M.OCI_MANIFEST,
        'config': {'mediaType': M.MEDIA[M.OCI_MANIFEST][0], 'digest': auxiliary_config, 'size': len(config)},
        'layers': statements})
    attestation_id = 'sha256:' + sha256(attestation).hexdigest(); entries[blob(attestation_id)] = attestation
    children.append({'mediaType': M.OCI_MANIFEST, 'digest': attestation_id, 'size': len(attestation),
                     'platform': {'os': 'unknown', 'architecture': 'unknown'},
                     'annotations': {'vnd.docker.reference.type': 'attestation-manifest',
                                     'vnd.docker.reference.digest': runtime_id}})
    if foreign:
        for architecture,variant in sorted(M.FOREIGN_PLATFORMS):
            platform = {'os': 'linux', 'architecture': architecture}
            if variant is not None:platform['variant'] = variant
            digest = 'sha256:' + sha256(encode(platform)).hexdigest()
            children.append({'mediaType': M.OCI_MANIFEST, 'digest': digest, 'size': 123, 'platform': platform})
            children.append({'mediaType': M.OCI_MANIFEST, 'digest': 'sha256:' + sha256(digest.encode()).hexdigest(), 'size': 124,
                             'platform': {'os': 'unknown', 'architecture': 'unknown'},
                             'annotations': {'vnd.docker.reference.type': 'attestation-manifest', 'vnd.docker.reference.digest': digest}})
    raw = encode({'schemaVersion': 2, 'mediaType': M.OCI_INDEX, 'manifests': children})
    root_id = 'sha256:' + sha256(raw).hexdigest(); entries[blob(root_id)] = raw
    entries['index.json'] = encode({'schemaVersion': 2, 'manifests': [
        {'mediaType': M.OCI_INDEX, 'digest': root_id, 'size': len(raw)}]})
    observation['image_id'] = root_id
    if missing_selected:
        del entries[blob(config_id)]
        for descriptor in json.loads(runtime_raw)['layers']:entries.pop(blob(descriptor['digest']), None)
    return entries, observation, config_id


def rewrite_index(entries, observation, change):
    """Reseal changed public metadata, so negatives reach semantic admission."""
    entries = dict(entries); observation = dict(observation)
    value = json.loads(entries.pop(blob(observation['image_id'])))
    change(value)
    raw = encode(value); digest = 'sha256:' + sha256(raw).hexdigest()
    entries[blob(digest)] = raw
    entries['index.json'] = encode({'schemaVersion': 2, 'manifests': [
        {'mediaType': M.OCI_INDEX, 'digest': digest, 'size': len(raw)}]})
    observation['image_id'] = digest
    return entries, observation


def rewrite_auxiliary(entries, observation, body, *, media=None):
    entries=dict(entries);root=json.loads(entries[blob(observation['image_id'])])
    old=root['manifests'][1];attestation=json.loads(entries.pop(blob(old['digest'])))
    statement=attestation['layers'][0];entries.pop(blob(statement['digest']))
    digest='sha256:'+sha256(body).hexdigest();entries[blob(digest)]=body
    statement.update(digest=digest,size=len(body))
    if media is not None:statement['mediaType']=media
    raw=encode(attestation);digest='sha256:'+sha256(raw).hexdigest();entries[blob(digest)]=raw
    return rewrite_index(entries,observation,lambda v:v['manifests'][1].update(digest=digest,size=len(raw)))


class ArchiveTransportTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name in ('subprocess.run', 'subprocess.Popen', 'os.system', 'os.kill', 'os.killpg',
                     'socket.socket', 'socket.create_connection', 'socket.getaddrinfo',
                     'urllib.request.urlopen', 'ctypes.CDLL', 'resource.setrlimit'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('ambient_host_network_forbidden')))

    def validate(self, entries, observation, **kwargs):
        raw = archive_bytes(entries, **kwargs)
        stream = io.BytesIO(raw)
        proof = M.validate_archive(stream, len(raw), time.monotonic() + 10, observation)
        self.assertFalse(stream.closed)
        return raw, proof

    def test_observation_requires_exact_public_projection_and_strict_json_types(self):
        _, observation, _ = fixture()
        value = {'id': observation['image_id'], 'os': 'linux', 'architecture': 'amd64',
                 'rootfs': {'Type': 'layers', 'Layers': observation['rootfs_layers']}}
        self.assertEqual(M.observe_image(encode(value), observation['image_id']), observation)
        for change in (lambda v: v.update(Config={'Env': ['excluded']}),
                       lambda v: v.update(id='sha256:' + '0' * 64),
                       lambda v: v.pop('os'), lambda v: v.update(architecture='arm64'),
                       lambda v: v.update(os='unknown'),
                       lambda v: v['rootfs'].update(Layers='not-a-list'),
                       lambda v: v['rootfs'].update(Type=True)):
            wrong = deepcopy(value)
            change(wrong)
            with self.assertRaises(M.TransportError): M.observe_image(encode(wrong), observation['image_id'])
        duplicate = b'{"id":"a","id":"b","rootfs":{}}'
        with self.assertRaises(M.TransportError): M.observe_image(duplicate, observation['image_id'])

    def test_manifest_identity_and_distinct_scanner_config_are_bound_without_hardcoded_image(self):
        entries, observation, config_id = fixture()
        raw, proof = self.validate(entries, observation)
        self.assertNotEqual(observation['image_id'], config_id)
        self.assertEqual(proof['kind'], 'OCI_manifest_to_config')
        self.assertEqual(proof['docker_image_id'], observation['image_id'])
        self.assertEqual(proof['scanner_config_id'], config_id)
        self.assertEqual(proof['archive_sha256'], sha256(raw).hexdigest())
        self.assertFalse(proof['security_accepted'])
        self.assertNotIn('not-for-output', json.dumps(proof))

    def test_config_identity_and_docker_media_supported_with_same_full_rootfs(self):
        entries, observation, config_id = fixture(identity='config', docker_media=True)
        _, proof = self.validate(entries, observation, directory_entries=False)
        self.assertEqual(proof['kind'], 'Docker_config_identity')
        self.assertEqual(proof['docker_image_id'], config_id)
        self.assertEqual(proof['rootfs_layers'], observation['rootfs_layers'])
        entries, observation, _ = fixture(docker_media=True)
        _, proof = self.validate(entries, observation)
        self.assertEqual(proof['kind'], 'Docker_manifest_to_config')

    def test_repeated_layer_references_preserve_order_with_one_physical_blob(self):
        entries, observation, _ = fixture(repeated=True)
        _, proof = self.validate(entries, observation)
        self.assertEqual(len(proof['rootfs_layers']), 3)
        self.assertEqual(proof['rootfs_layers'][0], proof['rootfs_layers'][2])

    def test_normal_gzip_stored_digest_and_uncompressed_diffid_both_match(self):
        for docker_media in (False,True):
            entries,observation,_=fixture(compressed=True,repeated=True,docker_media=docker_media)
            _,proof=self.validate(entries,observation)
            self.assertNotEqual(proof['rootfs_layers'][0],proof['layer_blob_digests'][0])
            self.assertEqual(proof['rootfs_layers'][1],proof['layer_blob_digests'][1])
            self.assertEqual(proof['expanded_layer_bytes'],3*10240)

    def test_layer_media_family_is_independent_of_top_manifest_family(self):
        for docker_media,types in ((False, ('application/vnd.docker.image.rootfs.diff.tar',
                                           'application/vnd.docker.image.rootfs.diff.tar.gzip')),
                                  (True, ('application/vnd.oci.image.layer.v1.tar',
                                          'application/vnd.oci.image.layer.v1.tar+gzip'))):
            with self.subTest(docker_manifest=docker_media):
                entries,observation,config_id=fixture(docker_media=docker_media,layer_media_types=types)
                _,proof=self.validate(entries,observation)
                self.assertEqual(proof['docker_image_id'],observation['image_id'])
                self.assertEqual(proof['scanner_config_id'],config_id)
                self.assertEqual(proof['rootfs_layers'],observation['rootfs_layers'])
                self.assertEqual(proof['expanded_layer_bytes'],2*10240)
                self.assertEqual(proof['layer_blob_digests'][0],proof['rootfs_layers'][0])
                self.assertNotEqual(proof['layer_blob_digests'][1],proof['rootfs_layers'][1])
                with self.assertRaisesRegex(M.TransportError,'archive_identity'):
                    self.validate(entries,{**observation,'image_id':'sha256:'+'0'*64})

    def test_actual_f79_fifteen_member_eight_layer_format_preserves_all_bindings(self):
        entries,observation,config_id=fixture(layer_media_types=ACTUAL_F79_FORMAT['layer_media_types'])
        raw,proof=self.validate(entries,observation)
        reader=M.ArchiveReader(io.BytesIO(raw),len(raw),time.monotonic()+10)
        members,_=M.inventory(reader)
        self.assertEqual(len(members),ACTUAL_F79_FORMAT['archive_member_count'])
        self.assertEqual(len(proof['rootfs_layers']),8)
        self.assertEqual(proof['manifest_media_type'],ACTUAL_F79_FORMAT['manifest_media_type'])
        self.assertEqual(proof['rootfs_layers'],observation['rootfs_layers'])
        self.assertEqual(proof['scanner_config_id'],config_id)
        self.assertEqual(proof['expanded_layer_bytes'],8*10240)
        self.assertEqual(proof['layer_blob_digests'][:6],proof['rootfs_layers'][:6])
        self.assertTrue(all(a!=b for a,b in zip(proof['layer_blob_digests'][6:],proof['rootfs_layers'][6:])))
        self.assertNotIn('not-for-output',json.dumps(proof))
        with patch.object(M,'EXPANDED_BYTES',8*10240-1),self.assertRaisesRegex(M.TransportError,'archive_bound'):
            self.validate(entries,observation)
        wrong={**observation,'rootfs_layers':list(reversed(observation['rootfs_layers']))}
        with self.assertRaisesRegex(M.TransportError,'archive_identity'):self.validate(entries,wrong)

    def test_unreviewed_layer_media_never_uses_a_suffix_or_content_decoder_guess(self):
        for kind in ('application/vnd.oci.image.layer.v1.tar+zstd',
                     'application/vnd.docker.image.rootfs.diff.tar.zstd',
                     'application/x-unreviewed.tar.gzip','',False,['application/vnd.oci.image.layer.v1.tar']):
            entries,observation,_=fixture(layer_media_types=(kind,'application/vnd.oci.image.layer.v1.tar'))
            with self.subTest(media=kind),self.assertRaisesRegex(M.TransportError,'archive_identity'):
                self.validate(entries,observation)

    def test_actual994_one_level_index_shape_authenticates_selected_runtime_and_auxiliary_closure(self):
        entries,observation,config_id=indexed_fixture()
        raw,proof=self.validate(entries,observation)
        members,_=M.inventory(M.ArchiveReader(io.BytesIO(raw),len(raw),time.monotonic()+10))
        self.assertEqual(len(members),25)
        self.assertEqual(proof['kind'],'OCI_index_to_manifest_to_config')
        self.assertEqual(proof['docker_image_id'],observation['image_id'])
        self.assertNotEqual(proof['docker_image_id'],proof['scanner_config_id'])
        self.assertEqual(proof['scanner_config_id'],config_id)
        self.assertEqual(len(proof['rootfs_layers']),14)
        binding=proof['index_binding']
        self.assertEqual(binding['root_index_sha256'],observation['image_id'][7:])
        self.assertEqual(binding['selected_manifest_sha256'],proof['manifest_sha256'])
        self.assertEqual(len(binding['descriptors']),2);self.assertEqual(len(binding['auxiliary_blobs']),3)
        self.assertTrue(all(binding[k] is False for k in ('statement_semantics_validated','signatures_verified','provenance_trusted')))
        self.assertNotIn('not-for-output',json.dumps(proof))

    def test_actual_fe_sixteen_child_shape_allows_only_declared_foreign_absence(self):
        entries,observation,_=indexed_fixture(foreign=True)
        _,proof=self.validate(entries,observation)
        rows=proof['index_binding']['descriptors']
        self.assertEqual(len(rows),16);self.assertEqual(sum(not r['present'] for r in rows),14)
        self.assertTrue(all(r['present'] for r in rows if r['kind']=='runtime'))
        missing,observation,_=indexed_fixture(foreign=True,missing_selected=True)
        raw=archive_bytes(missing)
        members,_=M.inventory(M.ArchiveReader(io.BytesIO(raw),len(raw),time.monotonic()+10))
        self.assertEqual(len(members),11)
        with self.assertRaises(M.TransportError):self.validate(missing,observation)

    def test_index_does_not_substitute_observation_platform_config_or_rootfs(self):
        entries,observation,_=indexed_fixture()
        for field,value in (('os','windows'),('architecture','arm64'),('image_id','sha256:'+'0'*64),
                            ('rootfs_layers',list(reversed(observation['rootfs_layers'])))):
            with self.subTest(field=field),self.assertRaises(M.TransportError):
                self.validate(entries,{**observation,field:value})

    def test_index_ambiguous_variant_unknown_platform_nested_media_and_duplicate_child_refuse(self):
        entries,observation,_=indexed_fixture()
        changes=(lambda v:v['manifests'][0]['platform'].update(variant='v1'),
                 lambda v:v['manifests'][0]['platform'].update(architecture='unreviewed'),
                 lambda v:v['manifests'][0].update(mediaType=M.OCI_INDEX),
                 lambda v:v['manifests'].append(deepcopy(v['manifests'][0])),
                 lambda v:v['manifests'][1]['annotations'].update({'vnd.docker.reference.digest':'sha256:'+'0'*64}))
        for change in changes:
            wrong,seen=rewrite_index(entries,observation,change)
            with self.assertRaisesRegex(M.TransportError,'archive_identity'):self.validate(wrong,seen)

    def test_selected_attestation_is_required_and_unknown_missing_references_cannot_be_foreign(self):
        entries,observation,_=indexed_fixture(foreign=True)
        root=json.loads(entries[blob(observation['image_id'])]);selected_attestation=root['manifests'][1]['digest']
        wrong=dict(entries);del wrong[blob(selected_attestation)]
        with self.assertRaisesRegex(M.TransportError,'archive_identity'):self.validate(wrong,observation)
        wrong,seen=rewrite_index(entries,observation,lambda v:v['manifests'].pop(2))
        with self.assertRaisesRegex(M.TransportError,'archive_identity'):self.validate(wrong,seen)

    def test_opaque_nine_subjects_unknown_future_schema_and_non_json_never_select_runtime_or_grant_trust(self):
        entries,observation,config_id=indexed_fixture();_,baseline=self.validate(entries,observation)
        runtime=baseline['manifest_sha256']
        bodies=(encode({'_type':'https://in-toto.io/Statement/v0.1','predicateType':'https://spdx.dev/Document',
                        'subject':[{'digest':{'sha256':runtime}} for _ in range(9)]}),
                encode({'_type':'future-public-schema','predicateType':'unrecognized-future-predicate',
                        'subject':[{'digest':{'sha256':'0'*64}}]}),
                b'raise AssertionError("opaque bytes must never execute")\n')
        actual_parse=M.parse
        for body in bodies:
            changed,seen=rewrite_auxiliary(entries,observation,body)
            def parse(raw,*args,**kwargs):
                self.assertNotEqual(raw,body,'opaque auxiliary body must not be interpreted')
                return actual_parse(raw,*args,**kwargs)
            with patch.object(M,'parse',side_effect=parse):_,proof=self.validate(changed,seen)
            self.assertEqual(proof['manifest_sha256'],runtime)
            self.assertEqual(proof['scanner_config_id'],config_id)
            self.assertEqual(proof['rootfs_layers'],baseline['rootfs_layers'])
            self.assertTrue(all(proof['index_binding'][k] is False for k in
                                ('statement_semantics_validated','signatures_verified','provenance_trusted')))
            with self.assertRaises(M.TransportError):self.validate(changed,{**seen,'rootfs_layers':list(reversed(seen['rootfs_layers']))})

    def test_opaque_auxiliary_does_not_waive_its_mime_size_or_hash_envelope(self):
        entries,observation,_=indexed_fixture()
        for media in ('application/octet-stream','application/vnd.oci.image.layer.v1.tar','application/vnd.in-toto+unknown'):
            changed,seen=rewrite_auxiliary(entries,observation,b'public opaque bytes',media=media)
            with self.assertRaisesRegex(M.TransportError,'archive_identity'):self.validate(changed,seen)
        changed,seen=rewrite_auxiliary(entries,observation,b'X'*(M.CONTROL_BYTES+1))
        with self.assertRaises(M.TransportError):self.validate(changed,seen)

    def test_present_auxiliary_bytes_and_unreferenced_members_are_never_ignored(self):
        entries,observation,_=indexed_fixture();_,proof=self.validate(entries,observation)
        name=blob(proof['index_binding']['auxiliary_blobs'][-1]['digest'])
        wrong=dict(entries);wrong[name]=wrong[name]+b' '
        with self.assertRaisesRegex(M.TransportError,'archive_identity'):self.validate(wrong,observation)
        with self.assertRaisesRegex(M.TransportError,'archive_shape'):
            self.validate(entries,observation,extra=[('blobs/sha256/'+'0'*64,b'public unreferenced metadata')])

    def test_index_report_binding_requires_exact_root_manifest_platform_and_false_flags(self):
        entries,observation,config_id=indexed_fixture();_,proof=self.validate(entries,observation)
        raw=encode({'SchemaVersion':2,'ArtifactType':'container_image','Metadata':{'ImageID':config_id,
            'ImageConfig':{'rootfs':{'type':'layers','diff_ids':observation['rootfs_layers']}}},'Results':[]})
        result=M.validate_report(raw,proof)
        self.assertEqual(result['image_id'],observation['image_id']);self.assertEqual(result['scanner_image_id'],config_id)
        self.assertEqual(result['identity_binding'],proof)
        changes=(lambda p:p['index_binding'].update(root_index_sha256='0'*64),
                 lambda p:p['index_binding'].update(selected_manifest_sha256='0'*64),
                 lambda p:p['index_binding'].update(statement_semantics_validated=0),
                 lambda p:p['index_binding'].update(signatures_verified=0),
                 lambda p:p['index_binding'].update(provenance_trusted=0),
                 lambda p:p['index_binding']['descriptors'][0].update(present=1),
                 lambda p:p['index_binding']['descriptors'][0]['platform'].update(variant='v1'),
                 lambda p:p.update(kind='OCI_manifest_to_config'))
        for change in changes:
            wrong=deepcopy(proof);change(wrong)
            with self.assertRaisesRegex(M.TransportError,'report_identity'):M.validate_report(raw,wrong)

    def test_gzip_expansion_and_decoder_calls_are_bounded_by_bytes_and_deadline(self):
        entries,observation,_=fixture(compressed=True)
        with patch.object(M,'EXPANDED_BYTES',10239),self.assertRaisesRegex(M.TransportError,'archive_bound'):
            self.validate(entries,observation)
        payload=gzip.compress(b'ordinary public file',mtime=0)
        stream=Mock(wraps=io.BytesIO(payload))
        reader=M.ArchiveReader(stream,1024,time.monotonic()+10)
        with patch.object(M.time,'monotonic',return_value=reader.end),self.assertRaisesRegex(M.TransportError,'command_timeout'):
            M.layer_digest(reader,len(payload),True,1024)
        stream.read.assert_not_called()

    def test_gzip_header_change_cannot_hide_behind_an_unchanged_raw_diffid(self):
        entries,observation,_=fixture(compressed=True)
        name=json.loads(entries['manifest.json'])[0]['Layers'][0]
        original=entries[name];changed=bytearray(original);changed[4]^=1;entries[name]=bytes(changed)
        self.assertEqual(gzip.decompress(original),gzip.decompress(entries[name]))
        with self.assertRaisesRegex(M.TransportError,'archive_identity'):self.validate(entries,observation)

    def test_correct_stored_gzip_hash_is_insufficient_when_raw_diffid_is_different(self):
        entries,observation,_=fixture(identity='config',compressed=True,
            stored_layer=ordinary_layer('ordinary-other',b'public alternative file'))
        with self.assertRaisesRegex(M.TransportError,'archive_identity'):self.validate(entries,observation)

    def test_wrong_observed_image_or_reordered_layers_cannot_pass(self):
        entries, observation, _ = fixture()
        for wrong in ({**observation, 'image_id': 'sha256:' + '0' * 64},
                      {**observation, 'rootfs_layers': list(reversed(observation['rootfs_layers']))}):
            with self.assertRaisesRegex(M.TransportError, 'archive_identity'): self.validate(entries, wrong)

    def test_ordinary_layer_body_change_is_detected_even_with_all_control_pins_unchanged(self):
        entries, observation, _ = fixture()
        name = blob(observation['rootfs_layers'][0])
        old = entries[name]
        entries[name] = old.replace(b'public file one', b'public file ONE', 1)
        self.assertEqual(len(entries[name]), len(old))
        with self.assertRaisesRegex(M.TransportError, 'archive_identity'): self.validate(entries, observation)

    def test_index_manifest_config_and_docker_reference_drift_refuse(self):
        original, observation, config_id = fixture()
        for target in ('index.json', blob(observation['image_id']), blob(config_id), 'manifest.json'):
            entries = dict(original)
            if target == 'manifest.json':
                value = json.loads(entries[target]); value[0]['Layers'].reverse(); entries[target] = encode(value)
            elif target == 'index.json':
                value = json.loads(entries[target]); value['manifests'][0]['size'] += 1; entries[target] = encode(value)
            else:
                entries[target] += b' '
            with self.subTest(target=target), self.assertRaises(M.TransportError): self.validate(entries, observation)

    def test_duplicate_extra_missing_and_nonregular_outer_members_refuse(self):
        entries, observation, _ = fixture()
        for extra in ([('manifest.json', entries['manifest.json'])], [('unrelated.json', b'{}')],
                      [('blobs/sha256/' + 'f' * 64, b'ordinary extra public blob')]):
            with self.subTest(extra=extra[0][0]), self.assertRaisesRegex(M.TransportError, 'archive_shape'):
                self.validate(entries, observation, extra=extra)
        missing = dict(entries); del missing['oci-layout']
        with self.assertRaises(M.TransportError): self.validate(missing, observation)
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w', format=tarfile.USTAR_FORMAT) as archive:
            item = tarfile.TarInfo('manifest.json'); item.type = tarfile.SYMTYPE; item.linkname = 'ordinary-public-alias'; archive.addfile(item)
        raw = stream.getvalue()
        with self.assertRaisesRegex(M.TransportError, 'archive_shape'):
            M.validate_archive(io.BytesIO(raw), len(raw), time.monotonic()+10, observation)

    def test_control_document_and_layer_count_bounds_precede_large_reads(self):
        entries, observation, _ = fixture()
        with patch.object(M, 'CONTROL_BYTES', 1), self.assertRaisesRegex(M.TransportError, 'archive_bound'):
            self.validate(entries, observation)
        wrong = {**observation, 'rootfs_layers': ['sha256:'+'0'*64]*(M.MAX_LAYERS+1)}
        stream = Mock()
        with self.assertRaisesRegex(M.TransportError, 'observation_shape'):
            M.validate_archive(stream, 10240, time.monotonic()+10, wrong)
        stream.read.assert_not_called()

    def test_normal_long_name_extension_is_explicitly_unsupported_before_payload_read(self):
        _, observation, _ = fixture()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream,mode='w',format=tarfile.PAX_FORMAT) as archive:
            item=tarfile.TarInfo('ordinary-public-component/'*8+'notes.txt');item.size=4
            archive.addfile(item,io.BytesIO(b'note'))
        raw=stream.getvalue();stream=Mock(wraps=io.BytesIO(raw))
        with self.assertRaisesRegex(M.TransportError,'archive_shape'):
            M.validate_archive(stream,len(raw),time.monotonic()+10,observation)
        self.assertEqual(stream.read.call_args_list,[unittest.mock.call(512)])

    def test_reader_refuses_outside_read_seek_and_deadline_before_underlying_access(self):
        underlying = io.BytesIO(bytes(1024)); stream = Mock(wraps=underlying)
        reader = M.ArchiveReader(stream, 1024, time.monotonic()+10)
        for operation in (lambda: reader.read(1025), lambda: reader.read(-1),
                          lambda: reader.seek(-1), lambda: reader.seek(1025),
                          lambda: reader.seek(1, os.SEEK_END)):
            stream.reset_mock()
            with self.assertRaisesRegex(M.TransportError, 'archive_bound'): operation()
            stream.read.assert_not_called(); stream.seek.assert_not_called()
        with patch.object(M.time, 'monotonic', return_value=reader.end), self.assertRaisesRegex(M.TransportError, 'command_timeout'):
            reader.read(1)
        stream.read.assert_not_called()
        reader.close(); self.assertFalse(underlying.closed)

    def test_size_and_finite_shared_image_deadline_refuse_before_stream_reads(self):
        entries, observation, _ = fixture()
        for size, end in ((M.ARCHIVE_BYTES+512,time.monotonic()+10), (True,time.monotonic()+10),
                          (10240,0), (10240,float('inf')), (10240,time.monotonic()+601)):
            stream = Mock()
            with self.subTest(size=size,end=end), self.assertRaises(M.TransportError):
                M.validate_archive(stream, size, end, observation)
            stream.read.assert_not_called(); stream.seek.assert_not_called()

    def test_growing_zero_tail_is_bounded_and_deadline_checked(self):
        entries, observation, _ = fixture(); raw = archive_bytes(entries)
        class Growing(io.BytesIO):
            def read(self, size=-1):
                block = super().read(size)
                return block if block else b'\0' * size
        with self.assertRaisesRegex(M.TransportError, 'archive_bound'):
            M.validate_archive(Growing(raw), len(raw), time.monotonic()+10, observation)
        stream = Mock(wraps=io.BytesIO(bytes(1024)))
        with patch.object(M.time,'monotonic',return_value=10), self.assertRaisesRegex(M.TransportError,'command_timeout'):
            M._tail(stream,0,1024,10)
        stream.seek.assert_not_called();stream.read.assert_not_called()

    def test_nonzero_or_short_tail_and_truncated_regular_payload_refuse(self):
        entries, observation, _ = fixture(); raw = archive_bytes(entries)
        for changed in (raw[:-512], raw[:-1]+b'X', raw[:1024]):
            with self.subTest(length=len(changed)), self.assertRaises(M.TransportError):
                M.validate_archive(io.BytesIO(changed), len(raw), time.monotonic()+10, observation)

    def test_report_binds_both_ids_exact_layers_without_rewriting_raw_or_exposing_config(self):
        entries, observation, config_id = fixture(); _, proof = self.validate(entries, observation)
        value = {'SchemaVersion':2,'ArtifactType':'container_image',
                 'Metadata':{'ImageID':config_id,'ImageConfig':{'rootfs':{'type':'layers','diff_ids':observation['rootfs_layers']},
                     'config':{'Env':['PUBLIC_TEST_DEFAULT=not-for-output']}}},'Results':[]}
        raw = encode(value); before = sha256(raw).hexdigest(); result = M.validate_report(raw, proof)
        self.assertEqual(sha256(raw).hexdigest(), before)
        self.assertEqual(result['image_id'], observation['image_id'])
        self.assertEqual(result['scanner_image_id'], config_id)
        self.assertNotIn('not-for-output', json.dumps(result)); self.assertFalse(result['findings_waived'])
        for change in (lambda v:v['Metadata'].update(ImageID=observation['image_id']),
                       lambda v:v['Metadata']['ImageConfig']['rootfs']['diff_ids'].reverse(),
                       lambda v:v.update(SchemaVersion=True)):
            wrong = deepcopy(value); change(wrong)
            with self.assertRaisesRegex(M.TransportError,'report_identity'): M.validate_report(encode(wrong),proof)
        wrong = {**proof,'security_accepted':True}
        with self.assertRaisesRegex(M.TransportError,'report_identity'):M.validate_report(raw,wrong)

    def test_fallback_once_for_command_failure_never_timeout_global_integrity_or_capacity(self):
        self.assertTrue(M.may_fallback('command_failed',False))
        self.assertFalse(M.may_fallback('command_failed',True))
        for code in ('command_timeout','batch_timeout','database_changed','database_freshness',
                     'inventory_drift','storage_budget','storage_reserve','resource_contract','report_identity'):
            self.assertFalse(M.may_fallback(code,False))


if __name__ == '__main__':
    unittest.main()
