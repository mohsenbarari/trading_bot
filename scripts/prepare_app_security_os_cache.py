"""Prepare the fixed App vendor-package cache or install it inside an image build.

Preparation supports the current real UID, including the hosted runner's1001.
The image command requires real root, /capsule read-only, network=none and the
existing setpriv contract. Neither command grants deployment/security acceptance.
"""
import argparse
from contextlib import ExitStack
import ctypes
import errno
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import resource
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from types import ModuleType,SimpleNamespace

ROOT=Path(__file__).resolve().parent
LOCK_NAME='app_security_os_packages.lock.json'
LOCK_SHA='14eefb6a4d40e02f2407b676c6c4c418b69968227a2059a67ddf58fdf99b1a82'
GROUPS=('util','ncurses','acl-tar')
CAPSULE=Path('/capsule')
LIBACL_DOC_APT_OPTIONS=('-o','DPkg::Options::=--path-include=/usr/share/doc/libacl1','-o','DPkg::Options::=--path-include=/usr/share/doc/libacl1/*')
MAX_SOURCE=131072
MAX_PACKAGE=32*1024**2
MAX_INSTALL_OUTPUT=2*1024**2
INSTALL_WALL_SECONDS=600
INSTALL_COMMAND_SECONDS=120
INSTALL_CPU_SECONDS=240
INSTALL_ADDRESS_BYTES=512*1024**2
# The 15 authenticated DEBs contain 816 data members, total 12,563,637 bytes;
# the largest regular member is 506,608 bytes. This file limit also leaves
# room for dpkg's status/journal writes; captured streams have a separate cap.
INSTALL_FILE_BYTES=64*1024**2
CODES=frozenset(('path','parent','file_metadata','file_bound','file_changed','file_pin','json_invalid','duplicate_key',
    'lock_contract','recipe_contract','manifest_contract','cache_inventory','cache_metadata','cache_busy','lock_metadata',
    'cache_changed','artifact_arguments','cache_exists','publish_unknown','root_required','image_cache_path',
    'image_architecture','image_readonly','image_network','installer_contract','installer_output_bound','installer_timeout',
    'installer_failed','installer_command','installer_cleanup','io_refusal','unexpected_refusal',
    'package_zip_identity','package_zip_inventory','package_zip_member','package_receipt_pin','package_receipt_state',
    'package_runtime_inventory','package_pin','package_path','package_bytes','acl_parent_pin','input_path','input_metadata',
    'input_changed','input_bound','artifact_download_failed','artifact_size','artifact_digest','download_exists','stream_bound','stream_size'))

def need(ok,code):
    if not ok:raise RuntimeError(code)

def identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def encode(value):return (json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()

def document(raw):
    def unique(rows):
        value={}
        for key,item in rows:
            need(key not in value,'duplicate_key');value[key]=item
        return value
    try:return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:need(False,'json_invalid'))
    except (ValueError,UnicodeError):raise RuntimeError('json_invalid') from None

def canonical(path):
    path=Path(path).absolute()
    need(path.resolve(strict=True)==path,'path');return path

def parents(path,readonly_root=None):
    rows=[]
    for parent in path.parents:
        s=parent.lstat()
        sticky_tmp=parent==Path('/tmp') and s.st_uid==0 and stat.S_IMODE(s.st_mode)==0o1777
        public_cache=readonly_root is not None and parent==readonly_root[0] and s.st_uid==readonly_root[1]
        need(parent.resolve(strict=True)==parent and stat.S_ISDIR(s.st_mode)
            and (s.st_uid in {0,os.geteuid()} or public_cache) and (not s.st_mode&0o022 or sticky_tmp),'parent')
        rows.append((parent,identity(s)[:5]))
    return rows

def check_parents(rows):
    for path,before in rows:
        need(path.resolve(strict=True)==path and identity(path.lstat())[:5]==before,'parent')

