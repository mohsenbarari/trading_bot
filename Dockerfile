FROM python:3.11-slim-bullseye
ARG RELEASE_SHA=unbound
LABEL org.opencontainers.image.revision=${RELEASE_SHA}
RUN apt-get -o Acquire::Retries=5 update \
    && apt-get -o Acquire::Retries=5 install -y --no-install-recommends libpq-dev build-essential libmagic1 \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --upgrade pip setuptools wheel
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
COPY dr_receiver_app.py .
COPY writer_witness_app.py .
COPY manage.py .
COPY run_bot.py .
COPY schemas.py .
COPY seed_fake_data.py .
COPY scripts/ ./scripts/
COPY deploy/writer-witness/001_initial.sql ./deploy/writer-witness/001_initial.sql
COPY deploy/writer-witness/002_failover_operation_ledger.sql ./deploy/writer-witness/002_failover_operation_ledger.sql
COPY deploy/writer-witness/003_human_approval_relay.sql ./deploy/writer-witness/003_human_approval_relay.sql

COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/
