#!/bin/bash
set -Eeuo pipefail
set +x
[[ "$-" != *x* ]] || { echo "Writer Witness host-fault helper refuses shell tracing" >&2; exit 70; }

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "writer witness matrix host fault helper must run as root" >&2
    exit 2
fi
action="${1:-}"
if [[ "$action" != "disk-full" && "$action" != "clock-jump" && "$action" != "recover" ]] || \
    [[ "${2:-}" != "--tag" || "$#" -ne 3 ]]; then
    echo "usage: writer-witness-matrix-host-faults {disk-full|clock-jump|recover} --tag WWM_TAG" >&2
    exit 2
fi
tag="$3"
[[ "$tag" =~ ^wwm_[0-9a-f]{12}$ ]] || {
    echo "unsafe matrix ownership tag" >&2
    exit 2
}
state_helper_path="/usr/local/sbin/writer-witness-matrix-host-fault-state"
[[ -x "$state_helper_path" ]] || {
    echo "durable host-fault recovery helper is unavailable" >&2
    exit 1
}
isolated_system_python() {
    /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null "$@"
}
state_helper() {
    isolated_system_python "$state_helper_path" "$@"
}
if [[ "$action" == "recover" ]]; then
    state_helper recover --tag "$tag" --caller-pid "$$"
    exit 0
fi

suffix="disk"
port=55439
if [[ "$action" == "clock-jump" ]]; then
    suffix="clock"
    port=55440
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
    state_helper recover --tag "$tag" --kind "$suffix" --caller-pid "$$" || status=70
    exit "$status"
}

process_start_ticks() {
    isolated_system_python - "$1" <<'PY'
from pathlib import Path
import sys

raw = Path(f"/proc/{int(sys.argv[1])}/stat").read_text(encoding="ascii")
end = raw.rfind(")")
fields = raw[end + 2:].split()
if end < 0 or len(fields) < 20:
    raise SystemExit("cannot attest process start ticks")
print(fields[19])
PY
}

database_inventory_sha256() {
    LC_ALL=C runuser -u postgres -- psql -XAt -v ON_ERROR_STOP=1 postgres <<'SQL' | sha256sum | awk '{print $1}'
SELECT json_build_object(
    'name', datname,
    'allow_connections', datallowconn,
    'connection_limit', datconnlimit,
    'is_template', datistemplate,
    'owner', pg_get_userbyid(datdba)
)::text
FROM pg_database
ORDER BY datname;
SQL
}

credential_bundle_sha256() {
    sha256sum \
        /etc/trading-bot-witness/runtime.env \
        /root/writer-witness-client-material/webapp-fi.env \
        /root/writer-witness-client-material/webapp-ir.env \
        /root/writer-witness-client-material/witness-ca.crt \
        | sha256sum | awk '{print $1}'
}

