"""Recover stalled Market roles without changing data, peers or authority.

Run from a root-owned systemd timer on each existing pipeline host. Readiness
degradation (quiet market, review or catch-up) alone NEVER triggers a restart.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time


ROLES = {
    "bot": ("market-fact-receiver", "market-store-adapter", "coin-estimator", "estimator-snapshot-sender"),
    "web": ("market-capture-account1", "market-capture-account2", "market-capture-external", "market-processor", "market-fact-sync-worker", "estimator-snapshot-receiver"),
}
SCHEMAS = {
    "market-fact-receiver": "market_fact_receiver/1.0",
    "market-store-adapter": "market_fact_adapter/1.0",
    "coin-estimator": "coin_estimator_runtime/1.0",
    "estimator-snapshot-sender": "estimator_snapshot_sender/1.0",
    "market-capture-account1": "market_capture_engine/1.0",
    "market-capture-account2": "market_capture_engine/1.0",
    "market-capture-external": "external_quote_capture/1.0",
    "market-processor": "market_processor/4.0",
    "market-fact-sync-worker": "market_fact_sync/1.0",
    "estimator-snapshot-receiver": "estimator_snapshot_receiver/1.0",
}
RECEIVERS = {"market-fact-receiver", "estimator-snapshot-receiver"}
STALE_SECONDS = 180
COOLDOWN_SECONDS = 300

# Stdlib-only probe also works before the new receiver image is installed.
# CA verification, client auth, and exact server certificate pinning all hold.
PROBE = '''import http.client,json,os,ssl
from pathlib import Path
role=os.environ.get("MARKET_PIPELINE_TRANSPORT_CERT_ROLE", "bot")
ca=os.environ.get("MARKET_TRANSPORT_CA_PATH", "/run/secrets/market_transport_ca")
cert=os.environ.get("MARKET_TRANSPORT_CERT_PATH", "/run/secrets/market_"+role+"_transport_cert")
key=os.environ.get("MARKET_TRANSPORT_KEY_PATH", "/run/secrets/market_"+role+"_transport_key")
ctx=ssl.create_default_context(cafile=ca); ctx.check_hostname=False; ctx.load_cert_chain(cert,key)
c=http.client.HTTPSConnection("127.0.0.1",int(os.environ.get("MARKET_PIPELINE_LISTEN_PORT","9443")),context=ctx,timeout=2)
try:
 c.connect(); assert c.sock.getpeercert(binary_form=True)==ssl.PEM_cert_to_DER_cert(Path(cert).read_text())
 c.request("GET","/healthz",headers={"Connection":"close"}); r=c.getresponse(); p=r.read(1048577)
 assert r.status==200 and len(p)<=1048576 and isinstance(json.loads(p),dict)
finally: c.close()
'''


def run(args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, value):
    temporary = path.with_name(f".{path.name}.{os.getpid()}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def age(stamp, now):
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return now - value.timestamp()
    except (ValueError, TypeError):
        return float("inf")


def assess(role, health, previous, now, *, endpoint_ok=True, upstream=None, expected_release=None):
    """Return a liveness reason, preserving ordinary source silence."""
    if health.get("schema") != SCHEMAS[role] or health.get("role") != role:
        return "HEARTBEAT_INVALID"
    if expected_release and health.get("release_sha") != expected_release:
        return "HEARTBEAT_RELEASE_MISMATCH"
    if age(health.get("updated_at_utc"), now) > STALE_SECONDS:
        return "HEARTBEAT_STALLED"
    if role in RECEIVERS and not endpoint_ok:
        return "MTLS_HTTP_UNRESPONSIVE"
    if role == "market-store-adapter" and upstream:
        source = upstream.get("streams") or {}
        target = health.get("streams") or {}
        prior_streams = previous.get("streams") or {}
        progress_at = previous.get("stream_progress_at") or {}
        for stream_id, sequence in source.items():
            behind = int(sequence) > int(target.get(stream_id, 0))
            unchanged = target.get(stream_id) == prior_streams.get(stream_id)
            last_progress = progress_at.get(stream_id, previous.get("last_progress_at", now))
            if behind and unchanged and now - last_progress > STALE_SECONDS:
                return "ADAPTER_PROGRESS_STALLED"
    if role in {"coin-estimator", "estimator-snapshot-sender"}:
        latest = health.get("latest") or {}
        version = latest.get("snapshot_version")
        if version is not None and version == previous.get("snapshot_version"):
            expected = ((upstream or {}).get("latest") or {}).get("snapshot_version", 0)
            # An estimator should keep its cadence even if the market is quiet;
            # a snapshot sender needs a newer upstream output before recovery.
            demanded = role == "coin-estimator" or int(expected or 0) > int(version)
            if demanded and now - previous.get("last_progress_at", now) > STALE_SECONDS:
                return "SNAPSHOT_PROGRESS_STALLED"
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-role", choices=ROLES, required=True)
    parser.add_argument("--project", default="market-private-pipeline-primary")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--ops-root", type=Path, required=True)
    parser.add_argument("--deployment-lock", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0 or not args.state_root.is_dir():
        raise RuntimeError("watchdog_host_contract_invalid")
    if not args.ops_root.is_dir() or args.ops_root.resolve() != args.ops_root:
        raise RuntimeError("watchdog_ops_root_invalid")
    with open(args.ops_root / "watchdog.lock", "a") as mutex:
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with open(args.deployment_lock, "r") as deployment:
            try:
                fcntl.flock(deployment, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print(json.dumps({"status": "DEPLOYMENT_IN_PROGRESS"}))
                return
            state_path = args.ops_root / "watchdog-state.json"
            state = read_json(state_path)
            now = time.time()
            names = [f"{args.project}-{role}-1" for role in ROLES[args.host_role]]
            inspected = run(["docker", "inspect", *names])
            # Missing deployment components are surfaced, never fabricated.
            if inspected.returncode:
                raise RuntimeError("watchdog_inventory_unavailable")
            containers = json.loads(inspected.stdout)
            healths = {role: read_json(args.state_root / role / role / "health.json") for role in ROLES[args.host_role]}
            results = []
            restarted = False
            for role, container in zip(ROLES[args.host_role], containers):
                labels = container["Config"].get("Labels") or {}
                if labels.get("com.docker.compose.project") != args.project or labels.get("com.docker.compose.service") != role:
                    raise RuntimeError("watchdog_container_identity_mismatch")
                current = state.setdefault(role, {})
                if not container["State"]["Running"]:
                    results.append({"role": role, "status": "STOPPED_REQUIRES_REVIEW"})
                    continue  # Preserve intentional stops; Docker recovers crashes.
                started = container["State"]["StartedAt"]
                if current.get("started_at") != started:
                    current.update(started_at=started, failures=0, last_progress_at=now, stream_progress_at={})
                if age(started, now) < STALE_SECONDS:
                    results.append({"role": role, "status": "STARTUP_GRACE"})
                    continue
                health = healths[role]
                endpoint_ok = True
                if role in RECEIVERS:
                    try:
                        probe = run(["docker", "exec", "-e", "MARKET_PIPELINE_TRANSPORT_CERT_ROLE=" + ("bot" if role == "market-fact-receiver" else "web"), container["Id"], "python", "-c", PROBE], timeout=7)
                        endpoint_ok = probe.returncode == 0
                    except subprocess.TimeoutExpired:
                        endpoint_ok = False
                upstream = healths.get("market-fact-receiver") if role == "market-store-adapter" else healths.get("coin-estimator")
                runtime_env = dict(item.split("=", 1) for item in container["Config"].get("Env", []) if item.startswith("MARKET_PIPELINE_RELEASE_SHA="))
                reason = assess(role, health, current, now, endpoint_ok=endpoint_ok, upstream=upstream,
                                expected_release=runtime_env.get("MARKET_PIPELINE_RELEASE_SHA"))
                current["failures"] = int(current.get("failures", 0)) + 1 if reason else 0
                streams = health.get("streams")
                version = (health.get("latest") or {}).get("snapshot_version")
                if role == "market-store-adapter" and isinstance(streams, dict):
                    progress_at = current.setdefault("stream_progress_at", {})
                    for stream_id, sequence in streams.items():
                        if stream_id not in progress_at or sequence != (current.get("streams") or {}).get(stream_id):
                            progress_at[stream_id] = now
                if streams != current.get("streams") or version != current.get("snapshot_version"):
                    current["last_progress_at"] = now
                current.update(streams=streams, snapshot_version=version)
                result = {"role": role, "status": "LIVENESS_OK" if not reason else "DEGRADED", "reason_code": reason}
                if reason and current["failures"] >= 2 and not restarted and now - current.get("last_restart_at", 0) >= COOLDOWN_SECONDS:
                    if args.apply:
                        # Commit cooldown before restart so an interrupted timer
                        # cannot create a tight restart loop. Stop/restart keeps
                        # the same container, mounts and single session owner.
                        current["last_restart_at"] = now
                        write_json(state_path, state)
                        action = run(["docker", "restart", "--timeout", "20", container["Id"]], timeout=35)
                        if action.returncode:
                            raise RuntimeError("watchdog_restart_failed")
                        result["status"] = "RESTARTED"
                        restarted = True
                    else:
                        result["status"] = "WOULD_RESTART"
                results.append(result)
            write_json(state_path, state)
            report = {"updated_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(), "roles": results}
            write_json(args.ops_root / "watchdog-health.json", report)
            print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(json.dumps({"status": "WATCHDOG_FAILED", "error_type": type(exc).__name__}))
        raise SystemExit(1)
