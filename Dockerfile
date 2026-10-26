FROM python:3.11.17-slim-trixie@sha256:b95327ce1439e687237841b0210677aeadd62d55a060a5f6bcb9c15bf6251dd3 AS patched-base
# A pinned base alone does not refresh its OS security updates or CA bundle.
RUN sed -i 's#http://deb.debian.org/debian-security#https://security.debian.org/debian-security#g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true upgrade -y --no-install-recommends \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true install -y --no-install-recommends ca-certificates libpq5 libmagic1t64 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir --upgrade 'pip>=26.2.2' 'setuptools>=78.1.1' 'wheel>=0.46.2'

# Native dependencies may compile at build time, never in the serving image.
# Both stages share the exact patched Python/OS base and target architecture.
FROM patched-base AS wheel-builder
RUN apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true install -y --no-install-recommends "libpq-dev=$(dpkg-query -W -f='${Version}' libpq5)" build-essential curl \
    && rm -rf /var/lib/apt/lists/*
# Copy pre-downloaded packages (downloaded on fast German server)
# Source admission requires non-writable ancestors; /tmp is intentionally 1777.
COPY pip_packages/ /opt/pip_packages/
COPY scripts/prepare_psycopg2_source.py /tmp/prepare_psycopg2_source.py
COPY requirements.txt .
RUN if find /opt/pip_packages -maxdepth 1 -type f -name '*.whl' | grep -q .; then \
            python -I -B /tmp/prepare_psycopg2_source.py check --cache-dir /opt/pip_packages --requirements /app/requirements.txt; \
        else \
            python -I -B /tmp/prepare_psycopg2_source.py prepare --cache-dir /opt/pip_packages --requirements /app/requirements.txt; \
        fi \
        && python -m pip wheel --no-cache-dir --no-index --no-deps --no-build-isolation \
            --wheel-dir=/wheelhouse /opt/pip_packages/psycopg2_binary-2.9.13.tar.gz \
        && if find /opt/pip_packages -maxdepth 1 -type f -name '*.whl' | grep -q .; then \
            python -m pip wheel --no-cache-dir --no-index --find-links=/opt/pip_packages/ --find-links=/wheelhouse \
                --wheel-dir=/wheelhouse -r /opt/pip_packages/.requirements-without-psycopg2.txt; \
        else \
            python -m pip wheel --no-cache-dir --wheel-dir=/wheelhouse -r /opt/pip_packages/.requirements-without-psycopg2.txt; \
        fi \
        && rm -rf /opt/pip_packages/

FROM patched-base AS runtime
COPY requirements.txt .
COPY --from=wheel-builder /wheelhouse/ /tmp/wheelhouse/
RUN pip install --no-cache-dir --no-index --only-binary=:all: --find-links=/tmp/wheelhouse/ -r requirements.txt \
    && python -m pip check \
    && rm -rf /tmp/wheelhouse/
ARG FRONTEND_DIST_DIR=mini_app_dist
COPY api/ ./api/
COPY bot/ ./bot/
COPY core/ ./core/
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY models/ ./models/
COPY templates/ ./templates/
COPY fonts/ ./fonts/
COPY alembic.ini .
COPY main.py .
COPY manage.py .
COPY run_bot.py .
COPY schemas.py .
COPY seed_fake_data.py .
COPY trading_settings.json .
COPY scripts/ ./scripts/
RUN --network=none \
    --mount=type=bind,source=pip_packages/security-os-capsule,target=/capsule,ro \
    --mount=type=tmpfs,target=/tmp \
    ["/usr/bin/setpriv", "--bounding-set=-all,+chown,+fowner,+fsetid,+setuid,+setgid", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs", "/usr/local/bin/python3", "-I", "-B", "/app/scripts/prepare_app_security_os_cache.py", "--install-image-cache", "--cache-dir", "/capsule"]
RUN --network=none \
    --mount=type=bind,source=pip_packages/sqlite-wal-capsule,target=/sqlite-capsule,ro \
    --mount=type=tmpfs,target=/tmp \
    ["/usr/bin/setpriv", "--bounding-set=-all,+chown,+fowner,+fsetid,+setuid,+setgid", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs", "/usr/local/bin/python3", "-I", "-B", "/app/scripts/prepare_sqlite_wal_os_cache.py", "--install-image-cache", "--cache-dir", "/sqlite-capsule"]
RUN --network=none \
    --mount=type=bind,source=pip_packages/tar-acl-capsule,target=/tar-capsule,ro \
    --mount=type=tmpfs,target=/tmp \
    ["/usr/bin/setpriv", "--bounding-set=-all,+chown,+fowner,+fsetid,+setuid,+setgid", "--inh-caps=-all", "--ambient-caps=-all", "--no-new-privs", "/usr/local/bin/python3", "-I", "-B", "/app/scripts/prepare_tar_acl_os_cache.py", "--install-image-cache", "--cache-dir", "/tar-capsule"]
RUN python -I -B scripts/verify_image_trust.py --require-certifi
RUN python -I -B scripts/verify_psycopg2_runtime.py
RUN python -I -B scripts/verify_sqlite_runtime.py --require-libmagic
RUN python -I -B scripts/remove_runtime_pip.py --image-build \
    && python -I -B scripts/verify_psycopg2_runtime.py --final-runtime

COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/

# Mount ancestors belong to the immutable image, never its writable runtime layer.
RUN --network=none test "$(id -u):$(id -g)" = "0:0" \
    && test ! -e /app/runtime && test ! -L /app/runtime \
    && install -d -m 0755 -o 0 -g 0 /app/runtime \
        /app/runtime/coin-inference /app/runtime/product-estimator
