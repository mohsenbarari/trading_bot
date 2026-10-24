#!/usr/bin/env python3
"""Actual candidate ELF/C-API/Python proof; run only in offline Noble builder."""
import ctypes as C
import json
import os
from pathlib import Path
import re
import resource
import signal
import subprocess
import sys
import tempfile


def require(ok, code):
    if not ok: raise RuntimeError(code)


def capture(argv):
    with tempfile.TemporaryFile() as output:
        subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                       timeout=5, check=True, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'})
        require(output.tell() <= 1024 * 1024, 'elf_output_bound')
        output.seek(0)
        return output.read(1024 * 1024).decode()


def elf(path):
    header = capture(['/usr/bin/readelf', '-h', str(path)])
    require('ELF64' in header and 'Advanced Micro Devices X86-64' in header, 'elf_architecture')
    dynamic = capture(['/usr/bin/readelf', '-d', str(path)])
    versions = capture(['/usr/bin/readelf', '--version-info', str(path)])
    required = set(re.findall(r'Name: GLIBC_([0-9.]+)', versions))
    require(all(tuple(map(int, v.split('.'))) <= (2, 39) for v in required), 'glibc_too_new')
    exports = {line.split()[0] for line in capture(['/usr/bin/nm', '-D', '--defined-only', '--format=posix', str(path)]).splitlines() if line.strip()}
    return {'soname': re.findall(r'\(SONAME\).*?\[([^]]+)\]', dynamic),
            'needed': sorted(re.findall(r'\(NEEDED\).*?\[([^]]+)\]', dynamic)),
            'glibc': sorted(required), 'exports': sorted(exports)}


def compare_abi(old, new, *, soname=None):
    require(old['soname'] == new['soname'] == ([] if soname is None else [soname]), 'soname_changed')
    require(set(old['exports']) <= set(new['exports']), 'export_removed')
    require(old['needed'] == new['needed'], 'dynamic_dependency_changed')


def c_api(candidate):
    directory = candidate / 'usr/lib/x86_64-linux-gnu'
    C.CDLL(str((directory / 'libbrotlicommon.so.1').resolve()), mode=C.RTLD_GLOBAL)
    encoder = C.CDLL(str((directory / 'libbrotlienc.so.1').resolve()), mode=C.RTLD_GLOBAL)
    decoder = C.CDLL(str((directory / 'libbrotlidec.so.1').resolve()), mode=C.RTLD_GLOBAL)
    u8, size = C.c_ubyte, C.c_size_t
    pointer = C.POINTER(u8)
    encoder.BrotliEncoderMaxCompressedSize.argtypes = [size]; encoder.BrotliEncoderMaxCompressedSize.restype = size
    encoder.BrotliEncoderCompress.argtypes = [C.c_int, C.c_int, C.c_int, size, pointer, C.POINTER(size), pointer]
    decoder.BrotliDecoderDecompress.argtypes = [size, pointer, C.POINTER(size), pointer]
    decoder.BrotliDecoderErrorString.argtypes = [C.c_int]; decoder.BrotliDecoderErrorString.restype = C.c_char_p
    require(decoder.BrotliDecoderErrorString(-17) == b'_ERROR_FORMAT_BLOCK_SWITCH', 'compiled_security_fix_missing')
    plain = ('آزمون قیمت سکه؛ Brotli streaming\n'.encode() + bytes(range(256))) * 8
    source = (u8 * len(plain)).from_buffer_copy(plain)
    encoded_size = size(encoder.BrotliEncoderMaxCompressedSize(len(plain)))
    encoded = (u8 * encoded_size.value)()
    require(encoder.BrotliEncoderCompress(4, 18, 0, len(plain), source, C.byref(encoded_size), encoded) == 1, 'c_encode')
    decoded_size, decoded = size(len(plain)), (u8 * len(plain))()
    require(decoder.BrotliDecoderDecompress(encoded_size, encoded, C.byref(decoded_size), decoded) == 1
            and decoded_size.value == len(plain) and bytes(decoded) == plain, 'c_roundtrip')
    decoder.BrotliDecoderCreateInstance.argtypes = [C.c_void_p] * 3
    decoder.BrotliDecoderCreateInstance.restype = C.c_void_p
    decoder.BrotliDecoderDestroyInstance.argtypes = [C.c_void_p]
    decoder.BrotliDecoderDecompressStream.argtypes = [C.c_void_p, C.POINTER(size), C.POINTER(pointer), C.POINTER(size), C.POINTER(pointer), C.POINTER(size)]
    state = decoder.BrotliDecoderCreateInstance(None, None, None)
    require(bool(state), 'c_stream_state')
    result, offset, body = 0, 0, bytearray()
    available_in, next_in, total = size(0), pointer(), size(0)
    try:
        for _ in range(10000):
            if available_in.value == 0 and offset < encoded_size.value:
                part = bytes(encoded[offset:min(offset + 3, encoded_size.value)])
                storage = (u8 * len(part)).from_buffer_copy(part)
                next_in, available_in = C.cast(storage, pointer), size(len(part))
                offset += len(part)
            output = (u8 * 64)(); available_out, next_out = size(64), C.cast(output, pointer)
            result = decoder.BrotliDecoderDecompressStream(state, C.byref(available_in), C.byref(next_in), C.byref(available_out), C.byref(next_out), C.byref(total))
            body.extend(bytes(output[:64 - available_out.value]))
            require(len(body) <= len(plain) and result in {1, 2, 3}, 'c_stream_error')
            if result == 1: break
        require(result == 1 and bytes(body) == plain, 'c_stream_roundtrip')
    finally: decoder.BrotliDecoderDestroyInstance(state)
    mappings = Path('/proc/self/maps').read_text()
    for line in mappings.splitlines():
        if '/libbrotli' in line:
            require(str(candidate) in line, 'old_shared_decoder_loaded')
    return {'roundtrip': True, 'streaming': True, 'security_error_minus17': True}


