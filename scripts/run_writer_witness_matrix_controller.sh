#!/bin/bash
set -Eeuo pipefail
set +x
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-}" in
    preflight)
        target="$ROOT_DIR/scripts/plan_writer_witness_real_host_matrix.py"
        ;;
    scenario)
        target="$ROOT_DIR/scripts/run_writer_witness_real_host_matrix.py"
        ;;
    *)
        echo "usage: run_writer_witness_matrix_controller.sh preflight|scenario [arguments...]" >&2
        exit 2
        ;;
esac
shift

if [[ -z "${WRITER_WITNESS_CONTROLLER_TRANSACTION_UNIT:-}" ]]; then
    [[ -z "${INVOCATION_ID:-}" && -z "${SYSTEMD_EXEC_PID:-}" ]] || {
        echo "Matrix controller refuses an unrelated systemd execution context" >&2
        exit 70
    }
    suffix="$(/usr/bin/sha256sum <<<"$$:$target:$*" | /usr/bin/awk '{print substr($1,1,20)}')"
    unit="writer-witness-matrix-controller-$suffix.service"
    forwarded_environment=()
    for name in \
        WRITER_WITNESS_REAL_HOST_MATRIX_CONFIRM \
        WRITER_WITNESS_REAL_HOST_MATRIX_OBSERVER_CONFIRM \
        WRITER_WITNESS_REAL_HOST_MATRIX_SCENARIO
    do
        if [[ -n "${!name:-}" ]]; then
            forwarded_environment+=("$name=${!name}")
        fi
    done
    exec /usr/bin/systemd-run \
        --wait --collect --pipe --quiet --service-type=exec \
        --property=KillMode=control-group \
        --unit="$unit" \
        /usr/bin/env -i \
        HOME=/root LANG=C.UTF-8 LC_ALL=C.UTF-8 LOGNAME=root \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin USER=root \
        WRITER_WITNESS_CONTROLLER_TRANSACTION_UNIT="$unit" \
        "${forwarded_environment[@]}" \
        /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        "$ROOT_DIR/scripts/hold_writer_witness_package_locks.py" \
        --exec /usr/bin/python3.12 -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        "$target" "$@"
fi

echo "Matrix controller launcher unexpectedly survived its transaction exec" >&2
exit 70
