"""UNARMED finite provider smoke for the exact public candidate image.

No work on import. The hosted bootstrap supplies the unchanged, hash-admitted
configure-recovery I/O helpers and owns the source writer/readback finalizers.
"""
from hashlib import sha256
from pathlib import Path
import argparse
import ctypes
import errno
import fcntl
import json
import os
import re
import select
import signal
import stat
import struct
import subprocess
import time

CONFIRM = 'RUN-ONLY-2389-COREUTILS-PROVIDER-OWNED-FIXTURES-20261029-01'
UID = 10001
TRACE_MAX = 256 * 1024
STDOUT_MAX = 16384
RECEIPT_MAX = 2 * 1024**2
IMAGE = 'sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434'
ACL = '/usr/lib/x86_64-linux-gnu/libacl.so.1.2.2400'
LIBC = '/usr/lib/x86_64-linux-gnu/libc.so.6'
AT_EMPTY_PATH = 0x1000
ACCESS, DEFAULT = 0x8000, 0x4000
TEXT = b'u::rwx,u:10002:r-x,g::r-x,m::r-x,o::---'
PAYLOAD = b'public provider capsule fixture\n'
XATTR = 'user.gold_provider_capsule'
XVALUE = b'public-owned-value'
PUBLIC_CODES = frozenset(('provider_unarmed','provider_entry','provider_uid','provider_contract','provider_scope',
    'provider_deadline','provider_alias','provider_environment','provider_trace_bound','provider_stdout_bound',
    'provider_trace_ascii','provider_trace_line','provider_trace_mapping','provider_trace_symbol','provider_cli_failed',
    'provider_child_timeout','provider_child_finality','provider_child_identity','provider_pipe_identity',
    'provider_fixture_fresh','provider_fixture_write','provider_fixture_readback','provider_fixture_finality',
    'provider_object_identity','provider_object_finality','provider_object_HELD','provider_acl_api','provider_acl_result','provider_xattr',
    'provider_mode','provider_exdev','provider_source_removed','provider_clock','provider_preload','provider_output_bound'))


def need(ok, code):
    if not ok:
        raise RuntimeError(code)


def frame(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def flags(fd):
    return fcntl.fcntl(fd, fcntl.F_GETFL), fcntl.fcntl(fd, fcntl.F_GETFD)


def closed(error):
    code = error.args[0] if type(error) is RuntimeError and len(error.args) == 1 else None
    if type(code) is not str: code=None
    return {'code': code if code in PUBLIC_CODES else 'provider_failure',
            'exception_class': type(error).__name__ if type(error).__name__ in
            {'RuntimeError','OSError','ValueError','TypeError','FileNotFoundError','PermissionError','TimeoutError'} else 'unclassified_exception',
            'os_errno': error.errno if isinstance(error,OSError) and type(error.errno) is int and 0 < error.errno <= 4095 else None}


def encoded(value, maximum=RECEIPT_MAX):
    parts=[]; count=0
    for part in json.JSONEncoder(sort_keys=True,separators=(',',':'),allow_nan=False).iterencode(value):
        raw=part.encode(); need(count+len(raw)+1 <= maximum,'provider_output_bound')
        count+=len(raw); parts.append(raw)
    return b''.join(parts)+b'\n'


def document(raw, maximum=16384):
    need(type(raw) is bytes and 0 < len(raw) <= maximum,'provider_contract')
    def pairs(rows):
        out={}
        for k,v in rows:
            need(k not in out,'provider_contract'); out[k]=v
        return out
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=lambda _:need(False,'provider_contract'))


