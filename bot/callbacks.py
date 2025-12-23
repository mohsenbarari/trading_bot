from aiogram.filters.callback_data import CallbackData

# ==========================================
# Trade Creation Callbacks
# ==========================================

class TradeTypeCallback(CallbackData, prefix="trade_type", sep="_"):
    """
    Format: trade_type_{type}
    Example: trade_type_buy, trade_type_sell
    """
    type: str

class CommodityCallback(CallbackData, prefix="trade_commodity", sep="_"):
    """
    Format: trade_commodity_{id}
    Example: trade_commodity_12
    """
    id: int

class PageCallback(CallbackData, prefix="trade_page", sep="_"):
    """
    Format: trade_page_{trade_type}_{page}
    Example: trade_page_buy_2
    """
    trade_type: str
    page: int

class QuantityCallback(CallbackData, prefix="quantity", sep="_"):
    """
    Format: quantity_{value}
    Example: quantity_10, quantity_manual
    """
    value: str

class LotTypeCallback(CallbackData, prefix="lot_type", sep="_"):
    """
    Format: lot_type_{type}
    Example: lot_type_wholesale, lot_type_retail
    """
    type: str

class AcceptLotsCallback(CallbackData, prefix="accept_lots", sep="_"):
    """
    Format: accept_lots_{lots_str}
    Example: accept_lots_10_15
    """
    lots: str

class TradeActionCallback(CallbackData, prefix="trade", sep="_"):
    """
    Format: trade_{action}
    Example: trade_cancel, trade_confirm, trade_back_to_type
    """
    action: str

class SkipNotesCallback(CallbackData, prefix="skip", sep="_"):
    """
    Format: skip_{target}
    Example: skip_notes
    """
    target: str

# ==========================================
# Text Offer Callbacks
# ==========================================

class TextOfferActionCallback(CallbackData, prefix="text_offer", sep="_"):
    """
    Format: text_offer_{action}
    Example: text_offer_confirm, text_offer_cancel
    """
    action: str

# ==========================================
# Channel Execution Callbacks
# ==========================================

class ChannelTradeCallback(CallbackData, prefix="channel_trade", sep="_"):
    """
    Format: channel_trade_{offer_id}_{amount}
    Example: channel_trade_123_50
    """
    offer_id: int
    amount: int

# ==========================================
# Management Callbacks
# ==========================================

class ExpireOfferCallback(CallbackData, prefix="expire_offer", sep="_"):
    """
    Format: expire_offer_{offer_id}
    Example: expire_offer_123
    """
    offer_id: int

# ==========================================
# Trade History Callbacks
# ==========================================

class TradeHistoryCallback(CallbackData, prefix="trade_history", sep="_"):
    """
    Format: trade_history_{target_user_id}
    Example: trade_history_12345
    """
    target_user_id: int

class HistoryPageCallback(CallbackData, prefix="history", sep="_"):
    """
    Format: history_{months}m_{target_user_id}
    Example: history_3m_12345
    NOTE: In handler regex was history_\\d+m_\\d+, so prefix="history" is correct.
    But manual construction was `history_{months}m_{id}`.
    Standard CallbackData uses `sep` between fields.
    So `history_3_12345` would be standard.
    But to match existing regex or migration, I can use standard pack() which will produce `history_3_12345`.
    The regex handler in trade_history.py expects `history_3m_12345`.
    I will change the handler to use HistoryPageCallback.filter() which expects `history:3:12345` (if using default colon) or `history_3_12345`.
    I will stick to standard CallbackData packing.
    """
    months: int
    target_user_id: int

class ExportHistoryCallback(CallbackData, prefix="export", sep="_"):
    """
    Format: export_{format}_{target_user_id}
    Example: export_excel_12345
    """
    format: str
    target_user_id: int

class ProfileCallback(CallbackData, prefix="back_to_profile", sep="_"):
    """
    Format: back_to_profile_{target_user_id}
    Example: back_to_profile_12345
    """
    target_user_id: int

# ==========================================
# Constants
# ==========================================

ACTION_NOOP = "noop"
