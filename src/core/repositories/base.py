# src/core/repositories/base.py
"""Repository Interfaces - Abstract"""

from typing import Protocol, TypeVar, Generic, Optional, List
from abc import abstractmethod

T = TypeVar('T')


class IRepository(Protocol[T]):
    """اینترفیس پایه Repository"""
    
    @abstractmethod
    async def get_by_id(self, id: int) -> Optional[T]:
        """دریافت با آیدی"""
        ...
    
    @abstractmethod
    async def get_all(self, limit: int = 100, offset: int = 0) -> List[T]:
        """دریافت همه"""
        ...
    
    @abstractmethod
    async def create(self, entity: T) -> T:
        """ایجاد"""
        ...
    
    @abstractmethod
    async def update(self, entity: T) -> T:
        """بروزرسانی"""
        ...
    
    @abstractmethod
    async def delete(self, id: int) -> bool:
        """حذف"""
        ...
