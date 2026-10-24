#!/usr/bin/env python3
"""Ordinary ACL ABI and valid-file archive compatibility, executed only in CI."""
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

spec=importlib.util.spec_from_file_location('acl_tar_candidate_build',Path(__file__).with_name('build.py'))
B=importlib.util.module_from_spec(spec);spec.loader.exec_module(B)

CALLER=r'''
#define _GNU_SOURCE
#include <assert.h>
#include <dlfcn.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <sys/acl.h>
#include <acl/libacl.h>
int main(int argc,char **argv) {
    assert(argc==4);
    assert(strcmp(argv[3],"create")==0 || strcmp(argv[3],"check")==0);
    Dl_info info;
    assert(dladdr(dlsym(RTLD_DEFAULT,"acl_from_text"),&info));
    size_t prefix=strlen(argv[1]);
    assert(strncmp(info.dli_fname,argv[1],prefix)==0 && info.dli_fname[prefix]=='/');
    int descriptor=open(argv[2],O_RDONLY|O_NOFOLLOW);
    assert(descriptor>=0);
    struct stat file;
    assert(fstat(descriptor,&file)==0 && S_ISREG(file.st_mode));
    assert(file.st_uid==geteuid() && file.st_nlink==1);
    char text[160];
    assert(snprintf(text,sizeof(text),"u::rw-,u:%lu:r--,g::r--,m::r--,o::---",(unsigned long)geteuid()+1)>0);
    acl_t expected=acl_from_text(text); assert(expected && acl_valid(expected)==0);
    if (strcmp(argv[3],"create")==0) assert(acl_set_file(argv[2],ACL_TYPE_ACCESS,expected)==0);
    acl_t observed=acl_get_file(argv[2],ACL_TYPE_ACCESS); assert(observed);
    assert(acl_valid(observed)==0 && acl_cmp(expected,observed)==0);
    acl_t via_descriptor=acl_get_fd(descriptor); assert(via_descriptor);
    assert(acl_cmp(expected,via_descriptor)==0);
    assert(acl_free(via_descriptor)==0 && acl_free(observed)==0 && acl_free(expected)==0);
    assert(close(descriptor)==0);
    return 0;
}
'''


def exports(text):
    result=set()
    for line in text.splitlines():
        fields=line.split()
        if len(fields)>=8 and fields[4] in ('GLOBAL','WEAK') and fields[6] not in ('UND','ABS'):
            result.add(fields[7].replace('@@','@'))
    B.require(result,'empty_export_inventory')
    return result


def soname(text):
    values=re.findall(r'\(SONAME\).*\[([^\]]+)\]',text)
    B.require(len(values)==1,'soname_identity')
    return values[0]


def check_glibc(text):
    versions={tuple(map(int,value.split('.'))) for value in re.findall(r'Name: GLIBC_([0-9.]+)',text)}
    B.require(not versions or max(versions)<=(2,41),'newer_than_trixie_glibc')


def library(root):
    folder=root/'usr/lib/x86_64-linux-gnu'
    targets={path.resolve(strict=True) for path in folder.glob('libacl.so.*')}
    B.require(len(targets)==1,'library_inventory')
    target=targets.pop()
    B.require(target.is_relative_to(folder.resolve(strict=True)) and target.is_file(),'library_escape')
    return target


def inspect_library(root,tag,path):
    outputs={kind:root/(tag+'-'+kind+'.txt') for kind in ('dynamic','symbols','versions')}
    for arguments,kind in ((['-d'],'dynamic'),(['--dyn-syms','--wide'],'symbols'),(['--version-info'],'versions')):
        B.command(['/usr/bin/readelf',*arguments,str(path)],root,outputs[kind],10)
    check_glibc(B.read(outputs['versions']).decode())
    return soname(B.read(outputs['dynamic']).decode()),exports(B.read(outputs['symbols']).decode())


def caller_run(root,binary,digest,library_path,filename,mode,label):
    B.command([str(binary),str(library_path.parent),str(filename),mode],root,root/(label+'.log'),15,
              {'LD_LIBRARY_PATH':str(library_path.parent)})
    B.require(sha256(B.read(binary)).hexdigest()==digest,'old_caller_changed')


