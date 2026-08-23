# core/config.py
"""
تنظیمات اپلیکیشن از متغیرهای محیطی

این ماژول از pydantic-settings برای مدیریت تنظیمات استفاده می‌کند.
تمام مقادیر از فایل .env خوانده می‌شوند.
"""
import math
import os

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings

__all__ = ["Settings", "settings"]

class Settings(BaseSettings):
    bot_token: str | None = None
    bot_username: str | None = None
    
    # Server Mode (iran vs foreign)
    server_mode: str = "foreign"
    peer_server_url: str | None = None
    iran_server_url: str | None = None
    germany_server_url: str | None = None
    foreign_server_domain: str | None = None
    iran_server_domain: str | None = None
    foreign_server_aliases: str | None = None
    iran_server_aliases: str | None = None
    extra_cors_origins: str | None = None
    sms_public_host: str | None = None
    trade_forward_timeout_seconds: float = 3.0
    trade_forward_grace_seconds: int = 3
    trade_forward_verify_tls: bool = False
    trade_forward_ca_bundle: str | None = None
    trade_contention_gate_ttl_seconds: float = 2.5
    trade_contention_gate_max_inflight: int = 3
    offer_expiry_command_receipts_enabled: bool = False
    foreign_server_url: str | None = None
    public_webapp_url: str | None = None
    sync_api_key: str | None = None
    sync_direct_push_cooldown_seconds: float = 90.0
    # Active offers created on the Iran WebApp must reach the foreign Telegram
    # publisher before their short market lifetime is consumed by unrelated
    # replication work.  This is a committed-outbox acceleration only: the
    # normal sync worker remains the durable reconciliation path.
    offer_priority_sync_enabled: bool = True
    offer_priority_sync_timeout_seconds: float = 2.0
    # Recovery remains the regular change-log worker.  This bounded fast lane
    # is deliberately for just-committed market state, never historical
    # backlog replay after a deploy or outage.
    offer_priority_sync_max_change_age_seconds: float = 45.0
    sync_verify_tls: bool = True
    sync_ca_bundle: str | None = None
    sync_parity_status_max_age_seconds: int = 900
    sync_watermark_strict_mode: bool = False
    environment: str = "production"
    release_sha: str | None = None
    # Product inference stays opt-in until its local atomic Snapshot publisher
    # and replay gates are deployed. This flag never starts a collector.
    coin_intelligence_inference_preview_enabled: bool = False
    # Separate from the passive preview switch.  This authorizes an inferred
    # commodity to enter a real offer only after final-submit revalidation.
    coin_intelligence_inference_selection_enabled: bool = False
    # Even when selection is enabled for a constrained staging rollout, keep
    # a unique model result as an explicit user confirmation until an owner
    # promotes eligible cells.  This must never default to automatic choice.
    coin_intelligence_inference_auto_selection_enabled: bool = False
    coin_intelligence_inference_snapshot_path: str | None = None
    # Hard guard for only clearly outlying coin-offer prices.  It consumes the
    # same atomic estimator Snapshot but is independent from commodity and
    # condition-model selection.  Missing/stale evidence always fails open.
    offer_model_price_guard_enabled: bool = False
    offer_model_price_guard_max_snapshot_age_seconds: int = 120
    log_level: str = "INFO"
    log_format: str = "json"
    error_tracking_dsn: str | None = None
    error_tracking_sample_rate: float = 1.0
    error_tracking_rate_limit_window_seconds: int = 60
    error_tracking_max_events_per_fingerprint: int = 10
    error_tracking_rate_limit_max_fingerprints: int = 2048
    observability_api_key: str | None = None
    trading_bot_service: str = "app"
    # Split Telegram execution: `all` keeps one process; `primary` and
    # `publishers` own disjoint lanes. Unknown values refuse startup.
    telegram_bot_runtime_role: str = "all"
    trading_bot_metrics_backend: str = "memory"
    audit_trail_path: str | None = None
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128"
    observability_telegram_user_hash_salt: str | None = None
    grafana_alert_default_receiver: str | None = None
    grafana_alert_critical_receiver: str | None = None
    grafana_alert_warning_receiver: str | None = None
    grafana_alert_webhook_url: str | None = None
    grafana_alert_email_addresses: str | None = None
    api_workers: int = 2
    db_pool_size: int = 15
    db_max_overflow: int = 10
    db_pool_recycle_seconds: int = 3600
    db_pool_pre_ping: bool = True
    postgres_max_connections: int = 500
    postgres_shared_buffers: str = "128MB"
    postgres_effective_cache_size: str = "4GB"
    postgres_work_mem: str = "4MB"
    postgres_maintenance_work_mem: str = "64MB"
    postgres_random_page_cost: float = 4.0
    postgres_effective_io_concurrency: int = 1
    postgres_checkpoint_timeout: str = "5min"
    postgres_max_wal_size: str = "1GB"
    postgres_min_wal_size: str = "80MB"
    postgres_wal_buffers: str = "4MB"
    background_leader_lock_ttl_seconds: int = 90
    background_leader_lock_refresh_seconds: int = 30
    background_leader_retry_seconds: int = 10
    background_jobs_enabled: bool = True
    trade_delivery_worker_interval_seconds: float = 1.0
    trade_delivery_worker_batch_limit: int = 50
    trade_delivery_worker_lease_seconds: int = 30
    trade_delivery_worker_recover_limit: int = 100
    offer_publication_worker_interval_seconds: float = 1.0
    offer_publication_worker_batch_limit: int = 25
    offer_publication_worker_channel_edit_spacing_seconds: float = 0.35
    offer_publication_worker_channel_send_spacing_seconds: float = 0.35
    offer_publication_worker_rate_limit_cooldown_seconds: float = 10.0
    offer_publication_worker_max_rate_limit_cooldown_seconds: float = 120.0
    offer_publication_worker_retry_base_seconds: float = 5.0
    offer_publication_worker_retry_max_seconds: float = 300.0
    telegram_notification_outbox_worker_interval_seconds: float = 1.0
    telegram_notification_outbox_worker_batch_limit: int = 50
    telegram_notification_outbox_worker_lease_seconds: int = 30
    telegram_notification_outbox_worker_recover_limit: int = 100
    telegram_notification_outbox_worker_max_sends_per_second: float = 10.0
    # Shared Telegram queue rollout controls. Defaults preserve legacy ownership.
    # Producers (API/Bot business paths) only need the non-secret ownership mode.
    # Executors additionally require the worker/cutover controls and credentials
    # below.  None preserves the legacy single-runtime compatibility contract by
    # inheriting telegram_delivery_execution_owner.
    telegram_delivery_producer_mode: str | None = None
    # Non-secret cross-service attestation. API/sync processes do not receive
    # executor controls, but they must still prove that their producer contract
    # matches the operator-selected global owner.
    telegram_delivery_expected_execution_owner: str | None = None
    telegram_delivery_execution_owner: str = "legacy"
    telegram_delivery_queue_worker_enabled: bool = False
    telegram_delivery_queue_cutover_ready: bool = False
    telegram_provider_test_authority: bool = False
    telegram_otp_queue_secret: str | None = None
    telegram_delivery_queue_channel_editor_enabled: bool = False
    # Multi-publisher delivery is intentionally disabled until every staged
    # migration and staging acceptance gate has passed.  B2B dispatch is a
    # stricter sub-feature: enabling it without the parent flag is a startup
    # error rather than a partially active configuration.
    telegram_multi_publisher_enabled: bool = False
    telegram_b2b_dispatch_enabled: bool = False
    telegram_b2b_dispatch_interval_seconds: float = 0.5
    telegram_b2b_dispatch_batch_size: int = 8
    telegram_b2b_acknowledgement_timeout_seconds: float = 15.0
    # Publisher credentials remain separate to make accidental token reuse and
    # identity drift fail before a worker can be composed.  They are consumed
    # only when TELEGRAM_MULTI_PUBLISHER_ENABLED is true.
    telegram_publisher_1_enabled: bool = False
    telegram_publisher_1_bot_token: SecretStr | None = None
    telegram_publisher_1_expected_bot_id: int | None = None
    telegram_publisher_1_expected_username: str | None = None
    telegram_publisher_2_enabled: bool = False
    telegram_publisher_2_bot_token: SecretStr | None = None
    telegram_publisher_2_expected_bot_id: int | None = None
    telegram_publisher_2_expected_username: str | None = None
    telegram_publisher_3_enabled: bool = False
    telegram_publisher_3_bot_token: SecretStr | None = None
    telegram_publisher_3_expected_bot_id: int | None = None
    telegram_publisher_3_expected_username: str | None = None
    telegram_publisher_4_enabled: bool = False
    telegram_publisher_4_bot_token: SecretStr | None = None
    telegram_publisher_4_expected_bot_id: int | None = None
    telegram_publisher_4_expected_username: str | None = None
    telegram_publisher_5_enabled: bool = False
    telegram_publisher_5_bot_token: SecretStr | None = None
    telegram_publisher_5_expected_bot_id: int | None = None
    telegram_publisher_5_expected_username: str | None = None
    telegram_multi_publisher_lane_concurrency: int = 1
    telegram_delivery_queue_channel_editor_bot_token: SecretStr | None = None
    telegram_delivery_queue_expected_primary_bot_id: int | None = None
    telegram_delivery_queue_expected_channel_editor_bot_id: int | None = None
    telegram_delivery_queue_expected_channel_id: int | None = None
    # Retained as a diagnostic migration flag only. Publishers also own B2B and
    # callback ingress, so enabling this flag is a production-cutover blocker;
    # staging and production must use distinct central/publisher identities.
    telegram_delivery_queue_shared_publisher_fleet_enabled: bool = False
    telegram_delivery_queue_preflight_timeout_seconds: float = 10.0
    telegram_delivery_queue_worker_interval_seconds: float = 1.0
    # PostgreSQL commit notifications wake local producers and execution lanes
    # immediately. Lane values are bounded fallbacks for a lost hint or
    # listener outage. The notification feeder keeps its short fallback because
    # synced rows can be applied by the peer receiver without a local hint.
    telegram_delivery_queue_primary_idle_poll_interval_seconds: float = 1.0
    telegram_delivery_queue_publisher_idle_poll_interval_seconds: float = 1.0
    telegram_notification_outbox_queue_feeder_interval_seconds: float = 0.2
    telegram_trade_result_queue_feeder_interval_seconds: float = 0.2
    telegram_delivery_queue_worker_batch_limit: int = 25
    telegram_delivery_queue_primary_concurrency: int = 4
    telegram_delivery_queue_primary_m0_reserved_concurrency: int = 1
    telegram_delivery_queue_channel_editor_concurrency: int = 1
    telegram_delivery_queue_worker_request_timeout_seconds: float = 10.0
    telegram_delivery_queue_worker_lease_seconds: float = 30.0
    telegram_delivery_queue_worker_recover_limit: int = 100
    telegram_offer_queue_feeder_batch_limit: int = 25
    telegram_offer_queue_feeder_interval_seconds: float = 0.5
    telegram_delivery_queue_retry_after_safety_seconds: float = 0.1
    telegram_delivery_queue_retry_base_seconds: float = 1.0
    telegram_delivery_queue_retry_max_seconds: float = 300.0
    telegram_delivery_queue_retry_jitter_ratio: float = 0.2
    telegram_delivery_queue_bot_min_interval_seconds: float = 0.035
    telegram_delivery_queue_destination_min_interval_seconds: float = 1.05
    telegram_delivery_queue_rate_limit_probe_delay_seconds: float = 0.1
    telegram_delivery_queue_global_rate_limit_window_seconds: float = 2.0
    telegram_delivery_queue_limiter_key_ttl_seconds: int = 86400
    telegram_direct_registration_enabled: bool = False
    telegram_registration_reconciliation_enabled: bool = False
    telegram_login_otp_enabled: bool = False
    otp_sms_auto_fallback_enabled: bool = False
    otp_sms_auto_fallback_seconds: int = 40
    otp_ttl_seconds: int = 120
    otp_delivery_state_secret: str | None = None
    telegram_registration_post_expiry_grace_seconds: int = 86400
    telegram_registration_job_batch_size: int = 10
    telegram_registration_job_concurrency: int = 1
    otp_sms_fallback_job_concurrency: int = 4
    invitation_sms_standard_enabled: bool = False
    invitation_sms_customer_tier1_enabled: bool = False
    invitation_sms_accountant_enabled: bool = True
    invitation_sms_customer_tier2_enabled: bool = True
    invitation_contract_v2_enabled: bool = False
    registration_sync_v2_enabled: bool = False
    registration_sync_accept_unversioned: bool = True
    invitation_public_rate_limit_per_minute: int = 30

    database_url: str
    sync_database_url: str
    postgres_db: str
    postgres_user: str
    postgres_password: str
    frontend_url: str  
    redis_url: str
    redis_host: str = "redis"  # Default Docker service name
    redis_port: int = 6379
    redis_appendonly: str = "yes"
    redis_appendfsync: str = "everysec"
    redis_maxmemory: str = "0"
    redis_maxmemory_policy: str = "noeviction"
    channel_id: int | None = None  # آیدی کانال برای ارسال پیام
    channel_invite_link: str | None = None  # لینک دعوت کانال

    jwt_secret_key: str  # ❗ اجباری - باید در .env تعریف شود (JWT_SECRET_KEY)
    jwt_algorithm: str = "HS256"
    dev_api_key: str | None = None
    # Deprecated compatibility field. Invitation creation must use core.trading_settings.
    invitation_expiry_days: int = 1

    # Web Push Notifications
    web_push_enabled: bool = True
    web_push_vapid_public_key: str | None = None
    web_push_vapid_private_key: str | None = None
    web_push_vapid_subject: str | None = None
    web_push_ttl_seconds: int = 3600
    web_push_timeout_seconds: float = 5.0
    sync_signal_redis_timeout_seconds: float = 0.25

    # SMS.ir Service
    smsir_api_key: str | None = None
    smsir_line_number: int | None = None
    smsir_base_url: str = "https://api.sms.ir"
    smsir_timeout_seconds: float = 10.0
    smsir_otp_template_id: str | None = "585147"
    smsir_otp_template_parameter: str = "CODE"
    smsir_invitation_template_id: str | None = "657938"
    smsir_invitation_template_parameter: str = "NAME"
    smsir_accountant_invitation_template_id: str | None = "162103"
    smsir_customer_invitation_template_id: str | None = "903643"
    invitation_registration_session_ttl_seconds: int = 600
    staging_log_otp_codes: bool = False

    @field_validator("smsir_line_number", mode="before")
    @classmethod
    def _blank_smsir_line_number_is_unset(cls, value):
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_telegram_delivery_queue_settings(self):
        if (
            self.telegram_b2b_dispatch_enabled
            and not self.telegram_multi_publisher_enabled
        ):
            raise ValueError("telegram_b2b_dispatch_requires_multi_publisher")
        for name in (
            "telegram_b2b_dispatch_interval_seconds",
            "telegram_b2b_acknowledgement_timeout_seconds",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name}_must_be_positive")
        actual_owner = str(self.telegram_delivery_execution_owner or "").strip().lower()
        producer = str(self.telegram_delivery_producer_mode or "").strip().lower()
        expected_owner = str(
            self.telegram_delivery_expected_execution_owner or ""
        ).strip().lower()
        if not producer:
            producer = "queue-v1" if actual_owner == "producer-only" else actual_owner
        if not expected_owner:
            expected_owner = (
                "queue-v1" if actual_owner == "producer-only" else actual_owner
            )
        if actual_owner == "producer-only":
            if not str(self.telegram_delivery_producer_mode or "").strip():
                self.telegram_delivery_producer_mode = producer
            if not str(self.telegram_delivery_expected_execution_owner or "").strip():
                self.telegram_delivery_expected_execution_owner = expected_owner
        if producer not in {"legacy", "queue-v1"}:
            raise ValueError("telegram_delivery_producer_mode_invalid")
        if expected_owner not in {"legacy", "queue-v1"}:
            raise ValueError("telegram_delivery_expected_execution_owner_invalid")
        if actual_owner not in {"legacy", "queue-v1", "producer-only"}:
            raise ValueError("telegram_delivery_execution_owner_invalid")
        if producer != expected_owner:
            raise ValueError("telegram_delivery_producer_executor_split_brain")
        if self.trading_bot_service == "bot" and actual_owner != expected_owner:
            raise ValueError("telegram_delivery_bot_executor_split_brain")
        if actual_owner == "producer-only":
            if self.trading_bot_service == "bot":
                raise ValueError("telegram_delivery_bot_cannot_be_producer_only")
            if self.telegram_delivery_queue_worker_enabled or self.telegram_delivery_queue_cutover_ready:
                raise ValueError("telegram_delivery_producer_only_rejects_workers")
        if bool(self.telegram_provider_test_authority) and str(
            self.trading_bot_service or ""
        ).strip().lower() in {"api", "bot", "sync_worker", "load_runner", "webapp", "migration"}:
            raise ValueError("telegram_provider_test_authority_forbidden_on_deployable_service")
        runtime_role = str(self.telegram_bot_runtime_role or "").strip().lower()
        if runtime_role not in {"all", "primary", "publishers"}:
            raise ValueError("telegram_bot_runtime_role_unknown")
        self.telegram_bot_runtime_role = runtime_role
        positive_float_fields = (
            "telegram_delivery_queue_preflight_timeout_seconds",
            "telegram_delivery_queue_worker_interval_seconds",
            "telegram_delivery_queue_primary_idle_poll_interval_seconds",
            "telegram_delivery_queue_publisher_idle_poll_interval_seconds",
            "telegram_notification_outbox_queue_feeder_interval_seconds",
            "telegram_trade_result_queue_feeder_interval_seconds",
            "telegram_delivery_queue_worker_request_timeout_seconds",
            "telegram_delivery_queue_worker_lease_seconds",
            "telegram_offer_queue_feeder_interval_seconds",
            "telegram_delivery_queue_retry_base_seconds",
            "telegram_delivery_queue_retry_max_seconds",
            "telegram_delivery_queue_bot_min_interval_seconds",
            "telegram_delivery_queue_destination_min_interval_seconds",
            "telegram_delivery_queue_rate_limit_probe_delay_seconds",
            "telegram_delivery_queue_global_rate_limit_window_seconds",
        )
        for name in positive_float_fields:
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name}_must_be_finite_positive")
        if (
            self.telegram_delivery_queue_shared_publisher_fleet_enabled
            and self.telegram_delivery_queue_destination_min_interval_seconds < 1.05
        ):
            raise ValueError(
                "telegram_shared_publisher_destination_interval_must_be_at_least_1_05"
            )
        safety = float(self.telegram_delivery_queue_retry_after_safety_seconds)
        if not math.isfinite(safety) or safety < 0:
            raise ValueError(
                "telegram_delivery_queue_retry_after_safety_seconds_invalid"
            )
        jitter = float(self.telegram_delivery_queue_retry_jitter_ratio)
        if not math.isfinite(jitter) or jitter < 0 or jitter > 1:
            raise ValueError("telegram_delivery_queue_retry_jitter_ratio_invalid")
        if (
            self.telegram_delivery_queue_retry_base_seconds
            > self.telegram_delivery_queue_retry_max_seconds
        ):
            raise ValueError("telegram_delivery_queue_retry_base_exceeds_max")
        if (
            self.telegram_delivery_queue_worker_lease_seconds
            < self.telegram_delivery_queue_worker_request_timeout_seconds + 15.0
        ):
            raise ValueError("telegram_delivery_queue_lease_too_short")
        for name in (
            "telegram_delivery_queue_worker_batch_limit",
            "telegram_delivery_queue_worker_recover_limit",
            "telegram_delivery_queue_primary_concurrency",
            "telegram_delivery_queue_primary_m0_reserved_concurrency",
            "telegram_delivery_queue_channel_editor_concurrency",
            "telegram_multi_publisher_lane_concurrency",
            "telegram_offer_queue_feeder_batch_limit",
            "telegram_delivery_queue_limiter_key_ttl_seconds",
            "telegram_b2b_dispatch_batch_size",
        ):
            if isinstance(getattr(self, name), bool) or int(getattr(self, name)) <= 0:
                raise ValueError(f"{name}_must_be_positive")
        if (
            self.telegram_delivery_queue_primary_m0_reserved_concurrency
            >= self.telegram_delivery_queue_primary_concurrency
        ):
            raise ValueError(
                "telegram_delivery_queue_primary_m0_reservation_must_leave_general_capacity"
            )
        return self
    
    class Config:
        # Defaults to the deployment file. The unit-test target points this at
        # ``config/unit-test.env.example`` so tests read code defaults instead
        # of whatever a developer machine happens to have configured.
        env_file = os.getenv("APP_ENV_FILE", ".env")

settings = Settings()
