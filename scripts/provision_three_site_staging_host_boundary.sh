#!/usr/bin/env bash
# Install one fail-closed staging storage mount and aggregate cgroup boundary.

set -Eeuo pipefail

readonly DATA_ROOT=/srv/trading-bot-three-site-staging-data
readonly SLICE_NAME=trading-bot-three-site-staging.slice

role=
device=
expected_uuid=
cpu_quota=
memory_high=
memory_max=
tasks_max=
apply=false
confirm=

usage() {
    printf '%s\n' \
        "Usage: $0 --role ROLE --device /dev/disk/by-id/ID --expected-uuid UUID" \
        "  --cpu-quota PERCENT --memory-high SIZE --memory-max SIZE --tasks-max COUNT" \
        "  [--apply --confirm provision-three-site-staging-boundary:ROLE:UUID]"
}

while (($#)); do
    case "$1" in
        --role) role=${2:-}; shift 2 ;;
        --device) device=${2:-}; shift 2 ;;
        --expected-uuid) expected_uuid=${2:-}; shift 2 ;;
        --cpu-quota) cpu_quota=${2:-}; shift 2 ;;
        --memory-high) memory_high=${2:-}; shift 2 ;;
        --memory-max) memory_max=${2:-}; shift 2 ;;
        --tasks-max) tasks_max=${2:-}; shift 2 ;;
        --apply) apply=true; shift ;;
        --confirm) confirm=${2:-}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done

case "$role" in
    bot-fi|webapp-fi|webapp-ir|witness) ;;
    *) printf 'Unsupported staging role.\n' >&2; exit 2 ;;
