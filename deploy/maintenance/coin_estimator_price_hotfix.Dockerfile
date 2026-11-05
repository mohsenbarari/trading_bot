ARG ESTIMATOR_BASE
FROM ${ESTIMATOR_BASE}
ARG SOURCE_COMMIT
COPY --chown=0:0 --chmod=0444 coin_rate_engine.py /app/core/market_intelligence/coin_rate_engine.py
LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      io.gold-trade.hotfix.name="private-melted-reference-v9" \
      io.gold-trade.hotfix.source="${SOURCE_COMMIT}"