def validate_contract(value):
    need(type(value) is dict and value.get('schema')=='current_coreutils_provider_capsule_contract/1'
         and value.get('image_id')==IMAGE and value.get('scope')=='candidate_CI_only'
         and value.get('security_accepted') is False and value.get('provider_accepted') is False,'provider_contract')
    rows=value.get('bodies')
    need(type(rows) is list and len(rows)==9 and len({r['path'] for r in rows})==9,'provider_contract')
    for r in rows:
        need(type(r) is dict and type(r.get('bytes')) is int and 0 < r['bytes'] <= 4*1024**2
             and type(r.get('sha256')) is str and re.fullmatch('[a-f0-9]{64}',r['sha256'])
             and r.get('mode') in (0o644,0o755) and type(r.get('symlink_aliases')) is list,'provider_contract')
    need({r['path'] for r in rows}=={'/usr/bin/cp','/usr/bin/install','/usr/bin/mv',ACL,LIBC,
        '/usr/lib/x86_64-linux-gnu/libattr.so.1.1.2502','/usr/lib/x86_64-linux-gnu/libselinux.so.1',
        '/usr/lib/x86_64-linux-gnu/libpcre2-8.so.0.14.0','/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'},'provider_contract')
    return {r['path']:r for r in rows}


def interpreter(raw,resolve):
    need(type(raw)is bytes and len(raw)>=64 and raw[:6]==b'\x7fELF\x02\x01','provider_contract')
    off=struct.unpack_from('<Q',raw,32)[0];size,count=struct.unpack_from('<HH',raw,54)
    need(size==56 and 0<count<=128 and off+size*count<=len(raw),'provider_contract')
    values=[]
    for i in range(count):
        p=struct.unpack_from('<IIQQQQQQ',raw,off+i*size)
        if p[0]==3:
            start,length=p[2],p[5];need(1<length<=256 and start+length<=len(raw),'provider_contract')
            data=raw[start:start+length];need(data.endswith(b'\0')and b'\0'not in data[:-1],'provider_contract')
            name=data[:-1].decode('ascii');need(name.startswith('/'),'provider_contract');values.append(name)
    need(len(values)==1,'provider_contract')
    target=resolve(values[0]);need(target=='/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2','provider_contract')
    return {'PT_INTERP':values[0],'resolved_interpreter':target}


def cli_environment():
    # CI-only eager relocation supplies complete imported-ABI attribution.
    # It does not change the library search route or claim function-call events.
    return {'PATH':'/usr/bin:/bin','LC_ALL':'C','LD_DEBUG':'libs,versions,bindings','LD_BIND_NOW':'1'}


def validate_cli_environment(value):
    need(type(value)is dict and value=={'PATH':'/usr/bin:/bin','LC_ALL':'C',
         'LD_DEBUG':'libs,versions,bindings','LD_BIND_NOW':'1'},'provider_environment')


