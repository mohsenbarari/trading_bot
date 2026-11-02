"""Seal this public Git source cohort, then run the exact new2389 inventory and unchanged32 proofs.

Default unarmed. Genuine hosted UID1001 only; no host/runtime identity override. Default explicit-action flag is false; no action on import.
Image release and controller commits are separate immutable identities.
"""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,errno,fcntl,json,os,re,stat,sys,time

ROOT_PREFIX = 'security-app-ci-smoke-gold2-'
TRACKED=Path('deploy/maintenance/app_gold2_smoke')
REPOSITORY='mohsenbarari/trading_bot'
CONFIRM = 'RUN-ONLY-GOLD2-2389-FLAT-INVENTORY-AND-UNCHANGED32-20261029-04'
# Exact public source hashes are generated once before this source cohort freezes.
SOURCES = {'admit_security_component_image_reuse.py': (27518, '9adddae3d6966443f33040f54a57ce597a1816af918e8c606557801d27bf4025'), 'archive_inventory.py': (16581, '05780cb0566e7e9c77395eaef84c039190fa610befc46437ea1daf5204bddd23'), 'compose_app_mountpoint_image.py': (49042, '14849bd8c616b27ade1c8488b8e6f276b369480456d0acc1a8c8eb4284f40226'), 'compose_market_security_os_candidate.py': (52231, 'c53cfd7ae4570314ef7f13d43ecd73de26436cf56407676ac2fe24d19dc1caa9'), 'compose_security_os_candidate.py': (60894, '4156ad042885bc7104c5a9ce537193adf2211ade8447e77676ad29d520e7633d'), 'datastore_candidate_contract.py': (38383, '253f234f2ba68dea6929e16128d8dcb48021cda7bf71ffd004ecb8e709c575ee'), 'export_controller.py': (15009, '965c335b56b852ccb9d1aa512e0d19fb91d0198a431c0e685aaa8c4c037f380e'), 'inventory.py': (27509, 'e8171fb6d28a5b3e938e0136dab230c13a9e864f66aad4c2727878e22a3ea73f'), 'prepare_canonical_app_image.py': (33828, '403e95c4f59a4f22c6154f5ebddfd8d6f77b1b3a9c84fff4f7ab84a10ae698f3'), 'producer02_controller.py': (57553, '9de2b86dedbb773351b042bcd3177ebb41a88272cfd76e13f24de5ece6d49a25'), 'run_app_image_ci_smoke.py': (68163, 'b2c958060d176c0098b86a44d0dc225d4494023120b3253239bef9e2f3f2acb2'), 'runtime_image_archive_transport.py': (35148, '20c4b90f1c719d4cdee69df8ad1c31c2bcd663b99619b1d58dcbafa1eafecad2'), 'test_security_form_parser.py': (3910, 'de0090351837742ba6b0be747f0b586d4943590831215a4b4ea2619ebd5149fb'), 'test_stage6_otp_delivery_redis.py': (25543, 'b1e59ad7285a3c1f034aa269e024e091fe331cbdb446ac5e82f85de64530bd50'), 'test_telegram_otp_ephemeral_queue_redis.py': (8024, 'cbe02df0618239e4affa1f8e51b2e69cdfe13f332a44d46e8b4626b21d80e22e'), 'controller.py': (31794, '2cb6e1b8f075e03b7abdebad3b6b370401df2d85ba13690e3b00ae2c40d47f24'), 'intake.json': (5840, '9cf7ac4c0670b66797fa4961d391896ffdaa5ba41baf6538efdaf2421539767a'), 'source-manifest.json': (3594, '8db473c668f50a3ab4c8da6f19debd01e42ba886911a0e598c028faad1718e48')}
SOURCE_PATHS = {'admit_security_component_image_reuse.py': 'deploy/maintenance/app_r2_observe02/admit_security_component_image_reuse.py', 'archive_inventory.py': 'deploy/maintenance/app_r2_observe02/archive_inventory.py', 'compose_app_mountpoint_image.py': 'deploy/maintenance/app_r2_observe02/compose_app_mountpoint_image.py', 'compose_market_security_os_candidate.py': 'deploy/maintenance/app_r2_observe02/compose_market_security_os_candidate.py', 'compose_security_os_candidate.py': 'deploy/maintenance/app_r2_observe02/compose_security_os_candidate.py', 'datastore_candidate_contract.py': 'deploy/maintenance/app_r2_observe02/datastore_candidate_contract.py', 'export_controller.py': 'deploy/maintenance/app_r2_observe02/export_controller.py', 'inventory.py': 'deploy/maintenance/app_r2_observe02/inventory.py', 'prepare_canonical_app_image.py': 'scripts/prepare_canonical_app_image.py', 'producer02_controller.py': 'deploy/maintenance/app_r2_observe02/producer02_controller.py', 'run_app_image_ci_smoke.py': 'deploy/maintenance/app_r2_observe02/run_app_image_ci_smoke.py', 'runtime_image_archive_transport.py': 'deploy/maintenance/app_r2_observe02/runtime_image_archive_transport.py', 'test_security_form_parser.py': 'tests/test_security_form_parser.py', 'test_stage6_otp_delivery_redis.py': 'tests/test_stage6_otp_delivery_redis.py', 'test_telegram_otp_ephemeral_queue_redis.py': 'tests/test_telegram_otp_ephemeral_queue_redis.py', 'controller.py': 'deploy/maintenance/app_gold2_smoke/controller.py', 'intake.json': 'deploy/maintenance/app_gold2_smoke/intake.json', 'source-manifest.json': 'deploy/maintenance/app_gold2_smoke/source-manifest.json'}
SOURCES['export_controller_gold2.py']=(27863, '5480efd9fb0a5443e19262323821f917c57e2168f4aec6151bc44c27ecae95ad')
SOURCE_PATHS['export_controller_gold2.py']='deploy/maintenance/app_gold2_smoke/export_controller_gold2.py'
SOURCES['archive_inventory_gold2.py']=(33083, '04a58e38792d9c5e11ac3ee37db593b6a624b059f0378d0ff2bf6aa560dff005')
SOURCE_PATHS['archive_inventory_gold2.py']='deploy/maintenance/app_gold2_smoke/archive_inventory_gold2.py'
EXECUTION_ARMED=False  # CLI default only; explicit reviewed workflow action is required.
IMAGE_SOURCE_COMMIT='4df2fed29859c0b82367666521f1a345f269f58a'
IMAGE_ID='sha256:2389c900dbbf520bd7d7f723e8c48a5c258f1612c43e80d2b66c6d046f9f8434'
PHASE='unarmed'


