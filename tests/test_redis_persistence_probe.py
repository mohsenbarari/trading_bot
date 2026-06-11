import unittest

from scripts import probe_redis_persistence as probe


class RedisPersistenceProbeTests(unittest.TestCase):
    def test_parse_args_requires_key_and_payload(self):
        args = probe.parse_args(["--key", "p3:test", "--payload", "value", "--write"])

        self.assertEqual(args.key, "p3:test")
        self.assertEqual(args.payload, "value")
        self.assertTrue(args.write)
        self.assertEqual(args.ttl_seconds, probe.DEFAULT_TTL_SECONDS)

    def test_parse_args_accepts_verify_cleanup_flow(self):
        args = probe.parse_args(["--key", "p3:test", "--payload", "value", "--verify", "--cleanup"])

        self.assertTrue(args.verify)
        self.assertTrue(args.cleanup)


if __name__ == "__main__":
    unittest.main()