class Held:
    def __init__(self,stack,readonly_root=None):
        self.stack=stack;self.checks=[];self.bindings=[];self.directories=[];self.readonly_root=None
        if readonly_root is not None:
            root=canonical(readonly_root);need(root==CAPSULE and os.statvfs(root).f_flag&os.ST_RDONLY,'image_readonly')
            self.readonly_root=(root,root.lstat().st_uid)
    def read(self,path,maximum,pin=None,mode=None,owner=None):
        path=canonical(path);ancestors=parents(path,self.readonly_root)
        fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);self.stack.callback(os.close,fd)
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and not before.st_mode&0o022
            and (owner is None or before.st_uid==owner)
            and (mode is None or stat.S_IMODE(before.st_mode)==mode)
            and 0<before.st_size<=maximum,'file_metadata')
        raw=os.pread(fd,before.st_size+1,0);need(len(raw)==before.st_size,'file_bound')
        if pin is not None:need(sha256(raw).hexdigest()==pin,'file_pin')
        binding=SimpleNamespace(path=path,parents=ancestors,fd=fd,before=identity(before))
        def check():
            check_parents(binding.parents)
            need(binding.before==identity(os.fstat(fd))==identity(binding.path.lstat()),'file_changed')
        check();self.bindings.append(binding);self.checks.append(check);return raw
    def check(self):
        if self.readonly_root is not None:need(os.statvfs(self.readonly_root[0]).f_flag&os.ST_RDONLY,'image_readonly')
        for check in self.checks:check()
    def rebind_tree(self,old,new):
        for binding in self.bindings:
            if binding.path.is_relative_to(old):
                binding.path=new/binding.path.relative_to(old);binding.parents=parents(binding.path)
                need(binding.before==identity(os.fstat(binding.fd))==identity(binding.path.lstat()),'file_changed')
        for directory,check in self.directories:
            if directory==old:self.checks.remove(check)

def load_recipe(held):
    raw=held.read(ROOT/LOCK_NAME,65536,LOCK_SHA,owner=os.geteuid())
    lock=document(raw)
    need(type(lock) is dict and lock.get('schema')=='app_security_os_packages_lock/1'
        and len(lock.get('packages',[]))==15 and set(lock.get('artifacts',{}))==set(GROUPS),'lock_contract')
    spec=lock['source'];source=held.read(ROOT/spec['path'],MAX_SOURCE,spec['sha256'],owner=os.geteuid())
    need(spec['path']=='compose_security_os_candidate.py' and len(source)==spec['bytes'],'recipe_contract')
    module=ModuleType('_app_vendor_package_recipe');module.__file__=str(ROOT/spec['path'])
    exec(compile(source,module.__file__,'exec'),module.__dict__)
    rows=lock['packages'];need(rows==sorted(rows,key=lambda r:r['name']) and len({r['name'] for r in rows})==15,'lock_contract')
    need({r['name']:(r['version'],r['bytes'],r['sha256']) for r in rows}==module.PACKAGES,'recipe_contract')
    for group in GROUPS:
        row=lock['artifacts'][group]
        need((row['run_id'],row['artifact_id'],row['bytes'],row['sha256'],row['receipt_sha256'],set(row['packages']),row['members'])
            ==module.GROUPS[group],'recipe_contract')
    installer=module.INSTALLER.encode()
    need(len(installer)==lock['installer']['bytes'] and sha256(installer).hexdigest()==lock['installer']['sha256'],'installer_contract')
    manifest=module.encoded({'schema':lock['manifest']['schema'],'packages':rows})
    need(len(manifest)==lock['manifest']['bytes'] and sha256(manifest).hexdigest()==lock['manifest']['sha256'],'manifest_contract')
    held.check();return SimpleNamespace(module=module,lock=lock,manifest=manifest)

def write_new(path,raw,mode=0o644):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,mode)
    try:
        view=memoryview(raw)
        while view:
            count=os.write(fd,view);need(count>0,'io_refusal');view=view[count:]
        os.fchmod(fd,mode);os.fsync(fd)
    finally:os.close(fd)

