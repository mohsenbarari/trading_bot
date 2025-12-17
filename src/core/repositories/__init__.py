# src/core/repositories/__init__.py
from .base import IRepository
from .user_repository import IUserRepository
from .offer_repository import IOfferRepository

__all__ = [
    "IRepository",
    "IUserRepository",
    "IOfferRepository",
]