def need(ok,code):
    if not ok: raise RuntimeError(code)


def ident(s):
    return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def encoded(value):
    return(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def parents(path,owner):
    result=[]
    for p in path.parents:
        frame=ident(p.lstat())
        need(p.resolve(strict=True)==p and stat.S_ISDIR(frame[2]) and frame[3] in {0,owner} and not frame[2]&0o022,'sealed_parent')
        result.append((p,frame[:5]))
    return result


class IOFailure(RuntimeError):
    def __init__(self,errors):
        super().__init__('sealed_io_finality');self.errors=tuple(errors)

def causes(error):return error.errors if type(error)is IOFailure else(error,)

def fail(errors):
    if len(errors)==1:raise errors[0]
    raise IOFailure(errors)from errors[0]

class MetadataPass:
    """One guard-only observation pass; never shared across payload operations.

    Names/parent facts are deduplicated within this pass only, then freshly
    rechecked at pass retirement. Original held FD/full9/flags are never cached.
    Every callback creates a new pass; no current pass is globally published.
    """
    def __init__(self,clocks):
        self.clocks=clocks;self.rows={};self.closed=False;self.clocks()
    def match(self,path,expected,code):
        need(not self.closed and type(expected)is tuple and len(expected)in(5,9),'sealed_changed')
        if path not in self.rows:
            need(len(self.rows)<512,'sealed_metadata')
            self.rows[path]=[path.resolve(strict=True),ident(path.lstat()),len(expected)]
        row=self.rows[path];row[2]=max(row[2],len(expected))
        need(row[0]==path and row[1][:len(expected)]==expected,code)
    def finish(self):
        need(not self.closed,'sealed_changed');self.closed=True;errors=[]
        try:
            try:self.clocks()
            except BaseException as error:errors.extend(causes(error))
            for path,(resolved,before,count)in self.rows.items():
                try:need(path.resolve(strict=True)==resolved==path and ident(path.lstat())[:count]==before[:count],'sealed_changed')
                except BaseException as error:errors.extend(causes(error))
            try:self.clocks()
            except BaseException as error:errors.extend(causes(error))
        finally:self.rows.clear()
        if errors:fail(errors)


def metadata_check(held,saved,keeper,clocks):
    observation=MetadataPass(clocks);errors=[]
    try:
        for item in held:
            if item.fd is None:item.named(observation)
            else:item.check(observation)
        for item in saved:
            if item.original_fd is not None:item.check(observation)
        if keeper is not None:keeper.check()
    except BaseException as error:errors.extend(causes(error))
    finally:
        try:observation.finish()
        except BaseException as error:errors.extend(causes(error))
    if errors:fail(errors)


class Held:
    def __init__(self,path,pin,*,owner,mode,maximum=131072,scope=lambda:None):
        self.path=path;self.fd=None;self.original_fd=None;self.flags=None;self.close_attempted=False;self.close_errors=[];self.io_errors=[];self.scope=scope
        self.parents=parents(path,owner);self.before=ident(path.lstat())
        need(path.resolve(strict=True)==path and self.before[2:6]==(stat.S_IFREG|mode,owner,os.getegid(),1)
            and type(maximum) is int and 0<self.before[6]<=maximum<=4*1024**2,'sealed_metadata')
        size=self.before[6]
        need(pin is None or type(pin)is tuple and len(pin)==2 and pin[0]==size,'sealed_pin')
        self.fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
        self.original_fd=self.fd;self.flags=(fcntl.fcntl(self.fd,fcntl.F_GETFL),fcntl.fcntl(self.fd,fcntl.F_GETFD));self.close_attempted=False
        need(self.flags[0]&os.O_ACCMODE==os.O_RDONLY and self.flags[1]&fcntl.FD_CLOEXEC,'sealed_flags')
        try:
            self.check();parts=[];count=0
            while True:
                part=self.read(min(65536,size-count+1))
                if not part:break
                count+=len(part);need(count<=size,'sealed_bound');parts.append(part)
            self.raw=b''.join(parts);self.binding=(count,sha256(self.raw).hexdigest())
            need(count==size and (pin is None or self.binding==pin),'sealed_pin');self.check()
        except BaseException:
            # The outer capture list already owns this constructor prefix.
            # It alone performs the one-shot physical finalizer.
            raise
    def named(self,observation=None):
        if observation is None:
            need(self.path.resolve(strict=True)==self.path and ident(self.path.lstat())==self.before
                and all(p.resolve(strict=True)==p and ident(p.lstat())[:5]==old for p,old in self.parents),'sealed_changed')
        else:
            observation.match(self.path,self.before,'sealed_changed')
            for p,old in self.parents:observation.match(p,old,'sealed_changed')
    def check(self,observation=None):
        self.named(observation);need(self.fd==self.original_fd and ident(os.fstat(self.original_fd))==self.before and(fcntl.fcntl(self.original_fd,fcntl.F_GETFL),fcntl.fcntl(self.original_fd,fcntl.F_GETFD))==self.flags,'sealed_changed')
    def read(self,size):
        if self.io_errors:fail(self.io_errors)
        errors=[];raw=None
        def guard():
            current=[]
            for function in(self.scope,self.check):
                try:function()
                except BaseException as error:current.extend(causes(error))
            if current:fail(current)
        try:guard();raw=os.read(self.original_fd,size)
        except BaseException as error:errors.extend(causes(error))
        finally:
            try:guard()
            except BaseException as error:errors.extend(causes(error))
        for error in errors:
            if all(error is not old for old in self.io_errors):self.io_errors.append(error)
        if errors:fail(errors)
        return raw
    def close(self):
        if self.fd is None:return
        need(not self.close_attempted,'sealed_close_unknown');self.close_attempted=True;errors=[]
        try:self.check()
        except BaseException as error:errors.append(error)
        try:
            os.close(self.original_fd)
            try:os.fstat(self.original_fd)
            except OSError as error:need(error.errno==errno.EBADF,'sealed_close_unknown')
            else:raise RuntimeError('sealed_close_unknown')
            self.fd=None
        except BaseException as error:errors.append(error)
        try:self.named()
        except BaseException as error:errors.append(error)
        self.close_errors=errors
        if errors:fail(errors)


PUBLIC_CLASSES=frozenset(('RuntimeError','Refusal','OSError','FileNotFoundError','PermissionError','TimeoutError','ValueError','TypeError','KeyError','FinalizationFailure','IOFailure'))
LAST_FAILURE=None

def closed_error(error):
    code=error.args[0]if isinstance(error,RuntimeError)and len(error.args)==1 and type(error.args[0])is str else None
    return {'code':code if code in CODES else'hosted_proof_unproven','exception_class':type(error).__name__ if type(error).__name__ in PUBLIC_CLASSES else'unclassified_exception','os_errno':error.errno if isinstance(error,OSError)and type(error.errno)is int and 0<error.errno<=4095 else None}

class FinalizationFailure(RuntimeError):
    def __init__(self,failures):
        super().__init__('sealed_finalization');self.failures=failures

def finalize(held,keeper=None,clocks=lambda:None,saved=()):
    failures=[]
    def attempt(stage,index,function):
        try:function()
        except BaseException as error:
            for cause in causes(error):failures.append({'stage':stage,'index':index,**closed_error(cause)})
    for index in reversed(range(len(held))):attempt('outer_source_close',index,held[index].close)
    for index in reversed(range(len(saved))):attempt('saved_source_close',index,saved[index].close)
    if keeper is not None:attempt('outer_keeper_close',0,keeper.close)
    for index,item in enumerate(held):attempt('outer_source_named',index,item.named)
    for index,item in enumerate(saved):attempt('saved_source_named',index,item.named)
    if keeper is not None:attempt('outer_keeper_named',0,keeper.named)
    attempt('outer_original_clock',0,clocks)
    if failures:raise FinalizationFailure(failures)

def closed_failure(error):
    if LAST_FAILURE is not None and LAST_FAILURE[0]is error:return LAST_FAILURE[1]
    return {'status':'REFUSED_OR_FAILED','schema':'gold2_hosted_outer_failure/2','phase':PHASE,**closed_error(error),'primary_failure':closed_error(error),'controller_failure':None,'physical_finalizer_failures':[],'outer_physical_finalizers_completed':False,'security_accepted':False}


def source_limit(name):
    return 2*1024**2 if name=='native-expectation.json' else 131072


class Saved:
    """Only the new18 source copies and final completion; no shared runtime API."""
    def __init__(self,path,raw,scope):
        self.path=Path(path);self.raw=raw;self.scope=scope;self.parents=None
        self.fd=None;self.original_fd=None;self.flags=None;self.before=None;self.created=None;self.count=0
        self.writer_attempted=False;self.writer_closed=False;self.reader_attempted=False
        self.reader_fd=None;self.original_reader=None;self.reader_flags=None;self.reader_before=None
        self.reader_close_attempted=False;self.reader_closed=False;self.errors=[]
    def note(self,error):
        for cause in causes(error):
            if all(cause is not old for old in self.errors):self.errors.append(cause)
    def named(self,observation=None):
        if observation is None:
            need(self.before is not None and self.path.resolve(strict=True)==self.path and ident(self.path.lstat())==self.before,'saved_changed')
            need(all(p.resolve(strict=True)==p and ident(p.lstat())[:5]==before for p,before in self.parents),'sealed_parent')
        else:
            need(self.before is not None,'saved_changed');observation.match(self.path,self.before,'saved_changed')
            for p,before in self.parents:observation.match(p,before,'sealed_parent')
    def check(self,observation=None):
        self.named(observation)
        if self.original_fd is not None and not self.writer_closed:
            need(self.fd==self.original_fd and ident(os.fstat(self.original_fd))==self.before and(fcntl.fcntl(self.original_fd,fcntl.F_GETFL),fcntl.fcntl(self.original_fd,fcntl.F_GETFD))==self.flags and os.lseek(self.original_fd,0,os.SEEK_CUR)==self.count,'saved_changed')
        if self.original_reader is not None and not self.reader_closed:
            need(self.reader_fd==self.original_reader and ident(os.fstat(self.original_reader))==self.reader_before==self.before and(fcntl.fcntl(self.original_reader,fcntl.F_GETFL),fcntl.fcntl(self.original_reader,fcntl.F_GETFD))==self.reader_flags,'saved_reader_changed')
        if self.writer_closed:need(self.original_reader is not None,'saved_reader_missing')
    def guard(self):
        errors=[]
        for function in(self.check,self.scope,self.check):
            try:function()
            except BaseException as error:self.note(error);errors.extend(causes(error))
        if errors:fail(errors)
    def call(self,function,*args):
        if self.errors:fail(self.errors)
        errors=[];value=None
        try:self.guard();value=function(*args)
        except BaseException as error:self.note(error);errors.extend(causes(error))
        finally:
            try:self.guard()
            except BaseException as error:self.note(error);errors.extend(causes(error))
        if errors:fail(errors)
        return value
    def acquire(self):
        self.scope();self.parents=parents(self.path,os.geteuid())
        self.fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600);self.original_fd=self.fd
        self.before=ident(os.fstat(self.original_fd));self.created=self.before[:6]
        self.flags=(fcntl.fcntl(self.original_fd,fcntl.F_GETFL),fcntl.fcntl(self.original_fd,fcntl.F_GETFD))
        need(self.before[2:7]==(0o100600,os.geteuid(),os.getegid(),1,0)and self.flags[0]&os.O_ACCMODE==os.O_RDWR and self.flags[1]&fcntl.FD_CLOEXEC,'saved_changed');self.guard()
    def write(self,raw):
        def actual():
            count=os.write(self.original_fd,raw);need(type(count)is int and 0<count<=len(raw),'sealed_write')
            self.count+=count;after=ident(os.fstat(self.original_fd))
            need(after[:6]==self.created and after[6]==self.count and ident(self.path.lstat())==after,'saved_changed')
            self.before=after;return count
        return self.call(actual)
    def retain_reader(self):
        need(not self.reader_attempted,'saved_reader_missing');self.reader_attempted=True
        # Even a failed-write cleanup publishes the actual RO acquisition before
        # checking it. Unexpected bytes/frame stay failed; they are never adopted.
        self.reader_fd=os.open(self.path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC);self.original_reader=self.reader_fd
        self.reader_before=ident(os.fstat(self.original_reader));self.reader_flags=(fcntl.fcntl(self.original_reader,fcntl.F_GETFL),fcntl.fcntl(self.original_reader,fcntl.F_GETFD))
        need(self.reader_flags[0]&os.O_ACCMODE==os.O_RDONLY and self.reader_flags[1]&fcntl.FD_CLOEXEC,'saved_reader_changed');self.guard()
    def finish(self):
        self.call(os.fsync,self.original_fd);self.retain_reader();digest=sha256()
        for offset in range(0,len(self.raw),65536):
            raw=self.call(os.pread,self.original_reader,min(65536,len(self.raw)-offset),offset)
            need(raw==self.raw[offset:offset+65536],'saved_readback');digest.update(raw)
        need(self.call(os.pread,self.original_reader,1,len(self.raw))==b''and self.count==len(self.raw)and digest.hexdigest()==sha256(self.raw).hexdigest(),'saved_readback')
    def close_writer(self):
        if self.original_fd is None or self.writer_closed:return
        need(not self.writer_attempted,'saved_close_unknown');self.writer_attempted=True;errors=[]
        try:self.guard()
        except BaseException as error:errors.extend(causes(error))
        try:
            os.close(self.original_fd)
            try:os.fstat(self.original_fd)
            except OSError as error:need(error.errno==errno.EBADF,'saved_close_unknown')
            else:raise RuntimeError('saved_close_unknown')
            self.fd=None;self.writer_closed=True
        except BaseException as error:self.note(error);errors.append(error)
        # The original RO reader is still held at this immediate postclose check.
        try:self.guard()
        except BaseException as error:errors.extend(causes(error))
        if errors:fail(errors)
    def close(self):
        errors=[]
        if self.original_fd is not None and not self.writer_closed:
            if not self.reader_attempted:
                try:self.retain_reader()
                except BaseException as error:self.note(error);errors.extend(causes(error))
            if not self.writer_attempted:
                try:self.close_writer()
                except BaseException as error:errors.extend(causes(error))
            else:errors.append(RuntimeError('saved_close_unknown'))
        if self.original_reader is not None and not self.reader_closed:
            if self.reader_close_attempted:errors.append(RuntimeError('saved_close_unknown'))
            else:
                self.reader_close_attempted=True
                try:self.guard()
                except BaseException as error:errors.extend(causes(error))
                try:
                    os.close(self.original_reader)
                    try:os.fstat(self.original_reader)
                    except OSError as error:need(error.errno==errno.EBADF,'saved_close_unknown')
                    else:raise RuntimeError('saved_close_unknown')
                    self.reader_fd=None;self.reader_closed=True
                except BaseException as error:self.note(error);errors.append(error)
        for function in(self.named,self.scope):
            try:function()
            except BaseException as error:self.note(error);errors.extend(causes(error))
        if errors:fail(errors)

