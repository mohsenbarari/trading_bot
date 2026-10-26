# Disposable package builder only. Root/CI builds after source review.
# Build ONLY the fresh allowlisted context emitted by --stage-context, never
# this historical source directory. Its deny-all.dockerignore excludes history.
FROM python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3 AS build
ENV PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8 LC_ALL=C.UTF-8 DEBIAN_FRONTEND=noninteractive
WORKDIR /build
COPY sourcebuild.py Dockerfile context-manifest.json verify_positive.py 0001-wal-reset-production-only.patch /recipe/
COPY inputs/sqlite3_3.46.1-7+deb13u2.dsc inputs/sqlite3_3.46.1.orig.tar.xz inputs/sqlite3_3.46.1.orig-www.tar.xz inputs/sqlite3_3.46.1-7+deb13u2.debian.tar.xz /inputs/
RUN /usr/local/bin/python3 -I -B /recipe/sourcebuild.py --configure-build-repository
# No network is available to source extraction, patching, compilation,
# installation or ordinary positive compatibility tests.
RUN --network=none /usr/local/bin/python3 -I -B /recipe/sourcebuild.py --build

# Export only the proper runtime package and public provenance. Never copy a
# compiler, extracted-library loader tree, Python replacement or customer data.
FROM scratch AS package-export
COPY --from=build /out/ /
