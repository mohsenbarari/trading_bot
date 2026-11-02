"""CI-only security overlays for two exact public PostgreSQL/Redis baselines.

No production data, application payload, server upgrade or admission waiver.
The existing composition controller supplies only its bounded I/O primitives.
"""
from contextlib import contextmanager, ExitStack
from hashlib import sha1, sha256
import argparse
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
from types import ModuleType
import urllib.request
from urllib.parse import urlsplit

BASELINE_FACTS_SHA256 = '70889f7b6856039fa11a44eb3c208a52f9a1a13f1ea7b49b9360653e625d52f7'
REGISTRY_FACTS_SHA256 = '47484f3eafd4c0ad7453593ec72350eb9267445992bde88acf384a390b9fe661'
FOLLOWUP_FACTS_SHA256 = '846df3ae578ce7941d538fdc639409f5d523a4a7a7f66b73190748a53ac87d0e'
USER_MODULE_VERSION = 'v0.4.1'
USER_MODULE_SUMS = (
    b'github.com/moby/sys/user v0.4.1 h1:RgjRlaDKi/Xmyrz4t8lyzXT6v2ooFeO/7xtchmhVWE0=',
    b'github.com/moby/sys/user v0.4.1/go.mod h1:E9QsW5WRe1kUAf7kW8hXKwu1uhsZEAdPLYHYSDudF4Y=',
)
WINDOWS_SCOPE_MARKER = 'GOSU_WINDOWS_CODE_ABSENT linux amd64 package_absent symbols_absent'
GOSU_COMMIT = '6456aaa0f3c854d199d0f037f068eb97515b7513'
GO = ('go1.26.10.linux-amd64.tar.gz', 66936659,
      'ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5')
GOSU_FILES = {
    'go.mod': (110, '9f8577f60f12903668dc3aff4bb9030145d83cea'),
    'go.sum': (318, 'dea95ab128c5ce9ebb3deec4a7462cd75c2ae8e2'),
    'main.go': (2778, '7eb3d47a9baaa8c51167fff34fe79e81999ccc6b'),
    'setup-user.go': (1308, '87afcd1748da9efa4af5128452ac8fbec245a7ce'),
    'version.go': (37, 'a5c6e8a41c1506dff3f7232bc770b7335105551b'),
}
PROFILES = {'postgres': {'reference': 'postgres@sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b',
              'platform_manifest_digest': 'sha256:a2c20749c564b4eb73a77bfda626f8a3cde1bbfae020fb97c616a00cdc1a2181',
              'platform_manifest_bytes': 3054,
              'config_id': 'sha256:aad6289ca337b3ce76896f2e7e61480490152886c7828120371fb28e6b779e1d',
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
                         'sha256:363da9133d416185d21029bc178708bdf472186a612acab540f800233cd01cdb'],
              'volume': '/var/lib/postgresql/data',
              'server': '/usr/local/bin/postgres',
              'version': 'postgres (PostgreSQL) 15.19',
              'server_sha256': '1e08a0e796ae0461ec40031e9bd7a76831d453e723812b9c3e8757bc411a38c7',
              'entrypoint_sha256': '9c440299ae04a0a79d55b8bf03307036d890a40979d2fb698073c9050d4b20a5',
              'gosu_sha256': '52c8749d0142edd234e9d6bd5237dff2d81e71f43537e2f4f66f75dd4b243dd0',
              'branch': 'v3.24',
              'updates': {'libssl3': '3.5.9-r0',
                          'libcrypto3': '3.5.9-r0',
                          'libuuid': '2.42.3-r1',
                          'musl': '1.2.6-r3',
                          'musl-utils': '1.2.6-r3',
                          'zlib': '1.3.2-r1'},
              'musl_baseline_version': '1.2.6-r2',
              'musl_target_version': '1.2.6-r3'},
 'redis': {'reference': 'redis@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf',
           'platform_manifest_digest': 'sha256:1db42ccef14898aa29bae778452d567534b59c107129cbc1163fb552de184d3c',
           'platform_manifest_bytes': 2288,
           'config_id': 'sha256:5509c0097c6064aa8a3b1df58f1d950e67090fffa6678ae8f3f1dc2385f12deb',
           'layers': ['sha256:7211d01aba8fa2e9eb71ad34395cd313df5fc51a6b69b92d2dfad32cc37759cb',
                      'sha256:a0caa8bc04fc2a6bc37012dfcb190e6e1711e18bb1bfdf3768bafe736da6001b',
                      'sha256:2898dc82d6a9e325f70948c4da63e430a8a57b5b556024653d4291db83077b58',
                      'sha256:a6f8bf15c6b1bd407aaaeb094b75e619c8a91905c6f4d7f2f6a07cabaf542875',
                      'sha256:fd2bae83d0a7a6d58ed98af0be51b6468c5a989bf457c4bc289bf64a72acfda5',
                      'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef',
                      'sha256:96bf91f53b18ebaf5f197fb649a488a4fc428521cbe86e6e73fe7eefda68a15d'],
           'volume': '/data',
           'server': '/usr/local/bin/redis-server',
           'version': 'Redis server v=7.4.11 sha=00000000:0 malloc=jemalloc-5.3.0 bits=64 '
                      'build=fbf7a7d03c2e9219',
           'server_sha256': '67adb510c4c9db9585ad63404950694241c748ad6f34c3e1cc8fdc095268f1b6',
           'entrypoint_sha256': 'f8122ddc0eb2ae6ca7762725999f08b2ee7a6975da54e9061feee88f6d4ab2bb',
           'gosu_sha256': None,
           'branch': 'v3.21',
           'updates': {'libssl3': '3.3.7-r2',
                       'libcrypto3': '3.3.7-r2',
                       'musl': '1.2.5-r12',
                       'musl-utils': '1.2.5-r12',
                       'zlib': '1.3.2-r1'},
           'musl_baseline_version': '1.2.5-r11',
           'musl_target_version': '1.2.5-r12'}}
COMMON = None
PHASE = 'controller_admission'
LABEL = 'io.gold-trade.datastore-security-owner'
SHA = r'sha256:[a-f0-9]{64}'


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True)+'\n').encode()


def phase(name):
    global PHASE
    require(name in {'controller_admission', 'published_sources', 'pull_baseline', 'registry_identity',
                    'baseline_inventory', 'baseline_smoke', 'signed_inputs', 'builder', 'build_candidate',
                    'candidate_inventory', 'candidate_smoke', 'candidate_receipt', 'final_admission',
                    'raw_reports', 'export'}, 'phase')
    PHASE = name


@contextmanager
def sealed(path, maximum, *, mode=0o600):
    require(mode in {0o600, 0o644}, 'sealed_metadata')
    require(path.is_absolute() and path.resolve(strict=True) == path, 'sealed_path')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_uid,
                               s.st_nlink, s.st_mtime_ns, s.st_ctime_ns)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
            and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == mode
                and 0 < before.st_size <= maximum
                and identity(before) == identity(path.lstat()), 'sealed_metadata')
        with os.fdopen(os.dup(fd), 'rb') as source:
            raw = source.read(before.st_size+1)
        require(len(raw) == before.st_size, 'sealed_bound')
        yield raw
        require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()),
                'sealed_changed')
    finally:
        os.close(fd)


