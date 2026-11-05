ARG PROCESSOR_BASE
FROM ${PROCESSOR_BASE}
ARG SOURCE_COMMIT
COPY --chown=0:0 --chmod=0444 market_fact_projection.py /app/core/market_intelligence/market_fact_projection.py
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.gold-trade.hotfix.name="recent-cash-revision-priority" \
      io.gold-trade.hotfix.source="${SOURCE_COMMIT}"
