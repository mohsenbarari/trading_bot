"""Independent, benign CI source contract for CVE-2026-78409.

Never mount, unshare, open a tree, change credentials, or execute package code.
Only ordinary GitHub CI may compile/run the extracted source with harmless
stubs. Existing package receipts and their OPEN reconciliation stay unchanged.
"""
import argparse
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys
import tarfile
import time

ARCHIVE = 'util-linux-2.41.6.tar.xz'
PACKAGING = 'util-linux_2.41.5-0+deb13u1.debian.tar.xz'
INPUTS = {
    ARCHIVE: (9470680, 'e596083744e746be7d2823b62b43f4418dd7bf56303b4dc09e6fe8112fe3d7ed'),
    PACKAGING: (107604, '5b327ccd22f0f4ed28a389870aa51d04ecedb8693e52a1d122850f2b3188cbf6'),
    '79c2881c27a0b40889cd5433d9f125689826d1d2.patch': (1271, 'ad095c0456e71636c2b9fd9c41fb24031ad5ab66dfac9e3fd6a9f40cfcecf0ab'),
    '485dbb67f1b6bb18e08b1b77f4aa2373ff3a705b.patch': (2620, '0b30248db96c77926ab9ad33474003bb1b13d652368dc754a84ac51ec61f7220'),
    '233cf7321e9d0fd2cea901d0a97e565c725640ad.patch': (4383, '6f98c72584bbc602869f663b4e1f1394cd45f8c086199241c3e2a4293f401b4e'),
}
FOLLOWUPS = {
    '79c2881c27a0b40889cd5433d9f125689826d1d2.patch': {'libmount/src/hook_idmap.c'},
    '485dbb67f1b6bb18e08b1b77f4aa2373ff3a705b.patch': {'sys-utils/nsenter.c'},
    '233cf7321e9d0fd2cea901d0a97e565c725640ad.patch': {
        'libmount/src/hook_idmap.c', 'libmount/src/hooks.c', 'libmount/src/version.c', 'sys-utils/mount.8.adoc'},
}
SERIES = ('debian/man-getopt-examples.patch', 'debian/verbose-tests.patch',
    'debian/hardlink-tests-known-failed.patch', 'debian/lsfd-usrbin.patch', 'debian/sensible-pager.patch',
    'debian/login-support-LOG_OK_LOGINS.patch', 'debian/login-do-not-print-motd-done-by-PAM.patch',
    'debian/login-turn-off-btmp-utmp-lastlog-writing.patch', 'debian/salsa-ci-skip-tests.patch',
    'upstream/loopdev-use-openat2-RESOLVE_NO_SYMLINKS-for-backing-file.patch',
    'upstream/libmount-restrict-source-path-canonicalization-for-non-ro.patch')
PROTECTED = frozenset(('libmount/src/hook_subdir.c', 'libmount/src/context.c'))
RECEIPT_SHA = '3a799ad92549c9d3c5e18013463743d9bc2b721eb83557e5ee3a0273d4514dde'
PACKAGE = 'libmount1_2.41.6-0+deb13u0+gold1_amd64.deb'
PACKAGE_SIZE, PACKAGE_SHA = 210776, '111baa6313bd1e72002d99725bf2f82e07d8dafd282269e005d5600aa616aaca'


def require(value, code):
    if not value:
        raise RuntimeError(code)


def identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mode,
            value.st_nlink, value.st_mtime_ns, value.st_ctime_ns)


def read(path, maximum):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid in (0, os.geteuid())
                and before.st_nlink == 1 and not before.st_mode & 0o022
                and 0 < before.st_size <= maximum, 'input_metadata')
        raw = bytearray()
        while len(raw) <= before.st_size:
            block = os.read(fd, min(65536, before.st_size + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(fd))
                == identity(Path(path).lstat()), 'input_changed')
        return bytes(raw)
    finally:
        os.close(fd)


def members(raw, wanted, *, prefix, maximum):
    result, count, total, seen = {}, 0, 0, set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode='r:xz') as archive:
        for entry in archive:
            count += 1; total += entry.size
            require(count <= 20000 and total <= maximum and entry.name not in seen, 'source_inventory')
            seen.add(entry.name)
            require((entry.name.startswith(prefix + '/') or entry.isdir() and entry.name.rstrip('/') == prefix)
                    and '..' not in Path(entry.name).parts
                    and not Path(entry.name).is_absolute(), 'source_path')
            if entry.name in wanted:
                require(entry.isfile() and 0 < entry.size <= 262144, 'source_member')
                data = archive.extractfile(entry).read(entry.size + 1)
                require(len(data) == entry.size, 'source_member')
                result[entry.name] = data
    require(set(result) == set(wanted), 'source_members_missing')
    return result


