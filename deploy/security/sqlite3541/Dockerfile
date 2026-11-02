# Build only on the disposable GitHub-hosted matrix. No host installation.
ARG BASE_IMAGE
FROM python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3 AS public-ca
FROM ${BASE_IMAGE} AS build
ARG SQLITE_VARIANT
ENV PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8 LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive
WORKDIR /build
COPY --from=public-ca /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY deploy/security/sqlite3541/ /recipe/
COPY inputs/sqlite-src-3540100.zip /inputs/sqlite-src-3540100.zip
RUN /bin/sh /recipe/bootstrap.sh "$SQLITE_VARIANT"
# Canonical extraction, generation, compilation, tests and dpkg installation
# have no network and use no production mount, key, session or user data.
RUN --network=none python3 -I -B /recipe/build.py --build "$SQLITE_VARIANT"
FROM scratch AS package-export
COPY --from=build /out/ /
