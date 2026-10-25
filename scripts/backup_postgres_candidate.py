"""Opt-in public identity binding for the one reviewed PostgreSQL 15.19 image.

This control is post-LOAD evidence, not LOAD or activation authority. It never
contains runtime configuration values or changes a production container.
"""
from contextlib import contextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import resource
import stat
import subprocess
import tempfile

CONFIG = 'sha256:49a811b28ed9289d4e981c8f6cd36e1cf05259653a858e40df0cb6d6413c9aa1'
RECIPE = 'ecfb667ee1583168f2777a6fa5f54766a63ef787'
RECEIPT = '7995bb380b621119b56e58c7b7ba063884ecb888e5d323c8fffbf5a85fcbdb60'
SERVER_SHA = '1e08a0e796ae0461ec40031e9bd7a76831d453e723812b9c3e8757bc411a38c7'
SERVER_VERSION = 'postgres (PostgreSQL) 15.19'
BASELINE_CONFIG = 'sha256:aad6289ca337b3ce76896f2e7e61480490152886c7828120371fb28e6b779e1d'
WEB_APP_BASELINE = 'sha256:7bf293f93568d967c8dfcf6d95c2ff522289dc11c558989a9c6f926a644d6dfa'
WEB_MARKET_BASELINE = 'sha256:fe0737ba566a2c5b2a28f34433c0a423261900ec17b9bf7ad115e1aae7e57f1b'
BASELINE_PROOF = '4af0d3e9d694e4869c0158bde2171b339713df4e4b2dcfc75d0b5e533a9ffbdb'
LAYERS = [
 'sha256:34884abbe92863fce933ed7c39c0e045631af0ed86d5cc0dfbdf9fdca426ce3c',
 'sha256:66b9f7f9f6d3928185e0c7cad648b4b841fdc2bc80eb2cb0029a9bc4bfe6a4de',
 'sha256:a7e41a59116f2957c93dc08ecef69269070b27f237bd63fc0c0a6db072716237',
 'sha256:9b19de80e7618a2353a2498eacd0c3aa775b8656d947745a8752ec1c03bd7739',
 'sha256:9658b7b1393f1afecbea689d5c12ed9701dbbbcfe74c7bb10152e40cd3bd9aa1',
 'sha256:0cde6740c33cf0ccc77b12e6961dc3c4a4d001117a1db9a96723bfb2895465ec',
 'sha256:b1cd71796a42a83279af8905bd54a8733f05508355ddd81ad04e2a4e5b59b90f',
 'sha256:daaf5f9bd329b43986a264faefa8563443b4810d9380ccbcff3f41cce961d683',
 'sha256:ff7b413583a0d05e5dec20adf8e38685da7c0337fa8bc5fb8af4333323f5df43',
 'sha256:ea2d2c2012fff2d0532da738c475ee0e9d335d135531275b8e3844ea5f15d385',
 'sha256:363da9133d416185d21029bc178708bdf472186a612acab540f800233cd01cdb',
 'sha256:e350ec1404ae4e743ceca8e08674a0cb15a6d798387ca50fe8e85dfea99cdc2f']
SITES = {'foreign': ('65.109.216.187', 22), 'iran': ('65.109.220.59', 37067)}
SOURCES = {'foreign': {'/trading_bot_db': 'trading_bot'},
           'iran': {'/trading_bot_db': 'current',
                    '/market-private-pipeline-primary-market-database-1': 'market-private-pipeline-primary'}}
HEX = re.compile('[a-f0-9]{64}')
DOCKER = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock']


def need(ok, code):
    if not ok: raise RuntimeError(code)


def identity(s):
    return (s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)


def decode(raw):
    def unique(pairs):
        value = {}
        for k,v in pairs:
            need(k not in value, 'postgres_candidate_duplicate_key');value[k] = v
        return value
    value = json.loads(raw, object_pairs_hook=unique,
                       parse_constant=lambda _: need(False, 'postgres_candidate_json'))
    def public(v):
        if type(v) is dict:
            need(not {'Env','Config','HostConfig','environment'} & set(v), 'postgres_candidate_private_control')
            for item in v.values(): public(item)
        elif type(v) is list:
            for item in v: public(item)
    public(value)
    return value