def patch_paths(raw):
    paths = re.findall(rb'(?m)^(?:---|\+\+\+) ([^\t\n ]+)', raw)
    require(paths and len(paths) % 2 == 0, 'patch_headers')
    result = set()
    for path in paths:
        require(path.startswith((b'a/', b'b/')), 'patch_headers')
        value = path[2:].decode('ascii')
        require(value and '..' not in Path(value).parts and not value.startswith('/'), 'patch_headers')
        result.add(value)
    return result


def block_end(text, opening):
    require(text[opening] == '{', 'function_block')
    pos, depth, state = opening + 1, 1, 'code'
    while pos < len(text):
        char, pair = text[pos], text[pos:pos + 2]
        if state == 'code':
            if pair in ('/*', '//'):
                state = pair; pos += 2; continue
            if char in ('"', "'"):
                state = char
            elif char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    return pos + 1
        elif state == '/*' and pair == '*/':
            state = 'code'; pos += 2; continue
        elif state == '//' and char == '\n':
            state = 'code'
        elif state in ('"', "'"):
            if char == '\\':
                pos += 2; continue
            if char == state:
                state = 'code'
        pos += 1
    raise RuntimeError('function_unterminated')


def function(text, name):
    hits = list(re.finditer(r'(?m)^(?:static\s+)?int\s+' + re.escape(name) + r'\s*\([^;{}]*\)\s*\{', text))
    require(len(hits) == 1, 'function_identity')
    hit = hits[0]
    return text[hit.start():block_end(text, hit.end() - 1)]


def remove_guard(text):
    hits = list(re.finditer(r'\bif\s*\(\s*mnt_context_target_fd_required\s*\(\s*cxt\s*\)\s*\)\s*\{', text))
    require(len(hits) == 1, 'guard_identity')
    hit = hits[0]; end = block_end(text, hit.end() - 1)
    require('return -ENOTSUP;' in text[hit.start():end], 'guard_refusal')
    return text[:hit.start()] + '/* controlled missing-guard negative control */' + text[end:]


def prepare(buffers, receipt_raw, package_raw):
    require(set(buffers) == set(INPUTS), 'input_inventory')
    for name, (size, digest) in INPUTS.items():
        require(len(buffers[name]) == size and sha256(buffers[name]).hexdigest() == digest, 'input_pin')
    require(sha256(receipt_raw).hexdigest() == RECEIPT_SHA
            and len(package_raw) == PACKAGE_SIZE and sha256(package_raw).hexdigest() == PACKAGE_SHA, 'package_receipt_pin')
    receipt = json.loads(receipt_raw)
    require(receipt['schema'] == 'trixie_util_linux_source_candidate/1'
            and receipt['status'] == 'candidate_built_not_accepted'
            and receipt['source_regressions']['CVE-2026-78409_vendor_binary_reconciliation'] == 'OPEN'
            and all(receipt[key] is False for key in ('security_accepted', 'raw_scanner_gate_waived',
                                                     'production_installed', 'services_changed')), 'historical_receipt')
    for name, (_, digest) in INPUTS.items():
        key = name[:8] + '.patch' if name in FOLLOWUPS else name
        require(receipt['inputs_sha256'][key] == digest, 'receipt_input_pin')
    selected = [row for row in receipt['all_family_packages'] if row['name'] == 'libmount1']
    require(selected == [{'name': 'libmount1', 'path': PACKAGE, 'version': '2.41.6-0+deb13u0+gold1',
                          'bytes': PACKAGE_SIZE, 'sha256': PACKAGE_SHA}], 'receipt_package_pin')
    paths = {}
    for name, expected in FOLLOWUPS.items():
        actual = patch_paths(buffers[name])
        require(actual == expected and not actual & PROTECTED, 'followup_protected_path')
        paths[name] = sorted(actual)
    wanted = ['debian/patches/series', *('debian/patches/' + value for value in SERIES[:9])]
    packaging = members(buffers[PACKAGING], wanted, prefix='debian', maximum=16 * 1024**2)
    require(tuple(packaging[wanted[0]].decode().splitlines()) == SERIES, 'packaging_series')
    for name in wanted[1:]:
        actual = patch_paths(packaging[name])
        require(not actual & PROTECTED, 'packaging_protected_path')
        paths[name] = sorted(actual)
    prefix = 'util-linux-2.41.6/'
    source = members(buffers[ARCHIVE], [prefix + name for name in sorted(PROTECTED)],
                     prefix='util-linux-2.41.6', maximum=256 * 1024**2)
    texts = {name.removeprefix(prefix): raw.decode() for name, raw in source.items()}
    functions = {'mnt_context_is_restricted': function(texts['libmount/src/context.c'], 'mnt_context_is_restricted'),
                 'mnt_context_target_fd_required': function(texts['libmount/src/context.c'], 'mnt_context_target_fd_required'),
                 'hook_mount_pre': function(texts['libmount/src/hook_subdir.c'], 'hook_mount_pre')}
    remove_guard(functions['hook_mount_pre'])
    evidence = {'input_sha256': {name: sha256(raw).hexdigest() for name, raw in buffers.items()},
        'source_member_sha256': {name: sha256(raw).hexdigest() for name, raw in source.items()},
        'source_function_sha256': {name: sha256(raw.encode()).hexdigest() for name, raw in functions.items()},
        'active_patch_paths': paths, 'historical_build_receipt_sha256': RECEIPT_SHA,
        'libmount_package': {'path': PACKAGE, 'bytes': PACKAGE_SIZE, 'sha256': PACKAGE_SHA}}
    return functions, evidence


