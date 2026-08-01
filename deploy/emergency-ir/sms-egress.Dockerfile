# Build only on the controlled release builder, then transfer the resulting
# attested image through the private Object Storage release artifact.  WA-IR
# must never pull this base image from a public registry during activation.
#
# EMERGENCY_SMS_EGRESS_BASE_IMAGE must be a pinned, reviewed Nginx image with
# /usr/sbin/nginx and the system CA bundle at the path used by the relay config.
ARG EMERGENCY_SMS_EGRESS_BASE_IMAGE
FROM ${EMERGENCY_SMS_EGRESS_BASE_IMAGE}

ARG SOURCE_RELEASE_SHA
ARG EMERGENCY_PATCH_SHA
LABEL org.opencontainers.image.revision=${EMERGENCY_PATCH_SHA}
LABEL org.goldtrade.emergency.base-revision=${SOURCE_RELEASE_SHA}
LABEL org.goldtrade.emergency.scope=ir-standalone-sms-egress
LABEL org.goldtrade.emergency.egress=fixed-api.sms.ir-v1-send-verify

COPY deploy/emergency-ir/sms-egress.nginx.conf /etc/nginx/conf.d/default.conf
