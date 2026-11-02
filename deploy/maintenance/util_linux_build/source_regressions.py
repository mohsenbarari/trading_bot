#!/usr/bin/env python3
"""Compile real fixed function bodies with closed syscall/context fixtures.

This is source-function evidence, not a privileged mount/cgroup exploit test.
The kernel/credential negative controls remain explicitly NOT_TESTED.
"""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

spec = importlib.util.spec_from_file_location('candidate_build', Path(__file__).with_name('build.py'))
B = importlib.util.module_from_spec(spec)
spec.loader.exec_module(B)


def block_end(text, opening):
    B.require(text[opening] == '{', 'block_opening')
    pos = opening + 1; depth = 1; state = 'code'
    while pos < len(text):
        c, pair = text[pos], text[pos:pos + 2]
        if state == 'code':
            if pair in ('/*', '//'):
                state = pair; pos += 2; continue
            if c in ('"', "'"):
                state = c
            elif c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    return pos + 1
        elif state == '/*' and pair == '*/':
            state = 'code'; pos += 2; continue
        elif state == '//' and c == '\n':
            state = 'code'
        elif state in ('"', "'"):
            if c == '\\':
                pos += 2; continue
            if c == state:
                state = 'code'
        pos += 1
    raise RuntimeError('function_unterminated')


def function(text, name):
    pattern = r'(?m)^(?:static\s+)?(?:inline\s+)?(?:int|void)\s+' + re.escape(name) + r'\s*\([^;{}]*\)\s*\{'
    matches = list(re.finditer(pattern, text))
    B.require(len(matches) == 1, 'function_identity')
    match = matches[0]
    return text[match.start():block_end(text, match.end() - 1)]


def main_join_block(text):
    main = function(text, 'main')
    matches = list(re.finditer(r'(?m)^\s*if\s*\(\s*cgroup_procs_fd\s*>=\s*0\s*\)\s*', main))
    B.require(len(matches) == 1, 'main_cgroup_block_identity')
    match = matches[0]; opening = match.end()
    if main[opening] == '{':
        end = block_end(main, opening)
    else:
        statement = re.match(r'join_into_cgroup\s*\(\s*\)\s*;', main[opening:])
        B.require(statement is not None, 'main_cgroup_statement_identity')
        end = opening + statement.end()
    boundary = list(re.finditer(r'if\s*\(\s*uid_gid_fd\s*>=\s*0\s*\)', main))
    gid = list(re.finditer(r'\bsetgid\s*\(\s*gid\s*\)', main))
    uid = list(re.finditer(r'\bsetuid\s*\(\s*uid\s*\)', main))
    B.require(len(boundary) == len(gid) == len(uid) == 1
              and end < boundary[0].start() < gid[0].start() < uid[0].start(), 'main_credential_drop_order')
    return main[match.start():end]


