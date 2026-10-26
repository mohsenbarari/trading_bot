FROM python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3
COPY --chmod=0600 sourcebuild.py verify_ordinary.py 0001-tar-descriptor-acl-caller.patch debian-local-changelog.patch source-manifest.json ordinary_acl_fd_fixture.c /recipe/
COPY --chmod=0600 inputs/libacl1_2.4.0-1~trixie+gold1_amd64.deb inputs/tar_1.35+dfsg-5~trixie+gold1_amd64.deb /inputs/
WORKDIR /build
# No unbounded Dockerfile RUN build: reviewed native runner controls resources,
# signed preparation, actual network cutoff and SDK/Tar sequencing.
ENTRYPOINT ["/usr/local/bin/python3", "-I", "-S", "-B", "/recipe/sourcebuild.py"]