def verify_cache(cache,recipe,held,owner=None):
    cache=canonical(cache);ancestors=parents(cache);before=cache.lstat()
    need(stat.S_ISDIR(before.st_mode) and stat.S_IMODE(before.st_mode)==0o755
        and (owner is None or before.st_uid==owner),'cache_metadata')
    rows=recipe.lock['packages'];names={r['path'] for r in rows}|{'manifest.json'}
    need({p.name for p in cache.iterdir()}==names,'cache_inventory')
    need(held.read(cache/'manifest.json',65536,recipe.lock['manifest']['sha256'],0o644,before.st_uid)==recipe.manifest,'manifest_contract')
    for row in rows:
        raw=held.read(cache/row['path'],MAX_PACKAGE,row['sha256'],0o644,before.st_uid)
        need(len(raw)==row['bytes'],'file_bound')
    def check():
        check_parents(ancestors);need(identity(before)==identity(cache.lstat())
            and {p.name for p in cache.iterdir()}==names,'cache_changed')
    check();held.checks.append(check);held.directories.append((cache,check));held.check()

class CacheLock:
    def __init__(self,cache,stack):
        self.parent=canonical(cache.parent);self.parents=parents(cache)
        self.fd=os.open(self.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC);stack.callback(os.close,self.fd)
        self.parent_id=identity(os.fstat(self.fd))[:5]
        name='.'+cache.name+'.lock'
        self.path=self.parent/name
        self.lock=os.open(name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC,0o600,dir_fd=self.fd)
        stack.callback(os.close,self.lock);self.lock_id=identity(os.fstat(self.lock))
        need(stat.S_ISREG(self.lock_id[2]) and self.lock_id[3]==os.geteuid() and self.lock_id[5:7]==(1,0)
            and stat.S_IMODE(self.lock_id[2])==0o600,'lock_metadata')
        self.check()
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in {errno.EAGAIN,errno.EWOULDBLOCK}:raise RuntimeError('cache_busy') from None
            raise
        self.check()
    def check(self):
        check_parents(self.parents)
        need(self.parent_id==identity(os.fstat(self.fd))[:5]==identity(self.parent.lstat())[:5]
            and self.lock_id==identity(os.fstat(self.lock))==identity(self.path.lstat()),'cache_changed')

def rename_noreplace(parent_fd,source,target):
    # Linux atomic no-clobber publication; os.rename can replace an empty dir.
    libc=ctypes.CDLL(None,use_errno=True);rename=libc.renameat2
    rename.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint);rename.restype=ctypes.c_int
    if rename(parent_fd,os.fsencode(source),parent_fd,os.fsencode(target),1):
        number=ctypes.get_errno()
        if number==errno.EEXIST:raise RuntimeError('cache_exists')
        raise OSError(number,'cache publication refused')

def download_artifact(recipe,group,path):
    spec=recipe.lock['artifacts'][group]
    # The sealed existing fetcher permits only official gh artifact GET, bounds
    # its output file before accumulation, and never imposes Go RLIMIT_AS.
    recipe.module.fetch_artifact(spec['artifact_id'],path,spec['bytes'],spec['sha256'])

