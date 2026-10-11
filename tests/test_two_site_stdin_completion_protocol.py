"""Root guards real refs/control/Scope; fixture cleanup limitations remain observed."""
from __future__ import annotations
import os
os.environ["APP_ENV_FILE"]="config/unit-test.env.example"
from tests import test_p1_00_characterization_network_isolation as _net
_net.install_p1_00_network_fence()
import ast
import contextlib as ctx
import pickle
import io
from dataclasses import dataclass,replace
import hashlib
import importlib
import inspect
from pathlib import Path
import select
import selectors as sel
import signal
import stat
import subprocess as sp
import sys
import textwrap
from unittest.mock import patch
import pytest
from scripts import two_site_proposal_runner as owner
from scripts import two_site_provider_broker as bus
from scripts import two_site_codex_autopilot as auto
from scripts import two_site_cycle_metrics as _stats
from scripts.two_site_assignment_scope import AssignmentScope
_CAPTURE=owner.capture_bounded_process
_POPEN=sp.Popen
_READ,_WRITE=os.read,os.write
_SEL=sel.DefaultSelector
_TERMINATE=owner.terminate_process
_ERROR="CLI input was not completely written"
_WAIT=("wait","selector-close")
_FAIL=("wait","terminate","selector-close")
_KILL=("terminate","wait","selector-close")
_EARLY=("terminate","selector-close")
_ACK="import os,sys,hashlib\na=int(os.environ['ACK_FD']);g=int(os.environ['GATE_FD'])\n"
_DIGEST=("d=sys.stdin.buffer.read()\n" "os.write(1,(str(len(d))+':'+hashlib.sha256(d).hexdigest()+'\\n').encode())\n" "os.write(2,b'err\\x00\\n')\n")
_CLOSE=("d=os.read(0,64)\nos.close(0)\n" "os.write(1,b'read=64\\n');os.write(2,b'err\\x00\\n')\n" "os.write(a,b'!')\n")
_ZERO="os.close(0);os.write(1,b'read=0\\n');os.write(2,b'err\\x00\\n');os.write(a,b'!')\n"
_HOLD="os.write(1,b'R');os.write(a,b'!');os.read(g,1)\n"
_FOREIGN={"prior":[None,{"nested":[b"foreign",7]}]}
_MODS=(owner,bus,auto,_stats)
_MODE=0o666&~int(next(x.split()[1]for x in Path("/proc/self/status").read_text().splitlines()if x.startswith("Umask:")),8)
def _fds():
 result={}
 for entry in Path("/proc/self/fd").iterdir():
  try:
   result[int(entry.name)]=os.readlink(entry)
  except FileNotFoundError:pass
 return result
def _stat(path):
 s=path.lstat()
 return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
def _tree(root):return{str(p.relative_to(root)):(_stat(p)if p.is_file()else _stat(p)[:6],p.read_bytes()if p.is_file()else None)for p in sorted(root.rglob("*"))}
def _typed(value):
 b=io.BytesIO();p=pickle.Pickler(b,5);p.fast=True;p.dump(value);return b.getvalue()
def _ids(v):return(id(v),tuple(_ids(x)for x in(v.values()if isinstance(v,dict)else v)))if isinstance(v,(dict,list,tuple))else id(v)
def _custody(c,out,err,rows):return(_typed(c),(id(c),id(c.data),id(out),id(err)),_typed(_FOREIGN),_ids(_FOREIGN),_typed(rows),_ids(rows),tuple((m.__name__,id(sys.modules[m.__name__]),m.__file__)for m in _MODS),(os.read,os.write,sel.DefaultSelector,owner.terminate_process,owner.time.monotonic,owner.capture_bounded_process,bus.capture_bounded_process,auto.capture_bounded_process))
@dataclass(frozen=True)
class Case:
 code:str=_DIGEST
 data:bytes=b"good\x00\n"
 out:bytes|None=None
 err:bytes|None=b"err\x00\n"
 etype:type|None=None
 args:tuple=()
 rc:int|None=0
 closed:tuple=(True,True,True)
 order:tuple=_WAIT
 sent:int|None=None
 read_n:int|None=None
 hit:tuple=()
 mode:str="normal"
 cap:int=owner.MAX_OUTPUT
 timeout:float=3.0
 prewait:bool=False
 collision:str|None=None
