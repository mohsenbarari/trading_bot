# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
ARG PYTHON_BASE_IMAGE=python:3.11.17-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf
ARG SOURCE_DATE_EPOCH=0

FROM ${PYTHON_BASE_IMAGE} AS patched-base
RUN sed -i 's#http://deb.debian.org/debian-security#https://security.debian.org/debian-security#g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true upgrade -y --no-install-recommends \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true install -y --no-install-recommends ca-certificates libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/verify_image_trust.py /usr/local/libexec/verify_image_trust.py
COPY scripts/verify_psycopg2_runtime.py /usr/local/libexec/verify_psycopg2_runtime.py
COPY deploy/production/pip-bootstrap-requirements.txt /tmp/pip-bootstrap-requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/pip-bootstrap-requirements.txt \
    && rm /tmp/pip-bootstrap-requirements.txt
RUN python -I -B /usr/local/libexec/verify_image_trust.py

FROM patched-base AS assembly
RUN apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true install -y --no-install-recommends "libpq-dev=$(dpkg-query -W -f='${Version}' libpq5)" build-essential \
    && rm -rf /var/lib/apt/lists/*

ARG SOURCE_DATE_EPOCH

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY deploy/market-data/requirements.lock ./requirements.lock
RUN python -m pip install \
        --no-compile \
        --no-build-isolation \
        --only-binary=:all: \
        --no-binary=pyaes,psycopg2-binary \
        --require-hashes \
        --target=/rootfs/opt/market-python \
        --requirement requirements.lock

COPY core/__init__.py /rootfs/app/core/__init__.py
COPY core/market_intelligence /rootfs/app/core/market_intelligence
COPY config/market_data_sources.v1.json /rootfs/app/config/market_data_sources.v1.json
COPY contracts/market_data /rootfs/app/contracts/market_data
COPY deploy/market-data/migrations /rootfs/app/deploy/market-data/migrations
RUN mkdir -p /rootfs/var/lib/market-data/state \
    && chown -R 10001:10001 /rootfs/app /rootfs/var/lib/market-data \
    && find /rootfs -exec touch -h -d "@${SOURCE_DATE_EPOCH}" {} +

FROM patched-base AS runtime

ARG SOURCE_SHA
ARG SOURCE_TREE
ARG IMAGE_INPUT_SIGNATURE
ARG IMAGE_VERSION=stage5-coin-parser-shadow

LABEL org.opencontainers.image.title="Trading Bot Private Market Pipeline" \
      org.opencontainers.image.description="Docker-native private market data pipeline" \
      org.opencontainers.image.revision="${SOURCE_SHA}" \
      org.opencontainers.image.version="${IMAGE_VERSION}" \
      io.gold-trade.release.tree="${SOURCE_TREE}" \
      io.gold-trade.release.input-signature="${IMAGE_INPUT_SIGNATURE}"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/opt/market-python \
    MARKET_PIPELINE_IMAGE_REVISION="${SOURCE_SHA}"

COPY --from=assembly /rootfs/ /
WORKDIR /app
RUN python -I -B /usr/local/libexec/verify_psycopg2_runtime.py --package-root /opt/market-python

USER 10001:10001
ENTRYPOINT ["python", "-m", "core.market_intelligence.private_pipeline_foundation"]
CMD ["service", "--role", "market-processor"]
