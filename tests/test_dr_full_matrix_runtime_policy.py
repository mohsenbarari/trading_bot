import unittest

from core.dr_full_matrix_runtime_policy import (
    CapacityWatermarks,
    ambiguous_retry_decision,
    amplified_webapp_decision,
    artifact_chain_decision,
    batch_flush_decision,
    bidirectional_capacity_decision,
    capacity_watermark_decision,
    dpi_budget_decision,
    durable_drain_decision,
    healthy_link_backlog_decision,
    recovery_eta_decision,
    relay_identity_decision,
    second_cycle_decision,
    writer_final_state_decision,
)


class FullMatrixRuntimePolicyTests(unittest.TestCase):
    def test_relay_queue_and_idempotency_gates(self):
        digest = "a" * 64
        self.assertTrue(
            all(
                relay_identity_decision(
                    origin_site="bot_fi",
                    relay_site="webapp_fi",
                    destination_site="webapp_ir",
                    received_origin_site="bot_fi",
                    received_envelope_hash=digest,
                    source_envelope_hash=digest,
                    echo_destination=None,
                ).values()
            )
        )
        self.assertTrue(
            all(
                durable_drain_decision(
                    committed_jobs=10,
                    wakeups_delivered=0,
                    claimed_jobs=10,
                    terminal_jobs=10,
                    duplicate_effects=0,
                ).values()
            )
        )
        self.assertTrue(
            all(
                ambiguous_retry_decision(
                    command_attempts=2,
                    committed_commands=1,
                    business_rows=1,
                    outbox_jobs=1,
                    provider_effects=1,
                ).values()
            )
        )

    def test_capacity_and_recovery_gates(self):
        decisions = [
            bidirectional_capacity_decision(
                fi_to_peer_events=150,
                peer_to_fi_events=150,
                acknowledged_events=300,
                duplicate_applies=0,
            ),
            amplified_webapp_decision(
                source_events=300,
                destination_deliveries=600,
                destination_receipts=600,
                relay_echoes=0,
            ),
            batch_flush_decision(
                committed_before_flush=64,
                flushed=64,
                acknowledged=64,
                stranded=0,
            ),
            capacity_watermark_decision(
                CapacityWatermarks(0.3, 0.2, 0.4, 0.5, 0.1)
            ),
            dpi_budget_decision(
                request_bytes=1024,
                response_bytes=2048,
                configured_request_limit=4096,
                configured_response_limit=4096,
                oversized_request_rejected=True,
            ),
            recovery_eta_decision(
                initial_backlog=100,
                final_backlog=0,
                live_ingress_events=20,
                applied_events=120,
                elapsed_seconds=10,
                declared_eta_seconds=0,
            ),
            healthy_link_backlog_decision(
                samples=[0, 1, 0],
                oldest_age_seconds=1,
                unresolved_gaps=0,
            ),
        ]
        for decision in decisions:
            self.assertTrue(all(decision.values()), decision)

    def test_final_state_chain_and_repeatability_gates(self):
        self.assertTrue(
            all(
                writer_final_state_decision(
                    active_site="webapp_fi",
                    public_origin_site="webapp_fi",
                    fi_control_state="active",
                    ir_runtime_role="standby",
                    active_epoch=4,
                    prior_epoch=3,
                ).values()
            )
        )
        hashes = ["a" * 64, "b" * 64]
        import hashlib

        head = "0" * 64
        for value in hashes:
            head = hashlib.sha256(f"{head}:{value}".encode()).hexdigest()
        self.assertTrue(
            all(
                artifact_chain_decision(
                    ordered_hashes=hashes,
                    retained_head=head,
                    external_anchor=head,
                ).values()
            )
        )
        self.assertTrue(
            all(
                second_cycle_decision(
                    first_cycle={"lag": 2.0, "coverage": 10},
                    second_cycle={"lag": 1.0, "coverage": 10},
                    lower_is_better={"lag"},
                ).values()
            )
        )


if __name__ == "__main__":
    unittest.main()