STUBS = r'''
#include <errno.h>
#include <stdlib.h>
#include <string.h>
struct libmnt_context { int restricted; void *fs; };
struct libmnt_hookset { int fixture; };
struct hookset_data { char *org_target; };
static struct hookset_data hsd;
static int have_subdir = 1, effects;
#define DBG(a,b) ((void)0)
#define MNT_PATH_TMPTGT "/inert-fixture-only"
#define MNT_STAGE_MOUNT_POST 2
static void *mnt_context_get_hookset_data(struct libmnt_context *c, const struct libmnt_hookset *h)
{ (void)c; (void)h; return have_subdir ? &hsd : NULL; }
static const char *mnt_fs_get_target(void *p) { (void)p; effects++; return "ordinary-target"; }
static char *fixture_strdup(const char *p)
{ size_t n = strlen(p)+1; char *v=malloc(n); if (v) memcpy(v,p,n); return v; }
#define strdup fixture_strdup
/* These stubs never call mkdir, unshare, open_tree, mount, or any syscall. */
static int tmptgt_unshare(struct hookset_data *p) { (void)p; effects++; return 0; }
static int mnt_fs_set_target(void *p, const char *s) { (void)p; (void)s; effects++; return 0; }
static int hook_mount_post(struct libmnt_context *c, const struct libmnt_hookset *h, void *p)
{ (void)c; (void)h; (void)p; effects++; return 0; }
static int mnt_context_append_hook(struct libmnt_context *c, const struct libmnt_hookset *h,
 int stage, void *p, int (*fn)(struct libmnt_context *,const struct libmnt_hookset *,void *))
{ (void)c; (void)h; (void)stage; (void)p; (void)fn; effects++; return 0; }
'''
MAIN = r'''
int main(void) {
 struct libmnt_context c = {1, NULL}; struct libmnt_hookset h = {0};
 if (mnt_context_target_fd_required(&c) != 1) return 11;
 if (hook_mount_pre(&c, &h, NULL) != -ENOTSUP || effects != 0 || hsd.org_target != NULL) return 10;
 have_subdir=0;
 if (hook_mount_pre(&c, &h, NULL) != 0 || effects != 0) return 12;
 have_subdir=1; c.restricted=0;
 if (mnt_context_target_fd_required(&c) != 0) return 13;
 if (hook_mount_pre(&c, &h, NULL) != 0 || effects != 4 || hsd.org_target == NULL) return 14;
 free(hsd.org_target); return 0;
}
'''


def source_code(functions, *, mutated=False):
    pre = functions['hook_mount_pre']
    if mutated:
        pre = remove_guard(pre)
    return STUBS + '\n' + functions['mnt_context_is_restricted'] + '\n' \
        + functions['mnt_context_target_fd_required'] + '\n' + pre + '\n' + MAIN


