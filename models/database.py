from sqlalchemy.orm import declarative_base

Base = declarative_base()

def get_db():
    from core.db import get_db as _get_db
    return _get_db()