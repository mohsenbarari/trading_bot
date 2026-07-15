#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "provision_writer_witness_host.sh must run as root" >&2
    exit 2
fi

SOURCE_DIR="${WRITER_WITNESS_SOURCE_DIR:-}"
WITNESS_PUBLIC_IP="${WRITER_WITNESS_PUBLIC_IP:-}"
WEBAPP_FI_SOURCE_IP="${WRITER_WITNESS_WEBAPP_FI_SOURCE_IP:-}"
WEBAPP_IR_SOURCE_IP="${WRITER_WITNESS_WEBAPP_IR_SOURCE_IP:-}"
RELEASE_ID="${WRITER_WITNESS_RELEASE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
HARDEN_SSH="${WRITER_WITNESS_HARDEN_SSH:-false}"
SSH_KEY_SOURCE_USER="${WRITER_WITNESS_SSH_KEY_SOURCE_USER:-ubuntu}"
WHEELHOUSE="${WRITER_WITNESS_WHEELHOUSE:-}"
ROTATE_TLS="${WRITER_WITNESS_ROTATE_TLS:-false}"

for value_name in SOURCE_DIR WITNESS_PUBLIC_IP WEBAPP_FI_SOURCE_IP WEBAPP_IR_SOURCE_IP; do
    value="${!value_name}"
    if [[ -z "$value" ]]; then
        echo "$value_name is required" >&2
        exit 2
    fi
done
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
if [[ -n "$WHEELHOUSE" ]]; then
    if [[ ! -d "$WHEELHOUSE" || ! -f "$WHEELHOUSE/SHA256SUMS" ]]; then
        echo "WRITER_WITNESS_WHEELHOUSE requires a directory with SHA256SUMS" >&2
        exit 2
    fi
    (
        cd "$WHEELHOUSE"
        sha256sum --check SHA256SUMS
    )
fi
python3 - "$WITNESS_PUBLIC_IP" "$WEBAPP_FI_SOURCE_IP" "$WEBAPP_IR_SOURCE_IP" <<'PY'
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
    "$ASSET_DIR/nginx.conf.template" \
    "$ASSET_DIR/writer-witness.service"
do
    if [[ ! -f "$required" ]]; then
        echo "missing release artifact: $required" >&2
        exit 2
    fi
done
python3 - "$SOURCE_DIR" <<'PY'
from pathlib import Path
import hashlib
import json
import sys

root = Path(sys.argv[1]).resolve()
manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
if not isinstance(manifest, dict) or not manifest:
    raise SystemExit("writer witness release manifest is empty or invalid")
