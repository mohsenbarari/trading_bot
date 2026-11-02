"""Synthetic image scopes with real public verifier Source; no live collection."""
from pathlib import Path
from types import ModuleType
from copy import deepcopy
from hashlib import sha256
import json,unittest

ROOT=Path(__file__).absolute().parents[1]
C=ModuleType('owned_existing_assessor_fixtures');C.__file__=str(ROOT/'tests/test_assess_image_security_evidence.py')
exec(compile(Path(C.__file__).read_bytes(),C.__file__,'exec'),C.__dict__)
VERIFIER=(ROOT/'scripts/verify_image_component_presence.py').read_bytes()

def refresh(packet,actual):
    name='owned-component-original.json';packet.inputs[name]=C.object_bytes(actual)
    for proof_name in ('homed.json','perl.json'):
        packet.proofs[proof_name]['original_inputs'][name]=C.pin(packet.inputs[name])

def packet():
    p=C.Packet();p.proofs['payload.json']['observations']['packages']=[item for item in p.proofs['payload.json']['observations']['packages']if item['package']not in{'libsystemd0','libudev1','perl-base'}]
    p.inputs['reviewed-component-verifier.py']=VERIFIER
    actual={'schema':'security_image_component_presence/1','image_id':p.scope['image_id'],'source_commit':p.scope['source_commit'],
        'verifier_sha256':sha256(VERIFIER).hexdigest(),'packages':{name:'OWNED-SYNTHETIC-1'for name in('libsystemd0','libudev1','perl-base')},
        'inventory':{'entries_examined':1000,'roots':['/app','/etc','/opt','/usr'],'matches':{'systemd_homed':[],'archive_tar':[]}},
        'perl_inc':['/etc/perl','/usr/share/perl/5.40'],'perl_inc_archive_tar_matches':[],
        'components':{'systemd_homed':{'advisory':'CVE-2026-16742','component_absent':True},'archive_tar':{'advisory':'CVE-2026-9538','component_absent':True}},
        'scope':'known_vendor_component_paths_and_names_plus_actual_perl_inc',
        **{key:False for key in('directory_symlinks_followed','dynamic_runtime_code_downloads_analyzed','renamed_or_obfuscated_embedded_code_analyzed','production_mounts_or_data_used','scanner_findings_modified','security_accepted')}}
    for name in ('homed.json','perl.json'):
        p.descriptors[name]['original_inputs']+=['reviewed-component-verifier.py','owned-component-original.json']
        p.proofs[name]['original_inputs']['reviewed-component-verifier.py']=C.pin(VERIFIER)
    refresh(p,actual);return p,actual

