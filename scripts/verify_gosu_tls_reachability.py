#!/usr/bin/env python3
"""Read-only evidence for ONE official gosu binary and CVE-2025-68121 only.

Explicit opt-in downloads 1.77 MB of public release bytes into bounded RAM.
Never opens Docker, local binaries, environment files, or writes any artifact.
This is not govulncheck and is not an assessment of other vulnerabilities.
"""
import argparse
from hashlib import sha256
import json
import signal
import struct
import urllib.error
import urllib.parse
import urllib.request

URL = 'https://github.com/tianon/gosu/releases/download/1.19/gosu-amd64'
SIZE = 1769900
SHA256 = '52c8749d0142edd234e9d6bd5237dff2d81e71f43537e2f4f66f75dd4b243dd0'
POSITIVES = ('main.main', 'main.SetupUser', 'os/exec.LookPath', 'syscall.Exec')


class Refusal(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Refusal(code)


class PublicReleaseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        require(target.scheme == 'https' and target.hostname in {
            'github.com', 'release-assets.githubusercontent.com',
        } and target.port in {None, 443} and not target.username and not target.password,
                'public_release_redirect_refused')
        return super().redirect_request(request, fp, code, message, headers, newurl)


def fetch_official():
    def expired(*_args):
        raise TimeoutError('public_release_wall_deadline')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(35)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicReleaseRedirect())
        with opener.open(URL, timeout=15) as response:
            require(response.status == 200, 'public_release_status')
            length = response.headers.get('Content-Length')
            require(length is None or length == str(SIZE), 'public_release_length')
            raw = bytearray()
            while chunk := response.read1(min(65536, SIZE + 1 - len(raw))):
                raw.extend(chunk)
                require(len(raw) <= SIZE, 'public_release_byte_bound')
            return bytes(raw)
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def verify_binary(raw):
    require(len(raw) == SIZE and sha256(raw).hexdigest() == SHA256, 'public_release_identity')
    require(raw[:7] == b'\x7fELF\x02\x01\x01' and struct.unpack_from('<H', raw, 18)[0] == 62,
            'elf64_little_x86_64_required')
    offset = struct.unpack_from('<Q', raw, 40)[0]
    entry_size, count, string_index = struct.unpack_from('<HHH', raw, 58)
    require(entry_size == 64 and 0 < string_index < count < 128 and offset + count * 64 <= len(raw),
            'elf_section_table_bound')
    sections = [struct.unpack_from('<IIQQQQIIQQ', raw, offset + n * 64) for n in range(count)]

    def section_bytes(section):
        start, length = section[4:6]
        require(start + length <= len(raw), 'elf_section_bound')
        return raw[start:start + length]

    strings = section_bytes(sections[string_index])
    pcln = None
    for section in sections:
        end = strings.find(b'\0', section[0])
        require(0 <= section[0] <= end < len(strings), 'elf_section_name_bound')
        if strings[section[0]:end] == b'.gopclntab':
            require(pcln is None, 'duplicate_go_function_table')
            pcln = section_bytes(section)
    require(pcln is not None and len(pcln) >= 72 and
            struct.unpack_from('<I', pcln)[0] == 0xfffffff1 and pcln[4:8] == b'\0\0\x01\x08',
            'go120_function_table_header')
    function_count = struct.unpack_from('<Q', pcln, 8)[0]
    name_start, name_end = struct.unpack_from('<QQ', pcln, 32)
    require(1000 < function_count < 20000 and 72 <= name_start < name_end <= len(pcln),
            'go_function_name_bounds')
    names = [name.decode('utf-8') for name in pcln[name_start:name_end].split(b'\0') if name]
    positives = {name: name in names for name in POSITIVES}
    require(all(positives.values()) and len(names) >= function_count, 'go_positive_controls')
    packages = {package: sum(name.startswith(package + '.') or name.startswith(package + '/')
                             for name in names) for package in ('crypto/tls', 'crypto/x509', 'net')}
    require(not any(packages.values()), 'tls_or_network_package_present')
    return {
        'scope': 'CVE-2025-68121 in exact gosu 1.19 amd64 binary only',
        'binary_sha256': SHA256, 'binary_bytes': len(raw),
        'runtime_function_count': function_count, 'function_name_table_count': len(names),
        'positive_controls': positives, 'package_function_counts': packages,
        'govulncheck_run': False, 'gpg_signature_verified': False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download-official', action='store_true', required=True,
                        help='explicitly allow bounded public HTTPS download; no files are written')
    parser.parse_args(argv)
    try:
        evidence = verify_binary(fetch_official())
    except (ValueError, OSError, struct.error) as error:
        print(json.dumps({'status': 'refused', 'reason': str(error) if isinstance(error, Refusal)
                          else type(error).__name__}))
        return 1
    print(json.dumps({'status': 'evidence_verified', **evidence}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
