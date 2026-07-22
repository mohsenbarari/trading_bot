# models/offer.py
"""مدل لفظ (درخواست خرید/فروش در کانال)"""
from sqlalchemy import Column, Integer, String, BigInteger, Enum, DateTime, ForeignKey, Boolean, JSON, CheckConstraint, Index
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from .database import Base
from core.enums import SettlementType
from core.offer_identity import generate_offer_public_id
import enum


class OfferType(str, enum.Enum):
    BUY = "buy"    # خرید
    SELL = "sell"  # فروش


class OfferStatus(str, enum.Enum):
    ACTIVE = "active"      # فعال - در انتظار معامله
    COMPLETED = "completed" # تکمیل شده - معامله انجام شد
    CANCELLED = "cancelled" # لغو شده
    EXPIRED = "expired"    # منقضی شده


class Offer(Base):
    """لفظ - درخواست خرید/فروش در کانال"""
    __tablename__ = "offers"
    
    # ===== Database Constraints & Indexes =====
    __table_args__ = (
        CheckConstraint('quantity > 0', name='ck_offers_quantity_positive'),
        CheckConstraint('price > 0', name='ck_offers_price_positive'),
        CheckConstraint('remaining_quantity >= 0', name='ck_offers_remaining_nonnegative'),
        # ایندکس ترکیبی برای کوئری‌های رایج: WHERE status='active' AND commodity_id=X
        Index('ix_offers_status_commodity', 'status', 'commodity_id'),
    )
    
    id = Column(Integer, primary_key=True, index=True)
    offer_public_id = Column(String(40), nullable=False, unique=True, index=True, default=generate_offer_public_id)
    
    # ===== Optimistic Locking =====
    # این ستون برای جلوگیری از Lost Update در درخواست‌های همزمان استفاده می‌شود
    version_id = Column(Integer, nullable=False, default=1)
    
    # کاربر لفظ‌دهنده - nullable برای حفظ رکورد پس از حذف کاربر
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user = relationship("User", foreign_keys=[user_id])
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_user = relationship("User", foreign_keys=[actor_user_id])
    home_server = Column(String(16), nullable=False, default="foreign", index=True)
    
    # نوع لفظ
    offer_type = Column(Enum(OfferType), nullable=False)
    settlement_type = Column(
        Enum(SettlementType),
        nullable=False,
        default=SettlementType.CASH,
        server_default=SettlementType.CASH.name,
    )
    
    # کالا
    commodity_id = Column(Integer, ForeignKey("commodities.id"), nullable=False)
    commodity = relationship("Commodity")
    
    # تعداد و قیمت
    quantity = Column(Integer, nullable=False)  # تعداد اولیه
    price = Column(BigInteger, nullable=False)
    exclude_from_competitive_price = Column(Boolean, nullable=False, default=False, server_default='false', index=True)
    price_warning_type = Column(String(64), nullable=True)
    expire_reason = Column(String(32), nullable=True)
    expired_at = Column(DateTime(timezone=True), nullable=True, index=True)
    expired_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    expired_by_actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    expire_source_surface = Column(String(32), nullable=True)
    expire_source_server = Column(String(16), nullable=True)
    
    # تعداد باقیمانده (برای فروش خُرد)
    remaining_quantity = Column(Integer, nullable=True)
    
    # فروش یکجا یا خُرد
    is_wholesale = Column(Boolean, nullable=False, default=True)  # True = یکجا، False = خُرد
    
    # لیست بخش‌ها برای فروش خُرد (JSON array مثل [10, 15, 25])
    lot_sizes = Column(JSON, nullable=True)
    
    # لیست اولیه بخش‌ها برای تاریخچه؛ تکرار فقط از مانده و lot_sizes فعلی استفاده می‌کند
    original_lot_sizes = Column(JSON, nullable=True)
    
    # وضعیت لفظ
    status = Column(Enum(OfferStatus), nullable=False, default=OfferStatus.ACTIVE)
    
    # توضیحات یا شرایط کاربر (اختیاری)
    notes = Column(String(200), nullable=True)
    
    # آیدی پیام در کانال
    channel_message_id = Column(BigInteger, nullable=True)
    
    # آیدی لفظ جدید (اگر این لفظ تکرار شده باشد)
    republished_offer_id = Column(Integer, nullable=True)
    republished_offer_public_id = Column(String(40), nullable=True, index=True)

    # Immutable provenance owned by the replacement offer. Unlike the legacy
    # outgoing pointer above, creating a replacement never mutates its source.
    republished_from_offer_public_id = Column(String(40), nullable=True)
    
    # زمان ایجاد
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)

    # ===== Sync Fields =====
    idempotency_key = Column(String(64), unique=True, nullable=True)
    idempotency_fingerprint_version = Column(Integer, nullable=True)
    idempotency_fingerprint = Column(String(64), nullable=True)
    archived = Column(Boolean, default=False)

    
    # ===== فعال‌سازی Optimistic Locking =====
    __mapper_args__ = {
        "version_id_col": version_id
    }


Index(
    'ix_offers_time_limit_expired_history',
    func.coalesce(Offer.expired_at, Offer.updated_at, Offer.created_at).desc(),
    Offer.created_at.desc(),
    postgresql_where=(Offer.status == OfferStatus.EXPIRED) & (Offer.expire_reason == "time_limit"),
)

Index(
    'ix_offers_active_created_id',
    Offer.created_at.desc(),
    Offer.id.desc(),
    postgresql_where=Offer.status == OfferStatus.ACTIVE,
)

Index(
    'uq_offers_republished_from_offer_public_id_home_server',
    Offer.republished_from_offer_public_id,
    Offer.home_server,
    unique=True,
)
