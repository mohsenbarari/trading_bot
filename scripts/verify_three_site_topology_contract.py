#!/usr/bin/env python3
"""Fail closed when tracked three-site role bindings drift apart."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml

from core.three_site_topology import NORMAL_WRITER_SITE, PRODUCTION_WITNESS_HOST, WITNESS_SITE
from scripts import plan_writer_witness_real_host_matrix as matrix_preflight
from scripts.verify_three_site_staging_inventory import PRODUCTION_IPS
from scripts.verify_webapp_ir_dark_standby_manifest import EXPECTED_TOPOLOGY, REQUIRED_VALUES


class TopologyContractError(RuntimeError):
    pass


def validate() -> dict[str, str]:
    compose_path = ROOT / "deploy/staging/docker-compose.three-site.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    witness = compose.get("services", {}).get("witness_api", {}).get("environment", {})
    if not isinstance(witness, dict):
        raise TopologyContractError("three-site Witness service is missing")
    if witness.get("PHYSICAL_SITE") != WITNESS_SITE:
        raise TopologyContractError("three-site Witness must use the independent witness identity")
    if witness.get("WRITER_WITNESS_AUTHORITATIVE_SITE") != NORMAL_WRITER_SITE:
        raise TopologyContractError("three-site normal writer must be WebApp-FI")
    if matrix_preflight.MATRIX_WITNESS != PRODUCTION_WITNESS_HOST:
        raise TopologyContractError("real-host Matrix Witness differs from canonical topology")
    if EXPECTED_TOPOLOGY["WRITER_WITNESS_HOST"] != PRODUCTION_WITNESS_HOST:
        raise TopologyContractError("legacy data-only verifier differs from canonical Witness")
    if PRODUCTION_WITNESS_HOST not in PRODUCTION_IPS:
        raise TopologyContractError("staging isolation gate does not protect the production Witness")
    if REQUIRED_VALUES["DARK_STANDBY_PROFILE"] != "legacy-data-only-v1":
        raise TopologyContractError("legacy dark-standby profile is not explicitly segregated")
    return {
        "status": "passed",
        "normal_writer_site": NORMAL_WRITER_SITE,
        "witness_site": WITNESS_SITE,
        "witness_host": PRODUCTION_WITNESS_HOST,
        "dark_standby_profile": REQUIRED_VALUES["DARK_STANDBY_PROFILE"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        payload = validate()
    except Exception as exc:
        payload = {"status": "failed", "error": str(exc)}
    if args.json:
        print(json.dumps(payload, sort_keys=True))
    else:
        print("three-site topology contract: " + payload["status"])
        if payload["status"] != "passed":
            print("FAIL: " + payload["error"])
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
