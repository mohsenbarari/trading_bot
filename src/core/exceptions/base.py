# src/core/exceptions/base.py
"""استثناهای دامنه"""


class DomainException(Exception):
    """استثنای پایه دامنه"""
    def __init__(self, message: str, code: str = "DOMAIN_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class ValidationError(DomainException):
    """خطای اعتبارسنجی"""
    def __init__(self, message: str):
        super().__init__(message, "VALIDATION_ERROR")


class NotFoundError(DomainException):
    """خطای یافت نشدن"""
    def __init__(self, entity: str, id: int):
        super().__init__(f"{entity} با آیدی {id} یافت نشد", "NOT_FOUND")


class UnauthorizedError(DomainException):
    """خطای عدم دسترسی"""
    def __init__(self, message: str = "دسترسی غیرمجاز"):
        super().__init__(message, "UNAUTHORIZED")


class ConflictError(DomainException):
    """خطای تداخل"""
    def __init__(self, message: str):
        super().__init__(message, "CONFLICT")
