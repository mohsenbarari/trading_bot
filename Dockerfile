FROM python:3.11.17-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf AS patched-base
# A pinned base alone does not refresh its OS security updates or CA bundle.
RUN sed -i 's#http://deb.debian.org/debian-security#https://security.debian.org/debian-security#g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true upgrade -y --no-install-recommends \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true install -y --no-install-recommends ca-certificates libpq5 libmagic1 libstdc++6 \
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
COPY pip_packages/ /tmp/pip_packages/
COPY scripts/prepare_psycopg2_source.py /tmp/prepare_psycopg2_source.py
COPY requirements.txt .
RUN if find /tmp/pip_packages -maxdepth 1 -type f -name '*.whl' | grep -q .; then \
            python -I -B /tmp/prepare_psycopg2_source.py check --cache-dir /tmp/pip_packages --requirements /app/requirements.txt; \
        else \
            python -I -B /tmp/prepare_psycopg2_source.py prepare --cache-dir /tmp/pip_packages --requirements /app/requirements.txt; \
        fi \
        && python -m pip wheel --no-cache-dir --no-index --no-deps --no-build-isolation \
            --wheel-dir=/wheelhouse /tmp/pip_packages/psycopg2_binary-2.9.13.tar.gz \
        && if find /tmp/pip_packages -maxdepth 1 -type f -name '*.whl' | grep -q .; then \
            python -m pip wheel --no-cache-dir --no-index --find-links=/tmp/pip_packages/ --find-links=/wheelhouse \
                --wheel-dir=/wheelhouse -r /tmp/pip_packages/.requirements-without-psycopg2.txt; \
        else \
            python -m pip wheel --no-cache-dir --wheel-dir=/wheelhouse -r /tmp/pip_packages/.requirements-without-psycopg2.txt; \
        fi \
        && rm -rf /tmp/pip_packages/

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
RUN python -I -B scripts/verify_image_trust.py --require-certifi
RUN python -I -B scripts/verify_psycopg2_runtime.py

COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/