def prepare_cache(cache_dir,artifacts=None,check_only=False):
    cache=Path(cache_dir).absolute();need(cache.name=='security-os-capsule','path')
    need(artifacts is None or set(artifacts)==set(GROUPS),'artifact_arguments')
    with ExitStack() as stack:
        held=Held(stack);recipe=load_recipe(held)
        if check_only:
            verify_cache(cache,recipe,held,os.geteuid());held.check()
            return {'status':'cache_verified','packages':15,'manifest_sha256':recipe.lock['manifest']['sha256'],'security_accepted':False}
        lock=CacheLock(cache,stack);held.checks.append(lock.check)
        if os.path.lexists(cache):
            verify_cache(cache,recipe,held,os.geteuid());held.check()
            return {'status':'cache_verified','packages':15,'manifest_sha256':recipe.lock['manifest']['sha256'],'security_accepted':False}
        stage=Path(tempfile.mkdtemp(prefix='.'+cache.name+'-',dir=cache.parent));stage_id=identity(stage.lstat())[:2]
        published=False
        try:
            with tempfile.TemporaryDirectory(prefix='.app-os-downloads-',dir=cache.parent) as temporary:
                for group in GROUPS:
                    spec=recipe.lock['artifacts'][group]
                    path=Path(artifacts[group]).absolute() if artifacts is not None else Path(temporary)/(group+'.zip')
                    if artifacts is None:download_artifact(recipe,group,path)
                    raw=held.read(path,spec['bytes'],spec['sha256'],0o600,os.geteuid())
                    need(len(raw)==spec['bytes'],'file_bound')
                    admitted=recipe.module.admit_packages(path,group,stage)
                    expected=[r for r in recipe.lock['packages'] if r['name'] in spec['packages']]
                    need(sorted(admitted['packages'],key=lambda r:r['name'])==expected,'recipe_contract')
                    held.check()
                write_new(stage/'manifest.json',recipe.manifest);stage.chmod(0o755)
                verify_cache(stage,recipe,held,os.geteuid())
                directory=os.open(stage,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
                try:os.fsync(directory)
                finally:os.close(directory)
                held.check();lock.check()
                need(identity(stage.lstat())[:2]==stage_id,'cache_changed')
                rename_noreplace(lock.fd,stage.name,cache.name);published=True;os.fsync(lock.fd)
                need(identity(cache.lstat())[:2]==stage_id,'publish_unknown')
                # Rebind only this owned staging namespace. Source/artifact/lock
                # checkers and every retained file FD survive publication.
                held.rebind_tree(stage,cache)
                verify_cache(cache,recipe,held,os.geteuid());held.check()
        finally:
            if not published and os.path.lexists(stage):
                primary=sys.exc_info()[0] is not None
                try:
                    need(identity(stage.lstat())[:2]==stage_id,'cache_changed');shutil.rmtree(stage)
                except BaseException:
                    if not primary:raise
        return {'status':'cache_prepared','packages':15,'manifest_sha256':recipe.lock['manifest']['sha256'],'security_accepted':False}

def image_boundary(cache):
    need(platform.machine() in {'x86_64','amd64'},'image_architecture')
    need(os.statvfs(cache).f_flag&os.ST_RDONLY,'image_readonly')
    need(set(os.listdir('/sys/class/net'))=={'lo'},'image_network')

def approved_installer_commands(paths):
    paths=tuple(paths)
    need(len(paths)==15 and len(set(paths))==15 and all(Path(p).parent==CAPSULE and Path(p).suffix=='.deb' for p in paths),'installer_contract')
    rows={('/usr/bin/dpkg-deb','-f',path,'Package','Version','Architecture') for path in paths}
    rows|={('/usr/bin/dpkg-query','-W','-f=${Package}\t${Version}\t${db:Status-Status}\n'),('/usr/bin/dpkg','--audit'),
        ('/usr/bin/apt-get','--simulate','--no-remove','--no-download','install',*paths),
        ('/usr/bin/apt-get','--yes','--no-remove','install',*paths)}
    return lambda argv:tuple(argv) in rows

def private_output():
    fd=os.memfd_create('app-vendor-private-output',os.MFD_CLOEXEC|os.MFD_ALLOW_SEALING)
    try:
        os.ftruncate(fd,MAX_INSTALL_OUTPUT+1)
        fcntl.fcntl(fd,fcntl.F_ADD_SEALS,fcntl.F_SEAL_GROW|fcntl.F_SEAL_SHRINK|fcntl.F_SEAL_SEAL)
        return fd
    except BaseException:
        os.close(fd);raise

def output_size(fd):
    before=os.fstat(fd)
    try:sealed=fcntl.fcntl(fd,fcntl.F_GET_SEALS)&fcntl.F_SEAL_GROW
    except OSError:sealed=False
    return os.lseek(fd,0,os.SEEK_CUR) if sealed and before.st_size==MAX_INSTALL_OUTPUT+1 else before.st_size

class InstallerCommands:
    """Child-local adapter: finite files, then size admission, then reads."""
    def __init__(self,allowed):self.allowed=allowed
    def run(self,argv,*,capture_output=False,check=False,timeout=None,text=False,encoding=None,errors=None,
            stdout=None,stderr=None,stdin=None,env=None):
        need(type(argv) in {list,tuple} and all(type(v) is str for v in argv) and self.allowed(argv),'installer_command')
        # Only the already authenticated 15-package install argv gets these
        # exact libacl1 doc includes; approved INSTALLER bytes remain unchanged.
        if tuple(argv[:4])==('/usr/bin/apt-get','--yes','--no-remove','install'):
            argv=[argv[0],*LIBACL_DOC_APT_OPTIONS,*argv[1:]]
        need((stdin is None or stdin==subprocess.DEVNULL) and not (capture_output and (stdout is not None or stderr is not None)),'installer_command')
        if capture_output:stdout=stderr=subprocess.PIPE
        timeout=min(INSTALL_COMMAND_SECONDS,timeout) if timeout is not None else INSTALL_COMMAND_SECONDS
        need(type(timeout) in {int,float} and 0<timeout<=INSTALL_COMMAND_SECONDS,'installer_command')
        with ExitStack() as stack:
            captures={};streams=[]
            for name,target,default in (('stdout',stdout,1),('stderr',stderr,2)):
                if target==subprocess.PIPE:
                    target=private_output();stack.callback(os.close,target);captures[name]=target
                elif target==subprocess.STDOUT and name=='stderr':target=streams[0]
                elif target is None:target=default
                elif hasattr(target,'fileno'):target=target.fileno()
                need(type(target) is int and target>=0 and stat.S_ISREG(os.fstat(target).st_mode),'installer_command')
                streams.append(target)
            def sizes():
                need(all(output_size(fd)<=MAX_INSTALL_OUTPUT for fd in set(streams)),'installer_output_bound')
            sizes();process=None
            try:
                process=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=streams[0],stderr=streams[1],env=env,close_fds=True)
                deadline=time.monotonic()+timeout
                while process.poll() is None:
                    sizes();need(time.monotonic()<deadline,'installer_timeout');time.sleep(0.01)
                sizes();code=process.wait();values={}
                for name,fd in captures.items():
                    size=output_size(fd);need(size<=MAX_INSTALL_OUTPUT,'installer_output_bound')
                    value=os.pread(fd,size,0);need(len(value)==size,'installer_output_bound')
                    values[name]=value.decode(encoding or 'utf-8',errors or 'strict').replace('\r\n','\n').replace('\r','\n') if text or encoding or errors else value
                result=subprocess.CompletedProcess(argv,code,values.get('stdout'),values.get('stderr'))
                if check:result.check_returncode()
                return result
            finally:
                primary=sys.exc_info()[0] is not None;cleanup=False
                if process is not None:
                    try:
                        if process.poll() is None:process.kill()
                    except BaseException:cleanup=True
                    try:process.wait(timeout=2)
                    except BaseException:cleanup=True
                if cleanup and not primary:raise RuntimeError('installer_cleanup')
    def check_output(self,argv,**kwargs):
        need('stdout' not in kwargs,'installer_command')
        return self.run(argv,stdout=subprocess.PIPE,check=True,**kwargs).stdout