def ci(root):
    require(os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
            and os.environ.get('GITHUB_REPOSITORY') == 'mohsenbarari/trading_bot'
            and os.geteuid() == 1001, 'github_ci_only')
    require(root.is_absolute() and root.resolve(strict=True) == root
            and root.parent == Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
            and root.name.startswith('security-os-compose-')
            and stat.S_IMODE(root.stat().st_mode) == 0o700
            and root.stat().st_uid == 1001, 'ci_root')
    require(re.fullmatch('[0-9]+-[0-9]+', owner())
            and re.fullmatch('[a-f0-9]{40}', os.environ.get('GITHUB_SHA', '')), 'ci_identity')
    os.umask(0o077)


def owner():
    return os.environ.get('GITHUB_RUN_ID', '')+'-'+os.environ.get('GITHUB_RUN_ATTEMPT', '')


def load_source(root):
    global COMMON
    with sealed(root/'source/identity.json', 65536) as raw:
        identity = json.loads(raw)
    names = {'compose_alpine_datastore_security.py', 'compose_security_os_candidate.py'}
    require(identity.get('recipe_commit') == os.environ['GITHUB_SHA']
            and set(identity.get('files', {})) == names, 'source_inventory')
    # Compile exactly the retained, checked source buffer; never reopen after hashing.
    for name in sorted(names):
        with sealed(root/'source'/name, 131072) as raw:
            row = identity['files'][name]
            require(row == {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}, 'source_changed')
            if name == 'compose_security_os_candidate.py':
                module = ModuleType('sealed_datastore_common')
                exec(compile(raw, '<sealed-datastore-common>', 'exec'), module.__dict__)
                COMMON = module
            else:
                with sealed(Path(__file__).resolve(), 131072) as current:
                    require(raw == current, 'controller_changed')
    return identity


def docker(root, arguments, **options):
    return COMMON.docker(root, arguments, **options)


def download(url, path, maximum, *, size=None, digest=None, blob=None):
    """Bound reads before accumulation; only fixed public HTTPS inputs call this."""
    require(url.startswith(('https://dl.google.com/go/',
                            'https://raw.githubusercontent.com/tianon/gosu/'+GOSU_COMMIT+'/',
                            'https://dl-cdn.alpinelinux.org/alpine/')), 'download_origin')
    deadline = time.monotonic()+90
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    value = sha256(); git = sha1(b'blob '+str(size).encode()+b'\0') if blob else None
    total = 0
    with os.fdopen(fd, 'wb') as out, urllib.request.urlopen(url, timeout=20) as source:
        final = urlsplit(source.geturl())
        require(final.scheme == 'https' and final.hostname in {'dl.google.com', 'raw.githubusercontent.com',
                'dl-cdn.alpinelinux.org'} and final.username is None and final.password is None, 'download_redirect')
        while True:
            require(time.monotonic() < deadline, 'download_timeout')
            block = source.read(min(65536, maximum+1-total))
            if not block:
                break
            total += len(block); require(total <= maximum, 'download_bound')
            out.write(block); value.update(block)
            if git:
                git.update(block)
        out.flush(); os.fsync(out.fileno())
    require(total > 0 and (size is None or total == size)
            and (digest is None or value.hexdigest() == digest)
            and (blob is None or git.hexdigest() == blob), 'download_identity')
    return {'bytes': total, 'sha256': value.hexdigest(), 'url': url}


def registry_identity(root, profile):
    pin = PROFILES[profile]; reference = pin['reference']; top = reference.split('@', 1)[1]
    def fetch(ref, digest, size=None):
        raw = docker(root, ['buildx', 'imagetools', 'inspect', '--raw', ref], seconds=60, maximum=1024**2)
        require((size is None or len(raw) == size) and sha256(raw).hexdigest() == digest[7:], 'registry_digest')
        value = json.loads(raw); require(value.get('schemaVersion') == 2, 'registry_schema')
        return value
    value = fetch(reference, top); selected = top
    if value.get('mediaType') in {'application/vnd.oci.image.index.v1+json',
                                  'application/vnd.docker.distribution.manifest.list.v2+json'}:
        rows = value.get('manifests')
        require(type(rows) is list and 1 <= len(rows) <= 32, 'registry_index')
        matches = [row for row in rows if row.get('platform', {}).get('os') == 'linux'
                   and row.get('platform', {}).get('architecture') == 'amd64'
                   and row.get('platform', {}).get('variant', '') in {'', 'v1'}]
        require(len(matches) == 1 and re.fullmatch(SHA, matches[0].get('digest', '')), 'registry_platform')
        selected = matches[0]['digest']
        require(selected == pin['platform_manifest_digest']
                and type(matches[0].get('size')) is int and matches[0]['size'] == pin['platform_manifest_bytes'], 'registry_platform_pin')
        value = fetch(reference.split('@')[0]+'@'+selected, selected, pin['platform_manifest_bytes'])
    require(selected == pin['platform_manifest_digest'], 'registry_platform_pin')
    require(value.get('mediaType') in {'application/vnd.oci.image.manifest.v1+json',
                                     'application/vnd.docker.distribution.manifest.v2+json'}
            and value.get('config', {}).get('digest') == pin['config_id']
            and value['config'].get('mediaType') in {'application/vnd.oci.image.config.v1+json',
                'application/vnd.docker.container.image.v1+json'}
            and type(value['config'].get('size')) is int and 0 < value['config']['size'] <= 65536, 'registry_config')
    layers = value.get('layers')
    require(type(layers) is list and len(layers) == len(pin['layers'])
            and all(type(row.get('size')) is int and 0 < row['size'] <= 256*1024**2
                    and re.fullmatch(SHA, row.get('digest', ''))
                    and row.get('mediaType') in {'application/vnd.oci.image.layer.v1.tar',
                        'application/vnd.oci.image.layer.v1.tar+gzip',
                        'application/vnd.docker.image.rootfs.diff.tar.gzip'} for row in layers), 'registry_layers')
    return {'reference': reference, 'top_digest': top, 'platform_manifest_digest': selected,
            'config_digest': pin['config_id'], 'layer_descriptors': layers}


def baseline(profile, record, registry):
    require(profile in PROFILES and type(record) is dict, 'profile')
    pin = PROFILES[profile]
    require(registry.get('reference') == pin['reference'] and registry.get('config_digest') == pin['config_id']
            and registry.get('top_digest') == pin['reference'].split('@')[1]
            and registry.get('platform_manifest_digest') == pin['platform_manifest_digest']
            and record.get('Id') in {pin['config_id'], registry['top_digest'], registry['platform_manifest_digest']}
            and record.get('Os') == 'linux'
            and record.get('Architecture') == 'amd64'
            and record.get('RootFS') == {'Type': 'layers', 'Layers': pin['layers']}
            and type(record.get('Config')) is dict
            and record['Config'].get('Volumes') == {pin['volume']: {}}
            and record['Config'].get('User', '') in ('', 'root', '0')
            and not record['Config'].get('OnBuild'), 'baseline_identity')
    # A registry index, platform manifest and config digest remain distinct.
    require(pin['reference'] in record.get('RepoDigests', []), 'baseline_repository')
    return pin