def _freeze(case):return replace(case,out=(str(len(case.data))+":"+hashlib.sha256(case.data).hexdigest()+"\n").encode()if case.out is None and case.code==_DIGEST else case.out,sent=len(case.data)if case.sent is None else case.sent,read_n=len(case.data)if case.read_n is None and case.code==_DIGEST else case.read_n,)
class _Process:
 def __init__(self,child,mode,order,cause):self.child,self.mode,self.order,self.cause=child,mode,order,cause
 def __getattr__(self,name):
  if self.mode=="missing-"+name:return None
  return getattr(self.child,name)
 def wait(self,*args,**kwargs):
  self.order.append("wait")
  if self.mode in{"wait-error","wait-base","wait-timeout"}:raise self.cause
  return self.child.wait(*args,**kwargs)
class Harness:
 def __init__(self,root):
  self.root,self.rows=root,[]
  for name,content in(("canary",b"canary\x00"),("HEAD",b"a"*40),("protected-refs",b"main=protected\n"),("control.json",b'{"state":"STOPPED"}\n'),("scope.json",b'{"allowed":["capture"]}\n')):
   (root/name).write_bytes(content)
  self.root_id=_stat(root)[:5]
 def call(self,label,c,fn=_CAPTURE,*,process=None,paths=None,propagate=False):
  root=self.root
  out,err=paths or(root/(label+".stdout"),root/(label+".stderr"))
  if c.collision:(out if c.collision=="stdout" else err).write_bytes(b"collision")
  before,fd_before=_tree(root),_fds()
  saved=_custody(c,out,err,self.rows)
  order,hit,writes=[],[],[]
  owned,child,seen=[],process,None
  provided=set()if process is None else{s.fileno()for s in(process.stdin,process.stdout,process.stderr)}
  stack=ctx.ExitStack()
  try:
   if child is None:
    ar,aw=os.pipe();gr,gw=os.pipe()
    owned=[ar,aw,gr,gw]
    child=_POPEN([sys.executable,"-I","-u","-c",_ACK+c.code],stdin=sp.PIPE,stdout=sp.PIPE,stderr=sp.PIPE,start_new_session=True,pass_fds=(aw,gr),cwd=root,env={"PATH":"/usr/bin","PYTHONDONTWRITEBYTECODE":"1","ACK_FD":str(aw),"GATE_FD":str(gr)})
    os.close(aw);os.close(gr);owned=[ar,gw]
    if c.prewait:child.wait(timeout=3)
    if c.mode in{"read-error","read-base","terminate-error","hung","zero-write"}:self._ack(ar)
   cause=c.etype(*c.args)if c.etype and(c.mode.startswith(("read-","write-","wait-"))or c.mode=="terminate-error")else None
   proc=_Process(child,c.mode,order,cause)
   fd_in=child.stdin.fileno()
   outs={child.stdout.fileno(),child.stderr.fileno()}
   sent=0
   rounds=0
   def write(fd,data):
    nonlocal sent
    if fd!=fd_in:return _WRITE(fd,data)
    if c.mode in{"epipe","poll-exit"}and sent==0:
     n=_WRITE(fd,data[:64]);assert n==64
     sent+=n;writes.append(n)
     self._ack(ar)
     if c.mode=="poll-exit":child.wait(timeout=3)
     return n
    if c.mode=="zero-reader" and not writes:self._ack(ar)
    if c.mode=="zero-write":
     writes.append(0);hit.append("zero-write")
     return 0
    if c.mode in{"write-error","write-base"}:
     hit.append(c.mode);raise cause
    if c.mode=="blocking" and not writes:
     writes.append("blocked");hit.append("BlockingIOError")
     raise BlockingIOError(11,"write blocked")
    try:
     n=_WRITE(fd,data[:257]if c.mode in{"short","blocking"}or len(c.data)>=200_000 and c.mode=="normal"else data)
    except BrokenPipeError as exc:
     assert exc.errno==32
     hit.append("actual-EPIPE")
     raise
    sent+=n;writes.append(n)
    if n<len(data)and "short-write" not in hit:hit.append("short-write")
    return n
   def read(fd,count):
    if fd in outs:
     if c.mode in{"read-error","terminate-error"}:
      hit.append("read-error");raise cause if c.mode=="read-error" else OSError(5,"read fault")
     if c.mode=="read-base":
      hit.append("read-base");raise cause
     if c.mode=="read-blocking" and "read-BlockingIOError" not in hit:
      hit.append("read-BlockingIOError")
      raise BlockingIOError(11,"read blocked")
    return _READ(fd,count)
   class Spy:
    def __init__(self):self.child=_SEL()
    def __getattr__(self,name):return getattr(self.child,name)
    def select(self,timeout):
     nonlocal rounds
     rounds+=1
     if c.mode=="poll-exit" and sent==64 and rounds==2:
      assert child.poll()==c.rc
      assert self.child.get_key(child.stdin).data=="input"
      hit.append("native-poll-exit")
      return[]
     events=self.child.select(timeout)
     if c.mode in{"epipe","poll-exit","zero-reader"}and not hit:
      inputs=[e for e in events if e[0].data=="input"]
      if inputs:return inputs
     if c.mode in{"read-error","read-base","terminate-error"}:events=[e for e in events if e[0].data=="output"]
     return sorted(events,key=lambda e:e[0].fd)
    def close(self):
     order.append("selector-close");self.child.close()
   def terminate(p):
    order.append("terminate")
    if c.mode=="terminate-error":raise cause
    return _TERMINATE(p)
   stack.enter_context(patch.object(os,"write",write))
   stack.enter_context(patch.object(os,"read",read))
   stack.enter_context(patch.object(sel,"DefaultSelector",Spy))
   stack.enter_context(patch.object(owner,"terminate_process",terminate))
   if c.mode=="clock-timeout":
    ticks=iter((0,c.timeout+1))
    stack.enter_context(patch.object(owner.time,"monotonic",side_effect=lambda:next(ticks)))
   value,exc=None,None
   try:
    value=fn(proc,c.data,out,err,c.timeout,max_output=c.cap)
   except BaseException as caught:exc=caught
   if c.collision:assert exc.filename==str(out if c.collision=="stdout"else err)
   if cause is not None:assert exc is cause
   seen=(value,type(exc)if exc else None,exc.args if exc else(),out.read_bytes()if out.exists()else None,err.read_bytes()if err.exists()else None,child.poll(),tuple(s.closed for s in(child.stdin,child.stdout,child.stderr)),tuple(order),sent,self._read_count(out),tuple(dict.fromkeys(hit)))
   if exc is not None and type(exc)is owner.ProposalError and str(exc)==_ERROR:assert not owner._provider_output_is_retryable(exc)
   self.check(seen,c)
   if propagate and exc:raise exc
   return seen
  finally:
   stack.close()
   if child is not None:
    if child.poll()is None:os.killpg(child.pid,signal.SIGKILL)
    child.wait(timeout=3)
    for stream in(child.stdin,child.stdout,child.stderr):
     if not stream.closed:stream.close()
    assert child.poll()is not None
   for fd in owned:
    os.close(fd)
   assert _custody(c,out,err,self.rows)==saved
   after=_tree(root)
   allowed={str(out.relative_to(root)),str(err.relative_to(root))}-before.keys()
   assert set(after)-set(before)<=allowed
   assert set(before)<=set(after)
   for name,saved in before.items():
    assert after[name]==saved
   for path in(out,err):
    if str(path.relative_to(root))not in before and path.exists():
     s=path.lstat()
     assert stat.S_ISREG(s.st_mode)and s.st_nlink==1 and s.st_uid==os.geteuid()
     assert stat.S_IMODE(s.st_mode)==_MODE
     assert s.st_dev==self.root_id[0]
     assert s.st_size==len(path.read_bytes())
   assert _stat(root)[:5]==self.root_id
   assert _fds()=={fd:target for fd,target in fd_before.items()if fd not in provided}
   if seen is not None:self.rows.append(seen)
 @staticmethod
 def _ack(fd):
  ready,_,_=select.select([fd],[],[],3)
  assert ready and _READ(fd,1)==b"!","native child did not reach controlled behavior"
 @staticmethod
 def _read_count(path):
  if not path.exists():return None
  data=path.read_bytes()
  for line in data.splitlines():
   if line.startswith(b"read="):return int(line[5:])
   if b":" in line and line.split(b":",1)[0].isdigit():return int(line.split(b":",1)[0])
  return None
 @staticmethod
 def check(value,c):
  expect=(None,c.etype,c.args,c.out,c.err,c.rc,c.closed,c.order,c.sent,c.read_n,c.hit)
  assert _typed(value)==_typed(expect)
