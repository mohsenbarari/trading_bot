"""Exact seventh transport inverse for immutable historical Runner oracles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

SOURCE_66_SHA = "46544061459af3d0df38079abe21817bf59606341bdf6d9100fe9607e79e9cb4"
SOURCE_752_SHA = "f01b25fd68a423b695c929f921e08007c18326dc3a406aa03b3c0db7b12aacfd"
RUN_752_AST_SHA = "4426ff2c3c1f7024975bb178127031daa1f2c1d57332b743376bc5078d49a5da"
CONTEXT_752_LINE = 3568
ASSET_SHA = "0903bc78735559f8228e3f389f7e669e421eb3c06701a2cf75885d2a83ee8a64"
ASSET_BYTES = 56301
ASSET_REL = "tests/fixtures/two_site_runner_752_source_inverse.json"


def apply_generation_completion_transport(source: str, *, inverse: bool = False) -> str:
    """Only the complete pinned66/752 pair is accepted, never partial fragments."""
    if type(source) is not str or type(inverse) is not bool:
        raise AssertionError("invalid seventh transport fixture input")
    before, after = (SOURCE_752_SHA, SOURCE_66_SHA) if inverse else (SOURCE_66_SHA, SOURCE_752_SHA)
    if hashlib.sha256(source.encode()).hexdigest() != before:
        raise AssertionError("unknown or partial seventh transport source")
    asset = Path(__file__).resolve().parent / "fixtures/two_site_runner_752_source_inverse.json"
    with asset.open("rb") as stream:
        raw = stream.read(ASSET_BYTES + 1)
    if len(raw) != ASSET_BYTES or hashlib.sha256(raw).hexdigest() != ASSET_SHA:
        raise AssertionError("seventh transport inverse fixture changed")
    material = json.loads(raw)
    if (material["before_sha256"], material["after_sha256"]) != (SOURCE_66_SHA, SOURCE_752_SHA):
        raise AssertionError("seventh transport fixture pair changed")
    for hunk in reversed(material["hunks"]) if inverse else material["hunks"]:
        old, new = (hunk["after"], hunk["before"]) if inverse else (hunk["before"], hunk["after"])
        if source.count(old) != 1:
            raise AssertionError("seventh transport hunk is not unique")
        source = source.replace(old, new, 1)
    if hashlib.sha256(source.encode()).hexdigest() != after:
        raise AssertionError("seventh transport exact inverse failed")
    return source
