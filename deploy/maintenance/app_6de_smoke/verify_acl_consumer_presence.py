"""Static ACL consumer metadata from one disposable, immutable image.

No inspected file is executed or loaded. This report is an inventory for caller
review; it neither proves safe ACL use nor changes a vulnerability disposition.
The controller must establish the image identity before invoking these sealed
bytes in a read-only, networkless container without production mounts.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import struct
import sys
import time


ROOTS = ('/usr', '/app', '/opt')
MAX_ENTRIES = 100000
MAX_FILES = 50000
MAX_BYTES = 2 * 1024**3
MAX_ELF = 128 * 1024**2
MAX_REPORT = 4 * 1024**2
MAX_SECONDS = 60
LEGACY = frozenset(('acl_get_file', 'acl_set_file', 'acl_extended_file', 'acl_delete_def_file'))
AT = frozenset(name + '_at' for name in LEGACY)
FD = frozenset(('acl_get_fd', 'acl_set_fd', 'acl_extended_fd'))


def require(value, code):
    if not value:
        raise RuntimeError(code)


def identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def elf_metadata(raw):
    """Read bounded ELF section metadata; missing dynamic sections fail closed.

    Both ELF classes and byte orders are supported. Sectionless dynamic objects
    are refused rather than silently omitted from the consumer inventory.
    """
    require(16 <= len(raw) <= MAX_ELF and raw[:4] == b'\x7fELF', 'elf_identity')
    kind, order = raw[4], raw[5]
    require(kind in (1, 2) and order in (1, 2) and raw[6] == 1, 'elf_encoding')
    endian = '<' if order == 1 else '>'
    hfmt = endian + ('HHIIIIIHHHHHH' if kind == 1 else 'HHIQQQIHHHHHH')
    hsize = 16 + struct.calcsize(hfmt)
    require(len(raw) >= hsize, 'elf_header_bound')
    header = struct.unpack_from(hfmt, raw, 16)
    etype, machine, version, _, phoff, shoff, _, ehsize, phsize, phnum, shsize, shnum, _ = header
    require(etype in (1, 2, 3) and version == 1 and ehsize == hsize, 'elf_header')
    sfmt = endian + ('IIIIIIIIII' if kind == 1 else 'IIQQQQIIQQ')
    pfmt = endian + ('IIIIIIII' if kind == 1 else 'IIQQQQQQ')
    require(0 < shnum <= 8192 and shsize == struct.calcsize(sfmt), 'elf_section_inventory')
    require(shoff >= hsize and shoff + shnum * shsize <= len(raw), 'elf_section_bound')
    require(phnum <= 4096 and (phnum == 0 or phsize == struct.calcsize(pfmt)
            and phoff >= hsize and phoff + phnum * phsize <= len(raw)), 'elf_program_bound')
    sections = [struct.unpack_from(sfmt, raw, shoff + n * shsize) for n in range(shnum)]
    require(sections[0] == (0,) * 10, 'elf_null_section')
    for section in sections:
        require(section[1] == 8 or section[4] + section[5] <= len(raw), 'elf_section_bound')

    def string(table, offset):
        require(table[1] == 3 and 0 <= offset < table[5], 'elf_string_bound')
        start, end = table[4] + offset, table[4] + table[5]
        stop = raw.find(b'\0', start, min(end, start + 4097))
        require(stop >= start, 'elf_string_bound')
        value = raw[start:stop]
        require(all(32 <= c < 127 for c in value), 'elf_string_encoding')
        return value.decode('ascii')

    dynamic = [section for section in sections if section[1] == 6]
    require(len(dynamic) <= 1, 'elf_dynamic_inventory')
    segments = []
    for n in range(phnum):
        ph = struct.unpack_from(pfmt, raw, phoff + n * phsize)
        offset, size = (ph[1], ph[4]) if kind == 1 else (ph[2], ph[5])
        require(offset + size <= len(raw), 'elf_program_bound')
        if ph[0] == 2:
            segments.append((offset, size))
    require(segments == [(s[4], s[5]) for s in dynamic], 'elf_dynamic_section_required')
    needed, tags = [], {}
    if dynamic:
        section = dynamic[0]
        entry = 8 if kind == 1 else 16
        require(section[9] == entry and section[5] % entry == 0
                and section[6] < shnum, 'elf_dynamic_shape')
        table = sections[section[6]]
        ended = False
        for pos in range(section[4], section[4] + section[5], entry):
            tag, value = struct.unpack_from(endian + ('iI' if kind == 1 else 'qQ'), raw, pos)
            if tag == 0:
                ended = True
            else:
                require(not ended, 'elf_dynamic_after_null')
                if tag == 1:
                    name = string(table, value)
                    require(name and '/' not in name and len(name) <= 256, 'elf_needed_name')
                    needed.append(name)
                elif tag in (5, 6, 10, 11):
                    require(tag not in tags, 'elf_dynamic_shape')
                    tags[tag] = value
        require(ended and len(needed) <= 256, 'elf_dynamic_bound')
        require(tags.get(5) == table[3] and tags.get(10) == table[5], 'elf_dynamic_string_binding')
    symbols = [section for section in sections if section[1] == 11]
    require(len(symbols) <= 1, 'elf_symbol_inventory')
    imported = set()
    for section in symbols:
        entry = 16 if kind == 1 else 24
        require(section[9] == entry and section[5] % entry == 0
                and section[5] // entry <= 300000 and section[6] < shnum, 'elf_symbol_bound')
        table = sections[section[6]]
        if dynamic:
            require(section[6] == dynamic[0][6] and tags.get(6) == section[3]
                    and tags.get(11) == entry, 'elf_dynamic_symbol_binding')
        for pos in range(section[4], section[4] + section[5], entry):
            fields = struct.unpack_from(endian + ('IIIBBH' if kind == 1 else 'IBBHQQ'), raw, pos)
            name = string(table, fields[0])
            undefined = fields[5] == 0 if kind == 1 else fields[3] == 0
            if undefined and name in LEGACY | AT | FD:
                imported.add(name)
    require(not dynamic or symbols, 'elf_dynamic_symbols_required')
    return {'class': kind * 32, 'byte_order': 'little' if order == 1 else 'big',
            'machine': machine, 'type': etype, 'needed': sorted(needed),
            'undefined_legacy_acl': sorted(imported & LEGACY),
            'undefined_at_acl': sorted(imported & AT), 'undefined_fd_acl': sorted(imported & FD)}


def inventory(root=Path('/'), *, deadline=None):
    deadline = time.monotonic() + MAX_SECONDS if deadline is None else deadline
    count = files = advertised = examined = names_bytes = report_bytes = 0
    rows, links, regular = [], {}, set()

    def current():
        require(time.monotonic() < deadline, 'inventory_timeout')

    def walk(fd, public, depth):
        nonlocal count, files, advertised, examined, names_bytes, report_bytes
        require(depth <= 64, 'inventory_depth')
        before = os.fstat(fd)
        with os.scandir(fd) as entries:
            for entry in entries:
                current()
                count += 1
                path = public + '/' + entry.name
                names_bytes += len(os.fsencode(path))
                require(count <= MAX_ENTRIES and len(os.fsencode(path)) <= 4096, 'inventory_count_bound')
                require(names_bytes <= 8 * 1024**2, 'inventory_name_bound')
                info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    target = os.readlink(entry.name, dir_fd=fd)
                    require(len(os.fsencode(target)) <= 4096, 'symlink_bound')
                    names_bytes += len(os.fsencode(target))
                    require(names_bytes <= 8 * 1024**2, 'inventory_name_bound')
                    links[path] = posixpath.normpath(target if target.startswith('/')
                                                       else posixpath.dirname(path) + '/' + target)
                    require(identity(info) == identity(os.stat(entry.name, dir_fd=fd,
                                                               follow_symlinks=False)), 'inventory_changed')
                elif stat.S_ISDIR(info.st_mode):
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        require(identity(info) == identity(os.fstat(child)), 'inventory_changed')
                        walk(child, path, depth + 1)
                        require(identity(info) == identity(os.fstat(child))
                                == identity(os.stat(entry.name, dir_fd=fd, follow_symlinks=False)), 'inventory_changed')
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    files += 1
                    advertised += info.st_size
                    require(files <= MAX_FILES and advertised <= MAX_BYTES, 'inventory_byte_bound')
                    item = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                    try:
                        require(identity(info) == identity(os.fstat(item)), 'inventory_changed')
                        prefix = os.read(item, 4)
                        if prefix == b'\x7fELF':
                            require(4 <= info.st_size <= MAX_ELF, 'elf_file_bound')
                            raw = bytearray(prefix)
                            while len(raw) <= info.st_size:
                                current()
                                block = os.read(item, min(65536, info.st_size + 1 - len(raw)))
                                if not block:
                                    break
                                raw.extend(block)
                            require(len(raw) == info.st_size, 'inventory_changed')
                            examined += len(raw)
                            require(examined <= MAX_BYTES, 'inventory_byte_bound')
                            row = elf_metadata(bytes(raw))
                            row = {'path': path, 'bytes': len(raw), 'sha256': sha256(raw).hexdigest(), **row}
                            report_bytes += len(json.dumps(row, ensure_ascii=True))
                            require(report_bytes <= MAX_REPORT - 32768, 'report_bound')
                            rows.append(row)
                        regular.add(path)
                        require(identity(info) == identity(os.fstat(item))
                                == identity(os.stat(entry.name, dir_fd=fd, follow_symlinks=False)), 'inventory_changed')
                    finally:
                        os.close(item)
                else:
                    raise RuntimeError('inventory_special_file')
        current()
        require(identity(before) == identity(os.fstat(fd)), 'inventory_changed')

    for public in ROOTS:
        current()
        path = root / public.lstrip('/')
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            before = os.fstat(fd)
            require(identity(before) == identity(path.lstat()), 'inventory_changed')
            walk(fd, public, 0)
            require(identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'inventory_changed')
        finally:
            os.close(fd)
    aliases = {}
    for path in sorted(links):
        target, visited = links[path], {path}
        for _ in range(32):
            if target in regular:
                aliases.setdefault(target, []).append(path)
                break
            if target not in links or target in visited:
                break
            visited.add(target)
            target = links[target]
    final_size = 0
    for row in rows:
        row['symlink_aliases'] = aliases.get(row['path'], [])
        final_size += len(json.dumps(row, ensure_ascii=True))
        require(final_size <= MAX_REPORT - 4096, 'report_bound')
    rows.sort(key=lambda row: row['path'])
    current()
    return {'roots': list(ROOTS), 'entries_examined': count, 'regular_files_examined': files,
            'advertised_regular_bytes': advertised, 'elf_bytes_read': examined,
            'elf_files': rows, 'symlinks_observed': len(links)}


def probe(image, commit, verifier, *, root=Path('/')):
    require(type(image) is str and re.fullmatch('sha256:[a-f0-9]{64}', image)
            and type(commit) is str and re.fullmatch('[a-f0-9]{40}', commit)
            and type(verifier) is str and re.fullmatch('[a-f0-9]{64}', verifier), 'identity_invalid')
    result = inventory(root)
    return {'schema': 'security_acl_consumer_presence/1', 'image_id': image,
            'source_commit': commit, 'verifier_sha256': verifier, 'inventory': result,
            'scope': 'regular_elf_dynamic_needed_and_undefined_acl_symbols_under_fixed_roots',
            'directory_symlinks_followed': False, 'inspected_code_executed': False,
            'static_or_obfuscated_embedded_code_analyzed': False, 'dlopen_or_ctypes_reachability_analyzed': False,
            'acl_flags_or_callsite_safety_analyzed': False, 'production_mounts_or_data_used': False,
            'scanner_findings_modified': False, 'security_accepted': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-id', required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--verifier-sha256', required=True)
    args = parser.parse_args()
    raw = (json.dumps(probe(args.image_id, args.source_commit, args.verifier_sha256),
                      sort_keys=True, ensure_ascii=True, separators=(',', ':')) + '\n').encode()
    require(len(raw) <= MAX_REPORT, 'report_bound')
    sys.stdout.buffer.write(raw)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"status":"refused","code":"acl_consumer_probe_failed"}', file=sys.stderr)
        raise SystemExit(1) from None
