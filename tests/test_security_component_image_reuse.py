"""Public synthetic archives only; ambient commands and network are denied."""
import ast
from contextlib import ExitStack, nullcontext
from copy import deepcopy
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from unittest.mock import MagicMock, patch
from zipfile import ZipFile, ZipInfo
import gzip

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/security-component-presence-reuse.yml'
SPEC = importlib.util.spec_from_file_location('image_reuse_fixture', ROOT / 'scripts/admit_security_component_image_reuse.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

# Literal Go v28 zero-value container.Config JSON, including the non-omitempty
# OnBuild field. Keep this fixture independent of the admission implementation.
ZERO_CONTAINER = (b'{"Hostname":"","Domainname":"","User":"","AttachStdin":false,'
    b'"AttachStdout":false,"AttachStderr":false,"Tty":false,"OpenStdin":false,'
    b'"StdinOnce":false,"Env":null,"Cmd":null,"Image":"","Volumes":null,'
    b'"WorkingDir":"","Entrypoint":null,"OnBuild":null,"Labels":null}')


def legacy_fixture(config, *, os_before_id=False, wrong_chain_order=False):
    """Synthetic ASCII saveImage fixture; no admission helper generates it."""
    dumps = lambda value: json.dumps(value, separators=(',', ':')).encode('ascii')
    zero = json.loads(ZERO_CONTAINER)
    final = dict(zero)
    final.update(config['config'])
    final['Labels'] = dict(sorted(final['Labels'].items()))
    previous_id = None
    chain = None
    entries = []
    diffs = config['rootfs']['diff_ids']
    for number, diff in enumerate(diffs):
        next_diff = diffs[-1-number] if wrong_chain_order else diff
        chain = next_diff if chain is None else 'sha256:' + sha256((chain+' '+next_diff).encode()).hexdigest()
        fields = {'created': '1970-01-01T00:00:00Z', 'container_config': zero}
        if number == len(diffs)-1:
            fields.update(created=config['created'], config=final, architecture='amd64', os='linux')
        elif os_before_id:
            fields['os'] = 'linux'
        hashed = dict(fields, layer_id=chain)
        if previous_id:
            hashed['parent'] = 'sha256:' + previous_id
        # RawMessage maps sort their keys; nested struct JSON retains order.
        identifier = sha256(dumps(dict(sorted(hashed.items())))).hexdigest()
        saved = {'id': identifier}
        if previous_id:
            saved['parent'] = previous_id
        saved.update(fields)
        saved['os'] = 'linux'
        raw = dumps(saved)
        entries.append(('blobs/sha256/'+sha256(raw).hexdigest(), raw, tarfile.REGTYPE))
        previous_id = identifier
    return entries


def blocks():
    return [textwrap.dedent(value) for value in re.findall(
        r"(?ms)^          python3 -I -B - <<'PY'\n(.*?)^          PY$", WORKFLOW.read_text())]


def actionlint(executable):
    require = lambda value: None if value else (_ for _ in ()).throw(AssertionError('workflow_semantics'))
    source = WORKFLOW.read_text()
    # A bad context must be diagnosed by the real parser, not a string check.
    invalid = source.replace('GH_TOKEN: ${{ github.token }}', 'GH_TOKEN: ${{ failure() }}')
    require(invalid != source)
    with tempfile.TemporaryDirectory(prefix='component-reuse-actionlint-') as folder:
        config = Path(folder) / 'actionlint.yaml'; config.write_text('{}\n')
        arguments = [str(executable), '-no-color', '-shellcheck=', '-pyflakes=', '-config-file', str(config),
                     '-format', '{{json .}}', '-stdin-filename', WORKFLOW.name, '-']
        valid = subprocess.run(arguments, input=source, capture_output=True, text=True, timeout=15)
        require(valid.returncode == 0 and not valid.stderr and json.loads(valid.stdout) == [])
        bad = subprocess.run(arguments, input=invalid, capture_output=True, text=True, timeout=15)
        errors = json.loads(bad.stdout)
        require(bad.returncode == 1 and not bad.stderr and len(errors) == 1
                and errors[0]['kind'] == 'expression' and 'failure' in errors[0]['message'])
    return {'status': 'passed', 'workflow_valid': True, 'invalid_environment_expression_rejected': True}


class ComponentImageReuseTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        for name in ('subprocess.Popen', 'subprocess.run', 'os.system', 'socket.socket',
                     'socket.create_connection', 'socket.getaddrinfo', 'urllib.request.urlopen'):
            self.stack.enter_context(patch(name, side_effect=AssertionError('no_host_or_network')))
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='.image-reuse-', dir=ROOT)))
        self.root.chmod(0o700)
        self.zip = self.root / 'artifact.zip'
        self.output = self.root / 'admitted'

    def tar(self, entries):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w') as bundle:
            for name, data, kind in entries:
                member = tarfile.TarInfo(name); member.type = kind; member.mode = 0o644
                member.size = len(data) if kind == tarfile.REGTYPE else 0
                if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                    member.linkname = '/outside-fixture'
                bundle.addfile(member, io.BytesIO(data) if member.isfile() else None)
        return output.getvalue()

    def image(self, *, oci=False, layer_gzip=False, config_changes=None, extra=None, repeated_empty=False,
              legacy=False):
        layer = b'public synthetic image-layer bytes'
        empty = b'\0' * 1024
        layers = [layer, empty, b'public second layer', empty, empty] if repeated_empty else [layer]
        packed = [gzip.compress(value, mtime=0) if layer_gzip else value for value in layers]
        labels = {'org.opencontainers.image.revision': M.BASELINE, 'io.gold-trade.release.tree': M.PAYLOAD_TREE,
                  'io.gold-trade.security.overlay.revision': M.SOURCE, 'io.gold-trade.security.overlay.tree': M.SOURCE_TREE,
                  'io.gold-trade.security.payload.sha256': M.MANIFEST_SHA256}
        config = {'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': labels},
                  'rootfs': {'type': 'layers', 'diff_ids': ['sha256:' + sha256(value).hexdigest() for value in layers]}}
        if legacy:
            config.update(created='2026-10-24T07:49:39.591986633Z', history=[])
            config['config'].update(Cmd=['/public-command'], Env=['PUBLIC_FIXTURE=1'], WorkingDir='/public')
        config.update(config_changes or {})
        raw = json.dumps(config, sort_keys=True).encode()
        image = 'sha256:' + sha256(raw).hexdigest()
        config_name = ('blobs/sha256/' if oci else '') + image[7:] + ('' if oci else '.json')
        layer_names = [('blobs/sha256/' + sha256(value).hexdigest()) if oci else
                       (sha256(value).hexdigest() if repeated_empty else 'a' * 64) + '/layer.tar'
                       for value in packed]
        manifest = [{'Config': config_name, 'Layers': layer_names, 'RepoTags': None}]
        entries = [('manifest.json', json.dumps(manifest).encode(), tarfile.REGTYPE),
                   (config_name, raw, tarfile.REGTYPE)]
        # Ordered references may repeat; the TAR stores each addressed blob once.
        entries += [(name, data, tarfile.REGTYPE) for name, data in dict(zip(layer_names, packed)).items()]
        if oci:
            descriptor = lambda data, media: {'mediaType': media, 'size': len(data), 'digest': 'sha256:' + sha256(data).hexdigest()}
            image_manifest = json.dumps({'schemaVersion': 2,
                'config': descriptor(raw, 'application/vnd.oci.image.config.v1+json'),
                'layers': [descriptor(value, 'application/vnd.oci.image.layer.v1.tar+gzip' if layer_gzip else 'application/vnd.oci.image.layer.v1.tar') for value in packed]}).encode()
            entries += [('blobs/sha256/' + sha256(image_manifest).hexdigest(), image_manifest, tarfile.REGTYPE),
                        ('index.json', json.dumps({'schemaVersion': 2, 'manifests': [descriptor(image_manifest, 'application/vnd.oci.image.manifest.v1+json')]}).encode(), tarfile.REGTYPE),
                        ('oci-layout', b'{"imageLayoutVersion":"1.0.0"}', tarfile.REGTYPE)]
        if legacy:
            entries += legacy_fixture(config)
        self.stack.enter_context(patch.object(M, 'IMAGE', image))
        return self.tar(entries + (extra or [])), entries

    def package(self, tar=None, *, changes=None, zip_extra=None):
        if tar is None:
            tar, _ = self.image()
        compressed = gzip.compress(tar, mtime=0)
        record = {'schema': 'security_consumer_image_export/1', 'status': 'exported_not_accepted', 'image_id': M.IMAGE,
            'provenance': {'image_id': M.IMAGE, 'baseline_commit': M.BASELINE, 'source_commit': M.SOURCE,
                'source_tree': M.SOURCE_TREE, 'payload_tree': M.PAYLOAD_TREE,
                'payload_manifest_sha256': M.MANIFEST_SHA256, 'frontend_dist_sha256': 'f' * 64, 'deployment_performed': False},
            'archive': {'name': 'candidate-image.tar.gz', 'format': 'docker-archive+gzip', 'size_bytes': len(compressed),
                        'sha256': sha256(compressed).hexdigest(), 'uncompressed_bytes': len(tar)},
            'elapsed_seconds': 1.0, 'runtime_data_included': False, 'secrets_disclosed': False, 'deployment_performed': False}
        if changes:
            changes(record)
        with ZipFile(self.zip, 'w') as archive:
            archive.writestr('image-export.json', json.dumps(record))
            archive.writestr('candidate-image.tar.gz', compressed)
            for name, data in zip_extra or []:
                archive.writestr(name, data)
        self.zip.chmod(0o600)
        self.stack.enter_context(patch.object(M, 'ZIP_BYTES', self.zip.stat().st_size))
        self.stack.enter_context(patch.object(M, 'ZIP_SHA256', sha256(self.zip.read_bytes()).hexdigest()))
        return record

    def admit(self):
        return M.admit_image(self.zip, self.output)

    def test_classic_archive_is_verified_without_any_host_command(self):
        self.package()
        result = self.admit()
        self.assertEqual(result['layout'], 'docker')
        self.assertEqual(result['image_id'], M.IMAGE)
        self.assertEqual(result['layers'], 1)
        self.assertFalse(result['security_accepted'])
        self.assertFalse(result['deployment_performed'])
        self.assertFalse(result['image_rebuilt'])
        self.assertEqual({p.name for p in self.output.iterdir()}, M.FILES | {'candidate-image.tar'})
        self.assertTrue(all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in self.output.iterdir()))

    def test_oci_backed_docker_export_validates_descriptor_and_compressed_layer_hashes(self):
        tar, _ = self.image(oci=True, layer_gzip=True)
        self.package(tar)
        self.assertEqual(self.admit()['layout'], 'oci-docker')

    def test_classic_and_oci_repeated_empty_layer_references_preserve_order(self):
        self.assertEqual(sha256(b'\0' * 1024).hexdigest(),
                         '5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef')
        for oci in (False, True):
            raw, entries = self.image(oci=oci, layer_gzip=oci, repeated_empty=True)
            path = self.root / ('repeated-oci.tar' if oci else 'repeated-classic.tar')
            path.write_bytes(raw); path.chmod(0o600)
            references = json.loads(entries[0][1])[0]['Layers']
            self.assertEqual(len(references), 5)
            self.assertEqual(len(set(references)), 3)
            self.assertEqual(references[1], references[3])
            self.assertEqual(references[3], references[4])
            self.assertEqual(sum(name == references[1] for name, _, _ in entries), 1)
            with self.subTest(oci=oci):
                result = M.validate_docker_archive(path)
                self.assertEqual(result['layers'], 5)
                self.assertEqual(result['layout'], 'oci-docker' if oci else 'docker')

    def test_repeated_empty_layers_still_check_every_ordered_rootfs_position(self):
        for oci in (False, True):
            _, valid = self.image(oci=oci, repeated_empty=True)
            expected = json.loads(valid[1][1])['rootfs']['diff_ids']
            swapped = list(expected); swapped[0], swapped[1] = swapped[1], swapped[0]
            wrong_repetition = list(expected); wrong_repetition[4] = 'sha256:' + '0' * 64
            for diff_ids in (swapped, wrong_repetition):
                raw, _ = self.image(oci=oci, repeated_empty=True,
                    config_changes={'rootfs': {'type': 'layers', 'diff_ids': diff_ids}})
                path = self.root / 'wrong-ordered-rootfs.tar'
                path.write_bytes(raw); path.chmod(0o600)
                with self.subTest(oci=oci, diff_ids=diff_ids), self.assertRaisesRegex(RuntimeError, 'reuse_layer_diff_id'):
                    M.validate_docker_archive(path)

    def test_repeated_references_do_not_allow_duplicate_tar_entries_or_extra_blobs(self):
        for oci in (False, True):
            _, entries = self.image(oci=oci, repeated_empty=True)
            references = json.loads(entries[0][1])[0]['Layers']
            repeated_blob = next(entry for entry in entries if entry[0] == references[1])
            for extra, reason in ((repeated_blob, 'reuse_tar_member_invalid'),
                                  (('blobs/sha256/' + 'f' * 64, b'unreferenced', tarfile.REGTYPE),
                                   'reuse_tar_closed_inventory')):
                path = self.root / 'wrong-repeated-inventory.tar'
                path.write_bytes(self.tar(entries + [extra])); path.chmod(0o600)
                with self.subTest(oci=oci, reason=reason), self.assertRaisesRegex(RuntimeError, reason):
                    M.validate_docker_archive(path)

    def test_moby_v28_legacy_ids_match_independent_actual_public_vectors(self):
        # CI40117617410 public metadata and the original image's public DiffIDs.
        # A fourth layer keeps all three known vectors intermediate, independent
        # of the original final config's values.
        diffs = ['a70f6272827f64e1427d048ced77491aab5d64ab95725e56920802ff391197d7',
                 '535a5d38abecfac4fa49da33fa942b53c53c463bd0d39a860967e46eb03faafe',
                 '6972161a9a77e7c1afb997daac15abc54f5b70c7b40b814601f53e038d8c398c', 'f'*64]
        config = {'architecture': 'amd64', 'os': 'linux', 'created': '2026-10-24T07:49:39.591986633Z',
                  'history': [], 'rootfs': {'type': 'layers', 'diff_ids': ['sha256:'+item for item in diffs]},
                  'config': {'Cmd': ['/public'], 'Env': [], 'Labels': {}, 'WorkingDir': '/'}}
        expected = [
            ('5f8d3200190edddbf5cd381cf2c0fd3499122fb6801ec16e341d65f1aed02e80',
             '90dd2996262ff4fe6242a91a91ddff653fb306f80142897b14bd0e441e3f2738', None, 401),
            ('a58a5e6856bc2d06f9cd3feb5c88867de173d42f0d80a25e5f94ea213190ed8f',
             '50a8212d1810941b7b5f89aa49afe3d7cba9615e554e85f54926f733f7d8af0c',
             '90dd2996262ff4fe6242a91a91ddff653fb306f80142897b14bd0e441e3f2738', 477),
            ('11bee0262d6c99d2270befe57d3c9df1c4d12b486aec69445c5912179ceae315',
             '7af4b97e4e656685e9a0b0e4f89b5b747ea41087825aeef9a76b3d88f9f924b8',
             '50a8212d1810941b7b5f89aa49afe3d7cba9615e554e85f54926f733f7d8af0c', 477)]
        generated = list(M.moby_legacy_configs(config).items())
        for (name, raw), (blob, identifier, parent, size) in zip(generated, expected):
            self.assertEqual(name, 'blobs/sha256/'+blob)
            self.assertEqual(len(raw), size)
            value = json.loads(raw)
            self.assertEqual(value['id'], identifier)
            self.assertEqual(value.get('parent'), parent)
            self.assertEqual(value['container_config'], json.loads(ZERO_CONTAINER))
            self.assertEqual(value['os'], 'linux')
            self.assertEqual(value['created'], '1970-01-01T00:00:00Z')
        reference = legacy_fixture(config)
        self.assertEqual(list(M.moby_legacy_configs(config).items()), [(name, raw) for name, raw, _ in reference])

    def test_oci_moby_compatibility_chain_is_checked_with_repeated_ordered_layers(self):
        for repeated in (False, True):
            raw, entries = self.image(oci=True, legacy=True, repeated_empty=repeated)
            path = self.root/'moby-compatibility.tar'
            path.write_bytes(raw); path.chmod(0o600)
            result = M.validate_docker_archive(path)
            expected_count = 5 if repeated else 1
            self.assertEqual(result['layers'], expected_count)
            self.assertEqual(result['legacy_compatibility_configs'], expected_count)
            self.assertEqual(result['docker_archive_sha256'], sha256(raw).hexdigest())
            configs = [json.loads(data) for name, data, _ in entries
                       if name.startswith('blobs/') and data.startswith(b'{"id":')]
            self.assertEqual(len(configs), expected_count)
            self.assertEqual(configs[-1]['created'], '2026-10-24T07:49:39.591986633Z')
            self.assertEqual(configs[-1]['config']['Env'], ['PUBLIC_FIXTURE=1'])

    def test_legacy_chain_requires_exact_all_nodes_and_refuses_other_extras(self):
        _, entries = self.image(oci=True, legacy=True, repeated_empty=True)
        configs = [entry for entry in entries if entry[1].startswith(b'{"id":')]
        ordinary = [entry for entry in entries if entry not in configs]
        path = self.root/'wrong-compatibility-closure.tar'
        invalid = [configs[:-1], configs[1:], configs+[configs[0]],
                   configs+[('blobs/sha256/'+'e'*64, b'{}', tarfile.REGTYPE)]]
        for index, altered in enumerate(invalid):
            path.write_bytes(self.tar(ordinary+altered)); path.chmod(0o600)
            reason = 'reuse_tar_member_invalid' if index == 2 else 'reuse_tar_closed_inventory'
            with self.subTest(index=index), patch.object(M.sys, 'stderr', io.StringIO()), \
                    self.assertRaisesRegex(RuntimeError, reason):
                M.validate_docker_archive(path)

    def test_legacy_chain_rejects_noncanonical_zero_final_parent_os_and_blob_bytes(self):
        _, entries = self.image(oci=True, legacy=True, repeated_empty=True)
        configs = [entry for entry in entries if entry[1].startswith(b'{"id":')]
        ordinary = [entry for entry in entries if entry not in configs]
        config = json.loads(entries[1][1])
        path = self.root/'wrong-compatibility-content.tar'
        wrong = [legacy_fixture(config, os_before_id=True),
                 legacy_fixture(config, wrong_chain_order=True)]
        for index, change in ((0, lambda value: value['container_config'].update(Env=[])),
                              (1, lambda value: value.update(parent='sha256:'+value['parent'])),
                              (4, lambda value: value['config'].update(WorkingDir='/altered')),
                              (4, lambda value: value.update(created='2026-10-24T07:49:39Z')),
                              (4, lambda value: value.update(architecture='arm64'))):
            altered = list(configs)
            name, data, kind = altered[index]
            value = json.loads(data); change(value)
            raw = json.dumps(value, separators=(',', ':')).encode()
            altered[index] = ('blobs/sha256/'+sha256(raw).hexdigest(), raw, kind)
            wrong.append(altered)
        # Content-addressed names alone are insufficient: even a wrong byte
        # with the original expected path must be refused by exact content.
        altered = list(configs)
        name, data, kind = altered[0]
        changed = data.replace(b'"os":"linux"', b'"os":"other"')
        self.assertNotEqual(changed, data); self.assertEqual(len(changed), len(data))
        altered[0] = (name, changed, kind)
        wrong.append(altered)
        for index, altered in enumerate(wrong):
            path.write_bytes(self.tar(ordinary+altered)); path.chmod(0o600)
            with self.subTest(index=index), patch.object(M.sys, 'stderr', io.StringIO()), \
                    self.assertRaisesRegex(RuntimeError, '^reuse_tar_closed_inventory$'):
                M.validate_docker_archive(path)

    def test_legacy_projection_refuses_unrepresented_source_config_fields(self):
        _, entries = self.image(oci=True, legacy=True)
        config = json.loads(entries[1][1])
        for key, value in (('config', dict(config['config'], User='extra')), ('config', None),
                           ('author', 'unrepresented'), ('created', 'not-a-timestamp')):
            bad = dict(config); bad[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, '^reuse_legacy_'):
                M.moby_legacy_configs(bad)

    def test_go_json_escape_and_map_projection_keep_struct_order(self):
        self.assertEqual(M.moby_go_json({'value': '<>&\u2028\u2029\n"\\'}),
                         b'{"value":"\\u003c\\u003e\\u0026\\u2028\\u2029\\n\\"\\\\"}')
        value = M.moby_container_config({'Cmd': ['public'], 'Env': [],
                                        'Labels': {'z': 'last', 'a': 'first'}, 'WorkingDir': '/'})
        raw = M.moby_go_json(value)
        self.assertLess(raw.index(b'"Env"'), raw.index(b'"Cmd"'))
        self.assertIn(b'"Labels":{"a":"first","z":"last"}', raw)
        self.assertIn(b'"OnBuild":null', raw)

    def test_wrong_zip_digest_or_size_refuses_before_output_creation(self):
        self.package()
        for key, value in (('ZIP_BYTES', self.zip.stat().st_size - 1), ('ZIP_SHA256', '0' * 64)):
            with self.subTest(key=key), patch.object(M, key, value):
                with self.assertRaises(RuntimeError): self.admit()
            self.assertFalse(self.output.exists())

    def test_zip_closed_inventory_rejects_extra_and_traversal_paths(self):
        self.package(zip_extra=[('../outside', b'public')])
        with self.assertRaisesRegex(RuntimeError, 'reuse_zip_inventory'): self.admit()
        self.assertFalse(self.output.exists())

    def test_zip_duplicate_member_refuses(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            self.package(zip_extra=[('image-export.json', b'{}')])
        with self.assertRaisesRegex(RuntimeError, 'reuse_zip_inventory'): self.admit()

    def test_zip_symlink_refuses(self):
        self.package()
        entries = {}
        with ZipFile(self.zip) as archive:
            entries = {row.filename: archive.read(row) for row in archive.infolist()}
        with ZipFile(self.zip, 'w') as archive:
            for name, raw in entries.items():
                entry = ZipInfo(name); entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(entry, raw)
        with patch.object(M, 'ZIP_BYTES', self.zip.stat().st_size), patch.object(M, 'ZIP_SHA256', sha256(self.zip.read_bytes()).hexdigest()):
            with self.assertRaisesRegex(RuntimeError, 'reuse_zip_member'): self.admit()

    def test_export_exact_provenance_and_false_flags_are_required(self):
        record = self.package()
        for field, value in (('source_commit', '0' * 40), ('source_tree', '0' * 40),
                             ('baseline_commit', '0' * 40), ('payload_tree', '0' * 40),
                             ('payload_manifest_sha256', '0' * 64), ('deployment_performed', 0)):
            bad = deepcopy(record); bad['provenance'][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError): M.validate_export(bad)
        for field in ('runtime_data_included', 'secrets_disclosed', 'deployment_performed'):
            bad = deepcopy(record); bad[field] = 0
            with self.subTest(field=field), self.assertRaises(RuntimeError): M.validate_export(bad)

    def test_export_sizes_reject_boolean_and_excess_expansion(self):
        record = self.package()
        for field, value in (('size_bytes', True), ('uncompressed_bytes', True),
                             ('uncompressed_bytes', M.MAX_TAR + 1)):
            bad = deepcopy(record); bad['archive'][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError): M.validate_export(bad)

    def test_gzip_declared_size_is_a_streaming_limit(self):
        self.package(changes=lambda row: row['archive'].update(uncompressed_bytes=1024))
        with self.assertRaisesRegex(RuntimeError, 'reuse_gzip_expansion'): self.admit()
        self.assertLessEqual((self.output / 'candidate-image.tar').stat().st_size, 1024)

    def test_tar_traversal_links_devices_and_duplicate_names_refuse(self):
        _, entries = self.image()
        for name, kind in (('../outside', tarfile.REGTYPE), ('link', tarfile.SYMTYPE),
                           ('link', tarfile.LNKTYPE), ('device', tarfile.CHRTYPE), ('fifo', tarfile.FIFOTYPE),
                           ('manifest.json', tarfile.REGTYPE)):
            raw = self.tar(entries + [(name, b'{}', kind)])
            path = self.root / 'invalid.tar'; path.write_bytes(raw); path.chmod(0o600)
            with self.subTest(name=name, kind=kind), self.assertRaisesRegex(RuntimeError, 'reuse_tar_member_invalid'):
                M.validate_docker_archive(path)

    def test_tar_extra_file_and_trailing_second_archive_refuse(self):
        raw, _ = self.image(extra=[('unexpected', b'public', tarfile.REGTYPE)])
        path = self.root / 'extra.tar'; path.write_bytes(raw); path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'reuse_tar_closed_inventory'): M.validate_docker_archive(path)
        good, _ = self.image(); path.write_bytes(good + b'payload-after-zero-trailer')
        with self.assertRaisesRegex(RuntimeError, 'reuse_tar_trailing_payload'): M.validate_docker_archive(path)

    def test_closed_inventory_diagnostic_is_public_escaped_bounded_and_still_refuses(self):
        _, entries = self.image()
        parent = entries[2][0].rsplit('/', 1)[0]
        unexpected = [('extra-%03d-"' % index + '\U0001f642' * 300, b'public', tarfile.REGTYPE)
                      for index in range(80)]
        # This path belongs to the required directory closure but is a file.
        raw = self.tar(entries + unexpected + [(parent, b'', tarfile.REGTYPE)])
        path = self.root / 'public-closed-inventory-diagnostic.tar'
        path.write_bytes(raw); path.chmod(0o600)
        error = io.StringIO()
        with patch.object(M.sys, 'stderr', error):
            with self.assertRaisesRegex(RuntimeError, '^reuse_tar_closed_inventory$'):
                M.validate_docker_archive(path)
        emitted = error.getvalue()
        self.assertLessEqual(len(emitted.encode('utf-8')), 16 * 1024)
        self.assertEqual(len(emitted.splitlines()), 1)
        self.assertTrue(emitted.isascii())
        self.assertIn('\\"', emitted)
        self.assertIn('\\ud83d\\ude42', emitted)
        record = json.loads(emitted)
        self.assertEqual(record['code'], 'reuse_tar_closed_inventory')
        self.assertEqual(record['unexpected_count'], 80)
        self.assertEqual(record['wrong_directory_type_count'], 1)
        self.assertEqual(record['wrong_directory_type_names'], [parent])
        self.assertTrue(record['names_or_characters_omitted'])
        for key in ('unexpected_names', 'wrong_directory_type_names'):
            self.assertLessEqual(len(record[key]), 32)
            self.assertEqual(record[key], sorted(record[key]))
            self.assertTrue(all(len(name) <= 256 for name in record[key]))

    def test_closed_inventory_diagnostic_caps_names_even_when_short(self):
        error = io.StringIO()
        with patch.object(M.sys, 'stderr', error):
            M.closed_inventory_diagnostic({'public-%03d' % index for index in range(100)},
                                          {'directory-%03d' % index for index in range(50)})
        record = json.loads(error.getvalue())
        self.assertEqual(record['unexpected_count'], 100)
        self.assertEqual(record['wrong_directory_type_count'], 50)
        self.assertEqual(record['unexpected_names'], ['public-%03d' % index for index in range(32)])
        self.assertEqual(record['wrong_directory_type_names'], ['directory-%03d' % index for index in range(32)])
        self.assertLessEqual(len(error.getvalue().encode()), 16 * 1024)

    def test_small_unexpected_blob_metadata_is_bounded_public_and_still_refused(self):
        _, entries = self.image(oci=True)
        private_marker = 'PUBLIC_FIXTURE_CONFIG_VALUE_MUST_NOT_BE_EMITTED'
        extras = []
        for index in range(33):
            value = {'id': '%064x' % index, 'parent': '%064x' % max(0, index-1),
                     'created': '1970-01-01T00:00:00Z', 'os': 'linux',
                     'container_config': {'Env': [private_marker]},
                     'unrequested_value': private_marker}
            raw = json.dumps(value, sort_keys=True).encode()
            extras.append(('blobs/sha256/'+sha256(raw).hexdigest(), raw, tarfile.REGTYPE))
        path = self.root/'unexpected-public-legacy-metadata.tar'
        path.write_bytes(self.tar(entries+extras)); path.chmod(0o600)
        error = io.StringIO()
        with patch.object(M.sys, 'stderr', error):
            with self.assertRaisesRegex(RuntimeError, '^reuse_tar_closed_inventory$'):
                M.validate_docker_archive(path)
        emitted = error.getvalue(); record = json.loads(emitted)
        self.assertLessEqual(len(emitted.encode()), 16*1024)
        self.assertNotIn(private_marker, emitted)
        self.assertNotIn('Env', emitted)
        self.assertEqual(record['unexpected_count'], 33)
        metadata = record['unexpected_blob_metadata']
        self.assertGreater(len(metadata), 0); self.assertLessEqual(len(metadata), 32)
        allowed = {'name', 'size', 'sha256_matches_name', 'keys', 'id', 'parent', 'created', 'os'}
        source = {name: data for name, data, _ in extras}
        for item in metadata:
            self.assertEqual(set(item), allowed)
            self.assertTrue(item['sha256_matches_name'])
            self.assertEqual(item['name'].removeprefix('blobs/sha256/'), sha256(source[item['name']]).hexdigest())
            self.assertEqual(item['size'], len(source[item['name']]))
            self.assertEqual(item['keys'], sorted(json.loads(source[item['name']])))
            self.assertRegex(item['id'], '^[a-f0-9]{64}$')
            self.assertEqual(item['created'], '1970-01-01T00:00:00Z')
        self.assertTrue(record['blob_metadata_omitted'])

    def test_all_thirty_one_realistic_legacy_metadata_rows_fit_the_hard_budget(self):
        _, entries = self.image(oci=True)
        extras = []
        for index in range(31):
            value = {'id': '%064x' % (index+1), 'created': '1970-01-01T00:00:00Z',
                     'container_config': {}, 'os': 'linux'}
            if index:
                value['parent'] = '%064x' % index
            if index == 30:
                value.update(created='2026-10-24T12:34:56.123456789Z', architecture='amd64',
                             config={'Env': ['PUBLIC_CONFIG_VALUE_NEVER_PRINTED']})
            raw = json.dumps(value, sort_keys=True).encode()
            extras.append(('blobs/sha256/'+sha256(raw).hexdigest(), raw, tarfile.REGTYPE))
        path = self.root/'thirty-one-public-metadata.tar'
        path.write_bytes(self.tar(entries+extras)); path.chmod(0o600)
        error = io.StringIO()
        with patch.object(M.sys, 'stderr', error):
            with self.assertRaisesRegex(RuntimeError, '^reuse_tar_closed_inventory$'):
                M.validate_docker_archive(path)
        record = json.loads(error.getvalue())
        self.assertEqual(len(record['unexpected_blob_metadata']), 31)
        self.assertEqual(len(record['unexpected_names']), 31)
        self.assertFalse(record['blob_metadata_omitted'])
        self.assertFalse(record['names_or_characters_omitted'])
        self.assertTrue(all(item['sha256_matches_name'] for item in record['unexpected_blob_metadata']))
        self.assertEqual([item['created'] for item in record['unexpected_blob_metadata']].count('2026-10-24T12:34:56.123456789Z'), 1)
        self.assertLessEqual(len(error.getvalue().encode()), 16*1024)
        self.assertNotIn('PUBLIC_CONFIG_VALUE_NEVER_PRINTED', error.getvalue())

    def test_blob_metadata_never_decodes_more_than_64k_or_more_than_32_members(self):
        oversized = b'x'*(64*1024+1)
        name = 'blobs/sha256/'+sha256(oversized).hexdigest()
        member = tarfile.TarInfo(name); member.size = len(oversized)
        bundle = MagicMock()
        bundle.extractfile.side_effect = AssertionError('oversized_metadata_read')
        record = M.unexpected_blob_metadata(bundle, {name: member}, {name})[0]
        self.assertIsNone(record['sha256']); self.assertIsNone(record['keys'])
        self.assertIsNone(record['sha256_matches_name'])
        self.assertEqual(record['size'], len(oversized))
        raw = b'{"created":"1970-01-01T00:00:00Z","id":"invalid-value","os":"private-value"}'
        names = ['blobs/sha256/%064x' % index for index in range(40)]
        members = {}
        for item in names:
            value = tarfile.TarInfo(item); value.size = len(raw); members[item] = value
        bundle.extractfile.side_effect = lambda member: io.BytesIO(raw)
        records = M.unexpected_blob_metadata(bundle, members, set(names))
        self.assertEqual(len(records), 32)
        self.assertTrue(all(item['id'] is None and item['os'] is None for item in records))
        self.assertTrue(all(item['sha256_matches_name'] is False and item['sha256'] == sha256(raw).hexdigest() for item in records))

    def test_adversarial_long_json_keys_cannot_expand_metadata_diagnostic_budget(self):
        _, entries = self.image(oci=True)
        extras = []
        for index in range(32):
            value = {'key-%02d-' % key + '\U0001f642'*128: 'value-never-emitted' for key in range(32)}
            value['id'] = '%064x' % index
            raw = json.dumps(value, ensure_ascii=False).encode()
            self.assertLessEqual(len(raw), 64*1024)
            extras.append(('blobs/sha256/'+sha256(raw).hexdigest(), raw, tarfile.REGTYPE))
        path = self.root/'adversarial-public-metadata.tar'
        path.write_bytes(self.tar(entries+extras)); path.chmod(0o600)
        error = io.StringIO()
        with patch.object(M.sys, 'stderr', error):
            with self.assertRaisesRegex(RuntimeError, '^reuse_tar_closed_inventory$'):
                M.validate_docker_archive(path)
        emitted = error.getvalue(); record = json.loads(emitted)
        self.assertLessEqual(len(emitted.encode()), 16*1024)
        self.assertTrue(record['blob_metadata_omitted'])
        self.assertEqual(record['unexpected_count'], 32)
        self.assertNotIn('value-never-emitted', emitted)

    def test_config_digest_and_layer_content_are_both_required(self):
        raw, entries = self.image()
        path = self.root / 'changed.tar'
        for index in (1, 2):
            changed = list(entries)
            name, data, kind = changed[index]; changed[index] = (name, data + b'x', kind)
            path.write_bytes(self.tar(changed)); path.chmod(0o600)
            with self.subTest(index=index), self.assertRaises(RuntimeError): M.validate_docker_archive(path)

    def test_zero_tail_that_grows_cannot_outlive_advertised_archive_size(self):
        class GrowingTail:
            def __init__(self):
                self.requests = []
            def seek(self, offset):
                self.offset = offset
            def read(self, maximum):
                self.requests.append(maximum)
                if len(self.requests) > 2:
                    raise AssertionError('unbounded_zero_tail_read')
                return b'\0' * maximum
        stream = GrowingTail()
        entry = tarfile.TarInfo('manifest.json')
        entry.offset_data = 512
        bundle = MagicMock()
        bundle.__enter__.return_value = bundle
        bundle.__iter__.return_value = iter([entry])
        with patch.object(M, 'pinned', return_value=nullcontext((stream, 1024))), \
                patch.object(M.tarfile, 'open', return_value=bundle):
            with self.assertRaisesRegex(RuntimeError, 'reuse_tar_stream_bound'):
                M.validate_docker_archive(self.root / 'synthetic.tar')
        self.assertEqual(stream.requests, [513])

    def test_platform_and_public_payload_labels_cannot_be_substituted(self):
        for changes in ({'architecture': 'arm64'}, {'config': {'Labels': {}}}):
            raw, _ = self.image(config_changes=changes)
            path = self.root / 'wrong.tar'; path.write_bytes(raw); path.chmod(0o600)
            with self.subTest(changes=changes), self.assertRaises(RuntimeError): M.validate_docker_archive(path)

    def test_oci_descriptor_hash_and_closed_blob_inventory_are_required(self):
        _, entries = self.image(oci=True)
        path = self.root / 'wrong-oci.tar'
        changed = list(entries)
        name, raw, kind = changed[3]; changed[3] = (name, raw + b' ', kind)
        path.write_bytes(self.tar(changed)); path.chmod(0o600)
        with self.assertRaisesRegex(RuntimeError, 'reuse_oci_blob_identity'): M.validate_docker_archive(path)
        path.write_bytes(self.tar(entries + [('blobs/sha256/' + 'f' * 64, b'unreferenced', tarfile.REGTYPE)]))
        with self.assertRaisesRegex(RuntimeError, 'reuse_tar_closed_inventory'): M.validate_docker_archive(path)

    def test_source_zip_aba_and_hardlinks_refuse(self):
        self.package()
        original, info = self.zip.read_bytes(), self.zip.stat()
        real = M.validate_docker_archive
        def changed(path):
            result = real(path)
            self.zip.write_bytes(original + b'x'); self.zip.write_bytes(original)
            os.utime(self.zip, ns=(info.st_atime_ns, info.st_mtime_ns))
            return result
        with patch.object(M, 'validate_docker_archive', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'reuse_input_changed'): self.admit()
        os.link(self.zip, self.root / 'linked.zip')
        with self.assertRaisesRegex(RuntimeError, 'reuse_input_metadata'):
            with M.pinned(self.zip, M.ZIP_BYTES): self.fail('hardlink accepted')

    def test_partial_or_existing_output_is_never_adopted(self):
        self.package(); self.output.mkdir(mode=0o700)
        with self.assertRaisesRegex(RuntimeError, 'reuse_output_exists'): self.admit()

    def test_duplicate_json_keys_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, 'reuse_duplicate_json_key'):
            M.document(b'{"image_id":"first","image_id":"second"}')

    def test_workflow_has_one_fixed_download_and_no_build_scan_deploy_or_mount(self):
        import yaml
        workflow = yaml.safe_load(WORKFLOW.read_text())
        self.assertEqual(workflow['permissions'], {'contents': 'read', 'actions': 'read'})
        job = workflow['jobs']['component-evidence']
        self.assertEqual(job['runs-on'], 'ubuntu-24.04')
        self.assertLessEqual(job['timeout-minutes'], 20)
        programs = blocks(); self.assertEqual(len(programs), 3)
        for code in programs: compile(code, '<workflow-public-source>', 'exec')
        download = ast.parse(programs[1])
        calls = [node for node in ast.walk(download) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'Popen']
        self.assertEqual(len(calls), 1)
        self.assertEqual(ast.literal_eval(calls[0].args[0]), ['gh', 'api', '--method', 'GET', 'repos/mohsenbarari/trading_bot/actions/artifacts/12552928309/zip'])
        source = WORKFLOW.read_text()
        self.assertNotIn('docker build', source); self.assertNotIn('trivy', source)
        self.assertNotIn('--mount', source); self.assertNotIn('--volume', source)
        self.assertNotIn('secrets.', source); self.assertNotIn('contents: write', source)
        probe = programs[2]
        self.assertLess(probe.index('helper.admit_image('), probe.index('load_archive(stream)'))
        self.assertLess(probe.index('load_archive(stream)'), probe.index('probe.in_container('))
        self.assertIn('stdin=stream', probe)
        self.assertIn('current', job['steps'][1]['name'])
        self.assertEqual(sha256((ROOT / 'scripts/verify_image_component_presence.py').read_bytes()).hexdigest(), M.PROBE_SHA256)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--actionlint']:
        if len(sys.argv) != 3: raise SystemExit('expected --actionlint /path/to/actionlint')
        print(json.dumps(actionlint(sys.argv[2]), sort_keys=True))
    else:
        unittest.main()
