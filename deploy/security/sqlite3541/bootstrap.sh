#!/bin/sh
set -eu
test "$(id -u)" = 0
printf '%s  %s\n' e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7 /etc/ssl/certs/ca-certificates.crt | sha256sum --check --strict
printf '%s  %s\n' 22b557a27055b33606b6559f37703928d3e4ad79f110b407d04986e1843543d1 /recipe/snapshot-public-isrg-root-x1.crt | sha256sum --check --strict
rm -f /etc/apt/sources.list.d/*
case "$1" in
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
 if [ "$1" = trixie ]; then components=main; else components='main universe'; fi
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
apt_read 300 -o APT::Update::Error-Mode=any update
for item in $sums; do
 suite=${item%%:*}; sum=${item#*:}
 set -- /var/lib/apt/lists/*_dists_"$suite"_InRelease
 test "$#" = 1
 printf '%s  %s\n' "$sum" "$1" | sha256sum --check --strict
done
apt_read 900 -y --no-install-recommends install build-essential binutils dpkg-dev tcl8.6-dev zlib1g-dev python3 ca-certificates pkgconf libreadline-dev
apt_read 300 -y --no-install-recommends --reinstall \
 -o 'Dpkg::Options::=--path-include=/usr/share/doc/libsqlite3-0' \
 -o 'Dpkg::Options::=--path-include=/usr/share/doc/libsqlite3-0/*' \
 install "libsqlite3-0=$baseline"
mkdir -m 0700 /build/old-package
cd /build/old-package
apt_read 300 download "libsqlite3-0=$baseline"
# This saved package is only a disposable backwards-format test baseline.
# Production rollback remains the separately retained and accepted f88 package.