class ComponentAbsenceWithoutDebTests(unittest.TestCase):
    def refuses(self,p,code):
        calls=[]
        with self.assertRaisesRegex(C.A.Refused,code):p.run(lambda:calls.append(True))
        self.assertFalse(calls)

    def test_exact_reviewed_absence_resolves_without_inventing_base_DEB_payloads(self):
        p,_=packet();value=p.run();self.assertEqual(value['status'],'PASS');self.assertEqual(value['unresolved_count'],0)
        self.assertEqual(value['raw_counts']['HIGH'],44);self.assertFalse(value['raw_gate_passed']);self.assertFalse(value['security_accepted'])
        self.assertFalse({'libsystemd0','libudev1','perl-base'}&{item['package']for item in p.proofs['payload.json']['observations']['packages']})

    def test_other_image_release_verifier_or_installed_version_cannot_be_reused(self):
        for edit in ('image','release','verifier','version'):
            p,actual=packet()
            if edit=='image':actual['image_id']='sha256:'+'f'*64
            elif edit=='release':actual['source_commit']='f'*40
            elif edit=='verifier':actual['verifier_sha256']='f'*64
            else:actual['packages']['libsystemd0']='OTHER'
            refresh(p,actual)
            with self.subTest(edit=edit):self.refuses(p,'component_absence_(identity|installed)')

    def test_incomplete_inventory_or_present_implementation_cannot_resolve(self):
        for edit in ('roots','count','boolean_count','match','component','perl_module'):
            p,actual=packet()
            if edit=='roots':actual['inventory']['roots'].remove('/opt')
            elif edit=='count':actual['inventory']['entries_examined']=100001
            elif edit=='boolean_count':actual['inventory']['entries_examined']=True
            elif edit=='match':actual['inventory']['matches']['systemd_homed']=['/usr/lib/systemd/systemd-homed']
            elif edit=='component':actual['components']['systemd_homed']['component_absent']=False
            else:actual['perl_inc_archive_tar_matches']=['/usr/share/perl/Archive/Tar.pm']
            refresh(p,actual)
            with self.subTest(edit=edit):self.refuses(p,'component_absence_(roots|present|perl_present)')

    def test_missing_or_changed_verifier_and_missing_duplicate_original_refuse(self):
        for edit in ('source_missing','source_changed','original_missing','duplicate'):
            p,actual=packet()
            if edit=='source_changed':
                p.inputs['reviewed-component-verifier.py']+=b'\n# UNREVIEWED\n'
                for name in ('homed.json','perl.json'):p.proofs[name]['original_inputs']['reviewed-component-verifier.py']=C.pin(p.inputs['reviewed-component-verifier.py'])
            elif edit=='duplicate':
                name='duplicate-component-original.json';p.inputs[name]=C.object_bytes(actual)
                for proof in('homed.json','perl.json'):
                    p.descriptors[proof]['original_inputs'].append(name);p.proofs[proof]['original_inputs'][name]=C.pin(p.inputs[name])
            else:
                name='reviewed-component-verifier.py'if edit=='source_missing'else'owned-component-original.json'
                for proof in('homed.json','perl.json'):
                    p.descriptors[proof]['original_inputs'].remove(name);p.proofs[proof]['original_inputs'].pop(name)
            with self.subTest(edit=edit):self.refuses(p,'component_absence_(verifier|original)')

    def test_absence_facts_remain_typed_and_fail_closed(self):
        for key,value in (('absent',False),('inventory_complete',False),('unreadable_inputs',False),('unreadable_inputs',1),('limit_exceeded',True)):
            p,_=packet();p.proofs['homed.json']['observations'][key]=value
            with self.subTest(key=key,value=value):self.refuses(p,'component_absence_facts')

    def test_fixed_util_ncurses_and_acl_packages_still_require_real_native_payload(self):
        for name in('util-linux','libacl1','libncursesw6'):
            p,_=packet();p.proofs['payload.json']['observations']['packages']=[item for item in p.proofs['payload.json']['observations']['packages']if item['package']!=name]
            with self.subTest(package=name):self.refuses(p,'native_payload_missing_or_ambiguous')

    def test_actual_perl_include_roots_and_scope_flags_are_required(self):
        for edit in ('empty_inc','relative_inc','duplicate_inc','private_data','follow_symlinks'):
            p,actual=packet()
            if edit=='empty_inc':actual['perl_inc']=[]
            elif edit=='relative_inc':actual['perl_inc']=['RELATIVE']
            elif edit=='duplicate_inc':actual['perl_inc']=['/usr/share/perl','/usr/share/perl']
            elif edit=='private_data':actual['production_mounts_or_data_used']=True
            else:actual['directory_symlinks_followed']=True
            refresh(p,actual)
            with self.subTest(edit=edit):self.refuses(p,'component_absence_(perl_inc|scope)')

    def test_original_product_documents_remain_required_for_absence_proof(self):
        p,_=packet();name=C.PRODUCT_NAMES['build'];p.descriptors['homed.json']['original_inputs'].remove(name);p.proofs['homed.json']['original_inputs'].pop(name)
        self.refuses(p,'product_proof_originals')

if __name__=='__main__':unittest.main()
