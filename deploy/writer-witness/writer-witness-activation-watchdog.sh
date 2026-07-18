#!/usr/bin/env bash
set -Eeuo pipefail

PATH=/usr/sbin:/usr/bin:/sbin:/bin
activation_helper=(
    /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin
    /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null
    /usr/local/sbin/writer-witness-activation
)

# Keep both cross-operation capabilities for the complete recovery, service,
# health, and journal-completion sequence.  A live provision or HMAC rotation
# therefore makes this timer defer without observing or publishing mixed state.
provision_lock=/var/lib/trading-bot-witness/activation-state/.provision.lock
rotation_lock=/var/lib/trading-bot-witness/hmac-rotation/.runtime.lock
for lock in "$provision_lock" "$rotation_lock"; do
    [[ -f "$lock" && ! -L "$lock" \
        && "$(stat -c '%u:%g:%a:%h' "$lock")" == 0:0:600:1 ]] || {
        echo "Writer Witness watchdog lock metadata is unsafe: $lock" >&2
        exit 70
    }
done
exec {provision_lock_fd}<>"$provision_lock"
flock -n "$provision_lock_fd" || exit 0
exec {rotation_lock_fd}<>"$rotation_lock"
flock -n "$rotation_lock_fd" || exit 0

result="$("${activation_helper[@]}" recover)"
case "$result" in
    activation_recovered=no)
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
        release_id="$("${activation_helper[@]}" active-release-id)"
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
        "${activation_helper[@]}" complete \
            --release-id "$release_id" >/dev/null
        ;;
    *)
        echo "unexpected Writer Witness activation recovery result: $result" >&2
        exit 70
        ;;
esac
