# alembic/env.py (نسخه نهایی و همزمان برای manage.py)
from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context
import os

# آبجکت Config که به تنظیمات alembic.ini دسترسی دارد
config = context.config

# این بخش ضروری نیست چون ما آدرس را در manage.py تنظیم می‌کنیم، اما بودنش ضرری ندارد
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# مدل‌های SQLAlchemy خود را برای پشتیبانی از autogenerate وارد کنید
from models.database import Base
import models.user
import models.invitation
target_metadata = Base.metadata

def run_migrations_online() -> None:
    """اجرای migration ها در حالت آنلاین به صورت همزمان."""
    # ایجاد یک engine با استفاده از تنظیماتی که manage.py به ما می‌دهد
    connectable = engine_from_config(
        config.get_section(config.main_section_name, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )
        with context.begin_transaction():
            context.run_migrations()

# ما فقط حالت آنلاین را نیاز داریم
run_migrations_online()