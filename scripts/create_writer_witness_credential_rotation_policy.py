#!/usr/bin/env python3
"""Create the fixed, root-only Writer Witness current-credential policy.

This is a deliberately small privileged ceremony.  It consumes two fresh
non-secret client receipts, verifies their signed Witness contracts, and
creates exactly one canonical policy at the fixed control-host path.  It does
not read an HMAC secret, contact the Witness, or activate any service.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import verify_writer_witness_paired_attestation as paired  # noqa: E402


def _parse_not_after(value: str) -> datetime:
    try:
        return paired._parse_time(value, field="credential rotation policy not_after")
    except paired.WriterWitnessPairAttestationError as exc:
        raise argparse.ArgumentTypeError("--not-after must be an RFC3339 timestamp") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--webapp-fi-attestation", type=Path, required=True)
    parser.add_argument("--webapp-ir-attestation", type=Path, required=True)
    parser.add_argument("--policy-id", required=True)
    parser.add_argument("--webapp-fi-generation", required=True)
    parser.add_argument("--webapp-ir-generation", required=True)
    parser.add_argument("--not-after", type=_parse_not_after, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = paired.create_rotation_policy(
            webapp_fi_attestation_path=arguments.webapp_fi_attestation,
            webapp_ir_attestation_path=arguments.webapp_ir_attestation,
            policy_id=arguments.policy_id,
            webapp_fi_generation=arguments.webapp_fi_generation,
            webapp_ir_generation=arguments.webapp_ir_generation,
            not_after=arguments.not_after,
        )
        print(paired._canonical_json_bytes(result).decode("utf-8"))
        return 0
    except paired.WriterWitnessPairAttestationError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