esac
[[ "$device" == /dev/disk/by-id/* || "$device" == /dev/disk/by-uuid/* ]] || {
    printf 'Device must use a stable /dev/disk/by-id or by-uuid path.\n' >&2
    exit 2
}
[[ "$expected_uuid" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || {
    printf 'Expected filesystem UUID is malformed.\n' >&2
    exit 2
}
[[ "$cpu_quota" =~ ^[1-9][0-9]{0,3}%$ ]] || { printf 'Invalid CPU quota.\n' >&2; exit 2; }
[[ "$memory_high" =~ ^[1-9][0-9]*(M|G)$ ]] || { printf 'Invalid MemoryHigh.\n' >&2; exit 2; }
[[ "$memory_max" =~ ^[1-9][0-9]*(M|G)$ ]] || { printf 'Invalid MemoryMax.\n' >&2; exit 2; }
[[ "$tasks_max" =~ ^[1-9][0-9]{1,5}$ ]] || { printf 'Invalid TasksMax.\n' >&2; exit 2; }
[[ $EUID -eq 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }

real_device=$(readlink -f -- "$device")
[[ -b "$real_device" ]] || { printf 'Staging device is not a block device.\n' >&2; exit 1; }
actual_uuid=$(blkid -s UUID -o value -- "$real_device" | tr 'A-F' 'a-f')
filesystem=$(blkid -s TYPE -o value -- "$real_device")
expected_uuid=${expected_uuid,,}
[[ "$actual_uuid" == "$expected_uuid" && "$filesystem" =~ ^(ext4|xfs)$ ]] || {
    printf 'Device filesystem or UUID differs from the approved boundary.\n' >&2
    exit 1
}
root_source=$(findmnt -n -o SOURCE --target /)
[[ "$(readlink -f -- "$root_source" 2>/dev/null || printf '%s' "$root_source")" != "$real_device" ]] || {
    printf 'Refusing to use the operating-system root device for staging.\n' >&2
    exit 1
}

required_confirmation="provision-three-site-staging-boundary:${role}:${expected_uuid}"
if [[ "$apply" != true ]]; then
    printf '{"status":"planned","role":"%s","data_root":"%s","filesystem_uuid":"%s","slice":"%s","required_confirmation":"%s"}\n' \
        "$role" "$DATA_ROOT" "$expected_uuid" "$SLICE_NAME" "$required_confirmation"
    exit 0
fi
[[ "$confirm" == "$required_confirmation" ]] || {
    printf 'Apply confirmation does not match the exact role and filesystem UUID.\n' >&2
    exit 1
}

if mountpoint -q "$DATA_ROOT"; then
    mounted_uuid=$(findmnt -n -o UUID --target "$DATA_ROOT" | tr 'A-F' 'a-f')
    [[ "$mounted_uuid" == "$expected_uuid" ]] || {
        printf 'Existing staging mount has a different UUID.\n' >&2
        exit 1
    }
else
    if [[ -L "$DATA_ROOT" ]] || { [[ -d "$DATA_ROOT" ]] && find "$DATA_ROOT" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; }; then
        printf 'Unounted staging root is not an empty, safe directory.\n' >&2
        exit 1
    fi
    install -d -o root -g root -m 0750 "$DATA_ROOT"
fi

mount_unit=$(systemd-escape --path --suffix=mount "$DATA_ROOT")
mount_unit_path="/etc/systemd/system/${mount_unit}"
mount_tmp=$(mktemp)
slice_tmp=$(mktemp)
cleanup() { rm -f -- "$mount_tmp" "$slice_tmp"; }
trap cleanup EXIT

printf '%s\n' \
    '[Unit]' \
    "Description=Dedicated three-site staging data mount (${role})" \
    'ConditionPathIsDirectory=/srv/trading-bot-three-site-staging-data' \
    '' \
    '[Mount]' \
    "What=/dev/disk/by-uuid/${expected_uuid}" \
    "Where=${DATA_ROOT}" \
    "Type=${filesystem}" \
    'Options=rw,nodev,nosuid,noexec' \
    '' \
    '[Install]' \
    'WantedBy=multi-user.target' >"$mount_tmp"

printf '%s\n' \
    '[Unit]' \
    'Description=Aggregate resource ceiling for three-site staging containers' \
    '' \
    '[Slice]' \
    'CPUAccounting=true' \
    "CPUQuota=${cpu_quota}" \
    'MemoryAccounting=true' \
    "MemoryHigh=${memory_high}" \
    "MemoryMax=${memory_max}" \
    'TasksAccounting=true' \
    "TasksMax=${tasks_max}" \
    '' \
    '[Install]' \
    'WantedBy=multi-user.target' >"$slice_tmp"

install -o root -g root -m 0644 "$mount_tmp" "$mount_unit_path"
install -o root -g root -m 0644 "$slice_tmp" "/etc/systemd/system/${SLICE_NAME}"
systemctl daemon-reload
systemctl enable --now "$mount_unit"
systemctl enable --now "$SLICE_NAME"

mounted_uuid=$(findmnt -n -o UUID --target "$DATA_ROOT" | tr 'A-F' 'a-f')
mounted_target=$(findmnt -n -o TARGET --target "$DATA_ROOT")
[[ "$mounted_uuid" == "$expected_uuid" && "$mounted_target" == "$DATA_ROOT" ]] || {
    printf 'Mounted staging boundary failed post-install verification.\n' >&2
    exit 1
}

role_dir=${role//-/_}
case "$role" in
    witness) directories=(postgres audit) ;;
    *) directories=(postgres redis uploads audit) ;;
esac
install -d -o root -g root -m 0750 "$DATA_ROOT/$role_dir"
for directory in "${directories[@]}"; do
    install -d -o root -g root -m 0750 "$DATA_ROOT/$role_dir/$directory"
done

marker="$DATA_ROOT/.three-site-staging-boundary"
if [[ -e "$marker" ]]; then
    [[ ! -L "$marker" ]] || { printf 'Storage marker cannot be a symlink.\n' >&2; exit 1; }
    marker_value=$(<"$marker")
    [[ "$marker_value" == "role=${role};uuid=${expected_uuid}" ]] || {
        printf 'Storage marker belongs to a different staging boundary.\n' >&2
        exit 1
    }
else
    marker_tmp=$(mktemp --tmpdir="$DATA_ROOT" .boundary.XXXXXX)
    printf 'role=%s;uuid=%s' "$role" "$expected_uuid" >"$marker_tmp"
    chmod 0600 "$marker_tmp"
    mv -T -- "$marker_tmp" "$marker"
fi

cpu_effective=$(systemctl show -p CPUQuotaPerSecUSec --value "$SLICE_NAME")
memory_high_effective=$(systemctl show -p MemoryHigh --value "$SLICE_NAME")
memory_max_effective=$(systemctl show -p MemoryMax --value "$SLICE_NAME")
tasks_effective=$(systemctl show -p TasksMax --value "$SLICE_NAME")
[[ "$memory_high_effective" != infinity && "$memory_max_effective" != infinity && "$tasks_effective" != infinity ]] || {
    printf 'Aggregate staging slice did not acquire hard limits.\n' >&2
    exit 1
}

printf '{"status":"provisioned","role":"%s","data_root":"%s","filesystem_uuid":"%s","slice":"%s","cpu_quota_effective":"%s","memory_high_effective":"%s","memory_max_effective":"%s","tasks_max_effective":"%s"}\n' \
    "$role" "$DATA_ROOT" "$expected_uuid" "$SLICE_NAME" "$cpu_effective" \
    "$memory_high_effective" "$memory_max_effective" "$tasks_effective"
