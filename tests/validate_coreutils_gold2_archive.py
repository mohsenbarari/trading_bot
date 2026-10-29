"""Read-only author QA of an explicitly supplied authentic PUBLIC CI ZIP.

This invokes the actual current lock and archive validators. It never installs
the package, executes a candidate binary, downloads an artifact or builds an
image. Ordinary unit negative fixtures remain explicitly synthetic.
"""
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import argparse,json

ROOT=Path(__file__).resolve().parents[1]

def main(argv=None):
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('public_package_zip',type=Path)
 args=parser.parse_args(argv)
 path=ROOT/'scripts/prepare_coreutils_acl_os_cache.py'
 helper=ModuleType('owned_authentic_gold2_archive');helper.__file__=str(path)
 exec(compile(path.read_bytes(),str(path),'exec'),helper.__dict__)
 with helper.Held()as held:
  lock=helper.load_lock(held)
  row=held.read(args.public_package_zip,helper.MAX_ZIP,
   pin={k:lock['artifact'][k]for k in('bytes','sha256')})
  package=helper.admit_archive(row.raw,lock)
  held.check()
  result={'schema':'owned_authentic_coreutils_gold2_archive_QA/1',
   'status':'PASS','archive':row.binding,'package':{'bytes':len(package),'sha256':sha256(package).hexdigest()},
   'lock_sha256':helper.LOCK_SHA,'artifact_id':lock['artifact']['id'],'run_id':lock['artifact']['run_id'],
   'ordinary_cli_cases':len(helper.CLI_CASES),'required_native_fixtures':3,
   'parent_patch':dict(helper.PARENT_PATCH),'parent_cp':dict(helper.PARENT_CP),
   'package_installed':False,'candidate_binary_executed':False,'security_accepted':False}
 print(json.dumps(result,sort_keys=True))
 return 0

if __name__=='__main__':raise SystemExit(main())