def parse_audit(raw):
    require(type(raw) is bytes and 0 < len(raw) <= 4*1024**2, 'audit_bound')
    rows = raw.decode('utf-8').splitlines(); result = {}; packages = {}; files = {}; nodes = {}; links = {}; keys = {}
    for line in rows:
        fields = line.split('\t')
        require(len(fields) == 3 and fields[0] in {'meta', 'pkg', 'file', 'node', 'link', 'key'}, 'audit_shape')
        kind, key, value = fields
        target = {'meta': result, 'pkg': packages, 'file': files, 'node': nodes, 'link': links, 'key': keys}[kind]
        require(key not in target and value, 'audit_duplicate')
        if kind == 'file':
            require(key.startswith('/usr/local/') and re.fullmatch('[a-f0-9]{64}', value), 'audit_file')
        if kind == 'key':
            require(key.startswith('/etc/apk/keys/') and re.fullmatch('[a-f0-9]{64}', value), 'audit_file')
        target[key] = value
    require(set(result) == {'alpine', 'world', 'musl', 'server', 'gosu', 'repositories'}
            and re.fullmatch('[a-f0-9]{64}', result['world'])
            and re.fullmatch('[a-f0-9]{64}', result['musl'])
            and re.fullmatch('[a-f0-9]{64}', result['repositories']) and 1 <= len(keys) <= 32
            and packages and len(packages) <= 512 and 1 <= len(files) <= 4096, 'audit_inventory')
    require(1 <= len(nodes) <= 8192 and len(links) <= 4096
            and set(files) <= set(nodes) and set(links) <= set(nodes), 'audit_nodes')
    return {'metadata': result, 'packages': packages, 'files': files, 'nodes': nodes, 'links': links, 'keys': keys}


def validate_audit(profile, value, *, candidate=False):
    pin = PROFILES[profile]; meta = value['metadata']; packages = value['packages']; files = value['files']
    require(re.fullmatch(re.escape(pin['branch'][1:])+r'\.[0-9]+', meta['alpine'])
            and meta['server'] == pin['version']
            and files.get(pin['server']) == pin['server_sha256']
            and files.get('/usr/local/bin/docker-entrypoint.sh') == pin['entrypoint_sha256'], 'software_baseline')
    if profile == 'postgres':
        expected = '1.19 (go1.26.10 on linux/amd64; gc)' if candidate else '1.19 (go1.24.6 on linux/amd64; gc)'
        require(meta['gosu'] == expected and '/usr/local/bin/gosu' in files, 'gosu_version')
        if not candidate:
            require(files['/usr/local/bin/gosu'] == pin['gosu_sha256'], 'gosu_baseline')
    else:
        require(meta['gosu'] == 'ABSENT' and '/usr/local/bin/gosu' not in files, 'unexpected_gosu')
    require(set(pin['updates']) <= set(packages) and 'musl' in packages, 'package_baseline')
    version = pin['musl_target_version'] if candidate else pin['musl_baseline_version']
    require(packages['musl'] == packages['musl-utils'] == version, 'libc_revision')


def compare_audits(profile, before, after):
    validate_audit(profile, before); validate_audit(profile, after, candidate=True)
    left, right = before['packages'], after['packages']; updates = PROFILES[profile]['updates']
    require(set(left) == set(right) and {key for key in left if left[key] != right[key]} == set(updates)
            and all(right[key] == version for key, version in updates.items()), 'package_delta')
    require(all(before['metadata'][key] == after['metadata'][key] for key in ('alpine', 'world', 'server', 'repositories'))
            and before['keys'] == after['keys'], 'protected_runtime_changed')
    require(left['musl'].split('-r')[0] == right['musl'].split('-r')[0]
            and before['metadata']['musl'] != after['metadata']['musl'], 'libc_security_revision')
    a, b = dict(before['files']), dict(after['files'])
    if profile == 'postgres':
        require(a.pop('/usr/local/bin/gosu') != b.pop('/usr/local/bin/gosu'), 'gosu_not_rebuilt')
    require(a == b and before['nodes'] == after['nodes'] and before['links'] == after['links'], 'server_or_support_changed')


def libc_patch_receipt(profile, before, after, inputs):
    compare_audits(profile, before, after)
    pin = PROFILES[profile]; names = ('musl', 'musl-utils')
    artifacts = {name+'-'+pin['updates'][name]+'.apk': inputs[name+'-'+pin['updates'][name]+'.apk'] for name in names}
    artifacts['APKINDEX.tar.gz'] = inputs['APKINDEX.tar.gz']
    for name, row in artifacts.items():
        require(type(row.get('bytes')) is int and 0 < row['bytes'] <= 16*1024**2
                and type(row.get('sha256')) is str and re.fullmatch('[a-f0-9]{64}', row['sha256'])
                and row.get('url') == 'https://dl-cdn.alpinelinux.org/alpine/'+pin['branch']+'/main/x86_64/'+name,
                'libc_vendor_input')
    return {'schema': 'alpine_datastore_libc_security_revision/1', 'profile': profile,
        'branch': pin['branch'], 'cve': 'CVE-2026-107891',
        'baseline_version': pin['musl_baseline_version'], 'target_version': pin['musl_target_version'],
        'baseline_libc_sha256': before['metadata']['musl'], 'candidate_libc_sha256': after['metadata']['musl'],
        'signed_vendor_inputs': artifacts, 'same_upstream_version': True,
        'full_exported_symbol_abi_matrix': 'NOT_TESTED', 'security_accepted': False}


def gosu_dependency_delta(context, records):
    """The only approved upstream-source edits are the two explicit security lock files."""
    original = {name: COMMON.read(context/'gosu'/name, 4096) for name in ('go.mod', 'go.sum')}
    for name, raw in original.items():
        size, blob = GOSU_FILES[name]
        require(len(raw) == size and sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest() == blob, 'gosu_original_lock')
    old = b'github.com/moby/sys/user v0.1.0'; new = b'github.com/moby/sys/user v0.4.1'
    require(original['go.mod'].count(old) == 1 and new not in original['go.mod'], 'gosu_original_lock')
    effective = {'go.mod': original['go.mod'].replace(old, new, 1),
        'go.sum': b'\n'.join(sorted(original['go.sum'].splitlines()+list(USER_MODULE_SUMS)))+b'\n'}
    require(len(original['go.sum'].splitlines()) == 4
            and len(set(effective['go.sum'].splitlines())) == 6, 'gosu_original_lock')
    result = {'schema': 'gosu_locked_dependency_security_delta/1', 'base_source_commit': GOSU_COMMIT,
        'gosu_cli_version': '1.19', 'changed_files': {},
        'moby_sys_user': {'baseline_version': 'v0.1.0', 'effective_version': USER_MODULE_VERSION,
            'cve': 'CVE-2026-61801', 'sumdb_lines': [line.decode() for line in USER_MODULE_SUMS]},
        'x_sys_version': 'v0.1.0', 'three_go_code_files_unchanged': True, 'security_accepted': False}
    for name, raw in effective.items():
        path = context/'gosu'/name
        # Original reads are already stable/hash-pinned; replace only this fresh owned build input.
        temporary = path.with_name(name+'.security-new'); COMMON.write(temporary, raw)
        os.replace(temporary, path)
        key = 'gosu/'+name; previous = dict(records[key])
        records[key] = {'url': previous['url'], 'bytes': len(raw), 'sha256': sha256(raw).hexdigest(),
            'original_download': previous, 'transformation': 'moby_user_0_4_1_security_lock_only'}
        result['changed_files'][name] = {'original_sha256': sha256(original[name]).hexdigest(),
            'effective_sha256': sha256(raw).hexdigest()}
    manifest = ''.join(row['sha256']+'  '+name.removeprefix('gosu/')+'\n'
        for name, row in sorted(records.items()) if name.startswith('gosu/')).encode()
    COMMON.write(context/'gosu/effective.sha256', manifest)
    records['gosu/effective.sha256'] = {'bytes': len(manifest), 'sha256': sha256(manifest).hexdigest(),
        'origin': 'controlled_effective_source_manifest'}
    return result


