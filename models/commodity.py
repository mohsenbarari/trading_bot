# (فایل جدید) models/commodity.py
from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from .database import Base

class Commodity(Base):
    __tablename__ = "commodities"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False) # نام اصلی کالا (مثلا: سکه امامی)
    
    # تعریف رابطه یک-به-چند با نام‌های مستعار
    # cascade="all, delete-orphan": اگر کالا حذف شد، تمام نام‌های مستعارش هم حذف شوند
    aliases = relationship("CommodityAlias", back_populates="commodity", cascade="all, delete-orphan")

class CommodityAlias(Base):
    __tablename__ = "commodity_aliases"
    id = Column(Integer, primary_key=True, index=True)
    alias = Column(String, unique=True, index=True, nullable=False) # نام مستعار (مثلا: سکه طرح جدید)
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    
    # تعریف رابطه چند-به-یک با کالا
    commodity = relationship("Commodity", back_populates="aliases")