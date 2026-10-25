"""One fresh five-role App activation. Default CHECK; no resume/retry/lease.

This new coordinator never invokes a historical C/S/R/COW/fresh-profile tool.
Actual host LOAD, fresh backup/restore and schema compatibility are mandatory.
"""
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse
import json
import os
import re
import select
import stat
import subprocess
import sys
import time

ROOTS = {'bot':Path('/root/trading-bot/trading_bot/.local/security-canonical-app-activation-20261025-01'),
         'web':Path('/srv/trading-bot/security-canonical-app-activation-20261025-01')}
SSH = ['/usr/bin/ssh','-F','/dev/null','-p','37067','-oBatchMode=yes','-oIdentitiesOnly=yes',
    '-oStrictHostKeyChecking=yes','-i','/root/.ssh/id_ed25519_iran','-oControlMaster=no','-S','none',
    '-oUpdateHostKeys=no','-oUserKnownHostsFile=/root/.ssh/known_hosts','-oForwardAgent=no',
    '-oClearAllForwardings=yes','-oConnectTimeout=15','root@65.109.220.59']
FRAME_CODES = {'ready','checked','begun','active','known_failure','rolled_back','final_verified',
    'terminal_prepared','closed','refused_or_unknown','recovery'}
C = S = None
PEER_INDEX = 0