def parse_loader_trace(raw, program, contract, resolve):
    """Actual glibc bindings only; a search candidate is never a selected map."""
    need(type(raw) is bytes and 0 < len(raw) <= TRACE_MAX,'provider_trace_bound')
    need(all(b in (9,10) or 32 <= b < 127 for b in raw),'provider_trace_ascii')
    rows=validate_contract(contract); bindings=[]; versions=[]; transitive=[]; pids=set(); initialized=[]; selected=set()
    rx=re.compile(r"binding file (.+?) \[(\d+)\] to (.+?) \[(\d+)\]: (?:normal|protected|direct) symbol `([^']+)'(?: \[([^\]]+)\])?")
    vr=re.compile(r"checking for version `([^']+)' in file (.+?) \[(\d+)\] required by file (.+?) \[(\d+)\]")
    lines=raw.decode('ascii').splitlines(); need(len(lines)<=16384,'provider_trace_line')
    for line in lines:
        if not line.strip(): continue
        m=re.fullmatch(r'\s*([1-9][0-9]{0,19}):\s?(.*)',line)
        need(m is not None,'provider_trace_line'); pids.add(m[1]); text=m[2].strip()
        if text.startswith('initialize program: '): initialized.append(text.removeprefix('initialize program: ').strip())
        b=rx.fullmatch(text)
        if b and b[2]=='0' and b[4]=='0' and b[1].startswith('/') and b[3].startswith('/'):
            src=resolve(b[1]); dst=resolve(b[3])
            need(src in rows and dst in rows,'provider_trace_mapping')
            if src in rows: selected.add(src)
            if dst in rows: selected.add(dst)
            if src==program and dst in rows:
                bindings.append({'from':src,'to':dst,'symbol':b[5],'version':b[6]})
            elif src in rows and dst in rows:
                transitive.append({'from':src,'to':dst,'symbol':b[5],'version':b[6]})
        v=vr.fullmatch(text)
        if v and v[3]=='0' and v[5]=='0' and v[2].startswith('/') and v[4].startswith('/'):
            src=resolve(v[4]); dst=resolve(v[2])
            if src in rows and dst in rows:
                selected.add(src); selected.add(dst)
                versions.append({'required_by':src,'provider':dst,'version':v[1]})
    need(len(pids)==1 and initialized==[program],'provider_trace_mapping')
    direct={ACL,'/usr/lib/x86_64-linux-gnu/libattr.so.1.1.2502',LIBC,'/usr/lib/x86_64-linux-gnu/libselinux.so.1'}
    need(direct <= {r['to'] for r in bindings},'provider_trace_mapping')
    required={('acl_set_file_at','ACL_1.3'),('acl_delete_def_file_at','ACL_1.3')}
    need(required <= {(r['symbol'],r['version']) for r in bindings if r['to']==ACL},'provider_trace_symbol')
    attr='/usr/lib/x86_64-linux-gnu/libattr.so.1.1.2502'
    need(any(r['symbol']=='attr_copy_fd' and r['version']=='ATTR_1.1' and r['to']==attr for r in bindings),'provider_trace_symbol')
    need(any(r['provider']==attr and r['version']=='ATTR_1.1' for r in versions),'provider_trace_symbol')
    need(any(r['provider']==ACL and r['version']=='ACL_1.3' for r in versions),'provider_trace_symbol')
    need({'/usr/lib/x86_64-linux-gnu/libpcre2-8.so.0.14.0',
          '/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2'} <= selected,'provider_trace_mapping')
    return {'actual_pid':int(next(iter(pids))),'program_initialized':program,'bindings':bindings,'version_requirements':versions,
            'transitive_bindings':transitive,'selected_known_body_paths':sorted(selected),
            'trace_bytes':len(raw),'trace_sha256':sha256(raw).hexdigest(),'complete_trace_ascii':raw.decode('ascii')}


