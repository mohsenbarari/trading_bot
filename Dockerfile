FROM python:3.11.17-slim-bookworm@sha256:f06d01d4ba3f8ad5af45a3ec87df19c1d317d8e8961edc0fabda61811af6f7cf
# A pinned base alone does not refresh its OS security updates or CA bundle.
RUN sed -i 's#http://deb.debian.org/debian-security#https://security.debian.org/debian-security#g' /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true update \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true upgrade -y --no-install-recommends \
    && apt-get -o Acquire::Retries=5 -o Acquire::https::No-Cache=true install -y --no-install-recommends ca-certificates libpq-dev build-essential libmagic1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --upgrade 'pip>=26.2.2' 'setuptools>=78.1.1' 'wheel>=0.46.2'
# Copy pre-downloaded packages (downloaded on fast German server)
COPY pip_packages/ /tmp/pip_packages/
COPY requirements.txt .
RUN if find /tmp/pip_packages -maxdepth 1 -type f -name '*.whl' | grep -q .; then \
            pip install --no-cache-dir --no-index --find-links=/tmp/pip_packages/ -r requirements.txt; \
        else \
            pip install --no-cache-dir -r requirements.txt; \
        fi \
        && rm -rf /tmp/pip_packages/
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

COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/