def installer_limits():
    for kind,value in ((resource.RLIMIT_CORE,0),(resource.RLIMIT_FSIZE,INSTALL_FILE_BYTES),
            (resource.RLIMIT_CPU,INSTALL_CPU_SECONDS),(resource.RLIMIT_AS,INSTALL_ADDRESS_BYTES)):
        hard=resource.getrlimit(kind)[1];value=value if hard==resource.RLIM_INFINITY else min(value,hard)
        resource.setrlimit(kind,(value,value))

def reap_installer_descendants():
    # The isolated child is a Linux subreaper. Any orphan from an owned package
    # command is killed and reaped; an unexpected survivor refuses success.
    unexpected=False;deadline=time.monotonic()+2
    while True:
        while True:
            try:pid,_=os.waitpid(-1,os.WNOHANG)
            except ChildProcessError:pid=0
            if pid<=0:break
        fd=os.open('/proc/self/task/'+str(os.getpid())+'/children',os.O_RDONLY|os.O_CLOEXEC)
        try:raw=os.read(fd,4097)
        finally:os.close(fd)
        need(len(raw)<=4096,'installer_cleanup');children=raw.split();need(len(children)<=64,'installer_cleanup')
        if not children:break
        unexpected=True
        for child in children:
            try:os.kill(int(child),signal.SIGKILL)
            except ProcessLookupError:pass
        need(time.monotonic()<deadline,'installer_cleanup');time.sleep(0.01)
    need(not unexpected,'installer_cleanup')

