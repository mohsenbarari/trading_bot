FROM python:3.11-slim-bullseye
# These provenance inputs are deliberately optional for ordinary local/CI
# builds.  A Release-0 Fenced-FI candidate, however, must supply all three:
# preflight refuses images whose labels do not exactly bind its signed source
# capability evidence.
ARG TERM_FENCED_RELEASE_SHA
ARG TERM_FENCED_RELEASE_TREE_SHA
ARG TERM_FENCED_APPLICATION_EVIDENCE_SHA256
ARG FENCED_FI_BUILD_INPUT_MANIFEST_SHA256
ARG MINI_APP_DIST_MANIFEST_SHA256
ARG MINI_APP_DIST_FILES_SHA256
ARG MINI_APP_DIST_FILE_COUNT
ARG MINI_APP_DIST_TOTAL_BYTES
LABEL org.opencontainers.image.revision="${TERM_FENCED_RELEASE_SHA}" \
      org.goldtrade.source-tree="${TERM_FENCED_RELEASE_TREE_SHA}" \
      org.goldtrade.term-fence-evidence-sha256="${TERM_FENCED_APPLICATION_EVIDENCE_SHA256}" \
      org.goldtrade.fenced-fi-build-input-sha256="${FENCED_FI_BUILD_INPUT_MANIFEST_SHA256}" \
      org.goldtrade.mini-app-dist-manifest-sha256="${MINI_APP_DIST_MANIFEST_SHA256}" \
      org.goldtrade.mini-app-dist-files-sha256="${MINI_APP_DIST_FILES_SHA256}" \
      org.goldtrade.mini-app-dist-file-count="${MINI_APP_DIST_FILE_COUNT}" \
      org.goldtrade.mini-app-dist-total-bytes="${MINI_APP_DIST_TOTAL_BYTES}"
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
COPY manage.py .
COPY run_bot.py .
COPY schemas.py .
COPY seed_fake_data.py .
COPY scripts/ ./scripts/

COPY ${FRONTEND_DIST_DIR}/ /app/mini_app_dist/