def child_command(argv, guard, end, *, popen=subprocess.Popen):
    """One owned real CLI child, two physically checked pipes, no shell."""
    need(type(argv) is list and argv and argv[0] in ('/usr/bin/cp','/usr/bin/install','/usr/bin/mv'),'provider_scope')
    env=cli_environment();validate_cli_environment(env)
    child=None; poll=None; pipes=[]; buffers={'stdout':bytearray(),'stderr':bytearray()}; observed={'stdout':0,'stderr':0}
    failures=[]; primary=None; result=None; original_pid=None
    try:
        guard(); need(time.monotonic()<end,'provider_deadline')
        child=popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                    env=env,start_new_session=True,close_fds=True)
        # Publish both owned pipe prefixes before any fallible fileno/metadata
        # operation, so a failure inspecting stdout does not lose stderr.
        pipes=[{'name':n,'pipe':p,'fd':None,'before':None,'flags':None,'closed':False}
               for n,p in (('stdout',child.stdout),('stderr',child.stderr))]
        original_pid=child.pid; need(type(original_pid) is int and original_pid>0,'provider_child_identity')
        poll=select.poll()
        for row in pipes:
            name,pipe=row['name'],row['pipe'];fd=pipe.fileno();row['fd']=fd
            os.set_blocking(fd,False); before=frame(os.fstat(fd)); fl=flags(fd);row['before']=before;row['flags']=fl
            need(stat.S_ISFIFO(before[2]) and fl[0]&os.O_ACCMODE==os.O_RDONLY and fl[1]&fcntl.FD_CLOEXEC,'provider_pipe_identity')
            poll.register(fd,select.POLLIN|select.POLLHUP|select.POLLERR)
        pending={p['fd']:p for p in pipes}
        while pending:
            guard(); need(child.pid==original_pid and time.monotonic()<end,'provider_child_timeout')
            for fd,event in poll.poll(min(100,max(1,int((end-time.monotonic())*1000)))):
                need(fd in pending,'provider_pipe_identity'); row=pending[fd]
                name,pipe,original,before,fl=(row[k]for k in('name','pipe','fd','before','flags'))
                # Pipe timestamps legitimately change as its producer writes.
                # The original FD/object, stable inode/type/owner and flags do
                # not; file-source full9 checks remain unchanged elsewhere.
                need(pipe.fileno()==original and frame(os.fstat(original))[:6]==before[:6] and flags(original)==fl,'provider_pipe_identity')
                limit=STDOUT_MAX if name=='stdout' else TRACE_MAX
                block=os.read(original,min(65536,limit-len(buffers[name])+1)); observed[name]+=len(block)
                need(len(buffers[name])+len(block)<=limit,'provider_stdout_bound' if name=='stdout' else 'provider_trace_bound')
                guard()
                if not block: poll.unregister(original); del pending[original]
                else: buffers[name].extend(block)
        code=child.wait(timeout=max(.01,end-time.monotonic())); guard()
        need(type(code) is int and code==0,'provider_cli_failed')
        result={'argv':argv,'actual_returncode':code,'stdout':bytes(buffers['stdout']),'stderr':bytes(buffers['stderr']),
                'loader_environment':env}
    except BaseException as e: primary=e
    # The direct child is not signalled after reaping. There is no retry/adoption.
    if child is not None and child.returncode is None:
        try:
            need(child.pid==original_pid,'provider_child_identity')
            try: os.waitid(os.P_PID,original_pid,os.WEXITED|os.WNOHANG|os.WNOWAIT)
            except ChildProcessError: raise RuntimeError('provider_child_finality') from None
            try: os.killpg(original_pid,signal.SIGKILL)
            except ProcessLookupError: pass
            child.wait(timeout=10)
        except BaseException as e: failures.append(e)
    for row in pipes:
        name,pipe,fd,before,fl=(row[k]for k in('name','pipe','fd','before','flags'))
        try:
            # A still-uninspected sibling is nevertheless the original pipe
            # returned by this owned Popen, not an arbitrary adopted handle.
            need(getattr(child,name)is pipe,'provider_pipe_identity')
            if fd is None:fd=pipe.fileno();row['fd']=fd
            need(pipe.fileno()==fd,'provider_pipe_identity')
            if before is not None:need(frame(os.fstat(fd))[:6]==before[:6],'provider_pipe_identity')
            if fl is not None:need(flags(fd)==fl,'provider_pipe_identity')
            pipe.close()
            try: os.fstat(fd)
            except OSError as e: need(e.errno==errno.EBADF,'provider_child_finality')
            else: raise RuntimeError('provider_child_finality')
            row['closed']=True
        except BaseException as e: failures.append(e)
    try:
        guard()
        if child is not None:need(child.returncode is not None,'provider_child_finality')
    except BaseException as e: failures.append(e)
    if primary is not None or failures:
        error=failures[0] if failures else primary
        error.provider_child_evidence={'actual_pid':original_pid,'actual_returncode':None if child is None else child.returncode,
            'observed_bytes':observed,'stdout_prefix':bytes(buffers['stdout']).decode('ascii',errors='replace'),
            'stderr_prefix':bytes(buffers['stderr']).decode('ascii',errors='replace'),
            'primary':None if primary is None else closed(primary),'finalizers':[closed(e) for e in failures],
            'pipe_prefixes':[{'name':r['name'],'original_FD_captured':r['fd']is not None,'physically_closed':r['closed']}for r in pipes]}
        if error is not primary: raise error from primary
        raise error
    result['physical_child_and_pipes_finalized']=True
    return result


def fresh_file(M,path, raw=PAYLOAD):
    need(type(raw) is bytes and 0<len(raw)<=4096 and not os.path.lexists(path),'provider_fixture_fresh')
    state=M.State();primary=None;final=None;result=None
    try:
        result=M.fixed_write(state,path,raw,mode=0o600,maximum=4096)
    except BaseException as e: primary=e
    try:M.finish(state)
    except BaseException as e:final=e
    if primary is not None or final is not None:
        error=final if final is not None else primary;error.provider_fixture_state=state
        if error is not primary:raise error from primary
        raise error
    return {'bytes':result['bytes'],'sha256':result['sha256'],'original_writer_reader_finalizers_complete':all(r.closed for r in state.rows)}


