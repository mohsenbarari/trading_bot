"""Closed transport stages; only synthetic commands, no live transport/data."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from hashlib import sha256
import ast,json,os,subprocess,unittest
ROOT=Path(__file__).resolve().parents[1]
os.environ['APP_ENV_FILE']=str(ROOT/'config/unit-test.env.example')
from scripts import relay_production_coin_inference_snapshot as R
EXPECTED={'snapshot_query':'remote_snapshot_query_failed','runtime_prepare':'remote_runtime_prepare_failed','snapshot_transfer':'remote_snapshot_transfer_failed','snapshot_install':'remote_snapshot_install_failed'}
BASE_FINGERPRINTS={'_run_bounded': '7ac92708fbd3497f4126bdb9cfd5ab1afa9cc7548a8e2b9bb4bfa4a9488e445d', '_validated_snapshot': '1c32524b9d732505be9111704c7b23ea0617d60043e1eb21ab9be9c6f9c8f186', '_single_writer_lock': 'd2f8f92303dd9dc1ac6c91abaf01a2c555435461d27056d0a663b84ef68d0ddf', '_restore_local_transaction': 'feef5b056ab8e318ee92904b90ff9a8a516b5dfedb6c6b962d8a9627b60572ff', '_remote_snapshot_digest': '1cfd75853f933852a0788dd2125d749d93ac26be303a1a4ae64c44a491b57f1b', '_relay_remote': 'a5af52b78dc2f251234688b1c130444274c3e13add391df95b9eb2732fcef117', '_publish_and_relay': '5cf05a536d0dca9ed8351a1de886b7929b1144b027003e880b939071a9d18d6e', '_install_relayed': 'b23431e3dbc2b4a7632d4720658c8e13b98097338103932964d384d2d7b2d041', 'build_parser': 'bf78e59022289a83b550ade253f0274f46184dfbed173051824dcaa8d1874377'}

class NormalizeCalls(ast.NodeTransformer):
 def visit_Call(self,node):
  if isinstance(node.func,ast.Name)and node.func.id=='_run_transport':
   if not node.args or not isinstance(node.args[0],ast.Constant)or node.args[0].value not in EXPECTED:raise AssertionError('unexpected stage call')
   node.func=ast.Name(id='_run_bounded',ctx=ast.Load());node.args=node.args[1:]
  return self.generic_visit(node)
def fingerprint(node):
 for item in ast.walk(node):
  if hasattr(item,'type_params'):
   if item.type_params:raise AssertionError('unreviewed type parameters')
   item._fields=tuple(name for name in item._fields if name!='type_params')
 return sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()

class TransportStageTests(unittest.TestCase):
 def test_default_commands_and_all_safety_transaction_functions_preserved(self):
  nodes={n.name:n for n in ast.parse(Path(R.__file__).read_bytes()).body if isinstance(n,ast.FunctionDef)}
  for name,digest in BASE_FINGERPRINTS.items():
   with self.subTest(function=name):self.assertEqual(fingerprint(NormalizeCalls().visit(nodes[name])),digest)
  self.assertEqual((R.REMOTE_TRANSPORT_TIMEOUT_SECONDS,R.REMOTE_CLEANUP_TIMEOUT_SECONDS,R.PRODUCTION_MAXIMUM_AGE_SECONDS),(30,10,120))
 def test_all_four_failures_remain_calledprocess_errors_with_original_fields(self):
  self.assertEqual(R.TRANSPORT_FAILURE_CODES,EXPECTED)
  for stage,code in EXPECTED.items():
   command=['owned-public-command'];original=subprocess.CalledProcessError(23,command,output='SYNTHETIC_PRIVATE_OUTPUT',stderr='SYNTHETIC_PRIVATE_STDERR')
   with self.subTest(stage=stage),patch.object(R,'_run_bounded',side_effect=original)as runner,self.assertRaises(subprocess.CalledProcessError)as caught:
    R._run_transport(stage,command,check=True,capture_output=True,timeout=30)
   error=caught.exception;self.assertIsInstance(error,R.ProductionSnapshotTransportError)
   self.assertEqual((error.returncode,error.cmd,error.output,error.stderr),(23,command,original.output,original.stderr))
   self.assertEqual(str(error),code);self.assertEqual(error.reason_code,code)
   runner.assert_called_once_with(command,check=True,capture_output=True,timeout=30)
 def test_success_returns_original_result_and_nontransport_errors_are_not_wrapped(self):
  result=subprocess.CompletedProcess(['owned-public-command'],0,stdout='owned-public')
  with patch.object(R,'_run_bounded',return_value=result):self.assertIs(R._run_transport('snapshot_query',['owned-public-command'],check=True),result)
  original=ValueError('SYNTHETIC_PRIVATE_GATE')
  with patch.object(R,'_run_bounded',side_effect=original),self.assertRaises(ValueError)as caught:R._run_transport('snapshot_query',['owned-public-command'],check=True)
  self.assertIs(caught.exception,original)
 def test_main_reports_only_closed_stage_and_preserves_exit_two(self):
  def handler(args):raise R.ProductionSnapshotTransportError('snapshot_install',subprocess.CalledProcessError(2,['SYNTHETIC_PRIVATE_ARG'],output='SYNTHETIC_PRIVATE_BODY'))
  args=SimpleNamespace(production_confirmation=R.PRODUCTION_CONFIRMATION,maximum_age_seconds=120,handler=handler)
  with patch.object(R,'build_parser',return_value=SimpleNamespace(parse_args=lambda argv:args)),patch.object(R,'_emit')as emit:self.assertEqual(R.main([]),2)
  emit.assert_called_once_with(status='FAILED',reason='remote_snapshot_install_failed');self.assertNotIn('SYNTHETIC_PRIVATE',str(emit.call_args))
 def test_unknown_stage_refuses_before_transport_and_old_gate_error_is_preserved(self):
  with patch.object(R,'_run_bounded')as runner,self.assertRaisesRegex(R.ProductionSnapshotRelayError,'remote_transport_stage_invalid'):
   R._run_transport('UNKNOWN_SYNTHETIC_STAGE',['owned-public-command'])
  runner.assert_not_called()
  original=R.ProductionSnapshotRelayError('snapshot_stale_or_future')
  with patch.object(R,'_run_bounded',side_effect=original),self.assertRaises(R.ProductionSnapshotRelayError)as caught:R._run_transport('snapshot_query',['owned-public-command'],check=True)
  self.assertIs(caught.exception,original)
 def test_each_normal_callsite_reports_its_own_stage_with_cleanup_preserved(self):
  options=dict(remote_host='root@192.0.2.10',remote_port=2200,remote_runtime_root='/owned/production-runtime',remote_snapshot='/owned/production-runtime/snapshot.json',remote_project_dir='/owned/public-project',remote_identity_file='/owned/public-test-identity')
  with patch.object(R,'_validate_remote_identity_file',return_value=Path('/owned/public-test-identity')):
   original=subprocess.CalledProcessError(255,['owned-public-command'])
   with patch.object(R,'_run_bounded',side_effect=original),self.assertRaises(R.ProductionSnapshotTransportError)as caught:R._remote_snapshot_digest(**options)
   self.assertEqual(caught.exception.reason_code,EXPECTED['snapshot_query'])
   for failure,stage in ((1,'runtime_prepare'),(2,'snapshot_transfer'),(3,'snapshot_install')):
    calls=[]
    def runner(command,**kwargs):
     calls.append((command,kwargs))
     if len(calls)==failure:raise subprocess.CalledProcessError(23,command)
    with self.subTest(stage=stage),patch.object(R,'_run_bounded',side_effect=runner),self.assertRaises(R.ProductionSnapshotTransportError)as caught:
     R._relay_remote(Path('/owned/public-snapshot.json'),maximum_age_seconds=120,digest='f'*64,**options)
    self.assertEqual(caught.exception.reason_code,EXPECTED[stage]);self.assertEqual(len(calls),1 if failure==1 else failure+1)
    if failure>1:self.assertFalse(calls[-1][1]['check'])

 def test_stateless_options_are_fixed_and_keep_default_configuration(self):
  self.assertEqual(R.REMOTE_STATELESS_SSH_OPTIONS,('-o','ControlMaster=no','-o','ControlPath=none','-o','ControlPersist=no'))
  self.assertIsInstance(R.REMOTE_STATELESS_SSH_OPTIONS,tuple)
 def test_query_prepare_scp_and_install_all_disable_multiplexing(self):
  options=dict(remote_host='root@192.0.2.10',remote_port=2200,remote_runtime_root='/owned/production-runtime',remote_snapshot='/owned/production-runtime/snapshot.json',remote_project_dir='/owned/public-project',remote_identity_file='/owned/public-test-identity')
  calls=[]
  def runner(command,**kwargs):
   calls.append((command,kwargs));return subprocess.CompletedProcess(command,0,stdout='absent\n')
  with patch.object(R,'_validate_remote_identity_file',return_value=Path('/owned/public-test-identity')),patch.object(R,'_run_bounded',side_effect=runner):
   self.assertIsNone(R._remote_snapshot_digest(**options))
   R._relay_remote(Path('/owned/public-snapshot.json'),maximum_age_seconds=120,digest='f'*64,**options)
  self.assertEqual([c[0][0] for c in calls],['ssh','ssh','scp','ssh'])
  expected=['ControlMaster=no','ControlPath=none','ControlPersist=no','StrictHostKeyChecking=accept-new','BatchMode=yes','IdentitiesOnly=yes','PasswordAuthentication=no','KbdInteractiveAuthentication=no','ConnectTimeout=10']
  for command,kwargs in calls:
   with self.subTest(program=command[0]):
    self.assertEqual([command[i+1] for i,x in enumerate(command[:-1]) if x=='-o'],expected)
    self.assertNotIn('-F',command);self.assertEqual(command[command.index('-i')+1],'/owned/public-test-identity')
    self.assertEqual(kwargs['timeout'],30);self.assertTrue(kwargs['check'])
  self.assertEqual(calls[0][1],{'check':True,'text':True,'capture_output':True,'timeout':30})
  for command,kwargs in calls[1:]:self.assertEqual(kwargs,{'check':True,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,'timeout':30})
 def test_failed_transfer_cleanup_uses_the_same_stateless_ssh(self):
  options=dict(remote_host='root@192.0.2.10',remote_port=2200,remote_runtime_root='/owned/production-runtime',remote_snapshot='/owned/production-runtime/snapshot.json',remote_project_dir='/owned/public-project',remote_identity_file='/owned/public-test-identity')
  for failure in (2,3):
   calls=[]
   def runner(command,**kwargs):
    calls.append((command,kwargs))
    if len(calls)==failure:raise subprocess.CalledProcessError(23,command)
    return subprocess.CompletedProcess(command,0)
   with self.subTest(failure=failure),patch.object(R,'_validate_remote_identity_file',return_value=Path('/owned/public-test-identity')),patch.object(R,'_run_bounded',side_effect=runner),self.assertRaises(R.ProductionSnapshotTransportError):
    R._relay_remote(Path('/owned/public-snapshot.json'),maximum_age_seconds=120,digest='f'*64,**options)
   command,kwargs=calls[-1];self.assertEqual(command[0],'ssh');self.assertIn('rm -f --',command[-1])
   self.assertEqual(command[3:9],list(R.REMOTE_STATELESS_SSH_OPTIONS))
   self.assertEqual(kwargs,{'check':False,'stdout':subprocess.DEVNULL,'stderr':subprocess.DEVNULL,'timeout':10})

if __name__=='__main__':unittest.main()
