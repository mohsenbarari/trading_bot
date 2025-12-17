# src/core/exceptions/user_exceptions.py
"""استثناهای مربوط به کاربر"""

from .base import DomainException, NotFoundError


class UserNotFoundError(NotFoundError):
    """کاربر یافت نشد"""
    def __init__(self, user_id: int):
        super().__init__("کاربر", user_id)


class UserAlreadyExistsError(DomainException):
    """کاربر از قبل وجود دارد"""
    def __init__(self, identifier: str):
        super().__init__(f"کاربر با {identifier} از قبل وجود دارد", "USER_EXISTS")


class UserRestrictedError(DomainException):
    """کاربر محدود شده"""
    def __init__(self, until: str):
        super().__init__(f"دسترسی شما تا {until} محدود شده است", "USER_RESTRICTED")


class MaxActiveOffersError(DomainException):
    """حداکثر لفظ‌های فعال"""
    def __init__(self, max_count: int):
        super().__init__(f"شما حداکثر {max_count} لفظ فعال دارید", "MAX_OFFERS")