def windows_scope_proof(log):
    require(type(log) is bytes and 0 < len(log) <= 32*1024**2, 'gosu_scope_log')
    lines = [re.sub(r'^#[0-9]+\s+(?:[0-9]+(?:\.[0-9]+)?\s+)?', '', line)
             for line in log.decode('utf-8').splitlines()]
    require(lines.count(WINDOWS_SCOPE_MARKER) == 1, 'gosu_windows_scope')
    return {'schema': 'gosu_windows_advisory_target_scope/1', 'cve': 'CVE-2026-39824',
        'goos': 'linux', 'goarch': 'amd64', 'x_sys_version': 'v0.1.0',
        'affected_windows_package_in_go_list_deps': False, 'affected_windows_symbols_in_go_nm': False,
        'module_version_fixed': False, 'raw_finding_modified': False, 'security_accepted': False}


AUDIT = r'''
set -eu
printf 'meta\talpine\t%s\n' "$(cat /etc/alpine-release)"
printf 'meta\tworld\t%s\n' "$(sha256sum /etc/apk/world | cut -d' ' -f1)"
printf 'meta\tmusl\t%s\n' "$(sha256sum /lib/ld-musl-x86_64.so.1 | cut -d' ' -f1)"
printf 'meta\trepositories\t%s\n' "$(sha256sum /etc/apk/repositories | cut -d' ' -f1)"
printf 'meta\tserver\t%s\n' "$("$1" --version)"
if test -e /usr/local/bin/gosu; then
  printf 'meta\tgosu\t%s\n' "$(/usr/local/bin/gosu --version)"
else printf 'meta\tgosu\tABSENT\n'; fi
awk 'BEGIN{RS="";FS="\n"} {p="";v="";for(i=1;i<=NF;i++){if($i~/^P:/)p=substr($i,3);if($i~/^V:/)v=substr($i,3)} if(p!=""&&v!="")printf "pkg\t%s\t%s\n",p,v;else exit 1}' /lib/apk/db/installed
find /etc/apk/keys -type f -exec sha256sum '{}' ';' | LC_ALL=C sort | while read -r digest path; do
  printf 'key\t%s\t%s\n' "$path" "$digest"
done
find /usr/local -xdev -type f -exec sha256sum '{}' ';' | LC_ALL=C sort | while read -r digest path; do
  printf 'file\t%s\t%s\n' "$path" "$digest"
done
find /usr/local -xdev -exec sh -eu -c 'for path do
  printf "node\t%s\t%s\n" "$path" "$(stat -c "%a:%u:%g:%F" "$path")"
  if test -L "$path"; then printf "link\t%s\t%s\n" "$path" "$(readlink "$path")"; fi
done' -- '{}' '+'
'''


def cleanup_container(root, target, name, image):
    values = json.loads(docker(root, ['inspect', target], maximum=2*1024**2))
    require(type(values) is list and len(values) == 1, 'cleanup_unknown')
    value = values[0]; cid = value.get('Id')
    require(type(cid) is str and re.fullmatch('[a-f0-9]{64}', cid)
            and value.get('Name') == '/'+name and value.get('Image') == image
            and value.get('Config', {}).get('Labels', {}).get(LABEL) == owner()
            and target in {name, cid}, 'cleanup_owner')
    docker(root, ['rm', '--force', cid], seconds=20)


def isolated(root, profile, image, script, purpose, *, user='0:0', caps=(), mounts=(), maximum=4*1024**2):
    pin = PROFILES[profile]; name = 'security-datastore-'+profile+'-'+purpose+'-'+owner(); cid = None
    uid, gid = user.split(':')
    argv = ['create', '--name', name, '--label', LABEL+'='+owner(), '--network=none', '--read-only',
            '--cap-drop=ALL', '--security-opt=no-new-privileges', '--memory=1g', '--memory-swap=1g',
            '--cpus=2', '--pids-limit=128', '--ulimit', 'core=0', '--user', user,
            '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=128m,uid='+uid+',gid='+gid,
            '--tmpfs', pin['volume']+':rw,nosuid,nodev,noexec,size=768m,uid='+uid+',gid='+gid,
            '--entrypoint', '/bin/sh']
    for cap in caps:
        require(cap in {'SETUID', 'SETGID'}, 'fixture_capability'); argv += ['--cap-add', cap]
    for source, target in mounts:
        argv += ['--mount', 'type=bind,source='+str(source)+',target='+target+',readonly']
    try:
        output = docker(root, [*argv, image, '-eu', '-c', script, '--', pin['server']], maximum=256)
        observed = output.decode().strip()
        require(re.fullmatch('[a-f0-9]{64}', observed), 'container_identity'); cid = observed
        output = docker(root, ['start', '--attach', cid], seconds=180, maximum=maximum,
                        combine_stderr=True, log_path=root/'reports'/(purpose+'.log'))
        require(docker(root, ['inspect', '--format', '{{.State.ExitCode}}', cid], maximum=64).strip() == b'0', 'fixture_failed')
        return output
    finally:
        if cid is None:
            status = 'not_confirmed'
            try:
                cleanup_container(root, name, name, image); status = 'removed_owned_container'
            except Exception:
                pass
            COMMON.write(root/'reports'/(purpose+'-daemon-outcome.json'), encoded({
                'daemon_outcome': 'UNKNOWN', 'best_effort_cleanup': status,
                'name': name, 'image_id': image, 'cancellation_proven': False, 'security_accepted': False}))
        else:
            cleanup_container(root, cid, name, image)


