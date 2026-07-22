FROM postgres:15-alpine AS builder

RUN apk add --no-cache build-base
WORKDIR /build
COPY deploy/postgres-boottime/ ./
# The upstream image was built with LLVM support, but its runtime build stage
# intentionally does not ship the exact clang toolchain. Native PIC output is
# sufficient for a loadable extension and avoids a fragile compiler pin.
RUN make with_llvm=no

FROM postgres:15-alpine
ARG RELEASE_SHA=unbound
COPY --from=builder /build/trading_bot_boottime.so /usr/local/lib/postgresql/trading_bot_boottime.so
COPY --from=builder /build/trading_bot_boottime.control /usr/local/share/postgresql/extension/trading_bot_boottime.control
COPY --from=builder /build/trading_bot_boottime--1.0.sql /usr/local/share/postgresql/extension/trading_bot_boottime--1.0.sql

LABEL org.opencontainers.image.title="trading-bot-postgres-boottime" \
      org.opencontainers.image.description="PostgreSQL 15 with clock-independent Writer fencing primitives" \
      org.opencontainers.image.revision=${RELEASE_SHA}