def validate(raw, pin, site):
    need(type(raw) is bytes and 0 < len(raw) <= 65536 and type(pin) is str
         and HEX.fullmatch(pin) and sha256(raw).hexdigest() == pin, 'postgres_candidate_control_pin')
    d = decode(raw)
    fixed = {'schema':'backup_postgres_candidate_identity/1','candidate_config_id':CONFIG,
             'recipe_commit':RECIPE,'ci_run_id':40142076769,'candidate_receipt_sha256':RECEIPT,
             'baseline_public_proof_sha256':BASELINE_PROOF,
             'rootfs_layers':LAYERS,'server_sha256':SERVER_SHA,'server_version':SERVER_VERSION,
             'raw_counts':{'CRITICAL':0,'HIGH':0,'MEDIUM':0,'LOW':0,'UNKNOWN':1},
             'security_accepted':False,'raw_finding_waived':False,'configuration_values_disclosed':False}
    need(type(d) is dict and set(d) == set(fixed)|{'sites'} and all(d[k] == v for k,v in fixed.items())
         and type(d['ci_run_id']) is int and all(type(v) is int for v in d['raw_counts'].values())
         and all(d[k] is False for k in ('security_accepted','raw_finding_waived','configuration_values_disclosed')),
         'postgres_candidate_scope')
    need(site in SITES and type(d['sites']) is dict and site in d['sites']
         and set(d['sites']) <= set(SITES), 'postgres_candidate_site')
    for name, row in d['sites'].items():
        need(type(row) is dict and set(row) == {'host','port','daemon_image_id','image_loaded',
             'stable_before_after','configuration_values_disclosed','binding','source_bindings'}
             and (row['host'],row['port']) == SITES[name] and type(row['port']) is int
             and type(row['daemon_image_id']) is str and re.fullmatch('sha256:[a-f0-9]{64}',row['daemon_image_id'])
             and row['image_loaded'] is True and row['stable_before_after'] is True
             and row['configuration_values_disclosed'] is False, 'postgres_candidate_loaded_site')
        p = row['binding'];actual = row['daemon_image_id']
        need(type(p) is dict and p.get('schema') == 'runtime_image_archive_binding/1'
             and p.get('docker_image_id') == actual and p.get('scanner_config_id') == CONFIG
             and p.get('config_sha256') == CONFIG[7:] and p.get('rootfs_layers') == LAYERS
             and p.get('archive_bytes') == 303898112 and type(p.get('archive_bytes')) is int
             and type(p.get('archive_sha256')) is str and HEX.fullmatch(p['archive_sha256'])
             and p.get('security_accepted') is False and p.get('findings_waived') is False,
             'postgres_candidate_archive_binding')
        if p.get('kind') == 'Docker_config_identity':
            need(actual == CONFIG, 'postgres_candidate_config_identity')
        elif p.get('kind') in {'OCI_manifest_to_config','Docker_manifest_to_config'}:
            need(p.get('manifest_sha256') == actual[7:], 'postgres_candidate_manifest_identity')
        else:
            i = p.get('index_binding')
            need(p.get('kind') == 'OCI_index_to_manifest_to_config' and type(i) is dict
                 and i.get('root_index_sha256') == actual[7:]
                 and all(i.get(k) is False for k in ('statement_semantics_validated','signatures_verified','provenance_trusted')),
                 'postgres_candidate_index_identity')
        sources = row['source_bindings']
        need(type(sources) is dict and sources and set(sources) <= set(SOURCES[name]), 'postgres_candidate_source_scope')
        for container, choices in sources.items():
            need(type(choices) is list and 1 <= len(choices) <= 2
                 and len({v.get('mode') for v in choices if type(v) is dict}) == len(choices), 'postgres_candidate_source_modes')
            old = BASELINE_CONFIG if name == 'foreign' else WEB_APP_BASELINE if container == '/trading_bot_db' else WEB_MARKET_BASELINE
            for choice in choices:
                need(type(choice) is dict and set(choice) == {'mode','daemon_image_id','runtime_image_reference'},
                     'postgres_candidate_source_binding')
                if choice['mode'] == 'baseline':
                    need(choice['daemon_image_id'] == old and choice['runtime_image_reference'] in
                         {'postgres:15-alpine','postgres:15-alpine@'+WEB_MARKET_BASELINE,old,BASELINE_CONFIG},
                         'postgres_candidate_baseline_source')
                else:
                    need(choice['mode'] == 'candidate' and choice['daemon_image_id'] == actual
                         and choice['runtime_image_reference'] == actual, 'postgres_candidate_new_source')
    return d


def probe(argv):
    """Bound public metadata before accumulation; never return raw stderr."""
    out = os.memfd_create('pg-candidate-public');err = os.memfd_create('pg-candidate-error')
    child = None
    def limits():
        resource.setrlimit(resource.RLIMIT_FSIZE,(65537,65537))
        resource.setrlimit(resource.RLIMIT_CPU,(20,20))
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    try:
        with tempfile.TemporaryDirectory(prefix='.pg-candidate-empty-config-') as empty:
            child = subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=out,stderr=err,
                env={'PATH':'/usr/bin:/bin','LC_ALL':'C','DOCKER_CONFIG':empty},preexec_fn=limits)
            try:rc = child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                child.kill()
                try:child.wait(timeout=10)
                except subprocess.TimeoutExpired:pass
                raise RuntimeError('postgres_candidate_metadata_timeout') from None
            need(rc == 0 and os.fstat(out).st_size <= 65536 and os.fstat(err).st_size <= 65536,
                 'postgres_candidate_metadata_failed')
            return os.pread(out,65537,0).decode().strip()
    finally:
        os.close(out);os.close(err)