MOUNT_FIXTURE = r'''
#include <assert.h>
#include <errno.h>
#include <string.h>
struct libmnt_context {void *fs; int helper_exec_status,flags,action; const char *fstype_pattern;};
struct libmnt_ns {int fixture;};
#define MNT_FL_MOUNTFLAGS_MERGED 1
#define MNT_FL_PREPARED 2
#define MNT_ACT_MOUNT 1
#define MNT_ERR_NAMESPACE 100
#define MNT_STAGE_MOUNT_PRE 1
#define MNT_STAGE_MOUNT_POST 2
#define DBG(a,b) ((void)0)
static int helper, status, syscall_called, hooks;
static struct libmnt_ns ns;
static int mnt_context_helper_executed(struct libmnt_context *c){(void)c;return helper;}
static int mnt_context_get_helper_status(struct libmnt_context *c){(void)c;return status;}
static int mnt_context_syscall_called(struct libmnt_context *c){(void)c;return syscall_called;}
static int mnt_context_get_status(struct libmnt_context *c){(void)c;return status;}
static struct libmnt_ns *mnt_context_switch_target_ns(struct libmnt_context *c){(void)c;return &ns;}
static int mnt_context_switch_ns(struct libmnt_context *c,struct libmnt_ns *n){(void)c;(void)n;return 1;}
static int mnt_context_call_hooks(struct libmnt_context *c,int stage){(void)c;if(stage==2)hooks++;return 0;}
static const char *mnt_fs_get_fstype(void *fs){(void)fs;return "fixture";}
static int do_mount(struct libmnt_context *c,void *p){(void)c;(void)p;return 0;}
static int do_mount_by_types(struct libmnt_context *c,const char *t){(void)c;(void)t;return 0;}
static int do_mount_by_pattern(struct libmnt_context *c,const char *t){(void)c;(void)t;return 0;}
'''
MOUNT_MAIN = r'''
int main(void){
 struct libmnt_context c={&ns,1,3,1,0};
 helper=1;status=1;syscall_called=0;hooks=0;
 if(mnt_context_do_mount(&c)!=0 || hooks!=0)return 10;
 helper=0;status=0;syscall_called=1;hooks=0;
 if(mnt_context_do_mount(&c)!=0 || hooks!=0)return 11;
 helper=0;status=0;syscall_called=0;hooks=0;
 if(mnt_context_do_mount(&c)!=0 || hooks!=1)return 12;
 helper=1;status=0;syscall_called=0;hooks=0;
 if(mnt_context_do_mount(&c)!=0 || hooks!=1)return 13;
 return 0;
}
'''
CGROUP_FIXTURE = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <err.h>
#include <fcntl.h>
#include <limits.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/syscall.h>
#define _(x) (x)
#define stringify_value(x) "4294967295"
#define _PATH_SYS_CGROUP "/fixture-cgroup"
static int cgroup_procs_fd=-1, source_fd, append_fd, read_fd=-1, bad_initial_fd, missing_cloexec;
static void open_target_fd(int *fd,const char *type,const char *arg){
 (void)type;(void)arg;if(*fd!=-1)bad_initial_fd=1;*fd=dup(source_fd);read_fd=*fd;assert(*fd>=3);
}
static ssize_t read_all_alloc(int fd,char **buf){*buf=calloc(1,256);assert(*buf);return read(fd,*buf,255);}
static int proof_open(const char *path,int flags,...){
 assert(strcmp(path,"/fixture-cgroup//fixture/cgroup.procs")==0);
 assert((flags&O_APPEND)==O_APPEND);if(!(flags&O_CLOEXEC))missing_cloexec=1;
 return fcntl(append_fd,F_DUPFD_CLOEXEC,3);
}
static int write_all(int fd,const void *buf,size_t size){return write(fd,buf,size)==(ssize_t)size?0:-1;}
#define open proof_open
'''
CGROUP_MAIN = r'''
#undef open
int main(int argc,char **argv){
 assert(argc==3);source_fd=open(argv[1],O_RDONLY);append_fd=open(argv[2],O_WRONLY|O_APPEND);
 assert(source_fd>=3 && append_fd>=3);open_cgroup_procs();int privileged_fd=cgroup_procs_fd;
 actual_main_join();
 if(bad_initial_fd || missing_cloexec || cgroup_procs_fd!=-1)return 20;
 if(fcntl(read_fd,F_GETFD)!=-1 || errno!=EBADF)return 21;
 if(fcntl(privileged_fd,F_GETFD)!=-1 || errno!=EBADF)return 22;
 if(fcntl(0,F_GETFD)==-1)return 23;
 close(source_fd);close(append_fd);return 0;
}
'''


def compile_run(root, name, code, argv, expected_success):
    source, binary = root / (name + '.c'), root / name
    source.write_text(code)
    B.command(['/usr/bin/cc', '-Wall', '-Wextra', '-Wno-unused-function', '-Werror', '-O2',
               str(source), '-o', str(binary)], root, root / (name + '-compile.log'), 20)
    with (root / (name + '-run.log')).open('xb') as output:
        result = subprocess.run([str(binary), *argv], input=b'fixture-stdin\n',
                                stdout=output, stderr=output, check=False, timeout=5, env=B.environment())
    B.require((result.returncode == 0) == expected_success, 'compiled_negative_control_failed')
    return {'exit_code': result.returncode, 'source_function_fixture_sha256': sha256(code.encode()).hexdigest()}


def run(old, source, disabled):
    B.require(old == Path('/work/util-linux-2.41.5') and source == Path('/work/util-linux-2.41.6')
              and disabled == Path('/work/mountfd-disabled'), 'source_paths_only')
    root = Path('/work/source-regressions'); root.mkdir(mode=0o700)
    records = {}
    for tag, folder, success in (('old', old, False), ('candidate', source, True)):
        text = B.read(folder / 'libmount/src/context_mount.c').decode()
        helper = function(text, 'is_mount_stage_failed') if 'static int is_mount_stage_failed(' in text else ''
        code = MOUNT_FIXTURE + helper + '\n' + function(text, 'mnt_context_do_mount') + MOUNT_MAIN
        mount = compile_run(root, tag + '-failed-mount', code, [], success)
        text = B.read(folder / 'sys-utils/nsenter.c').decode()
        inp, out = root / (tag + '-cgroup.txt'), root / (tag + '-cgroup.procs')
        inp.write_text('0::/fixture\n'); out.write_text('')
        # The stable fix closes the privileged FD in MAIN, after join returns.
        # Compile that exact source block, never hand-write its close/reset.
        code = (CGROUP_FIXTURE + function(text, 'open_cgroup_procs') + '\n'
                + function(text, 'join_into_cgroup') + '\nstatic void actual_main_join(void){\n'
                + main_join_block(text) + '\n}\n' + CGROUP_MAIN)
        cgroup = compile_run(root, tag + '-descriptor-lifetime', code, [str(inp), str(out)], success)
        B.require(re.fullmatch(rb'[1-9][0-9]*', B.read(out)), 'self_pid_write_not_observed')
        records[tag] = {'failed_mount_posthook': mount, 'descriptor_lifetime': cgroup}
    # Loading the actually compiled disabled variant with NOW binding catches
    # unresolved mnt_open_tree references rather than merely grepping a guard.
    lib = disabled / '.libs/libmount.so'
    lib = lib.resolve(strict=True)
    B.require(lib.is_relative_to(disabled), 'disabled_library_escape')
    B.command(['/usr/local/bin/python3', '-I', '-B', '-c',
               'import ctypes,os,sys;ctypes.CDLL(sys.argv[1],mode=os.RTLD_NOW|os.RTLD_LOCAL)', str(lib)],
              root, root / 'disabled-loader.log', 10)
    print(json.dumps({'compiled_contracts': 'passed', 'negative_controls': records,
        'mountfd_enabled_full_build': 'passed', 'mountfd_disabled_build_and_loader': 'passed',
        'fixture_boundary': 'real_source_functions_and_main_join_block_compiled_with_controlled_context_and_syscall_stubs',
        'privileged_failed_helper_ownership': 'NOT_TESTED',
        'privileged_cgroup_namespace_credential_drop': 'NOT_TESTED',
        'restricted_symlink_procfs_mount_races': 'NOT_TESTED',
        'CVE-2026-78409_vendor_binary_reconciliation': 'OPEN',
        'security_accepted': False, 'production_installed': False}, sort_keys=True))


if __name__ == '__main__':
    run(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
