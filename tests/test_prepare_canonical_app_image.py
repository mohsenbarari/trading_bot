"""Public hermetic fixtures only; never build images, run Git/npm, or install."""
from contextlib import ExitStack,redirect_stdout
from hashlib import sha1,sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
import ast
import yaml
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
REAL_POPEN=subprocess.Popen
spec=importlib.util.spec_from_file_location('_canonical_app',ROOT/'scripts/prepare_canonical_app_image.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)

class CanonicalAppTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='canonical-public-')));self.root.chmod(0o700)
        self.repo=self.root/'repo';self.repo.mkdir(mode=0o700)
        for target in ('subprocess.run','subprocess.Popen','subprocess.check_output','os.system','os.popen',
                'socket.socket','socket.create_connection','urllib.request.urlopen'):
            self.stack.enter_context(patch(target,side_effect=AssertionError('ambient operation denied')))

    def file(self,name,raw=b'ordinary public source',base=None):
        path=(base or self.repo)/name;path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw);path.chmod(0o600);return path

    def shell(self,body):
        process=REAL_POPEN(['/bin/bash','-c',body,str(self.repo),*M.INPUTS],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        out,error=process.communicate(timeout=5);self.assertEqual((process.returncode,error),(0,b''));return out.decode().strip()

    def test_signature_matches_literal_ordinary_shell_including_empty_directory_and_caches(self):
        self.file('Dockerfile',b'FROM public\n');self.file('main.py');self.file('scripts/z.py');self.file('scripts/a.py')
        self.file('pip_packages/security-os-capsule/ordinary.deb',b'public package')
        self.file('scripts/__pycache__/ordinary.pyc',b'ignored');self.file('scripts/ordinary.pyo',b'ignored')
        (self.repo/'fonts').mkdir();self.file('mini_app_dist/index.html',b'public frontend')
        # Literal production algorithm, never source/eval deploy.sh or a guard.
        body=r'''set -e
PROJECT_DIR="$0"
hash_file_or_dir() {
 local rel="$1"; local path="$PROJECT_DIR/$rel"
 if [[ -f "$path" ]]; then sha256sum "$path" | sed "s#  $PROJECT_DIR/#  #"
 elif [[ -d "$path" ]]; then
  (cd "$PROJECT_DIR" && find "$rel" -type f ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo' ! -name '*.pyd' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum)
 fi
}
{ printf 'docker_image=%s\n' 'trading_bot_base'; for rel in "$@"; do hash_file_or_dir "$rel"; done; } | sha256sum | cut -d' ' -f1
'''
        rows=M.input_files(self.repo);empty=M.empty_inputs(self.repo,rows)
        self.assertEqual(M.foreign_signature(rows,empty),self.shell(body));self.assertEqual(empty,['fonts'])
        original=M.foreign_signature(rows,empty)
        for name in ('main.py','scripts/a.py','pip_packages/security-os-capsule/ordinary.deb','mini_app_dist/index.html'):
            old=(self.repo/name).read_bytes();self.file(name,old+b'changed');updated=M.input_files(self.repo)
            self.assertNotEqual(M.foreign_signature(updated,M.empty_inputs(self.repo,updated)),original);self.file(name,old)

    def test_input_inventory_rejects_links_and_bounds_before_reading(self):
        self.file('main.py');(self.repo/'scripts').mkdir();(self.repo/'scripts/alias.py').symlink_to(self.repo/'main.py')
        with self.assertRaisesRegex(RuntimeError,'^input_inventory$'):M.input_files(self.repo)
        (self.repo/'scripts/alias.py').unlink();os.link(self.repo/'main.py',self.repo/'scripts/hard.py')
        with self.assertRaisesRegex(RuntimeError,'^metadata$'):M.input_files(self.repo)
        (self.repo/'scripts/hard.py').unlink()
        with patch.object(M,'MAX_TOTAL',1),self.assertRaisesRegex(RuntimeError,'^input_bound$'):M.input_files(self.repo)

    def test_source_manifest_proves_git_blob_bytes_and_held_descriptor_detects_aba(self):
        path=self.file('Dockerfile',b'FROM public\n');raw=path.read_bytes();blob=sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        listing=('100644 blob '+blob+' '+str(len(raw))+'\tDockerfile\0').encode()
        with patch.object(M,'git',return_value=listing):rows=M.source_manifest(self.repo,'a'*40)
        self.assertEqual(rows[0]['sha256'],sha256(raw).hexdigest())
        with ExitStack() as stack:
            held=M.HeldSource(stack,self.repo,rows);held.check();path.rename(path.with_name('retained'));self.file('Dockerfile',raw)
            with self.assertRaisesRegex(RuntimeError,'^source_changed$'):held.check()
        with patch.object(M,'git',return_value=listing):
            self.file('Dockerfile',b'changed-public-source')
            with self.assertRaisesRegex(RuntimeError,'^source_changed$'):M.source_manifest(self.repo,'a'*40)

    def test_git_identity_rejects_sha_drift_and_dirty_output_without_running_git(self):
        with patch.dict(os.environ,{'GITHUB_SHA':'a'*40}),patch.object(M,'git',side_effect=[b'a'*40+b'\n',b'b'*40+b'\n',b'']):
            self.assertEqual(M.git_identity(self.repo),('a'*40,'b'*40))
        with patch.dict(os.environ,{'GITHUB_SHA':'a'*40}),patch.object(M,'git',side_effect=[b'c'*40,b'b'*40]),self.assertRaisesRegex(RuntimeError,'^git_identity$'):M.git_identity(self.repo)
        with patch.dict(os.environ,{'GITHUB_SHA':'a'*40}),patch.object(M,'git',side_effect=[b'a'*40,b'b'*40,b' M ordinary-public']),self.assertRaisesRegex(RuntimeError,'^git_dirty$'):M.git_identity(self.repo)

    def test_cli_hosted_boundary_precedes_any_state_or_source_read(self):
        with patch.dict(os.environ,{'RUNNER_ENVIRONMENT':'ordinary-local'}),patch.object(M,'read_file',side_effect=AssertionError('must reject first')):
            with self.assertRaisesRegex(RuntimeError,'^hosted_only$'):M.state(self.root)
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(M.main(['build','--root',str(self.root),'--repo','ordinary-private']),1)
        self.assertNotIn('ordinary-private',out.getvalue());self.assertFalse(json.loads(out.getvalue())['security_accepted'])

    def test_build_uses_true_labels_double_inputs_and_never_publishes_config(self):
        self.file('Dockerfile',b'FROM public\n');self.file('mini_app_dist/index.html',b'public frontend')
        output=self.root/'out';output.mkdir(mode=0o700);(output/'reports').mkdir(mode=0o700);rows=[r for r in M.input_files(self.repo) if r['path']=='Dockerfile']
        source={'source_commit':'a'*40,'source_tree':'b'*40,'run_id':'123','run_attempt':'1','files':rows}
        commands=[];image='sha256:'+'c'*64
        config={'WorkingDir':'/app','User':'','Entrypoint':None,'Cmd':['python3'],'Labels':{'ordinary':'public'},'Env':['PRIVATE_TEST_VALUE=never-publish']}
        info={'Config':config,'RootFS':{'Type':'layers','Layers':['sha256:'+'d'*64]}}
        def command(argv,**kwargs):
            commands.append(argv)
            if 'build' in argv:self.file('candidate-image.id',image.encode()+b'\n',base=output)
            return b''
        with patch.object(M,'state',return_value=(self.repo,source)),patch.object(M,'unchanged') as unchanged,\
                patch.object(M,'public_input_names',return_value={'Dockerfile'}),\
                patch.object(M,'command',side_effect=command),patch.object(M,'inspect',return_value=info),patch.object(M,'emit'):
            value=M.build(output)
        self.assertEqual(unchanged.call_count,2);self.assertEqual(value['image_id'],image)
        build=commands[-1];self.assertIn('org.opencontainers.image.revision='+'a'*40,build)
        self.assertIn('io.gold-trade.release.tree='+'b'*40,build)
        manifest=json.loads((output/'input-manifest.json').read_bytes())
        self.assertIn('io.gold-trade.release.input-signature='+manifest['input_signature'],build)
        self.assertEqual(build[-3:],['-f',str(self.repo/'Dockerfile'),str(self.repo)])
        raw=(output/'image-build.json').read_bytes();self.assertNotIn(b'PRIVATE_TEST_VALUE',raw);self.assertNotIn(b'Config',raw)
        self.assertFalse(json.loads(raw)['activation_performed'])

    def test_build_rejects_generated_input_change_after_build_without_success_receipt(self):
        self.file('Dockerfile');self.file('mini_app_dist/index.html',b'original')
        output=self.root/'out';output.mkdir(mode=0o700);(output/'reports').mkdir(mode=0o700)
        source={'source_commit':'a'*40,'source_tree':'b'*40,'run_id':'123','run_attempt':'1',
            'files':[r for r in M.input_files(self.repo) if r['path']=='Dockerfile']}
        def command(argv,**kwargs):
            if 'build' in argv:
                self.file('candidate-image.id',('sha256:'+'c'*64).encode(),base=output);self.file('mini_app_dist/index.html',b'changed')
        with patch.object(M,'state',return_value=(self.repo,source)),patch.object(M,'unchanged'),patch.object(M,'command',side_effect=command),\
                patch.object(M,'public_input_names',return_value={'Dockerfile'}),\
                patch.object(M,'inspect',return_value={'Config':{},'RootFS':{}}),self.assertRaisesRegex(RuntimeError,'^input_changed$'):
            M.build(output)
        self.assertFalse((output/'image-build.json').exists())

    def test_archive_config_binds_actual_config_digest_and_whole_rootfs(self):
        config={'config':{'Env':['ordinary-public-default']},'rootfs':{'type':'layers','diff_ids':['sha256:'+'d'*64]}}
        raw=M.encode(config);image='sha256:'+sha256(raw).hexdigest();path=self.root/'public.tar'
        with tarfile.open(path,'w') as archive:
            for name,body in [('manifest.json',M.encode([{'Config':'config.json','Layers':[]}])),('config.json',raw)]:
                item=tarfile.TarInfo(name);item.size=len(body);archive.addfile(item,io.BytesIO(body))
        self.assertEqual(M.archive_config(path,image,config['rootfs']['diff_ids']),image)
        with self.assertRaisesRegex(RuntimeError,'^archive_config$'):M.archive_config(path,'sha256:'+'e'*64,config['rootfs']['diff_ids'])
        with self.assertRaisesRegex(RuntimeError,'^archive_config$'):M.archive_config(path,image,['sha256:'+'f'*64])

    def reports(self,image,high=0):
        scan={'Metadata':{'ImageID':image},'Results':[{'Vulnerabilities':[{'Severity':'HIGH'}]*high}]}
        sbom={'metadata':{'component':{'type':'container','name':image,'properties':[{'name':'aquasecurity:trivy:ImageID','value':image}]}}}
        self.file('reports/vulnerabilities.json',M.encode(scan),base=self.root);self.file('reports/sbom.cdx.json',M.encode(sbom),base=self.root)

    def test_reports_bind_exact_image_retain_all_counts_and_gate_refuses_high(self):
        image='sha256:'+'c'*64;self.reports(image,2);counts,bound=M.reports(self.root,image)
        self.assertEqual(counts['HIGH'],2);self.assertEqual(set(bound),{'vulnerabilities.json','sbom.cdx.json'})
        self.file('image-build.json',M.encode({'image_id':image}),base=self.root)
        with patch.object(M,'hosted'),self.assertRaisesRegex(RuntimeError,'^raw_findings$'):M.gate(self.root)
        with self.assertRaisesRegex(RuntimeError,'^report_identity$'):M.reports(self.root,'sha256:'+'d'*64)

    def test_finalize_exports_same_public_fixture_and_binds_unsigned_receipt(self):
        self.file('Dockerfile');self.file('mini_app_dist/index.html')
        rows=M.input_files(self.repo);signature=M.foreign_signature(rows,M.empty_inputs(self.repo,rows))
        source={'source_commit':'a'*40,'source_tree':'b'*40,'run_id':'123','run_attempt':'1',
            'files':[r for r in rows if r['path']=='Dockerfile']}
        config={'config':{'Env':['ordinary-private-must-not-be-published']},'rootfs':{'type':'layers','diff_ids':['sha256:'+'d'*64]}}
        raw=M.encode(config);image='sha256:'+sha256(raw).hexdigest();body=io.BytesIO()
        with tarfile.open(fileobj=body,mode='w') as archive:
            for name,content in [('manifest.json',M.encode([{'Config':'config.json'}])),('config.json',raw)]:
                item=tarfile.TarInfo(name);item.size=len(content);archive.addfile(item,io.BytesIO(content))
        tar_bytes=body.getvalue()
        self.file('source-identity.json',M.encode(source),base=self.root)
        self.file('input-manifest.json',M.encode({'files':rows,'input_signature':signature,'empty_directories':M.empty_inputs(self.repo,rows)}),base=self.root)
        self.file('image-build.json',M.encode({'image_id':image,'source_commit':'a'*40,'source_tree':'b'*40,'input_signature':signature}),base=self.root)
        self.reports(image)
        info={'Config':{'Env':['ordinary-private-must-not-be-published']},'RootFS':{'Type':'layers','Layers':config['rootfs']['diff_ids']}}
        def export(argv,**kwargs):
            self.assertEqual(argv,[*M.DOCKER,'image','save',image]);os.write(kwargs['target'],tar_bytes)
        with patch.object(M,'state',return_value=(self.repo,source)),patch.object(M,'unchanged'),patch.object(M,'inspect',return_value=info),\
                patch.object(M,'public_input_names',return_value={'Dockerfile'}),\
                patch.object(M,'command',side_effect=export),patch.object(M,'emit'),\
                patch.object(M.os,'statvfs',return_value=SimpleNamespace(f_bavail=10*1024**3,f_frsize=1)):
            result=M.finalize(self.root)
        self.assertEqual(result['image_id'],image)
        receipt=json.loads((self.root/'image-export.json').read_bytes())
        self.assertEqual(receipt['archive']['uncompressed_sha256'],sha256(tar_bytes).hexdigest())
        self.assertEqual(receipt['archive']['sha256'],sha256((self.root/'candidate-image.tar.gz').read_bytes()).hexdigest())
        self.assertIs(receipt['full_config_compared_in_ram'],True)
        self.assertIs(receipt['physical_archive_admission_complete'],False);self.assertIs(receipt['signatures_verified'],False)
        self.assertFalse(receipt['security_accepted']);self.assertNotIn('ordinary-private',json.dumps(receipt))

    def test_unadmitted_ignored_file_is_refused_before_content_read_or_hash(self):
        self.file('Dockerfile');private=self.file('scripts/ordinary-unadmitted-fixture.env',b'public synthetic sensitive marker')
        read=M.read_file;seen=[]
        def admitted(path,maximum):
            self.assertNotEqual(Path(path),private);seen.append(str(path));return read(path,maximum)
        with patch.object(M,'read_file',side_effect=admitted),self.assertRaisesRegex(RuntimeError,'^input_inventory$'):
            M.input_files(self.repo,{'Dockerfile','scripts/approved.py'})
        self.assertNotIn(str(private),seen)

    def test_actual_authenticated_vendor_lock_names_are_admitted_before_build(self):
        relative='scripts/app_security_os_packages.lock.json'
        raw=(ROOT/relative).read_bytes()
        path=self.file(relative,raw)
        _,metadata=M.read_file(path,65536)
        sqlite_relative='scripts/sqlite_wal_package.lock.json'
        sqlite_raw=(ROOT/sqlite_relative).read_bytes();sqlite_path=self.file(sqlite_relative,sqlite_raw)
        _,sqlite_metadata=M.read_file(sqlite_path,65536)
        sqlite_row={'path':sqlite_relative,'identity':sqlite_metadata,'sha256':sha256(sqlite_raw).hexdigest()}
        tar_relative='scripts/tar_acl_package.lock.json'
        tar_raw=(ROOT/tar_relative).read_bytes();tar_path=self.file(tar_relative,tar_raw)
        _,tar_metadata=M.read_file(tar_path,65536)
        tar_row={'path':tar_relative,'identity':tar_metadata,'sha256':sha256(tar_raw).hexdigest()}
        source={'files':[{'path':relative,'identity':metadata,'sha256':sha256(raw).hexdigest()},sqlite_row,tar_row]}
        lock=json.loads(raw)
        allowed=M.public_input_names(self.repo,source)
        expected={'pip_packages/security-os-capsule/'+r['path'] for r in lock['packages']}
        self.assertEqual(len(expected),15)
        self.assertEqual(sum('~' in name for name in expected),6)
        self.assertTrue(expected<=allowed)
        sqlite_name='pip_packages/sqlite-wal-capsule/'+json.loads(sqlite_raw)['package']['path']
        self.assertIn(sqlite_name,allowed);self.file(sqlite_name,b'owned public SQLite package-name fixture')
        tar_name='pip_packages/tar-acl-capsule/'+json.loads(tar_raw)['package']['path']
        self.assertIn(tar_name,allowed);self.file(tar_name,b'owned public Tar package-name fixture')
        for name in expected:self.file(name,b'owned public package-name fixture')
        observed={r['path'] for r in M.input_files(self.repo,allowed)}
        self.assertEqual(observed,expected|{relative,sqlite_relative,sqlite_name,tar_relative,tar_name})
        for bad in ('../foreign.deb','nested/package.deb','foreign\\package.deb','package\n.deb'):
            changed=json.loads(raw);changed['packages'][0]['path']=bad
            payload=M.encode(changed);self.file(relative,payload)
            _,changed_metadata=M.read_file(path,65536)
            changed_source={'files':[{'path':relative,'identity':changed_metadata,'sha256':sha256(payload).hexdigest()},sqlite_row,tar_row]}
            with self.assertRaisesRegex(RuntimeError,'^input_inventory$'):
                M.public_input_names(self.repo,changed_source)

    def test_source_descriptor_budget_refuses_before_open_without_changing_limits(self):
        with ExitStack() as stack,patch.object(M.resource,'getrlimit',return_value=(3,3)),\
                patch.object(M.os,'open',side_effect=AssertionError('must refuse before open')),\
                self.assertRaisesRegex(RuntimeError,'^source_fd_budget$'):
            M.HeldSource(stack,self.repo,[{'path':'ordinary'}])

    def test_workflow_exact_scanner_and_phase_order_do_not_activate(self):
        value=yaml.safe_load((ROOT/'.github/workflows/canonical-app-image.yml').read_text());job=value['jobs']['candidate'];steps=job['steps']
        self.assertEqual(value['permissions'],{'contents':'read','actions':'read'});self.assertEqual(job['if'],"github.ref == 'refs/heads/main'")
        token=[s for s in steps if 'GH_TOKEN' in s.get('env',{})];self.assertEqual([s.get('id') for s in token],['prepare'])
        scans=[s for s in steps if s.get('uses','').startswith('aquasecurity/trivy-action@')]
        self.assertEqual(len(scans),2)
        for scan in scans:
            self.assertEqual(scan['uses'],'aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25')
            self.assertEqual(scan['with']['version'],'v0.76.1');self.assertEqual(scan['with']['image-ref'],'${{ steps.build.outputs.image_id }}')
            self.assertEqual(scan['with']['cache-dir'],'${{ steps.prepare.outputs.root }}/trivy-cache')
            self.assertEqual(scan['env']['TRIVY_IGNOREFILE'],'/dev/null')
        self.assertEqual(scans[0]['with']['ignore-unfixed'],'false')
        ids={s['id']:i for i,s in enumerate(steps) if 'id' in s}
        self.assertLess(ids['prepare'],ids['build']);self.assertLess(ids['build'],ids['finalize'])
        uploads=[i for i,s in enumerate(steps) if s.get('uses')=='actions/upload-artifact@v4']
        self.assertEqual(len(uploads),2);self.assertLess(ids['finalize'],min(uploads));self.assertLess(max(uploads),len(steps)-1)
        for step in steps:
            text=step.get('run','')
            if "<<'PY'" in text:ast.parse(text.split("<<'PY'\n",1)[1].rsplit('\nPY',1)[0])

    def test_command_actual_public_python_bounds_before_read_and_reaps_timeout(self):
        children=[];pread=os.pread
        def spawn(argv,**kwargs):
            self.assertEqual(argv[:4],[sys.executable,'-I','-B','-c']);child=REAL_POPEN(argv,**kwargs);children.append(child);return child
        def bounded(fd,count,offset):
            self.assertLessEqual(os.fstat(fd).st_size,1024);return pread(fd,count,offset)
        before=resource.getrlimit(resource.RLIMIT_FSIZE)
        with patch.object(M.subprocess,'Popen',side_effect=spawn),patch.object(M.os,'pread',side_effect=bounded):
            self.assertEqual(M.command([sys.executable,'-I','-B','-c',"print('public')"],maximum=1024),b'public\n')
            for code,expected in (("import os;os.write(1,b'x'*1025)",'command_output_bound'),('import time;time.sleep(10)','command_timeout')):
                with self.assertRaisesRegex(RuntimeError,'^'+expected+'$'):
                    M.command([sys.executable,'-I','-B','-c',code],maximum=1024,seconds=.1)
        self.assertEqual(resource.getrlimit(resource.RLIMIT_FSIZE),before)
        for child in children:
            self.assertIsNotNone(child.returncode)
            with self.assertRaises(ChildProcessError):os.waitpid(child.pid,os.WNOHANG)

if __name__=='__main__':unittest.main()