@pytest.fixture
def h(tmp_path):return Harness(tmp_path)
def _triplet(h,label,case,fn=_CAPTURE):
 good,kind,restored=map(_freeze,(Case(),case,Case()))
 h.call(label+"-good",good,fn)
 h.call(label+"-kind",kind,fn)
 h.call(label+"-restored",restored,fn)
@pytest.mark.parametrize("size",[0,200_000,819_200])
def test_native_complete_sizes(h,size):_triplet(h,str(size),Case(data=b"x"*size,hit=()if not size else("short-write",)))
@pytest.mark.parametrize("mode,code,count,rc,hit",[("zero-reader",_ZERO,0,0,("actual-EPIPE",)),("epipe",_CLOSE,64,0,("actual-EPIPE",)),("epipe",_CLOSE+"sys.exit(7)\n",64,7,("actual-EPIPE",)),("poll-exit",_CLOSE,64,0,("native-poll-exit",)),])
def test_native_incomplete(h,mode,code,count,rc,hit):_triplet(h,mode+str(rc),Case(code=code,data=b"x"*819_200,out=("read="+str(count)+"\n").encode(),etype=owner.ProposalError,args=(_ERROR,),sent=count,read_n=count,rc=rc,mode=mode,hit=hit,order=_FAIL))
@pytest.mark.parametrize("mode,hit",[("short",("short-write",)),("blocking",("BlockingIOError","short-write")),("read-blocking",("read-BlockingIOError",)),])
def test_io_progress(h,mode,hit):_triplet(h,mode,Case(data=b"x"*(8 if mode=="read-blocking" else 200_000),mode=mode,hit=hit))
@pytest.mark.parametrize("data",['{"event":"user","message":{"content":"سلام\\u0000\\n"}}\n'.encode(),"سلام\x00".encode()])
def test_stream_bytes(h,data):
 echo="d=sys.stdin.buffer.read();os.write(1,d);os.write(2,b'err\\x00\\n')\n"
 _triplet(h,"framing",Case(code=echo,data=data,out=data,sent=len(data)))
