#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "configure_writer_witness_s3_backup.sh must run as root" >&2
    exit 2
fi

ROOT_DIR="${WRITER_WITNESS_S3_SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CREDENTIAL_SOURCE="${WRITER_WITNESS_S3_CREDENTIAL_SOURCE:-}"
BUCKET_SOURCE="${WRITER_WITNESS_S3_BUCKET_SOURCE:-}"
RECIPIENT_SOURCE="${WRITER_WITNESS_S3_RECIPIENT_SOURCE:-}"
ASSET_DIR="$ROOT_DIR/deploy/writer-witness"
readonly WRITER_WITNESS_SYSTEM_PYTHON=/usr/bin/python3.12
isolated_system_python() {
    /usr/bin/env -i \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        "$WRITER_WITNESS_SYSTEM_PYTHON" \
        -I -S -B -X utf8 -X pycache_prefix=/dev/null "$@"
}

for source in "$CREDENTIAL_SOURCE" "$BUCKET_SOURCE" "$RECIPIENT_SOURCE"; do
    if [[ -z "$source" || ! -f "$source" ]]; then
        echo "S3 credential, bucket, and age recipient source files are required" >&2
        exit 2
    fi
done
for required in \
    "$ASSET_DIR/writer-witness-offsite-backup.sh" \
    "$ASSET_DIR/writer-witness-s3-put.py" \
    "$ASSET_DIR/writer-witness-offsite-backup.service" \
    "$ASSET_DIR/writer-witness-offsite-backup.timer"
do
    if [[ ! -f "$required" ]]; then
        echo "missing Writer Witness S3 backup asset: $required" >&2
        exit 2
    fi
done

export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y --no-install-recommends age ca-certificates

install -d -m 0750 -o root -g writer-witness /etc/trading-bot-witness
recipient_target=/etc/trading-bot-witness/offsite-age-recipient.txt
environment_target=/etc/trading-bot-witness/offsite-backup.env
isolated_system_python - \
    "$CREDENTIAL_SOURCE" "$BUCKET_SOURCE" "$RECIPIENT_SOURCE" \
    "$environment_target" "$recipient_target" <<'PY'
from pathlib import Path
import os
import re
import stat
import sys

credential_path, bucket_path, recipient_path, environment_path, recipient_target = map(Path, sys.argv[1:])


def read_env(path: Path, required: tuple[str, ...], *, private: bool) -> dict[str, str]:
    if private and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise SystemExit(f"private S3 source file has unsafe mode: {path}")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SystemExit(f"invalid S3 environment source: {path}")
        values[key] = value.strip()
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise SystemExit(f"missing S3 settings in {path}: {','.join(missing)}")
    return values


credentials = read_env(
    credential_path,
    (
        "HETZNER_S3_ACCESS_KEY",
        "HETZNER_S3_SECRET_KEY",
        "HETZNER_S3_ENDPOINT",
        "HETZNER_S3_REGION",
    ),
    private=True,
)
bucket = read_env(
    bucket_path,
    ("HETZNER_S3_BUCKET", "HETZNER_S3_ENDPOINT", "HETZNER_S3_REGION"),
    private=True,
)
endpoint = credentials["HETZNER_S3_ENDPOINT"].rstrip("/")
region = credentials["HETZNER_S3_REGION"]
bucket_name = bucket["HETZNER_S3_BUCKET"]
if (
    endpoint != "https://hel1.your-objectstorage.com"
    or bucket["HETZNER_S3_ENDPOINT"].rstrip("/") != endpoint
    or region != "hel1"
    or bucket["HETZNER_S3_REGION"] != region
    or not re.fullmatch(r"tb-witness-[a-z0-9-]+", bucket_name)
    or not re.fullmatch(r"[A-Za-z0-9]+", credentials["HETZNER_S3_ACCESS_KEY"])
    or not re.fullmatch(r"[A-Za-z0-9]+", credentials["HETZNER_S3_SECRET_KEY"])
    or len(credentials["HETZNER_S3_SECRET_KEY"]) < 32
):
    raise SystemExit("Writer Witness S3 settings failed safety validation")
recipient = recipient_path.read_text(encoding="utf-8").strip()
if not re.fullmatch(r"age1[0-9a-z]+", recipient):
    raise SystemExit("invalid age recipient")

environment = (
    f"WRITER_WITNESS_S3_ENDPOINT={endpoint}\n"
    f"WRITER_WITNESS_S3_REGION={region}\n"
    f"WRITER_WITNESS_S3_BUCKET={bucket_name}\n"
    f"WRITER_WITNESS_S3_ACCESS_KEY={credentials['HETZNER_S3_ACCESS_KEY']}\n"
    f"WRITER_WITNESS_S3_SECRET_KEY={credentials['HETZNER_S3_SECRET_KEY']}\n"
    f"WRITER_WITNESS_OFFSITE_RECIPIENT_FILE={recipient_target}\n"
    "WRITER_WITNESS_OFFSITE_S3_PUT_HELPER=/usr/local/sbin/writer-witness-s3-put\n"
)
environment_path.write_text(environment, encoding="utf-8")
os.chmod(environment_path, 0o600)
recipient_target.write_text(recipient + "\n", encoding="utf-8")
os.chmod(recipient_target, 0o644)
PY

install -m 0755 "$ASSET_DIR/writer-witness-offsite-backup.sh" \
    /usr/local/sbin/writer-witness-offsite-backup
install -m 0755 "$ASSET_DIR/writer-witness-s3-put.py" \
    /usr/local/sbin/writer-witness-s3-put
install -m 0644 "$ASSET_DIR/writer-witness-offsite-backup.service" \
    /etc/systemd/system/writer-witness-offsite-backup.service
install -m 0644 "$ASSET_DIR/writer-witness-offsite-backup.timer" \
    /etc/systemd/system/writer-witness-offsite-backup.timer

systemctl daemon-reload
systemctl enable --now writer-witness-offsite-backup.timer
systemctl start writer-witness-offsite-backup.service
systemctl is-active --quiet writer-witness-offsite-backup.timer
systemctl is-failed --quiet writer-witness-offsite-backup.service && exit 1

bucket_name="$(sed -n 's/^WRITER_WITNESS_S3_BUCKET=//p' "$environment_target")"
printf '{"status":"active","bucket":"%s","timer_enabled":true,"private_decryption_key_present":false}\n' \
    "$bucket_name"
