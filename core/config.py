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
    foreign_server_url: str | None = None
    sync_api_key: str | None = None
    sync_direct_push_cooldown_seconds: float = 90.0
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

    database_url: str
    sync_database_url: str
    postgres_db: str
    postgres_user: str
    postgres_password: str
    frontend_url: str  
    redis_url: str
    redis_host: str = "redis"  # Default Docker service name
    redis_port: int = 6379
    channel_id: int | None = None  # آیدی کانال برای ارسال پیام
    channel_invite_link: str | None = None  # لینک دعوت کانال

    jwt_secret_key: str  # ❗ اجباری - باید در .env تعریف شود (JWT_SECRET_KEY)
    jwt_algorithm: str = "HS256"
    dev_api_key: str | None = None
    invitation_expiry_days: int = 1

    # SMS.ir Service
    smsir_api_key: str | None = None
    smsir_line_number: int | None = None
    
    class Config:
        env_file = ".env"

settings = Settings()
