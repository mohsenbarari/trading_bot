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
    audit_trail_path: str | None = None
    trusted_proxy_cidrs: str = "127.0.0.1/32,::1/128"
    observability_telegram_user_hash_salt: str | None = None
    grafana_alert_default_receiver: str | None = None
    grafana_alert_critical_receiver: str | None = None
    grafana_alert_warning_receiver: str | None = None
    grafana_alert_webhook_url: str | None = None
    grafana_alert_email_addresses: str | None = None

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
