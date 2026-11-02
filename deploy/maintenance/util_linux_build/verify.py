#!/usr/bin/env python3
"""Actual old-header/old-library callers, rerun unchanged on candidate libraries."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('candidate_build', Path(__file__).with_name('build.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

CALLERS = {
    'blkid': ('blkid/blkid.h', 'blkid_new_probe_from_filename', r'''
    blkid_probe p=blkid_new_probe_from_filename(argv[1]); assert(p);
    assert(blkid_do_safeprobe(p)==0); const char *value; size_t len;
    assert(blkid_probe_lookup_value(p,"TYPE",&value,&len)==0);
    assert(strncmp(value,"ext",3)==0); blkid_free_probe(p);'''),
    'mount': ('libmount/libmount.h', 'mnt_new_table', r'''
    struct libmnt_table *t=mnt_new_table(); assert(t);
    assert(mnt_table_parse_file(t,argv[1])==0); assert(mnt_table_get_nents(t)==1);
    mnt_unref_table(t);'''),
    'uuid': ('uuid/uuid.h', 'uuid_generate', r'''
    uuid_t a,b; char text[37]; uuid_generate(a); uuid_unparse_lower(a,text);
    assert(uuid_parse(text,b)==0); assert(uuid_compare(a,b)==0);'''),
    'smartcols': ('libsmartcols/libsmartcols.h', 'scols_new_table', r'''
    struct libscols_table *t=scols_new_table(); assert(t);
    assert(scols_table_new_column(t,"fixture",0,0));
    struct libscols_line *l=scols_table_new_line(t,NULL); assert(l);
    assert(scols_line_set_data(l,0,"Persian-safe-fixture")==0);
    char *text=NULL; assert(scols_print_table_to_string(t,&text)==0);
    assert(text && strstr(text,"Persian-safe-fixture")); free(text); scols_unref_table(t);'''),
    'fdisk': ('libfdisk/libfdisk.h', 'fdisk_new_context', r'''
    struct fdisk_context *c=fdisk_new_context(); assert(c);
    assert(fdisk_assign_device(c,argv[1],1)==0); assert(fdisk_is_readonly(c));
    fdisk_deassign_device(c,1); fdisk_unref_context(c);'''),
    'lastlog2': ('lastlog2.h', 'll2_new_context', r'''
    struct ll2_context *c=ll2_new_context(argv[1]); assert(c); char *error=NULL;
    assert(ll2_write_entry(c,"fixture",1792800000,"tty-fixture","local","candidate",&error)==0);
    int64_t when=0; char *tty=NULL,*host=NULL,*pam=NULL;
    assert(ll2_read_entry(c,"fixture",&when,&tty,&host,&pam,&error)==0);
    assert(when==1792800000 && strcmp(tty,"tty-fixture")==0);
    free(tty); free(host); free(pam); free(error); ll2_unref_context(c);'''),
}


def exports(text):
    result = set()
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[4] in ('GLOBAL', 'WEAK') and fields[6] not in ('UND', 'ABS'):
            result.add(fields[7].replace('@@', '@'))
    B.require(result, 'empty_export_inventory')
    return result


def soname(text):
    values = re.findall(r'\(SONAME\).*\[([^\]]+)\]', text)
    B.require(len(values) == 1, 'soname_identity')
    return values[0]


def library(root, name):
    folder = root / 'usr/lib/x86_64-linux-gnu'
    candidates = list(folder.glob('lib' + name + '.so.*'))
    targets = {p.resolve(strict=True) for p in candidates}
    B.require(len(targets) == 1, 'library_inventory')
    target = targets.pop()
    B.require(target.is_relative_to(folder.resolve(strict=True)) and target.is_file(), 'library_escape')
    return target


def run(old, candidate):
    B.require(old == Path('/work/old') and candidate == Path('/work/candidate'), 'candidate_paths_only')
    root = Path('/work/abi'); root.mkdir(mode=0o700)
    filesystem = root / 'ext4.img'
    with filesystem.open('xb') as output:
        output.truncate(16 * 1024**2)
    B.command(['/usr/sbin/mkfs.ext4', '-q', '-F', str(filesystem)], root, root / 'mkfs.log', 20)
    mounttable = root / 'fstab'; mounttable.write_text('fixture /fixture ext4 ro 0 0\n')
    blank = root / 'blank.img'
    with blank.open('xb') as output:
        output.truncate(2 * 1024**2)
    records = {}
    for name, (header, symbol, body) in CALLERS.items():
        before, after = library(old, name), library(candidate, name)
        inventories = []
        for tag, path in (('old', before), ('candidate', after)):
            dynamic, symbols, versions = [root / (name + '-' + tag + '-' + kind + '.txt')
                                          for kind in ('dynamic', 'symbols', 'versions')]
            B.command(['/usr/bin/readelf', '-d', str(path)], root, dynamic, 10)
            B.command(['/usr/bin/readelf', '--dyn-syms', '--wide', str(path)], root, symbols, 10)
            B.command(['/usr/bin/readelf', '--version-info', str(path)], root, versions, 10)
            glibc = {tuple(map(int, v.split('.'))) for v in re.findall(r'Name: GLIBC_([0-9.]+)', B.read(versions).decode())}
            B.require(not glibc or max(glibc) <= (2, 41), 'newer_than_trixie_glibc')
            inventories.append((soname(B.read(dynamic).decode()), exports(B.read(symbols).decode())))
        B.require(inventories[0][0] == inventories[1][0] and inventories[0][1] <= inventories[1][1], 'old_export_removed')
        code = ('#define _GNU_SOURCE\n#include <assert.h>\n#include <stdlib.h>\n#include <string.h>\n'
                '#include <stdint.h>\n#include <dlfcn.h>\n#include <' + header + '>\n'
                'int main(int argc,char **argv){assert(argc==3); Dl_info info; '
                'assert(dladdr(dlsym(RTLD_DEFAULT,"' + symbol + '"),&info)); '
                'assert(strstr(info.dli_fname,argv[2])==info.dli_fname);\n' + body + '\nreturn 0;}\n')
        source, binary = root / (name + '.c'), root / (name + '-old-caller')
        source.write_text(code)
        B.command(['/usr/bin/cc', '-Wall', '-Wextra', '-Werror', '-O2', '-I' + str(old / 'usr/include'),
                   '-I' + str(old / 'usr/include/liblastlog2'), str(source), '-L' + str(before.parent),
                   '-Wl,-rpath,' + str(before.parent), '-l' + name, '-ldl', '-o', str(binary)], root, root / (name + '-compile.log'), 20)
        digest = sha256(B.read(binary)).hexdigest()
        for tag, location in (('old', before.parent), ('candidate', after.parent)):
            argument = str(filesystem if name == 'blkid' else mounttable if name == 'mount' else blank if name == 'fdisk'
                           else root / (tag + '-lastlog.db'))
            B.command([str(binary), argument, str(location)], root, root / (name + '-' + tag + '-run.log'), 10,
                      {'LD_LIBRARY_PATH': str(location)})
            B.require(sha256(B.read(binary)).hexdigest() == digest, 'old_caller_changed')
        records[name] = {'status': 'passed', 'old_caller_sha256': digest, 'soname': inventories[0][0],
                         'old_exports_retained': len(inventories[0][1]),
                         'old_library_sha256': sha256(B.read(before)).hexdigest(),
                         'candidate_library_sha256': sha256(B.read(after)).hexdigest()}
    # Headers and packaging mode behavior must not silently drift. These are
    # actual extracted files; dpkg symbol checks also ran during full build.
    for path in ('etc/pam.d/login', 'etc/pam.d/su', 'etc/pam.d/su-l',
                 'etc/pam.d/runuser', 'etc/pam.d/runuser-l'):
        B.require(B.read(old / path) == B.read(candidate / path), 'pam_configuration_changed')
    for path in ('usr/bin/su', 'usr/bin/newgrp', 'usr/bin/mount', 'usr/bin/umount', 'usr/bin/login'):
        B.require((old / path).stat().st_mode & 0o7777 == (candidate / path).stat().st_mode & 0o7777,
                  'privileged_program_mode_changed')
    print(json.dumps({'status': 'passed', 'libraries': records, 'pam_bytes_unchanged': True,
                      'program_modes_unchanged': True, 'production_installed': False}, sort_keys=True))


if __name__ == '__main__':
    run(Path(sys.argv[1]), Path(sys.argv[2]))
