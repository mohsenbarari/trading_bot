#!/usr/bin/env python3
"""Ordinary vendor-update ABI and functional compatibility, executed only in CI."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec = importlib.util.spec_from_file_location('ncurses_candidate_build', Path(__file__).with_name('build.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)

CALLERS = {
    'ncurses': ('curses.h', 'newterm', r'''
    SCREEN *screen = newterm("xterm", terminal_output, terminal_input); assert(screen);
    set_term(screen); WINDOW *window = newwin(3, 20, 0, 0); assert(window);
    assert(waddstr(window, "normal text") != ERR);
    char observed[32] = {0}; assert(mvwinnstr(window, 0, 0, observed, 20) >= 0);
    assert(strncmp(observed, "normal text", 11) == 0);
    assert(wrefresh(window) != ERR); assert(delwin(window) != ERR);
    endwin(); delscreen(screen);'''),
    'ncursesw': ('ncursesw/curses.h', 'newterm', r'''
    assert(setlocale(LC_ALL, "C.UTF-8"));
    SCREEN *screen = newterm("xterm", terminal_output, terminal_input); assert(screen);
    set_term(screen); WINDOW *window = newwin(3, 20, 0, 0); assert(window);
    assert(waddwstr(window, L"wide Ω") != ERR);
    wchar_t observed[32] = {0}; assert(mvwinnwstr(window, 0, 0, observed, 20) >= 0);
    assert(wcsncmp(observed, L"wide Ω", 6) == 0);
    assert(wrefresh(window) != ERR); assert(delwin(window) != ERR);
    endwin(); delscreen(screen);'''),
    'tinfo': ('term.h', 'setupterm', r'''
    int terminal_status = 0;
    assert(setupterm("xterm", fileno(terminal_output), &terminal_status) == OK);
    assert(terminal_status == 1 && tigetnum("cols") > 0);
    char *cursor = tigetstr("cup"); assert(cursor && cursor != (char *)-1);
    char *expanded = tparm(cursor, 2L, 4L); assert(expanded && *expanded);
    assert(del_curterm(cur_term) == OK);'''),
}
TERMINALS = ('xterm', 'linux')

PYTHON_SMOKE = r'''
import curses, json, pathlib, readline, sys
curses.setupterm(term="xterm")
assert curses.tigetnum("cols") > 0
cursor = curses.tigetstr("cup")
assert isinstance(cursor, bytes) and cursor
assert isinstance(curses.tparm(cursor, 2, 4), bytes)
readline.clear_history()
readline.add_history("ordinary compatibility")
assert readline.get_current_history_length() == 1
assert readline.get_history_item(1) == "ordinary compatibility"
readline.parse_and_bind("set editing-mode emacs")
readline.clear_history()
with pathlib.Path('/proc/self/maps').open('rb') as stream:
    raw = stream.read(1048577)
assert len(raw) <= 1048576
mapped = {line.split(maxsplit=5)[5] for line in raw.decode().splitlines()
          if len(line.split(maxsplit=5)) == 6}
assert all(str(pathlib.Path(value).resolve(strict=True)) in mapped for value in sys.argv[1:])
print(json.dumps({'status': 'passed', 'curses': True, 'readline': True,
                  'expected_library_maps': True}, sort_keys=True))
'''


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
    B.require(name in CALLERS, 'library_scope')
    folder = root / 'usr/lib/x86_64-linux-gnu'
    targets = {path.resolve(strict=True) for path in folder.glob('lib' + name + '.so.*')}
    B.require(len(targets) == 1, 'library_inventory')
    target = targets.pop()
    B.require(target.is_relative_to(folder.resolve(strict=True)) and target.is_file(), 'library_escape')
    return target


def terminal_data(root):
    folders = []
    for relative in ('usr/share/terminfo', 'lib/terminfo', 'etc/terminfo'):
        path = root / relative
        if path.exists():
            folder = path.resolve(strict=True)
            B.require(folder.is_relative_to(root) and folder.is_dir(), 'terminfo_directory_escape')
            if folder not in folders:
                folders.append(folder)
    entries = {}
    for name in TERMINALS:
        for folder in folders:
            for prefix in (name[0], format(ord(name[0]), 'x')):
                entry = folder / prefix / name
                if entry.is_file():
                    resolved = entry.resolve(strict=True)
                    B.require(resolved.is_relative_to(root), 'terminfo_entry_escape')
                    raw = B.read(resolved, 1024 * 1024)
                    B.require(raw, 'terminfo_entry_empty')
                    entries[name] = {'folder': folder, 'sha256': sha256(raw).hexdigest()}
                    break
            if name in entries:
                break
    B.require(set(entries) == set(TERMINALS), 'ordinary_terminfo_entries_missing')
    return folders, entries


def runtime_environment(root, location):
    folders, entries = terminal_data(root)
    return {
        'LD_LIBRARY_PATH': str(location), 'TERM': 'xterm',
        'TERMINFO': str(entries['xterm']['folder']),
        'TERMINFO_DIRS': ':'.join(map(str, folders)),
    }


def normalized_entry(text):
    # Ignore only infocmp's reconstructed-file comments and field ordering.
    return sorted(line.strip() for line in text.splitlines()
                  if line.strip() and not line.lstrip().startswith('#'))


def ordinary_functions(root, package_root, tag, libraries):
    environment = runtime_environment(package_root, libraries['tinfo'].parent)
    smoke = root / (tag + '-python.json')
    B.command(['/usr/local/bin/python3', '-I', '-B', '-c', PYTHON_SMOKE,
               str(libraries['tinfo']), str(libraries['ncursesw'])], root, smoke, 20, environment)
    python = json.loads(B.read(smoke, 65536))
    B.require(python == {'status': 'passed', 'curses': True, 'readline': True,
                         'expected_library_maps': True}, 'python_compatibility_failed')
    _, entries = terminal_data(package_root)
    results = {}
    for name in TERMINALS:
        original = root / (tag + '-' + name + '-original.info')
        rebuilt = root / (tag + '-' + name + '-rebuilt.info')
        compiled = root / (tag + '-' + name + '-terminfo')
        compiled.mkdir(mode=0o700)
        infocmp, tic = package_root / 'usr/bin/infocmp', package_root / 'usr/bin/tic'
        for executable in (infocmp, tic):
            B.require(executable.resolve(strict=True).is_relative_to(package_root)
                      and executable.is_file(), 'normal_tool_path_invalid')
        B.command([str(infocmp), '-x', '-1', '-A', str(entries[name]['folder']), name],
                  root, original, 15, environment)
        B.command([str(tic), '-x', '-o', str(compiled), str(original)], root,
                  root / (tag + '-' + name + '-tic.log'), 15, environment)
        B.command([str(infocmp), '-x', '-1', '-A', str(compiled), name],
                  root, rebuilt, 15, environment)
        B.require(normalized_entry(B.read(original, 1024 * 1024).decode())
                  == normalized_entry(B.read(rebuilt, 1024 * 1024).decode()), 'terminfo_roundtrip_changed')
        results[name] = {'status': 'passed', 'existing_entry_sha256': entries[name]['sha256']}
    return {'python': python, 'existing_terminfo_roundtrips': results}


def run(old, candidate):
    B.require(old == Path('/work/baseline') and candidate == Path('/work/candidate'), 'candidate_paths_only')
    B.require(os.geteuid() == 1001 and Path('/.dockerenv').is_file(), 'nonroot_ci_container_required')
    root = Path('/work/abi')
    root.mkdir(mode=0o700)
    libraries = {tag: {name: library(package_root, name) for name in CALLERS}
                 for tag, package_root in (('old', old), ('candidate', candidate))}
    records = {}
    for name, (header, symbol, body) in CALLERS.items():
        inventories = []
        for tag in ('old', 'candidate'):
            path = libraries[tag][name]
            outputs = {kind: root / (name + '-' + tag + '-' + kind + '.txt')
                       for kind in ('dynamic', 'symbols', 'versions')}
            for arguments, kind in ((['-d'], 'dynamic'), (['--dyn-syms', '--wide'], 'symbols'),
                                    (['--version-info'], 'versions')):
                B.command(['/usr/bin/readelf', *arguments, str(path)], root, outputs[kind], 10)
            versions = B.read(outputs['versions']).decode()
            glibc = {tuple(map(int, value.split('.')))
                     for value in re.findall(r'Name: GLIBC_([0-9.]+)', versions)}
            B.require(not glibc or max(glibc) <= (2, 41), 'newer_than_trixie_glibc')
            inventories.append((soname(B.read(outputs['dynamic']).decode()),
                                exports(B.read(outputs['symbols']).decode())))
        B.require(inventories[0][0] == inventories[1][0] == 'lib' + name + '.so.6'
                  and inventories[0][1] <= inventories[1][1], 'old_export_or_soname_changed')
        code = ('#define _GNU_SOURCE\n#define _XOPEN_SOURCE_EXTENDED 1\n'
                '#include <assert.h>\n#include <stdio.h>\n#include <stdlib.h>\n'
                '#include <string.h>\n#include <locale.h>\n#include <wchar.h>\n'
                '#include <dlfcn.h>\n' + ('#include <curses.h>\n' if name == 'tinfo' else '')
                + '#include <' + header + '>\n'
                'int main(int argc,char **argv){assert(argc==3); Dl_info info;\n'
                'assert(dladdr(dlsym(RTLD_DEFAULT,"' + symbol + '"),&info));\n'
                'size_t prefix=strlen(argv[1]); assert(strncmp(info.dli_fname,argv[1],prefix)==0 '
                '&& info.dli_fname[prefix]==\'/\');\n'
                'FILE *terminal_output=fopen(argv[2],"w"); assert(terminal_output);\n'
                'FILE *terminal_input=fopen("/dev/null","r"); assert(terminal_input);\n'
                + body + '\nassert(fclose(terminal_input)==0); '
                'assert(fclose(terminal_output)==0); return 0;}\n')
        source, binary = root / (name + '.c'), root / (name + '-old-caller')
        source.write_text(code)
        before = libraries['old'][name]
        link = [str(before)]
        if name != 'tinfo':
            link.append(str(libraries['old']['tinfo']))
        B.command(['/usr/bin/cc', '-Wall', '-Wextra', '-Werror', '-O2',
                   '-I' + str(old / 'usr/include'), str(source), *link,
                   '-Wl,--enable-new-dtags,-rpath,' + str(before.parent),
                   '-ldl', '-o', str(binary)], root, root / (name + '-compile.log'), 30)
        digest = sha256(B.read(binary)).hexdigest()
        for tag, package_root in (('old', old), ('candidate', candidate)):
            location = libraries[tag][name].parent
            B.command([str(binary), str(location), str(root / (name + '-' + tag + '-terminal.out'))],
                      root, root / (name + '-' + tag + '-run.log'), 15,
                      runtime_environment(package_root, location))
            B.require(sha256(B.read(binary)).hexdigest() == digest, 'old_caller_changed')
        records[name] = {
            'status': 'passed', 'old_caller_sha256': digest, 'soname': inventories[0][0],
            'old_exports_retained': len(inventories[0][1]),
            'old_library_sha256': sha256(B.read(before)).hexdigest(),
            'candidate_library_sha256': sha256(B.read(libraries['candidate'][name])).hexdigest(),
        }
    functional = {tag: ordinary_functions(root, package_root, tag, libraries[tag])
                  for tag, package_root in (('old', old), ('candidate', candidate))}
    print(json.dumps({
        'status': 'passed', 'libraries': records,
        'ordinary_functional_compatibility': 'passed', 'functional_profiles': functional,
        'interactive_upstream_suite': 'NOT_TESTED', 'security_accepted': False,
        'production_installed': False,
    }, sort_keys=True))


if __name__ == '__main__':
    run(Path(sys.argv[1]), Path(sys.argv[2]))
