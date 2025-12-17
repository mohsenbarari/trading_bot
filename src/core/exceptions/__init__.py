# src/core/exceptions/__init__.py
from .base import (
    DomainException, 
    ValidationError, 
    NotFoundError, 
    UnauthorizedError, 
    ConflictError
)
from .user_exceptions import (
    UserNotFoundError,
    UserAlreadyExistsError,
    UserRestrictedError,
    MaxActiveOffersError
)

__all__ = [
    "DomainException",
    "ValidationError",
    "NotFoundError",
    "UnauthorizedError",
    "ConflictError",
    "UserNotFoundError",
    "UserAlreadyExistsError",
    "UserRestrictedError",
    "MaxActiveOffersError",
]