class ObjectPrefix:
    """One short-lived mutable owned fixture handle; not an input-file loan."""
    def __init__(self,path,*,data=False):
        self.path=Path(path);self.fd=self.original_fd=None;self.before=self.fl=None
        self.open_flags=(os.O_RDONLY|os.O_NONBLOCK if data else os.O_PATH)|os.O_NOFOLLOW|os.O_CLOEXEC
        self.data=data;self.origin=None
        self.admitted=self.closed=self.attempted=False;self.errors=[]
    def acquire(self):
        try:
            self.fd=self.original_fd=os.open(self.path,self.open_flags)
            self.before=frame(os.fstat(self.original_fd));self.fl=flags(self.original_fd)
            self.origin=(self.path,self.original_fd,self.before,self.fl,self.open_flags,self.data)
            need(self.before==frame(os.stat(self.path,follow_symlinks=False))and self.before[3:5]==(UID,UID),'provider_object_identity')
            if self.data:need(self.before[5]==1,'provider_object_identity')
            need((stat.S_ISREG(self.before[2])if self.data else stat.S_ISREG(self.before[2])or stat.S_ISDIR(self.before[2]))
                 and self.fl[1]==fcntl.FD_CLOEXEC,'provider_object_identity')
            need((not self.fl[0]&os.O_PATH and self.fl[0]&os.O_ACCMODE==os.O_RDONLY)if self.data else self.fl[0]&os.O_PATH,'provider_object_identity')
            self.admitted=True
        except BaseException as e:self.errors.append(e);raise
    def check(self):
        need(self.admitted and self.fd==self.original_fd and not self.closed
             and(self.path,self.original_fd,self.before,self.fl,self.open_flags,self.data)==self.origin,'provider_object_HELD')
        now=frame(os.fstat(self.original_fd));named=frame(os.stat(self.path,follow_symlinks=False))
        need(now==named and now[:2]==self.before[:2]and stat.S_IFMT(now[2])==stat.S_IFMT(self.before[2])
             and now[3:6]==self.before[3:6]and flags(self.original_fd)==self.fl,'provider_object_identity')
    def close(self):
        need(not self.attempted,'provider_object_finality');self.attempted=True
        if self.original_fd is None:self.closed=True;return
        self.check();original=self.original_fd;os.close(original)
        try:os.fstat(original)
        except OSError as e:need(e.errno==errno.EBADF,'provider_object_finality')
        else:raise RuntimeError('provider_object_finality')
        self.fd=None;self.closed=True


