import unittest

from scripts import report_market_trade_query_plans as report_script


class MarketTradeQueryPlanReportTests(unittest.TestCase):
    def test_summarize_explain_payload_extracts_scan_nodes(self):
        payload = [
            {
                "Execution Time": 1.25,
                "Planning Time": 0.5,
                "Plan": {
                    "Node Type": "Limit",
                    "Actual Rows": 3,
                    "Plan Rows": 50,
                    "Shared Hit Blocks": 7,
                    "Shared Read Blocks": 0,
                    "Plans": [
                        {"Node Type": "Index Scan", "Relation Name": "offers"},
                        {"Node Type": "Seq Scan", "Relation Name": "trades"},
                    ],
                },
            }
        ]

        summary = report_script.summarize_explain_payload(payload)

        self.assertEqual(summary["node_type"], "Limit")
        self.assertEqual(summary["execution_time_ms"], 1.25)
        self.assertEqual(summary["scan_nodes"], ["Index Scan on offers", "Seq Scan on trades"])

    def test_parse_args_defaults_to_safe_read_only_limit(self):
        args = report_script.parse_args([])

        self.assertFalse(args.include_sql)
        self.assertEqual(args.limit, 50)


if __name__ == "__main__":
    unittest.main()
