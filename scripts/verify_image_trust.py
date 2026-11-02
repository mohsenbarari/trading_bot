#!/usr/bin/env python3
"""Offline build gate for the October 2026 CA distrust (not a CVE scanner).

Checks the effective Python/OpenSSL system store and, for application images,
certifi's separate store. No network, application imports or certificate output.
Never work around a failed gate by disabling TLS verification.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import ssl


BLOCKED_CA_NAMES = frozenset({"emsigneccrootcag3"})


def check_context(context: ssl.SSLContext, store: str) -> dict[str, object]:
    certificates = context.get_ca_certs()
    if not certificates:
        raise ValueError(f"{store}: empty trust store")
    for certificate in certificates:
        for rdn in certificate.get("subject", ()):
            for key, value in rdn:
                if key == "commonName":
                    normalized = re.sub(r"[^a-z0-9]", "", str(value).lower())
                    if normalized in BLOCKED_CA_NAMES:
                        raise ValueError(f"{store}: distrusted CA remains installed")
    return {"store": store, "ca_count": len(certificates), "blocked_ca_present": False}


def verify_trust(*, require_certifi: bool = False) -> list[dict[str, object]]:
    results = [check_context(ssl.create_default_context(), "system")]
    if importlib.util.find_spec("certifi") is not None:
        import certifi

        results.append(check_context(ssl.create_default_context(cafile=certifi.where()), "certifi"))
    elif require_certifi:
        raise ValueError("certifi: required trust store unavailable")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-certifi", action="store_true")
    args = parser.parse_args()
    try:
        results = verify_trust(require_certifi=args.require_certifi)
    except (ValueError, OSError, ImportError):
        # Avoid leaking local paths or certificate material from exception text.
        print(json.dumps({"status": "FAIL", "reason": "trust_store_security_gate_failed"}))
        return 1
    print(json.dumps({"status": "PASS", "scope": "USN-8944-1/DLA-4845-1", "stores": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
