# Documentary source recipe only. The admitted host controller creates the
# digest-pinned disposable builder directly; it never builds a product image.
FROM python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3
WORKDIR /build
COPY . /recipe/
CMD ["/usr/local/bin/python3", "-I", "-S", "-B", "/recipe/sourcebuild.py"]
