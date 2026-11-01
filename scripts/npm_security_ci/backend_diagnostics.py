"""Finite public backend telemetry for this isolated hosted CI only.

An observed or unavailable diagnostic never grants health/test acceptance.
No lifecycle, complete Config/Env, credentials or recovery commands. A failed
browser gate may retain only public traceback structure from its original App.
"""
import argparse
from contextlib import ExitStack,contextmanager
import errno
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from types import ModuleType

ROOT=Path(__file__).resolve().parents[2]
NAMES=('trading_bot_app','trading_bot_db','trading_bot_redis')
PINS={
 'scripts/npm_security_ci/browser_evidence.py':(10369,'453153185f1bed7bbf88baea8dc6959c32042eb175e9c245c6df9138b0545327'),
 'deploy/maintenance/glibc_backport_native239/host_io_original.py':(53351,'fc9fc8e4ae7fe1af75fd0b4c63fedb0ebfa5820148bb311c5eaafcaaa5bbbb82'),
}
SYNTHETIC_SOURCE_PINS={'.github/workflows/npm-security-backports.yml': (16996, 'a15ce4d4197db4f7e10e86bb52e13faedd03f7ecce0913dfa38b08856fec6145'), 'scripts/bootstrap_ci_backend.sh': (2658, '738244554c769336dd04c74894235e133f600cab43d26a49e65f84860d6a6151'), 'docker-compose.yml': (11834, '394f0ca37559c2d083987ae74b02ab4fd1e510886fe635484eb8af29570985d8'), 'Dockerfile': (5812, '54447fdee488e17246238796cb5e6778a382227a51399256da0b22eeb147178e'), '.dockerignore': (939, '74bc46670bfb452f913b93597a193e1e2312138bd850546dacfa454c033b3b3e'), 'core/logging_config.py': (7026, '41751aad4f54aea1c5df525e881717e522c059bbbf36aff405bc46da713c455b')}
PUBLIC_PROJECT_SOURCE_LINES={'api/__init__.py': 8, 'api/admin_authority.py': 29, 'api/deps.py': 257, 'api/routers/__init__.py': 1, 'api/routers/accountants.py': 396, 'api/routers/admin_messages.py': 317, 'api/routers/auth.py': 2953, 'api/routers/blocks.py': 235, 'api/routers/chat.py': 2807, 'api/routers/chat_schemas.py': 764, 'api/routers/commodities.py': 471, 'api/routers/customers.py': 872, 'api/routers/invitations.py': 449, 'api/routers/notifications.py': 421, 'api/routers/offers.py': 3255, 'api/routers/realtime.py': 659, 'api/routers/sessions.py': 1358, 'api/routers/sync.py': 5108, 'api/routers/trades.py': 5876, 'api/routers/trading_settings.py': 374, 'api/routers/user_flags.py': 152, 'api/routers/users.py': 717, 'api/routers/users_public.py': 629, 'bot/__init__.py': 13, 'bot/callbacks.py': 166, 'bot/handlers/__init__.py': 38, 'bot/handlers/admin.py': 437, 'bot/handlers/admin_broadcast.py': 678, 'bot/handlers/admin_commodities.py': 746, 'bot/handlers/admin_users.py': 1864, 'bot/handlers/block_manage.py': 503, 'bot/handlers/commodity_catalog.py': 267, 'bot/handlers/default.py': 56, 'bot/handlers/link_account.py': 832, 'bot/handlers/offer_overtime_callbacks.py': 388, 'bot/handlers/offer_overtime_preference.py': 285, 'bot/handlers/panel.py': 1477, 'bot/handlers/start.py': 1697, 'bot/handlers/telegram_publisher_b2b.py': 54, 'bot/handlers/telegram_publisher_channel_callbacks.py': 70, 'bot/handlers/trade_create.py': 3546, 'bot/handlers/trade_execute.py': 1288, 'bot/handlers/trade_history.py': 635, 'bot/handlers/trade_manage.py': 359, 'bot/handlers/trade_utils.py': 248, 'bot/keyboards.py': 436, 'bot/message_manager.py': 153, 'bot/middlewares/__init__.py': 14, 'bot/middlewares/auth.py': 161, 'bot/middlewares/callback_receipt.py': 46, 'bot/middlewares/logging_context.py': 87, 'bot/middlewares/stale_navigation_handoff.py': 36, 'bot/middlewares/telegram_bot_identity.py': 29, 'bot/middlewares/trade_contention_gate.py': 185, 'bot/onboarding.py': 124, 'bot/overtime_request_status.py': 317, 'bot/repeat_offer.py': 378, 'bot/states.py': 76, 'bot/telegram_callback_answer.py': 84, 'bot/telegram_command_menu.py': 63, 'bot/telegram_interaction_message.py': 864, 'bot/telegram_pre_auth_interaction.py': 376, 'bot/utils/__init__.py': 22, 'bot/utils/channel_invites.py': 87, 'bot/utils/customer_display.py': 97, 'bot/utils/offer_parser.py': 695, 'bot/utils/public_profile.py': 248, 'bot/utils/redis_helpers.py': 313, 'bot/utils/trade_suggestion_messages.py': 560, 'core/__init__.py': 0, 'core/admin_authority.py': 100, 'core/async_checkpoints.py': 21, 'core/audit_logger.py': 95, 'core/audit_sink.py': 248, 'core/background_job_authority.py': 436, 'core/cache.py': 311, 'core/commodity_defaults.py': 14, 'core/config.py': 466, 'core/connectivity.py': 103, 'core/customer_invite.py': 243, 'core/customer_invite_forwarding.py': 109, 'core/db.py': 72, 'core/deployment_surface.py': 115, 'core/enums.py': 80, 'core/error_tracking.py': 220, 'core/events.py': 2059, 'core/frontend_static.py': 71, 'core/invitation_contract_service.py': 158, 'core/invitation_creation_contracts.py': 87, 'core/invitation_creation_forwarding.py': 81, 'core/invitation_sms_policy.py': 34, 'core/job_logging.py': 105, 'core/jwt_codec.py': 11, 'core/log_redaction.py': 221, 'core/logging_config.py': 219, 'core/market_intelligence/__init__.py': 21, 'core/market_intelligence/capture_event_adapter.py': 2921, 'core/market_intelligence/coin_catalog.py': 246, 'core/market_intelligence/coin_condition_review.py': 1150, 'core/market_intelligence/coin_group_calibration_corpus.py': 251, 'core/market_intelligence/coin_group_feedback.py': 673, 'core/market_intelligence/coin_group_payloads.py': 258, 'core/market_intelligence/coin_group_pipeline.py': 1264, 'core/market_intelligence/coin_group_resolution.py': 641, 'core/market_intelligence/coin_group_review_projection.py': 378, 'core/market_intelligence/coin_group_staging.py': 508, 'core/market_intelligence/coin_group_trades.py': 830, 'core/market_intelligence/coin_groups.py': 679, 'core/market_intelligence/coin_inference.py': 304, 'core/market_intelligence/coin_inference_audit.py': 223, 'core/market_intelligence/coin_inference_outcome.py': 151, 'core/market_intelligence/coin_inference_rollout_metrics.py': 256, 'core/market_intelligence/coin_inference_selection.py': 125, 'core/market_intelligence/coin_inference_shadow.py': 214, 'core/market_intelligence/coin_offer_conditions.py': 462, 'core/market_intelligence/coin_prediction_anchors.py': 267, 'core/market_intelligence/coin_rate_engine.py': 659, 'core/market_intelligence/coin_relationship_challenger.py': 160, 'core/market_intelligence/coin_relationships.py': 151, 'core/market_intelligence/consumed_input_parity.py': 454, 'core/market_intelligence/conversation_quality.py': 1073, 'core/market_intelligence/estimator_snapshot_receiver.py': 2014, 'core/market_intelligence/estimator_snapshot_receiver_service.py': 372, 'core/market_intelligence/estimator_snapshot_runtime.py': 894, 'core/market_intelligence/external_markets.py': 184, 'core/market_intelligence/external_quote_capture.py': 601, 'core/market_intelligence/herat_price_normalization.py': 138, 'core/market_intelligence/input_health.py': 465, 'core/market_intelligence/market_contracts.py': 404, 'core/market_intelligence/market_fact_adapter.py': 1576, 'core/market_intelligence/market_fact_archive.py': 490, 'core/market_intelligence/market_fact_projection.py': 1160, 'core/market_intelligence/market_fact_receiver.py': 638, 'core/market_intelligence/market_fact_receiver_service.py': 337, 'core/market_intelligence/market_fact_recovery.py': 249, 'core/market_intelligence/market_fact_sync.py': 701, 'core/market_intelligence/market_history_backfill.py': 773, 'core/market_intelligence/market_history_export.py': 657, 'core/market_intelligence/market_history_operations.py': 498, 'core/market_intelligence/market_input_materializer.py': 581, 'core/market_intelligence/market_regime.py': 732, 'core/market_intelligence/market_research_backfill.py': 315, 'core/market_intelligence/market_snapshot.py': 945, 'core/market_intelligence/market_store.py': 1010, 'core/market_intelligence/melted_relationship_challenger.py': 135, 'core/market_intelligence/melted_relationships.py': 279, 'core/market_intelligence/price_magnitude_policy.py': 216, 'core/market_intelligence/private_capture.py': 2745, 'core/market_intelligence/private_capture_service.py': 226, 'core/market_intelligence/private_capture_telegram.py': 1446, 'core/market_intelligence/private_coin_processor.py': 1204, 'core/market_intelligence/private_gold.py': 650, 'core/market_intelligence/private_gold_payloads.py': 392, 'core/market_intelligence/private_gold_pipeline.py': 165, 'core/market_intelligence/private_gold_staging.py': 521, 'core/market_intelligence/private_gold_telegram.py': 222, 'core/market_intelligence/private_gold_trade_revisions.py': 270, 'core/market_intelligence/private_market_transport.py': 351, 'core/market_intelligence/private_pipeline_contracts.py': 759, 'core/market_intelligence/private_pipeline_foundation.py': 1011, 'core/market_intelligence/private_tls_server.py': 60, 'core/market_intelligence/product_snapshot_reader.py': 550, 'core/market_intelligence/project_outbox.py': 235, 'core/market_intelligence/project_outbox_consumer.py': 373, 'core/market_intelligence/public_telegram/__init__.py': 15, 'core/market_intelligence/public_telegram/ingest.py': 477, 'core/market_intelligence/public_telegram/parser.py': 293, 'core/market_intelligence/public_telegram/sources.py': 44, 'core/market_intelligence/public_telegram/transport.py': 245, 'core/market_intelligence/relationship_ledger.py': 460, 'core/market_intelligence/research_archive.py': 441, 'core/market_intelligence/shadow_legacy_bridge.py': 1073, 'core/market_intelligence/shadow_parity.py': 1625, 'core/market_intelligence/single_owner_parity.py': 1068, 'core/market_intelligence/snapshot_publisher.py': 165, 'core/market_intelligence/xau_model_input.py': 54, 'core/market_presence.py': 96, 'core/market_schedule_loop.py': 129, 'core/metrics.py': 670, 'core/notifications.py': 66, 'core/offer_expiry.py': 482, 'core/offer_expiry_contracts.py': 171, 'core/offer_expiry_forwarding.py': 182, 'core/offer_identity.py': 38, 'core/offer_lifecycle.py': 285, 'core/offer_overtime_bot_copy.py': 128, 'core/offer_overtime_preference_transport.py': 102, 'core/offer_overtime_request_forwarding.py': 157, 'core/offer_priority_sync.py': 253, 'core/offer_publication_worker.py': 776, 'core/offer_quantity.py': 12, 'core/offer_request_identity.py': 26, 'core/offer_request_policy.py': 169, 'core/offer_settlement.py': 105, 'core/offer_source.py': 44, 'core/offer_sync_payload.py': 64, 'core/otp_sms_fallback_worker.py': 238, 'core/overtime_observability.py': 147, 'core/pack_commodities.py': 83, 'core/production_test_isolation.py': 192, 'core/public_webapp_url.py': 117, 'core/redis.py': 106, 'core/registration_contracts.py': 314, 'core/registration_feature_policy.py': 19, 'core/registration_identity.py': 95, 'core/registration_observability.py': 443, 'core/registration_sync_policy.py': 261, 'core/request_context.py': 34, 'core/request_logging.py': 311, 'core/schema_revision.py': 33, 'core/security.py': 71, 'core/server_routing.py': 94, 'core/services/__init__.py': 1, 'core/services/accountant_chat_contract.py': 208, 'core/services/accountant_relation_service.py': 731, 'core/services/admin_message_service.py': 377, 'core/services/authoritative_registration_service.py': 1127, 'core/services/authoritative_telegram_account_link_service.py': 311, 'core/services/avatar_service.py': 28, 'core/services/block_service.py': 489, 'core/services/bot_access_policy.py': 208, 'core/services/canonical_invitation_creation_service.py': 170, 'core/services/chat_backfill_service.py': 232, 'core/services/chat_role_badge_service.py': 105, 'core/services/chat_room_service.py': 2874, 'core/services/chat_service.py': 1929, 'core/services/chat_upload_session_service.py': 1022, 'core/services/cross_server_recovery_service.py': 395, 'core/services/customer_relation_service.py': 1503, 'core/services/invitation_identity_reservation_service.py': 232, 'core/services/invitation_lifecycle_service.py': 112, 'core/services/invitation_public_access_service.py': 86, 'core/services/invitation_requester_service.py': 61, 'core/services/invitation_sms_delivery_service.py': 178, 'core/services/invitation_transition_lock_service.py': 114, 'core/services/market_schedule_service.py': 267, 'core/services/market_transition_service.py': 1097, 'core/services/offer_cancel_all_service.py': 472, 'core/services/offer_creation_service.py': 580, 'core/services/offer_expiry_command_receipt_service.py': 186, 'core/services/offer_expiry_gate.py': 95, 'core/services/offer_expiry_limits.py': 70, 'core/services/offer_expiry_service.py': 224, 'core/services/offer_model_price_guard.py': 470, 'core/services/offer_overtime_preference_service.py': 308, 'core/services/offer_overtime_reconciliation_service.py': 669, 'core/services/offer_overtime_request_service.py': 1365, 'core/services/offer_publication_reconciliation_service.py': 924, 'core/services/offer_publication_state_service.py': 424, 'core/services/offer_republish_service.py': 219, 'core/services/offer_request_ledger_service.py': 270, 'core/services/otp_delivery_state_service.py': 648, 'core/services/otp_sms_delivery_service.py': 69, 'core/services/registration_command_receipt_service.py': 150, 'core/services/registration_notification_service.py': 180, 'core/services/session_reset_service.py': 96, 'core/services/session_service.py': 503, 'core/services/single_session_recovery_service.py': 527, 'core/services/telegram_admin_broadcast_delivery_service.py': 743, 'core/services/telegram_admin_broadcast_queue_feedback.py': 420, 'core/services/telegram_admin_broadcast_queue_service.py': 356, 'core/services/telegram_admin_broadcast_service.py': 690, 'core/services/telegram_callback_queue_feedback.py': 76, 'core/services/telegram_callback_queue_service.py': 110, 'core/services/telegram_channel_membership_queue_feedback.py': 200, 'core/services/telegram_channel_membership_saga_service.py': 252, 'core/services/telegram_delivery_observability_service.py': 687, 'core/services/telegram_delivery_queue_service.py': 2070, 'core/services/telegram_delivery_reconciliation_service.py': 776, 'core/services/telegram_delivery_resume_service.py': 973, 'core/services/telegram_delivery_retention_service.py': 636, 'core/services/telegram_delivery_rollback_service.py': 188, 'core/services/telegram_delivery_runtime_gate_service.py': 677, 'core/services/telegram_interaction_outbox_service.py': 554, 'core/services/telegram_link_token_service.py': 196, 'core/services/telegram_market_notice_queue_feedback.py': 361, 'core/services/telegram_market_notice_queue_service.py': 258, 'core/services/telegram_notification_outbox_orphan_reconciliation_service.py': 160, 'core/services/telegram_notification_outbox_queue_feedback.py': 611, 'core/services/telegram_notification_outbox_queue_service.py': 645, 'core/services/telegram_notification_outbox_service.py': 1599, 'core/services/telegram_offer_channel_service.py': 617, 'core/services/telegram_offer_publication_service.py': 490, 'core/services/telegram_offer_queue_feedback.py': 580, 'core/services/telegram_offer_queue_service.py': 818, 'core/services/telegram_otp_delivery_service.py': 30, 'core/services/telegram_otp_ephemeral_queue.py': 1067, 'core/services/telegram_overtime_owner_approval_legacy_service.py': 248, 'core/services/telegram_overtime_owner_approval_queue_feedback.py': 184, 'core/services/telegram_overtime_owner_approval_queue_service.py': 175, 'core/services/telegram_overtime_requester_status_service.py': 66, 'core/services/telegram_publisher_dispatch_service.py': 696, 'core/services/telegram_registration_intent_service.py': 599, 'core/services/telegram_scheduled_operation_queue_feedback.py': 263, 'core/services/telegram_scheduled_operation_service.py': 618, 'core/services/telegram_trade_result_queue_feedback.py': 409, 'core/services/telegram_trade_result_queue_service.py': 299, 'core/services/telegram_username_observation_service.py': 126, 'core/services/trade_contention_gate.py': 140, 'core/services/trade_delivery_receipt_service.py': 1099, 'core/services/trade_delivery_reconciliation_service.py': 629, 'core/services/trade_history_export_service.py': 318, 'core/services/trade_notification_audience_service.py': 821, 'core/services/trade_service.py': 780, 'core/services/trade_telegram_delivery_service.py': 1003, 'core/services/trade_webapp_delivery_service.py': 633, 'core/services/user_account_status_service.py': 378, 'core/services/user_deletion_service.py': 588, 'core/services/user_flag_service.py': 241, 'core/services/user_management_context_service.py': 175, 'core/session_authority.py': 287, 'core/session_expiry.py': 86, 'core/sms.py': 457, 'core/sync_authority.py': 19, 'core/sync_field_policy.py': 752, 'core/sync_metadata.py': 323, 'core/sync_outbox_guard.py': 451, 'core/sync_parity.py': 561, 'core/sync_parity_observability.py': 229, 'core/sync_protocol.py': 172, 'core/sync_push.py': 197, 'core/sync_registry.py': 643, 'core/sync_repair.py': 401, 'core/sync_transport.py': 86, 'core/sync_worker.py': 774, 'core/telegram_account_link_contracts.py': 149, 'core/telegram_admin_broadcast_queue_feeder.py': 147, 'core/telegram_admin_broadcast_worker.py': 160, 'core/telegram_bot_identity_context.py': 21, 'core/telegram_bot_runtime_role.py': 169, 'core/telegram_bot_runtime_topology.py': 132, 'core/telegram_bot_split_compose_operator.py': 427, 'core/telegram_bot_split_cutover.py': 438, 'core/telegram_callback_receipt_context.py': 32, 'core/telegram_central_poller_owner.py': 157, 'core/telegram_delivery_account_notice_contract.py': 204, 'core/telegram_delivery_admin_broadcast_freshness.py': 630, 'core/telegram_delivery_callback_contract.py': 117, 'core/telegram_delivery_callback_freshness.py': 173, 'core/telegram_delivery_channel_membership_freshness.py': 262, 'core/telegram_delivery_credentials.py': 323, 'core/telegram_delivery_cutover_contract.py': 191, 'core/telegram_delivery_freshness_router.py': 359, 'core/telegram_delivery_interaction_result_contract.py': 519, 'core/telegram_delivery_lifecycle_router.py': 220, 'core/telegram_delivery_market_freshness.py': 454, 'core/telegram_delivery_new_user_membership_freshness.py': 460, 'core/telegram_delivery_notification_action_contract.py': 145, 'core/telegram_delivery_notification_action_freshness.py': 1163, 'core/telegram_delivery_offer_freshness.py': 687, 'core/telegram_delivery_offer_success_contract.py': 60, 'core/telegram_delivery_offer_success_freshness.py': 483, 'core/telegram_delivery_overtime_owner_approval_contract.py': 220, 'core/telegram_delivery_overtime_owner_approval_freshness.py': 264, 'core/telegram_delivery_overtime_requester_status_contract.py': 53, 'core/telegram_delivery_preflight.py': 655, 'core/telegram_delivery_queue_contract.py': 1680, 'core/telegram_delivery_queue_limiter.py': 741, 'core/telegram_delivery_queue_owner.py': 151, 'core/telegram_delivery_queue_wakeup.py': 188, 'core/telegram_delivery_queue_worker.py': 2454, 'core/telegram_delivery_relink_policy.py': 96, 'core/telegram_delivery_repeat_offer_freshness.py': 461, 'core/telegram_delivery_runtime_composition.py': 334, 'core/telegram_delivery_runtime_policy.py': 241, 'core/telegram_delivery_scheduled_operation_freshness.py': 475, 'core/telegram_delivery_trade_freshness.py': 612, 'core/telegram_delivery_trade_result_binding.py': 66, 'core/telegram_dispatch_latency_baseline.py': 74, 'core/telegram_dispatch_latency_cadence.py': 73, 'core/telegram_dispatch_latency_hot_path.py': 154, 'core/telegram_dispatch_latency_midpoint.py': 261, 'core/telegram_dispatch_latency_pool.py': 261, 'core/telegram_gateway.py': 629, 'core/telegram_legacy_otp_relay_contract.py': 49, 'core/telegram_market_notice_queue_feeder.py': 154, 'core/telegram_market_notice_worker.py': 75, 'core/telegram_multi_publisher_contract.py': 294, 'core/telegram_notification_outbox_queue_feeder.py': 151, 'core/telegram_notification_outbox_worker.py': 180, 'core/telegram_offer_queue_feeder.py': 285, 'core/telegram_otp_transport.py': 84, 'core/telegram_publisher_b2b_runtime.py': 58, 'core/telegram_queue_stage4_harness.py': 2165, 'core/telegram_queue_stage4_workload.py': 860, 'core/telegram_registration_reconciliation_worker.py': 385, 'core/telegram_registration_transport.py': 118, 'core/telegram_scheduled_operation_queue_feeder.py': 160, 'core/telegram_trade_callbacks.py': 52, 'core/telegram_trade_result_queue_feeder.py': 137, 'core/trade_delivery_worker.py': 199, 'core/trade_forward_pending.py': 202, 'core/trade_forwarding.py': 167, 'core/trading_observability.py': 253, 'core/trading_settings.py': 497, 'core/user_account_status_loop.py': 60, 'core/user_counter_sync.py': 241, 'core/utils.py': 542, 'core/web_push.py': 535, 'main.py': 591, 'models/__init__.py': 205, 'models/accountant_relation.py': 84, 'models/admin_message.py': 42, 'models/change_log.py': 44, 'models/chat.py': 43, 'models/chat_file.py': 29, 'models/chat_member.py': 79, 'models/coin_intelligence_inference_audit.py': 88, 'models/coin_intelligence_inference_outcome.py': 56, 'models/coin_intelligence_market_outbox.py': 58, 'models/commodity.py': 22, 'models/conversation.py': 64, 'models/customer_relation.py': 107, 'models/database.py': 7, 'models/invitation.py': 108, 'models/invitation_identity_reservation.py': 35, 'models/invitation_sms_delivery.py': 36, 'models/market_channel_notice_receipt.py': 93, 'models/market_runtime_state.py': 21, 'models/market_schedule_override.py': 52, 'models/message.py': 82, 'models/notification.py': 37, 'models/offer.py': 161, 'models/offer_expiry_command_receipt.py': 46, 'models/offer_publication_state.py': 118, 'models/offer_request.py': 283, 'models/push_subscription.py': 34, 'models/session.py': 165, 'models/sync_apply_watermark.py': 32, 'models/sync_block.py': 17, 'models/telegram_admin_broadcast.py': 240, 'models/telegram_channel_membership_saga.py': 106, 'models/telegram_delivery_feeder_state.py': 36, 'models/telegram_delivery_job.py': 308, 'models/telegram_delivery_provider_outcome.py': 108, 'models/telegram_delivery_reconciliation_evidence.py': 55, 'models/telegram_delivery_resume_operation.py': 136, 'models/telegram_delivery_runtime_gate.py': 93, 'models/telegram_interaction_anchor_state.py': 105, 'models/telegram_link_token.py': 51, 'models/telegram_notification_outbox.py': 149, 'models/telegram_publisher_dispatch_command.py': 125, 'models/telegram_registration_command_receipt.py': 55, 'models/telegram_registration_intent.py': 72, 'models/telegram_scheduled_operation.py': 137, 'models/trade.py': 123, 'models/trade_delivery_receipt.py': 150, 'models/trading_setting.py': 28, 'models/upload_session.py': 159, 'models/user.py': 135, 'models/user_block.py': 40, 'models/user_counter_event_receipt.py': 60, 'models/user_flag.py': 58, 'models/user_notification_preference.py': 28, 'schemas.py': 558, 'src/__init__.py': 0, 'src/core/__init__.py': 0, 'src/core/config/__init__.py': 0, 'src/core/entities/__init__.py': 8, 'src/core/entities/offer.py': 40, 'src/core/entities/user.py': 36, 'src/core/exceptions/__init__.py': 26, 'src/core/exceptions/base.py': 34, 'src/core/exceptions/user_exceptions.py': 28, 'src/core/repositories/__init__.py': 10, 'src/core/repositories/base.py': 36, 'src/core/repositories/offer_repository.py': 41, 'src/core/repositories/user_repository.py': 41, 'src/core/schemas/__init__.py': 8, 'src/core/schemas/offer_schemas.py': 60, 'src/core/schemas/user_schemas.py': 55, 'src/core/services/__init__.py': 8, 'src/core/services/offer_service.py': 153, 'src/core/services/user_service.py': 106, 'src/infrastructure/__init__.py': 0, 'src/infrastructure/cache/__init__.py': 0, 'src/infrastructure/database/__init__.py': 9, 'src/infrastructure/database/connection.py': 49, 'src/infrastructure/database/models/__init__.py': 0, 'src/infrastructure/database/repositories/__init__.py': 6, 'src/infrastructure/database/repositories/sqlalchemy_user_repo.py': 110, 'src/infrastructure/external/__init__.py': 0, 'src/interfaces/__init__.py': 0, 'src/interfaces/http_api/__init__.py': 0, 'src/interfaces/http_api/dependencies.py': 74, 'src/interfaces/http_api/middlewares/__init__.py': 0, 'src/interfaces/http_api/routers/__init__.py': 4, 'src/interfaces/http_api/routers/user_router.py': 73, 'src/interfaces/http_api/schemas/__init__.py': 0, 'src/interfaces/telegram_bot/__init__.py': 0, 'src/interfaces/telegram_bot/dependencies.py': 94, 'src/interfaces/telegram_bot/handlers/__init__.py': 0, 'src/interfaces/telegram_bot/keyboards/__init__.py': 0, 'src/interfaces/telegram_bot/middlewares/__init__.py': 0, 'src/interfaces/telegram_bot/states/__init__.py': 0, 'src/shared/__init__.py': 0, 'src/shared/utils/__init__.py': 0}
PUBLIC_EXCEPTION_CLASSES=frozenset(('ArithmeticError', 'AssertionError', 'AttributeError', 'BaseExceptionGroup', 'BlockingIOError', 'BrokenPipeError', 'BufferError', 'ChildProcessError', 'ConnectionAbortedError', 'ConnectionError', 'ConnectionRefusedError', 'ConnectionResetError', 'EOFError', 'Exception', 'ExceptionGroup', 'FileExistsError', 'FileNotFoundError', 'FloatingPointError', 'ImportError', 'IndentationError', 'IndexError', 'InterruptedError', 'IsADirectoryError', 'KeyError', 'LookupError', 'MemoryError', 'ModuleNotFoundError', 'NameError', 'NotADirectoryError', 'NotImplementedError', 'OSError', 'OverflowError', 'PermissionError', 'ProcessLookupError', 'RecursionError', 'ReferenceError', 'RuntimeError', 'StopAsyncIteration', 'StopIteration', 'SyntaxError', 'SystemError', 'TabError', 'TimeoutError', 'TypeError', 'UnboundLocalError', 'UnicodeDecodeError', 'UnicodeEncodeError', 'UnicodeError', 'UnicodeTranslateError', 'ValueError', 'ZeroDivisionError', 'asyncpg.exceptions.CheckViolationError', 'asyncpg.exceptions.DataError', 'asyncpg.exceptions.ForeignKeyViolationError', 'asyncpg.exceptions.NotNullViolationError', 'asyncpg.exceptions.PostgresError', 'asyncpg.exceptions.UndefinedColumnError', 'asyncpg.exceptions.UndefinedTableError', 'asyncpg.exceptions.UniqueViolationError', 'builtins.ArithmeticError', 'builtins.AssertionError', 'builtins.AttributeError', 'builtins.BaseExceptionGroup', 'builtins.BlockingIOError', 'builtins.BrokenPipeError', 'builtins.BufferError', 'builtins.ChildProcessError', 'builtins.ConnectionAbortedError', 'builtins.ConnectionError', 'builtins.ConnectionRefusedError', 'builtins.ConnectionResetError', 'builtins.EOFError', 'builtins.Exception', 'builtins.ExceptionGroup', 'builtins.FileExistsError', 'builtins.FileNotFoundError', 'builtins.FloatingPointError', 'builtins.ImportError', 'builtins.IndentationError', 'builtins.IndexError', 'builtins.InterruptedError', 'builtins.IsADirectoryError', 'builtins.KeyError', 'builtins.LookupError', 'builtins.MemoryError', 'builtins.ModuleNotFoundError', 'builtins.NameError', 'builtins.NotADirectoryError', 'builtins.NotImplementedError', 'builtins.OSError', 'builtins.OverflowError', 'builtins.PermissionError', 'builtins.ProcessLookupError', 'builtins.RecursionError', 'builtins.ReferenceError', 'builtins.RuntimeError', 'builtins.StopAsyncIteration', 'builtins.StopIteration', 'builtins.SyntaxError', 'builtins.SystemError', 'builtins.TabError', 'builtins.TimeoutError', 'builtins.TypeError', 'builtins.UnboundLocalError', 'builtins.UnicodeDecodeError', 'builtins.UnicodeEncodeError', 'builtins.UnicodeError', 'builtins.UnicodeTranslateError', 'builtins.ValueError', 'builtins.ZeroDivisionError', 'fastapi.exceptions.HTTPException', 'fastapi.exceptions.RequestValidationError', 'fastapi.exceptions.ResponseValidationError', 'pydantic.error_wrappers.ValidationError', 'pydantic_core._pydantic_core.ValidationError', 'redis.exceptions.ConnectionError', 'redis.exceptions.RedisError', 'redis.exceptions.ResponseError', 'redis.exceptions.TimeoutError', 'sqlalchemy.exc.ArgumentError', 'sqlalchemy.exc.DBAPIError', 'sqlalchemy.exc.DataError', 'sqlalchemy.exc.IntegrityError', 'sqlalchemy.exc.InterfaceError', 'sqlalchemy.exc.InternalError', 'sqlalchemy.exc.InvalidRequestError', 'sqlalchemy.exc.MissingGreenlet', 'sqlalchemy.exc.MultipleResultsFound', 'sqlalchemy.exc.NoResultFound', 'sqlalchemy.exc.OperationalError', 'sqlalchemy.exc.PendingRollbackError', 'sqlalchemy.exc.ProgrammingError', 'sqlalchemy.exc.ResourceClosedError', 'sqlalchemy.exc.SQLAlchemyError', 'sqlalchemy.exc.StatementError', 'sqlalchemy.exc.TimeoutError', 'sqlalchemy.orm.exc.DetachedInstanceError', 'sqlalchemy.orm.exc.FlushError', 'sqlalchemy.orm.exc.StaleDataError', 'starlette.exceptions.HTTPException', 'starlette.requests.ClientDisconnect'))
APP_TAIL_MAX=2*1024*1024
EXCEPTION_FAILURE_PHASES=('scope','source_admission','before_observer','app_inspect','log_read','parse','after_inspect','finality')
EXCEPTION_FAILURE_CODES=frozenset((
 'exception_gate_scope','synthetic_source_inventory','public_source_path','public_source_metadata','public_source_pin','public_source_changed','public_source_close','public_source_postclose',
 'before_observer_subject','inspect_shape','inspect_subject','inspect_integer','inspect_status','app_scope_shape','app_scope_subject','app_scope_state','app_mount_count','app_mount_shape','app_mount_destination','app_mount_source','app_scope_changed',
 'exception_command_bound','app_tail_bound','duplicate_json_key','nonfinite_JSON','deadline','command_output_bound','command_failed','command_physical_close',
))
APP_SCOPE='{"Name":{{json .Name}},"Id":{{json .Id}},"Image":{{json .Config.Image}},"Service":{{json (index .Config.Labels "com.docker.compose.service")}},"Project":{{json (index .Config.Labels "com.docker.compose.project")}},"WorkingDir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}},"ConfigFiles":{{json (index .Config.Labels "com.docker.compose.project.config_files")}},"RestartCount":{{json .RestartCount}},"OOMKilled":{{json .State.OOMKilled}},"Mounts":[{{range $i,$m := .Mounts}}{{if $i}},{{end}}{"Type":{{json $m.Type}},"Source":{{json $m.Source}},"Destination":{{json $m.Destination}},"Name":{{with index $m "Name"}}{{json .}}{{else}}""{{end}},"RW":{{json $m.RW}}}{{end}}]}'
INSPECT='{"Name":{{json .Name}},"Id":{{json .Id}},"Running":{{json .State.Running}},"Pid":{{json .State.Pid}},"ExitCode":{{json .State.ExitCode}},"OOMKilled":{{json .State.OOMKilled}},"StartedAt":{{json .State.StartedAt}},"FinishedAt":{{json .State.FinishedAt}},"RestartCount":{{json .RestartCount}},"Memory":{{json .HostConfig.Memory}},"NanoCpus":{{json .HostConfig.NanoCpus}},"Health":{{with index .State "Health"}}{{json .Status}}{{else}}"none"{{end}}}'
STATS='{"Name":{{json .Name}},"ID":{{json .ID}},"CPUPerc":{{json .CPUPerc}},"MemUsage":{{json .MemUsage}},"MemPerc":{{json .MemPerc}},"PIDs":{{json .PIDs}}}'
def need(value,code):
    if not value:raise ValueError(code)