POSTGRES_SMOKE = r'''
set -eu
cp /proof/cert.pem /tmp/cert.pem; cp /proof/key.pem /tmp/key.pem; chmod 600 /tmp/key.pem
initdb -D /var/lib/postgresql/data --no-locale -A trust -U postgres >/tmp/init.log
options="-h 127.0.0.1 -k /tmp -c ssl=on -c ssl_cert_file=/tmp/cert.pem -c ssl_key_file=/tmp/key.pem"
pg_ctl -D /var/lib/postgresql/data -l /tmp/server.log -o "$options" -w start
trap 'pg_ctl -D /var/lib/postgresql/data -m immediate -w stop >/dev/null 2>&1 || true' EXIT
psql 'host=127.0.0.1 user=postgres sslmode=require' -v ON_ERROR_STOP=1 -c "CREATE TABLE ordinary_fixture(id integer PRIMARY KEY, value text); INSERT INTO ordinary_fixture VALUES (1,'ordinary'),(2,'restore');" >/tmp/query.log
pg_dump -h /tmp -U postgres -Fc postgres >/tmp/fixture.dump
createdb -h /tmp -U postgres restored
pg_restore -h /tmp -U postgres --exit-on-error -d restored /tmp/fixture.dump
test "$(psql -h /tmp -U postgres -d restored -Atc 'SELECT string_agg(value,chr(44) ORDER BY id) FROM ordinary_fixture')" = ordinary,restore
pg_ctl -D /var/lib/postgresql/data -m fast -w stop
pg_ctl -D /var/lib/postgresql/data -l /tmp/server.log -o "$options" -w start
test "$(psql -h /tmp -U postgres -d restored -Atc 'SELECT count(*) FROM ordinary_fixture')" = 2
printf 'DATASTORE_FIXTURE_PASS\n'
'''

REDIS_SMOKE = r'''
set -eu
cp /proof/cert.pem /tmp/cert.pem; cp /proof/key.pem /tmp/key.pem; chmod 600 /tmp/key.pem
start() {
  redis-server --port 0 --tls-port 16379 --tls-cert-file /tmp/cert.pem --tls-key-file /tmp/key.pem --tls-ca-cert-file /tmp/cert.pem --tls-auth-clients no --bind 127.0.0.1 --unixsocket /tmp/redis.sock --dir /data --appendonly "$1" --appendfsync always --daemonize no --logfile /tmp/server.log &
  server=$!
  for attempt in $(seq 1 100); do
    if test "$(cli ping 2>/dev/null)" = PONG; then return 0; fi
    sleep 0.05
  done
  return 1
}
cli() { redis-cli -s /tmp/redis.sock "$@"; }
stop() { cli shutdown nosave; wait "$server"; }
start yes
trap 'cli shutdown nosave >/dev/null 2>&1 || true; wait "$server" || true' EXIT
test "$(cli ping)" = PONG
test "$(cli set ordinary_fixture ordinary)" = OK
test "$(cli get ordinary_fixture)" = ordinary
test "$(redis-cli --tls --cacert /tmp/cert.pem -h 127.0.0.1 -p 16379 ping)" = PONG
test "$(cli save)" = OK
stop
start yes
test "$(cli get ordinary_fixture)" = ordinary
stop
start no
test "$(cli get ordinary_fixture)" = ordinary
printf 'DATASTORE_FIXTURE_PASS\n'
'''

GOSU_SMOKE = r'''
set -eu
test "$(gosu postgres id -u)" = 70
test "$(gosu postgres id -g)" = 70
test "$(gosu postgres id -G)" = 70
test "$(gosu 70:70 id -u)" = 70
exec gosu 70:70 sh -eu -c 'test "$1" = "$$"; printf "GOSU_FIXTURE_PASS\n"' -- "$$"
'''


def dockerfile(profile):
    pin = PROFILES[profile]; base = pin['reference']
    stages = ''
    if profile == 'postgres':
        stages = ('FROM '+base+' AS gosu_acquire\n'
            'RUN --mount=type=bind,source=gosu,target=/source,ro '
            '--mount=type=bind,source='+GO[0]+',target=/sdk.tar.gz,ro '
            'mkdir /build && cp /source/* /build/ && tar -xzf /sdk.tar.gz -C /usr/local && '
            'test "$(/usr/local/go/bin/go version)" = "go version go1.26.10 linux/amd64" && '
            'cd /build && sha256sum -c effective.sha256 && CGO_ENABLED=0 GOTOOLCHAIN=local '
            'GOENV=off GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org '
            '/usr/local/go/bin/go mod download && sha256sum -c effective.sha256\n'
            'FROM gosu_acquire AS gosu_build\n'
            'RUN --network=none cd /build && CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off '
            '/usr/local/go/bin/go build -mod=readonly -trimpath -buildvcs=false -ldflags="-d -w" -o /gosu . && '
            'test "$(/gosu --version)" = "1.19 (go1.26.10 on linux/amd64; gc)" && '
            'sha256sum -c effective.sha256 && /usr/local/go/bin/go version -m /gosu && '
            'test "$(/usr/local/go/bin/go env GOOS)" = linux && '
            'test "$(/usr/local/go/bin/go env GOARCH)" = amd64 && '
            'test "$(GOPROXY=off GOSUMDB=off /usr/local/go/bin/go list -mod=readonly -m -f "{{.Version}}" github.com/moby/sys/user)" = v0.4.1 && '
            'test "$(GOPROXY=off GOSUMDB=off /usr/local/go/bin/go list -mod=readonly -m -f "{{.Version}}" golang.org/x/sys)" = v0.1.0 && '
            'CGO_ENABLED=0 GOTOOLCHAIN=local GOPROXY=off GOSUMDB=off /usr/local/go/bin/go list -mod=readonly -deps . > /build/deps.txt && '
            'grep -Fx golang.org/x/sys/unix /build/deps.txt && '
            '! grep -Fx golang.org/x/sys/windows /build/deps.txt && '
            '/usr/local/go/bin/go tool nm /gosu > /build/nm.txt && '
            '! grep -F golang.org/x/sys/windows /build/nm.txt && '
            'printf "'+WINDOWS_SCOPE_MARKER+'\\n"\n')
    command = ('/sbin/apk --no-network --no-cache --no-progress --repositories-file /capsule/repositories '
               'fix --upgrade '+ ' '.join(pin['updates']))
    # Preserve every inherited OCI Config field: no ENV, USER, CMD, LABEL or VOLUME instructions.
    final = ('FROM '+base+'\nRUN --network=none '
        '--mount=type=bind,source=repo,target=/capsule/repo,ro '
        '--mount=type=bind,source=repositories,target=/capsule/repositories,ro ')
    if profile == 'postgres':
        final += '--mount=type=bind,from=gosu_build,source=/gosu,target=/capsule/gosu,ro '
    final += json.dumps(['/bin/sh', '-eu', '-c', command+
        (' && cp /capsule/gosu /usr/local/bin/gosu && chmod 0755 /usr/local/bin/gosu' if profile == 'postgres' else '')])+'\n'
    return stages+final


