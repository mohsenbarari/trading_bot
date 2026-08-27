#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
source_config="$repo_root/deploy/market-data/market-pipeline-transfer.tmpfiles.conf"
target_config=/etc/tmpfiles.d/trading-bot-market-pipeline-transfer.conf
transfer_root=/var/tmp/trading-bot-market-pipeline-transfer

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  printf '%s\n' 'market_pipeline_transfer_workspace_requires_root' >&2
  exit 1
fi

filesystem_type="$(findmnt -n -o FSTYPE -T /var/tmp)"
case "$filesystem_type" in
  tmpfs|ramfs)
    printf '%s\n' 'market_pipeline_transfer_workspace_must_be_disk_backed' >&2
    exit 1
    ;;
esac

install -o root -g root -m 0644 -- "$source_config" "$target_config"
systemd-tmpfiles --create "$target_config"
systemd-tmpfiles --clean "$target_config"

if [[ -L "$transfer_root" || ! -d "$transfer_root" ]]; then
  printf '%s\n' 'market_pipeline_transfer_workspace_invalid' >&2
  exit 1
fi

actual_mode="$(stat -c '%a' -- "$transfer_root")"
actual_owner="$(stat -c '%u:%g' -- "$transfer_root")"
if [[ "$actual_mode" != 700 || "$actual_owner" != 0:0 ]]; then
  printf '%s\n' 'market_pipeline_transfer_workspace_permissions_invalid' >&2
  exit 1
fi

printf 'market_pipeline_transfer_workspace=ready path=%s filesystem=%s retention=1h\n' \
  "$transfer_root" "$filesystem_type"
