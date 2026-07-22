from __future__ import annotations

import unittest

from core.dr_sync_auth import (
    DR_SYNC_PROTOCOL,
    DrSyncAuthError,
    parse_pairwise_keys,
    sign_request,
    verify_request,
)


NOW = 1_800_000_000
BODY = b'{"events":[]}'
SECRET = "a-unique-pairwise-secret-with-more-than-32-bytes"


def keys():
    return parse_pairwise_keys(
        '[{"key_id":"ir-to-fi-v1","source_site":"webapp_ir",'
        '"destination_site":"webapp_fi","secret":"' + SECRET + '"}]'
    )


def headers(*, source="webapp_ir", destination="webapp_fi", nonce="n" * 32):
    signature = sign_request(
        secret=SECRET,
        method="POST",
        path="/api/dr-sync/events",
        body=BODY,
        timestamp=NOW,
        nonce=nonce,
        key_id="ir-to-fi-v1",
        source_site=source,
        destination_site=destination,
    )
    return {
        "x-dr-protocol": DR_SYNC_PROTOCOL,
        "x-dr-key-id": "ir-to-fi-v1",
        "x-dr-source-site": source,
        "x-dr-destination-site": destination,
        "x-dr-timestamp": str(NOW),
        "x-dr-nonce": nonce,
        "x-dr-signature": signature,
    }


class DrSyncAuthTests(unittest.TestCase):
    def test_request_binds_method_path_body_source_destination_nonce_and_key(self):
        result = verify_request(
            method="POST",
            path="/api/dr-sync/events",
            body=BODY,
            headers=headers(),
            keys=keys(),
            expected_destination_site="webapp_fi",
            now=NOW,
        )
        self.assertEqual(result.source_site, "webapp_ir")
        self.assertEqual(len(result.request_hash), 64)

    def test_cross_site_impersonation_wrong_body_and_replay_window_fail(self):
        for kwargs in (
            {"headers": headers(destination="bot_fi"), "expected_destination_site": "webapp_fi"},
            {"headers": headers(), "body": b'{"events":[1]}'},
            {"headers": headers(), "now": NOW + 31},
        ):
            call = {
                "method": "POST",
                "path": "/api/dr-sync/events",
                "body": BODY,
                "headers": headers(),
                "keys": keys(),
                "expected_destination_site": "webapp_fi",
                "now": NOW,
            }
            call.update(kwargs)
            with self.assertRaises(DrSyncAuthError):
                verify_request(**call)

    def test_configuration_rejects_shared_pair_duplicate_fields_and_short_secret(self):
        with self.assertRaises(DrSyncAuthError):
            parse_pairwise_keys(
                '[{"key_id":"a","key_id":"b","source_site":"webapp_ir",'
                '"destination_site":"webapp_fi","secret":"' + SECRET + '"}]'
            )
        with self.assertRaisesRegex(DrSyncAuthError, "32 bytes"):
            parse_pairwise_keys(
                '[{"key_id":"a","source_site":"webapp_ir",'
                '"destination_site":"webapp_fi","secret":"short"}]'
            )
