#!/usr/bin/env python3
"""Install sealed owner-pack and classic model as private review-only assets.

The explicit staging flag acknowledges that these files enable authenticated
research shadow display only.  They never promote the classifier into offer or
estimator runtime.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile

from core.market_intelligence.coin_condition_review import (
    CONDITION_TAXONOMY_VERSION,
    OWNER_PACK_VERSION,
    load_owner_pack,
)


def _copy_private(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target.parent, 0o700)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as destination, source.open("rb") as origin:
            shutil.copyfileobj(origin, destination)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return sha256(target.read_bytes()).hexdigest()


def install(args: argparse.Namespace) -> dict[str, object]:
    if not args.runtime_staging:
        raise ValueError("condition_review_runtime_staging_flag_required")
    runtime = args.runtime_dir.expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    try:
        runtime.relative_to(repository)
    except ValueError:
        pass
    else:
        if runtime != (repository / "apps/coin_rate_estimator/runtime/condition-review").resolve():
            raise ValueError("condition_review_runtime_directory_in_repository_forbidden")
    pack_path = args.owner_pack.expanduser().resolve()
    model_path = args.model.expanduser().resolve()
    samples, pack_status = load_owner_pack(pack_path)
    if len(samples) != 240 or pack_status.get("status") != "READY":
        raise ValueError("condition_review_owner_pack_not_exact_240")
    model_stat = model_path.stat()
    if model_stat.st_uid != os.geteuid() or model_stat.st_mode & 0o022:
        raise ValueError("condition_review_model_permissions_invalid")
    import joblib

    artifact = joblib.load(model_path)
    if not isinstance(artifact, dict):
        raise ValueError("condition_review_model_invalid")
    if artifact.get("taxonomy_version") != CONDITION_TAXONOMY_VERSION:
        raise ValueError("condition_review_model_taxonomy_invalid")
    if artifact.get("status") != "RESEARCH_ONLY_NOT_PROMOTED":
        raise ValueError("condition_review_model_status_invalid")
    pack_target = runtime / "coin-offer-condition-owner-review.json"
    model_target = runtime / "coin-offer-condition-model.joblib"
    result = {
        "schema_version": "coin-condition-review-asset-install-v1",
        "status": "INSTALLED_RESEARCH_SHADOW_ONLY",
        "owner_pack": {
            "schema_version": OWNER_PACK_VERSION,
            "sample_count": len(samples),
            "source_fingerprint": pack_status["source_fingerprint"],
            "sha256": _copy_private(pack_path, pack_target),
        },
        "model": {
            "taxonomy_version": CONDITION_TAXONOMY_VERSION,
            "sha256": _copy_private(model_path, model_target),
        },
        "runtime_effect": {
            "condition_review_page": True,
            "offer_registration": False,
            "estimator": False,
            "tolerance": False,
        },
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-pack", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--runtime-staging", action="store_true")
    result = install(parser.parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
