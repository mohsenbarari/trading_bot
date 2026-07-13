from aiogram.filters.callback_data import CallbackData

from core.telegram_trade_callbacks import (
    CHANNEL_TRADE_LEGACY_CALLBACK_PREFIX,
    CHANNEL_TRADE_PUBLIC_CALLBACK_PREFIX,
)

# ==========================================
# Trade Creation Callbacks
# ==========================================

class TradeTypeCallback(CallbackData, prefix="trade_type"):
    """
    Format: trade_type:{type}
    Example: trade_type:buy
    """
    type: str


class TradeSettlementCallback(CallbackData, prefix="trade_settlement"):
    type: str

class CommodityCallback(CallbackData, prefix="trade_commodity"):
    """
    Format: trade_commodity:{id}
    Example: trade_commodity:12
    """
    id: int

class PageCallback(CallbackData, prefix="trade_page"):
    """
    Format: trade_page:{trade_type}:{page}
    Example: trade_page:buy:2
    """
    trade_type: str
    page: int

class QuantityCallback(CallbackData, prefix="quantity"):
    """
    Format: quantity:{value}
    Example: quantity:10
    """
    value: str

class LotTypeCallback(CallbackData, prefix="lot_type"):
    """
    Format: lot_type:{type}
    Example: lot_type:wholesale
    """
    type: str

class AcceptLotsCallback(CallbackData, prefix="accept_lots"):
    """
    Format: accept_lots:{lots_str}
    Example: accept_lots:10_15
    """
    lots: str

class TradeActionCallback(CallbackData, prefix="trade"):
    """
    Format: trade:{action}
    Example: trade:cancel
    """
    action: str

class SkipNotesCallback(CallbackData, prefix="skip"):
    """
    Format: skip:{target}
    Example: skip:notes
    """
    target: str

class TextOfferActionCallback(CallbackData, prefix="text_offer"):
    """
    Format: text_offer:{action}
    Example: text_offer:confirm
    """
    action: str


class TradeWizardActionCallback(CallbackData, prefix="trade_wizard"):
    action: str


class TradeWizardEditCallback(CallbackData, prefix="trade_edit"):
    field: str

class ChannelTradeCallback(CallbackData, prefix=CHANNEL_TRADE_LEGACY_CALLBACK_PREFIX):
    """
    Format: channel_trade:{offer_id}:{amount}
    Example: channel_trade:123:50
    """
    offer_id: int
    amount: int


class ChannelTradePublicCallback(CallbackData, prefix=CHANNEL_TRADE_PUBLIC_CALLBACK_PREFIX):
    """
    Format: ct2:{offer_public_id}:{amount}
    Example: ct2:ofr_abc123:50
    """
    offer_public_id: str
    amount: int

class ExpireOfferCallback(CallbackData, prefix="expire_offer"):
    """
    Format: expire_offer:{offer_id}
    Example: expire_offer:123
    """
    offer_id: int

class TradeHistoryCallback(CallbackData, prefix="trade_history"):
    """
    Format: trade_history:{target_user_id}
    Example: trade_history:12345
    """
    target_user_id: int

class HistoryPageCallback(CallbackData, prefix="history"):
    """
    Format: history:{months}:{target_user_id}
    Example: history:3:12345
    """
    months: int
    target_user_id: int

class ExportHistoryCallback(CallbackData, prefix="export"):
    """
    Format: export:{format}:{target_user_id}
    Example: export:excel:12345
    """
    format: str
    target_user_id: int

class ProfileTradePdfCallback(CallbackData, prefix="profile_pdf"):
    """
    Format: profile_pdf:{target_user_id}
    Example: profile_pdf:12345
    """
    target_user_id: int

class CommodityCatalogPageCallback(CallbackData, prefix="commodity_catalog"):
    """
    Format: commodity_catalog:{page}
    Example: commodity_catalog:2
    """
    page: int

class ProfileCallback(CallbackData, prefix="back_to_profile"):
    """
    Format: back_to_profile:{target_user_id}
    Example: back_to_profile:12345
    """
    target_user_id: int

# ==========================================
# Constants
# ==========================================

ACTION_NOOP = "noop"
