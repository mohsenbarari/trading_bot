#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly WRITER_WITNESS_SYSTEM_PYTHON=/usr/bin/python3.12
isolated_system_python() {
    /usr/bin/env -i \
        PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        "$WRITER_WITNESS_SYSTEM_PYTHON" \
        -I -S -B -X utf8 -X pycache_prefix=/dev/null "$@"
}
installed_activation() {
    isolated_system_python /usr/local/sbin/writer-witness-activation "$@"
}

wait_for_writer_witness_ready() {
    for attempt in $(seq 1 30); do
        if curl --fail --silent --show-error \
            http://127.0.0.1:8011/health/ready >/dev/null; then
            return 0
        fi
        [[ "$attempt" -lt 30 ]] || {
            echo "reconciled Writer Witness generation did not become ready" >&2
            return 1
        }
        sleep 1
    done
}

reconcile_installed_activation() {
    local result release_id
    result="$(installed_activation recover)"
    case "$result" in
        activation_recovered=no)
            return 0
            ;;
        activation_recovered=yes)
            systemctl daemon-reload
            systemctl enable --now \
                nginx \
                writer-witness.service \
                writer-witness-backup.timer \
                writer-witness-offsite-backup.timer
            systemctl restart nginx writer-witness.service
            wait_for_writer_witness_ready
            ;;
        activation_recovered=committed-pending-service-completion)
            release_id="$(installed_activation active-release-id)"
            systemctl daemon-reload
            systemctl enable --now \
                nginx \
                writer-witness.service \
                writer-witness-backup.timer \
                writer-witness-offsite-backup.timer
            systemctl restart nginx writer-witness.service
            wait_for_writer_witness_ready
            installed_activation complete --release-id "$release_id" >/dev/null
            ;;
        *)
            echo "unexpected Writer Witness activation recovery result: $result" >&2
            return 70
            ;;
    esac
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "provision_writer_witness_host.sh must run as root" >&2
    exit 2
fi

SOURCE_DIR="${WRITER_WITNESS_SOURCE_DIR:-}"
WITNESS_PUBLIC_IP="${WRITER_WITNESS_PUBLIC_IP:-}"
WEBAPP_FI_SOURCE_IP="${WRITER_WITNESS_WEBAPP_FI_SOURCE_IP:-}"
WEBAPP_IR_SOURCE_IP="${WRITER_WITNESS_WEBAPP_IR_SOURCE_IP:-}"
SSH_SOURCE_IP="${WRITER_WITNESS_SSH_SOURCE_IP:-}"
RELEASE_ID="${WRITER_WITNESS_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
HARDEN_SSH="${WRITER_WITNESS_HARDEN_SSH:-false}"
SSH_KEY_SOURCE_USER="${WRITER_WITNESS_SSH_KEY_SOURCE_USER:-ubuntu}"
WHEELHOUSE="${WRITER_WITNESS_WHEELHOUSE:-}"
ROTATE_TLS="${WRITER_WITNESS_ROTATE_TLS:-false}"
EXPECTED_MANIFEST_SHA256="${WRITER_WITNESS_EXPECTED_MANIFEST_SHA256:-}"

for value_name in SOURCE_DIR WITNESS_PUBLIC_IP WEBAPP_FI_SOURCE_IP WEBAPP_IR_SOURCE_IP SSH_SOURCE_IP EXPECTED_MANIFEST_SHA256 WHEELHOUSE; do
    value="${!value_name}"
    if [[ -z "$value" ]]; then
        echo "$value_name is required" >&2
        exit 2
    fi