def strict(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= 131072:raise RuntimeError('control_bound')
    def unique(rows):
        result = {}
        for key,value in rows:
            if key in result:raise RuntimeError('duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw,object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(RuntimeError('nonfinite_json')))


def cold(path,stack,pin=None,maximum=131072):
    proof = []
    for parent in path.parents:
        value = parent.lstat()
        if parent.resolve(strict=True) != parent or not stat.S_ISDIR(value.st_mode) or value.st_uid != 0 or value.st_gid != 0 or value.st_mode & 0o022:
            raise RuntimeError('source_parent')
        proof.append((parent,(value.st_dev,value.st_ino,value.st_mode,value.st_uid,value.st_gid)))
    fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC); stack.callback(os.close,fd)
    identity = lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    before = identity(os.fstat(fd))
    if before[2:6] != (0o100600,0,0,1) or not 0 < before[6] <= maximum:raise RuntimeError('source_identity')
    raw = os.pread(fd,before[6]+1,0)
    if len(raw) != before[6] or pin is not None and sha256(raw).hexdigest() != pin:raise RuntimeError('source_pin')
    def check():
        if before != identity(os.fstat(fd)) or before != identity(path.lstat()) or os.pread(fd,len(raw)+1,0) != raw:
            raise RuntimeError('source_changed')
        for parent,value in proof:
            actual = parent.lstat()
            if parent.resolve(strict=True) != parent or (actual.st_dev,actual.st_ino,actual.st_mode,actual.st_uid,actual.st_gid) != value:
                raise RuntimeError('source_parent_changed')
    def final():
        primary = sys.exc_info()[0] is not None
        try:check()
        except BaseException:
            if not primary:raise
    check(); stack.callback(final); return raw,check


def load(site,nonce,stack):
    global C,S
    if os.geteuid() != 0 or os.getegid() != 0 or not re.fullmatch('[a-f0-9]{32}',nonce):raise RuntimeError('root_nonce_required')
    root = ROOTS[site]; raw,check = cold(root/'controls'/(nonce+'.json'),stack)
    control = strict(raw); pins = control.get('source_files')
    if type(pins) is not dict or set(pins) != {'canonical_app_activation_contract.py','canonical_app_activation_site.py','canonical_app_activation_coordinator.py'}:
        raise RuntimeError('unarmed')
    sources = {}; checks = [check]
    for name,binding in pins.items():
        if type(binding) is not dict or set(binding) != {'bytes','sha256'}:raise RuntimeError('source_inventory')
        body,verify = cold(root/name,stack,binding['sha256']); checks.append(verify)
        if type(binding['bytes']) is not int or len(body) != binding['bytes']:raise RuntimeError('source_size')
        sources[name] = body
    if Path(__file__) != root/'canonical_app_activation_coordinator.py':raise RuntimeError('installed_source_path')
    C = ModuleType('_canonical_app_activation_contract'); C.__file__ = str(root/'canonical_app_activation_contract.py')
    exec(compile(sources['canonical_app_activation_contract.py'],C.__file__,'exec'),C.__dict__)
    C.validate_control(control,int(time.time()))
    S = ModuleType('_canonical_app_activation_site'); S.__file__ = str(root/'canonical_app_activation_site.py')
    exec(compile(sources['canonical_app_activation_site.py'],S.__file__,'exec'),S.__dict__); S.C = C
    return control,checks


def frame(control,index,status,service=None,container_id=None,terminal_sha256=None):
    C.require(status in FRAME_CODES,'frame_status')
    return {'schema':'canonical_app_activation_peer/1','operation':C.OPERATION,'control_sha256':C.control_binding(control),
        'nonce':control['nonce'],'index':index,'status':status,
        'service':service,'container_id':container_id,'terminal_sha256':terminal_sha256,'security_accepted':False}


def admit(value,control,index):
    C.require(type(value) is dict and set(value) == {'schema','operation','control_sha256','nonce','index','status','service','container_id','terminal_sha256','security_accepted'}
        and value['schema'] == 'canonical_app_activation_peer/1' and value['operation'] == C.OPERATION
        and value['control_sha256'] == C.control_binding(control) and value['nonce'] == control['nonce']
        and type(value['index']) is int and value['index'] == index and value['status'] in FRAME_CODES
        and value['security_accepted'] is False and (value['service'] is None or value['service'] in {'app','bot','sync_worker'})
        and (value['container_id'] is None or C.hex64(value['container_id'])),'peer_frame')
    C.require((C.hex64(value['terminal_sha256']) if value['status'] == 'terminal_prepared' else
        value['terminal_sha256'] is None or value['status'] == 'closed' and C.hex64(value['terminal_sha256'])),'peer_terminal')
    C.require((value['service'] is not None and C.hex64(value['container_id'])) if value['status'] in {'active','rolled_back','known_failure'}
        else value['container_id'] is value['service'] is None,'peer_container')
    if value['status'] == 'refused_or_unknown':raise S.Unknown('peer_refused_or_unknown')
    return value


def read(fd,deadline):
    raw = bytearray()
    while time.monotonic() < deadline:
        if not select.select([fd],[],[],max(0,deadline-time.monotonic()))[0]:break
        data = os.read(fd,1)
        if not data:raise S.Unknown('peer_closed')
        raw += data
        if len(raw) > 65536:raise S.Unknown('peer_output_bound')
        if data == b'\n':return C.document(bytes(raw),65536)
    raise S.Unknown('peer_timeout')


def write(fd,value,deadline):
    raw = C.encoded(value); C.require(len(raw) <= 4096,'peer_request_bound'); offset = 0
    while offset < len(raw) and time.monotonic() < deadline:
        if not select.select([],[fd],[],max(0,deadline-time.monotonic()))[1]:break
        count = os.write(fd,raw[offset:]); C.require(count > 0,'peer_write'); offset += count
    if offset != len(raw):raise S.Unknown('peer_write_unproven')


class Peer:
    def __init__(self,control,deadline):
        self.control,self.deadline,self.index = control,deadline,0
        # The remote bootstrap admits the exact held coordinator bytes BEFORE
        # compile. Root reviewed control is the authority for all three sources.
        path = str(ROOTS['web']/'canonical_app_activation_coordinator.py'); pin = control['source_files']['canonical_app_activation_coordinator.py']['sha256']
        bootstrap = ("import os,stat,hashlib; p="+repr(path)+"; f=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC); "
            "s=os.fstat(f); assert stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600 and 0<s.st_size<=131072; "
            "b=os.pread(f,s.st_size+1,0); assert len(b)==s.st_size and hashlib.sha256(b).hexdigest()=="+repr(pin)+"; "
            "ident=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_uid,v.st_gid,v.st_nlink,v.st_size,v.st_mtime_ns,v.st_ctime_ns); "
            "assert ident(os.fstat(f))==ident(s) and ident(os.stat(p,follow_symlinks=False))==ident(s); "
            "g={'__name__':'__main__','__file__':p}; exec(compile(b,p,'exec'),g)")
        import shlex
        unit = 'security-canonical-app-activation-'+control['nonce']
        remote = ['/usr/bin/systemd-run','--scope','--quiet','--collect','--unit='+unit,
            '-p','RuntimeMaxSec=7200s','-p','MemoryMax=768M','-p','TasksMax=64','-p','CPUQuota=100%',
            '/usr/bin/python3','-I','-B','-c',bootstrap,'--peer','--nonce',control['nonce']]
        self.child = subprocess.Popen([*SSH,shlex.join(remote)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,env=S.COMMAND_ENV,close_fds=True)
        try:self.receive('ready')
        except BaseException:
            # Constructor assignment has not returned to main. Close/reap our
            # local client here; this never proves remote cancellation.
            try:self.close_client(known=False)
            except BaseException:pass
            raise
    def receive(self,status):
        answer = admit(read(self.child.stdout.fileno(),self.deadline),self.control,self.index)
        C.require(answer['status'] == status,'peer_status'); return answer
    def request(self,action,service=None,native_terminal=None):
        self.index += 1
        write(self.child.stdin.fileno(),{'schema':'canonical_app_activation_request/1','operation':C.OPERATION,
            'control_sha256':C.control_binding(self.control),'nonce':self.control['nonce'],
            'index':self.index,'action':action,'service':service,'native_terminal_sha256':native_terminal},self.deadline)
        answer = admit(read(self.child.stdout.fileno(),self.deadline),self.control,self.index)
        C.require(answer['service'] == service,'peer_service')
        expected = {'check':'checked','begin':'begun','replace':'active','rollback':'rolled_back','final':'final_verified',
            'prepare-close':'terminal_prepared','close':'closed','end-check':'closed','recovery':'recovery'}[action]
        if answer['status'] == 'known_failure' and action == 'replace':raise S.KnownFailure('peer_measured_role_rejected')
        C.require(answer['status'] == expected,'peer_status')
        if action == 'prepare-close':self.terminal = answer['terminal_sha256']
        if action == 'close':
            C.require(answer['terminal_sha256'] == self.terminal,'peer_terminal')
            # The terminal ACK is followed by successful held-FD finalization
            # and peer exit BEFORE Native receives guard-closure authority.
            self.child.stdin.close()
            try:rc = self.child.wait(timeout=5)
            except subprocess.TimeoutExpired:raise S.Unknown('peer_finalization_unproven') from None
            C.require(rc == 0,'peer_finalization_unproven')
        return answer
    def close_client(self,known=False):
        failed = False
        try:self.child.stdin.close()
        except BaseException:failed = True
        try:
            if known:
                try:rc = self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:self.child.kill(); self.child.wait(timeout=5); failed = True
                else:failed = failed or rc != 0
            else:
                if self.child.poll() is None:self.child.kill()
                self.child.wait(timeout=5)
        except BaseException:failed = True
        finally:
            try:self.child.stdout.close()
            except BaseException:failed = True
        if failed and known:raise S.Unknown('peer_client_cleanup_unproven')


def sequence(backends):
    completed = []; current = None
    try:
        for site,service in C.ROLE_ORDER:
            current = (site,service); backends[site].replace(service); completed.append(current); current = None
        for backend in backends.values():backend.final()
    except S.KnownFailure:
        for backend in backends.values():backend.recovery()
        if current is not None:backends[current[0]].rollback(current[1])
        for site,service in reversed(completed):backends[site].rollback(service)
        return 'rolled_back'
    except BaseException:raise S.Unknown('activation_outcome_unproven') from None
    return 'activated'


class Remote:
    def __init__(self,peer):self.peer = peer
    def replace(self,service):return self.peer.request('replace',service)
    def rollback(self,service):return self.peer.request('rollback',service)
    def final(self):return self.peer.request('final')
    def recovery(self):
        self.peer.deadline = time.monotonic()+self.peer.control['recovery_deadline']-time.time()
        return self.peer.request('recovery')


def peer(control,checks,stack,apply):
    global PEER_INDEX
    site = S.Site('web',control,stack,apply=apply); index = 0; prepared = False
    emit = lambda value:write(1,value,site.deadline)
    emit(frame(control,index,'ready'))
    while True:
        wait_end = time.monotonic()+control['recovery_deadline']-time.time()
        request = read(0,wait_end)
        C.require(type(request) is dict and set(request) == {'schema','operation','control_sha256','nonce','index','action','service','native_terminal_sha256'}
            and request['schema'] == 'canonical_app_activation_request/1' and request['operation'] == C.OPERATION
            and request['control_sha256'] == C.control_binding(control) and request['nonce'] == control['nonce']
            and type(request['index']) is int and request['index'] == index+1,'peer_request')
        index += 1; PEER_INDEX = index; action,service = request['action'],request['service']
        C.require(action in {'check','begin','replace','rollback','final','prepare-close','close','end-check','recovery'}
            and (service in {'app','sync_worker'} if action in {'replace','rollback'} else service is None),'peer_request')
        C.require(C.hex64(request['native_terminal_sha256']) if action == 'close' else request['native_terminal_sha256'] is None,'peer_terminal')
        for check in checks:check()
        if action == 'end-check':
            C.require(site.guard is None,'guard_exists'); emit(frame(control,index,'closed')); return 0
        if action == 'recovery':site.recovery(); status = 'recovery'
        elif action == 'check':site.unchanged(); status = 'checked'
        elif action == 'begin':site.begin(); status = 'begun'
        elif action == 'replace':
            try:site.replace(service); status = 'active'
            except S.KnownFailure:status = 'known_failure'
        elif action == 'rollback':site.rollback(service); status = 'rolled_back'
        elif action == 'final':site.final(); status = 'final_verified'
        elif action == 'prepare-close':site.prepare_terminal(); prepared = True; status = 'terminal_prepared'
        else:
            C.require(prepared,'terminal_not_prepared')
            S.publish(site.journal.path/'native-terminal-ack.json',{'schema':'canonical_app_activation_terminal_ack/1',
                'nonce':control['nonce'],'native_terminal_sha256':request['native_terminal_sha256'],
                'web_terminal_sha256':site.terminal,'security_accepted':False})
            site.close(); status = 'closed'
        for check in checks:check()
        cid = site.role_ids[service] if service else None
        emit(frame(control,index,status,service,cid,site.terminal if status in {'terminal_prepared','closed'} else None))
        if action == 'close':return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nonce',required=True); parser.add_argument('--apply',action='store_true')
    parser.add_argument('--confirm'); parser.add_argument('--peer',action='store_true')
    args = parser.parse_args(argv); result = None; connection = None; known = False; control = None
    try:
        with ExitStack() as stack:
            control,checks = load('web' if args.peer else 'bot',args.nonce,stack)
            apply = args.apply or args.peer
            C.require(not args.apply or args.confirm == C.CONFIRM,'confirmation_required')
            if args.peer:return peer(control,checks,stack,apply)
            site = S.Site('bot',control,stack,apply=apply)
            connection = Peer(control,site.deadline); connection.request('check'); site.unchanged()
            if not apply:
                connection.request('end-check')
                result = {'schema':'canonical_app_activation_result/1','status':'checked_not_activated',
                    'nonce':control['nonce'],'security_accepted':False,'full_matrix_accepted':False}
            else:
                site.begin(); connection.request('begin')
                status = sequence({'web':Remote(connection),'bot':site})
                site.final(); connection.request('final')
                acknowledgment = connection.request('prepare-close'); terminal = site.prepare_terminal(status)
                S.publish(site.journal.path/'web-terminal-ack.json',acknowledgment)
                connection.request('close',native_terminal=terminal); site.close()
                result = {'schema':'canonical_app_activation_result/1','status':status,'nonce':control['nonce'],
                    'image_source_commit':C.IMAGE['source_commit'],'image_config_id':C.IMAGE['image_id'],
                    'security_accepted':False,'full_matrix_accepted':False}
                S.publish(site.journal.path/'result.json',result)
            for check in checks:check()
            known = True; connection.close_client(known=True); connection = None
        # Success is visible only after held sources/fences/FDs close successfully.
        print(json.dumps(result,sort_keys=True),flush=True); return 0
    except BaseException:
        if args.peer and C is not None and control is not None:
            try:write(1,frame(control,PEER_INDEX,'refused_or_unknown'),time.monotonic()+1)
            except BaseException:pass
        elif not args.peer:
            print(json.dumps({'schema':'canonical_app_activation_result/1','status':'refused_or_unknown',
                'retry_allowed':False,'daemon_cancellation_proven':False,'security_accepted':False,'full_matrix_accepted':False}),flush=True)
        return 1
    finally:
        if connection is not None:
            try:connection.close_client(known=False)
            except BaseException:pass


if __name__ == '__main__':raise SystemExit(main())
