from datetime import timedelta
import hmac
from typing import Optional, Union, Any
import bcrypt
from jose import jwt
from core.config import settings
from core.utils import utc_now_naive

# JWT configuration
SECRET_KEY = settings.jwt_secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30


def constant_time_secret_equals(supplied: str | None, expected: str | None) -> bool:
    """Compare configured secrets without leaking prefix match timing."""
    if not supplied or not expected:
        return False
    return hmac.compare_digest(str(supplied), str(expected))

def create_access_token(subject: Union[int, str, Any] = None, data: dict = None, expires_delta: Optional[timedelta] = None, session_id: str = None, server_id: str = None) -> str:
    to_encode = data.copy() if data else {}
    if subject is not None:
        to_encode["sub"] = str(subject)
    if session_id is not None:
        to_encode["sid"] = session_id
    if server_id is not None:
        to_encode["srv"] = server_id
    if expires_delta:
        expire = utc_now_naive() + expires_delta
    else:
        expire = utc_now_naive() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "type": "access"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_refresh_token(subject: Union[int, str, Any] = None, data: dict = None, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy() if data else {}
    if subject is not None:
        to_encode["sub"] = str(subject)
    if expires_delta:
        expire = utc_now_naive() + expires_delta
    else:
        expire = utc_now_naive() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire, "type": "refresh"})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not plain_password or not hashed_password:
        return False
    # Encode and truncate to 72 bytes to satisfy bcrypt limits
    password_bytes = plain_password.encode('utf-8')
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    try:
        return bcrypt.checkpw(password_bytes, hashed_password.encode('utf-8'))
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    if not password:
        return ""
    # Encode and truncate to 72 bytes for bcrypt
    password_bytes = password.encode('utf-8')
    if len(password_bytes) > 72:
        password_bytes = password_bytes[:72]
    # Generate salt and hash
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode('utf-8')