def write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def command(argv, output, name, deadline, expected):
    remaining = min(20, deadline - time.monotonic())
    require(remaining > 0, 'contract_deadline')
    def limits():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (15, 15))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (262144, 262144))
    log = output / (name + '.log')
    with log.open('xb') as stream:
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
            cwd=output, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'HOME': str(output)},
            start_new_session=True, preexec_fn=limits)
        completed = False
        try:
            require(child.wait(timeout=remaining) == expected, 'contract_command_failed')
            completed = True
        finally:
            if not completed or child.poll() is None:
                try: os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError: pass
            child.wait(timeout=2)
    info = log.lstat()
    require(info.st_size <= 262144, 'contract_log_bound')
    return {'exit_code': expected, 'log_bytes': info.st_size,
            'log_sha256': sha256(read(log, 262144)).hexdigest() if info.st_size else sha256(b'').hexdigest()}


def compile_contract(functions, output, deadline):
    records = {}
    for enabled in (False, True):
        mode = 'mountfd-enabled' if enabled else 'mountfd-disabled'
        for mutated in (False, True):
            name = mode + ('-missing-guard' if mutated else '-original')
            code = source_code(functions, mutated=mutated).encode()
            source, binary = output / (name + '.c'), output / name
            write(source, code)
            argv = ['/usr/bin/cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function', '-O2']
            if enabled:
                argv.append('-DUSE_LIBMOUNT_MOUNTFD_SUPPORT=1')
            compile_result = command([*argv, str(source), '-o', str(binary)], output, name + '-compile', deadline, 0)
            binary_raw = read(binary, 262144)
            run_result = command([str(binary)], output, name + '-run', deadline, 10 if mutated else 0)
            records[name] = {'fixture_source_sha256': sha256(code).hexdigest(),
                'fixture_binary_sha256': sha256(binary_raw).hexdigest(), 'compile': compile_result, 'run': run_result}
    return records


def admit_ci(inputs, receipt, package, output, commit, verifier):
    require(os.geteuid() == 1001 and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_OS') == 'Linux', 'github_ci_only')
    require(type(commit) is str and re.fullmatch('[a-f0-9]{40}', commit)
            and type(verifier) is str and re.fullmatch('[a-f0-9]{64}', verifier), 'source_identity')
    require(sha256(read(Path(__file__), 65536)).hexdigest() == verifier, 'verifier_pin')
    temporary = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    require(output.is_absolute() and output.parent.resolve(strict=True) == output.parent
            and output.parent.is_relative_to(temporary) and output != temporary
            and output.parent.stat().st_uid == 1001, 'output_scope')
    for path, basename in ((inputs, None), (receipt, 'build-receipt.json'), (package, PACKAGE)):
        require(path.is_absolute() and path.resolve(strict=True) == path and path.is_relative_to(temporary)
                and (basename is None or path.name == basename), 'input_scope')
    require(not output.exists() and not output.is_symlink(), 'fresh_output_required')


def run(inputs, receipt, package, output, commit, verifier):
    admit_ci(inputs, receipt, package, output, commit, verifier)
    functions, evidence = prepare({name: read(inputs / name, size) for name, (size, _) in INPUTS.items()},
                                  read(receipt, 1024**2), read(package, PACKAGE_SIZE))
    os.umask(0o077); output.mkdir(mode=0o700)
    records = compile_contract(functions, output, time.monotonic() + 120)
    result = {'schema': 'util_linux_restricted_subdir_contract/1', 'status': 'source_contract_passed_not_accepted',
        'advisory': 'CVE-2026-78409', 'source_commit': commit, 'verifier_sha256': verifier,
        **evidence, 'compiled_contracts': records, 'restricted_refusal_before_stub_side_effects': True,
        'fixture_scope': 'real_source_functions_with_harmless_context_stubs_not_package_execution',
        'maintainer_signatures_authenticated': False,
        'privileged_or_kernel_mount_operations_executed': False, 'historical_receipts_modified': False,
        'vendor_database_reconciliation_completed': False, 'scanner_findings_modified': False,
        'production_installed': False, 'security_accepted': False}
    write(output / 'proof.json', (json.dumps(result, sort_keys=True) + '\n').encode())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'receipt', 'package', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--verifier-sha256', required=True)
    args = parser.parse_args()
    run(args.inputs, args.receipt, args.package, args.output, args.source_commit, args.verifier_sha256)
    print('{"status":"source_contract_passed_not_accepted","security_accepted":false}')


if __name__ == '__main__':
    try: main()
    except Exception:
        print('{"status":"refused","code":"restricted_subdir_contract_failed"}', file=sys.stderr)
        raise SystemExit(1) from None
