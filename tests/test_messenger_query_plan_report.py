import json
import unittest

from scripts import report_messenger_query_plans as report_script


class MessengerQueryPlanReportTests(unittest.TestCase):
    def test_build_explain_sql_wraps_statement(self):
        sql = report_script.build_explain_sql("SELECT 1")
        self.assertEqual(sql, "EXPLAIN (ANALYZE, BUFFERS, VERBOSE, FORMAT JSON) SELECT 1")

    def test_collect_scan_nodes_recurses_through_child_plans(self):
        nodes = report_script.collect_scan_nodes({
            "Node Type": "Nested Loop",
            "Plans": [
                {"Node Type": "Index Scan", "Relation Name": "chat_members"},
                {
                    "Node Type": "Bitmap Heap Scan",
                    "Relation Name": "messages",
                    "Plans": [{"Node Type": "Bitmap Index Scan", "Relation Name": "messages"}],
                },
            ],
        })
        self.assertEqual(nodes, [
            "Index Scan on chat_members",
            "Bitmap Heap Scan on messages",
            "Bitmap Index Scan on messages",
        ])

    def test_summarize_explain_payload_extracts_root_metrics(self):
        summary = report_script.summarize_explain_payload([
            {
                "Planning Time": 0.42,
                "Execution Time": 1.75,
                "Plan": {
                    "Node Type": "Index Scan",
                    "Actual Rows": 12,
                    "Plan Rows": 18,
                    "Shared Hit Blocks": 9,
                    "Shared Read Blocks": 1,
                    "Temp Read Blocks": 0,
                    "Temp Written Blocks": 0,
                    "Relation Name": "messages",
                },
            }
        ])
        self.assertEqual(summary.node_type, "Index Scan")
        self.assertEqual(summary.actual_rows, 12)
        self.assertEqual(summary.plan_rows, 18)
        self.assertEqual(summary.shared_hit_blocks, 9)
        self.assertEqual(summary.scan_nodes, ["Index Scan on messages"])

    def test_format_human_report_lists_reports_and_skips(self):
        text_report = report_script.format_human_report({
            "reports": [
                {
                    "name": "direct_unread_poll",
                    "summary": {
                        "node_type": "Nested Loop",
                        "execution_time_ms": 2.5,
                        "planning_time_ms": 0.5,
                        "scan_nodes": ["Index Scan on chat_members"],
                    },
                }
            ],
            "skipped": [{"name": "group_message_history", "reason": "No group membership sample found"}],
        })
        self.assertIn("direct_unread_poll", text_report)
        self.assertIn("Index Scan on chat_members", text_report)
        self.assertIn("group_message_history: skipped", text_report)

    def test_parse_args_supports_json_and_override_flags(self):
        args = report_script.parse_args([
            "--json",
            "--include-sql",
            "--history-limit",
            "25",
            "--group-current-user-id",
            "7",
            "--group-chat-id",
            "19",
        ])
        self.assertTrue(args.json)
        self.assertTrue(args.include_sql)
        self.assertEqual(args.history_limit, 25)
        self.assertEqual(args.group_current_user_id, 7)
        self.assertEqual(args.group_chat_id, 19)

    def test_json_output_shape_is_serializable(self):
        payload = {
            "reports": [
                {
                    "name": "channel_conversation_list",
                    "summary": {"node_type": "Nested Loop", "execution_time_ms": 1.0, "planning_time_ms": 0.2, "scan_nodes": []},
                }
            ],
            "skipped": [],
        }
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertIn("channel_conversation_list", encoded)


if __name__ == '__main__':
    unittest.main()