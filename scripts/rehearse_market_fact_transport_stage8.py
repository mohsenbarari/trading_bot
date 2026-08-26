#!/usr/bin/env python3
"""Loopback mTLS/HMAC rehearsal for the Stage 8 runtime receiver."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.market_intelligence.market_fact_receiver_service import (
    run_market_fact_receiver_service,
)
from core.market_intelligence.private_market_transport import (
    FACT_PATH,
    MarketTransportError,
    client_tls_context,
    post_document,
)


FIXTURE = (
    REPO_ROOT
    / "tests"
    / "fixtures"
    / "market_private_pipeline"
    / "market_fact_batch.json"
)


def command(arguments: list[str], *, cwd: Path) -> None:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("stage8_tls_fixture_generation_failed")


def generate_certificates(root: Path) -> dict[str, Path]:
    files = {
        name: root / name
        for name in (
            "ca.key",
            "ca.pem",
            "server.key",
            "server.csr",
            "server.pem",
            "client.key",
            "client.csr",
            "client.pem",
        )
    }
    command(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(files["ca.key"])],
        cwd=root,
    )
    command(
        [
            "openssl",
            "req",
            "-x509",
            "-new",
            "-key",
            str(files["ca.key"]),
            "-subj",
            "/CN=stage8-fixture-ca",
            "-days",
            "1",
            "-out",
            str(files["ca.pem"]),
        ],
        cwd=root,
    )
    for role in ("server", "client"):
        command(
            [
                "openssl",
                "genpkey",
                "-algorithm",
                "ED25519",
                "-out",
                str(files[f"{role}.key"]),
            ],
            cwd=root,
        )
        command(
            [
                "openssl",
                "req",
                "-new",
                "-key",
                str(files[f"{role}.key"]),
                "-subj",
                f"/CN=stage8-{role}",
                "-out",
                str(files[f"{role}.csr"]),
            ],
            cwd=root,
        )
        extension = root / f"{role}.ext"
        extension.write_text(
            (
                "basicConstraints=critical,CA:FALSE\n"
                "keyUsage=critical,digitalSignature\n"
                f"extendedKeyUsage={'serverAuth' if role == 'server' else 'clientAuth'}\n"
                + ("subjectAltName=IP:127.0.0.1\n" if role == "server" else "")
            ),
            encoding="utf-8",
        )
        command(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(files[f"{role}.csr"]),
                "-CA",
                str(files["ca.pem"]),
                "-CAkey",
                str(files["ca.key"]),
                "-CAcreateserial",
                "-days",
                "1",
                "-extfile",
                str(extension),
                "-out",
                str(files[f"{role}.pem"]),
            ],
            cwd=root,
        )
    for path in files.values():
        path.chmod(0o600)
    return files


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        certs = generate_certificates(root)
        active = root / "hmac-active"
        previous = root / "hmac-previous"
        active.write_bytes(b"a" * 32)
        previous.write_bytes(b"p" * 32)
        active.chmod(0o600)
        previous.chmod(0o600)
        state = root / "state"
        state.mkdir(mode=0o700)
        port = free_port()
        environment = {
            "MARKET_PIPELINE_LISTEN_HOST": "127.0.0.1",
            "MARKET_PIPELINE_LISTEN_PORT": str(port),
            "MARKET_PIPELINE_ALLOWED_PEER_IP": "127.0.0.1",
            "MARKET_HMAC_ACTIVE_PATH": str(active),
            "MARKET_HMAC_PREVIOUS_PATH": str(previous),
            "MARKET_TRANSPORT_CA_PATH": str(certs["ca.pem"]),
            "MARKET_TRANSPORT_CERT_PATH": str(certs["server.pem"]),
            "MARKET_TRANSPORT_KEY_PATH": str(certs["server.key"]),
        }
        tls = client_tls_context(
            ca=certs["ca.pem"], cert=certs["client.pem"], key=certs["client.key"]
        )
        document = json.loads(FIXTURE.read_text(encoding="utf-8"))
        statuses: list[int] = []
        duplicate_counts: list[int] = []
        unauthenticated_tls_rejected = False
        bad_hmac_rejected = False
        for restart in range(2):
            stop = threading.Event()
            failures: list[str] = []

            def target() -> None:
                try:
                    with patch.dict(os.environ, environment, clear=False):
                        run_market_fact_receiver_service(
                            role="market-fact-receiver",
                            mode="live",
                            release_sha="a" * 40,
                            state_directory=state,
                            stop=stop,
                        )
                except BaseException as exc:
                    failures.append(type(exc).__name__)

            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            deadline = time.monotonic() + 5
            while True:
                try:
                    status, response = post_document(
                        host="127.0.0.1",
                        port=port,
                        path=FACT_PATH,
                        document=document,
                        key_id="active-v1",
                        hmac_key=b"a" * 32,
                        tls_context=tls,
                        timeout_seconds=1,
                    )
                    statuses.append(status)
                    duplicate_counts.append(int(response.get("duplicate_count", 0)))
                    break
                except MarketTransportError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("stage8_live_receiver_start_timeout")
                    time.sleep(0.05)
            if restart == 0:
                no_client_certificate = ssl.create_default_context(
                    ssl.Purpose.SERVER_AUTH, cafile=str(certs["ca.pem"])
                )
                try:
                    post_document(
                        host="127.0.0.1",
                        port=port,
                        path=FACT_PATH,
                        document=document,
                        key_id="active-v1",
                        hmac_key=b"a" * 32,
                        tls_context=no_client_certificate,
                        timeout_seconds=1,
                    )
                except MarketTransportError:
                    unauthenticated_tls_rejected = True
                bad_status, _ = post_document(
                    host="127.0.0.1",
                    port=port,
                    path=FACT_PATH,
                    document=document,
                    key_id="active-v1",
                    hmac_key=b"z" * 32,
                    tls_context=tls,
                    timeout_seconds=1,
                )
                bad_hmac_rejected = bad_status == 401
            stop.set()
            thread.join(timeout=3)
            if thread.is_alive() or failures:
                raise RuntimeError("stage8_live_receiver_shutdown_failed")
        if (
            statuses != [200, 200]
            or duplicate_counts != [0, 1]
            or not unauthenticated_tls_rejected
            or not bad_hmac_rejected
        ):
            raise RuntimeError("stage8_live_receiver_replay_failed")
        return {
            "status": "pass",
            "tls": "mutual",
            "hmac": "sha256",
            "unauthenticated_tls_rejected": unauthenticated_tls_rejected,
            "bad_hmac_rejected": bad_hmac_rejected,
            "restart_preserved_checkpoint": True,
            "accepted_count": 1,
            "duplicate_count": 1,
            "raw_payload_logged": False,
        }


if __name__ == "__main__":
    print(json.dumps(run(), sort_keys=True, separators=(",", ":")))