def tar_roundtrip(root,label,tar_root,library_path,binary,digest):
    tar=(tar_root/'usr/bin/tar').resolve(strict=True)
    B.require(tar.is_relative_to(tar_root) and tar.is_file(),'tar_path_invalid')
    environment={'LD_LIBRARY_PATH':str(library_path.parent)}
    versions=root/(label+'-tar-versions.txt')
    B.command(['/usr/bin/readelf','--version-info',str(tar)],root,versions,10)
    check_glibc(B.read(versions).decode())
    trace=root/(label+'-loader.txt')
    B.command([str(tar)],root,trace,10,{**environment,'LD_TRACE_LOADED_OBJECTS':'1'})
    text=B.read(trace,65536).decode()
    matches=re.findall(r'(?m)^\s*libacl\.so\.1\s+=>\s+(\S+)',text)
    B.require('not found' not in text and len(matches)==1
              and Path(matches[0]).resolve(strict=True)==library_path,'tar_acl_mapping_changed')
    source,restored=root/(label+'-source'),root/(label+'-restored')
    source.mkdir(mode=0o700);restored.mkdir(mode=0o700)
    expected={'ordinary.txt':b'ordinary ACL archive compatibility\n','normal-\u03a9.txt':'normal Unicode content \u03a9\n'.encode()}
    for name,data in expected.items():(source/name).write_bytes(data)
    filename=source/'ordinary.txt'
    caller_run(root,binary,digest,library_path,filename,'create',label+'-acl-create')
    archive=root/(label+'.tar')
    B.command([str(tar),'--format=pax','--acls','--numeric-owner','-cf',str(archive),'-C',str(source),'.'],
              root,root/(label+'-create.log'),20,environment)
    B.command([str(tar),'--acls','--no-same-owner','--same-permissions','-xf',str(archive),'-C',str(restored)],
              root,root/(label+'-extract.log'),20,environment)
    B.require({path.name for path in restored.iterdir()}==set(expected),'archive_inventory_changed')
    for name,data in expected.items():
        B.require(B.read(restored/name,65536)==data,'archive_content_changed')
    # Check mode never sets the ACL: a missing restored ACL cannot be repaired
    # by the comparison operation and then mistaken for a successful roundtrip.
    caller_run(root,binary,digest,library_path,restored/'ordinary.txt','check',label+'-acl-check')
    return {'status':'passed','tar_sha256':sha256(B.read(tar)).hexdigest(),
            'loaded_acl_sha256':sha256(B.read(library_path)).hexdigest(),
            'normal_archive_and_extended_acl':'passed'}


def run(old,candidate,phase):
    B.require(old==Path('/work/baseline') and candidate==Path('/work/candidate') and phase in {'acl','tar'},'candidate_paths_only')
    B.require(os.geteuid()==1001 and Path('/.dockerenv').is_file(),'nonroot_ci_container_required')
    root=Path('/work/abi');root.mkdir(mode=0o700)
    before,after=library(old),library(candidate)
    old_inventory=inspect_library(root,'old',before)
    new_inventory=inspect_library(root,'candidate',after)
    B.require(old_inventory[0]==new_inventory[0]=='libacl.so.1' and old_inventory[1]<=new_inventory[1],
              'old_export_or_soname_changed')
    source,binary=root/'acl-old-caller.c',root/'acl-old-caller'
    source.write_text(CALLER)
    B.command(['/usr/bin/cc','-Wall','-Wextra','-Werror','-O2','-I'+str(old/'usr/include'),str(source),str(before),
               '-Wl,--enable-new-dtags,-rpath,'+str(before.parent),'-ldl','-o',str(binary)],root,root/'caller-compile.log',30)
    digest=sha256(B.read(binary)).hexdigest()
    for tag,path in (('old',before),('candidate',after)):
        owned=root/(tag+'-ordinary.txt');owned.write_bytes(b'normal owned regular file\n')
        caller_run(root,binary,digest,path,owned,'create',tag+'-valid-file-create')
        caller_run(root,binary,digest,path,owned,'check',tag+'-valid-file-check')
    profiles=(('old_tar_old_acl',old,before),('old_tar_new_acl',old,after)) if phase=='acl' else (
              ('old_tar_new_acl',old,after),('new_tar_new_acl',candidate,after))
    tar_profiles={label:tar_roundtrip(root,label,tar_root,path,binary,digest)
                  for label,tar_root,path in profiles}
    print(json.dumps({'status':'passed','phase':phase,
        'libraries':{'acl':{'status':'passed','soname':'libacl.so.1','old_exports_retained':len(old_inventory[1]),
            'old_caller_sha256':digest,'old_library_sha256':sha256(B.read(before)).hexdigest(),
            'candidate_library_sha256':sha256(B.read(after)).hexdigest()}},
        'normal_file_acl':'passed','tar_profiles':tar_profiles,'legacy_file_api_security_assessed':False,
        'interactive_or_privileged_coverage':'NOT_TESTED','security_accepted':False,'production_installed':False},sort_keys=True))


if __name__=='__main__':
    run(Path(sys.argv[1]),Path(sys.argv[2]),sys.argv[3])
