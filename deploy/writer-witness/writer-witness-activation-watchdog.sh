#!/bin/bash
set -Eeuo pipefail
set +x
[[ "$-" != *x* ]] || {
    echo "Writer Witness activation watchdog refuses shell tracing" >&2
    exit 70
}

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
managed_units=(
    nginx
    writer-witness.service
    writer-witness-backup.service
    writer-witness-backup.timer
    writer-witness-offsite-backup.service
    writer-witness-offsite-backup.timer
)

restore_rollback_unit_intent() {
    local release_id unit intent load_state active_state unit_file_state current_load current_active
    local -a observed_unit_state_args=()
    release_id="$("${activation_helper[@]}" pending-release-id \
        --phase rolled_back_pending_service_completion)"
    systemctl daemon-reload
    for unit in "${managed_units[@]}"; do
        intent="$("${activation_helper[@]}" rollback-unit-intent --unit "$unit")"
        IFS=: read -r load_state active_state unit_file_state <<<"$intent"
        [[ "$intent" == "$load_state:$active_state:$unit_file_state" \
            && "$load_state" =~ ^[A-Za-z0-9._-]+$ \
            && "$active_state" =~ ^[A-Za-z0-9._-]+$ \
            && "$unit_file_state" =~ ^[A-Za-z0-9._-]+$ ]] || {
            echo "unsafe Writer Witness rollback unit intent: $unit" >&2
            exit 70
        }
        systemctl unmask --runtime "$unit" >/dev/null 2>&1 || true
        if [[ "$unit_file_state" == masked ]]; then
            systemctl unmask "$unit" >/dev/null 2>&1 || true
        fi
        if [[ "$load_state" == not-found ]]; then
            current_load="$(systemctl show -p LoadState --value "$unit" 2>/dev/null || true)"
            [[ -z "$current_load" || "$current_load" == not-found ]] || {
                echo "Writer Witness rollback did not restore absent unit: $unit" >&2
                exit 70
            }
            continue
        fi
        case "$active_state" in
            active)
                systemctl start "$unit"
                systemctl is-active --quiet "$unit"
                ;;
            inactive)
                current_active="$(systemctl show -p ActiveState --value "$unit")"
                if [[ "$unit" == writer-witness-backup.service \
                    || "$unit" == writer-witness-offsite-backup.service ]]; then
                    [[ "$current_active" != active \
                        && "$current_active" != activating \
                        && "$current_active" != deactivating ]] || return 75
                else
                    systemctl stop "$unit"
                fi
                if [[ "$current_active" == failed ]]; then
                    systemctl reset-failed "$unit"
                fi
                ! systemctl is-active --quiet "$unit"
                ;;
            *)
                echo "unsupported Writer Witness rollback active state: $active_state" >&2
                exit 70
                ;;
        esac
        case "$unit_file_state" in
            enabled) systemctl enable "$unit" >/dev/null ;;
            enabled-runtime) systemctl enable --runtime "$unit" >/dev/null ;;
            disabled) systemctl disable "$unit" >/dev/null ;;
            masked) systemctl mask "$unit" >/dev/null ;;
            masked-runtime) systemctl mask --runtime "$unit" >/dev/null ;;
            static|indirect|generated|alias|linked|linked-runtime|transient) ;;
            *)
                echo "unsupported Writer Witness rollback unit-file state: $unit_file_state" >&2
                exit 70
                ;;
        esac
    done
    if [[ "$("${activation_helper[@]}" rollback-unit-intent \
        --unit writer-witness.service)" =~ ^loaded:active: ]]; then
        curl --fail --silent --show-error \
            --retry 30 --retry-delay 1 --retry-all-errors \
            http://127.0.0.1:8011/health/ready >/dev/null
    fi
    systemctl daemon-reload
    for unit in "${managed_units[@]}"; do
        load_state="$(systemctl show -p LoadState --value "$unit" 2>/dev/null || true)"
        active_state="$(systemctl show -p ActiveState --value "$unit" 2>/dev/null || true)"
        unit_file_state="$(systemctl show -p UnitFileState --value "$unit" 2>/dev/null || true)"
        [[ -n "$load_state" ]] || load_state=not-found
        [[ -n "$active_state" ]] || active_state=inactive
        [[ -n "$unit_file_state" ]] || unit_file_state=not-found
        observed_unit_state_args+=(
            --unit-state "$unit:$load_state:$active_state:$unit_file_state"
        )
    done
    "${activation_helper[@]}" complete-rollback \
        --release-id "$release_id" \
        "${observed_unit_state_args[@]}" >/dev/null
}
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
    activation_recovered=rolled-back-without-service-changes)
        exit 0
        ;;
    activation_recovered=rolled-back-pending-service-completion)
        restore_rollback_unit_intent
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
