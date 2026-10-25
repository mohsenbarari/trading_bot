# The CI caller must first SHA-admit App8da and bind this local alias to its ID.
# No default parent, registry fallback or application rebuild is supplied here.
ARG APP_SECURITY_PARENT
FROM ${APP_SECURITY_PARENT}
ARG SECURITY_MOUNTPOINT_SOURCE_COMMIT
ARG SECURITY_MOUNTPOINT_INPUT_SIGNATURE
LABEL io.gold-trade.security.mountpoints.parent-config="sha256:8da3e404d01cc239606d1c79a30c4701c5a2ef2d103787b68b9ec221ae723672" \
      io.gold-trade.security.mountpoints.source-commit="${SECURITY_MOUNTPOINT_SOURCE_COMMIT}" \
      io.gold-trade.security.mountpoints.input-signature="${SECURITY_MOUNTPOINT_INPUT_SIGNATURE}" \
      io.gold-trade.security.mountpoints.status="packaging_candidate_not_accepted"
RUN --network=none test "$(id -u):$(id -g)" = "0:0" \
    && test "${#SECURITY_MOUNTPOINT_SOURCE_COMMIT}" -eq 40 \
    && test "${#SECURITY_MOUNTPOINT_INPUT_SIGNATURE}" -eq 64 \
    && case "$SECURITY_MOUNTPOINT_SOURCE_COMMIT$SECURITY_MOUNTPOINT_INPUT_SIGNATURE" in *[!0-9a-f]*) exit 1;; esac \
    && test ! -e /app/runtime && test ! -L /app/runtime \
    && install -d -m 0755 -o 0 -g 0 /app/runtime \
        /app/runtime/coin-inference /app/runtime/product-estimator