def test_native_backpressure(h):
 data=b"z"*819_200
 code="os.write(1,b'O'*131072+b'\\n');os.write(2,b'E'*131072)\n"+_DIGEST
 report=(str(len(data))+":"+hashlib.sha256(data).hexdigest()+"\n").encode()
 _triplet(h,"backpressure",Case(code=code,data=data,out=b"O"*131072+b"\n"+report,err=b"E"*131072+b"err\x00\n",sent=len(data),mode="blocking",read_n=len(data),hit=("BlockingIOError","short-write"),cap=300_000))
@pytest.mark.parametrize("extra",[0,1])
def test_shared_output_cap(h,extra):
 code="os.write(1,b'A'*8);os.write(2,b'B'*"+str(8+extra)+")\n"
 _triplet(h,"cap"+str(extra),Case(code=code,data=b"",out=b"A"*8,err=b"B"*8,cap=16,sent=0,prewait=True,etype=owner.ProposalError if extra else None,args=("CLI output exceeds budget",)if extra else(),order=_EARLY if extra else _WAIT))
@pytest.mark.parametrize("mode,etype,args,hit",[("read-error",OSError,(5,"read fault"),("read-error",)),("read-base",KeyboardInterrupt,("read interrupt",),("read-base",)),("write-error",OSError,(5,"write fault"),("write-error",)),("write-base",SystemExit,(17,),("write-base",)),("terminate-error",OSError,(5,"termination kind"),("read-error",)),])
def test_primary_and_cleanup_errors(h,mode,etype,args,hit):_triplet(h,mode,Case(code=_HOLD,data=b"pending"*50_000,out=b"",err=b"",etype=etype,args=args,mode=mode,rc=None if mode=="terminate-error" else-signal.SIGKILL,sent=0,hit=hit,order=_EARLY if mode=="terminate-error" else _KILL))
@pytest.mark.parametrize("mode,etype,args",[("wait-error",OSError,(5,"wait fault")),("wait-base",KeyboardInterrupt,("wait interrupt",)),("wait-timeout",sp.TimeoutExpired,("frozen wait",3)),])
def test_wait_error_priority(h,mode,etype,args):_triplet(h,mode,Case(code=_ZERO,data=b"pending"*50_000,out=b"read=0\n",etype=etype,args=args,mode=mode,sent=0,read_n=0,prewait=True,hit=("actual-EPIPE",),order=_FAIL))
@pytest.mark.parametrize("mode",["zero-write","hung"])
def test_timeout_priority(h,mode):
 code="os.write(a,b'!');os.read(g,1)\n"
 data=b"pending"*50_000 if mode=="zero-write" else b""
 timeout=0.03
 c=_freeze(Case(code=code,data=data,out=b"",err=b"",mode=mode,etype=sp.TimeoutExpired,args=([sys.executable,"-I","-u","-c",_ACK+code],timeout),rc=-signal.SIGKILL,sent=0,timeout=timeout,hit=("zero-write",)if mode=="zero-write" else(),order=_KILL))
 good,restored=_freeze(Case()),_freeze(Case())
 h.call(mode+"-good",good)
 h.call(mode+"-kind",c)
 h.call(mode+"-restored",restored)