def python_isolation():
    require(not sys.getdlopenflags() & os.RTLD_GLOBAL, 'python_global_loader_flags')
    with Path('/proc/self/maps').open() as stream:
        mappings = stream.read(1024 * 1024 + 1)
    require(len(mappings) <= 1024 * 1024, 'python_mapping_bound')
    require('/libbrotli' not in mappings, 'shared_decoder_loaded_before_python_proof')


def python_api(candidate):
    python_isolation()
    sys.path.insert(0, str(candidate / 'usr/lib/python3/dist-packages'))
    import brotli
    import _brotli
    require(all(Path(m.__file__).resolve().is_relative_to(candidate) for m in (brotli, _brotli)), 'wrong_python_module')
    for plain in (b'ASCII Brotli', 'آزمون دادهٔ فارسی'.encode(), bytes(range(256)) * 8):
        encoded = brotli.compress(plain)
        require(brotli.decompress(encoded) == plain, 'python_roundtrip')
        stream = brotli.Decompressor()
        parts = [stream.process(encoded[i:i + 3]) for i in range(0, len(encoded), 3)]
        require(b''.join(parts) == plain and stream.is_finished(), 'python_stream')
        try: brotli.decompress(encoded[:-1])
        except brotli.error: pass
        else: raise RuntimeError('truncated_stream_accepted')
    try: brotli.decompress(b'\xff' * 8)
    except brotli.error: pass
    else: raise RuntimeError('malformed_stream_accepted')
    python_isolation()
    return {'roundtrip': True, 'streaming': True, 'bounded_malformed': True,
            'candidate_module_path': True, 'no_shared_decoder_interposition': True}


def run(old, candidate):
    require(Path('/.dockerenv').is_file() and old == Path('/work/old') and candidate == Path('/work/candidate'), 'builder_only')
    resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20)); resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('candidate_test_deadline')))
    signal.alarm(30)
    abi = {}
    for name in ('common', 'dec', 'enc'):
        suffix = Path('usr/lib/x86_64-linux-gnu') / f'libbrotli{name}.so.1'
        before, after = elf((old / suffix).resolve()), elf((candidate / suffix).resolve())
        compare_abi(before, after, soname=f'libbrotli{name}.so.1')
        abi[name] = {'soname': after['soname'][0], 'exports_retained': True, 'glibc': after['glibc']}
    suffix = Path('usr/lib/python3/dist-packages/_brotli.cpython-312-x86_64-linux-gnu.so')
    before, after = elf(old / suffix), elf(candidate / suffix)
    compare_abi(before, after)
    require('libbrotlidec.so.1' not in after['needed'], 'python_embedding_contract_changed')
    result = {'status': 'passed', 'abi': abi, 'python_embedded_decoder_rebuilt': True,
              'python_api': python_api(candidate), 'c_api': c_api(candidate), 'host_installed': False}
    signal.alarm(0)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__': run(Path(sys.argv[1]), Path(sys.argv[2]))