def save(path,raw,maximum=131072,*,owned=None,check=lambda:None):
    need(type(maximum)is int and 0<maximum<=2*1024**2 and type(raw)is bytes and len(raw)<=maximum,'sealed_bound')
    item=Saved(path,raw,check)
    if owned is not None:owned.append(item)
    errors=[]
    try:
        item.acquire();offset=0
        while offset<len(raw):offset+=item.write(raw[offset:offset+65536])
        item.finish()
    except BaseException as error:item.note(error);errors.extend(causes(error))
    finally:
        if item.original_fd is not None:
            if not item.reader_attempted:
                try:item.retain_reader()
                except BaseException as error:item.note(error);errors.extend(causes(error))
            try:item.close_writer()
            except BaseException as error:item.note(error);errors.extend(causes(error))
        for function in(item.named,check):
            try:function()
            except BaseException as error:item.note(error);errors.extend(causes(error))
    if errors:
        for error in errors:error.saved_prefix=item
        fail(errors)
    return item

def completion_save(path,raw,held,keeper,saved,clocks,owned):
    """All sources are retired: only original named checks and clock are usable."""
    def retired():
        errors=[]
        functions=[item.named for item in held]+[item.named for item in saved]
        if keeper is not None:functions.append(keeper.named)
        functions.append(clocks)
        for function in functions:
            try:function()
            except BaseException as error:errors.extend(causes(error))
        if errors:fail(errors)
    errors=[]
    try:save(path,raw,maximum=2*1024**2,owned=owned,check=retired)
    except BaseException as error:errors.extend(causes(error))
    finally:
        for item in reversed(owned):
            try:item.close()
            except BaseException as error:errors.extend(causes(error))
        for function in([item.named for item in owned]+[retired]):
            try:function()
            except BaseException as error:errors.extend(causes(error))
    if errors:fail(errors)


