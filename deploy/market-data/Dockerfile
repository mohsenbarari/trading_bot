# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
ARG PYTHON_BASE_IMAGE=python:3.11-slim-bookworm@sha256:0bee7276f83efd4a1ee05bbbf4281d95ed28e079220a9457f25a93e3f1e3c31b

FROM ${PYTHON_BASE_IMAGE} AS dependencies

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
COPY deploy/market-data/requirements.lock ./requirements.lock
RUN python -m pip install \
        --no-compile \
        --only-binary=:all: \
        --require-hashes \
        --target=/opt/market-python \
        --requirement requirements.lock

FROM ${PYTHON_BASE_IMAGE} AS runtime

ARG SOURCE_SHA
ARG IMAGE_VERSION=stage3-foundation

LABEL org.opencontainers.image.title="Trading Bot Private Market Pipeline" \
      org.opencontainers.image.description="Docker-native private market data pipeline" \
      org.opencontainers.image.revision="${SOURCE_SHA}" \
      org.opencontainers.image.version="${IMAGE_VERSION}"

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app:/opt/market-python \
    MARKET_PIPELINE_IMAGE_REVISION="${SOURCE_SHA}"

RUN groupadd --gid 10001 market \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin market \
    && mkdir -p /app /var/lib/market-data/state \
    && chown -R 10001:10001 /var/lib/market-data

COPY --from=dependencies /opt/market-python /opt/market-python
WORKDIR /app
COPY core/__init__.py /app/core/__init__.py
COPY core/market_intelligence /app/core/market_intelligence
COPY config/market_data_sources.v1.json /app/config/market_data_sources.v1.json
COPY contracts/market_data /app/contracts/market_data
COPY deploy/market-data/migrations /app/deploy/market-data/migrations

USER 10001:10001
ENTRYPOINT ["python", "-m", "core.market_intelligence.private_pipeline_foundation"]
CMD ["service", "--role", "market-processor"]
