# Public disposable Trixie source candidate only. No production application.
FROM python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3 AS runtime
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
RUN printf '%s\n' \
    'Types: deb deb-src' \
    'URIs: https://snapshot.debian.org/archive/debian/20261024T000000Z/' \
    'Suites: trixie trixie-updates' 'Components: main' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
    'Check-Valid-Until: no' '' \
    'Types: deb deb-src' \
    'URIs: https://snapshot.debian.org/archive/debian-security/20261024T000000Z/' \
    'Suites: trixie-security' 'Components: main' \
    'Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg' \
    'Check-Valid-Until: no' > /etc/apt/sources.list.d/debian.sources \
    && timeout --signal=KILL 600 /bin/sh -ec '\
      apt-get update; apt-get install --yes --no-install-recommends \
      bsdutils=1:2.41.5-0+deb13u1 libblkid1=2.41.5-0+deb13u1 \
      liblastlog2-2=2.41.5-0+deb13u1 libmount1=2.41.5-0+deb13u1 \
      libsmartcols1=2.41.5-0+deb13u1 libuuid1=2.41.5-0+deb13u1 \
      login=1:4.16.0-2+really2.41.5-0+deb13u1 mount=2.41.5-0+deb13u1 \
      util-linux=2.41.5-0+deb13u1; \
      printf "#!/bin/sh\nexit 101\n" > /usr/sbin/policy-rc.d; chmod 0755 /usr/sbin/policy-rc.d; \
      rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*'

FROM runtime AS builder
COPY --chmod=0644 Dockerfile build.py verify.py source_regressions.py /recipe/
# Only dependency/input preparation has network. Compilation is a separate
# nonroot, read-only, no-network container with CPU/RAM/PID/time limits.
RUN timeout --signal=KILL 900 /bin/sh -ec '\
      apt-get update; \
      apt-get build-dep --yes --no-install-recommends --build-profiles=noudeb util-linux=2.41.5-0+deb13u1; \
      apt-get install --yes --no-install-recommends build-essential dpkg-dev quilt \
        python3 binutils e2fsprogs ca-certificates; \
      mkdir -m 0755 /inputs /baseline; cd /baseline; \
      apt-get download bsdutils=1:2.41.5-0+deb13u1 \
        login=1:4.16.0-2+really2.41.5-0+deb13u1 \
        util-linux=2.41.5-0+deb13u1 mount=2.41.5-0+deb13u1 \
        libblkid1=2.41.5-0+deb13u1 liblastlog2-2=2.41.5-0+deb13u1 \
        libmount1=2.41.5-0+deb13u1 libsmartcols1=2.41.5-0+deb13u1 \
        libuuid1=2.41.5-0+deb13u1 libfdisk1=2.41.5-0+deb13u1 \
        libblkid-dev=2.41.5-0+deb13u1 libmount-dev=2.41.5-0+deb13u1 \
        libsmartcols-dev=2.41.5-0+deb13u1 liblastlog2-dev=2.41.5-0+deb13u1 \
        libfdisk-dev=2.41.5-0+deb13u1 uuid-dev=2.41.5-0+deb13u1; \
      /usr/local/bin/python3 -I -B /recipe/build.py --fetch-inputs; \
      dpkg-query -W > /inputs/toolchain-packages.txt; \
      useradd --uid 1001 --home-dir /work/home --no-create-home candidate-builder; \
      rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*'
USER 1001:1001
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/python3", "-I", "-B", "/recipe/build.py"]
