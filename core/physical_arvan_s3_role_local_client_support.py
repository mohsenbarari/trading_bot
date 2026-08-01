"""Small non-paired primitives shared by one-role Arvan S3 factories.

This module intentionally knows neither role names nor credential paths.  It
does not import a paired credential loader or factory.  A role artifact first
performs its own route/identity checks, then may use these narrow mechanics to
construct one transient S3v4 client and revoke a callback-scoped proxy.
"""

from __future__ import annotations

import importlib
import os
import threading
from typing import Any


__all__ = (
    "ArvanS3RoleLocalClientSupportError",
    "ScopedRoleLocalCallbackLease",
    "create_role_local_raw_s3_client",
    "load_role_local_boto_sdk",
    "require_role_local_root",
    "result_leaks_role_local_callback_value",
)


_CONNECT_TIMEOUT_SECONDS = 5
_READ_TIMEOUT_SECONDS = 60
_MAX_ATTEMPTS = 2


class ArvanS3RoleLocalClientSupportError(ValueError):
    """Fixed internal error that contains no provider or credential detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise ArvanS3RoleLocalClientSupportError(code)


def require_role_local_root() -> None:
    try:
        if os.geteuid() != 0:
            _fail("ARVAN_S3_ROLE_LOCAL_FACTORY_ROOT_REQUIRED")
    except OSError:
        _fail("ARVAN_S3_ROLE_LOCAL_FACTORY_ROOT_REQUIRED")


def load_role_local_boto_sdk() -> tuple[object, object]:
    """Lazy SDK import after one-role credential admission has succeeded."""

    try:
        return importlib.import_module("boto3"), importlib.import_module("botocore.config")
    except Exception:
        _fail("ARVAN_S3_ROLE_LOCAL_FACTORY_SDK_UNAVAILABLE")


def create_role_local_raw_s3_client(
    *,
    boto3_module: object,
    botocore_config_module: object,
    endpoint: str,
    region: str,
    access_key: str,
    secret_key: str,
) -> object:
    """Construct one path-style S3v4 client from transient local facts."""

    if not all(type(item) is str and item for item in (endpoint, region, access_key, secret_key)):
        _fail("ARVAN_S3_ROLE_LOCAL_FACTORY_CLIENT_INPUT_INVALID")
    try:
        session_type = getattr(getattr(boto3_module, "session"), "Session")
        config_type = getattr(botocore_config_module, "Config")
        if not callable(session_type) or not callable(config_type):
            _fail("ARVAN_S3_ROLE_LOCAL_FACTORY_SDK_UNAVAILABLE")
        client_config = config_type(
            signature_version="s3v4",
            connect_timeout=_CONNECT_TIMEOUT_SECONDS,
            read_timeout=_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": _MAX_ATTEMPTS, "mode": "standard"},
            s3={"addressing_style": "path"},
            proxies={},
        )
        session = session_type(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        client = session.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            use_ssl=True,
            verify=True,
            config=client_config,
        )
    except ArvanS3RoleLocalClientSupportError:
        raise
    except Exception:
        _fail("ARVAN_S3_ROLE_LOCAL_FACTORY_CLIENT_CREATE_FAILED")
    if client is None:
        _fail("ARVAN_S3_ROLE_LOCAL_FACTORY_CLIENT_CREATE_FAILED")
    return client


class ScopedRoleLocalCallbackLease:
    """One-thread revocation token for a callback-only local proxy."""

    __slots__ = ("_active", "_thread_id")

    def __init__(self) -> None:
        self._active = True
        self._thread_id = threading.get_ident()

    def require_active(self) -> None:
        if not self._active or threading.get_ident() != self._thread_id:
            _fail("ARVAN_S3_ROLE_LOCAL_FACTORY_CALLBACK_REVOKED")

    def revoke(self) -> None:
        self._active = False


def result_leaks_role_local_callback_value(value: object, *, blocked: tuple[object, ...]) -> bool:
    """Reject a direct or shallow-container escape of a scoped proxy."""

    pending: list[tuple[object, int]] = [(value, 0)]
    seen: set[int] = set()
    while pending:
        item, depth = pending.pop()
        if any(item is blocked_value for blocked_value in blocked):
            return True
        if depth >= 8 or id(item) in seen:
            continue
        seen.add(id(item))
        if type(item) in {tuple, list, set, frozenset}:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            pending.extend((child, depth + 1) for pair in item.items() for child in pair)
    return False
