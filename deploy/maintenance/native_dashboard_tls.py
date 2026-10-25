"""Fixed Native dashboard TLS transport; default check, explicit finite apply/rollback."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
from hashlib import sha256
import http.client
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import ssl
import stat
import subprocess
import sys
import time

OP='security-native-dashboard-tls-20261025-01'
HOST='coin.362514.ir'
ADDRESS='65.109.216.187'
UNIT='coin-rate-estimator-dashboard.service'
PROJECT=Path('/root/trading-bot/trading_bot')
LAUNCHER=PROJECT/'apps/coin_rate_estimator/run_live.sh'
SOURCE=LAUNCHER.with_name('live_server.py')
SOURCE_SHA='82fc2a7b742fbfa1147d997ca5914e740b58f68489e8bfd17bbb814e46ac86e8'
LAUNCHER_SHA='fbeb8309dc2d3cb188c4a6b4b94500f76ecb83a52767100c372e38be90c84be2'
CERT=Path('/etc/letsencrypt/live/coin.362514.ir/fullchain.pem')
MARKER=Path('/root/secure-envs/trading-bot/queue-cutover-artifacts/production-release.lock')
CONTROL=MARKER.parent.parent/'release-control'
SOURCE_FENCE=CONTROL.parent/'.production-runtime-source.lock'
RESERVATION=CONTROL/'private-primary-controller.lock'
NGINX=Path('/etc/nginx/conf.d/90-security-native-dashboard-tls-20261025.conf')
DROPIN=Path('/etc/systemd/system')/(UNIT+'.d')/'90-security-dashboard-loopback-20261025.conf'
DROPIN_BYTES=('[Service]\nExecStart=\nExecStart=/usr/bin/env COIN_RATE_ESTIMATOR_HOST=127.0.0.1 '
    'COIN_RATE_ESTIMATOR_PORT=30487 /usr/bin/bash '+str(LAUNCHER)+'\n').encode()
NGINX_BYTES=('server {\n    listen 30486 ssl;\n    server_name '+HOST+';\n'
    '    ssl_certificate '+str(CERT)+';\n'
    '    ssl_certificate_key /etc/letsencrypt/live/coin.362514.ir/privkey.pem;\n'
    '    ssl_protocols TLSv1.2 TLSv1.3;\n'
    '    error_page 497 =308 https://'+HOST+':30486$request_uri;\n'
    '    location / {\n        proxy_pass http://127.0.0.1:30487;\n'
    '        proxy_set_header Host $http_host;\n        proxy_set_header X-Forwarded-Proto https;\n'
    '        proxy_cookie_flags coin_session_token secure;\n'
    '        proxy_connect_timeout 5s;\n        proxy_read_timeout 30s;\n    }\n}\n').encode()
BODIES={DROPIN:DROPIN_BYTES,NGINX:NGINX_BYTES}


class Refusal(RuntimeError):pass
def require(value,code):
    if not value:raise Refusal(code)
def encoded(value):return (json.dumps(value,sort_keys=True)+'\n').encode()
def utc(value):
    require(type(value) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z',value),'deadline_shape')
    try:return datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise Refusal('deadline_shape') from None
def identity(s):return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def parents(path):
    for p in path.parents:
        s=p.lstat();require(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'unsafe_parent')


@contextmanager
def retained(path,maximum,*,mode=None):
    parents(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
            and not before.st_mode&0o022 and 0<before.st_size<=maximum
            and (mode is None or stat.S_IMODE(before.st_mode)==mode),'public_file_metadata')
        raw=os.read(fd,before.st_size+1);require(len(raw)==before.st_size and identity(before)==identity(path.lstat()),'public_file_changed')
        yield raw
        require(identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'public_file_changed')
    finally:os.close(fd)


def read(path,maximum,*,mode=None):
    with retained(path,maximum,mode=mode) as raw:return raw


def publish(path,raw,mode):
    parents(path);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,mode)
    try:
        offset=0
        while offset<len(raw):
            length=os.write(fd,raw[offset:]);require(length>0,'publication_short');offset+=length
        os.fchmod(fd,mode);os.fsync(fd);before=os.fstat(fd)
        require(identity(before)==identity(path.lstat()),'publication_changed')
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(directory)
        finally:os.close(directory)
        return list(identity(before))
    except Exception:
        # Clean only this syscall's fresh inode, including a partial write.
        # A replacement or foreign pathname is never removed.
        try:
            if identity(os.fstat(fd))==identity(path.lstat()):os.unlink(path)
        except OSError:pass
        raise
    finally:os.close(fd)


@contextmanager
def budget(seconds):
    require(seconds>0 and signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0),'timer_busy')
    previous=signal.getsignal(signal.SIGALRM)
    def expired(*args):raise Refusal('operation_deadline')
    signal.signal(signal.SIGALRM,expired);signal.setitimer(signal.ITIMER_REAL,seconds)
    try:yield
    finally:signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,previous)


def command(argv,*,seconds=10,maximum=65536,end=None):
    require(end is None or time.monotonic()<end,'operation_deadline')
    deadline=min(time.monotonic()+seconds,end) if end is not None else time.monotonic()+seconds
    child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        start_new_session=True,env={'PATH':'/usr/sbin:/usr/bin:/sbin:/bin','LC_ALL':'C'})
    out=bytearray();complete=False
    try:
        with selectors.DefaultSelector() as select:
            select.register(child.stdout,selectors.EVENT_READ)
            while select.get_map():
                require(time.monotonic()<deadline,'command_timeout')
                for key,_ in select.select(.1):
                    block=os.read(key.fileobj.fileno(),min(65536,maximum+1-len(out)))
                    if not block:select.unregister(key.fileobj)
                    else:out.extend(block);require(len(out)<=maximum,'command_bound')
        require(child.wait(timeout=max(.01,deadline-time.monotonic()))==0,'command_failed');complete=True;return bytes(out)
    finally:
        if not complete or child.poll() is None:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
        try:child.wait(timeout=5)
        finally:child.stdout.close()


def service(end):
    names=('LoadState','ActiveState','SubState','MainPID','ExecMainStartTimestampMonotonic','WorkingDirectory',
        'User','FragmentPath','NeedDaemonReload','Job')
    raw=command(['/usr/bin/systemctl','show',UNIT,*['--property='+n for n in names]],end=end)
    value=dict(row.split('=',1) for row in raw.decode().splitlines() if '=' in row)
    require(set(value)==set(names) and value['LoadState']=='loaded' and value['ActiveState']=='active'
        and value['SubState']=='running' and value['User']=='root' and value['WorkingDirectory']==str(PROJECT)
        and value['FragmentPath']=='/etc/systemd/system/'+UNIT and value['NeedDaemonReload']=='no'
        and value['Job'] in ('','0') and value['MainPID'].isdigit() and int(value['MainPID'])>1,'dashboard_service_contract')
    return value


def launch_projection(end,*,tls):
    # Consume only this named public launch property in RAM; never print/save
    # arbitrary arguments and never request Environment or process cmdline.
    raw=command(['/usr/bin/systemctl','show',UNIT,'--property=ExecStart','--value'],end=end).decode().strip()
    path='/usr/bin/env' if tls else '/usr/bin/bash'
    argv=('/usr/bin/env COIN_RATE_ESTIMATOR_HOST=127.0.0.1 COIN_RATE_ESTIMATOR_PORT=30487 /usr/bin/bash '+str(LAUNCHER)
          if tls else '/usr/bin/bash '+str(LAUNCHER))
    match=re.fullmatch(r'\{ path=([^ ;]+) ; argv\[\]=([^;]+) ; .*\}',raw)
    require(match is not None and match[1]==path and match[2].strip()==argv,'dashboard_launch_contract')


def listeners(port,end):
    raw=command(['/usr/bin/ss','-H','-ltnp','sport = :'+str(port)],end=end)
    rows=[]
    for line in raw.decode().splitlines():
        parts=line.split();require(len(parts)>=5,'listener_shape')
        rows.append({'local':parts[3],'pids':[int(v) for v in re.findall(r'pid=([0-9]+),',line)]})
    return rows


def certificate(end):
    raw=command(['/usr/bin/openssl','x509','-in',str(CERT),'-noout','-ext','subjectAltName','-dates'],end=end).decode()
    require(HOST in re.findall(r'DNS:([^,\s]+)',raw),'certificate_hostname')
    start=re.search(r'^notBefore=(.+)$',raw,re.M);expiry=re.search(r'^notAfter=(.+)$',raw,re.M)
    require(start is not None and expiry is not None,'certificate_dates')
    try:
        lo=datetime.strptime(start[1],'%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc)
        hi=datetime.strptime(expiry[1],'%b %d %H:%M:%S %Y GMT').replace(tzinfo=timezone.utc)
    except ValueError:raise Refusal('certificate_dates') from None
    require(lo<=datetime.now(timezone.utc)<hi and (hi-datetime.now(timezone.utc)).total_seconds()>86400,'certificate_expired')
    addresses={r[4][0] for r in socket.getaddrinfo(HOST,30486,type=socket.SOCK_STREAM)}
    require(addresses=={ADDRESS},'hostname_address_changed')
    return {'hostname':HOST,'not_before':lo.isoformat(),'not_after':hi.isoformat(),'address':ADDRESS}


def head(*,tls,end):
    require(time.monotonic()<end,'operation_deadline')
    connection=(http.client.HTTPSConnection(HOST,30486,timeout=min(5,end-time.monotonic()),context=ssl.create_default_context())
        if tls else http.client.HTTPConnection('127.0.0.1',30486,timeout=min(5,end-time.monotonic())))
    try:
        connection.request('HEAD','/');response=connection.getresponse()
        require(response.status==(501 if tls else 308),'http_head_status')
        if not tls:require(response.getheader('Location')=='https://'+HOST+':30486/','https_redirect_changed')
    finally:connection.close()


def source_check():
    # Only known public source files; private runtime files never enter this tool.
    require(sha256(read(SOURCE,2*1024**2)).hexdigest()==SOURCE_SHA
        and sha256(read(LAUNCHER,16384)).hexdigest()==LAUNCHER_SHA,'dashboard_public_source_changed')


def state_path(nonce):return CONTROL/(OP+'-'+nonce+'.json')


def state_write(path,value):
    raw=encoded(value)
    if not path.exists():publish(path,raw,0o600)
    else:
        old=json.loads(read(path,131072,mode=0o600));require(old['nonce']==value['nonce'] and old['operation_id']==OP,'state_owner')
        temporary=path.with_name(path.name+'.new');publish(temporary,raw,0o600);os.replace(temporary,path)
        fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(fd)
        finally:os.close(fd)


@contextmanager
def marker_lock():
    parents(MARKER);fd=os.open(MARKER,os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);require(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
            and stat.S_IMODE(before.st_mode)==0o600 and 0<before.st_size<=16384,'marker_metadata')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise Refusal('marker_busy') from None
        def recheck():
            require(identity(before)==identity(os.fstat(fd))==identity(MARKER.lstat()),'marker_changed')
            require(not os.path.lexists(SOURCE_FENCE) and not os.path.lexists(RESERVATION),'other_controller_present')
        recheck();yield recheck;recheck()
    finally:os.close(fd)


def readiness(end,*,tls=False):
    source_check();snapshot=service(end);launch_projection(end,tls=tls);pid=int(snapshot['MainPID'])
    rows=listeners(30487 if tls else 30486,end)
    require(rows,'dashboard_listener_pending')
    require(len(rows)==1 and rows[0]['local']==('127.0.0.1:30487' if tls else '0.0.0.0:30486')
        and rows[0]['pids']==[pid],'dashboard_listener_changed')
    require(tls or not listeners(30487,end),'backend_port_in_use')
    cert=certificate(end);command(['/usr/sbin/nginx','-t'],end=end)
    if tls:
        front=listeners(30486,end);require(front,'tls_front_pending')
        require(all(pid not in r['pids'] for r in front),'tls_front_missing')
        head(tls=True,end=end);head(tls=False,end=end)
    return {'main_pid':pid,'start_monotonic':snapshot['ExecMainStartTimestampMonotonic'],
        'certificate':cert,'public_source_sha256':SOURCE_SHA,'public_launcher_sha256':LAUNCHER_SHA,
        'tls_local_head_verified':tls,'external_peer_tls_verified':False,'response_body_read':False}


def wait_readiness(end,*,tls=False):
    """Allow only the empty-listener interval after an owned service restart.

    Type=simple may report active before Python has bound its socket. Foreign
    listeners, launch/configuration drift and certificate errors remain terminal.
    """
    until=min(end,time.monotonic()+45)
    while True:
        try:return readiness(until,tls=tls)
        except Refusal as error:
            if str(error) not in {'dashboard_listener_pending','tls_front_pending'} or time.monotonic()>=until:
                raise
            time.sleep(.25)


def owned_remove(path,row):
    require(path in BODIES and type(row) is dict and set(row)=={'identity','sha256'}
        and type(row.get('identity')) is list and len(row['identity'])==9
        and all(type(v) is int for v in row['identity'])
        and row['sha256']==sha256(BODIES[path]).hexdigest(),'owned_file_changed')
    parents(path);fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);require(list(identity(before))==row['identity'] and stat.S_ISREG(before.st_mode)
            and before.st_uid==0 and before.st_nlink==1 and stat.S_IMODE(before.st_mode)==0o644
            and 0<before.st_size<=65536,'owned_file_changed')
        raw=os.read(fd,before.st_size+1)
        require(len(raw)==before.st_size and sha256(raw).hexdigest()==row['sha256']
            and identity(before)==identity(os.fstat(fd))==identity(path.lstat()),'owned_file_changed')
        os.unlink(path);require(os.fstat(fd).st_nlink==0,'owned_file_changed')
    finally:os.close(fd)
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def owned_check(path,row):
    require(path in BODIES and type(row) is dict and set(row)=={'identity','sha256'}
        and type(row.get('identity')) is list and len(row['identity'])==9
        and all(type(v) is int for v in row['identity'])
        and row['sha256']==sha256(BODIES[path]).hexdigest(),'owned_file_changed')
    with retained(path,65536,mode=0o644) as raw:
        require(list(identity(path.lstat()))==row['identity'] and sha256(raw).hexdigest()==row['sha256'],'owned_file_changed')


def rollback(record,path,end,recheck):
    require(record.get('operation_id')==OP and record.get('schema')=='native_dashboard_tls/1'
        and record.get('source_sha256')==SOURCE_SHA and record.get('launcher_sha256')==LAUNCHER_SHA
        and type(record.get('files')) is dict and set(record['files'])<={str(p) for p in BODIES}
        and record.get('status') in ('intent','restarting_backend','loading_tls','tls_local_verified_external_peer_pending',
            'forward_failed','manual_recovery_required'),'rollback_state')
    journal_failed=False;had_files=bool(record['files'])
    # Nginx must first stop owning the front port before the original server
    # can bind it again. Only the two exact owned public files can be removed.
    for target in (NGINX,DROPIN):
        if str(target) in record['files']:
            recheck();owned_remove(target,record['files'][str(target)])
            del record['files'][str(target)]
            try:state_write(path,record)
            except OSError:journal_failed=True
            if target==NGINX:
                command(['/usr/sbin/nginx','-t'],end=end);command(['/usr/bin/systemctl','reload','nginx'],seconds=30,end=end)
    recheck()
    if had_files:command(['/usr/bin/systemctl','daemon-reload'],end=end)
    if record.get('backend_restart_requested') is True:command(['/usr/bin/systemctl','restart',UNIT],seconds=45,end=end)
    record['rollback_readiness']=wait_readiness(end)
    record['status']='rolled_back';record['security_accepted']=False;state_write(path,record)
    require(not journal_failed,'rollback_journal_failed')


def execute(action,nonce,deadline,machine_id):
    require(os.geteuid()==0,'root_required');os.umask(0o077)
    require(action in ('check','apply','rollback'),'action')
    actual_machine=read(Path('/etc/machine-id'),128).decode().strip()
    require(re.fullmatch('[a-f0-9]{32}',actual_machine) is not None,'machine_identity')
    if machine_id:require(machine_id==actual_machine,'machine_identity')
    if action=='check':
        require(not os.path.lexists(DROPIN) and not os.path.lexists(NGINX),'preexisting_public_file')
        with budget(60):observation=readiness(time.monotonic()+60)
        return {'status':'checked','machine_id':actual_machine,'observation':observation,'security_accepted':False}
    require(machine_id==actual_machine and type(nonce) is str and re.fullmatch('[a-f0-9]{32}',nonce),'fresh_identity_required')
    remaining=(utc(deadline)-datetime.now(timezone.utc)).total_seconds()
    require(120<=remaining<=1800,'fresh_window_required');end=time.monotonic()+min(remaining,300)
    path=state_path(nonce)
    with marker_lock() as recheck:
        if action=='rollback':
            record=json.loads(read(path,131072,mode=0o600));require(record['nonce']==nonce and record['machine_id']==machine_id,'state_owner')
            with budget(min(remaining,300)):rollback(record,path,end,recheck)
            return {'status':'rolled_back','security_accepted':False}
        require(not os.path.lexists(path) and not os.path.lexists(DROPIN) and not os.path.lexists(NGINX),'fresh_operation_required')
        with budget(min(remaining,300)):observation=readiness(end)
        record={'schema':'native_dashboard_tls/1','operation_id':OP,'nonce':nonce,'machine_id':machine_id,'deadline_utc':deadline,
            'status':'intent','files':{},'before':observation,'source_sha256':SOURCE_SHA,'launcher_sha256':LAUNCHER_SHA,
            'runtime_env_file_read_or_changed':False,'private_key_diagnostically_read':False,
            'daemon_cancellation_proven':False,'security_accepted':False}
        state_write(path,record)
        try:
            with budget(max(.01,end-time.monotonic())):
                for target in (DROPIN,NGINX):
                    recheck()
                    if not target.parent.exists():parents(target.parent);target.parent.mkdir(mode=0o755);target.parent.chmod(0o755)
                    row={'identity':publish(target,BODIES[target],0o644),'sha256':sha256(BODIES[target]).hexdigest()}
                    record['files'][str(target)]=row;state_write(path,record)
                # Validate both published configs before interrupting the old
                # listener. nginx -t checks syntax/certificates without binding.
                recheck();command(['/usr/sbin/nginx','-t'],end=end)
                record['status']='restarting_backend';state_write(path,record)
                command(['/usr/bin/systemctl','daemon-reload'],end=end)
                record['backend_restart_requested']=True;state_write(path,record)
                command(['/usr/bin/systemctl','restart',UNIT],seconds=45,end=end)
                recheck();record['status']='loading_tls';state_write(path,record)
                command(['/usr/bin/systemctl','reload','nginx'],seconds=30,end=end)
                record['after']=wait_readiness(end,tls=True);recheck()
                for target in BODIES:owned_check(target,record['files'][str(target)])
                record['status']='tls_local_verified_external_peer_pending';state_write(path,record)
            return {'status':record['status'],'nonce':nonce,'security_accepted':False,'external_peer_tls_verified':False}
        except Exception:
            record['status']='forward_failed'
            try:state_write(path,record)
            except OSError:pass
            try:
                with budget(120):rollback(record,path,time.monotonic()+120,recheck)
            except Exception:
                record['status']='manual_recovery_required'
                try:state_write(path,record)
                except OSError:pass
            raise


CODES=frozenset(('deadline_shape','unsafe_parent','public_file_metadata','public_file_changed','operation_deadline',
    'publication_short','publication_changed','timer_busy','rollback_journal_failed',
    'command_timeout','command_bound','command_failed','dashboard_service_contract','dashboard_launch_contract','listener_shape',
    'certificate_hostname','certificate_dates','certificate_expired','hostname_address_changed','http_head_status','https_redirect_changed',
    'dashboard_public_source_changed','state_owner','marker_metadata','marker_busy','marker_changed','other_controller_present',
    'dashboard_listener_changed','dashboard_listener_pending','backend_port_in_use','tls_front_missing','tls_front_pending',
    'owned_file_changed','rollback_state','root_required',
    'action','machine_identity','preexisting_public_file','fresh_identity_required','fresh_window_required','fresh_operation_required'))
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--action',choices=('check','apply','rollback'),default='check')
    p.add_argument('--nonce',default='');p.add_argument('--deadline-utc',default='');p.add_argument('--machine-id',default='');args=p.parse_args(argv)
    try:print(json.dumps(execute(args.action,args.nonce,args.deadline_utc,args.machine_id),sort_keys=True));return 0
    except Exception as error:
        reason=error.args[0] if type(error) is Refusal and len(error.args)==1 and type(error.args[0]) is str and error.args[0] in CODES else 'dashboard_tls_refused'
        print(json.dumps({'status':'refused','reason':reason,'security_accepted':False},sort_keys=True),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