@pytest.mark.parametrize("missing",["stdin","stdout","stderr","cap"])
def test_early_configuration(h,missing):_triplet(h,missing,Case(code=_HOLD,data=b"",out=None,err=None,mode="missing-"+missing,cap=0 if missing=="cap" else owner.MAX_OUTPUT,etype=owner.ProposalError,args=("invalid bounded process configuration",),sent=0,rc=None,closed=(False,False,False),order=()))
@pytest.mark.parametrize("collision",["stdout","stderr"])
def test_file_collision(h,collision):
 case=Case(code=_HOLD,data=b"",out=b"collision" if collision=="stdout" else b"",err=None if collision=="stdout" else b"collision",collision=collision,etype=FileExistsError,rc=-signal.SIGKILL,sent=0,order=_KILL)
 _triplet(h,collision,replace(case,args=(17,"File exists")))
def _mutant():
 tree=ast.parse(textwrap.dedent(inspect.getsource(_CAPTURE)))
 removed=[]
 for node in ast.walk(tree):
  for field,value in ast.iter_fields(node):
   if not isinstance(value,list):continue
   for statement in tuple(value):
    if(isinstance(statement,ast.If)and ast.unparse(statement.test)=="input_offset != len(input_bytes)"):
     removed.append(statement);value.remove(statement)
 assert len(removed)==1,"counterproof must disable the actual final check"
 assert ast.unparse(removed[0].body[0])=="raise ProposalError('CLI input was not completely written')"
 namespace=dict(owner.__dict__)
 exec(compile(ast.fix_missing_locations(tree),"<actual-capture-minus-final-check>","exec"),namespace)
 return namespace["capture_bounded_process"]
@pytest.mark.parametrize("mode,hit",[("epipe",("actual-EPIPE",)),("poll-exit",("native-poll-exit",)),])
def test_counterproof_restore(h,mode,hit):
 good=_freeze(Case())
 negative=_freeze(Case(code=_CLOSE,data=b"x"*819_200,out=b"read=64\n",etype=owner.ProposalError,args=(_ERROR,),sent=64,read_n=64,mode=mode,hit=hit,order=_FAIL))
 h.call(mode+"-good",good)
 h.call(mode+"-negative",negative)
 mutant=_mutant()
 with pytest.raises(AssertionError):h.call(mode+"-disabled-negative",negative,mutant)
 assert h.rows[-1][1]is None
 assert h.rows[-1][8]==64 and h.rows[-1][9]==64
 assert h.rows[-1][10]==hit
 h.call(mode+"-disabled-good",good,mutant)
 assert owner.capture_bounded_process is _CAPTURE
 h.call(mode+"-restored-good",good)
 h.call(mode+"-restored-negative",negative)