capture_production_state() {
    local destination="$1"
    local manifest inventory credentials system_identifier app_pid app_ticks
    local production_data production_postgres_pid production_postgres_ticks
    manifest="$(/usr/local/sbin/writer-witness-state-manifest)"
    inventory="$(database_inventory_sha256)"
    credentials="$(credential_bundle_sha256)"
    system_identifier="$(runuser -u postgres -- psql -XAt -v ON_ERROR_STOP=1 \
        -d writer_witness -c 'SELECT system_identifier::text FROM pg_control_system()')"
    app_pid="$(systemctl show -p MainPID --value writer-witness.service)"
    [[ "$app_pid" =~ ^[0-9]+$ && "$app_pid" -gt 1 ]]
    app_ticks="$(process_start_ticks "$app_pid")"
    production_data="$(runuser -u postgres -- psql -XAt -v ON_ERROR_STOP=1 \
        -d writer_witness -c 'SHOW data_directory')"
    [[ "$production_data" == /* && -f "$production_data/postmaster.pid" ]]
    production_postgres_pid="$(head -1 "$production_data/postmaster.pid")"
    [[ "$production_postgres_pid" =~ ^[0-9]+$ && "$production_postgres_pid" -gt 1 ]]
    production_postgres_ticks="$(process_start_ticks "$production_postgres_pid")"
    if grep -Fq '/faketime/libfaketime.so' "/proc/$app_pid/maps" || \
        grep -Fq '/faketime/libfaketime.so' "/proc/$production_postgres_pid/maps"; then
        echo "libfaketime escaped into a production process" >&2
        return 1
    fi
    [[ "$manifest" =~ ^[0-9a-f]{64}$ ]]
    [[ "$inventory" =~ ^[0-9a-f]{64}$ ]]
    [[ "$credentials" =~ ^[0-9a-f]{64}$ ]]
    [[ "$system_identifier" =~ ^[0-9]+$ ]]
    printf '{"manifest_sha256":"%s","database_inventory_sha256":"%s","credential_bundle_sha256":"%s","system_identifier":"%s","writer_service_pid":%s,"writer_service_start_ticks":%s,"postgres_pid":%s,"postgres_start_ticks":%s,"libfaketime_loaded":false}\n' \
        "$manifest" "$inventory" "$credentials" "$system_identifier" \
        "$app_pid" "$app_ticks" "$production_postgres_pid" "$production_postgres_ticks" \
        >"$destination"
    chmod 0600 "$destination"
}

if ss -H -ltn | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
    echo "isolated PostgreSQL port is already in use" >&2
    exit 1
fi
state_helper claim --tag "$tag" --kind "$suffix" --helper-pid "$$"
trap cleanup EXIT
install -d -m 0700 -o postgres -g postgres "$root"
mount -t tmpfs -o size=96m,mode=0700,uid="$(id -u postgres)",gid="$(id -g postgres)" \
    "$tag" "$root"
state_helper update --tag "$tag" --kind "$suffix" --helper-pid "$$" --phase mounted
install -d -m 0700 -o postgres -g postgres "$data" "$socket_dir"
runuser -u postgres -- "$bindir/initdb" -D "$data" \
    --auth-local=peer --auth-host=reject --no-locale >/dev/null
state_helper update --tag "$tag" --kind "$suffix" --helper-pid "$$" --phase initialized

postgres_program="$bindir/postgres"
postgres_start_program="$postgres_program"
postgres_options="-p $port -k $socket_dir -c listen_addresses='' -c fsync=on -c full_page_writes=on"
faketime_library=""
faketime_library_sha256=""
clock_control_file=""
if [[ "$action" == "clock-jump" ]]; then
    mapfile -t faketime_candidates < <(
        dpkg-query -L libfaketime 2>/dev/null \
            | grep -E '/faketime/libfaketime\.so\.1$' \
            | sort -u
    )
    [[ "${#faketime_candidates[@]}" -eq 1 ]] || {
        echo "exactly one packaged libfaketime library is required" >&2
        exit 1
    }
    faketime_library="$(realpath -e "${faketime_candidates[0]}")"
    [[ "$faketime_library" == /usr/lib/* || "$faketime_library" == /lib/* ]]
    [[ -f "$faketime_library" && ! -L "$faketime_library" ]]
    [[ "$(stat -c '%u:%a' "$faketime_library")" == 0:* ]]
    [[ "$((8#$(stat -c '%a' "$faketime_library") & 8#022))" -eq 0 ]]
    faketime_library_sha256="$(sha256sum "$faketime_library" | awk '{print $1}')"
    [[ "$faketime_library_sha256" =~ ^[0-9a-f]{64}$ ]]
    clock_control_file="$root/faketime.rc"
    install -m 0600 -o postgres -g postgres /dev/null "$clock_control_file"
    printf '+0\n' >"$clock_control_file"
    chown postgres:postgres "$clock_control_file"
    chmod 0600 "$clock_control_file"
    postgres_start_program="$root/postgres-faketime"
    sed \
        -e "s|__FAKETIME_LIBRARY__|$faketime_library|g" \
        -e "s|__CLOCK_CONTROL_FILE__|$clock_control_file|g" \
        -e "s|__POSTGRES_PROGRAM__|$postgres_program|g" \
        /dev/stdin >"$postgres_start_program" <<'EOF'
#!/bin/sh
exec env -u FAKETIME \
    LD_PRELOAD=__FAKETIME_LIBRARY__ \
    FAKETIME_TIMESTAMP_FILE=__CLOCK_CONTROL_FILE__ \
    FAKETIME_NO_CACHE=1 \
    FAKETIME_DISABLE_SHM=1 \
    FAKETIME_DONT_FAKE_MONOTONIC=1 \
    FAKETIME_DONT_RESET=1 \
    __POSTGRES_PROGRAM__ "$@"
EOF
    chown postgres:postgres "$postgres_start_program"
    chmod 0700 "$postgres_start_program"
fi

start_isolated_postgres() {
    runuser -u postgres -- "$bindir/pg_ctl" -D "$data" \
        -l "$root/postgresql.log" \
        -o "$postgres_options" \
        -p "$postgres_start_program" \
        -w start >/dev/null
    postgres_pid="$(head -1 "$data/postmaster.pid")"
    [[ "$postgres_pid" =~ ^[0-9]+$ && "$postgres_pid" -gt 1 ]]
    state_helper update --tag "$tag" --kind "$suffix" --helper-pid "$$" \
        --phase postgres_started --postgres-pid "$postgres_pid"
}

stop_isolated_postgres() {
    runuser -u postgres -- "$bindir/pg_ctl" -D "$data" -m fast -w stop >/dev/null
}

start_isolated_postgres
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -h "$socket_dir" -p "$port" postgres \
    -f /srv/trading-bot-witness/current/deploy/writer-witness/001_initial.sql >/dev/null
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -h "$socket_dir" -p "$port" postgres \
    -f /srv/trading-bot-witness/current/deploy/writer-witness/002_failover_operation_ledger.sql >/dev/null
state_helper update --tag "$tag" --kind "$suffix" --helper-pid "$$" \
    --phase running --postgres-pid "$postgres_pid"

if [[ "$action" == "clock-jump" ]]; then
    production_before="$root/production-before.json"
    production_after="$root/production-after.json"
    phase_one="$root/clock-phase-one.json"
    phase_two="$root/clock-phase-two.json"
    capture_production_state "$production_before"
    production_system_identifier="$(isolated_system_python -c 'import json,sys; print(json.load(open(sys.argv[1]))["system_identifier"])' "$production_before")"
    first_postgres_pid="$postgres_pid"
    first_postgres_ticks="$(process_start_ticks "$postgres_pid")"
    runuser -u postgres -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        /opt/trading-bot-witness/active/venv/bin/python \
        -I -B -X utf8 -X pycache_prefix=/dev/null \
        /srv/trading-bot-witness/current/scripts/run_writer_witness_clock_jump_probe.py \
        --phase phase-one \
        --tag "$tag" \
        --socket-dir "$socket_dir" \
        --data-dir "$data" \
        --clock-control-file "$clock_control_file" \
        --postmaster-pid "$postgres_pid" \
        --faketime-library "$faketime_library" \
        --faketime-library-sha256 "$faketime_library_sha256" \
        --production-system-identifier "$production_system_identifier" \
        >"$phase_one"
    stop_isolated_postgres
    start_isolated_postgres
    second_postgres_pid="$postgres_pid"
    second_postgres_ticks="$(process_start_ticks "$postgres_pid")"
    if [[ "$first_postgres_pid:$first_postgres_ticks" == "$second_postgres_pid:$second_postgres_ticks" ]]; then
        echo "isolated PostgreSQL restart identity did not change" >&2
        exit 1
    fi
    state_helper update --tag "$tag" --kind "$suffix" --helper-pid "$$" \
        --phase running --postgres-pid "$postgres_pid"
    runuser -u postgres -- /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        /opt/trading-bot-witness/active/venv/bin/python \
        -I -B -X utf8 -X pycache_prefix=/dev/null \
        /srv/trading-bot-witness/current/scripts/run_writer_witness_clock_jump_probe.py \
        --phase phase-two \
        --tag "$tag" \
        --socket-dir "$socket_dir" \
        --data-dir "$data" \
        --clock-control-file "$clock_control_file" \
        --postmaster-pid "$postgres_pid" \
        --faketime-library "$faketime_library" \
        --faketime-library-sha256 "$faketime_library_sha256" \
        --production-system-identifier "$production_system_identifier" \
        >"$phase_two"
    if ss -H -ltn | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
        echo "isolated PostgreSQL unexpectedly exposed a TCP listener" >&2
        exit 1
    fi
    capture_production_state "$production_after"
    cmp --silent "$production_before" "$production_after" || {
        echo "production Witness state changed during the isolated clock probe" >&2
        exit 1
    }
    state_helper update --tag "$tag" --kind "$suffix" --helper-pid "$$" \
        --phase completed --postgres-pid "$postgres_pid"
    isolated_system_python - "$phase_one" "$phase_two" "$production_before" "$production_after" \
        "$tag" "$faketime_library" "$faketime_library_sha256" \
        "$first_postgres_pid" "$first_postgres_ticks" \
        "$second_postgres_pid" "$second_postgres_ticks" <<'PY'
import json
from pathlib import Path
import sys

phase_one = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
phase_two = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
production_before = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
production_after = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
if phase_one.get("status") != "phase_one_passed" or phase_two.get("status") != "passed":
    raise SystemExit("real database-clock probe phases did not both pass")
for payload in (phase_one, phase_two):
    isolation = payload.get("isolation", {})
    if (
        payload.get("production_clock_path") != "SELECT clock_timestamp()"
        or payload.get("synthetic_time_argument_used") is not False
        or isolation.get("unix_socket_transport") is not True
        or isolation.get("listen_addresses") != ""
        or isolation.get("libfaketime_loaded") is not True
    ):
        raise SystemExit("real database-clock/isolation evidence is incomplete")
if phase_one["isolation"]["system_identifier"] != phase_two["isolation"]["system_identifier"]:
    raise SystemExit("disposable PostgreSQL identity changed across restart")
if production_before != production_after:
    raise SystemExit("production state evidence changed during the clock probe")
result = dict(phase_two)
result.update(
    tag=sys.argv[5],
    libfaketime_library=sys.argv[6],
    libfaketime_library_sha256=sys.argv[7],
    phase_one=phase_one,
    production_before=production_before,
    production_after=production_after,
    production_state_unchanged=True,
    production_processes_never_loaded_libfaketime=True,
    disposable_postgres_restart={
        "before": {"pid": int(sys.argv[8]), "start_ticks": int(sys.argv[9])},
        "after": {"pid": int(sys.argv[10]), "start_ticks": int(sys.argv[11])},
    },
)
print(json.dumps(result, sort_keys=True))
PY
    exit 0
fi

production_before="$root/production-before.json"
production_after="$root/production-after.json"
capture_production_state "$production_before"
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
capture_production_state "$production_after"
cmp --silent "$production_before" "$production_after" || {
    echo "production Witness state changed during the isolated disk-full probe" >&2
    exit 1
}
isolated_system_python - "$production_before" "$production_after" "$tag" <<'PY'
import json
from pathlib import Path
import sys

before = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
after = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if before != after:
    raise SystemExit("production state evidence changed during disk-full probe")
print(json.dumps({
    "status": "passed",
    "scenario": "isolated-postgresql-disk-full",
    "tag": sys.argv[3],
    "production_before": before,
    "production_after": after,
    "production_state_unchanged": True,
}, sort_keys=True))
PY
