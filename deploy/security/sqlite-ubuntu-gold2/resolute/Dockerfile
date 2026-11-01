# Native disposable Ubuntu package builder, never a host install.
# Build only a new exact context emitted by --stage-context.
# Portable public trust anchors only; no foreign .deb or executable.
FROM --platform=linux/amd64 python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3 AS public-ca
FROM --platform=linux/amd64 ubuntu:26.04@sha256:42527649dbc4f9197a6073f426f488cbff0e9cc9f2afce32d91c383e4875634a AS build
ENV PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8 LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive
WORKDIR /build
COPY --from=public-ca /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY sourcebuild.py Dockerfile context-manifest.json verify_positive.py consumer-profile.json source-signer.asc 0001-wal-reset-production-only.patch 0002-fts5-detail-none-merge-bound.patch 0003-fts5-detail-none-upstream-regression.patch /recipe/
COPY inputs/sqlite3_3.46.1-9ubuntu0.3.debian.tar.xz inputs/sqlite3_3.46.1-9ubuntu0.3.dsc inputs/sqlite3_3.46.1.orig-www.tar.xz inputs/sqlite3_3.46.1.orig.tar.xz /inputs/
# Minimal Ubuntu has no Python. Its native signed APT bootstrap verifies EVERY
# immutable release hash before installing Python or any build dependency.
RUN test -f /etc/ssl/certs/ca-certificates.crt && test ! -L /etc/ssl/certs/ca-certificates.crt \
    && test "$(stat -c %s /etc/ssl/certs/ca-certificates.crt)" -eq 222073 \
    && printf '%s  %s\n' 'e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7' /etc/ssl/certs/ca-certificates.crt | sha256sum --check --strict \
    && chmod 0444 /etc/ssl/certs/ca-certificates.crt \
    && test "$(stat -c '%a:%u:%g' /etc/ssl/certs/ca-certificates.crt)" = '444:0:0' \
    && printf '%s\n' '{"bytes":222073,"data_only":true,"foreign_package_or_binary_copied":false,"mode":"0444","schema":"ubuntu_public_ca_bootstrap/1","sha256":"e9c7b63ee2623386e9b0a3ee32f499d01a8ea23dc83b8383ddff9f8e749543b7","source_image":"python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3","source_path":"/etc/ssl/certs/ca-certificates.crt"}' > /build/ca-bootstrap-public.json \
    && chmod 0444 /build/ca-bootstrap-public.json \
    && rm -f /etc/apt/sources.list.d/* \
    && printf '%b' \
    'deb [check-valid-until=no signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261025T000000Z resolute main universe\ndeb [check-valid-until=no signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261025T000000Z resolute-updates main universe\ndeb [check-valid-until=no signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] https://snapshot.ubuntu.com/ubuntu/20261025T000000Z resolute-security main universe\n' > /etc/apt/sources.list \
    && apt-get -o Acquire::Retries=0 -o Acquire::https::CaInfo=/etc/ssl/certs/ca-certificates.crt -o APT::Update::Error-Mode=any update \
    && set -- /var/lib/apt/lists/*_dists_resolute_InRelease && test "$#" -eq 1 && printf '%s  %s\n' '45f95ce276cdba3e41870516a130e03c58b8b7a79e9546b0efe9e526d255740c' "$1" | sha256sum --check --strict \
    && set -- /var/lib/apt/lists/*_dists_resolute-updates_InRelease && test "$#" -eq 1 && printf '%s  %s\n' 'ad66a058fbac078de33de5f589f6da0f9274be80fb891a7ee60ab05945ef096f' "$1" | sha256sum --check --strict \
    && set -- /var/lib/apt/lists/*_dists_resolute-security_InRelease && test "$#" -eq 1 && printf '%s  %s\n' '66bf17a6b779051e7fb05ef23d886aa7e0274f642e2a7f0cfd8cfc9aa2361515' "$1" | sha256sum --check --strict \
    && apt-get -o Acquire::https::CaInfo=/etc/ssl/certs/ca-certificates.crt -y --no-install-recommends install build-essential dpkg-dev debhelper autoconf libtool automake pkgconf chrpath lynx libreadline-dev tcl8.6-dev libicu-dev quilt ubuntu-keyring gnupg gpgv binutils python3 ca-certificates \
    && apt-get -o Acquire::https::CaInfo=/etc/ssl/certs/ca-certificates.crt -y --no-install-recommends --reinstall \
       -o 'Dpkg::Options::=--path-include=/usr/share/doc/libsqlite3-0' \
       -o 'Dpkg::Options::=--path-include=/usr/share/doc/libsqlite3-0/*' \
       install libsqlite3-0=3.46.1-9ubuntu0.3 \
    && /usr/bin/python3.14 -I -B /recipe/sourcebuild.py --configure-build-repository
RUN --network=none /usr/bin/python3.14 -I -B /recipe/sourcebuild.py --build
FROM scratch AS package-export
COPY --from=build /out/ /
