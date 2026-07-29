"""Minimal fail-closed Ed25519 public-key verification adapter.

The adapter intentionally exposes no key generation, signing, credential,
filesystem, network, or subprocess behavior.  It is suitable as the injected
``verify_ed25519`` callable used by the remote-receiver policy foundation.
"""

from __future__ import annotations


try:
    from cryptography.exceptions import InvalidSignature as _InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PublicKey as _Ed25519PublicKey,
    )
except ImportError:  # pragma: no cover - exercised through the availability guard
    _InvalidSignature = None
    _Ed25519PublicKey = None


MAX_PAYLOAD_BYTES = 256 * 1024


class Ed25519VerificationError(ValueError):
    """The exact public-key signature verification cannot be proven."""


def _exact_bytes(value: object, *, expected_length: int | None, label: str) -> bytes:
    if not isinstance(value, bytes):
        raise Ed25519VerificationError(f"{label} must be bytes")
    if expected_length is not None and len(value) != expected_length:
        raise Ed25519VerificationError(f"{label} length is invalid")
    return value


def verify_ed25519(public_key: bytes, signature: bytes, payload: bytes) -> None:
    """Verify one exact raw Ed25519 signature or raise fail-closed.

    The argument order intentionally matches the remote-receiver policy
    callback contract: ``(public_key, signature, canonical_payload)``.
    """

    public_key = _exact_bytes(public_key, expected_length=32, label="Ed25519 public key")
    signature = _exact_bytes(signature, expected_length=64, label="Ed25519 signature")
    payload = _exact_bytes(payload, expected_length=None, label="Ed25519 payload")
    if not 1 <= len(payload) <= MAX_PAYLOAD_BYTES:
        raise Ed25519VerificationError("Ed25519 payload length is invalid")
    if _Ed25519PublicKey is None or _InvalidSignature is None:
        raise Ed25519VerificationError("Ed25519 verification dependency is unavailable")
    try:
        verifier = _Ed25519PublicKey.from_public_bytes(public_key)
        verifier.verify(signature, payload)
    except _InvalidSignature as exc:
        raise Ed25519VerificationError("Ed25519 signature is invalid") from exc
    except (TypeError, ValueError) as exc:
        raise Ed25519VerificationError("Ed25519 verification input is invalid") from exc
