#!/bin/sh
set -eu
variant=$1
export DEBIAN_FRONTEND=noninteractive
test "$(id -u)" = 0
printf '%s  %s\n' e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7 /etc/ssl/certs/ca-certificates.crt | sha256sum --check --strict
printf '%s  %s\n' 22b557a27055b33606b6559f37703928d3e4ad79f110b407d04986e1843543d1 /recipe/snapshot-public-isrg-root-x1.crt | sha256sum --check --strict
rm -f /etc/apt/sources.list.d/*
case "$variant" in
 trixie)
  url=https://snapshot.debian.org/archive/debian/20261025T000000Z
  ca=/etc/ssl/certs/ca-certificates.crt
  key=/usr/share/keyrings/debian-archive-keyring.gpg
  suites='trixie'
  sums='trixie:0584fba32e13e0ab8285fb16c27adea1ec03a73669c18702821094fd6ca86675'
  baseline=3.46.1-7+deb13u2
  ;;
 noble)
  url=https://snapshot.ubuntu.com/ubuntu/20261025T000000Z
  ca=/recipe/snapshot-public-isrg-root-x1.crt
  key=/usr/share/keyrings/ubuntu-archive-keyring.gpg
  suites='noble noble-updates noble-security'
  sums='noble:cdb2f31d809f589719a53c6ad15f255b27569c4059542ada282aaa21b8e164b0 noble-updates:c053de67f316159b441e167f3f29b3f00751b71409dc1537bb90cb86d58b2024 noble-security:d53de120a213d4e17ede2854718ed8eedfc04f3ea2e263994d7f21317f7b851b'
  baseline=3.45.1-1ubuntu2.8
  ;;
 resolute)
  url=https://snapshot.ubuntu.com/ubuntu/20261025T000000Z
  ca=/recipe/snapshot-public-isrg-root-x1.crt
  key=/usr/share/keyrings/ubuntu-archive-keyring.gpg
  suites='resolute resolute-updates resolute-security'
  sums='resolute:45f95ce276cdba3e41870516a130e03c58b8b7a79e9546b0efe9e526d255740c resolute-updates:ad66a058fbac078de33de5f589f6da0f9274be80fb891a7ee60ab05945ef096f resolute-security:66bf17a6b779051e7fb05ef23d886aa7e0274f642e2a7f0cfd8cfc9aa2361515'
  baseline=3.46.1-9ubuntu0.3
  ;;
 *) exit 2 ;;
esac
: > /etc/apt/sources.list
for suite in $suites; do
 if [ "$variant" = trixie ]; then components=main; else components='main universe'; fi
 printf 'deb [check-valid-until=no signed-by=%s] %s %s %s\n' "$key" "$url" "$suite" "$components" >> /etc/apt/sources.list
done
# Immutable release/package hashes and signed-by remain enforced on every try.
apt_read() {
 apt_limit=$1; shift
 timeout "$apt_limit" apt-get -o "Acquire::https::CaInfo=$ca" \
  -o Acquire::Retries=3 -o Acquire::https::Timeout=30 -o Acquire::http::Timeout=30 \
  -o Acquire::AllowInsecureRepositories=false -o APT::Get::AllowUnauthenticated=false \
  "$@"
}
# Preserve original snapshot failure. Ubuntu can use a fresh official signed
# archive only; TLS, signed-by, exact baseline and canonical SQLite stay fixed.
mode=immutable_snapshot
snapshot_status=0
if apt_read 300 -o APT::Update::Error-Mode=any update; then
 :
else
 snapshot_status=$?
 test "$variant" != trixie
 mode=signed_archive_fallback
 ca=/etc/ssl/certs/ca-certificates.crt
 url=https://archive.ubuntu.com/ubuntu
 : > /etc/apt/sources.list
 for suite in $suites; do
  printf 'deb [signed-by=%s] %s %s main universe\n' "$key" "$url" "$suite" >> /etc/apt/sources.list
 done
 # No stale snapshot lists may be silently reused after the source change.
 rm -f /var/lib/apt/lists/*InRelease /var/lib/apt/lists/*Packages* /var/lib/apt/lists/partial/*
 apt_read 300 -o APT::Update::Error-Mode=any update
fi
if [ "$mode" = immutable_snapshot ]; then
 for item in $sums; do
  suite=${item%%:*}; sum=${item#*:}
  set -- /var/lib/apt/lists/*_dists_"$suite"_InRelease
  test "$#" = 1
  printf '%s  %s\n' "$sum" "$1" | sha256sum --check --strict
 done
fi
# Preserve actual downloaded build DEB identities, not large payload copies in
# the export. This mutation affects only this disposable build image.
rm -f /etc/apt/apt.conf.d/docker-clean
apt_read 900 -y --no-install-recommends -o APT::Keep-Downloaded-Packages=true install build-essential binutils dpkg-dev tcl8.6-dev zlib1g-dev python3 ca-certificates pkgconf libreadline-dev
mkdir -m 0700 /build/old-package
cd /build/old-package
if [ "$variant" = trixie ]; then
 apt_read 300 download "libsqlite3-0=$baseline"
else
 # The byte/hash pins derive from the previously authenticated Ubuntu
 # Packages indexes, not from the mutable archive response. Version cannot drift.
 python3 -I -B - "$variant" <<'BASELINE'
from pathlib import Path
from hashlib import sha256
from urllib.request import urlopen
import runpy,sys,time
recipe=runpy.run_path('/recipe/build.py');p=recipe['PROFILES'][sys.argv[1]]
name='libsqlite3-0_'+p['baseline']+'_amd64.deb';target=Path(name)
url='https://archive.ubuntu.com/ubuntu/pool/main/s/sqlite3/'+name
expected=p['baseline_pin'];digest=sha256();count=0;end=time.monotonic()+120
with urlopen(url,timeout=30)as incoming,target.open('xb')as output:
 while True:
  block=incoming.read(min(65536,expected['bytes']-count+1))
  if not block:break
  count+=len(block)
  if time.monotonic()>end or count>expected['bytes']:raise RuntimeError('baseline_download_bound')
  digest.update(block);output.write(block)
if {'bytes':count,'sha256':digest.hexdigest()}!=expected:raise RuntimeError('baseline_package_identity')
BASELINE
fi
python3 -I -B - "$variant" <<'VERIFY_BASELINE'
from pathlib import Path
import runpy,sys
recipe=runpy.run_path('/recipe/build.py');p=recipe['PROFILES'][sys.argv[1]]
files=list(Path('.').glob('libsqlite3-0_*.deb'))
if len(files)!=1 or recipe['pin'](files[0])!=p['baseline_pin']:raise RuntimeError('baseline_package_identity')
VERIFY_BASELINE
apt_read 300 -y --no-install-recommends --allow-downgrades --reinstall \
 -o 'Dpkg::Options::=--path-include=/usr/share/doc/libsqlite3-0' \
 -o 'Dpkg::Options::=--path-include=/usr/share/doc/libsqlite3-0/*' \
 install /build/old-package/libsqlite3-0_*.deb
# Public, compact reproducibility evidence: all actual signed indexes, exact
# installed versions, and hashes of actual APT-downloaded build dependencies.
python3 -I -B - "$variant" "$mode" "$snapshot_status" <<'BUILD_INPUTS'
from pathlib import Path
from hashlib import sha256
import json,shutil,subprocess,sys
out=Path('/build/build-inputs');out.mkdir(mode=0o755)
def pin(p):
 h=sha256();count=0
 with p.open('rb')as f:
  while True:
   block=f.read(65536)
   if not block:break
   count+=len(block)
   if count>256*1024**2:raise RuntimeError('public_APT_input_bound')
   h.update(block)
 return {'name':p.name,'bytes':count,'sha256':h.hexdigest()}
indexes=[]
for p in sorted(Path('/var/lib/apt/lists').iterdir()):
 if p.is_file()and(p.name.endswith('InRelease')or'Packages'in p.name):
  indexes.append(pin(p))
  if p.name.endswith('InRelease'):shutil.copyfile(p,out/p.name)
if not indexes or not any(p['name'].endswith('InRelease')for p in indexes):raise RuntimeError('actual_signed_APT_indexes_missing')
versions=subprocess.run(['/usr/bin/dpkg-query','-W','-f=${binary:Package}\t${Version}\t${Architecture}\n'],check=True,capture_output=True,text=True,timeout=30).stdout
raw=json.dumps({'schema':'sqlite3541_actual_build_inputs/1','variant':sys.argv[1],'repository_mode':sys.argv[2],
 'snapshot_update_actual_returncode':int(sys.argv[3]),'APT_authentication_and_TLS_required':True,
 'actual_sources':Path('/etc/apt/sources.list').read_text(),'actual_indexes':indexes,
 'installed_packages':[line.split('\t')for line in versions.splitlines()],
 'actual_downloaded_build_packages':[pin(p)for p in sorted(Path('/var/cache/apt/archives').glob('*.deb'))],
 'baseline_package':[pin(p)for p in Path('/build/old-package').glob('*.deb')],
 'security_accepted':False},sort_keys=True,indent=2)+'\n'
if len(raw.encode())>4*1024**2:raise RuntimeError('build_input_receipt_bound')
(out/'provenance.json').write_text(raw)
BUILD_INPUTS
# Production rollback remains separately retained accepted f88, not this fixture.
