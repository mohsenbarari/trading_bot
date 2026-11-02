# Only public source/toolchain inputs. Never a production runtime image.
FROM ubuntu:24.04@sha256:a2d43bffc46670d7108bb3c0468c67c5557c4c92e83e1e34cbff1322ad1b94dc
ENV DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
# Enable authenticated source indices ONLY inside this disposable builder.
# This prepares tools/inputs; compilation/tests run separately with network=none
# and explicit CPU/memory/PID limits, not an unconstrained Docker build RUN.
RUN timeout --signal=KILL 600 /bin/bash -ec '\
    sed -i "s/^Types: deb$/Types: deb deb-src/" /etc/apt/sources.list.d/ubuntu.sources; \
    apt-get update; \
    apt-get install --yes --no-install-recommends build-essential cmake debhelper dh-python libpython3-all-dev python3-all-dev python3-setuptools dpkg-dev patch ca-certificates; \
    mkdir -m 0700 /inputs /baseline; cd /inputs; \
    apt-get source --download-only brotli=1.1.0-2build2; \
    cd /baseline; apt-get download libbrotli1=1.1.0-2build2 python3-brotli=1.1.0-2build2 brotli=1.1.0-2build2 libbrotli-dev=1.1.0-2build2; \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*'
COPY Dockerfile build.py verify.py CVE-2026-111497.patch /recipe/
WORKDIR /work
ENTRYPOINT ["/usr/bin/python3", "-I", "-B", "/recipe/build.py"]