def signal_owned_installer_child(pid,sig,group=False):
    # A PID/group can be signalled only while its actual child identity remains
    # unreaped. Interrupted waits that already reaped the child must not signal.
    need(type(pid) is int and pid>0 and sig in {signal.SIGTERM,signal.SIGKILL}
        and type(group) is bool,'installer_cleanup')
    try:os.waitid(os.P_PID,pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
    except ChildProcessError:return False
    try:
        if group:os.killpg(pid,sig)
        else:os.kill(pid,sig)
    except ProcessLookupError:pass
    return True

def execute_installer(raw,package_paths=()):
    # Only this fork patches subprocess. The parent keeps its libraries,
    # descriptors and limits; the approved INSTALLER bytes stay unchanged.
    private=private_output();read_fd=write_fd=None;pid=None;status=None
    try:
        read_fd,write_fd=os.pipe2(os.O_CLOEXEC)
        pid=os.fork()
        if pid==0:
            os.close(read_fd);code='installer_failed'
            try:
                os.setsid();installer_limits();os.dup2(private,1);os.dup2(private,2)
                need(ctypes.CDLL(None,use_errno=True).prctl(36,1,0,0,0)==0,'installer_cleanup')
                signal.signal(signal.SIGTERM,lambda *_:need(False,'installer_timeout'))
                commands=InstallerCommands(approved_installer_commands(package_paths) if package_paths else lambda _:False)
                subprocess.run=commands.run;subprocess.check_output=commands.check_output
                try:
                    exec(compile(raw,'<approved-app-vendor-installer>','exec'),{'__name__':'__main__'})
                    need(output_size(private)<=MAX_INSTALL_OUTPUT,'installer_output_bound');code='ok'
                finally:
                    active=sys.exc_info()[0] is not None
                    try:reap_installer_descendants()
                    except BaseException:
                        if not active:raise
            except BaseException as error:
                code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'installer_failed'
            try:os.write(write_fd,(code+'\n').encode())
            finally:os._exit(0 if code=='ok' else 1)
        os.close(write_fd);write_fd=None;deadline=time.monotonic()+INSTALL_WALL_SECONDS
        while True:
            child,observed=os.waitpid(pid,os.WNOHANG)
            if child:status=observed;break
            need(output_size(private)<=MAX_INSTALL_OUTPUT,'installer_output_bound')
            need(time.monotonic()<deadline,'installer_timeout');time.sleep(0.01)
        need(output_size(private)<=MAX_INSTALL_OUTPUT,'installer_output_bound')
        message=os.read(read_fd,129)
        need(len(message)<=128 and message.endswith(b'\n'),'installer_failed')
        code=message[:-1].decode('ascii');need(code=='ok' or code in CODES,'installer_failed')
        need(status==0 and code=='ok',code if code!='ok' else 'installer_failed')
    finally:
        primary=sys.exc_info()[0] is not None;cleanup=False
        if pid and pid>0 and status is None:
            # Successful completion already proved descendant closure inside
            # the isolated subreaper. A reaped successful child grants no PID
            # or process-group signalling authority to this parent.
            try:
                if not signal_owned_installer_child(pid,signal.SIGTERM):cleanup=True
            except BaseException:cleanup=True
            deadline=time.monotonic()+3
            while status is None and time.monotonic()<deadline:
                try:
                    child,observed=os.waitpid(pid,os.WNOHANG)
                    if child:status=observed
                except ChildProcessError:status=0;cleanup=True
                if status is None:time.sleep(0.01)
            if status is None:
                try:
                    if not signal_owned_installer_child(pid,signal.SIGKILL,group=True):cleanup=True
                except BaseException:cleanup=True
                try:
                    if not signal_owned_installer_child(pid,signal.SIGKILL):cleanup=True
                except BaseException:cleanup=True
                try:_,status=os.waitpid(pid,0)
                except ChildProcessError:cleanup=True
                except BaseException:cleanup=True
        for fd in (private,read_fd,write_fd):
            if fd is not None:
                try:os.close(fd)
                except OSError:cleanup=True
        if cleanup and not primary:raise RuntimeError('installer_cleanup')

def install_image_cache(cache_dir):
    need(os.geteuid()==0,'root_required')
    cache=Path(cache_dir);need(cache==CAPSULE and cache.is_absolute(),'image_cache_path');image_boundary(cache)
    with ExitStack() as stack:
        held=Held(stack,readonly_root=cache);recipe=load_recipe(held);verify_cache(cache,recipe,held)
        try:execute_installer(recipe.module.INSTALLER.encode(),tuple(str(cache/r['path']) for r in recipe.lock['packages']))
        finally:
            primary=sys.exc_info()[0] is not None
            try:held.check()
            except BaseException:
                if not primary:raise
    return {'status':'image_packages_installed','packages':15,'security_accepted':False}

def failure(error):
    code=error.args[0] if type(error) is RuntimeError and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'io_refusal' if isinstance(error,OSError) else 'unexpected_refusal'
    return {'status':'refused','code':code,'exception_class':type(error).__name__ if type(error) in {RuntimeError,OSError,PermissionError,FileNotFoundError,ValueError,TypeError,KeyError} else 'unclassified',
        'os_errno':errno.errorcode.get(error.errno,'unclassified_errno') if isinstance(error,OSError) and type(error.errno) is int else None,
        'security_accepted':False}

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cache-dir',type=Path,required=True)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--check',action='store_true');mode.add_argument('--install-image-cache',action='store_true')
    for name in GROUPS:parser.add_argument('--'+('acl-tar' if name=='acl-tar' else name)+'-zip',type=Path)
    parser.error=lambda _message:need(False,'artifact_arguments')
    try:
        args=parser.parse_args(argv)
        paths={name:getattr(args,name.replace('-','_')+'_zip') for name in GROUPS}
        need(all(paths.values()) or not any(paths.values()),'artifact_arguments')
        need(not (args.install_image_cache or args.check) or not any(paths.values()),'artifact_arguments')
        value=install_image_cache(args.cache_dir) if args.install_image_cache else prepare_cache(args.cache_dir,paths if all(paths.values()) else None,args.check)
    except SystemExit as error:
        if error.code==0:return 0  # Fixed public --help output only.
        print(encode(failure(RuntimeError('artifact_arguments'))).decode(),end='');return 1
    except BaseException as error:print(encode(failure(error)).decode(),end='');return 1
    print(encode(value).decode(),end='');return 0

if __name__=='__main__':raise SystemExit(main())