def acquire(root, profile, image):
    pin = PROFILES[profile]; context = root/'context'; repo = context/'repo/x86_64'
    repo.mkdir(parents=True, mode=0o700)
    url = 'https://dl-cdn.alpinelinux.org/alpine/'+pin['branch']+'/main/x86_64/'
    records = {'APKINDEX.tar.gz': download(url+'APKINDEX.tar.gz', repo/'APKINDEX.tar.gz', 8*1024**2)}
    for name, version in pin['updates'].items():
        filename = name+'-'+version+'.apk'
        records[filename] = download(url+filename, repo/filename, 16*1024**2)
    COMMON.write(context/'repositories', b'/capsule/repo\n')
    publish_public_repository(context, profile, records)
    # Authenticity comes from apk's original baseline trust store and unmodified vendor signatures.
    script = '/sbin/apk verify '+ ' '.join('/capsule/repo/x86_64/'+name for name in records if name.endswith('.apk'))
    script += ' && /sbin/apk --no-network --no-cache --repositories-file /capsule/repositories --simulate fix --upgrade '+ ' '.join(pin['updates'])
    isolated(root, profile, image, script, 'signed-apk',
             mounts=((context/'repo', '/capsule/repo'), (context/'repositories', '/capsule/repositories')))
    if profile == 'postgres':
        records[GO[0]] = download('https://dl.google.com/go/'+GO[0], context/GO[0], GO[1], size=GO[1], digest=GO[2])
        (context/'gosu').mkdir(mode=0o700)
        for name, (size, blob) in GOSU_FILES.items():
            records['gosu/'+name] = download('https://raw.githubusercontent.com/tianon/gosu/'+GOSU_COMMIT+'/'+name,
                context/'gosu'/name, size, size=size, blob=blob)
        overlay = gosu_dependency_delta(context, records)
        COMMON.write(root/'reports/gosu-dependency-overlay.json', encoded(overlay))
    return records


def publish_public_repository(context, profile, records):
    """Only signed PUBLIC package inputs cross the CI-UID→cap0 fixture boundary."""
    names = {'APKINDEX.tar.gz'} | {name+'-'+version+'.apk' for name, version in PROFILES[profile]['updates'].items()}
    repo = context/'repo'; architecture = repo/'x86_64'
    require(set(records) == names and {p.name for p in repo.iterdir()} == {'x86_64'}
            and {p.name for p in architecture.iterdir()} == names, 'public_repository_inventory')
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_uid,
                          s.st_nlink, s.st_mtime_ns, s.st_ctime_ns)
    with ExitStack() as stack:
        directories = []
        for path in (repo, architecture):
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, fd); before = os.fstat(fd)
            require(stat.S_ISDIR(before.st_mode) and before.st_uid == os.geteuid()
                    and stat.S_IMODE(before.st_mode) == 0o700
                    and identity(before) == identity(path.lstat()), 'public_repository_metadata')
            directories.append((path, fd, before))
        files = [(architecture/name, row['bytes'], row['sha256']) for name, row in sorted(records.items())]
        files.append((context/'repositories', len(b'/capsule/repo\n'), sha256(b'/capsule/repo\n').hexdigest()))
        for path, size, expected in files:
            require(all(identity(before) == identity(os.fstat(fd)) == identity(directory.lstat())
                        for directory, fd, before in directories), 'public_repository_changed')
            require(type(size) is int and 0 < size <= 16*1024**2, 'public_repository_metadata')
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                        and before.st_nlink == 1 and before.st_size == size
                        and stat.S_IMODE(before.st_mode) == 0o600
                        and identity(before) == identity(path.lstat()), 'public_repository_metadata')
                with os.fdopen(os.dup(fd), 'rb') as source:
                    require(COMMON.stream_digest(source, size) == expected, 'public_repository_bytes')
                require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'public_repository_changed')
                require(all(identity(state) == identity(os.fstat(handle)) == identity(directory.lstat())
                            for directory, handle, state in directories), 'public_repository_changed')
                os.fchmod(fd, 0o644); os.fsync(fd)
                after = os.fstat(fd)
                require((after.st_dev, after.st_ino, after.st_size, after.st_uid, after.st_nlink, after.st_mtime_ns)
                        == (before.st_dev, before.st_ino, before.st_size, before.st_uid, before.st_nlink, before.st_mtime_ns)
                        and stat.S_IMODE(after.st_mode) == 0o644
                        and identity(after) == identity(path.lstat()), 'public_repository_changed')
            finally:
                os.close(fd)
        for path, fd, before in reversed(directories):
            require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'public_repository_changed')
            os.fchmod(fd, 0o755); os.fsync(fd); after = os.fstat(fd)
            require((after.st_dev, after.st_ino, after.st_uid) == (before.st_dev, before.st_ino, before.st_uid)
                    and stat.S_IMODE(after.st_mode) == 0o755 and identity(after) == identity(path.lstat()), 'public_repository_changed')


@contextmanager
def retained_inputs(root, records):
    with ExitStack() as stack:
        for name, row in records.items():
            path = root/'context'/(('repo/x86_64/'+name) if name.endswith(('.apk', '.tar.gz'))
                                   and name != GO[0] else name)
            if name == 'APKINDEX.tar.gz' or name.endswith('.apk'):
                raw = stack.enter_context(sealed(path, 16*1024**2, mode=0o644))
                require(len(raw) == row['bytes'] and sha256(raw).hexdigest() == row['sha256'], 'build_input_changed')
            else:
                source, size = stack.enter_context(COMMON.pinned(path, 80*1024**2))
                require(size == row['bytes'] and COMMON.stream_digest(source, size) == row['sha256'], 'build_input_changed')
        stack.enter_context(sealed(root/'context/repositories', 65536, mode=0o644))
        stack.enter_context(COMMON.pinned(root/'context/Dockerfile', 65536))
        yield


def smoke(root, profile, image, purpose):
    script = POSTGRES_SMOKE if profile == 'postgres' else REDIS_SMOKE
    user = '70:70' if profile == 'postgres' else '999:1000'
    raw = isolated(root, profile, image, script, purpose, user=user, mounts=((root/'fixture', '/proof'),))
    require(raw.splitlines()[-1:] == [b'DATASTORE_FIXTURE_PASS'], 'fixture_marker')
    if profile == 'postgres':
        raw = isolated(root, profile, image, GOSU_SMOKE, purpose+'-gosu', caps=('SETUID', 'SETGID'))
        require(raw.splitlines()[-1:] == [b'GOSU_FIXTURE_PASS'], 'gosu_fixture_marker')


