#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "writer witness matrix host fault helper must run as root" >&2
    exit 2
fi
action="${1:-}"
if [[ "$action" != "disk-full" && "$action" != "clock-jump" ]] || \
    [[ "${2:-}" != "--tag" || "$#" -ne 3 ]]; then
    echo "usage: writer-witness-matrix-host-faults {disk-full|clock-jump} --tag WWM_TAG" >&2
    exit 2
fi
tag="$3"
[[ "$tag" =~ ^wwm_[0-9a-f]{12}$ ]] || {
    echo "unsafe matrix ownership tag" >&2
    exit 2
}

suffix="disk"
port=55439
listen_addresses=""
if [[ "$action" == "clock-jump" ]]; then
    suffix="clock"
    port=55440
    listen_addresses="127.0.0.1"
fi
root="/run/$tag-$suffix"
data="$root/pgdata"
socket_dir="$root/socket"
bindir="$(pg_config --bindir)"
postgres_pid=""
error_log="$root/disk-full.expected-error.log"

cleanup() {
    local status=$?
    trap - EXIT
    set +e
    if [[ -n "$postgres_pid" ]]; then
        runuser -u postgres -- "$bindir/pg_ctl" -D "$data" -m immediate stop >/dev/null 2>&1 || status=70
    fi
    if mountpoint -q "$root"; then
        umount "$root" || status=70
    fi
    if ! mountpoint -q "$root"; then
        rm -rf "$root"
    fi
    exit "$status"
}
trap cleanup EXIT

[[ ! -e "$root" ]] || {
    echo "matrix disk-full path already exists" >&2
    exit 1
}
if ss -ltn | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
    echo "isolated PostgreSQL port is already in use" >&2
    exit 1
fi
install -d -m 0700 -o postgres -g postgres "$root"
mount -t tmpfs -o size=96m,mode=0700,uid="$(id -u postgres)",gid="$(id -g postgres)" \
    "$tag" "$root"
install -d -m 0700 -o postgres -g postgres "$data" "$socket_dir"
runuser -u postgres -- "$bindir/initdb" -D "$data" --auth=trust --no-locale >/dev/null
runuser -u postgres -- "$bindir/pg_ctl" -D "$data" \
    -o "-p $port -k $socket_dir -c listen_addresses='$listen_addresses' -c fsync=on -c full_page_writes=on" \
    -w start >/dev/null
postgres_pid="$(head -1 "$data/postmaster.pid")"
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -h "$socket_dir" -p "$port" postgres \
    -f /srv/trading-bot-witness/current/deploy/writer-witness/001_initial.sql >/dev/null

if [[ "$action" == "clock-jump" ]]; then
    probe_output="$root/clock-probe.json"
    runuser -u postgres -- /opt/trading-bot-witness/venv/bin/python \
        /srv/trading-bot-witness/current/scripts/run_writer_witness_clock_jump_probe.py \
        --database-url "postgresql+asyncpg://postgres@127.0.0.1:$port/postgres" \
        >"$probe_output"
    python3 - "$probe_output" "$tag" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
if payload.get("status") != "passed" or payload.get("production_database_touched") is not False:
    raise SystemExit("isolated PostgreSQL clock-jump probe did not pass safely")
payload["tag"] = sys.argv[2]
print(json.dumps(payload, sort_keys=True))
PY
    exit 0
fi

runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -h "$socket_dir" -p "$port" postgres \
    -c "CREATE TABLE matrix_disk_probe(id bigserial PRIMARY KEY, payload bytea NOT NULL);" \
    -c "INSERT INTO matrix_disk_probe(payload) VALUES (repeat('a', 1048576)::bytea);" >/dev/null

set +e
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -h "$socket_dir" -p "$port" postgres \
    -c "INSERT INTO matrix_disk_probe(payload) SELECT decode(repeat(md5(g::text), 2048), 'hex') FROM generate_series(1, 10000) g;" \
    >/dev/null 2>"$error_log"
insert_status=$?
set -e
if [[ "$insert_status" -eq 0 ]]; then
    echo "isolated PostgreSQL did not reach the disk-full boundary" >&2
    exit 1
fi
if ! grep -Eqi 'No space left|could not extend|disk full|PANIC|I/O error' "$error_log"; then
    echo "isolated PostgreSQL failed for an unexpected reason" >&2
    sed -n '1,20p' "$error_log" >&2
    exit 1
fi

printf '{"status":"passed","scenario":"isolated-postgresql-disk-full","tag":"%s","production_database_touched":false}\n' "$tag"
