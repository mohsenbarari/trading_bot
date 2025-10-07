
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    database_url: str
    sync_database_url: str  
    postgres_db: str
    postgres_user: str
    postgres_password: str
    
    # JWT settings
    jwt_secret_key: str = "a_very_secret_key" # In production, use a strong, random key
    jwt_algorithm: str = "HS256"

    class Config:
        env_file = ".env"

settings = Settings()