done
if [[ ! "$EXPECTED_MANIFEST_SHA256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "WRITER_WITNESS_EXPECTED_MANIFEST_SHA256 must be 64 lowercase hex characters" >&2
    exit 2
fi
if [[ ! "$RELEASE_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "WRITER_WITNESS_RELEASE_ID contains unsafe characters" >&2
    exit 2
fi
if [[ "$HARDEN_SSH" != "true" && "$HARDEN_SSH" != "false" ]]; then
    echo "WRITER_WITNESS_HARDEN_SSH must be true or false" >&2
    exit 2
fi
if [[ "$ROTATE_TLS" != "true" && "$ROTATE_TLS" != "false" ]]; then
    echo "WRITER_WITNESS_ROTATE_TLS must be true or false" >&2
    exit 2
fi
if [[ "$ROTATE_TLS" == "true" ]]; then
    echo "TLS rotation is a separate host transaction and is not allowed during release activation" >&2
    exit 2
fi
if [[ ! -d "$WHEELHOUSE" || -L "$WHEELHOUSE" ]]; then
    echo "WRITER_WITNESS_WHEELHOUSE must be one real offline wheel directory" >&2
    exit 2
fi
isolated_system_python - "$WITNESS_PUBLIC_IP" "$WEBAPP_FI_SOURCE_IP" "$WEBAPP_IR_SOURCE_IP" "$SSH_SOURCE_IP" <<'PY'
from ipaddress import ip_address
import sys
for value in sys.argv[1:]:
    parsed = ip_address(value)
    if parsed.version != 4 or parsed.is_unspecified or parsed.is_multicast:
        raise SystemExit(f"unsafe IPv4 address: {value}")
PY

ASSET_DIR="$SOURCE_DIR/deploy/writer-witness"
for required in \
    "$SOURCE_DIR/release-manifest.json" \
    "$SOURCE_DIR/writer_witness_app.py" \
    "$ASSET_DIR/001_initial.sql" \
    "$ASSET_DIR/requirements.txt" \
    "$ASSET_DIR/requirements.lock" \
    "$ASSET_DIR/python-runtime.json" \
    "$ASSET_DIR/nftables-policy.json" \
    "$ASSET_DIR/wheelhouse.sha256" \
    "$ASSET_DIR/nginx.conf.template" \
    "$ASSET_DIR/writer-witness-activation.py" \
    "$ASSET_DIR/writer-witness-activation-recovery.service" \
    "$ASSET_DIR/writer-witness-activation-watchdog.sh" \
    "$ASSET_DIR/writer-witness-activation-watchdog.service" \
    "$ASSET_DIR/writer-witness-activation-watchdog.timer" \
    "$ASSET_DIR/writer-witness.service" \
    "$ASSET_DIR/writer-witness-matrix-campaign.py" \
    "$ASSET_DIR/writer-witness-matrix-host-faults.sh" \
    "$ASSET_DIR/writer-witness-matrix-host-fault-state.py" \
    "$SOURCE_DIR/scripts/run_writer_witness_clock_jump_probe.py" \
    "$SOURCE_DIR/scripts/render_writer_witness_credentials.py" \
    "$SOURCE_DIR/scripts/smoke_writer_witness_client.py" \
    "$SOURCE_DIR/scripts/verify_writer_witness_release.py" \
    "$SOURCE_DIR/scripts/verify_writer_witness_runtime.py" \
    "$SOURCE_DIR/scripts/verify_writer_witness_runtime_provenance.py" \
    "$SOURCE_DIR/scripts/verify_writer_witness_wheelhouse.py" \
    "$SOURCE_DIR/scripts/verify_writer_witness_nftables.py"
do
    if [[ ! -f "$required" ]]; then
        echo "missing release artifact: $required" >&2
        exit 2
    fi
done
bootstrap_attest_release() {
    local release_root="$1"
    isolated_system_python - "$release_root" "$EXPECTED_MANIFEST_SHA256" <<'PY'
from pathlib import Path
import hashlib
import json
import os
import stat
import sys

root = Path(sys.argv[1])
expected_manifest = sys.argv[2]
if root.is_symlink() or not root.is_dir() or root.resolve(strict=True) != root:
    raise SystemExit("writer witness source root must be one canonical real directory")


def require_directory(path: Path, mode: int) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != mode
    ):
        raise SystemExit(f"unsafe release bootstrap directory: {path}")


require_directory(root, 0o755)
require_directory(root / "scripts", 0o755)

def read_regular(path: Path, maximum: int, expected_mode: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_nlink != 1
            or metadata.st_size < 1
            or metadata.st_size > maximum
        ):
            raise SystemExit(f"unsafe release bootstrap file: {path}")
        raw = b""
        while len(raw) < metadata.st_size:
            chunk = os.read(descriptor, metadata.st_size - len(raw))
            if not chunk:
                raise SystemExit(f"short release bootstrap read: {path}")
            raw += chunk
        after = os.fstat(descriptor)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_mode,
            metadata.st_nlink,
            metadata.st_uid,
            metadata.st_gid,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_uid,
            after.st_gid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise SystemExit(f"release bootstrap file changed during read: {path}")
        return raw
    finally:
        os.close(descriptor)

manifest_raw = read_regular(root / "release-manifest.json", 1024 * 1024, 0o644)
if hashlib.sha256(manifest_raw).hexdigest() != expected_manifest:
    raise SystemExit("writer witness release manifest is not the externally bound manifest")
manifest = json.loads(manifest_raw.decode("utf-8"))
verifier_relative = "scripts/verify_writer_witness_release.py"
if not isinstance(manifest, dict) or not isinstance(manifest.get(verifier_relative), str):
    raise SystemExit("writer witness release verifier is not bound by the manifest")
verifier_raw = read_regular(root / verifier_relative, 1024 * 1024, 0o755)
if hashlib.sha256(verifier_raw).hexdigest() != manifest[verifier_relative]:
    raise SystemExit("writer witness release verifier does not match the bound manifest")
PY
}
fsync_directories() {
    isolated_system_python - "$@" <<'PY'
import os
import sys

for value in sys.argv[1:]:
    descriptor = os.open(value, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY
}
fsync_trees() {
    isolated_system_python - "$@" <<'PY'
import os
from pathlib import Path
import stat
import sys


def sync_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise SystemExit(f"cannot durably sync unsafe tree root: {root}")
    directories: list[Path] = []
    for current_raw, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current = Path(current_raw)
        directories.append(current)
        for name in list(directory_names):
            path = current / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                directory_names.remove(name)
            elif not stat.S_ISDIR(metadata.st_mode):
                raise SystemExit(f"unsafe node in durable tree: {path}")
        for name in file_names:
            path = current / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise SystemExit(f"unsafe node in durable tree: {path}")
            descriptor = os.open(
                path,
                os.O_RDONLY
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                before = os.fstat(descriptor)
                os.fsync(descriptor)
                after = os.fstat(descriptor)
                identity = lambda value: (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_nlink,
                    value.st_uid,
                    value.st_gid,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )
                if identity(before) != identity(after):
                    raise SystemExit(f"file changed during durable sync: {path}")
            finally:
                os.close(descriptor)
    for directory in reversed(directories):
        descriptor = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


for value in sys.argv[1:]:
    sync_tree(Path(value))
PY
}
atomic_install_file() {
    local source="$1"
    local destination="$2"
    local mode="$3"
    local uid="${4:-0}"
    local gid="${5:-0}"
    isolated_system_python - "$source" "$destination" "$mode" "$uid" "$gid" <<'PY'
from pathlib import Path
import os
import stat
import sys
import uuid

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
mode = int(sys.argv[3], 8)
uid = int(sys.argv[4])
gid = int(sys.argv[5])
source_descriptor = os.open(
    source,
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
)
try:
    before = os.fstat(source_descriptor)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size < 1:
        raise SystemExit(f"unsafe atomic install source: {source}")
    payload = b""
    while len(payload) < before.st_size:
        chunk = os.read(source_descriptor, before.st_size - len(payload))
        if not chunk:
            raise SystemExit(f"short atomic install source read: {source}")
        payload += chunk
    after = os.fstat(source_descriptor)
    identity = lambda value: (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )
    if identity(before) != identity(after):
        raise SystemExit(f"atomic install source changed during read: {source}")
finally:
    os.close(source_descriptor)

parent = destination.parent
parent_metadata = parent.lstat()
if not stat.S_ISDIR(parent_metadata.st_mode) or stat.S_ISLNK(parent_metadata.st_mode):
    raise SystemExit(f"unsafe atomic install parent: {parent}")
temporary = parent / f".{destination.name}.install-{uuid.uuid4().hex}"
descriptor = os.open(
    temporary,
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0),
    mode,
)
try:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count < 1:
            raise SystemExit(f"short atomic install destination write: {destination}")
        written += count
    os.fchmod(descriptor, mode)
    os.fchown(descriptor, uid, gid)
    os.fsync(descriptor)
except BaseException:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
    raise
finally:
    os.close(descriptor)
os.replace(temporary, destination)
directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
}
assert_no_writer_witness_systemd_dropins() {
    local dropin_root
    for dropin_root in \
        /etc/systemd/system/writer-witness.service.d \
        /run/systemd/system/writer-witness.service.d \
        /usr/local/lib/systemd/system/writer-witness.service.d \
        /usr/lib/systemd/system/writer-witness.service.d
    do
        if [[ -e "$dropin_root" || -L "$dropin_root" ]]; then
            echo "Writer Witness systemd drop-ins are forbidden: $dropin_root" >&2
            exit 2
        fi
    done
}
bootstrap_attest_release "$SOURCE_DIR"
isolated_system_python "$SOURCE_DIR/scripts/verify_writer_witness_release.py" \
    --release-root "$SOURCE_DIR" \
    --expected-manifest-sha256 "$EXPECTED_MANIFEST_SHA256" \
    --expected-uid 0 \
    --expected-gid 0 \
    >/dev/null

export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
    ca-certificates \
    libfaketime \
    nginx \
    openssl \
    postgresql \
    postgresql-client \
    python3 \
    python3-venv \
    util-linux \
    ufw

if ! id -u writer-witness >/dev/null 2>&1; then
    useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin writer-witness
fi
install -d -m 0755 -o root -g root /opt/trading-bot-witness /srv/trading-bot-witness/releases
install -d -m 0750 -o root -g writer-witness /etc/trading-bot-witness
install -d -m 0700 -o root -g root /root/writer-witness-client-material
install -d -m 0700 -o root -g root /var/lib/trading-bot-witness/hmac-rotation
install -d -m 0700 -o root -g root \
    /var/lib/trading-bot-witness/restore-state \
    /var/lib/trading-bot-witness/restore-state/history
install -d -m 0700 -o root -g root \
    /var/lib/trading-bot-witness/matrix-campaign \
    /var/lib/trading-bot-witness/matrix-campaign/releases \
    /var/lib/trading-bot-witness/matrix-campaign/authorization-intents \
    /var/lib/trading-bot-witness/matrix-campaign/authorizations \
    /var/lib/trading-bot-witness/matrix-campaign/consumed-approvals \
    /var/lib/trading-bot-witness/matrix-campaign/consumed-preflights
install -d -m 0700 -o root -g root /var/backups/trading-bot-witness
install -d -m 0700 -o root -g root /var/lib/trading-bot-witness/activation-state
isolated_system_python - <<'PY'
from pathlib import Path
import os
import stat

root = Path("/var/lib/trading-bot-witness/matrix-campaign")
lock = root / ".campaign.lock"
flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(lock, flags | os.O_CREAT | os.O_EXCL, 0o600)
    created = True
except FileExistsError:
    descriptor = os.open(lock, flags)
    created = False
try:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise SystemExit("Writer Witness campaign lock metadata is unsafe")
    if created:
        os.fsync(descriptor)
finally:
    os.close(descriptor)
if created:
    directory = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
PY

release_dir="/srv/trading-bot-witness/releases/$RELEASE_ID"
venv_dir="/opt/trading-bot-witness/venvs/$RELEASE_ID"
activation_root=/opt/trading-bot-witness/activations
activation_dir="$activation_root/$RELEASE_ID"
install -d -m 0755 -o root -g root /opt/trading-bot-witness/venvs "$activation_root"

provision_lock=/var/lib/trading-bot-witness/activation-state/.provision.lock
isolated_system_python - "$provision_lock" <<'PY'
from pathlib import Path
import os
import stat
import sys

path = Path(sys.argv[1])
flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    created = True
except FileExistsError:
    descriptor = os.open(path, flags)
    created = False
try:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise SystemExit("Writer Witness provision lock metadata is unsafe")
    if created:
        os.fsync(descriptor)
finally:
    os.close(descriptor)
if created:
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
PY
exec {provision_lock_fd}<>"$provision_lock"
flock -n "$provision_lock_fd" || {
    echo "another Writer Witness provision operation is active" >&2
    exit 75
}

# Serialize the entire activation transaction with HMAC rotation. The same
# inherited descriptor is attested by both renderer phases, so no rotation can
# land between credential prepare and post-commit finalization.
rotation_lock=/var/lib/trading-bot-witness/hmac-rotation/.runtime.lock
isolated_system_python - "$rotation_lock" <<'PY'
from pathlib import Path
import os
import stat
import sys

path = Path(sys.argv[1])
flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
try:
    descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
    created = True
except FileExistsError:
    descriptor = os.open(path, flags)
    created = False
try:
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise SystemExit("Writer Witness HMAC rotation lock metadata is unsafe")
    if created:
        os.fsync(descriptor)
finally:
    os.close(descriptor)
if created:
    directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
PY
exec {rotation_lock_fd}<>"$rotation_lock"
flock -n "$rotation_lock_fd" || {
    echo "an HMAC rotation operation is active" >&2
    exit 75
}

# An interrupted transaction belongs to the helper version that created its
# journal. Recover it before atomically upgrading the recovery infrastructure.
if [[ -e /usr/local/sbin/writer-witness-activation || -L /usr/local/sbin/writer-witness-activation ]]; then
    [[ -f /usr/local/sbin/writer-witness-activation \
        && ! -L /usr/local/sbin/writer-witness-activation \
        && "$(stat -c '%u:%g:%a:%h' /usr/local/sbin/writer-witness-activation)" == 0:0:755:1 ]] || {
        echo "installed Writer Witness activation helper metadata is unsafe" >&2
        exit 2
    }
    reconcile_installed_activation
fi

# Recovery infrastructure is installed atomically before the first durable
# activation intent.  It remains compatible with both the legacy unit and the
# activation-aware unit and therefore is not itself rolled back.
atomic_install_file \
    "$ASSET_DIR/writer-witness-activation.py" \
    /usr/local/sbin/writer-witness-activation \
    0755
atomic_install_file \
    "$ASSET_DIR/writer-witness-activation-recovery.service" \
    /etc/systemd/system/writer-witness-activation-recovery.service \
    0644
atomic_install_file \
    "$ASSET_DIR/writer-witness-activation-watchdog.sh" \
    /usr/local/sbin/writer-witness-activation-watchdog \
    0755
atomic_install_file \
    "$ASSET_DIR/writer-witness-activation-watchdog.service" \
    /etc/systemd/system/writer-witness-activation-watchdog.service \
    0644
atomic_install_file \
    "$ASSET_DIR/writer-witness-activation-watchdog.timer" \
    /etc/systemd/system/writer-witness-activation-watchdog.timer \
    0644
systemctl daemon-reload
systemctl enable writer-witness-activation-recovery.service
systemctl enable --now writer-witness-activation-watchdog.timer

# Reconcile a previous power-loss journal before accepting another release.
# The operation is idempotent; a second invocation reports recovered=no.
reconcile_installed_activation

# Bootstrap credentials must predate the activation journal so the helper can
# restore their complete pre-finalization bytes after any crash.  The renderer
# is executed from the externally bound release under the isolated system
# interpreter; no ambient Python startup state participates in this trust step.
secrets_file=/etc/trading-bot-witness/bootstrap-secrets.env
credential_marker=/var/lib/trading-bot-witness/activation-state/credential-state.json
isolated_system_python \
    "$SOURCE_DIR/scripts/render_writer_witness_credentials.py" \
    --mode initialize-bootstrap \
    --bootstrap-secrets "$secrets_file" \
    >/dev/null

activation_transaction_open=false
activation_service_stopped=false
rollback_activation_transaction() {
    local original_status="${1:-1}"
    trap - ERR HUP INT TERM EXIT
    set +e
    if [[ "$activation_transaction_open" == true ]]; then
        if [[ "$activation_service_stopped" == true ]]; then
            systemctl stop writer-witness.service >/dev/null 2>&1 || true
        fi
        recovery_status=0
        installed_activation recover >/dev/null || recovery_status=$?
        if [[ "$recovery_status" -ne 0 ]]; then
            echo "Writer Witness activation rollback failed; refusing to restart services" >&2
            exit 70
        fi
        if [[ "$activation_service_stopped" == true ]]; then
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
        fi
    fi
    exit "$original_status"
}
activation_exit_guard() {
    local original_status="${1:-0}"
    if [[ "$activation_transaction_open" == true ]]; then
        if [[ "$original_status" -eq 0 ]]; then
            original_status=1
        fi
        rollback_activation_transaction "$original_status"
    fi
}
trap 'rollback_activation_transaction $?' ERR
trap 'rollback_activation_transaction 129' HUP
trap 'rollback_activation_transaction 130' INT
trap 'rollback_activation_transaction 143' TERM
trap 'activation_exit_guard $?' EXIT

for owned_path in "$release_dir" "$venv_dir" "$activation_dir"; do
    if [[ -e "$owned_path" || -L "$owned_path" ]]; then
        echo "release-owned path already exists before activation intent: $owned_path" >&2
        exit 2
    fi
done
# The helper may durably publish its journal and then fail before returning the
# candidate path. Arm the shell rollback first so that this ambiguous response
# is reconciled immediately rather than being left solely to boot recovery.
activation_transaction_open=true
activation_candidates="$(installed_activation begin \
    --release-id "$RELEASE_ID" \
    --release-dir "$release_dir" \
    --venv-dir "$venv_dir" \
    --activation-dir "$activation_dir")"

install -d -m 0755 -o root -g root "$release_dir"
cp -a "$SOURCE_DIR/." "$release_dir/"
find "$release_dir" -type d -exec chmod 0755 {} +
find "$release_dir" -type f -exec chmod 0644 {} +
chmod 0755 \
    "$release_dir/scripts/run_writer_witness_clock_jump_probe.py" \
    "$release_dir/scripts/smoke_writer_witness_client.py" \
    "$release_dir/scripts/verify_writer_witness_nftables.py" \
    "$release_dir/scripts/verify_writer_witness_release.py" \
    "$release_dir/scripts/verify_writer_witness_runtime.py" \
    "$release_dir/scripts/verify_writer_witness_runtime_provenance.py" \
    "$release_dir/scripts/verify_writer_witness_wheelhouse.py"
chown -R root:root "$release_dir"
bootstrap_attest_release "$release_dir"
isolated_system_python "$release_dir/scripts/verify_writer_witness_release.py" \
    --release-root "$release_dir" \
    --expected-manifest-sha256 "$EXPECTED_MANIFEST_SHA256" \
    --expected-uid 0 \
    --expected-gid 0 \
    >/dev/null
# Every subsequent source-controlled installation reads only from the exact,
# copied, externally bound release that was just attested.
SOURCE_DIR="$release_dir"
ASSET_DIR="$release_dir/deploy/writer-witness"
isolated_system_python "$release_dir/scripts/verify_writer_witness_wheelhouse.py" \
    --wheelhouse "$WHEELHOUSE" \
    --manifest "$ASSET_DIR/wheelhouse.sha256" \
    --expected-uid 0 \
    >/dev/null
system_runtime_manifest="$ASSET_DIR/python-runtime.json"
system_runtime_manifest_sha256="$(sha256sum "$system_runtime_manifest" | awk '{print $1}')"
expected_python_path="$(sed -n 's/^  "executable_path": "\([^"]*\)",$/\1/p' "$system_runtime_manifest")"
expected_python_version="$(sed -n 's/^  "python_version": "\([0-9][0-9.]*\)",$/\1/p' "$system_runtime_manifest")"
expected_python_sha256="$(sed -n 's/^  "executable_sha256": "\([0-9a-f]*\)",$/\1/p' "$system_runtime_manifest")"
[[ "$expected_python_version" =~ ^3\.12\.[0-9]+$ \
    && "$expected_python_path" == /usr/bin/python3.12 \
    && "$expected_python_sha256" =~ ^[0-9a-f]{64}$ \
    && "$system_runtime_manifest_sha256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "release-bound Writer Witness Python runtime identity is invalid" >&2
    exit 2
}
expected_nftables_policy_sha256="$(sed -n 's/^  "policy_sha256": "\([0-9a-f]*\)",$/\1/p' "$ASSET_DIR/nftables-policy.json")"
expected_nftables_policy_schema="$(sed -n 's/^  "schema_version": "\([A-Za-z0-9_]*\)",$/\1/p' "$ASSET_DIR/nftables-policy.json")"
[[ "$expected_nftables_policy_schema" == writer_witness_nftables_policy_v1 \
    && "$expected_nftables_policy_sha256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "release-bound Writer Witness nftables policy identity is invalid" >&2
    exit 2
}
python_executable="$(readlink -f "$expected_python_path")"
[[ -f "$python_executable" && ! -L "$python_executable" \
    && "$(stat -c '%u' "$python_executable")" == 0 \
    && "$(stat -c '%g' "$python_executable")" == 0 \
    && "$(stat -c '%h' "$python_executable")" == 1 \
    && $((8#$(stat -c '%a' "$python_executable") & 8#022)) == 0 ]] || {
    echo "Writer Witness Python executable metadata is unsafe" >&2
    exit 2
}
python_before="$(stat -c '%d:%i:%s:%Y:%Z' "$python_executable")"
[[ "$(sha256sum "$python_executable" | awk '{print $1}')" == "$expected_python_sha256" \
    && "$(stat -c '%d:%i:%s:%Y:%Z' "$python_executable")" == "$python_before" \
    && "$(/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        "$expected_python_path" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        -c 'import platform; print(platform.python_version())')" == "$expected_python_version" ]] || {
    echo "Writer Witness Python executable differs from its release-bound identity" >&2
    exit 2
}

# Close the host CPython/stdlib/ELF/loader/package boundary before any venv or
# package installer bytes are created or executed.
system_runtime_attestation="$(
    /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        "$expected_python_path" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        "$release_dir/scripts/verify_writer_witness_runtime.py" \
        --system-only \
        --system-runtime-manifest "$system_runtime_manifest" \
        --expected-system-runtime-manifest-sha256 "$system_runtime_manifest_sha256" \
        --expected-lock-uid 0
)"
[[ "$system_runtime_attestation" == *'"system_runtime_attested":"yes"'* ]] || {
    echo "Writer Witness system runtime attestation returned no evidence" >&2
    exit 2
}

/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$expected_python_path" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    -m venv --without-pip "$venv_dir"
pip_bootstrap_wheel="$WHEELHOUSE/pip-24.0-py3-none-any.whl"
[[ -f "$pip_bootstrap_wheel" && ! -L "$pip_bootstrap_wheel" ]] || {
    echo "release-bound Writer Witness pip bootstrap wheel is missing" >&2
    exit 2
}
test -z "$(find "$venv_dir/lib/python3.12/site-packages" -mindepth 1 -print -quit)"
grep -Fx 'include-system-site-packages = false' "$venv_dir/pyvenv.cfg" >/dev/null
pip_arguments=(
    install
    --quiet
    --disable-pip-version-check
    --no-cache-dir
    --no-deps
    --no-compile
    --force-reinstall
    --no-index
    --find-links "$WHEELHOUSE"
    --requirement "$ASSET_DIR/requirements.lock"
)
# Start from an empty venv and execute pip directly from the already-attested,
# release-bound wheel. No ensurepip-created or otherwise unbound pip bytes are
# executed during installation.
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$venv_dir/bin/python" -I -B -X utf8 -X pycache_prefix=/dev/null -c \
    'import runpy,sys; wheel=sys.argv.pop(1); sys.path.insert(0,wheel); sys.argv[0]="pip"; runpy.run_module("pip",run_name="__main__")' \
    "$pip_bootstrap_wheel" \
    --isolated \
    "${pip_arguments[@]}"
attest_writer_witness_runtime() {
    /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        "$venv_dir/bin/python" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        "$release_dir/scripts/verify_writer_witness_runtime.py" \
        --runtime-prefix "$venv_dir" \
        --system-runtime-manifest "$system_runtime_manifest" \
        --expected-system-runtime-manifest-sha256 "$system_runtime_manifest_sha256" \
        --requirements-lock "$ASSET_DIR/requirements.lock" \
        --expected-lock-uid 0 \
        --expected-python-version "$expected_python_version" \
        --expected-python-sha256 "$expected_python_sha256"
}
runtime_attestation_before_check="$(attest_writer_witness_runtime)"
[[ -n "$runtime_attestation_before_check" ]] || {
    echo "Writer Witness runtime attestation returned no evidence" >&2
    exit 2
}
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$venv_dir/bin/python" -I -S -B -X utf8 -X pycache_prefix=/dev/null -c \
    'import runpy,sys; site_packages=sys.argv.pop(1); sys.path.insert(0,site_packages); sys.argv=["pip","check"]; runpy.run_module("pip",run_name="__main__")' \
    "$venv_dir/lib/python3.12/site-packages" \
    >/dev/null
runtime_attestation="$(attest_writer_witness_runtime)"
[[ "$runtime_attestation" == "$runtime_attestation_before_check" ]] || {
    echo "Writer Witness runtime changed while checking dependency consistency" >&2
    exit 2
}

database_env="$activation_candidates/database.env"
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$expected_python_path" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    "$release_dir/scripts/render_writer_witness_credentials.py" \
    --mode database-env \
    --bootstrap-secrets "$secrets_file" \
    --database-env-output "$database_env" \
    >/dev/null
unset WITNESS_DB_MIGRATOR_PASSWORD WITNESS_DB_RUNTIME_PASSWORD
while IFS='=' read -r key value; do
    [[ "$value" =~ ^[0-9a-f]{64}$ ]] || {
        echo "invalid rendered Writer Witness database credential" >&2
        exit 2
    }
    case "$key" in
        WITNESS_DB_MIGRATOR_PASSWORD|WITNESS_DB_RUNTIME_PASSWORD)
            printf -v "$key" '%s' "$value"
            ;;
        *)
            echo "unexpected rendered Writer Witness database credential key" >&2
            exit 2
            ;;
    esac
done <"$database_env"
rm -f "$database_env"
[[ -n "${WITNESS_DB_MIGRATOR_PASSWORD:-}" \
    && -n "${WITNESS_DB_RUNTIME_PASSWORD:-}" ]] || {
    echo "rendered Writer Witness database credentials are incomplete" >&2
    exit 2
}

postgres_scram_verifier() {
    local password="$1"
    printf '%s' "$password" | isolated_system_python -c '
import base64
import hashlib
import hmac
import os
import sys

password = sys.stdin.buffer.read()
if len(password) != 64 or any(byte not in b"0123456789abcdef" for byte in password):
    raise SystemExit("invalid Writer Witness database password")
iterations = 4096
salt = os.urandom(16)
salted_password = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
stored_key = hashlib.sha256(client_key).digest()
server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()
encoded = lambda value: base64.b64encode(value).decode("ascii")
print(
    f"SCRAM-SHA-256${iterations}:{encoded(salt)}$"
    f"{encoded(stored_key)}:{encoded(server_key)}"
)
'
}

WITNESS_DB_MIGRATOR_VERIFIER="$(postgres_scram_verifier "$WITNESS_DB_MIGRATOR_PASSWORD")"
WITNESS_DB_RUNTIME_VERIFIER="$(postgres_scram_verifier "$WITNESS_DB_RUNTIME_PASSWORD")"
for verifier in "$WITNESS_DB_MIGRATOR_VERIFIER" "$WITNESS_DB_RUNTIME_VERIFIER"; do
    [[ "$verifier" =~ ^SCRAM-SHA-256\$4096:[A-Za-z0-9+/]{22}==\$[A-Za-z0-9+/]{43}=:[A-Za-z0-9+/]{43}=$ ]] || {
        echo "invalid generated Writer Witness PostgreSQL SCRAM verifier" >&2
        exit 2
    }
done

systemctl enable --now postgresql
if ! runuser -u postgres -- psql -XAtqc \
    "SELECT 1 FROM pg_roles WHERE rolname = 'writer_witness_migrator'" \
    | grep -qx 1
then
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 <<SQL
CREATE ROLE writer_witness_migrator LOGIN PASSWORD '$WITNESS_DB_MIGRATOR_VERIFIER' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
SQL
else
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 <<SQL
ALTER ROLE writer_witness_migrator PASSWORD '$WITNESS_DB_MIGRATOR_VERIFIER';
SQL
fi
if ! runuser -u postgres -- psql -XAtqc \
    "SELECT 1 FROM pg_roles WHERE rolname = 'writer_witness_runtime'" \
    | grep -qx 1
then
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 <<SQL
CREATE ROLE writer_witness_runtime LOGIN PASSWORD '$WITNESS_DB_RUNTIME_VERIFIER' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
SQL
else
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 <<SQL
ALTER ROLE writer_witness_runtime PASSWORD '$WITNESS_DB_RUNTIME_VERIFIER';
SQL
fi
unset WITNESS_DB_MIGRATOR_VERIFIER WITNESS_DB_RUNTIME_VERIFIER verifier
if ! runuser -u postgres -- psql -XAtqc \
    "SELECT 1 FROM pg_database WHERE datname = 'writer_witness'" \
    | grep -qx 1
then
    runuser -u postgres -- createdb --owner=writer_witness_migrator --template=template0 writer_witness
fi
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -c \
    "ALTER DATABASE writer_witness SET timezone TO 'UTC'"
if ! runuser -u postgres -- psql -XAtqc \
    "SELECT to_regclass('public.writer_witness_schema_version') IS NOT NULL" \
    writer_witness | grep -qx t
then
    PGPASSWORD="$WITNESS_DB_MIGRATOR_PASSWORD" psql \
        -Xv ON_ERROR_STOP=1 \
        -h 127.0.0.1 \
        -U writer_witness_migrator \
        -d writer_witness \
        -f "$ASSET_DIR/001_initial.sql"
fi
runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 writer_witness <<'SQL'
REVOKE ALL ON DATABASE writer_witness FROM PUBLIC;
GRANT CONNECT ON DATABASE writer_witness TO writer_witness_migrator, writer_witness_runtime;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO writer_witness_runtime;
GRANT SELECT ON writer_witness_schema_version TO writer_witness_runtime;
GRANT SELECT, UPDATE ON webapp_writer_witness_state TO writer_witness_runtime;
GRANT SELECT, INSERT ON webapp_writer_witness_receipts TO writer_witness_runtime;
SQL

private_key_file=/etc/trading-bot-witness/writer-witness-ed25519
public_key_file=/etc/trading-bot-witness/writer-witness-ed25519.pub
signing_init_root=/etc/trading-bot-witness/signing-key-initialization
install -d -m 0700 -o root -g root "$signing_init_root"
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$venv_dir/bin/python" -I -B -X utf8 -X pycache_prefix=/dev/null \
    - "$private_key_file" "$public_key_file" "$signing_init_root" \
    "$(id -u writer-witness)" <<'PY'
from pathlib import Path
import base64
import os
import re
import shutil
import stat
import sys
import uuid
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

private_path = Path(sys.argv[1])
public_path = Path(sys.argv[2])
initialization_root = Path(sys.argv[3])
writer_uid = int(sys.argv[4])


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_private(path: Path) -> Ed25519PrivateKey:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid not in {0, writer_uid}
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size < 1
            or metadata.st_size > 256
        ):
            raise SystemExit("Writer Witness signing private key is unsafe")
        raw = os.read(descriptor, 257)
    finally:
        os.close(descriptor)
    try:
        decoded = base64.b64decode(raw.strip(), validate=True)
        if len(decoded) != 32:
            raise ValueError("unexpected private-key length")
        return Ed25519PrivateKey.from_private_bytes(decoded)
    except ValueError as exc:
        raise SystemExit("Writer Witness signing private key is invalid") from exc


def create_exact(path: Path, payload: bytes, mode: int, uid: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        os.fchmod(descriptor, mode)
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count < 1:
                raise RuntimeError("short signing key write")
            written += count
        os.fchown(descriptor, uid, 0)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


root_meta = initialization_root.lstat()
if (
    not stat.S_ISDIR(root_meta.st_mode)
    or initialization_root.is_symlink()
    or root_meta.st_uid != 0
    or root_meta.st_gid != 0
    or stat.S_IMODE(root_meta.st_mode) != 0o700
):
    raise SystemExit("Writer Witness signing initialization root is unsafe")

# Every child belongs exclusively to this one initialization primitive.  A
# crash before publication can therefore be reclaimed without touching either
# stable key path or any foreign file.
for child in initialization_root.iterdir():
    metadata = child.lstat()
    if (
        not re.fullmatch(r"[0-9a-f]{32}", child.name)
        or not stat.S_ISDIR(metadata.st_mode)
        or child.is_symlink()
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise SystemExit("Writer Witness signing initialization residue is unsafe")
    shutil.rmtree(child)
fsync_directory(initialization_root)

private_exists = private_path.exists() or private_path.is_symlink()
public_exists = public_path.exists() or public_path.is_symlink()
if public_exists and not private_exists:
    raise SystemExit("Writer Witness signing public key lacks its private owner")

if private_exists:
    if private_path.is_symlink():
        raise SystemExit("Writer Witness signing private key is unsafe")
    key = read_private(private_path)
else:
    key = Ed25519PrivateKey.generate()

public_raw = key.public_key().public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw,
)

if public_exists:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(public_path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) not in {0o600, 0o644}
            or metadata.st_nlink != 1
            or metadata.st_size < 1
            or metadata.st_size > 256
        ):
            raise SystemExit("Writer Witness signing public key is unsafe")
        observed_public = os.read(descriptor, 257)
    finally:
        os.close(descriptor)
    try:
        decoded_public = base64.b64decode(observed_public.strip(), validate=True)
    except ValueError as exc:
        raise SystemExit("Writer Witness signing public key is invalid") from exc
    if decoded_public != public_raw:
        raise SystemExit("Writer Witness signing keypair does not match")

if not private_exists or not public_exists:
    operation = initialization_root / uuid.uuid4().hex
    operation.mkdir(mode=0o700)
    fsync_directory(operation)
    fsync_directory(initialization_root)
    if not private_exists:
        private_raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        staged_private = operation / "private"
        create_exact(
            staged_private,
            base64.b64encode(private_raw) + b"\n",
            0o600,
            writer_uid,
        )
        os.replace(staged_private, private_path)
        fsync_directory(private_path.parent)
    if not public_exists:
        staged_public = operation / "public"
        create_exact(staged_public, base64.b64encode(public_raw) + b"\n", 0o644, 0)
        os.replace(staged_public, public_path)
        fsync_directory(public_path.parent)
    shutil.rmtree(operation)
    fsync_directory(initialization_root)
PY
chown writer-witness:writer-witness "$private_key_file"
chmod 0600 "$private_key_file"
chown root:root "$public_key_file"
chmod 0644 "$public_key_file"
public_key="$(tr -d '\r\n' <"$public_key_file")"

tls_dir=/etc/trading-bot-witness/tls
tls_complete=true
for tls_file in ca.key ca.crt server.key server.crt; do
    if [[ ! -f "$tls_dir/$tls_file" || -L "$tls_dir/$tls_file" ]]; then
        tls_complete=false
    fi
done
if [[ "$tls_complete" != true ]]; then
    tls_present=false
    if [[ -e "$tls_dir" || -L "$tls_dir" ]]; then
        tls_present=true
    fi
    for tls_file in ca.key ca.crt server.key server.crt ca.srl server.csr; do
        if [[ -e "$tls_dir/$tls_file" || -L "$tls_dir/$tls_file" ]]; then
            tls_present=true
        fi
    done
    [[ "$tls_present" == false \
        && ! -e /opt/trading-bot-witness/active \
        && ! -L /opt/trading-bot-witness/active \
        && ! -e /srv/trading-bot-witness/current \
        && ! -L /srv/trading-bot-witness/current \
        && ! -e /opt/trading-bot-witness/venv \
        && ! -L /opt/trading-bot-witness/venv ]] || {
        echo "TLS material is incomplete; release activation refuses to rotate or repair live TLS" >&2
        exit 2
    }
    tls_generations=/etc/trading-bot-witness/tls-generations
    install -d -m 0700 -o root -g root "$tls_generations"
    isolated_system_python - "$tls_generations" /etc/trading-bot-witness <<'PY'
from pathlib import Path
import os
import re
import shutil
import stat
import sys

root = Path(sys.argv[1])
parent = Path(sys.argv[2])
metadata = root.lstat()
if (
    not stat.S_ISDIR(metadata.st_mode)
    or root.is_symlink()
    or metadata.st_uid != 0
    or metadata.st_gid != 0
    or stat.S_IMODE(metadata.st_mode) != 0o700
):
    raise SystemExit("Writer Witness TLS generation root is unsafe")
for child in root.iterdir():
    child_metadata = child.lstat()
    if (
        not re.fullmatch(r"(?:initializing|generation)-[0-9a-f]{32}", child.name)
        or not stat.S_ISDIR(child_metadata.st_mode)
        or child.is_symlink()
        or child_metadata.st_uid != 0
        or child_metadata.st_gid != 0
        or stat.S_IMODE(child_metadata.st_mode) != 0o700
    ):
        raise SystemExit("Writer Witness TLS generation residue is unsafe")
    shutil.rmtree(child)
for temporary in parent.glob(".tls.initialize-*"):
    temporary_metadata = temporary.lstat()
    if (
        not re.fullmatch(r"\.tls\.initialize-[0-9a-f]{32}", temporary.name)
        or not stat.S_ISLNK(temporary_metadata.st_mode)
        or temporary_metadata.st_uid != 0
        or temporary_metadata.st_gid != 0
    ):
        raise SystemExit("Writer Witness TLS symlink residue is unsafe")
    temporary.unlink()
for directory in (root, parent):
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
PY
    tls_token="$(openssl rand -hex 16)"
    [[ "$tls_token" =~ ^[0-9a-f]{32}$ ]]
    tls_staging="$tls_generations/initializing-$tls_token"
    tls_generation="$tls_generations/generation-$tls_token"
    install -d -m 0700 -o root -g root "$tls_staging"
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes \
        -days 3650 \
        -subj '/CN=Trading Bot Private Writer Witness CA' \
        -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -addext 'subjectKeyIdentifier=hash' \
        -keyout "$tls_staging/ca.key" \
        -out "$tls_staging/ca.crt"
    openssl req -new -newkey rsa:3072 -sha256 -nodes \
        -subj '/CN=writer-witness.internal' \
        -addext "subjectAltName=IP:$WITNESS_PUBLIC_IP" \
        -addext 'basicConstraints=critical,CA:FALSE' \
        -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
        -addext 'extendedKeyUsage=serverAuth' \
        -keyout "$tls_staging/server.key" \
        -out "$tls_staging/server.csr"
    openssl x509 -req \
        -in "$tls_staging/server.csr" \
        -CA "$tls_staging/ca.crt" \
        -CAkey "$tls_staging/ca.key" \
        -CAcreateserial \
        -days 397 \
        -sha256 \
        -copy_extensions copyall \
        -out "$tls_staging/server.crt"
    rm -f "$tls_staging/server.csr" "$tls_staging/ca.srl"
    chmod 0600 "$tls_staging/ca.key" "$tls_staging/server.key"
    chmod 0644 "$tls_staging/ca.crt" "$tls_staging/server.crt"
    openssl verify -CAfile "$tls_staging/ca.crt" "$tls_staging/server.crt"
    openssl x509 -in "$tls_staging/server.crt" -purpose -noout \
        | grep -q '^SSL server : Yes$'
    fsync_trees "$tls_staging"
    mv -T "$tls_staging" "$tls_generation"
    fsync_directories "$tls_generations"
    tls_link_tmp="/etc/trading-bot-witness/.tls.initialize-$tls_token"
    ln -s "$tls_generation" "$tls_link_tmp"
    mv -T "$tls_link_tmp" "$tls_dir"
    fsync_directories /etc/trading-bot-witness
fi
chmod 0600 "$tls_dir/ca.key" "$tls_dir/server.key"
chmod 0644 "$tls_dir/ca.crt" "$tls_dir/server.crt"
openssl verify -CAfile "$tls_dir/ca.crt" "$tls_dir/server.crt"
openssl x509 -in "$tls_dir/server.crt" -purpose -noout \
    | grep -q '^SSL server : Yes$'

client_dir=/root/writer-witness-client-material
atomic_install_file "$tls_dir/ca.crt" "$activation_candidates/witness-ca.crt" 0644
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$expected_python_path" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    "$release_dir/scripts/render_writer_witness_credentials.py" \
    --mode prepare \
    --runtime-env "$activation_candidates/runtime.env" \
    --client-dir "$activation_candidates" \
    --current-runtime-env /etc/trading-bot-witness/runtime.env \
    --current-client-dir "$client_dir" \
    --bootstrap-secrets "$secrets_file" \
    --marker "$credential_marker" \
    --hmac-state-root /var/lib/trading-bot-witness/hmac-rotation \
    --rotation-lock-fd "$rotation_lock_fd" \
    --internal-url "https://$WITNESS_PUBLIC_IP" \
    --public-key "$public_key" \
    --private-key-file "$private_key_file" \
    >/dev/null

nginx_target="$activation_candidates/nginx-writer-witness"
sed \
    -e "s/__WEBAPP_FI_SOURCE_IP__/$WEBAPP_FI_SOURCE_IP/g" \
    -e "s/__WEBAPP_IR_SOURCE_IP__/$WEBAPP_IR_SOURCE_IP/g" \
    -e "s/__WITNESS_PUBLIC_IP__/$WITNESS_PUBLIC_IP/g" \
    "$ASSET_DIR/nginx.conf.template" >"$nginx_target"
chmod 0644 "$nginx_target"

# Firewall mutation is intentionally outside release activation.  The host
# bootstrap/rotation transaction must establish the approved policy first;
# this release step is read-only and fails closed on any effective drift.
nft -j list ruleset \
    | /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
        "$expected_python_path" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
        "$release_dir/scripts/verify_writer_witness_nftables.py" \
        --expected-policy-sha256 "$expected_nftables_policy_sha256" \
        >/dev/null

atomic_install_file "$ASSET_DIR/writer-witness-backup.sh" "$activation_candidates/writer-witness-backup" 0755
atomic_install_file "$ASSET_DIR/writer-witness-offsite-backup.sh" "$activation_candidates/writer-witness-offsite-backup" 0755
atomic_install_file "$ASSET_DIR/writer-witness-s3-put.py" "$activation_candidates/writer-witness-s3-put" 0755
atomic_install_file "$ASSET_DIR/writer-witness-rotate-hmac.py" "$activation_candidates/writer-witness-rotate-hmac" 0755
atomic_install_file "$ASSET_DIR/writer-witness-live-restore.sh" "$activation_candidates/writer-witness-live-restore" 0755
atomic_install_file "$ASSET_DIR/writer-witness-matrix-campaign.py" "$activation_candidates/writer-witness-matrix-campaign" 0755
atomic_install_file "$ASSET_DIR/writer-witness-matrix-host-faults.sh" "$activation_candidates/writer-witness-matrix-host-faults" 0755
atomic_install_file "$ASSET_DIR/writer-witness-matrix-host-fault-state.py" "$activation_candidates/writer-witness-matrix-host-fault-state" 0755
atomic_install_file "$ASSET_DIR/writer-witness-state-manifest.sh" "$activation_candidates/writer-witness-state-manifest" 0755
atomic_install_file "$ASSET_DIR/writer-witness-restore-drill.sh" "$activation_candidates/writer-witness-restore-drill" 0755
atomic_install_file "$SOURCE_DIR/scripts/smoke_writer_witness_client.py" "$activation_candidates/writer-witness-smoke-client" 0755
atomic_install_file "$ASSET_DIR/writer-witness.service" "$activation_candidates/writer-witness.service" 0644
atomic_install_file "$ASSET_DIR/writer-witness-backup.service" "$activation_candidates/writer-witness-backup.service" 0644
atomic_install_file "$ASSET_DIR/writer-witness-backup.timer" "$activation_candidates/writer-witness-backup.timer" 0644
atomic_install_file "$ASSET_DIR/writer-witness-offsite-backup.service" "$activation_candidates/writer-witness-offsite-backup.service" 0644
atomic_install_file "$ASSET_DIR/writer-witness-offsite-backup.timer" "$activation_candidates/writer-witness-offsite-backup.timer" 0644
fsync_trees "$activation_candidates"

# The service consumes one atomic activation pointer that binds code and its
# exact Python runtime.  The transaction helper first normalizes a legacy host
# into a rollback activation, then publishes host-global files and the new
# pointer under one durable journal.
assert_no_writer_witness_systemd_dropins
install -d -m 0755 -o root -g root "$activation_dir"
ln -s "$release_dir" "$activation_dir/release"
ln -s "$venv_dir" "$activation_dir/venv"
wheelhouse_manifest_sha256="$(sha256sum "$ASSET_DIR/wheelhouse.sha256" | awk '{print $1}')"
requirements_lock_sha256="$(sha256sum "$ASSET_DIR/requirements.lock" | awk '{print $1}')"
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$venv_dir/bin/python" -I -S -B -X utf8 -X pycache_prefix=/dev/null - \
    "$activation_dir/runtime-provenance.json" \
    "$runtime_attestation" \
    "$EXPECTED_MANIFEST_SHA256" \
    "$wheelhouse_manifest_sha256" \
    "$requirements_lock_sha256" \
    "$expected_python_version" \
    "$expected_python_sha256" \
    "$system_runtime_manifest_sha256" <<'PY'
from pathlib import Path
import json
import os
import re
import sys

(
    destination_raw,
    runtime_raw,
    release_manifest_sha256,
    wheelhouse_manifest_sha256,
    requirements_lock_sha256,
    expected_python_version,
    expected_python_sha256,
    expected_system_runtime_manifest_sha256,
) = sys.argv[1:]
runtime = json.loads(runtime_raw)
if (
    not isinstance(runtime, dict)
    or runtime.get("runtime_attested") != "yes"
    or runtime.get("python_version") != expected_python_version
    or runtime.get("python_sha256") != expected_python_sha256
    or runtime.get("requirements_lock_sha256") != requirements_lock_sha256
    or runtime.get("system_runtime_attested") != "yes"
    or runtime.get("system_runtime_manifest_sha256")
        != expected_system_runtime_manifest_sha256
    or not re.fullmatch(r"[0-9a-f]{64}", str(runtime.get("system_runtime_sha256", "")))
    or not re.fullmatch(r"[0-9a-f]{64}", str(runtime.get("runtime_sha256", "")))
    or runtime.get("bootstrap_extra_count") != 0
):
    raise SystemExit("Writer Witness runtime evidence is not bound to its release inputs")
payload = {
    "release_manifest_sha256": release_manifest_sha256,
    "requirements_lock_sha256": requirements_lock_sha256,
    "runtime": runtime,
    "schema_version": "writer_witness_runtime_provenance_v2",
    "system_runtime_manifest_sha256": expected_system_runtime_manifest_sha256,
    "wheelhouse_manifest_sha256": wheelhouse_manifest_sha256,
}
encoded = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
destination = Path(destination_raw)
descriptor = os.open(
    destination,
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0),
    0o644,
)
try:
    written = 0
    while written < len(encoded):
        written += os.write(descriptor, encoded[written:])
    os.fchmod(descriptor, 0o644)
    os.fsync(descriptor)
finally:
    os.close(descriptor)
directory = os.open(destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(directory)
finally:
    os.close(directory)
PY
/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$venv_dir/bin/python" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    "$release_dir/scripts/verify_writer_witness_runtime_provenance.py" \
    --provenance "$activation_dir/runtime-provenance.json" \
    --runtime-attestation-json "$runtime_attestation" \
    --expected-release-manifest-sha256 "$EXPECTED_MANIFEST_SHA256" \
    --expected-wheelhouse-manifest-sha256 "$wheelhouse_manifest_sha256" \
    --expected-requirements-lock-sha256 "$requirements_lock_sha256" \
    --expected-python-version "$expected_python_version" \
    --expected-python-sha256 "$expected_python_sha256" \
    --expected-system-runtime-manifest-sha256 "$system_runtime_manifest_sha256" \
    --expected-uid 0 \
    --expected-gid 0 \
    >/dev/null
test "$(readlink -f "$activation_dir/release")" = "$release_dir"
test "$(readlink -f "$activation_dir/venv")" = "$venv_dir"
fsync_trees "$release_dir" "$venv_dir" "$activation_dir"
fsync_directories \
    /srv/trading-bot-witness/releases \
    /opt/trading-bot-witness/venvs \
    "$activation_root"

# No public process or backup timer may observe the serial host-global file
# publication.  The Writer Witness candidate is validated on loopback while
# Nginx remains stopped; the watchdog reconciles a killed provisioner within
# one timer interval after the provision lock is released.
activation_service_stopped=true
for unit in \
    nginx \
    writer-witness.service \
    writer-witness-backup.timer \
    writer-witness-offsite-backup.timer
do
    load_state="$(systemctl show -p LoadState --value "$unit" 2>/dev/null || true)"
    if [[ "$load_state" == not-found || -z "$load_state" ]]; then
        continue
    fi
    systemctl stop "$unit"
    active_state="$(systemctl show -p ActiveState --value "$unit")"
    [[ "$active_state" == inactive || "$active_state" == failed ]] || {
        echo "Writer Witness activation could not quiesce $unit" >&2
        exit 70
    }
done
installed_activation publish --release-id "$RELEASE_ID" >/dev/null
nginx -t
systemctl daemon-reload
assert_no_writer_witness_systemd_dropins
[[ "$(systemctl show -p FragmentPath --value writer-witness.service)" == \
    /etc/systemd/system/writer-witness.service ]]
[[ -z "$(systemctl show -p DropInPaths --value writer-witness.service)" ]]
for property in \
    User:writer-witness \
    Group:writer-witness \
    WorkingDirectory:/opt/trading-bot-witness/active/release \
    NoNewPrivileges:yes \
    PrivateTmp:yes \
    PrivateDevices:yes \
    ProtectSystem:strict \
    ProtectHome:yes \
    MemoryDenyWriteExecute:yes \
    RestrictSUIDSGID:yes \
    LockPersonality:yes \
    UMask:0077
do
    key="${property%%:*}"
    expected="${property#*:}"
    [[ "$(systemctl show -p "$key" --value writer-witness.service)" == "$expected" ]]
done
systemctl show -p ExecStart --value writer-witness.service \
    | grep -F '/opt/trading-bot-witness/active/venv/bin/python' >/dev/null
test "$(readlink -f /opt/trading-bot-witness/active)" = "$activation_dir"
test "$(readlink -f /srv/trading-bot-witness/current)" = "$release_dir"
test "$(readlink -f /opt/trading-bot-witness/venv)" = "$venv_dir"

systemctl enable \
    nginx \
    writer-witness.service \
    writer-witness-backup.timer \
    writer-witness-offsite-backup.timer
systemctl start writer-witness.service
systemctl restart writer-witness.service

for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error http://127.0.0.1:8011/health/ready >/dev/null; then
        break
    fi
    if [[ "$attempt" -eq 30 ]]; then
        systemctl status --no-pager writer-witness.service >&2 || true
        journalctl -u writer-witness.service -n 100 --no-pager >&2 || true
        exit 1
    fi
    sleep 1
done

/usr/local/sbin/writer-witness-backup >/dev/null
/usr/local/sbin/writer-witness-restore-drill

runtime_ddl="$(PGPASSWORD="$WITNESS_DB_RUNTIME_PASSWORD" psql \
    -XAtqc "SELECT has_database_privilege(current_user, current_database(), 'CREATE')" \
    -h 127.0.0.1 -U writer_witness_runtime -d writer_witness)"
runtime_super="$(runuser -u postgres -- psql -XAtqc \
    "SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname = 'writer_witness_runtime'")"
[[ "$runtime_ddl" == "f" ]]
[[ "$runtime_super" == "f" ]]

/usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    "$expected_python_path" -I -S -B -X utf8 -X pycache_prefix=/dev/null \
    "$release_dir/scripts/render_writer_witness_credentials.py" \
    --mode finalize \
    --current-runtime-env /etc/trading-bot-witness/runtime.env \
    --current-client-dir "$client_dir" \
    --bootstrap-secrets "$secrets_file" \
    --marker "$credential_marker" \
    --hmac-state-root /var/lib/trading-bot-witness/hmac-rotation \
    --rotation-lock-fd "$rotation_lock_fd" \
    >/dev/null
installed_activation commit --release-id "$RELEASE_ID" >/dev/null
activation_transaction_open=false

# A committed journal remains durable until the public daemons and timers are
# running.  If this shell dies in the gap, the periodic watchdog performs this
# same idempotent completion path.
systemctl enable --now \
    nginx \
    writer-witness.service \
    writer-witness-backup.timer \
    writer-witness-offsite-backup.timer
systemctl restart nginx writer-witness.service
curl --fail --silent --show-error \
    --retry 30 --retry-delay 1 --retry-all-errors \
    http://127.0.0.1:8011/health/ready >/dev/null
installed_activation complete --release-id "$RELEASE_ID" >/dev/null
activation_service_stopped=false
flock -u "$rotation_lock_fd"
exec {rotation_lock_fd}>&-
trap - ERR HUP INT TERM EXIT

# Optional SSH hardening is deliberately outside the release activation
# transaction.  Both files are atomically published only after the Writer
# Witness generation itself is durably committed.
if [[ "$HARDEN_SSH" == "true" ]]; then
    source_authorized_keys="$(getent passwd "$SSH_KEY_SOURCE_USER" | cut -d: -f6)/.ssh/authorized_keys"
    if [[ ! -s "$source_authorized_keys" ]]; then
        echo "cannot harden SSH without a non-empty source authorized_keys file" >&2
        exit 1
    fi
    install -d -m 0700 -o root -g root /root/.ssh
    atomic_install_file "$source_authorized_keys" /root/.ssh/authorized_keys 0600
    ssh_hardening_candidate="/var/lib/trading-bot-witness/activation-state/ssh-hardening-$RELEASE_ID.conf"
    cat >"$ssh_hardening_candidate" <<'EOF'
PubkeyAuthentication yes
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
EOF
    chmod 0600 "$ssh_hardening_candidate"
    fsync_trees /var/lib/trading-bot-witness/activation-state
    sshd -t \
        -o PubkeyAuthentication=yes \
        -o PermitRootLogin=prohibit-password \
        -o PasswordAuthentication=no \
        -o KbdInteractiveAuthentication=no \
        -o PermitEmptyPasswords=no
    atomic_install_file \
        "$ssh_hardening_candidate" \
        /etc/ssh/sshd_config.d/00-writer-witness-hardening.conf \
        0644
    sshd -t
    effective_password_auth="$(sshd -T | awk '$1 == "passwordauthentication" {value=$2} END {print value}')"
    effective_root_login="$(sshd -T | awk '$1 == "permitrootlogin" {value=$2} END {print value}')"
    [[ "$effective_password_auth" == "no" ]]
    [[ "$effective_root_login" == "without-password" || "$effective_root_login" == "prohibit-password" ]]
    systemctl reload ssh
fi

printf '{"status":"ready-dark","release":"%s","public_ip":"%s","webapp_flags_changed":false,"cdn_changed":false}\n' \
    "$RELEASE_ID" "$WITNESS_PUBLIC_IP"