def compose(root, profile):
    phase('controller_admission'); ci(root); phase('published_sources')
    identity = load_source(root); require(profile in PROFILES, 'profile'); pin = PROFILES[profile]
    for name in ('reports', 'context', 'docker-config', 'fixture'):
        (root/name).mkdir(mode=0o700, exist_ok=(name == 'reports'))
    space = os.statvfs(root); require(space.f_bavail*space.f_frsize >= 8*1024**3, 'runner_space')
    phase('pull_baseline'); docker(root, ['pull', '--platform=linux/amd64', pin['reference']], seconds=180,
        maximum=4*1024**2, combine_stderr=True, log_path=root/'reports/baseline-pull.log')
    phase('registry_identity'); registry = registry_identity(root, profile)
    before = COMMON.image_record(docker(root, ['image', 'inspect', pin['reference']], maximum=2*1024**2))
    baseline(profile, before, registry); parent_image = before['Id']
    COMMON.write(root/'reports/baseline-image.json', encoded(before))
    phase('baseline_inventory'); initial = parse_audit(isolated(root, profile, parent_image, AUDIT, 'before'))
    validate_audit(profile, initial)
    COMMON.command(['/usr/bin/openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-subj', '/CN=localhost', '-keyout', str(root/'fixture/key.pem'), '-out', str(root/'fixture/cert.pem')],
                   seconds=20, maximum=65536)
    # Public disposable fixture key only, copied into the user's private tmpfs before use.
    for path in (root/'fixture').iterdir():
        path.chmod(0o644)
    (root/'fixture').chmod(0o755)
    phase('baseline_smoke'); smoke(root, profile, parent_image, 'baseline-smoke')
    phase('signed_inputs'); inputs = acquire(root, profile, parent_image)
    phase('builder'); COMMON.inspect_builder(root)
    COMMON.write(root/'context/Dockerfile', dockerfile(profile).encode())
    phase('build_candidate')
    with retained_inputs(root, inputs):
        docker(root, ['build', '--builder', 'default', '--progress=plain', '--platform=linux/amd64', '--pull=false', '--no-cache',
                      '--iidfile', str(root/'candidate.id'), str(root/'context')], seconds=900, maximum=32*1024**2,
               combine_stderr=True, log_path=root/'reports/build.log')
    image = COMMON.read(root/'candidate.id', 256).decode().strip(); require(re.fullmatch(SHA, image), 'candidate_identity')
    after = COMMON.image_record(docker(root, ['image', 'inspect', image], maximum=2*1024**2), image)
    COMMON.compare_images(before, after)
    phase('candidate_inventory'); installed = parse_audit(isolated(root, profile, image, AUDIT, 'after')); compare_audits(profile, initial, installed)
    libc = libc_patch_receipt(profile, initial, installed, inputs)
    COMMON.write(root/'reports/libc-security-revision.json', encoded(libc))
    scope = None
    if profile == 'postgres':
        scope = windows_scope_proof(COMMON.read(root/'reports/build.log', 32*1024**2))
        scope['image_id'] = image; scope['gosu_sha256'] = installed['files']['/usr/local/bin/gosu']
        COMMON.write(root/'reports/gosu-windows-scope.json', encoded(scope))
    phase('candidate_smoke'); smoke(root, profile, image, 'candidate-smoke')
    COMMON.write(root/'reports/baseline-inventory.json', encoded(initial))
    COMMON.write(root/'reports/candidate-inventory.json', encoded(installed))
    COMMON.write(root/'reports/candidate-image.json', encoded(after))
    for name in ('vulnerabilities.json', 'sbom.cdx.json'):
        COMMON.write(root/'reports'/name, b'')
    COMMON.write(root/'empty-trivy.yaml', b'{}\n')
    phase('candidate_receipt')
    record = {'schema': 'alpine_datastore_security_candidate/1', 'status': 'built_tested_not_accepted',
        'profile': profile, 'image_id': image, 'baseline': pin, 'registry': registry,
        'parent_daemon_id': parent_image, 'baseline_public_facts_sha256': BASELINE_FACTS_SHA256,
        'public_registry_observation_sha256': REGISTRY_FACTS_SHA256,
        'followup_public_facts_sha256': FOLLOWUP_FACTS_SHA256, 'libc_vendor_security_revision': libc,
        'gosu_windows_advisory_scope': scope,
        'recipe': identity, 'inputs': inputs, 'runtime_config': before['Config'],
        'candidate_layers': after['RootFS']['Layers'], 'server_bytes_unchanged': True,
        'whole_runtime_config_unchanged': True, 'same_branch_signed_apk': True,
        'gosu_source_commit': GOSU_COMMIT if profile == 'postgres' else None,
        'gosu_dependency_security_overlay': json.loads(COMMON.read(root/'reports/gosu-dependency-overlay.json', 65536)) if profile == 'postgres' else None,
        'compiler': 'go1.26.10' if profile == 'postgres' else None,
        'production_data_used': False, 'production_installed': False, 'security_accepted': False,
        'scanner_gate_waived': False, 'full_role_matrix': 'NOT_TESTED'}
    record['evidence_sha256'] = evidence(root)
    COMMON.write(root/'reports/candidate.json', encoded(record))
    with Path(os.environ['GITHUB_OUTPUT']).open('a') as output:
        output.write('image_id='+image+'\n')


def evidence(root):
    paths = [path for path in sorted((root/'reports').iterdir())
             if path.name not in {'candidate.json', 'vulnerabilities.json', 'sbom.cdx.json'}]
    return {path.name: sha256(COMMON.read(path, 32*1024**2)).hexdigest() for path in paths}


def oci_runtime_config(value):
    # Docker inspect expands these API defaults; OCI/scanner JSON can omit them.
    # Whole before/after Docker Config equality is checked separately, without normalization.
    defaults = {'Hostname': '', 'Domainname': '', 'User': '', 'AttachStdin': False,
        'AttachStdout': False, 'AttachStderr': False, 'Tty': False, 'OpenStdin': False,
        'StdinOnce': False, 'ExposedPorts': None, 'Env': None, 'Cmd': None,
        'Healthcheck': None, 'ArgsEscaped': False, 'Image': '', 'Volumes': None,
        'WorkingDir': '', 'Entrypoint': None, 'NetworkDisabled': False, 'MacAddress': '',
        'OnBuild': None, 'Labels': None, 'StopSignal': '', 'StopTimeout': None, 'Shell': None}
    require(type(value) is dict, 'raw_config')
    return {key: item for key, item in value.items()
            if key not in defaults or type(item) is not type(defaults[key]) or item != defaults[key]}


def validate_reports(record, raw, sbom):
    require(raw.get('SchemaVersion') == 2 and raw.get('ArtifactType') == 'container_image'
            and raw.get('Metadata', {}).get('ImageID') == record['image_id']
            and type(raw.get('Results')) is list and raw['Results'], 'raw_identity')
    config = raw['Metadata']['ImageConfig']
    require(oci_runtime_config(config['config']) == oci_runtime_config(record['runtime_config'])
            and config['rootfs']['diff_ids'] == record['candidate_layers'], 'raw_config')
    component = sbom.get('metadata', {}).get('component', {})
    require(component.get('name') == record['image_id'] and component.get('type') == 'container'
            and [r.get('value') for r in component.get('properties', [])
                 if r.get('name') == 'aquasecurity:trivy:ImageID'] == [record['image_id']], 'sbom_identity')
    for name, version in record['baseline']['updates'].items():
        require({r.get('version') for r in sbom.get('components', []) if r.get('name') == name} == {version}, 'sbom_package')
    if record['baseline'] == PROFILES['postgres']:
        for name, version in (('github.com/moby/sys/user', USER_MODULE_VERSION), ('golang.org/x/sys', 'v0.1.0')):
            require({r.get('version') for r in sbom.get('components', []) if r.get('name') == name} == {version}, 'sbom_module')
    counts = {}
    for result in raw['Results']:
        for row in result.get('Vulnerabilities') or []:
            severity = row['Severity']; counts[severity] = counts.get(severity, 0)+1
    return counts


