# trading_bot/migrations/env.py (نسخه نهایی با خواندن env var)
import os # <--- اضافه شد
from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context

# --- مدل‌هایتان را import کنید ---
from models.database import Base
import models.user
import models.invitation
import models.session
import models.commodity
import models.offer  # مدل لفظ
import models.trade  # مدل معاملات
import models.message  # مدل پیام چت
import models.conversation  # مدل مکالمات چت

# --- خواندن تنظیمات Alembic ---
config = context.config

# --- خواندن متغیر محیطی دیتابیس ---
# این خط را اضافه کنید تا مطمئن شویم URL از env خوانده می‌شود
db_url = os.environ.get("SYNC_DATABASE_URL")
if not db_url:
    raise ValueError("SYNC_DATABASE_URL environment variable not set.")

# --- تنظیم sqlalchemy.url به صورت برنامه‌نویسی شده ---
# این خط را اضافه کنید
config.set_main_option("sqlalchemy.url", db_url)

# --- بقیه تنظیمات ---
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    # url = config.get_main_option("sqlalchemy.url") # دیگر نیازی به خواندن از اینجا نیست
    context.configure(
        url=db_url, # <-- از متغیر خوانده شده استفاده کنید
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # --- engine_from_config حالا sqlalchemy.url را خواهد داشت ---
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # url=db_url # می‌توانید مستقیماً هم پاس دهید، اما set_main_option بهتر است
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()