def host(root):
    need(os.geteuid()==os.getuid()==1001 and os.environ.get('GITHUB_ACTIONS')=='true'
        and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted' and os.environ.get('GITHUB_REPOSITORY')==REPOSITORY
        and os.environ.get('GITHUB_REF')=='refs/heads/main','hosted_ci_only')
    commit=os.environ.get('GITHUB_SHA','');need(re.fullmatch('[a-f0-9]{40}',commit),'hosted_ci_only')
    run=os.environ.get('GITHUB_RUN_ID','');attempt=os.environ.get('GITHUB_RUN_ATTEMPT','')
    need(re.fullmatch('[1-9][0-9]{0,19}',run) and re.fullmatch('[1-9][0-9]{0,5}',attempt),'hosted_ci_only')
    parent=Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    need(root.is_absolute() and root.parent==parent and root.name==ROOT_PREFIX+run+'-'+attempt
        and not os.path.lexists(root) and not parent.stat().st_mode&0o022,'hosted_root')
    repo=Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    need(Path(__file__).absolute()==repo/TRACKED/'seal_and_run.py','hosted_source_path')
    return repo,commit


def run(args):
    global PHASE,LAST_FAILURE
    PHASE='unarmed';LAST_FAILURE=None
    need(args.execute_reviewed_gold2_proof is True and type(IMAGE_ID)is str and re.fullmatch('sha256:[a-f0-9]{64}',IMAGE_ID) and type(IMAGE_SOURCE_COMMIT)is str and re.fullmatch('[a-f0-9]{40}',IMAGE_SOURCE_COMMIT),'future_intake_unbound')
    started=time.monotonic();end=started+1800;original_clock=(started,end);actor=os.getpid()
    def clocks():need(os.getpid()==actor and time.monotonic()<original_clock[1],'original_deadline')
    root=Path(args.root);repo,commit=host(root);clocks();held=[];saved=[];completion=[];source_finished=False;finished=False;finalization_attempted=False;code=None;keeper=None;controller_failure=None
    need(type(args.source_sha256)is str and re.fullmatch('[a-f0-9]{64}',args.source_sha256),'sealed_pin')
    PHASE='source_admission'
    def capture(path,pin,**kwargs):
        item=Held.__new__(Held);held.append(item);item.__init__(path,pin,scope=check,**kwargs);return item
    def check():
        metadata_check(held,saved,keeper,clocks)
    try:
        own=capture(Path(__file__).absolute(),(Path(__file__).lstat().st_size,args.source_sha256),owner=1001,mode=0o644)
        # Source files must be genuinely tracked in THIS controller commit;
        # neither the image release commit nor a moving blob ref is substituted.
        root.mkdir(mode=0o700)
        for leaf in('source','reports','docker-config'):(root/leaf).mkdir(mode=0o700)
        output=Path(os.environ['GITHUB_OUTPUT'])
        with output.open('a') as stream:stream.write('root='+str(root)+'\n')
        for name,pin in SOURCES.items():
            item=capture(repo/SOURCE_PATHS[name],pin,owner=1001,mode=0o644,maximum=source_limit(name))
        backend=ModuleType('_gold2_source_backend')
        backend.__file__=str(repo/SOURCE_PATHS['compose_security_os_candidate.py'])
        raw=next(item.raw for item in held if item.path==repo/SOURCE_PATHS['compose_security_os_candidate.py'])
        exec(compile(raw,backend.__file__,'exec'),backend.__dict__)
        keeper=backend.ProofKeeper(root)
        env={'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null','GIT_OPTIONAL_LOCKS':'0','GIT_TERMINAL_PROMPT':'0','GIT_ALLOW_PROTOCOL':''}
        def git(*argv,maximum=131072):
            check()
            try:return backend.command(['/usr/bin/git','--no-optional-locks','-C',str(repo),'-c','core.fsmonitor=false',*argv],seconds=15,maximum=maximum,environment=env)
            finally:check()
        need(git('rev-parse','HEAD',maximum=64)==commit.encode()+b'\n','controller_git_identity')
        own_ref=commit+':'+str(TRACKED/'seal_and_run.py')
        need(git('cat-file','blob',own_ref,maximum=len(own.raw)+1)==own.raw,'controller_git_identity')
        records={}
        for name,pin in SOURCES.items():
            item=next(item for item in held if item.path==repo/SOURCE_PATHS[name]);ref=commit+':'+SOURCE_PATHS[name]
            need(git('cat-file','-t',ref,maximum=16)==b'blob\n'
                and git('cat-file','-s',ref,maximum=32)==str(pin[0]).encode()+b'\n'
                and git('cat-file','blob',ref,maximum=pin[0]+1)==item.raw,'controller_git_identity')
            save(root/'source'/name,item.raw,maximum=source_limit(name),owned=saved,check=check);capture(root/'source'/name,pin,owner=1001,mode=0o600,maximum=source_limit(name))
            records[name]={'bytes':pin[0],'sha256':pin[1],'git_path':SOURCE_PATHS[name]}
        keeper.write(root/'reports/hosted-controller-source.json',encoded({'schema':'gold2_hosted_controller_source/1','controller_commit':commit,
            'image_source_commit':IMAGE_SOURCE_COMMIT,'image_id':IMAGE_ID,'run_id':os.environ['GITHUB_RUN_ID'],
            'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'files':records,'historical_profiles_modified':False,'security_accepted':False}),0o600)
        PHASE='canonical_archive_and_public_inventory'
        controller=ModuleType('_gold2_controller');controller.__file__=str(root/'source/controller.py')
        script=next(item.raw for item in held if item.path==root/'source/controller.py')
        exec(compile(script,controller.__file__,'exec'),controller.__dict__)
        need(controller.CONFIRM==CONFIRM,'future_intake_unbound')
        code=controller.main(['--root',str(root),'--execute-reviewed-gold2-proof','--confirm',CONFIRM],outer_keeper=keeper,outer_check=check)
        controller_failure=controller.LAST_FAILURE_RECORD if type(code)is int and code!=0 else None
        need(type(code)is int and code==0,'controller_failed')
        # Archives and immutable reports were admitted at FIRST use/creation,
        # not captured after the controller's inner ExitStack already closed.
        report_pins=keeper.bindings()
        PHASE='outer_source_finalization';finalization_attempted=True;finalize(held,keeper,clocks,saved);source_finished=True
        PHASE='completion_publication'
        completion_save(root/'reports/hosted-completion.json',encoded({'schema':'gold2_hosted_controller_completion/1','controller_commit':commit,
            'controller_source_sha256':args.source_sha256,'image_id':IMAGE_ID,'image_source_commit':IMAGE_SOURCE_COMMIT,
            'controller_exit_code':code,'prior_source_physical_finalizers_complete':True,'completion_finality':'conditional_on_successful_final_exit','runtime_assertions_executed':32,'fresh_ELF_paths':835,'all_inventory_rows_retained':True,'source_flags_review_created':False,'retained_public_inputs_and_proofs':report_pins,
            'security_accepted':False,'full_matrix_accepted':False}),held,keeper,saved,clocks,completion)
        finished=True
        print(json.dumps({'status':'GOLD2_PROOF_COMPLETED_NOT_ACCEPTED','outer_physical_finalizers_completed':True,'runtime_assertions_executed':32,'fresh_ELF_paths':835,'security_accepted':False,'full_matrix_accepted':False}))
        return 0
    except BaseException as primary:
        failures=primary.failures if type(primary)is FinalizationFailure else[];failure=primary
        # Exactly one all-attempted finalization, even on an earlier primary.
        # A failed close is never blindly retried against a possibly reused FD.
        if not finalization_attempted:
            finalization_attempted=True
            try:finalize(held,keeper,clocks,saved);source_finished=True
            except FinalizationFailure as closing:failures=closing.failures;failure=closing
        record={'status':'REFUSED_OR_FAILED','schema':'gold2_hosted_outer_failure/2','phase':PHASE,**closed_error(failure),'primary_failure':None if type(primary)is FinalizationFailure else closed_error(primary),'controller_failure':controller_failure,'physical_finalizer_failures':failures,'outer_physical_finalizers_completed':finished and not failures,'prior_source_physical_finalizers_completed':source_finished,'new_io_failures':[closed_error(cause)for cause in causes(primary)],'security_accepted':False}
        LAST_FAILURE=(failure,record)
        if failure is not primary:raise failure from primary
        raise


def main(argv=None):
    global LAST_FAILURE,PHASE
    LAST_FAILURE=None;PHASE='unarmed'
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root');parser.add_argument('--source-sha256');parser.add_argument('--execute-reviewed-gold2-proof',action='store_true',default=EXECUTION_ARMED)
    args=parser.parse_args(argv)
    if not args.execute_reviewed_gold2_proof:
        print(json.dumps({'status':'UNARMED','hosted_operation_performed':False,'security_accepted':False}));return 0
    return run(args)


CODES=frozenset(('sealed_io_finality','saved_changed','saved_reader_changed','saved_reader_missing','saved_readback','saved_close_unknown','original_deadline','sealed_flags','sealed_close_unknown','future_intake_unbound','sealed_parent','sealed_metadata','sealed_bound','sealed_pin','sealed_changed','sealed_finalization','sealed_write',
    'hosted_ci_only','hosted_root','hosted_source_path','controller_git_identity','controller_failed',
    'proof_parent','proof_parent_changed','proof_bound','proof_write','proof_metadata','proof_changed','proof_root','proof_scope','proof_closed','proof_finalization'))
if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        print(json.dumps(closed_failure(error),sort_keys=True));raise SystemExit(1)from None
