"""Shared, fail-closed access to the reviewed DR Object Storage bucket."""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import urlsplit

import boto3
from botocore.exceptions import ClientError

from core.config import settings
from core.secure_file_io import read_secure_text


ARVAN_ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
ARVAN_REGION = "ir-thr-at1"


class DrObjectStorageError(RuntimeError):
    """Raised when the reviewed Object Storage transport is not safe to use."""


@dataclass(frozen=True)
class S3Config:
    endpoint: str
    region: str
    bucket: str
    access_key: str
    secret_key: str


def load_s3_config() -> S3Config:
    """Load only the narrowly scoped, private DR bucket credentials."""

    endpoint = str(settings.dr_blob_object_endpoint or "").rstrip("/")
    region = str(settings.dr_blob_object_region or "")
    bucket = str(settings.dr_blob_object_bucket or "")
    path = settings.dr_blob_s3_credentials_file
    if endpoint != ARVAN_ENDPOINT or region != ARVAN_REGION or not bucket or not path:
        raise DrObjectStorageError(
            "DR Object Storage endpoint/region/bucket/credential file is incomplete"
        )
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.hostname != "s3.ir-thr-at1.arvanstorage.ir":
        raise DrObjectStorageError("DR Object Storage endpoint is outside the reviewed Arvan HTTPS origin")
    if settings.environment == "staging" and bucket == "production-sync-coin":
        raise DrObjectStorageError("staging DR worker refuses the production bucket")
    try:
        credentials = json.loads(
            read_secure_text(path, label="DR Object Storage credentials", max_size=16 * 1024)
        )
    except Exception as exc:
        raise DrObjectStorageError("DR Object Storage credential file is invalid") from exc
    if not isinstance(credentials, dict) or set(credentials) != {"access_key", "secret_key"}:
        raise DrObjectStorageError("DR Object Storage credential fields are invalid")
    access_key = str(credentials["access_key"])
    secret_key = str(credentials["secret_key"])
    if len(access_key) < 8 or len(secret_key) < 32:
        raise DrObjectStorageError("DR Object Storage credentials are malformed")
    return S3Config(endpoint, region, bucket, access_key, secret_key)


def object_storage_client(config: S3Config):  # noqa: ANN201
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint,
        region_name=config.region,
        aws_access_key_id=config.access_key,
        aws_secret_access_key=config.secret_key,
    )


def validate_versioned_bucket(config: S3Config) -> None:
    """The protocol needs an exact provider VersionId for every durable object."""

    versioning = object_storage_client(config).get_bucket_versioning(Bucket=config.bucket)
    if settings.dr_blob_require_versioning and versioning.get("Status") != "Enabled":
        raise DrObjectStorageError("DR Object Storage bucket versioning is not enabled")


def object_not_found(exc: ClientError) -> bool:
    return str((exc.response.get("Error") or {}).get("Code") or "") in {
        "404", "NoSuchKey", "NotFound",
    }