class ACLFixture:
    def __init__(self, guard):
        guard(); self.guard=guard; self.owned=[]; self.lib=ctypes.CDLL(ACL,use_errno=True)
        for name,args,result in (
            ('acl_from_text',[ctypes.c_char_p],ctypes.c_void_p),('acl_free',[ctypes.c_void_p],ctypes.c_int),
            ('acl_set_file_at',[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_uint,ctypes.c_void_p],ctypes.c_int),
            ('acl_get_file_at',[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_uint],ctypes.c_void_p),
            ('acl_delete_def_file_at',[ctypes.c_int,ctypes.c_char_p,ctypes.c_int],ctypes.c_int),
            ('acl_to_text',[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ssize_t)],ctypes.c_void_p)):
            fn=getattr(self.lib,name); fn.argtypes=args; fn.restype=result
        guard()
    def held(self,path,function,*,data=False):
        self.guard();row=ObjectPrefix(path,data=data);self.owned.append(row);primary=None;value=None;error=None
        try:
            row.acquire();row.check();value=function(row.original_fd)
        except BaseException as e: primary=e
        try:
            # ACL setup intentionally changes mode/ctime; device/inode/type and
            # owned identity stay fixed. Each operation uses the original FD.
            row.close();self.guard()
        except BaseException as e: error=e
        if error is not None:
            error.provider_object_prefixes=self.owned;raise error from primary
        if primary is not None:
            primary.provider_object_prefixes=self.owned;raise primary
        return value
    def set(self,path,kind):
        def actual(fd):
            acl=self.lib.acl_from_text(TEXT); need(acl,'provider_acl_api')
            try: need(self.lib.acl_set_file_at(fd,b'',AT_EMPTY_PATH,kind,acl)==0,'provider_acl_api')
            finally: need(self.lib.acl_free(acl)==0,'provider_acl_api')
        self.held(path,actual)
    def text(self,path,kind):
        def actual(fd):
            acl=self.lib.acl_get_file_at(fd,b'',AT_EMPTY_PATH,kind); need(acl,'provider_acl_api')
            text=None
            try:
                size=ctypes.c_ssize_t(); text=self.lib.acl_to_text(acl,ctypes.byref(size))
                need(text and 0<=size.value<=4096,'provider_acl_result')
                return ctypes.string_at(text,size.value)
            finally:
                if text is not None: need(self.lib.acl_free(text)==0,'provider_acl_api')
                need(self.lib.acl_free(acl)==0,'provider_acl_api')
        return self.held(path,actual)


def fixture_tree(M,root, acl,writers):
    root.mkdir(mode=0o700); f=root/'item';writers.append(fresh_file(M,f))
    acl.set(root,ACCESS); acl.set(root,DEFAULT); acl.set(f,ACCESS)
    def xattr(fd):
        os.setxattr(fd,XATTR,XVALUE);need(os.getxattr(fd,XATTR)==XVALUE,'provider_xattr')
    acl.held(f,xattr,data=True)
    return {'access':acl.text(root,ACCESS),'default':acl.text(root,DEFAULT),'file':acl.text(f,ACCESS)}


def verify_tree(root, expected, acl):
    need(acl.text(root,ACCESS)==expected['access'] and acl.text(root,DEFAULT)==expected['default']
         and acl.text(root/'item',ACCESS)==expected['file'],'provider_acl_result')
    acl.held(root/'item',lambda fd:need(os.getxattr(fd,XATTR)==XVALUE and os.pread(fd,4096,0)==PAYLOAD,'provider_xattr'),data=True)


