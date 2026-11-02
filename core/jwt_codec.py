"""Shared, patched JWT implementation and stable application exception name.

Keep algorithm allowlists at every decoding call site. PyJWT deliberately also
rejects future ``iat`` claims; do not disable claim/signature validation to make
malformed tokens compatible with the retired JOSE implementation.
"""

import jwt
from jwt.exceptions import PyJWTError as JWTError

__all__ = ["jwt", "JWTError"]
