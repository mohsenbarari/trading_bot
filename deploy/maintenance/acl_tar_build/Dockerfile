FROM python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3 AS runtime
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
RUN printf '%s\n' 'Types: deb deb-src' 'URIs: https://snapshot.debian.org/archive/debian/20261024T000000Z/' \
    'Suites: trixie trixie-updates' 'Components: main' 'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' 'Check-Valid-Until: no' '' \
    'Types: deb deb-src' 'URIs: https://snapshot.debian.org/archive/debian-security/20261024T000000Z/' \
    'Suites: trixie-security' 'Components: main' 'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' 'Check-Valid-Until: no' \
      > /etc/apt/sources.list.d/debian.sources \
    && timeout --signal=KILL 600 /bin/sh -ec '\
      apt-get update; apt-get install --yes --no-install-recommends libacl1=2.3.2-2+b1 tar=1.35+dfsg-3.1; \
      printf "#!/bin/sh\nexit 101\n" > /usr/sbin/policy-rc.d; chmod 0755 /usr/sbin/policy-rc.d; \
      rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*'
FROM runtime AS builder
COPY --chmod=0644 Dockerfile build.py verify.py /recipe/
# This historical Forky index supplies SOURCE ONLY; every binary index is Trixie.
RUN printf '%s\n' 'Types: deb-src' 'URIs: https://snapshot.debian.org/archive/debian/20260820T000000Z/' \
    'Suites: forky' 'Components: main' 'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' 'Check-Valid-Until: no' \
      > /etc/apt/sources.list.d/vendor-source.sources \
    && timeout --signal=KILL 1200 /bin/sh -ec '\
      apt-get update; apt-get build-dep --yes --no-install-recommends --build-profiles=noudeb --only-source acl=2.4.0-1 tar=1.35+dfsg-5; \
      apt-get install --yes --no-install-recommends build-essential dpkg-dev binutils ca-certificates pkgconf; \
      mkdir -m 0755 /inputs /baseline; cd /inputs; \
      apt-get source --download-only --only-source acl=2.4.0-1 tar=1.35+dfsg-5; \
      /usr/local/bin/python3 -I -B /recipe/build.py --verify-inputs; \
      cd /baseline; apt-get download libacl1=2.3.2-2+b1 libacl1-dev=2.3.2-2+b1 tar=1.35+dfsg-3.1; \
      dpkg-query -W > /inputs/toolchain-packages.txt; \
      useradd --uid 1001 --home-dir /work/home --no-create-home candidate-builder; \
      rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*'
USER 1001:1001
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/python3", "-I", "-B", "/recipe/build.py"]