def finalize(root, profile):
    phase('final_admission'); ci(root); identity = load_source(root)
    raw = COMMON.read(root/'reports/candidate.json', 2*1024**2); record = json.loads(raw)
    require(profile in PROFILES and record.get('schema') == 'alpine_datastore_security_candidate/1'
            and record.get('status') == 'built_tested_not_accepted' and record.get('profile') == profile
            and record.get('baseline') == PROFILES[profile] and record.get('recipe') == identity
            and all(record.get(key) is False for key in ('security_accepted', 'scanner_gate_waived',
                'production_installed', 'production_data_used'))
            and record.get('evidence_sha256') == evidence(root), 'candidate_state')
    original = json.loads(COMMON.read(root/'reports/baseline-inventory.json', 4*1024**2))
    installed = json.loads(COMMON.read(root/'reports/candidate-inventory.json', 4*1024**2))
    libc = libc_patch_receipt(profile, original, installed, record['inputs'])
    require(record.get('libc_vendor_security_revision') == libc
            == json.loads(COMMON.read(root/'reports/libc-security-revision.json', 65536)), 'libc_receipt')
    if profile == 'postgres':
        scope = windows_scope_proof(COMMON.read(root/'reports/build.log', 32*1024**2))
        scope['image_id'] = record['image_id']; scope['gosu_sha256'] = installed['files']['/usr/local/bin/gosu']
        require(record.get('gosu_windows_advisory_scope') == scope
                == json.loads(COMMON.read(root/'reports/gosu-windows-scope.json', 65536)), 'gosu_windows_scope')
        overlay = json.loads(COMMON.read(root/'reports/gosu-dependency-overlay.json', 65536))
        require(record.get('gosu_dependency_security_overlay') == overlay
                and overlay.get('base_source_commit') == GOSU_COMMIT
                and overlay.get('gosu_cli_version') == '1.19'
                and overlay.get('three_go_code_files_unchanged') is True
                and overlay.get('x_sys_version') == 'v0.1.0'
                and overlay.get('moby_sys_user', {}).get('effective_version') == USER_MODULE_VERSION
                and overlay['moby_sys_user'].get('sumdb_lines') == [line.decode() for line in USER_MODULE_SUMS]
                and set(overlay.get('changed_files', {})) == {'go.mod', 'go.sum'}, 'gosu_dependency_receipt')
        for name, change in overlay['changed_files'].items():
            item = record['inputs']['gosu/'+name]
            require(change == {'original_sha256': item['original_download']['sha256'],
                               'effective_sha256': item['sha256']}, 'gosu_dependency_receipt')
    before = COMMON.image_record(docker(root, ['image', 'inspect', PROFILES[profile]['reference']], maximum=2*1024**2))
    require(registry_identity(root, profile) == record['registry'], 'registry_changed')
    baseline(profile, before, record['registry'])
    after = COMMON.image_record(docker(root, ['image', 'inspect', record['image_id']], maximum=2*1024**2), record['image_id'])
    COMMON.compare_images(before, after)
    require(after['Config'] == record['runtime_config'] and after['RootFS']['Layers'] == record['candidate_layers'], 'candidate_changed')
    phase('raw_reports'); reports = {name: COMMON.read(root/'reports'/name, 32*1024**2) for name in ('vulnerabilities.json', 'sbom.cdx.json')}
    counts = validate_reports(record, *(json.loads(reports[name]) for name in ('vulnerabilities.json', 'sbom.cdx.json')))
    phase('export'); archive = COMMON.export_image(root, record['image_id'])
    COMMON.write(root/'image-export.json', encoded({'schema': 'alpine_datastore_security_export/1',
        'status': 'exported_not_accepted', 'profile': profile, 'image_id': record['image_id'],
        'parent_config_id': PROFILES[profile]['config_id'], 'parent_reference': PROFILES[profile]['reference'],
        'archive': archive, 'candidate_receipt_sha256': sha256(raw).hexdigest(),
        'evidence_sha256': record['evidence_sha256'], 'raw_counts': counts,
        'libc_vendor_security_revision': libc, 'gosu_windows_advisory_scope': record['gosu_windows_advisory_scope'],
        'gosu_dependency_security_overlay': record['gosu_dependency_security_overlay'],
        'reports_sha256': {name: sha256(value).hexdigest() for name, value in reports.items()},
        'security_accepted': False, 'scanner_gate_waived': False, 'production_data_used': False,
        'production_installed': False, 'full_role_matrix': 'NOT_TESTED'}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('compose', 'finalize'))
    parser.add_argument('--profile', choices=('postgres', 'redis'), required=True)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        {'compose': compose, 'finalize': finalize}[args.command](args.root, args.profile)
        return 0
    except Exception as error:
        known = {'profile', 'sealed_path', 'sealed_metadata', 'sealed_bound', 'sealed_changed',
            'github_ci_only', 'ci_root', 'ci_identity', 'source_inventory', 'source_changed', 'controller_changed',
            'download_origin', 'download_redirect', 'download_timeout', 'download_bound', 'download_identity',
            'registry_digest', 'registry_schema', 'registry_index', 'registry_platform', 'registry_platform_pin', 'registry_config', 'registry_layers',
            'baseline_identity', 'baseline_repository', 'audit_bound', 'audit_shape', 'audit_duplicate', 'audit_file',
            'audit_inventory', 'audit_nodes', 'software_baseline', 'gosu_version', 'gosu_baseline', 'unexpected_gosu',
            'package_baseline', 'package_delta', 'protected_runtime_changed', 'gosu_not_rebuilt', 'server_or_support_changed',
            'libc_revision', 'libc_security_revision', 'libc_vendor_input', 'libc_receipt', 'gosu_original_lock',
            'gosu_scope_log', 'gosu_windows_scope', 'gosu_dependency_receipt', 'sbom_module',
            'cleanup_unknown', 'cleanup_owner', 'fixture_capability', 'container_identity', 'fixture_failed',
            'build_input_changed', 'fixture_marker', 'gosu_fixture_marker', 'runner_space', 'candidate_identity',
            'public_repository_inventory', 'public_repository_metadata', 'public_repository_bytes', 'public_repository_changed',
            'raw_identity', 'raw_config', 'sbom_identity', 'sbom_package', 'candidate_state', 'registry_changed',
            'candidate_changed', 'phase'}
        code = error.args[0] if type(error) is RuntimeError and len(error.args) == 1 and type(error.args[0]) is str and error.args[0] in known else 'unexpected_failure'
        if code == 'unexpected_failure' and COMMON is not None:
            code = COMMON.public_error(error)
        failure = {'schema': 'alpine_datastore_security_failure/1', 'status': 'refused', 'phase': PHASE,
            'code': code, 'profile': args.profile, 'security_accepted': False, 'production_installed': False}
        if COMMON is not None and PHASE not in {'controller_admission', 'published_sources'}:
            try:
                COMMON.write(args.root/'reports/failure.json', encoded(failure))
            except OSError:
                pass
        print(json.dumps(failure), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
