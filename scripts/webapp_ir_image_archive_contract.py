#!/usr/bin/env python3
"""Pure contracts for isolated WA-IR Docker image archive tags.

The source hosts intentionally retain their normal shared Docker tags, for
example ``postgres:15-alpine``.  A transport archive must never contain those
tags because a later ``docker load`` would reassign them on WA-IR.  This module
defines the small deterministic namespace shared by the local preparer, the
staged-provenance verifier, and the future loader.  It performs no Docker,
network, filesystem, or process operation.
"""

from __future__ import annotations

import hashlib
import re


CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RELEASE_SHA_RE = re.compile(r"^[a-f0-9]{40,64}$")
IMAGE_ID_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
ARCHIVE_TAG_RE = re.compile(
    r"^goldtrade-wa-ir/campaign-[a-f0-9]{64}:release-[a-f0-9]{40,64}-image-[a-f0-9]{64}$"
)

ARCHIVE_REPOSITORY = "goldtrade-wa-ir"


class ImageArchiveContractError(ValueError):
    """A supplied archive-tag identity is not safe or canonical."""


def require_campaign_id(value: object, *, field: str = "campaign_id") -> str:
    if not isinstance(value, str) or not CAMPAIGN_ID_RE.fullmatch(value):
        raise ImageArchiveContractError(f"{field} has an unsafe format")
    return value


def require_release_sha(value: object, *, field: str = "release_sha") -> str:
    if not isinstance(value, str) or not RELEASE_SHA_RE.fullmatch(value):
        raise ImageArchiveContractError(f"{field} must be an exact lowercase Git SHA")
    return value


def require_image_id(value: object, *, field: str = "image_id") -> str:
    if not isinstance(value, str) or not IMAGE_ID_RE.fullmatch(value):
        raise ImageArchiveContractError(f"{field} must be a full immutable Docker image ID")
    return value


def canonical_archive_tag(*, campaign_id: str, release_sha: str, image_id: str) -> str:
    """Return the only Docker tag that an archive may assign for one image.

    The campaign is represented by its full SHA-256, rather than copied into
    a Docker repository component, so a valid control-plane ID can never
    exceed Docker's tag/reference limits.  The complete release SHA and image
    config hash remain visible and independently recomputable.
    """

    campaign_id = require_campaign_id(campaign_id)
    release_sha = require_release_sha(release_sha)
    image_id = require_image_id(image_id)
    campaign_sha256 = hashlib.sha256(campaign_id.encode("ascii")).hexdigest()
    return (
        ARCHIVE_REPOSITORY
        + "/campaign-"
        + campaign_sha256
        + ":release-"
        + release_sha
        + "-image-"
        + image_id.removeprefix("sha256:")
    )


def require_canonical_archive_tag(
    value: object,
    *,
    campaign_id: str,
    release_sha: str,
    image_id: str,
    field: str = "archive_tag",
) -> str:
    if not isinstance(value, str) or not ARCHIVE_TAG_RE.fullmatch(value):
        raise ImageArchiveContractError(f"{field} is not in the isolated WA-IR archive namespace")
    expected = canonical_archive_tag(
        campaign_id=campaign_id,
        release_sha=release_sha,
        image_id=image_id,
    )
    if value != expected:
        raise ImageArchiveContractError(f"{field} is not bound to its campaign, release, and image ID")
    return value