def frame(s):return(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
@contextmanager
def held_public(path,maximum,pin=None):
    need(path.resolve(strict=True)==path,'public_source_path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    before=flags=offset=None
    def guard():
        need(frame(os.fstat(fd))==frame(path.lstat())==before and (fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD))==flags and os.lseek(fd,0,os.SEEK_CUR)==offset,'public_source_changed')
    try:
        before=frame(os.fstat(fd));flags=(fcntl.fcntl(fd,fcntl.F_GETFL),fcntl.fcntl(fd,fcntl.F_GETFD));offset=os.lseek(fd,0,os.SEEK_CUR)
        need(stat.S_ISREG(before[2])and before[3]==os.geteuid()and before[5]==1 and not before[2]&0o022 and 0<before[6]<=maximum,'public_source_metadata')
        raw=os.pread(fd,before[6]+1,0);need(len(raw)==before[6]and(pin is None or(len(raw),sha256(raw).hexdigest())==pin),'public_source_pin');guard()
        yield raw,guard
    finally:
        drift=None
        try:
            if before is not None and flags is not None and offset is not None:guard()
        except Exception as exc:drift=exc
        os.close(fd)
        try:os.fstat(fd)
        except OSError as exc:need(exc.errno==errno.EBADF,'public_source_close')
        else:need(False,'public_source_close')
        if before is not None:need(frame(path.lstat())==before,'public_source_postclose')
        if drift is not None:raise drift

def app_scope(value,cid):
    need(type(value)is dict and set(value)=={'Name','Id','Image','Service','Project','WorkingDir','ConfigFiles','RestartCount','OOMKilled','Mounts'},'app_scope_shape')
    project=ROOT.name
    need(value['Name']=='/trading_bot_app'and value['Id']==cid and value['Image']=='trading_bot_base'and value['Service']=='app'and value['Project']==project and value['WorkingDir']==str(ROOT)and value['ConfigFiles']==str(ROOT/'docker-compose.yml'),'app_scope_subject')
    need(type(value['RestartCount'])is int and 0<=value['RestartCount']<=2**63-1 and type(value['OOMKilled'])is bool,'app_scope_state')
    expected={
      '/app/certs':('bind','/tmp/trading-ci-certs','',True),
      '/app/runtime/coin-inference':('bind','/tmp/trading-ci-models/coin','',False),
      '/app/runtime/product-estimator':('bind','/tmp/trading-ci-models/product','',False),
      '/app/uploads':('volume',f'/var/lib/docker/volumes/{project}_uploads_data/_data',f'{project}_uploads_data',True),
      '/app/audit_trail':('volume',f'/var/lib/docker/volumes/{project}_audit_data/_data',f'{project}_audit_data',True),
    }
    rows=value['Mounts'];need(type(rows)is list and len(rows)==len(expected),'app_mount_count');seen=set()
    for row in rows:
        need(type(row)is dict and set(row)=={'Type','Source','Destination','Name','RW'},'app_mount_shape')
        destination=row['Destination'];need(type(destination)is str and destination in expected and destination not in seen and type(row['RW'])is bool,'app_mount_destination');seen.add(destination)
        need((row['Type'],row['Source'],row['Name'],row['RW'])==expected[destination],'app_mount_source')
    return {**value,'Mounts':sorted(rows,key=lambda row:row['Destination'])}

def structural_traces(raw):
    """Discard messages, source lines, HTTP/SQL payloads, headers and all values."""
    need(type(raw)is bytes and len(raw)<=APP_TAIL_MAX,'app_tail_bound')
    public_frames={'/app/'+name:(name,limit)for name,limit in PUBLIC_PROJECT_SOURCE_LINES.items()}
    traces=[];current=None;discarded=0;omitted=0;structured_seen=0;unsupported=0;unfinished=False
    # A private typed sentinel cannot be forged by any raw log string.
    record_boundary=object()
    def unique(pairs):
        result={}
        for key,value in pairs:need(key not in result,'trace_JSON_duplicate_key');result[key]=value
        return result
    def trace_lines():
        nonlocal discarded,structured_seen,unsupported
        for chunk in raw.splitlines():
            # The raw aggregate and each raw line are bounded before json.loads.
            if len(chunk)>APP_TAIL_MAX:unsupported+=1;continue
            line=chunk.decode('utf-8','replace')
            line=re.sub(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z ','',line)
            if not line.lstrip().startswith(('{','[')):
                yield line;continue
            yield record_boundary
            try:
                depth=0;quoted=False;escaped=False
                for char in line:
                    if quoted:
                        if escaped:escaped=False
                        elif char=='\\':escaped=True
                        elif char=='"':quoted=False
                    elif char=='"':quoted=True
                    elif char in '{[':depth+=1;need(depth<=16,'trace_JSON_depth')
                    elif char in '}]':depth-=1;need(depth>=0,'trace_JSON_depth')
                need(depth==0 and not quoted,'trace_JSON_framing')
                value=json.loads(line,object_pairs_hook=unique,parse_constant=lambda _:need(False,'trace_JSON_nonfinite'))
                need(type(value)is dict,'trace_JSON_root')
                if 'exception'not in value:discarded+=1;continue
                exception=value['exception'];need(type(exception)is dict and set(exception)=={'type','message','stacktrace'},'trace_JSON_exception_shape')
                selected=exception['stacktrace'];need(type(selected)is str and 0<len(selected.encode('utf-8'))<=APP_TAIL_MAX,'trace_JSON_stack_bound')
                structured_seen+=1
            except Exception:unsupported+=1
            else:
                # No top-level message/type, exception.message/type, stack_info,
                # Source lines or arbitrary JSON values enter the trace parser.
                yield from selected.splitlines()
            finally:
                # Rejected/nonexception records must isolate pending state too.
                yield record_boundary
    for line in trace_lines():
        if line is record_boundary:
            unfinished=unfinished or current is not None;current=None;continue
        if len(line)>4096:discarded+=1;continue
        for _ in range(16):
            group_line=re.fullmatch(r' {0,32}\| (.*)',line)
            if not group_line:break
            line=group_line[1]
        if re.fullmatch(r' {0,32}\+ Exception Group Traceback \(most recent call last\):',line):
            line='Traceback (most recent call last):'
        if line=='Traceback (most recent call last):':current={'application_frames':[],'dependency_frames':[]};continue
        if current is None:discarded+=1;continue
        match=re.fullmatch(r'  File "([A-Za-z0-9_./-]{1,160}\.py)", line ([1-9][0-9]{0,6}), in [A-Za-z0-9_<>.]{1,128}',line)
        if match:
            admitted=public_frames.get(match[1]);line_number=int(match[2])
            if admitted is None or not 1<=line_number<=admitted[1]:discarded+=1;continue
            canonical,maximum=admitted;row={'file':canonical,'line':line_number}
            items=current['application_frames'];limit=12
            if len(items)==limit:items.pop(0);omitted+=1
            items.append(row);continue
        match=re.fullmatch(r'([A-Za-z_][A-Za-z0-9_.]{0,127}):.*',line)
        if match and len(match[1])<=128 and(current['application_frames']or current['dependency_frames']):
            public_class=match[1]if match[1]in PUBLIC_EXCEPTION_CLASSES else'UNKNOWN_EXCEPTION_CLASS'
            row={**current,'exception_class':public_class,'count':1};same=next((v for v in traces if all(v[k]==row[k]for k in('application_frames','dependency_frames','exception_class'))),None)
            if same is not None:same['count']=min(4096,same['count']+1)
            elif len(traces)<16:traces.append(row)
            else:omitted+=1
            current=None
        else:discarded+=1
    unfinished=unfinished or current is not None;current=None
    return {'traces':traces,'non_structural_lines_discarded':discarded,'bounded_frames_or_traces_omitted':omitted,'unfinished_trace_discarded':unfinished,'structured_exception_records_seen':structured_seen,'unsupported_framing_count':unsupported,'raw_log_retained':False,'exception_messages_retained':False}

def exception_failure_code(error):
    # Only literal codes owned by admitted public guards may leave RAM.
    # Never stringify an exception or export its class, message or arguments.
    allowed=EXCEPTION_FAILURE_CODES if type(error)is ValueError else('deadline','command_output_bound','command_failed','command_physical_close')if type(error)is RuntimeError else()
    if allowed and len(error.args)==1 and type(error.args[0])is str and error.args[0]in allowed:return error.args[0]
    return 'UNKNOWN'
def collect_app_exception(run,document,env,bound,before_path):
    phase='scope';body_error=None;body_phase=None
    result={'status':'UNAVAILABLE','reason':'synthetic_scope_unproven','failed_phase':None,'failure_code':None,'raw_log_retained':False,'security_accepted':False,'health_assessment':None,'original_scope_readers_closed':False}
    try:
        need(hosted(os.environ)and bound['phase']=='after'and bound['original_gate_outcome']=='failure','exception_gate_scope')
        with ExitStack()as stack:
            try:
                guards=[];phase='source_admission'
                need(set(SYNTHETIC_SOURCE_PINS)=={'.github/workflows/npm-security-backports.yml','scripts/bootstrap_ci_backend.sh','docker-compose.yml','Dockerfile','.dockerignore','core/logging_config.py'},'synthetic_source_inventory')
                for relative,pin in SYNTHETIC_SOURCE_PINS.items():
                    _,guard=stack.enter_context(held_public(ROOT/relative,131072,pin));guards.append(guard)
                phase='before_observer'
                raw,guard=stack.enter_context(held_public(before_path,16384));guards.append(guard);before=document(raw)
                want={**bound,'phase':'before','original_gate_outcome':'pending'}
                need(type(before)is dict and before.get('schema')=='npm_browser_public_backend_diagnostics/1'and before.get('subject')==want,'before_observer_subject')
                observed=inspect_row(before['states']['trading_bot_app'],'trading_bot_app');cid=observed['Id'];end=time.monotonic()+20
                def check():
                    for guard in guards:guard()
                def command(argv,maximum):
                    check();data=run(['/usr/bin/docker','--host=unix:///var/run/docker.sock',*argv],env=env,deadline=end,maximum=maximum,guard=check);need(type(data)is bytes and len(data)<=maximum,'exception_command_bound');check();return data
                phase='app_inspect'
                scope=app_scope(document(command(['container','inspect','--format',APP_SCOPE,cid],8192)),cid)
                client={'actual_returncode':None,'actual_pipe_closed':None,'stdout_eof_observed':None,'maximum_output_bytes':APP_TAIL_MAX,'output_bound_exceeded':False}
                failed_phase=failure_code=None;phase='log_read'
                try:
                    raw=command(['container','logs','--tail','2000','--timestamps',cid],APP_TAIL_MAX)
                    client.update(actual_returncode=0,actual_pipe_closed=True,stdout_eof_observed=True,observed_output_bytes=len(raw));status='OBSERVED_STRUCTURAL_TAIL'
                except Exception as exc:
                    failure=getattr(exc,'public_command_failure',{});raw=b''
                    if type(failure)is dict:
                        for name in('actual_returncode','actual_pipe_closed','stdout_eof_observed','observed_output_bytes'):
                            if type(failure.get(name))in(int,bool)or failure.get(name)is None:client[name]=failure.get(name)
                        captured=failure.get('captured_output')
                        if type(captured)is bytes and len(captured)<=APP_TAIL_MAX:raw=captured
                        count=client.get('observed_output_bytes');client['output_bound_exceeded']=type(count)is int and count>APP_TAIL_MAX
                    status='PARTIAL_OR_UNAVAILABLE';failed_phase='log_read';failure_code=exception_failure_code(exc)
                phase='parse';traces=structural_traces(raw)
                phase='after_inspect';after=app_scope(document(command(['container','inspect','--format',APP_SCOPE,cid],8192)),cid)
                need({k:v for k,v in scope.items()if k not in('RestartCount','OOMKilled')}=={k:v for k,v in after.items()if k not in('RestartCount','OOMKilled')},'app_scope_changed')
                result={'status':status,'reason':None,'failed_phase':failed_phase,'failure_code':failure_code,'subject':bound,'original_App_CID':cid,'before_observer_state':{k:observed[k]for k in('RestartCount','OOMKilled','Running','StartedAt')},'capture_state_before':{k:scope[k]for k in('RestartCount','OOMKilled')},'capture_state_after':{k:after[k]for k in('RestartCount','OOMKilled')},'client':client,'coverage':'last_2000_App_lines_only','complete_application_log':False,'structure':traces,'raw_log_retained':False,'security_accepted':False,'health_assessment':None,'original_scope_readers_closed':False}
            except Exception as exc:
                body_error=exc;body_phase=phase;raise
            finally:phase='finality'
        result['original_scope_readers_closed']=True
    except Exception as exc:
        result.update(status='UNAVAILABLE',reason='synthetic_scope_or_source_or_client_unavailable',failed_phase=body_phase if exc is body_error else phase,failure_code=exception_failure_code(exc))
    return result
def load(relative):
    path=ROOT/relative;pin=PINS[relative]
    need(path.resolve(strict=True)==path,'source_path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_uid==os.geteuid() and not before.st_mode&0o022 and before.st_size==pin[0]<=65536,'source_metadata')
        raw=os.pread(fd,pin[0]+1,0)
        need((len(raw),sha256(raw).hexdigest())==pin and frame(before)==frame(os.fstat(fd))==frame(path.lstat()),'source_pin')
    finally:os.close(fd)
    module=ModuleType('_finite_backend_'+path.stem);module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__);return module
def hosted(env):
    return os.getuid()==os.geteuid()==1001 and all(env.get(k)==v for k,v in {
      'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted','RUNNER_OS':'Linux',
      'GITHUB_REPOSITORY':'mohsenbarari/trading_bot','GITHUB_REPOSITORY_ID':'1065680631',
      'GITHUB_EVENT_NAME':'workflow_dispatch','GITHUB_WORKFLOW':'Isolated npm security backport qualification',
      'GITHUB_WORKSPACE':str(ROOT)}.items())
def subject(args,env):
    need(re.fullmatch('[a-f0-9]{40}',args.head) and args.browser in {'chromium','firefox','webkit'} and type(args.shard)is int and 1<=args.shard<=4,'subject')
    run,attempt=env.get('GITHUB_RUN_ID',''),env.get('GITHUB_RUN_ATTEMPT','')
    need(re.fullmatch('[1-9][0-9]{0,19}',run) and re.fullmatch('[1-9][0-9]{0,2}',attempt),'run_subject')
    need(args.outcome in ({'pending'}if args.phase=='before'else{'success','failure','cancelled','skipped'}),'gate_outcome')
    return {'head':args.head,'run_id':int(run),'run_attempt':int(attempt),'browser':args.browser,'shard':args.shard,'phase':args.phase,'original_gate_outcome':args.outcome}
def inspect_row(value,name):
    need(type(value)is dict and set(value)=={'Name','Id','Running','Pid','ExitCode','OOMKilled','StartedAt','FinishedAt','RestartCount','Memory','NanoCpus','Health'},'inspect_shape')
    need(value['Name']=='/'+name and re.fullmatch('[a-f0-9]{64}',value['Id']) and all(type(value[k])is bool for k in ('Running','OOMKilled')),'inspect_subject')
    need(all(type(value[k])is int and 0<=value[k]<=2**63-1 for k in ('Pid','ExitCode','RestartCount','Memory','NanoCpus')),'inspect_integer')
    need(all(type(value[k])is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z',value[k]) for k in ('StartedAt','FinishedAt')) and value['Health']in {'none','starting','healthy','unhealthy'},'inspect_status')
    return value
def stats_rows(raw,document,rows):
    lines=raw.splitlines();need(len(lines)==3,'stats_count');result={}
    for line in lines:
        value=document(line);need(type(value)is dict and set(value)=={'Name','ID','CPUPerc','MemUsage','MemPerc','PIDs'},'stats_shape')
        name=value['Name'];need(type(name)is str and name in NAMES and name not in result and name in rows and value['ID']==rows[name]['Id'],'stats_subject')
        need(all(type(value[k])is str and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?%',value[k]) and len(value[k])<=32 for k in ('CPUPerc','MemPerc')),'stats_percentage')
        need(type(value['PIDs'])is str and re.fullmatch('[0-9]{1,12}',value['PIDs']) and type(value['MemUsage'])is str and len(value['MemUsage'])<=80 and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?[kKMGTPE]?i?B / [0-9]+(?:\.[0-9]+)?[kKMGTPE]?i?B',value['MemUsage']),'stats_usage')
        result[name]=value
    return result
def collect(run,document,env):
    rows={};errors=[];end=time.monotonic()+20
    codes={'command':'bounded_command_unavailable','json':'bounded_json_invalid','validation':'bounded_metadata_invalid'}
    def command(argv,maximum):
        raw=run(['/usr/bin/docker','--host=unix:///var/run/docker.sock',*argv],env=env,deadline=end,maximum=maximum)
        need(type(raw)is bytes and len(raw)<=maximum,'command_bound');return raw
    for name in NAMES:
        stage='command'
        try:
            raw=command(['container','inspect','--format',INSPECT,name],8192)
            stage='json';value=document(raw)
            stage='validation';rows[name]=inspect_row(value,name)
        except Exception:errors.append({'operation':'inspect','container':name,'stage':stage,'code':codes[stage]})
    stats={}
    stage='command'
    def stats_document(raw):
        nonlocal stage
        stage='json';value=document(raw);stage='validation';return value
    try:
        raw=command(['stats','--no-stream','--no-trunc','--format',STATS,*NAMES],16384)
        stage='validation';stats=stats_rows(raw,stats_document,rows)
    except Exception:errors.append({'operation':'stats','stage':stage,'code':codes[stage]})
    return {'status':'OBSERVED'if not errors else'PARTIAL_OR_UNAVAILABLE','states':rows,'stats':stats,'errors':errors}
def main(argv=None):
    if not hosted(os.environ):
        print('{"status":"UNARMED","Docker_called":false,"security_accepted":false}');return 0
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('phase',choices=('before','after'))
    parser.add_argument('--browser',required=True);parser.add_argument('--shard',type=int,required=True);parser.add_argument('--head',required=True);parser.add_argument('--outcome',default='pending')
    try:
        args=parser.parse_args(argv);bound=subject(args,os.environ)
        temporary=Path(os.environ['RUNNER_TEMP']);need(temporary.is_absolute() and temporary.resolve(strict=True)==temporary and temporary.is_dir(),'temporary_directory')
        output=temporary/'npm-browser-backend-diagnostics';output.mkdir(mode=0o700,exist_ok=True)
        need(output.resolve(strict=True)==output and output.stat().st_uid==os.geteuid() and not output.stat().st_mode&0o077,'output_directory')
        E=load('scripts/npm_security_ci/browser_evidence.py');M=load('deploy/maintenance/glibc_backport_native239/host_io_original.py')
        def document(raw):return json.loads(raw,object_pairs_hook=E.unique,parse_constant=lambda _:need(False,'nonfinite_JSON'))
        with tempfile.TemporaryDirectory(prefix='empty-docker-client-',dir=output)as client:
            client_env={'PATH':'/usr/bin:/bin','HOME':client,'DOCKER_CONFIG':client,'DOCKER_HOST':'unix:///var/run/docker.sock','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'}
            result=collect(M.run,document,client_env)
            if args.phase=='after'and args.outcome=='failure':
                try:
                    exception=collect_app_exception(M.run,document,client_env,bound,output/f'{args.browser}-{args.shard}-before.json')
                    exception.update(schema='npm_browser_public_App_exception_structure/1',diagnostic_is_test_or_security_acceptance=False)
                    destination=temporary/'npm-browser-backend-exceptions';destination.mkdir(mode=0o700,exist_ok=True)
                    need(destination.resolve(strict=True)==destination and destination.stat().st_uid==os.geteuid()and not destination.stat().st_mode&0o077,'exception_output_directory')
                    E.write_new(destination/f'{args.browser}-{args.shard}.json',exception)
                except Exception:
                    # A separate optional exception artifact must not erase the
                    # existing finite state receipt or the failed browser gate.
                    pass
        result.update(schema='npm_browser_public_backend_diagnostics/1',subject=bound,health_assessment=None,diagnostic_is_test_or_security_acceptance=False,security_accepted=False)
        E.write_new(output/f'{args.browser}-{args.shard}-{args.phase}.json',result)
        print(json.dumps({'status':result['status'],'diagnostic_artifact_written':True,'security_accepted':False}));return 0
    except Exception:
        # Diagnostic availability must not suppress or erase the original test
        # gate. Never expose raw Docker output or private exception payloads.
        print('{"status":"DIAGNOSTIC_UNAVAILABLE","diagnostic_artifact_written":false,"test_gate_unchanged":true,"security_accepted":false}');return 0
if __name__=='__main__':raise SystemExit(main())