def test_public_routes(h):
 assert bus.capture_bounded_process is auto.capture_bounded_process is _CAPTURE
 for f in(owner.run,owner.cli_identity,bus.execute_pipeline,auto.codex_identity,auto.run_cycle):
  assert inspect.getclosurevars(f).globals["capture_bounded_process"]is _CAPTURE
 cli=h.root/"offline-cli";cli.write_bytes(b"offline fixture");cli.chmod(0o700)
 digest=hashlib.sha256(cli.read_bytes()).hexdigest()
 pipeline=h.root/bus.PIPELINE_REL;pipeline.parent.mkdir();pipeline.write_bytes(b"offline fixture")
 scope=AssignmentScope("WA-NATIVE","P1-00",("tests/**",),("scripts/**",),"2099-01-01T00:00:00Z","b"*64)
 request=dict(request_id="c"*32,run_prefix="offline",source_head="a"*40,timeout=10,controller_run_id="cycle-0001-20990101T000000Z-123")
 before=dict(head="a"*40,protected_refs={"main":"a"*40},assignment_scope=scope)
 complete=dict(state="COMPLETE",head_after="a"*40,summary="offline",next_action=None,blockers=[],gemini_worker_run_id=None,gemini_reviewer_run_id=None,proposal_sha256=None)
 reply=dict(schema_version="1.0",request_id=request["request_id"],run_prefix="offline",source_head="a"*40,state="COMPLETED",exit_code=0,stdout="ok",stderr="",finished_at=1234.0)
 wanted={k:_typed(v)for k,v in{"owner":dict(path=str(cli),version=owner.EXPECTED_CLI_VERSION,sha256=digest),"id":dict(path=str(cli),version=auto.EXPECTED_CODEX_CLI_VERSION,sha256=digest,model=auto.MODEL,reasoning=auto.REASONING,runtime={}),"bus":reply,"cycle":dict(complete,advisories=[])}.items()}
 bad_broker=_typed(dict(reply,exit_code=7,stderr="stderr"))
 failure={"owner":(owner.ProposalError,("CLI version is not the reviewed pin",)),"id":(auto.AutopilotError,("Codex CLI version probe failed",)),"cycle":(auto.AutopilotError,("Codex CLI exited with status 7",))}
 limits=dict(owner=10,id=15,bus=bus._overall_timeout(10),cycle=10)
 for name,module in(("owner",owner),("id",auto),("bus",bus),("cycle",auto)):
  for i,kind in enumerate((0,1,0,2,0,3,0)):
   base=bus.broker_paths(h.root)[0]if name=="bus"else h.root
   folder=base/(name+str(i));folder.mkdir()
   planned=((b"ok",b""),)
   if name=="owner":planned=((owner.EXPECTED_CLI_VERSION.encode(),b""),)
   if name=="id":planned=((auto.EXPECTED_CODEX_CLI_VERSION.encode(),b""),(b"offline login",b""))
   if kind==2:planned=tuple((out,b"stderr")for out,err in planned)
   tail=";sys.exit(7)"if kind==2 else ';os.write(int(os.environ["READY_FD"]),b"!");signal.pause()'if kind==3 else ""
   cmds=[[sys.executable,"-I","-u","-c","import os,sys,signal;os.close(0);os.write(1,"+repr(out)+");os.write(2,"+repr(err)+")"+tail]for out,err in planned]
   expect=(OSError,(5,"wait fault"))if kind==1 else(sp.TimeoutExpired,(cmds[0],limits[name]))if kind==3 else failure.get(name)if kind==2 else None
   expect=(expect[0],_typed(expect[1]))if expect else None
   want=bad_broker if name=="bus"and kind==2 else wanted[name]
   calls,errors=[],[]
   def spawn(command,**kwargs):
    assert kwargs["stdin"]is kwargs["stdout"]is kwargs["stderr"]is sp.PIPE
    cmd=cmds[len(calls)];calls.append(tuple(command))
    if name=="cycle":(next((state/"runs").iterdir())/"result.json").write_bytes(b"{}")
    ar,aw=os.pipe()
    try:
     p=_POPEN(cmd,stdin=sp.PIPE,stdout=sp.PIPE,stderr=sp.PIPE,start_new_session=True,cwd=h.root,pass_fds=(aw,),env={"READY_FD":str(aw),"PATH":"/usr/bin"})
     if kind==3:
      h._ack(ar);assert p.poll()is None
     else:p.wait(timeout=3)
     return p
    finally:os.close(ar);os.close(aw)
   def capture(p,data,out,err,timeout,max_output=owner.MAX_OUTPUT):
    assert data==b""and type(data)is bytes and timeout==limits[name]
    want_out,want_err=planned[len(calls)-1]
    c=_freeze(Case(code="public",data=b"",out=b""if kind==3 else want_out,err=b""if kind==3 else want_err,mode="wait-error"if kind==1 else"clock-timeout"if kind==3 else"normal",cap=max_output,timeout=timeout,etype=OSError if kind==1 else sp.TimeoutExpired if kind==3 else None,args=(5,"wait fault")if kind==1 else(p.args,timeout)if kind==3 else(),rc=-signal.SIGKILL if kind==3 else 7 if kind==2 else 0,order=_FAIL if kind==1 else _KILL if kind==3 else _WAIT))
    try:h.call("public",c,process=p,paths=(out,err),propagate=True)
    except BaseException as exc:errors.append(exc);raise
   with ctx.ExitStack()as stack:
    def bind(obj,field,**kwargs):return stack.enter_context(patch.object(obj,field,**kwargs))
    bind(sp,"Popen",side_effect=spawn)
    bind(module,"capture_bounded_process",side_effect=capture)
    bind(owner,"EXPECTED_CLI_SHA256",new=digest)
    bind(auto,"EXPECTED_CODEX_CLI_SHA256",new=digest)
    bind(auto,"codex_runtime_identity",return_value={})
    bind(owner.tempfile,"TemporaryDirectory",side_effect=lambda**kw:ctx.nullcontext(str(folder)))
    bind(bus.time,"time",return_value=1234.0)
    if name=="cycle":
     state=folder/"state";(state/"runs").mkdir(parents=True)
     for field,result in(("validate_repository",before),("prompt_for_cycle","offline"),("codex_identity",{"sha256":digest,"runtime":{}}),("file_sha256",digest),("validate_result",complete),("validate_transition",dict(complete))):
      bind(auto,field,return_value=result)
     for field in("write_supervisor_state","_heartbeat_running_cycle"):bind(auto,field,return_value=None)
     bind(_stats,"record_cycle",return_value=None)
     action=lambda:auto.run_cycle(h.root,cli,state,i+1,10)
    elif name=="owner":action=lambda:owner.cli_identity(cli)
    elif name=="id":action=lambda:auto.codex_identity(cli,folder)
    else:action=lambda:bus.execute_pipeline(h.root,request,pipeline,scope)
    if expect:
     with pytest.raises(expect[0])as caught:action()
     assert _typed(caught.value.args)==expect[1]
     if kind!=2:assert caught.value is errors[0]
     assert len(calls)==1
    else:
     result=action()
     assert _typed(result[1]if name=="cycle"else result)==want
   assert module.capture_bounded_process is _CAPTURE and auto._ACTIVE_PROCESS is None
def test_bare_aliases(h):
 with patch.object(sys,"path",[str(Path(owner.__file__).parent),*sys.path]),patch.dict(sys.modules):
  modules=[importlib.import_module(n)for n in("two_site_proposal_runner","two_site_provider_broker","two_site_codex_autopilot")]
  cap=modules[0].capture_bounded_process
  assert all(m.capture_bounded_process is _CAPTURE for m in modules[1:])
  assert modules[0].__file__==owner.__file__
  assert inspect.signature(cap)==inspect.signature(_CAPTURE)
  _triplet(h,"bare",Case(data=b""),cap)
