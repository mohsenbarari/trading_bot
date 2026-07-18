#!/usr/bin/env bash
set -Eeuo pipefail

result="$(/usr/local/sbin/writer-witness-activation recover-boot)"
case "$result" in
    activation_recovered=deferred-live-provision|activation_recovered=no)
        exit 0
        ;;
    activation_recovered=yes)
        systemctl daemon-reload
        systemctl enable --now \
            nginx \
            writer-witness.service \
            writer-witness-backup.timer \
            writer-witness-offsite-backup.timer
        systemctl restart nginx writer-witness.service
        ;;
    activation_recovered=committed-pending-service-completion)
        release_id="$(/usr/local/sbin/writer-witness-activation active-release-id)"
        systemctl daemon-reload
        systemctl enable --now \
            nginx \
            writer-witness.service \
            writer-witness-backup.timer \
            writer-witness-offsite-backup.timer
        systemctl restart nginx writer-witness.service
        curl --fail --silent --show-error \
            --retry 30 --retry-delay 1 --retry-all-errors \
            http://127.0.0.1:8011/health/ready >/dev/null
        /usr/local/sbin/writer-witness-activation complete \
            --release-id "$release_id" >/dev/null
        ;;
    *)
        echo "unexpected Writer Witness activation recovery result: $result" >&2
        exit 70
        ;;
esac
