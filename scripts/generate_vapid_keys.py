#!/usr/bin/env python3
"""Generate VAPID keys for Web Push configuration."""

from __future__ import annotations

import argparse
import base64

from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Web Push VAPID env values.")
    parser.add_argument(
        "--subject",
        default="mailto:admin@362514.ir",
        help="VAPID subject claim, usually mailto:ops@example.com",
    )
    args = parser.parse_args()

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_number = private_key.private_numbers().private_value
    private_raw = private_number.to_bytes(32, "big")

    public_numbers = private_key.public_key().public_numbers()
    public_raw = (
        b"\x04"
        + public_numbers.x.to_bytes(32, "big")
        + public_numbers.y.to_bytes(32, "big")
    )

    print(f"WEB_PUSH_VAPID_PUBLIC_KEY={b64url(public_raw)}")
    print(f"WEB_PUSH_VAPID_PRIVATE_KEY={b64url(private_raw)}")
    print(f"WEB_PUSH_VAPID_SUBJECT={args.subject}")


if __name__ == "__main__":
    main()
