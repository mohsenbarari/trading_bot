"""Pure adversarial tests for reverse Object-Storage route commitments."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest

from core import physical_arvan_s3_failback_route_commitment as commitment


CAMPAIGN = "ir-fi-commitment-20260731"
RELEASE = "3138d0c2a8d20a84042c3a438fbc88db7a4db498"
ENDPOINT = "https://s3.ir-thr-at1.arvanstorage.ir"
REGION = "ir-thr-at1"
BUCKET = "private-physical-failback"
MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "core"
    / "physical_arvan_s3_failback_route_commitment.py"
)


class PhysicalArvanS3FailbackRouteCommitmentTests(unittest.TestCase):
    def test_scope_commits_exact_campaign_release_origin_and_bucket(self) -> None:
        first = commitment.derive_physical_arvan_s3_failback_route_scope_sha256(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        self.assertEqual(64, len(first))
        self.assertEqual(
            f"physical-failback/{CAMPAIGN}/{RELEASE}/",
            commitment.physical_arvan_s3_failback_exact_prefix(
                campaign_id=CAMPAIGN, release_sha=RELEASE
            ),
        )
        for changed in (
            {"bucket": "another-private-bucket"},
            {"campaign_id": "other-commitment-20260731"},
            {"release_sha": "a" * 40},
        ):
            with self.subTest(changed=changed):
                values = {
                    "campaign_id": CAMPAIGN,
                    "release_sha": RELEASE,
                    "endpoint": ENDPOINT,
                    "region": REGION,
                    "bucket": BUCKET,
                    **changed,
                }
                self.assertNotEqual(
                    first,
                    commitment.derive_physical_arvan_s3_failback_route_scope_sha256(**values),
                )

    def test_four_role_binding_commits_all_identites_and_scope_digests(self) -> None:
        scope = commitment.derive_physical_arvan_s3_failback_route_scope_sha256(
            campaign_id=CAMPAIGN,
            release_sha=RELEASE,
            endpoint=ENDPOINT,
            region=REGION,
            bucket=BUCKET,
        )
        values = {
            "campaign_id": CAMPAIGN,
            "release_sha": RELEASE,
            "normal_route_scope_sha256": "a" * 64,
            "reverse_route_scope_sha256": scope,
            "fi_publisher_identity_sha256": "b" * 64,
            "ir_receiver_identity_sha256": "c" * 64,
            "ir_publisher_identity_sha256": "d" * 64,
            "fi_receiver_identity_sha256": "e" * 64,
        }
        result = commitment.derive_physical_arvan_s3_failback_four_role_route_binding_sha256(**values)
        self.assertEqual(64, len(result))
        self.assertNotEqual(
            result,
            commitment.derive_physical_arvan_s3_failback_four_role_route_binding_sha256(
                **{**values, "fi_receiver_identity_sha256": "f" * 64}
            ),
        )
        with self.assertRaisesRegex(
            commitment.PhysicalArvanS3FailbackRouteCommitmentError,
            "IDENTITIES_NOT_SEPARATE",
        ):
            commitment.derive_physical_arvan_s3_failback_four_role_route_binding_sha256(
                **{**values, "fi_receiver_identity_sha256": "d" * 64}
            )

    def test_invalid_endpoint_and_noncanonical_release_are_rejected(self) -> None:
        with self.assertRaises(commitment.PhysicalArvanS3FailbackRouteCommitmentError):
            commitment.derive_physical_arvan_s3_failback_route_scope_sha256(
                campaign_id=CAMPAIGN,
                release_sha=RELEASE,
                endpoint="https://example.invalid",
                region=REGION,
                bucket=BUCKET,
            )
        with self.assertRaises(commitment.PhysicalArvanS3FailbackRouteCommitmentError):
            commitment.physical_arvan_s3_failback_exact_prefix(
                campaign_id=CAMPAIGN, release_sha="not-a-release"
            )

    def test_source_has_no_sdk_network_or_file_side_effect_dependency(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"), filename=str(MODULE_PATH))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
        self.assertFalse(imports & {"boto3", "botocore", "socket", "subprocess", "requests", "os", "pathlib"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
