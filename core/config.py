# core/config.py
"""
تنظیمات اپلیکیشن از متغیرهای محیطی

این ماژول از pydantic-settings برای مدیریت تنظیمات استفاده می‌کند.
تمام مقادیر از فایل .env خوانده می‌شوند.
"""
from pydantic_settings import BaseSettings

__all__ = ["Settings", "settings"]

class Settings(BaseSettings):
    bot_token: str | None = None
    bot_username: str | None = None
    
    # Server Mode (iran vs foreign)
    server_mode: str = "foreign"
    # Logical authority remains compatible with server_mode. physical_site is
    # deployment identity and must not be reused as business/home authority.
    logical_authority: str | None = None
    physical_site: str | None = None
    topology_schema_version: str | None = None
    three_site_dr_enabled: bool = False
    dark_standby_mode: bool = False
    dr_event_protocol_enabled: bool = False
    dr_event_protocol_strict: bool = False
    dr_producer_epoch: int = 1
    dr_sync_pairwise_keys_json: str | None = None
    dr_sync_peer_urls_json: str | None = None
    dr_sync_request_max_age_seconds: int = 30
    dr_sync_http_timeout_seconds: float = 5.0
    dr_sync_verify_tls: bool = True
    dr_sync_ca_bundle: str | None = None
    dr_projection_database_url: str | None = None
    dr_control_database_url: str | None = None
    dr_auxiliary_db_pool_size: int = 3
    dr_auxiliary_db_max_overflow: int = 2
    dr_delivery_batch_size: int = 100
    dr_delivery_claim_seconds: int = 30
    dr_delivery_poll_seconds: float = 0.25
    dr_blob_root: str = "uploads/blobs"
    dr_blob_object_prefix: str = "three-site-dr/blobs/sha256"
    dr_blob_object_endpoint: str | None = None
    dr_blob_object_region: str | None = None
    dr_blob_object_bucket: str | None = None
    dr_blob_s3_credentials_file: str | None = None
    dr_blob_encryption_keyring_file: str | None = None
    dr_blob_require_versioning: bool = True
    dr_blob_gc_retention_days: int = 30
    dr_blob_orphan_grace_seconds: int = 3600
    dr_blob_orphan_quarantine_days: int = 30
    dr_blob_orphan_scan_max_entries: int = 10000
    dr_blob_local_quota_bytes: int = 20 * 1024 * 1024 * 1024
    dr_audit_anchor_object_prefix: str = "three-site-dr/audit-anchors"
    # Safe default until the owner approves RPO and a synchronous same-region
    # journal has independently passed restoration drills.
    dr_isolated_critical_write_policy: str = "freeze"
    dr_effect_worker_enabled: bool = False
    dr_effect_claim_seconds: int = 30
    dr_effect_min_lease_remaining_seconds: int = 10
    dr_effect_poll_seconds: float = 0.25
    origin_readiness_api_key: str | None = None
    origin_expected_migration_revision: str | None = None
    origin_readiness_max_evidence_age_seconds: int = 900
    origin_readiness_require_recovery_manifest: bool = True
    # Disabled until the dedicated Iran-reachable witness service and drills exist.
    writer_witness_required: bool = False
    writer_witness_public_key: str | None = None
    writer_witness_private_key_file: str | None = None
    writer_witness_lease_duration_seconds: int = 180
    writer_witness_renew_interval_seconds: int = 30
    writer_witness_safety_margin_seconds: int = 15
    writer_witness_max_clock_skew_seconds: int = 5
    writer_witness_boot_id_file: str = "/proc/sys/kernel/random/boot_id"
    writer_witness_authoritative_site: str = "webapp_ir"
    # Dedicated witness service and renewal transport. All remain disabled and
    # unset until the three-site rollout gate is explicitly approved.
    writer_witness_service_enabled: bool = False
    writer_witness_database_url: str | None = None
    writer_witness_product_database_user: str | None = None
    writer_witness_require_distinct_database_identity: bool = True
    writer_witness_service_webapp_fi_key_id: str | None = None
    writer_witness_service_webapp_fi_secret: str | None = None
    writer_witness_service_webapp_ir_key_id: str | None = None
    writer_witness_service_webapp_ir_secret: str | None = None
    writer_witness_internal_url: str | None = None
    writer_witness_client_key_id: str | None = None
    writer_witness_client_secret: str | None = None
    writer_witness_auth_max_age_seconds: int = 15
    writer_witness_http_timeout_seconds: float = 3.0
    writer_witness_verify_tls: bool = True
    writer_witness_ca_bundle: str | None = None
    writer_witness_auto_renew_enabled: bool = False
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
    foreign_server_url: str | None = None
    public_webapp_url: str | None = None
    sync_api_key: str | None = None
    sync_direct_push_cooldown_seconds: float = 90.0
    sync_verify_tls: bool = True
    sync_ca_bundle: str | None = None
    sync_parity_status_max_age_seconds: int = 900
    sync_watermark_strict_mode: bool = False
    environment: str = "production"
    release_sha: str | None = None
    log_level: str = "INFO"
    log_format: str = "json"
    error_tracking_dsn: str | None = None
    error_tracking_sample_rate: float = 1.0
    error_tracking_rate_limit_window_seconds: int = 60
    error_tracking_max_events_per_fingerprint: int = 10
    error_tracking_rate_limit_max_fingerprints: int = 2048
    observability_api_key: str | None = None
    trading_bot_service: str = "app"
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
    telegram_notification_outbox_worker_interval_seconds: float = 1.0
    telegram_notification_outbox_worker_batch_limit: int = 50
    telegram_notification_outbox_worker_lease_seconds: int = 30
    telegram_notification_outbox_worker_recover_limit: int = 100
    telegram_notification_outbox_worker_max_sends_per_second: float = 10.0
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
    
    class Config:
        env_file = ".env"

settings = Settings()
