"""Pure ELF fixtures; never download, inspect containers or run a command."""
from hashlib import sha256
import importlib.util
import io
from pathlib import Path
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_gosu_evidence_test', ROOT / 'scripts/verify_gosu_tls_reachability.py')
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


def binary(extra=(), *, omit=()):
    names = [name for name in R.POSITIVES if name not in omit]
    names += ['runtime.synthetic' + str(n) for n in range(1100)] + list(extra)
    name_bytes = b'\0'.join(name.encode() for name in names) + b'\0'
    pcln = bytearray(72 + len(name_bytes))
    struct.pack_into('<I', pcln, 0, 0xfffffff1)
    pcln[4:8] = b'\0\0\x01\x08'
    struct.pack_into('<Q', pcln, 8, 1100)
    struct.pack_into('<QQ', pcln, 32, 72, len(pcln))
    pcln[72:] = name_bytes
    strings = b'\0.shstrtab\0.gopclntab\0'
    raw = bytearray(256 + len(strings) + len(pcln))
    raw[:7] = b'\x7fELF\x02\x01\x01'
    struct.pack_into('<H', raw, 18, 62)
    struct.pack_into('<Q', raw, 40, 64)
    struct.pack_into('<HHH', raw, 58, 64, 3, 1)
    struct.pack_into('<IIQQQQIIQQ', raw, 128, 1, 3, 0, 0, 256, len(strings), 0, 0, 1, 0)
    struct.pack_into('<IIQQQQIIQQ', raw, 192, 11, 1, 0, 0, 256 + len(strings), len(pcln), 0, 0, 8, 0)
    raw[256:256 + len(strings)] = strings
    raw[256 + len(strings):] = pcln
    return bytes(raw)


class GosuReachabilityEvidenceTests(unittest.TestCase):
    def verify_fixture(self, raw):
        with patch.object(R, 'SIZE', len(raw)), patch.object(R, 'SHA256', sha256(raw).hexdigest()):
            return R.verify_binary(raw)

    def test_exact_table_has_positive_controls_and_no_tls_package(self):
        evidence = self.verify_fixture(binary())
        self.assertTrue(all(evidence['positive_controls'].values()))
        self.assertEqual(evidence['runtime_function_count'], 1100)
        self.assertEqual(evidence['package_function_counts'], {'crypto/tls': 0, 'crypto/x509': 0, 'net': 0})
        self.assertFalse(evidence['govulncheck_run'])
        self.assertFalse(evidence['gpg_signature_verified'])

    def test_tls_x509_or_network_symbols_cannot_be_accepted(self):
        for name in ('crypto/tls.(*Conn).Handshake', 'crypto/x509.Verify', 'net.Dial', 'net/http.Serve'):
            with self.subTest(name=name), self.assertRaisesRegex(R.Refusal, 'package_present'):
                self.verify_fixture(binary([name]))

    def test_missing_positive_control_is_not_negative_reachability_evidence(self):
        with self.assertRaisesRegex(R.Refusal, 'positive_controls'):
            self.verify_fixture(binary(omit=['main.main']))

    def test_wrong_public_hash_fails_before_elf_interpretation(self):
        with self.assertRaisesRegex(R.Refusal, 'public_release_identity'):
            R.verify_binary(binary())

    def test_invalid_section_or_go_offsets_are_refused(self):
        for offset, fmt, value in ((40, '<Q', 2**63), (58, '<H', 1), (192, '<I', 2**31),
                                   (256 + len(b'\0.shstrtab\0.gopclntab\0') + 32, '<Q', 2**63)):
            raw = bytearray(binary()); struct.pack_into(fmt, raw, offset, value)
            with self.subTest(offset=offset), self.assertRaises(R.Refusal):
                self.verify_fixture(bytes(raw))

    def test_default_cli_does_not_download_or_write(self):
        with patch.object(R, 'fetch_official', side_effect=AssertionError('no network')), \
                patch('sys.stderr', io.StringIO()), self.assertRaises(SystemExit) as error:
            R.main([])
        self.assertEqual(error.exception.code, 2)

    def test_download_rejects_oversized_stream_before_accumulating_unbounded_output(self):
        reads = []
        class Response(io.BytesIO):
            def read1(self, length):
                reads.append(length)
                return super().read1(length)
        response = Response(b'x' * 33)
        response.status = 200; response.headers = {}
        opener = SimpleNamespace(open=Mock(return_value=response))
        with patch.object(R, 'SIZE', 32), patch.object(R.urllib.request, 'build_opener', return_value=opener), \
                self.assertRaisesRegex(R.Refusal, 'byte_bound'):
            R.fetch_official()
        self.assertEqual(reads, [33])
        self.assertTrue(response.closed)

    def test_download_length_and_external_or_insecure_redirects_are_refused(self):
        response = io.BytesIO(b'')
        response.status = 200; response.headers = {'Content-Length': str(R.SIZE + 1)}
        opener = SimpleNamespace(open=Mock(return_value=response))
        with patch.object(R.urllib.request, 'build_opener', return_value=opener), \
                self.assertRaisesRegex(R.Refusal, 'release_length'):
            R.fetch_official()
        redirect = R.PublicReleaseRedirect()
        for url in ('http://github.com/a', 'https://outside.example/a', 'https://u:p@github.com/a'):
            with self.subTest(url=url), self.assertRaisesRegex(R.Refusal, 'redirect_refused'):
                redirect.redirect_request(None, None, 302, '', {}, url)


if __name__ == '__main__':
    unittest.main()