actual = {}
for path in sorted(item for item in root.rglob("*") if item.is_file()):
    relative = path.relative_to(root).as_posix()
    if relative == "release-manifest.json":
        continue
    actual[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != manifest:
    missing = sorted(set(manifest) - set(actual))
    unexpected = sorted(set(actual) - set(manifest))
    changed = sorted(key for key in set(actual) & set(manifest) if actual[key] != manifest[key])
    raise SystemExit(
        f"writer witness release integrity failed: missing={missing} "
        f"unexpected={unexpected} changed={changed}"
    )
PY

export DEBIAN_FRONTEND=noninteractive
apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
    ca-certificates \
    nginx \
    openssl \
    postgresql \
    postgresql-client \
    python3 \
    python3-venv \
    ufw

if ! id -u writer-witness >/dev/null 2>&1; then
    useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin writer-witness
fi
install -d -m 0755 -o root -g root /opt/trading-bot-witness /srv/trading-bot-witness/releases
install -d -m 0750 -o root -g writer-witness /etc/trading-bot-witness
install -d -m 0700 -o root -g root /etc/trading-bot-witness/tls /root/writer-witness-client-material
install -d -m 0700 -o root -g root /var/backups/trading-bot-witness

release_dir="/srv/trading-bot-witness/releases/$RELEASE_ID"
if [[ -e "$release_dir" ]]; then
    echo "release already exists: $release_dir" >&2
    exit 2
fi
install -d -m 0755 -o root -g root "$release_dir"
cp -a "$SOURCE_DIR/." "$release_dir/"
find "$release_dir" -type d -exec chmod 0755 {} +
find "$release_dir" -type f -exec chmod 0644 {} +
chown -R root:root "$release_dir"

if [[ ! -x /opt/trading-bot-witness/venv/bin/python ]]; then
    python3 -m venv /opt/trading-bot-witness/venv
fi
pip_arguments=(
    install
    --quiet
    --disable-pip-version-check
    --no-cache-dir
    --requirement "$ASSET_DIR/requirements.lock"
)
if [[ -n "$WHEELHOUSE" ]]; then
    pip_arguments+=(--no-index --find-links "$WHEELHOUSE")
fi
/opt/trading-bot-witness/venv/bin/pip "${pip_arguments[@]}"

secrets_file=/etc/trading-bot-witness/bootstrap-secrets.env
if [[ ! -f "$secrets_file" ]]; then
    umask 077
    {
        printf 'WITNESS_DB_MIGRATOR_PASSWORD=%s\n' "$(openssl rand -hex 32)"
        printf 'WITNESS_DB_RUNTIME_PASSWORD=%s\n' "$(openssl rand -hex 32)"
        printf 'WITNESS_FI_KEY_ID=webapp-fi-v1\n'
        printf 'WITNESS_FI_HMAC_SECRET=%s\n' "$(openssl rand -hex 32)"
        printf 'WITNESS_IR_KEY_ID=webapp-ir-v1\n'
        printf 'WITNESS_IR_HMAC_SECRET=%s\n' "$(openssl rand -hex 32)"
    } >"$secrets_file"
fi
chmod 0600 "$secrets_file"
# shellcheck disable=SC1090
source "$secrets_file"

systemctl enable --now postgresql
if ! runuser -u postgres -- psql -XAtqc \
    "SELECT 1 FROM pg_roles WHERE rolname = 'writer_witness_migrator'" \
    | grep -qx 1
then
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -c \
        "CREATE ROLE writer_witness_migrator LOGIN PASSWORD '$WITNESS_DB_MIGRATOR_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
else
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -c \
        "ALTER ROLE writer_witness_migrator PASSWORD '$WITNESS_DB_MIGRATOR_PASSWORD'"
fi
if ! runuser -u postgres -- psql -XAtqc \
    "SELECT 1 FROM pg_roles WHERE rolname = 'writer_witness_runtime'" \
    | grep -qx 1
then
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -c \
        "CREATE ROLE writer_witness_runtime LOGIN PASSWORD '$WITNESS_DB_RUNTIME_PASSWORD' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
else
    runuser -u postgres -- psql -Xv ON_ERROR_STOP=1 -c \
        "ALTER ROLE writer_witness_runtime PASSWORD '$WITNESS_DB_RUNTIME_PASSWORD'"
fi
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
if [[ ! -f "$private_key_file" || ! -f "$public_key_file" ]]; then
    /opt/trading-bot-witness/venv/bin/python - "$private_key_file" "$public_key_file" <<'PY'
from pathlib import Path
import base64
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

private_path, public_path = map(Path, sys.argv[1:])
key = Ed25519PrivateKey.generate()
private_raw = key.private_bytes(
    serialization.Encoding.Raw,
    serialization.PrivateFormat.Raw,
    serialization.NoEncryption(),
)
public_raw = key.public_key().public_bytes(
    serialization.Encoding.Raw,
    serialization.PublicFormat.Raw,
)
private_path.write_text(base64.b64encode(private_raw).decode("ascii") + "\n")
public_path.write_text(base64.b64encode(public_raw).decode("ascii") + "\n")
PY
fi
chown writer-witness:writer-witness "$private_key_file"
chmod 0600 "$private_key_file"
chown root:root "$public_key_file"
chmod 0644 "$public_key_file"
public_key="$(tr -d '\r\n' <"$public_key_file")"

tls_dir=/etc/trading-bot-witness/tls
if [[ "$ROTATE_TLS" == "true" ]]; then
    rm -f \
        "$tls_dir/ca.key" \
        "$tls_dir/ca.crt" \
        "$tls_dir/ca.srl" \
        "$tls_dir/server.key" \
        "$tls_dir/server.csr" \
        "$tls_dir/server.crt"
fi
if [[ ! -f "$tls_dir/ca.key" || ! -f "$tls_dir/ca.crt" ]]; then
    openssl req -x509 -newkey rsa:3072 -sha256 -nodes \
        -days 3650 \
        -subj '/CN=Trading Bot Private Writer Witness CA' \
        -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
        -addext 'keyUsage=critical,keyCertSign,cRLSign' \
        -addext 'subjectKeyIdentifier=hash' \
        -keyout "$tls_dir/ca.key" \
        -out "$tls_dir/ca.crt"
fi
if [[ ! -f "$tls_dir/server.key" || ! -f "$tls_dir/server.crt" ]]; then
    openssl req -new -newkey rsa:3072 -sha256 -nodes \
        -subj '/CN=writer-witness.internal' \
        -addext "subjectAltName=IP:$WITNESS_PUBLIC_IP" \
        -addext 'basicConstraints=critical,CA:FALSE' \
        -addext 'keyUsage=critical,digitalSignature,keyEncipherment' \
        -addext 'extendedKeyUsage=serverAuth' \
        -keyout "$tls_dir/server.key" \
        -out "$tls_dir/server.csr"
    openssl x509 -req \
        -in "$tls_dir/server.csr" \
        -CA "$tls_dir/ca.crt" \
        -CAkey "$tls_dir/ca.key" \
        -CAcreateserial \
        -days 397 \
        -sha256 \
        -copy_extensions copyall \
        -out "$tls_dir/server.crt"
fi
chmod 0600 "$tls_dir/ca.key" "$tls_dir/server.key"
chmod 0644 "$tls_dir/ca.crt" "$tls_dir/server.crt"
openssl verify -CAfile "$tls_dir/ca.crt" "$tls_dir/server.crt"
openssl x509 -in "$tls_dir/server.crt" -purpose -noout \
    | grep -q '^SSL server : Yes$'

runtime_env=/etc/trading-bot-witness/runtime.env
umask 077
cat >"$runtime_env" <<EOF
LOGICAL_AUTHORITY=webapp
PHYSICAL_SITE=webapp_ir
WRITER_WITNESS_SERVICE_ENABLED=true
WRITER_WITNESS_DATABASE_URL=postgresql+asyncpg://writer_witness_runtime:$WITNESS_DB_RUNTIME_PASSWORD@127.0.0.1:5432/writer_witness
WRITER_WITNESS_PRODUCT_DATABASE_USER=trading_bot_product
WRITER_WITNESS_REQUIRE_DISTINCT_DATABASE_IDENTITY=true
WRITER_WITNESS_PRIVATE_KEY_FILE=$private_key_file
WRITER_WITNESS_PUBLIC_KEY=$public_key
WRITER_WITNESS_SERVICE_WEBAPP_FI_KEY_ID=$WITNESS_FI_KEY_ID
WRITER_WITNESS_SERVICE_WEBAPP_FI_SECRET=$WITNESS_FI_HMAC_SECRET
WRITER_WITNESS_SERVICE_WEBAPP_IR_KEY_ID=$WITNESS_IR_KEY_ID
WRITER_WITNESS_SERVICE_WEBAPP_IR_SECRET=$WITNESS_IR_HMAC_SECRET
WRITER_WITNESS_LEASE_DURATION_SECONDS=180
WRITER_WITNESS_RENEW_INTERVAL_SECONDS=30
WRITER_WITNESS_SAFETY_MARGIN_SECONDS=15
WRITER_WITNESS_MAX_CLOCK_SKEW_SECONDS=5
WRITER_WITNESS_AUTH_MAX_AGE_SECONDS=15
WRITER_WITNESS_AUTHORITATIVE_SITE=webapp_ir
EOF
chown root:root "$runtime_env"
chmod 0600 "$runtime_env"

client_dir=/root/writer-witness-client-material
install -m 0644 "$tls_dir/ca.crt" "$client_dir/witness-ca.crt"
cat >"$client_dir/webapp-fi.env" <<EOF
WRITER_WITNESS_INTERNAL_URL=https://$WITNESS_PUBLIC_IP
WRITER_WITNESS_CLIENT_KEY_ID=$WITNESS_FI_KEY_ID
WRITER_WITNESS_CLIENT_SECRET=$WITNESS_FI_HMAC_SECRET
WRITER_WITNESS_PUBLIC_KEY=$public_key
WRITER_WITNESS_VERIFY_TLS=true
WRITER_WITNESS_CA_BUNDLE=/run/secrets/witness-ca.pem
EOF
cat >"$client_dir/webapp-ir.env" <<EOF
WRITER_WITNESS_INTERNAL_URL=https://$WITNESS_PUBLIC_IP
WRITER_WITNESS_CLIENT_KEY_ID=$WITNESS_IR_KEY_ID
WRITER_WITNESS_CLIENT_SECRET=$WITNESS_IR_HMAC_SECRET
WRITER_WITNESS_PUBLIC_KEY=$public_key
WRITER_WITNESS_VERIFY_TLS=true
WRITER_WITNESS_CA_BUNDLE=/run/secrets/witness-ca.pem
EOF
chmod 0600 "$client_dir/webapp-fi.env" "$client_dir/webapp-ir.env"

nginx_target=/etc/nginx/sites-available/writer-witness
sed \
    -e "s/__WEBAPP_FI_SOURCE_IP__/$WEBAPP_FI_SOURCE_IP/g" \
    -e "s/__WEBAPP_IR_SOURCE_IP__/$WEBAPP_IR_SOURCE_IP/g" \
    -e "s/__WITNESS_PUBLIC_IP__/$WITNESS_PUBLIC_IP/g" \
    "$ASSET_DIR/nginx.conf.template" >"$nginx_target"
chmod 0644 "$nginx_target"
ln -sfn "$nginx_target" /etc/nginx/sites-enabled/writer-witness
rm -f /etc/nginx/sites-enabled/default

install -m 0644 "$ASSET_DIR/writer-witness.service" /etc/systemd/system/writer-witness.service
install -m 0755 "$ASSET_DIR/writer-witness-backup.sh" /usr/local/sbin/writer-witness-backup
install -m 0755 "$ASSET_DIR/writer-witness-restore-drill.sh" /usr/local/sbin/writer-witness-restore-drill
install -m 0644 "$ASSET_DIR/writer-witness-backup.service" /etc/systemd/system/writer-witness-backup.service
install -m 0644 "$ASSET_DIR/writer-witness-backup.timer" /etc/systemd/system/writer-witness-backup.timer

ln -sfn "$release_dir" /srv/trading-bot-witness/current.new
mv -Tf /srv/trading-bot-witness/current.new /srv/trading-bot-witness/current

nginx -t
systemctl daemon-reload
systemctl enable --now nginx writer-witness.service writer-witness-backup.timer
systemctl restart nginx writer-witness.service

if [[ "$HARDEN_SSH" == "true" ]]; then
    source_authorized_keys="$(getent passwd "$SSH_KEY_SOURCE_USER" | cut -d: -f6)/.ssh/authorized_keys"
    if [[ ! -s "$source_authorized_keys" ]]; then
        echo "cannot harden SSH without a non-empty source authorized_keys file" >&2
        exit 1
    fi
    install -d -m 0700 -o root -g root /root/.ssh
    install -m 0600 -o root -g root "$source_authorized_keys" /root/.ssh/authorized_keys
    cat >/etc/ssh/sshd_config.d/00-writer-witness-hardening.conf <<'EOF'
PubkeyAuthentication yes
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitEmptyPasswords no
EOF
    sshd -t
    effective_password_auth="$(sshd -T | awk '$1 == "passwordauthentication" {value=$2} END {print value}')"
    effective_root_login="$(sshd -T | awk '$1 == "permitrootlogin" {value=$2} END {print value}')"
    [[ "$effective_password_auth" == "no" ]]
    [[ "$effective_root_login" == "without-password" || "$effective_root_login" == "prohibit-password" ]]
    systemctl reload ssh
fi

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow from "$WEBAPP_FI_SOURCE_IP" to any port 443 proto tcp comment 'writer-witness-webapp-fi'
ufw allow from "$WEBAPP_IR_SOURCE_IP" to any port 443 proto tcp comment 'writer-witness-webapp-ir'
ufw --force enable

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

printf '{"status":"ready-dark","release":"%s","public_ip":"%s","webapp_flags_changed":false,"cdn_changed":false}\n' \
    "$RELEASE_ID" "$WITNESS_PUBLIC_IP"