def execute(M,state,contract):
    need(os.getuid()==os.geteuid()==os.getgid()==os.getegid()==UID,'provider_uid')
    rows=validate_contract(contract); started=time.monotonic(); end=started+90; actor=os.getpid()
    def guard():
        need(os.getpid()==actor and time.monotonic()<end,'provider_deadline'); state.check()
    inputs=[];interpreters=[]
    for name,row in rows.items():
        loan=M.Loan(state,Path(name),(row['bytes'],row['sha256']),owners=(0,),modes=(row['mode'],),maximum=4*1024**2)
        inputs.append({'path':name,'bytes':row['bytes'],'sha256':row['sha256'],'original_full9':list(loan.before),
                       'original_reader_flags':list(loan.fl),'held_through_all_four_CLI_cases':True})
        if name in('/usr/bin/cp','/usr/bin/install','/usr/bin/mv'):
            interpreters.append({'program':name,**interpreter(loan.read(),lambda p:str(Path(p).resolve(strict=True)))})
        for alias in row['symlink_aliases']: M.NormalLink(state,Path(alias),Path(name))
    for alias,target in contract['directory_aliases'].items(): M.NormalLink(state,Path(alias),Path(target))
    need(not os.path.lexists('/etc/ld.so.preload'),'provider_preload')
    root=Path('/proof'); target=Path('/target')
    need(root.resolve(strict=True)==root and target.resolve(strict=True)==target
         and root.stat().st_uid==target.stat().st_uid==UID
         and stat.S_IMODE(root.stat().st_mode)==stat.S_IMODE(target.stat().st_mode)==0o700
         and root.stat().st_dev!=target.stat().st_dev,'provider_exdev')
    acl=ACLFixture(guard); results=[];writers=[]
    def cli(label,argv):
        result=child_command(argv,guard,min(end,time.monotonic()+20))
        trace=parse_loader_trace(result['stderr'],argv[0],contract,lambda p:str(Path(p).resolve(strict=True)))
        results.append({'case':label,'argv':argv,'actual_returncode':result['actual_returncode'],
                        'stdout_bytes':len(result['stdout']),'stdout_sha256':sha256(result['stdout']).hexdigest(),
                        'physical_child_and_pipes_finalized':result['physical_child_and_pipes_finalized'],
                        'loader_environment':result['loader_environment'],'loader':trace})
    src=root/'cp-src'; expected=fixture_tree(M,src,acl,writers)
    cli('cp_tree',['/usr/bin/cp','-a','--',str(src),str(target/'cp-dst')]); verify_tree(target/'cp-dst',expected,acl)
    writers.append(fresh_file(M,root/'install-src'))
    cli('install_mode',['/usr/bin/install','-m','0640','--',str(root/'install-src'),str(target/'install-dst')])
    need(stat.S_IMODE((target/'install-dst').stat().st_mode)==0o640,'provider_mode')
    need(acl.text(target/'install-dst',ACCESS)==b'user::rw-\ngroup::r--\nother::---\n','provider_acl_result')
    src=root/'mv-src'; expected=fixture_tree(M,src,acl,writers)
    cli('mv_EXDEV_tree',['/usr/bin/mv','--',str(src),str(target/'mv-dst')]); verify_tree(target/'mv-dst',expected,acl)
    need(not os.path.lexists(src),'provider_source_removed')
    parent=root/'parents'; expected=fixture_tree(M,parent,acl,writers); (target/'parents-out').mkdir(mode=0o700)
    cli('cp_parents',['/usr/bin/cp','--parents','--preserve=mode','--',str(parent/'item'),str(target/'parents-out')])
    restored=target/'parents-out'/str(parent).lstrip('/')
    need(acl.text(restored,ACCESS)==expected['access'] and acl.text(restored,DEFAULT)==expected['default'],'provider_acl_result')
    guard(); need(not os.path.lexists('/etc/ld.so.preload'),'provider_preload')
    need(len(writers)==4 and all(r['original_writer_reader_finalizers_complete']for r in writers)
         and all(r.closed for r in acl.owned),'provider_fixture_finality')
    return {'schema':'current_coreutils_provider_owned_smoke/1','image_id':IMAGE,'real_uid':UID,'actual_kernel':os.uname().release,
            'source_device':root.stat().st_dev,'destination_device':target.stat().st_dev,'different_tmpfs_devices_proven':True,
            'cases':results,'fixture_cases_passed':4,'fixture_writers':writers,'fixture_object_original_finalizers_complete':True,
            'original_body_readers':inputs,'program_interpreters':interpreters,
            'real_Coreutils_loader_bindings_observed':True,
            'loader_binding_scope':'CI-only eager relocations using LD_BIND_NOW=1; not function-call events or ordinary lazy timing',
            'ordinary_lazy_execution_timing_proven':False,
            'helper_ctypes_mappings_are_not_CLI_mapping_evidence':True,'source_provider_finality_pending_bootstrap':True,
            'candidate_CI_compatibility_only':True,'production_activation_performed':False,'security_accepted':False,'provider_accepted':False}


def main(argv=None,*,M=None,state=None,contract=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute-reviewed-provider-smoke',action='store_true'); p.add_argument('--confirm')
    args=p.parse_args(argv)
    if not args.execute_reviewed_provider_smoke:
        return {'status':'UNARMED','provider_operation_performed':False,'security_accepted':False}
    need(args.confirm==CONFIRM and M is not None and state is not None and contract is not None,'provider_entry')
    return execute(M,state,contract)


if __name__=='__main__':
    try: print(encoded(main()).decode(),end='')
    except BaseException as error:
        print(json.dumps({'status':'REFUSED_OR_FAILED','failure':closed(error),'security_accepted':False},sort_keys=True))
        raise SystemExit(1) from None