class Candidate:
    def __init__(self, data, pin, site, check=lambda: None):
        self.data,self.pin,self.site,self.check = data,pin,site,check
        self.image = data['sites'][site]['daemon_image_id']

    def summary(self):
        return {'control_sha256':self.pin,'site':self.site,'daemon_image_id':self.image,
                'config_sha256':CONFIG[7:],'rootfs_sha256':sha256(json.dumps(LAYERS,separators=(',',':')).encode()).hexdigest(),
                'server_sha256':SERVER_SHA,'server_version':SERVER_VERSION}

    def image_check(self, query=None, *, image=None, layers=None):
        query = probe if query is None else query
        self.check()
        image = self.image if image is None else image
        layers = LAYERS if layers is None else layers
        template = '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}},"rootfs":{{json .RootFS}}}'
        d = decode(query([*DOCKER,'image','inspect','--format',template,image]))
        need(d == {'id':image,'os':'linux','architecture':'amd64','rootfs':{'Type':'layers','Layers':layers}},
             'postgres_candidate_actual_rootfs')
        self.check()

    def runtime_check(self, document, query=None, *, restore=False):
        query = probe if query is None else query
        self.check()
        c = document.get('Config') or {};name = document.get('Name');cid = document.get('Id')
        need(type(cid) is str and re.fullmatch('[a-f0-9]{64}',cid), 'postgres_candidate_runtime_image')
        mode = 'candidate'
        if not restore:
            need(name in SOURCES[self.site] and (c.get('Labels') or {}).get('com.docker.compose.project') == SOURCES[self.site][name],
                 'postgres_candidate_runtime_name')
            choices = self.data['sites'][self.site]['source_bindings'].get(name,[])
            selected = [v for v in choices if v['daemon_image_id'] == document.get('Image')
                        and v['runtime_image_reference'] == c.get('Image')]
            need(len(selected) == 1, 'postgres_candidate_source_not_admitted');mode = selected[0]['mode']
        else:
            need(document.get('Image') == self.image and c.get('Image') == self.image,
                 'postgres_candidate_runtime_image')
        image,reference = document['Image'],c['Image']
        self.image_check(query,image=image,layers=LAYERS if mode == 'candidate' else LAYERS[:11])
        # Fixed public commands only; the in-container timeout bounds even a
        # late daemon exec. No SQL, Env dump, data or configuration values.
        guard = 'PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin; export PATH; exec timeout -s TERM -k 5 15 "$@"'
        prefix = [*DOCKER,'exec',cid,'/bin/sh','-ec',guard,'pg-candidate']
        need(query([*prefix,'sha256sum','/usr/local/bin/postgres']).split() == [SERVER_SHA,'/usr/local/bin/postgres']
             and query([*prefix,'/usr/local/bin/postgres','--version']) == SERVER_VERSION,
             'postgres_candidate_server_bytes')
        template = '{"id":{{json .Id}},"name":{{json .Name}},"image":{{json .Image}},"reference":{{json .Config.Image}}}'
        after = decode(query([*DOCKER,'container','inspect','--format',template,cid]))
        need(after == {'id':cid,'name':name,'image':image,'reference':reference}, 'postgres_candidate_runtime_changed')
        self.check()
        return {**self.summary(),'source_mode':mode,'source_daemon_image_id':image,
                'source_config_sha256':(CONFIG if mode == 'candidate' else BASELINE_CONFIG)[7:],
                'source_runtime_image_reference':reference}


@contextmanager
def bound(path, pin, site):
    need(isinstance(path,Path) and path.is_absolute() and path.resolve(strict=True) == path,
         'postgres_candidate_control_path')
    parent = os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    fd = None
    try:
        before_parent = identity(os.fstat(parent))[:5]
        need(stat.S_IMODE(before_parent[2]) == 0o700 and before_parent[3] == os.geteuid(),
             'postgres_candidate_control_parent')
        fd = os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent);s = os.fstat(fd);before = identity(s)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid() and s.st_nlink == 1
             and stat.S_IMODE(s.st_mode) == 0o600 and 0 < s.st_size <= 65536, 'postgres_candidate_control_metadata')
        def check():
            need(before == identity(os.fstat(fd)) == identity(os.stat(path.name,dir_fd=parent,follow_symlinks=False))
                 and before_parent == identity(os.fstat(parent))[:5] == identity(path.parent.lstat())[:5],
                 'postgres_candidate_control_changed')
        raw = os.pread(fd,s.st_size+1,0);check();data = validate(raw,pin,site)
        candidate = Candidate(data,pin,site,check);candidate.raw = raw
        yield candidate
        check()
    finally:
        if fd is not None:os.close(fd)
        os.close(parent)
