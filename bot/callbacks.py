from aiogram.filters.callback_data import CallbackData

# ==========================================
# Trade Creation Callbacks
# ==========================================

class TradeTypeCallback(CallbackData, prefix="trade_type"):
    """
    Format: trade_type:{type}
    Example: trade_type:buy
    """
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

class ChannelTradeCallback(CallbackData, prefix="channel_trade"):
    """
    Format: channel_trade:{offer_id}:{amount}
    Example: channel_trade:123:50
    """
    offer_id: int
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
