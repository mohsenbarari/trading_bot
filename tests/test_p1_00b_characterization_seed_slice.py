"""P1-00b: first executed seed partition beyond admin toggle-block."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

import schemas
from api.routers.admin_messages import (
    AdminBroadcastCreate,
    AdminMarketMessageCreate,
    clear_current_market_message,
    create_broadcast,
    create_market_message,
    get_broadcast_history,
    get_current_market_message,
    get_market_message_history,
)
from api.routers.chat import (
    create_group,
    delete_direct_conversation,
    delete_group_member,
    delete_message,
    demote_group_admin,
    get_group_detail,
    get_groups,
    mute_direct_conversation,
    mute_room_conversation,
    patch_group,
    pin_direct_conversation,
    pin_room_conversation,
    post_group_leave,
    post_group_member,
    promote_group_admin,
    send_message,
    update_message,
)
from api.routers.chat_schemas import (
    ConversationMuteUpdateRequest,
    ConversationPinUpdateRequest,
    GroupCreateRequest,
    GroupMemberAddRequest,
    GroupUpdateRequest,
    MessageSend,
    MessageUpdate,
)
from api.routers.commodities import (
    add_alias_to_commodity,
    create_commodity,
    delete_alias,
    delete_commodity,
    read_all_commodities,
    read_commodity,
    update_alias,
    update_commodity_name,
)
from api.routers.trading_settings import (
    MarketScheduleOverrideUpsert,
    TradingSettingsUpdate,
    create_market_override,
    delete_market_override,
    get_market_state,
    get_settings,
    list_market_overrides,
    reset_settings,
    update_market_override,
    update_settings,
)
from bot.handlers.admin import (
    cancel_invitation_creation,
    process_invitation_account_name,
    process_invitation_mobile,
    start_invitation_creation,
    start_invitation_creation_inline,
)
from bot.handlers.admin_broadcast import (
    cancel_telegram_admin_broadcast,
    choose_all_recipients,
    choose_group_recipients,
    choose_selected_recipients,
    confirm_telegram_admin_broadcast,
    finish_group_selection,
    finish_selected_recipients,
    process_broadcast_message_text,
    process_selected_recipient_search,
    search_again,
    start_telegram_admin_broadcast,
    toggle_group_recipient,
    toggle_selected_user,
)
from bot.handlers.admin_commodities import (
    handle_add_aliases_and_create,
    handle_add_name,
    handle_add_start,
    handle_alias_add_name,
    handle_alias_add_start,
    handle_alias_delete_start,
    handle_alias_delete_yes,
    handle_alias_edit_name,
    handle_alias_edit_start,
    handle_back_to_list,
    handle_cancel_fsm,
    handle_commodity_edit_name,
    handle_commodity_edit_start,
    handle_delete_confirm,
    handle_delete_yes,
    handle_manage_aliases,
    handle_manage_commodities,
)
from bot.handlers.admin_users import (
    handle_admin_max_block_custom,
    handle_admin_max_block_set,
    handle_admin_set_max_block,
    handle_back_to_admin,
    handle_limit_cancel,
    handle_limit_confirm,
    handle_set_commodities,
    handle_set_requests,
    handle_set_trades,
    handle_set_user_role,
    handle_user_block_actions,
    handle_user_block_settings,
    handle_user_delete_confirm,
    handle_user_delete_request,
    handle_user_edit_role,
    handle_user_limit_start,
    handle_user_search_cancel,
    handle_user_settings,
    handle_user_unblock,
    handle_user_unlimit,
    handle_users_list_command,
    handle_users_menu,
    handle_users_pagination,
    handle_view_user_profile,
    process_custom_max_block,
    process_limit_value,
    process_search_query,
    start_search_user,
)
from bot.states import (
    AdminBroadcast,
    CommodityManagement,
    InvitationCreation,
    UserLimitations,
    UserManagement,
)
from bot.handlers.trade_execute import handle_channel_trade, handle_channel_trade_public
from bot.utils import redis_helpers
from core.commodity_defaults import IMAM_COMMODITY_NAME
from core.enums import MessageType, UserAccountStatus, UserRole
from core.services.telegram_admin_broadcast_service import TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY
from models.admin_message import AdminBroadcastMessage
from models.chat import Chat
from models.chat_member import ChatMember
from models.commodity import Commodity, CommodityAlias
from models.market_runtime_state import MarketRuntimeState
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType
from models.message import Message
from models.notification import Notification
from models.offer import OfferStatus
from models.telegram_admin_broadcast import TelegramAdminBroadcastAudienceType
from models.user import User, UserRole as ModelUserRole
from tests.test_p1_00_characterization_admin_account_status import (
    ACCOUNT_SEED,
    MappedAccountUser,
    _make_store,
    _persist_user,
    _reload_user,
)
from tests.test_p1_00b_characterization_family_overlap import (
    ADMIN_SQL,
    YieldingSession,
    _engine as _overlap_engine,
    _exec_sql,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
INV_DIR = REPO_ROOT / "docs/refactor/two-server/section-1/inventory"

BROADCAST_GROUP_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-GROUP-RECIPIENT-L240"
BROADCAST_USER_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334"
MSG_SEND_SEED = "API-MSG-POST-CHAT-SEND-MESSAGE-L2028"
SET_ROLE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-USER-ROLE-L1090"
UNBLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-UNBLOCK-L925"
BROADCAST_CONFIRM_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-CONFIRM-TELEGRAM-ADMIN-BROADCAST-L593"
UNLIMIT_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-UNLIMIT-L998"
MAX_BLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-MAX-BLOCK-SET-L1723"
BLOCK_ACTIONS_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-ACTIONS-L798"
LIMIT_CONFIRM_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-LIMIT-CONFIRM-L1438"
MSG_UPDATE_SEED = "API-MSG-PUT-CHAT-UPDATE-MESSAGE-L2079"
MSG_DELETE_SEED = "API-MSG-DELETE-CHAT-DELETE-MESSAGE-L2183"
CUSTOM_MAX_BLOCK_SEED = "BOT-ADMIN-MESSAGE-ADMIN-USERS-PROCESS-CUSTOM-MAX-BLOCK-L1799"
CREATE_COMMODITY_SEED = "API-ADMIN-POST-COMMODITIES-CREATE-COMMODITY-L143"
UPDATE_COMMODITY_SEED = "API-ADMIN-PUT-COMMODITIES-UPDATE-COMMODITY-NAME-L189"
DELETE_COMMODITY_SEED = "API-ADMIN-DELETE-COMMODITIES-DELETE-COMMODITY-L243"
ADD_ALIAS_SEED = "API-ADMIN-POST-COMMODITIES-ADD-ALIAS-TO-COMMODITY-L340"
UPDATE_ALIAS_SEED = "API-ADMIN-PUT-COMMODITIES-UPDATE-ALIAS-L388"
DELETE_ALIAS_SEED = "API-ADMIN-DELETE-COMMODITIES-DELETE-ALIAS-L437"
CREATE_OVERRIDE_SEED = "API-ADMIN-POST-TRADING-SETTINGS-CREATE-MARKET-OVERRIDE-L222"
UPDATE_OVERRIDE_SEED = "API-ADMIN-PUT-TRADING-SETTINGS-UPDATE-MARKET-OVERRIDE-L253"
DELETE_OVERRIDE_SEED = "API-ADMIN-DELETE-TRADING-SETTINGS-DELETE-MARKET-OVERRIDE-L288"
BOT_ALIAS_ADD_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ALIAS-ADD-NAME-L365"
BOT_ALIAS_EDIT_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ALIAS-EDIT-NAME-L429"
BOT_ALIAS_DELETE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-ALIAS-DELETE-YES-L487"
BOT_COMMODITY_EDIT_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-COMMODITY-EDIT-NAME-L537"
BOT_COMMODITY_CREATE_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ADD-ALIASES-AND-CREATE-L622"
BOT_COMMODITY_DELETE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-DELETE-YES-L685"
UPDATE_SETTINGS_SEED = "API-ADMIN-PUT-TRADING-SETTINGS-UPDATE-SETTINGS-L306"
RESET_SETTINGS_SEED = "API-ADMIN-POST-TRADING-SETTINGS-RESET-SETTINGS-L353"
BOT_ALIAS_ADD_START_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-ALIAS-ADD-START-L350"
BOT_ALIAS_EDIT_START_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-ALIAS-EDIT-START-L408"
BOT_ALIAS_DELETE_START_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-ALIAS-DELETE-START-L466"
BOT_COMMODITY_EDIT_START_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-COMMODITY-EDIT-START-L523"
BOT_COMMODITY_ADD_START_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-ADD-START-L572"
BOT_COMMODITY_ADD_NAME_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-ADD-NAME-L583"
BOT_COMMODITY_DELETE_CONFIRM_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-DELETE-CONFIRM-L671"
BOT_COMMODITY_CANCEL_FSM_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-CANCEL-FSM-L719"
INVITE_START_SEED = "BOT-ADMIN-MESSAGE-ADMIN-START-INVITATION-CREATION-L140"
INVITE_START_INLINE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-START-INVITATION-CREATION-INLINE-L160"
INVITE_ACCOUNT_SEED = "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-ACCOUNT-NAME-L183"
INVITE_MOBILE_SEED = "BOT-ADMIN-MESSAGE-ADMIN-PROCESS-INVITATION-MOBILE-L230"
INVITE_CANCEL_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-CANCEL-INVITATION-CREATION-L402"
USER_EDIT_ROLE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-EDIT-ROLE-L1078"
USER_DELETE_REQUEST_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-DELETE-REQUEST-L1186"
USER_DELETE_CONFIRM_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-DELETE-CONFIRM-L1203"
LIMIT_CANCEL_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-LIMIT-CANCEL-L1563"
BLOCK_SETTINGS_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-BLOCK-SETTINGS-L1607"
SET_MAX_BLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-SET-MAX-BLOCK-L1698"
MAX_BLOCK_CUSTOM_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-MAX-BLOCK-CUSTOM-L1772"
CREATE_MARKET_MESSAGE_SEED = "API-ADMIN-POST-ADMIN-MESSAGES-CREATE-MARKET-MESSAGE-L204"
CLEAR_MARKET_MESSAGE_SEED = "API-ADMIN-DELETE-ADMIN-MESSAGES-CLEAR-CURRENT-MARKET-MESSAGE-L235"
BROADCAST_START_SEED = "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-START-TELEGRAM-ADMIN-BROADCAST-L179"
BROADCAST_CANCEL_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-CANCEL-TELEGRAM-ADMIN-BROADCAST-L194"
BROADCAST_ALL_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-CHOOSE-ALL-RECIPIENTS-L208"
BROADCAST_CHOOSE_GROUPS_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-CHOOSE-GROUP-RECIPIENTS-L221"
BROADCAST_FINISH_GROUPS_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-GROUP-SELECTION-L270"
BROADCAST_CHOOSE_SELECTED_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-CHOOSE-SELECTED-RECIPIENTS-L286"
BROADCAST_SEARCH_SEED = "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-SELECTED-RECIPIENT-SEARCH-L308"
BROADCAST_SEARCH_AGAIN_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-SEARCH-AGAIN-L377"
BROADCAST_FINISH_SELECTED_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-SELECTED-RECIPIENTS-L395"
BROADCAST_TEXT_SEED = "BOT-ADMIN-MESSAGE-ADMIN-BROADCAST-PROCESS-BROADCAST-MESSAGE-TEXT-L411"
LIMIT_START_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-LIMIT-START-L1252"
SET_TRADES_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-TRADES-L1312"
SET_COMMODITIES_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-COMMODITIES-L1336"
SET_REQUESTS_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-SET-REQUESTS-L1360"
PROCESS_LIMIT_SEED = "BOT-ADMIN-MESSAGE-ADMIN-USERS-PROCESS-LIMIT-VALUE-L1384"
SEARCH_START_SEED = "BOT-ADMIN-MESSAGE-ADMIN-USERS-START-SEARCH-USER-L635"
SEARCH_CANCEL_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-SEARCH-CANCEL-L657"
SEARCH_QUERY_SEED = "BOT-ADMIN-MESSAGE-ADMIN-USERS-PROCESS-SEARCH-QUERY-L678"
USER_SETTINGS_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USER-SETTINGS-L742"
CREATE_BROADCAST_SEED = "API-ADMIN-POST-ADMIN-MESSAGES-CREATE-BROADCAST-L261"
MANAGE_COMMODITIES_SEED = "BOT-ADMIN-MESSAGE-ADMIN-COMMODITIES-HANDLE-MANAGE-COMMODITIES-L305"
BACK_TO_LIST_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-BACK-TO-LIST-L318"
MANAGE_ALIASES_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-COMMODITIES-HANDLE-MANAGE-ALIASES-L333"
USERS_MENU_SEED = "BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-USERS-MENU-L489"
USERS_LIST_SEED = "BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-USERS-LIST-COMMAND-L512"
USERS_PAGE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-USERS-PAGINATION-L527"
VIEW_PROFILE_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-VIEW-USER-PROFILE-L548"
BACK_TO_ADMIN_SEED = "BOT-ADMIN-MESSAGE-ADMIN-USERS-HANDLE-BACK-TO-ADMIN-L602"
GET_MARKET_CURRENT_SEED = "API-ADMIN-GET-ADMIN-MESSAGES-GET-CURRENT-MARKET-MESSAGE-L180"
GET_MARKET_HISTORY_SEED = "API-ADMIN-GET-ADMIN-MESSAGES-GET-MARKET-MESSAGE-HISTORY-L194"
GET_BROADCAST_HISTORY_SEED = "API-ADMIN-GET-ADMIN-MESSAGES-GET-BROADCAST-HISTORY-L251"
READ_COMMODITY_SEED = "API-ADMIN-GET-COMMODITIES-READ-COMMODITY-L131"
READ_ALL_COMMODITIES_SEED = "API-ADMIN-GET-COMMODITIES-READ-ALL-COMMODITIES-L99"
GET_SETTINGS_SEED = "API-ADMIN-GET-TRADING-SETTINGS-GET-SETTINGS-L195"
GET_MARKET_STATE_SEED = "API-ADMIN-GET-TRADING-SETTINGS-GET-MARKET-STATE-L202"
LIST_OVERRIDES_SEED = "API-ADMIN-GET-TRADING-SETTINGS-LIST-MARKET-OVERRIDES-L214"
MSG_CREATE_GROUP_SEED = "API-MSG-POST-CHAT-CREATE-GROUP-L1234"
MSG_PATCH_GROUP_SEED = "API-MSG-PATCH-CHAT-PATCH-GROUP-L1324"
MSG_POST_GROUP_MEMBER_SEED = "API-MSG-POST-CHAT-POST-GROUP-MEMBER-L1362"
MSG_DELETE_GROUP_MEMBER_SEED = "API-MSG-DELETE-CHAT-DELETE-GROUP-MEMBER-L1384"
MSG_PROMOTE_GROUP_ADMIN_SEED = "API-MSG-POST-CHAT-PROMOTE-GROUP-ADMIN-L1406"
MSG_DEMOTE_GROUP_ADMIN_SEED = "API-MSG-DELETE-CHAT-DEMOTE-GROUP-ADMIN-L1428"
MSG_GROUP_LEAVE_SEED = "API-MSG-POST-CHAT-POST-GROUP-LEAVE-L1450"
MSG_PIN_ROOM_SEED = "API-MSG-POST-CHAT-PIN-ROOM-CONVERSATION-L1470"
MSG_MUTE_ROOM_SEED = "API-MSG-POST-CHAT-MUTE-ROOM-CONVERSATION-L1544"
MSG_GET_GROUPS_SEED = "API-MSG-GET-CHAT-GET-GROUPS-L1158"
MSG_GET_GROUP_DETAIL_SEED = "API-MSG-GET-CHAT-GET-GROUP-DETAIL-L1278"
MSG_PIN_DIRECT_SEED = "API-MSG-POST-CHAT-PIN-DIRECT-CONVERSATION-L643"
MSG_MUTE_DIRECT_SEED = "API-MSG-POST-CHAT-MUTE-DIRECT-CONVERSATION-L727"
MSG_HIDE_DIRECT_SEED = "API-MSG-DELETE-CHAT-DELETE-DIRECT-CONVERSATION-L777"
TRADE_CHANNEL_SEED = "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-L810"
TRADE_PUBLIC_SEED = "BOT-TRADE-CALLBACK-QUERY-TRADE-EXECUTE-HANDLE-CHANNEL-TRADE-PUBLIC-L829"
UNEVIDENCED_TRADE_GATEWAY_SEEDS = {TRADE_CHANNEL_SEED, TRADE_PUBLIC_SEED}
TOGGLE_BLOCK_SEED = "BOT-ADMIN-CALLBACK-QUERY-ADMIN-USERS-HANDLE-ADMIN-TOGGLE-BLOCK-L1649"

SLICE_EXECUTED_SEED_IDS = {
    ACCOUNT_SEED,
    BROADCAST_GROUP_SEED,
    BROADCAST_USER_SEED,
    MSG_SEND_SEED,
    SET_ROLE_SEED,
    UNBLOCK_SEED,
    BROADCAST_CONFIRM_SEED,
    UNLIMIT_SEED,
    MAX_BLOCK_SEED,
    BLOCK_ACTIONS_SEED,
    LIMIT_CONFIRM_SEED,
    MSG_UPDATE_SEED,
    MSG_DELETE_SEED,
    CUSTOM_MAX_BLOCK_SEED,
    CREATE_COMMODITY_SEED,
    UPDATE_COMMODITY_SEED,
    DELETE_COMMODITY_SEED,
    ADD_ALIAS_SEED,
    UPDATE_ALIAS_SEED,
    DELETE_ALIAS_SEED,
    CREATE_OVERRIDE_SEED,
    UPDATE_OVERRIDE_SEED,
    DELETE_OVERRIDE_SEED,
    BOT_ALIAS_ADD_SEED,
    BOT_ALIAS_EDIT_SEED,
    BOT_ALIAS_DELETE_SEED,
    BOT_COMMODITY_EDIT_SEED,
    BOT_COMMODITY_CREATE_SEED,
    BOT_COMMODITY_DELETE_SEED,
    UPDATE_SETTINGS_SEED,
    RESET_SETTINGS_SEED,
    BOT_ALIAS_ADD_START_SEED,
    BOT_ALIAS_EDIT_START_SEED,
    BOT_ALIAS_DELETE_START_SEED,
    BOT_COMMODITY_EDIT_START_SEED,
    BOT_COMMODITY_ADD_START_SEED,
    BOT_COMMODITY_ADD_NAME_SEED,
    BOT_COMMODITY_DELETE_CONFIRM_SEED,
    BOT_COMMODITY_CANCEL_FSM_SEED,
    INVITE_START_SEED,
    INVITE_START_INLINE_SEED,
    INVITE_ACCOUNT_SEED,
    INVITE_MOBILE_SEED,
    INVITE_CANCEL_SEED,
    USER_EDIT_ROLE_SEED,
    USER_DELETE_REQUEST_SEED,
    USER_DELETE_CONFIRM_SEED,
    LIMIT_CANCEL_SEED,
    BLOCK_SETTINGS_SEED,
    SET_MAX_BLOCK_SEED,
    MAX_BLOCK_CUSTOM_SEED,
    CREATE_MARKET_MESSAGE_SEED,
    CLEAR_MARKET_MESSAGE_SEED,
    BROADCAST_START_SEED,
    BROADCAST_CANCEL_SEED,
    BROADCAST_ALL_SEED,
    BROADCAST_CHOOSE_GROUPS_SEED,
    BROADCAST_FINISH_GROUPS_SEED,
    BROADCAST_CHOOSE_SELECTED_SEED,
    BROADCAST_SEARCH_SEED,
    BROADCAST_SEARCH_AGAIN_SEED,
    BROADCAST_FINISH_SELECTED_SEED,
    BROADCAST_TEXT_SEED,
    LIMIT_START_SEED,
    SET_TRADES_SEED,
    SET_COMMODITIES_SEED,
    SET_REQUESTS_SEED,
    PROCESS_LIMIT_SEED,
    SEARCH_START_SEED,
    SEARCH_CANCEL_SEED,
    SEARCH_QUERY_SEED,
    USER_SETTINGS_SEED,
    CREATE_BROADCAST_SEED,
    MANAGE_COMMODITIES_SEED,
    BACK_TO_LIST_SEED,
    MANAGE_ALIASES_SEED,
    USERS_MENU_SEED,
    USERS_LIST_SEED,
    USERS_PAGE_SEED,
    VIEW_PROFILE_SEED,
    BACK_TO_ADMIN_SEED,
    GET_MARKET_CURRENT_SEED,
    GET_MARKET_HISTORY_SEED,
    GET_BROADCAST_HISTORY_SEED,
    READ_COMMODITY_SEED,
    READ_ALL_COMMODITIES_SEED,
    GET_SETTINGS_SEED,
    GET_MARKET_STATE_SEED,
    LIST_OVERRIDES_SEED,
    MSG_CREATE_GROUP_SEED,
    MSG_PATCH_GROUP_SEED,
    MSG_POST_GROUP_MEMBER_SEED,
    MSG_DELETE_GROUP_MEMBER_SEED,
    MSG_PROMOTE_GROUP_ADMIN_SEED,
    MSG_DEMOTE_GROUP_ADMIN_SEED,
    MSG_GROUP_LEAVE_SEED,
    MSG_PIN_ROOM_SEED,
    MSG_MUTE_ROOM_SEED,
    MSG_GET_GROUPS_SEED,
    MSG_GET_GROUP_DETAIL_SEED,
    MSG_PIN_DIRECT_SEED,
    MSG_MUTE_DIRECT_SEED,
    MSG_HIDE_DIRECT_SEED,
}

SEED_EVIDENCE_NODES = {
    TOGGLE_BLOCK_SEED: (
        "tests/test_p1_00_characterization_admin_toggle_behavior.py"
        "::P100AdminToggleBehaviorTests"
        ".test_toggle_block_second_invoke_flips_again_and_has_no_command_identity"
    ),
    ACCOUNT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_account_status_seed_commits_then_denies_without_write"
    ),
    BROADCAST_GROUP_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_broadcast_toggle_group_recipient_flips_fsm_and_denies_injects"
    ),
    BROADCAST_USER_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_broadcast_toggle_selected_user_flips_fsm_and_denies_injects"
    ),
    MSG_SEND_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_send_message_persists_publishes_retries_and_fails_before_commit_injects"
    ),
    SET_ROLE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_set_user_role_persists_then_denies_and_fails_before_commit_injects"
    ),
    UNBLOCK_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_unblock_clears_restriction_then_denies_without_write_injects"
    ),
    BROADCAST_CONFIRM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_confirm_telegram_admin_broadcast_persists_then_replays_key_injects"
    ),
    UNLIMIT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_unlimit_clears_caps_then_denies_without_write_injects"
    ),
    MAX_BLOCK_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_admin_max_block_set_persists_then_denies_injects"
    ),
    BLOCK_ACTIONS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_block_actions_persists_restriction_then_denies_injects"
    ),
    LIMIT_CONFIRM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_limit_confirm_persists_caps_then_denies_injects"
    ),
    MSG_UPDATE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_message_edits_then_denies_and_fails_injects"
    ),
    MSG_DELETE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_message_soft_deletes_then_denies_injects"
    ),
    CUSTOM_MAX_BLOCK_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_process_custom_max_block_persists_then_denies_injects"
    ),
    CREATE_COMMODITY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_create_commodity_persists_then_rejects_digits_and_conflict"
    ),
    UPDATE_COMMODITY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_commodity_name_persists_then_404s"
    ),
    DELETE_COMMODITY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_commodity_removes_then_404s_and_locks_imam"
    ),
    ADD_ALIAS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_add_alias_to_commodity_persists_then_conflicts"
    ),
    UPDATE_ALIAS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_alias_persists_then_404s"
    ),
    DELETE_ALIAS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_alias_removes_then_404s"
    ),
    CREATE_OVERRIDE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_create_market_override_persists_then_rejects_duplicate"
    ),
    UPDATE_OVERRIDE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_market_override_persists_then_404s"
    ),
    DELETE_OVERRIDE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_market_override_removes_then_404s"
    ),
    BOT_ALIAS_ADD_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_add_name_persists_then_denies_without_user"
    ),
    BOT_ALIAS_EDIT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_edit_name_persists_then_denies_without_user"
    ),
    BOT_ALIAS_DELETE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_delete_yes_persists_then_denies_non_admin"
    ),
    BOT_COMMODITY_EDIT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_commodity_edit_name_persists_then_denies_without_user"
    ),
    BOT_COMMODITY_CREATE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_add_aliases_and_create_persists_then_denies_without_user"
    ),
    BOT_COMMODITY_DELETE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_delete_yes_removes_then_denies_non_admin"
    ),
    UPDATE_SETTINGS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_update_settings_persists_then_rejects_invalid_hours"
    ),
    RESET_SETTINGS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_reset_settings_restores_defaults_after_update"
    ),
    BOT_ALIAS_ADD_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_add_start_sets_fsm_then_denies_non_admin"
    ),
    BOT_ALIAS_EDIT_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_edit_start_sets_fsm_then_denies_non_admin"
    ),
    BOT_ALIAS_DELETE_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_alias_delete_start_sets_fsm_then_denies_non_admin"
    ),
    BOT_COMMODITY_EDIT_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_commodity_edit_start_sets_fsm_then_denies_non_admin"
    ),
    BOT_COMMODITY_ADD_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_add_start_sets_fsm_then_denies_non_admin"
    ),
    BOT_COMMODITY_ADD_NAME_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_add_name_advances_fsm_then_denies_without_user"
    ),
    BOT_COMMODITY_DELETE_CONFIRM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_delete_confirm_sets_fsm_then_denies_non_admin"
    ),
    BOT_COMMODITY_CANCEL_FSM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_bot_cancel_fsm_clears_then_denies_non_admin"
    ),
    INVITE_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_start_invitation_creation_sets_fsm_then_denies_without_user"
    ),
    INVITE_START_INLINE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_start_invitation_inline_sets_fsm_then_denies_without_user"
    ),
    INVITE_ACCOUNT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_process_invitation_account_name_advances_then_rejects_invalid"
    ),
    INVITE_MOBILE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_process_invitation_mobile_advances_then_rejects_invalid"
    ),
    INVITE_CANCEL_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_cancel_invitation_creation_clears_then_denies_without_user"
    ),
    USER_EDIT_ROLE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_edit_role_prompts_then_denies_non_admin"
    ),
    USER_DELETE_REQUEST_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_delete_request_redirects_then_denies_non_admin"
    ),
    USER_DELETE_CONFIRM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_delete_confirm_redirects_then_denies_non_admin"
    ),
    LIMIT_CANCEL_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_limit_cancel_clears_fsm_then_denies_non_admin"
    ),
    BLOCK_SETTINGS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_block_settings_shows_then_denies_non_admin"
    ),
    SET_MAX_BLOCK_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_admin_set_max_block_prompts_then_denies_non_admin"
    ),
    MAX_BLOCK_CUSTOM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_admin_max_block_custom_sets_fsm_then_denies_non_admin"
    ),
    CREATE_MARKET_MESSAGE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_create_market_message_persists_notifies_then_rejects_empty"
    ),
    CLEAR_MARKET_MESSAGE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_clear_current_market_message_deactivates_then_noop_when_empty"
    ),
    BROADCAST_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_start_telegram_admin_broadcast_opens_menu_then_denies_non_admin"
    ),
    BROADCAST_CANCEL_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_cancel_telegram_admin_broadcast_clears_then_denies_non_admin"
    ),
    BROADCAST_ALL_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_choose_all_recipients_asks_text_then_denies_non_admin"
    ),
    BROADCAST_CHOOSE_GROUPS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_choose_group_recipients_opens_picker_then_denies_non_admin"
    ),
    BROADCAST_FINISH_GROUPS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_finish_group_selection_asks_text_then_rejects_empty"
    ),
    BROADCAST_CHOOSE_SELECTED_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_choose_selected_recipients_asks_search_then_denies_non_admin"
    ),
    BROADCAST_SEARCH_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_process_selected_recipient_search_stores_query_then_denies_non_admin"
    ),
    BROADCAST_SEARCH_AGAIN_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_search_again_resets_query_state_then_denies_non_admin"
    ),
    BROADCAST_FINISH_SELECTED_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_finish_selected_recipients_asks_text_then_rejects_empty"
    ),
    BROADCAST_TEXT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_process_broadcast_message_text_drafts_then_rejects_empty"
    ),
    LIMIT_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_limit_start_prompts_duration_then_stores_expiry"
    ),
    SET_TRADES_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_set_trades_sets_fsm_then_denies_non_admin"
    ),
    SET_COMMODITIES_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_set_commodities_sets_fsm_then_denies_non_admin"
    ),
    SET_REQUESTS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_set_requests_sets_fsm_then_denies_non_admin"
    ),
    PROCESS_LIMIT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_process_limit_value_stores_then_rejects_invalid"
    ),
    SEARCH_START_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_start_search_user_sets_fsm_then_denies_non_admin"
    ),
    SEARCH_CANCEL_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_search_cancel_clears_then_denies_non_admin"
    ),
    SEARCH_QUERY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_process_search_query_finds_user_then_rejects_unknown"
    ),
    USER_SETTINGS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_user_settings_shows_then_denies_non_admin"
    ),
    CREATE_BROADCAST_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_create_broadcast_persists_messages_then_rejects_empty"
    ),
    MANAGE_COMMODITIES_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_manage_commodities_lists_then_denies_without_user"
    ),
    BACK_TO_LIST_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_back_to_list_clears_then_denies_non_admin"
    ),
    MANAGE_ALIASES_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_manage_aliases_lists_then_denies_non_admin"
    ),
    USERS_MENU_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_users_menu_stores_anchor_then_denies_non_admin"
    ),
    USERS_LIST_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_users_list_command_renders_then_denies_non_admin"
    ),
    USERS_PAGE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_users_pagination_renders_page_then_denies_non_admin"
    ),
    VIEW_PROFILE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_view_user_profile_edits_then_denies_non_admin"
    ),
    BACK_TO_ADMIN_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_back_to_admin_clears_then_denies_non_admin"
    ),
    GET_MARKET_CURRENT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_get_current_market_message_reads_row_then_none_when_empty"
    ),
    GET_MARKET_HISTORY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_get_market_message_history_lists_then_empty"
    ),
    GET_BROADCAST_HISTORY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_get_broadcast_history_lists_then_empty"
    ),
    READ_COMMODITY_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_read_commodity_returns_row_then_404s"
    ),
    READ_ALL_COMMODITIES_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_read_all_commodities_lists_then_empty"
    ),
    GET_SETTINGS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_get_settings_reads_persisted_quantity"
    ),
    GET_MARKET_STATE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_get_market_state_returns_schedule_view"
    ),
    LIST_OVERRIDES_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_list_market_overrides_lists_then_empty"
    ),
    MSG_CREATE_GROUP_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_create_group_persists_then_fails_before_commit"
    ),
    MSG_PATCH_GROUP_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_patch_group_renames_then_404s"
    ),
    MSG_POST_GROUP_MEMBER_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_post_group_member_adds_then_404s"
    ),
    MSG_DELETE_GROUP_MEMBER_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_group_member_removes_then_404s"
    ),
    MSG_PROMOTE_GROUP_ADMIN_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_promote_group_admin_promotes_then_404s"
    ),
    MSG_DEMOTE_GROUP_ADMIN_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_demote_group_admin_demotes_then_rejects_last_admin"
    ),
    MSG_GROUP_LEAVE_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_post_group_leave_leaves_then_rejects_last_admin"
    ),
    MSG_PIN_ROOM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_pin_room_conversation_pins_then_404s"
    ),
    MSG_MUTE_ROOM_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_mute_room_conversation_mutes_then_404s"
    ),
    MSG_GET_GROUPS_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_get_groups_lists_created_then_empty"
    ),
    MSG_GET_GROUP_DETAIL_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_get_group_detail_reads_then_404s"
    ),
    MSG_PIN_DIRECT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_pin_direct_conversation_pins_then_404s"
    ),
    MSG_MUTE_DIRECT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_mute_direct_conversation_mutes_then_404s"
    ),
    MSG_HIDE_DIRECT_SEED: (
        "tests/test_p1_00b_characterization_seed_slice.py"
        "::P100bSeedSliceExecutionTests.test_delete_direct_conversation_hides_then_404s"
    ),
}


class IsolatedConfirmRedis:
    """Client-construction isolate: eval fails so confirmation uses memory fallback."""

    async def eval(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def aclose(self):
        return None


class IsolatedAsyncRedis:
    """Client-construction isolate: no socket; cache/notify commands fail closed."""

    def __init__(self, *args, **kwargs):
        self.connected = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def set(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def delete(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def incr(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def publish(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    async def close(self):
        return None


class IsolatedSyncRedis:
    """Client-construction isolate for sync publish_event_sync."""

    def __init__(self, *args, **kwargs):
        self.connected = False

    def publish(self, *args, **kwargs):
        raise RuntimeError("isolated-redis")

    def close(self):
        return None


class FakeState:
    def __init__(self, **data):
        self.data = dict(data)
        self.updates = 0
        self.state = None

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)
        self.updates += 1

    async def set_state(self, state):
        self.state = state

    async def clear(self):
        self.data.clear()
        self.state = None


class RoleSession:
    """Async adapter over mapped sqlite users. Does not mock the durable write."""

    def __init__(self, sync_session, *, user_id=9, fail_commit=False):
        self._session = sync_session
        self.user_id = user_id
        self.fail_commit = fail_commit
        self.commit_count = 0

    async def execute(self, statement, params=None):
        blob = f"{statement} {getattr(statement, 'text', '')}"
        if "FROM users" in blob and "notifications" not in blob.lower():
            loaded = self._session.get(MappedAccountUser, self.user_id)
            return SimpleNamespace(scalar_one_or_none=lambda: loaded)
        return self._session.execute(statement)

    def add(self, obj):
        self._session.add(obj)

    async def flush(self):
        self._session.flush()

    async def refresh(self, obj, attribute_names=None):
        self._session.refresh(obj)

    async def commit(self):
        if self.fail_commit and self.commit_count == 0:
            raise RuntimeError("injected admin_role_commit_failure")
        self._session.commit()
        self.commit_count += 1

    async def rollback(self):
        self._session.rollback()


class MsgSession:
    def __init__(self, sync_session, *, fail_commit=False):
        self._session = sync_session
        self.commit_count = 0
        self.fail_commit = fail_commit

    def add(self, obj):
        self._session.add(obj)

    async def commit(self):
        if self.fail_commit and self.commit_count == 0:
            raise RuntimeError("injected send_message_commit_failure")
        self._session.commit()
        self.commit_count += 1

    async def refresh(self, obj, attribute_names=None):
        self._session.refresh(obj)

    async def get(self, model, ident, **kwargs):
        loaded = self._session.get(model, ident)
        created = getattr(loaded, "created_at", None)
        if loaded is not None and created is not None and created.tzinfo is None:
            loaded.created_at = created.replace(tzinfo=timezone.utc)
        return loaded

    async def rollback(self):
        self._session.rollback()


class CrudSession:
    """Async adapter over isolated sqlite. Does not mock the durable write."""

    def __init__(self, sync_session, *, fail_commit=False, fail_label="injected_crud_commit_failure"):
        self._session = sync_session
        self.fail_commit = fail_commit
        self.fail_label = fail_label
        self.commit_count = 0

    def add(self, obj):
        self._session.add(obj)

    async def flush(self):
        self._session.flush()

    async def execute(self, statement, params=None):
        return self._session.execute(statement)

    async def get(self, model, ident, **kwargs):
        return self._session.get(model, ident)

    async def delete(self, obj):
        self._session.delete(obj)

    async def commit(self):
        if self.fail_commit and self.commit_count == 0:
            raise RuntimeError(self.fail_label)
        self._session.commit()
        self.commit_count += 1

    async def refresh(self, obj, attribute_names=None):
        self._session.refresh(obj, attribute_names=attribute_names)

    async def rollback(self):
        self._session.rollback()


class _HttpResp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://isolated-commodities.local")
            raise httpx.HTTPStatusError("isolated-http-status", request=request, response=self)

    def json(self):
        return self._payload


def _isolated_commodity_client(db):
    class IsolatedCommodityClient:
        """Client-construction isolate: no socket; routes to live commodity callables."""

        def __init__(self, *args, **kwargs):
            self.db = db

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def _call(self, method, url, payload=None):
            parts = [item for item in urlparse(url).path.rstrip("/").split("/") if item]
            payload = payload or {}
            try:
                if method == "GET" and parts[-1] == "commodities":
                    items = await read_all_commodities(db=self.db)
                    return _HttpResp(
                        200,
                        [
                            {
                                "id": item.id,
                                "name": item.name,
                                "aliases": [
                                    {
                                        "id": alias.id,
                                        "alias": alias.alias,
                                        "commodity_id": alias.commodity_id,
                                    }
                                    for alias in (item.aliases or [])
                                ],
                            }
                            for item in items
                        ],
                    )
                if method == "GET" and parts[-2] == "commodities":
                    item = await read_commodity(int(parts[-1]), db=self.db)
                    return _HttpResp(
                        200,
                        {
                            "id": item.id,
                            "name": item.name,
                            "aliases": [
                                {
                                    "id": alias.id,
                                    "alias": alias.alias,
                                    "commodity_id": alias.commodity_id,
                                }
                                for alias in (item.aliases or [])
                            ],
                        },
                    )
                if method == "POST" and parts[-1] == "aliases":
                    created = await add_alias_to_commodity(
                        int(parts[-2]),
                        alias=schemas.CommodityAliasCreate(alias=payload["alias"]),
                        db=self.db,
                        source="bot",
                    )
                    return _HttpResp(201, {"id": created.id, "alias": created.alias})
                if method == "POST" and parts[-1] == "commodities":
                    created = await create_commodity(
                        commodity_data=schemas.CommodityCreate(name=payload["commodity_data"]["name"]),
                        aliases=payload.get("aliases") or [],
                        db=self.db,
                        source="bot",
                    )
                    return _HttpResp(201, {"id": created.id, "name": created.name})
                if method == "PUT" and parts[-2] == "aliases":
                    updated = await update_alias(
                        int(parts[-1]),
                        schemas.CommodityAliasCreate(alias=payload["alias"]),
                        db=self.db,
                        source="bot",
                    )
                    return _HttpResp(200, {"id": updated.id, "alias": updated.alias})
                if method == "PUT" and parts[-2] == "commodities":
                    updated = await update_commodity_name(
                        int(parts[-1]),
                        schemas.CommodityCreate(name=payload["name"]),
                        db=self.db,
                        source="bot",
                    )
                    return _HttpResp(200, {"id": updated.id, "name": updated.name})
                if method == "DELETE" and parts[-2] == "aliases":
                    await delete_alias(int(parts[-1]), db=self.db, source="bot")
                    return _HttpResp(204, {})
                if method == "DELETE" and parts[-2] == "commodities":
                    await delete_commodity(int(parts[-1]), db=self.db, source="bot")
                    return _HttpResp(204, {})
            except HTTPException as exc:
                return _HttpResp(exc.status_code, {"detail": exc.detail})
            return _HttpResp(404, {"detail": url})

        async def get(self, url, headers=None, timeout=None, **_kwargs):
            return await self._call("GET", url)

        async def post(self, url, json=None, headers=None, timeout=None, **_kwargs):
            return await self._call("POST", url, json)

        async def put(self, url, json=None, headers=None, timeout=None, **_kwargs):
            return await self._call("PUT", url, json)

        async def delete(self, url, headers=None, timeout=None, **_kwargs):
            return await self._call("DELETE", url)

    return IsolatedCommodityClient


class _SessionBox:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _engine(path: str):
    engine = create_engine(f"sqlite:///{path}", future=True)

    def _on_connect(conn, _rec):
        conn.execute("PRAGMA foreign_keys=OFF")

    event.listen(engine, "connect", _on_connect)
    return engine


def _admin_crud_store(path: str):
    engine = _engine(path)
    Commodity.__table__.create(engine, checkfirst=True)
    CommodityAlias.__table__.create(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS offers (id INTEGER PRIMARY KEY, commodity_id INTEGER, status VARCHAR)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, commodity_id INTEGER)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY)"))
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS market_schedule_overrides ("
                "id INTEGER PRIMARY KEY, date DATE NOT NULL, override_type VARCHAR NOT NULL, "
                "open_time_local TIME, close_time_local TIME, note VARCHAR(255), "
                "created_by_user_id INTEGER, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS trading_settings ("
                "key VARCHAR(100) PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS admin_market_messages ("
                "id INTEGER PRIMARY KEY, content TEXT NOT NULL, created_by_id INTEGER NOT NULL, "
                "reused_from_id INTEGER, is_active BOOLEAN NOT NULL DEFAULT 1, "
                "notified_recipients_count INTEGER NOT NULL DEFAULT 0, "
                "published_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME)"
            )
        )
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return engine, Session


def _bot_commodity_patches(db):
    return [
        patch("bot.handlers.admin_commodities.httpx.AsyncClient", _isolated_commodity_client(db)),
        patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()),
        patch(
            "bot.handlers.admin_commodities.answer_incoming_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=70)),
        ),
        patch(
            "bot.handlers.admin_commodities.edit_callback_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=71)),
        ),
        patch("bot.handlers.admin_commodities.edit_interaction_result_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.update_anchor", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.answer_callback_query_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.show_aliases_list", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.show_commodity_list", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()),
    ]


class UsersListSession:
    """Session-construction isolate: live list SQL shape with in-memory rows."""

    def __init__(self, users):
        self.users = list(users)

    async def execute(self, *_a, **_k):
        visible = [item for item in self.users if not getattr(item, "is_deleted", False)]
        return SimpleNamespace(
            scalar=lambda: len(visible),
            scalars=lambda: SimpleNamespace(all=lambda: list(visible)),
        )


def _bot_commodity_nav_patches(db, sent):
    async def _send(*_a, **kwargs):
        sent.append(kwargs.get("text") or (_a[1] if len(_a) > 1 else ""))
        return SimpleNamespace(message_id=70 + len(sent))

    return [
        patch("bot.handlers.admin_commodities.httpx.AsyncClient", _isolated_commodity_client(db)),
        patch("bot.handlers.admin_commodities.delete_user_message", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.update_anchor", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.answer_callback_query_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin_commodities.asyncio.sleep", new=AsyncMock()),
        patch("core.utils.redis.Redis", IsolatedAsyncRedis),
        patch("core.redis.get_redis_client", side_effect=RuntimeError("isolated-redis")),
        *_isolated_market_patches()[1:],
    ] + [
        patch(
            "bot.handlers.admin_commodities.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ),
    ]


def _capture_bot(sent):
    async def _send(*_a, **kwargs):
        sent.append(kwargs.get("text") or (_a[1] if len(_a) > 1 else ""))
        return SimpleNamespace(message_id=80 + len(sent))

    async def _edit(*_a, **kwargs):
        sent.append(kwargs.get("text") or (_a[2] if len(_a) > 2 else ""))
        return SimpleNamespace(message_id=90 + len(sent))

    return SimpleNamespace(send_message=_send, edit_message_text=_edit)


def _market_message_store(path: str):
    engine, Session = _admin_crud_store(path)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS users"))
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY, account_name VARCHAR NOT NULL, mobile_number VARCHAR NOT NULL, "
                "normalized_account_name VARCHAR NOT NULL, normalized_mobile_number VARCHAR NOT NULL, "
                "telegram_id INTEGER, username VARCHAR, full_name VARCHAR NOT NULL, address TEXT NOT NULL, "
                "avatar_file_id VARCHAR, role VARCHAR NOT NULL, account_status VARCHAR NOT NULL, "
                "deactivated_at DATETIME, messenger_grace_expires_at DATETIME, messenger_blocked_at DATETIME, "
                "has_bot_access BOOLEAN NOT NULL DEFAULT 1, bot_onboarding_required_step INTEGER NOT NULL DEFAULT 0, "
                "bot_onboarding_completed_step INTEGER NOT NULL DEFAULT 0, bot_onboarding_completed_at DATETIME, "
                "is_deleted BOOLEAN NOT NULL DEFAULT 0, deleted_at DATETIME, admin_password_hash VARCHAR, "
                "must_change_password BOOLEAN NOT NULL DEFAULT 0, offer_overtime_minutes INTEGER NOT NULL DEFAULT 0, "
                "trading_restricted_until DATETIME, max_daily_trades INTEGER, max_active_commodities INTEGER, "
                "max_daily_requests INTEGER, limitations_expire_at DATETIME, trades_count INTEGER NOT NULL DEFAULT 0, "
                "commodities_traded_count INTEGER NOT NULL DEFAULT 0, channel_messages_count INTEGER NOT NULL DEFAULT 0, "
                "counter_epoch INTEGER NOT NULL DEFAULT 1, max_sessions INTEGER NOT NULL DEFAULT 1, "
                "max_accountants INTEGER NOT NULL DEFAULT 3, max_customers INTEGER NOT NULL DEFAULT 5, "
                "home_server VARCHAR NOT NULL DEFAULT 'foreign', sync_version INTEGER NOT NULL DEFAULT 1, "
                "can_block_users BOOLEAN DEFAULT 1, max_blocked_users INTEGER DEFAULT 10, "
                "last_seen_at DATETIME, created_at DATETIME, updated_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS accountant_relations ("
                "id INTEGER PRIMARY KEY, owner_user_id INTEGER, accountant_user_id INTEGER, "
                "status VARCHAR, deleted_at DATETIME)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id, account_name, mobile_number, normalized_account_name, "
                "normalized_mobile_number, full_name, address, role, account_status, is_deleted) VALUES "
                "(1, 'admin', '09120000001', 'admin', '09120000001', 'Admin', 'a', 'SUPER_ADMIN', 'active', 0), "
                "(2, 'buyer', '09120000002', 'buyer', '09120000002', 'Buyer', 'b', 'STANDARD', 'active', 0)"
            )
        )
    Notification.__table__.create(engine, checkfirst=True)
    return engine, Session


def _isolated_market_patches():
    return [
        patch("core.utils.redis.Redis", IsolatedAsyncRedis),
        patch("core.events._get_sync_redis", return_value=IsolatedSyncRedis()),
        patch("core.redis.get_redis_client", side_effect=RuntimeError("isolated-redis")),
    ]


class EmptyQuerySession:
    """Session-construction isolate: live SQL runs against an empty result set."""

    async def execute(self, *args, **kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))


def _broadcast_flow_patches():
    return [
        patch(
            "bot.handlers.admin_broadcast.answer_incoming_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=90)),
        ),
        patch(
            "bot.handlers.admin_broadcast.edit_callback_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=91)),
        ),
        patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin_broadcast.AsyncSessionLocal", return_value=_SessionBox(EmptyQuerySession())),
    ]


_CUSTOMER_RELATIONS_SQL = (
    "CREATE TABLE customer_relations ("
    "id INTEGER PRIMARY KEY, owner_user_id INTEGER NOT NULL, customer_user_id INTEGER, "
    "created_by_user_id INTEGER, invitation_token VARCHAR NOT NULL DEFAULT 'tok', "
    "management_name VARCHAR(120) NOT NULL DEFAULT 'n', customer_tier VARCHAR NOT NULL DEFAULT 'tier1', "
    "commission_rate NUMERIC(5,2), min_trade_quantity INTEGER, max_trade_quantity INTEGER, "
    "max_daily_trades INTEGER, max_daily_commodity_volume INTEGER, trading_restricted_until DATETIME, "
    "status VARCHAR NOT NULL DEFAULT 'active', expires_at DATETIME, activated_at DATETIME, "
    "deleted_at DATETIME, created_at DATETIME, updated_at DATETIME, sync_version INTEGER NOT NULL DEFAULT 1)"
)


def _group_chat_store(path: str):
    engine, Session = _market_message_store(path)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS customer_relations"))
        conn.execute(text(_CUSTOMER_RELATIONS_SQL))
    Chat.__table__.create(engine, checkfirst=True)
    ChatMember.__table__.create(engine, checkfirst=True)
    return engine, Session


def _load_store_user(Session, user_id=1):
    sess = Session()
    try:
        return sess.get(User, user_id)
    finally:
        sess.close()


def _management_broadcast_store(path: str):
    engine, Session = _market_message_store(path)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS customer_relations ("
                "id INTEGER PRIMARY KEY, customer_user_id INTEGER, status VARCHAR, deleted_at DATETIME)"
            )
        )
    Chat.__table__.create(engine, checkfirst=True)
    ChatMember.__table__.create(engine, checkfirst=True)
    Message.__table__.create(engine, checkfirst=True)
    AdminBroadcastMessage.__table__.create(engine, checkfirst=True)
    return engine, Session


def _invitation_patches():
    return [
        patch(
            "bot.handlers.admin.answer_incoming_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=80)),
        ),
        patch(
            "bot.handlers.admin.edit_callback_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=81)),
        ),
        patch("bot.handlers.admin.answer_callback_query_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin.answer_callback_message_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin._return_to_admin_panel", new=AsyncMock()),
        patch(
            "bot.handlers.admin.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ),
        patch("bot.handlers.admin.get_commodity_fsm_cancel_keyboard", return_value="KB"),
        patch("bot.handlers.admin.get_role_selection_keyboard", return_value="ROLE-KB"),
    ]


def _user_admin_display_patches():
    return [
        patch("bot.handlers.admin_users.answer_callback_query_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin_users.edit_callback_message_via_runtime", new=AsyncMock()),
        patch("bot.handlers.admin_users.get_user_profile_text", new=AsyncMock(return_value="profile")),
        patch("bot.handlers.admin_users.get_user_role_edit_keyboard", return_value="ROLE-KB"),
        patch("bot.handlers.admin_users.get_max_block_options_keyboard", return_value="MAX-KB"),
        patch("bot.handlers.admin_users.get_block_settings_keyboard", return_value="BLOCK-KB"),
        patch("bot.handlers.admin_users.get_user_settings_keyboard", return_value="SET-KB"),
        patch("bot.handlers.admin_users.get_user_delete_webapp_redirect_keyboard", return_value="DEL-KB"),
        patch("bot.handlers.admin_users.get_user_profile_return_keyboard", return_value="RET-KB"),
        patch("bot.handlers.admin_users.get_limit_duration_keyboard", return_value="DUR-KB"),
        patch("bot.handlers.admin_users.delete_user_message", new=AsyncMock()),
        patch("bot.handlers.admin_users.update_anchor", new=AsyncMock()),
        patch(
            "bot.handlers.admin_users.answer_incoming_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=92)),
        ),
        patch(
            "bot.handlers.admin_users.answer_callback_message_via_runtime",
            new=AsyncMock(return_value=SimpleNamespace(message_id=93)),
        ),
        patch(
            "bot.handlers.admin_users.build_users_management_navigation_keyboard",
            new=AsyncMock(return_value="USERS-KB"),
        ),
        patch("bot.handlers.admin_users.attach_customer_management_names", new=AsyncMock()),
        patch("bot.handlers.admin_users.schedule_temporary_message_cleanup", new=AsyncMock()),
    ]


def _user_admin_nav_patches():
    return _user_admin_display_patches() + [
        patch("bot.handlers.admin_users.attach_user_management_relation_context", new=AsyncMock()),
        patch("bot.handlers.admin_users.clear_state_retain_anchors", new=AsyncMock()),
        patch("bot.handlers.admin_users.safe_delete_message", new=AsyncMock()),
        patch(
            "bot.handlers.admin_users.build_admin_panel_navigation_keyboard",
            new=AsyncMock(return_value="ADMIN-KB"),
        ),
        patch("bot.handlers.admin_users.get_users_list_inline_keyboard", return_value="LIST-KB"),
        patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ),
    ]


def _commodity_message(text: str):
    return SimpleNamespace(text=text, bot=SimpleNamespace(), chat=SimpleNamespace(id=1))


def _commodity_callback(data: str):
    return SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        bot=SimpleNamespace(),
        message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=10),
    )


def _broadcast_callback(data: str):
    return SimpleNamespace(
        data=data,
        id="cb-broadcast",
        from_user=SimpleNamespace(id=1),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=200),
            message_id=50,
            edit_reply_markup=AsyncMock(),
        ),
    )


def _trade_callback():
    return SimpleNamespace(
        id="cb-trade-slice",
        from_user=SimpleNamespace(id=300),
        answer=AsyncMock(),
        message=SimpleNamespace(
            chat=SimpleNamespace(id=200),
            message_id=50,
            edit_reply_markup=AsyncMock(),
        ),
    )


def _trade_user(**overrides):
    data = {
        "id": 5,
        "telegram_id": 555,
        "mobile_number": "0935",
        "account_name": "buyer",
        "role": UserRole.STANDARD,
        "account_status": UserAccountStatus.ACTIVE,
        "is_deleted": False,
        "trading_restricted_until": None,
        "limitations_expire_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _trade_offer(**overrides):
    data = {
        "id": 7,
        "user_id": 2,
        "status": OfferStatus.ACTIVE,
        "home_server": "foreign",
        "remaining_quantity": 10,
        "quantity": 10,
        "is_wholesale": False,
        "lot_sizes": None,
        "offer_public_id": "ofr_slice",
        "user": SimpleNamespace(id=2),
        "commodity": SimpleNamespace(id=1),
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class P100bSeedSliceExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_status_seed_commits_then_denies_without_write(self):
        from tests.test_p1_00_characterization_admin_account_status import (
            P100AdminAccountStatusCharacterizationTests,
        )

        helper = P100AdminAccountStatusCharacterizationTests()
        await helper.test_handler_uses_live_transition_then_may_commit_again()
        await helper.test_handler_denied_paths_do_not_write()

    async def test_broadcast_toggle_group_recipient_flips_fsm_and_denies_injects(self):
        state = FakeState(target_groups=[])
        answers = []
        markup_calls = {"n": 0}

        async def _answer(callback, text=None, show_alert=False):
            answers.append((text, show_alert))

        async def _markup(*_a, **_k):
            markup_calls["n"] += 1

        callback = _broadcast_callback(f"tgb:gt:{TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY}")
        with patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new=_answer), patch(
            "bot.handlers.admin_broadcast.edit_callback_reply_markup_via_runtime",
            new=_markup,
        ):
            await toggle_group_recipient(
                callback,
                state,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertEqual(state.data.get("target_groups"), [])
            self.assertEqual(state.updates, 0)
            self.assertEqual(answers[-1][0], "عدم دسترسی")

            admin = SimpleNamespace(role=UserRole.SUPER_ADMIN)
            await toggle_group_recipient(callback, state, user=admin)
            self.assertEqual(state.data["target_groups"], [TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY])
            await toggle_group_recipient(callback, state, user=admin)
            self.assertEqual(state.data["target_groups"], [])
            await toggle_group_recipient(
                _broadcast_callback("tgb:gt:not-a-group"),
                FakeState(target_groups=[]),
                user=admin,
            )
        self.assertEqual(markup_calls["n"], 2)
        self.assertTrue(any(text == "گروه نامعتبر است." for text, _alert in answers))

    async def test_broadcast_toggle_selected_user_flips_fsm_and_denies_injects(self):
        state = FakeState(selected_user_ids=[], last_search_query="")
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append((text, show_alert))

        callback = _broadcast_callback("tgb:tu:9")
        with patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new=_answer), patch(
            "bot.handlers.admin_broadcast.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_broadcast.search_telegram_admin_broadcast_recipients",
            new=AsyncMock(side_effect=AssertionError("search reached without query")),
        ):
            await toggle_selected_user(
                callback,
                state,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertEqual(state.data.get("selected_user_ids"), [])
            self.assertEqual(state.updates, 0)

            admin = SimpleNamespace(role=UserRole.SUPER_ADMIN)
            await toggle_selected_user(callback, state, user=admin)
            self.assertEqual(state.data["selected_user_ids"], [9])
            await toggle_selected_user(callback, state, user=admin)
            self.assertEqual(state.data["selected_user_ids"], [])
            await toggle_selected_user(_broadcast_callback("tgb:tu:bad"), FakeState(), user=admin)
        self.assertTrue(any(text == "کاربر نامعتبر است." for text, _alert in answers))

    async def test_send_message_persists_publishes_retries_and_fails_before_commit_injects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "msg-send.sqlite")
            engine = _engine(db_path)
            Message.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sender = SimpleNamespace(id=1)
            receiver = SimpleNamespace(id=9)
            published: list[tuple] = []
            thread_calls = {"n": 0}

            async def _prepare(_db, **_k):
                return receiver, "hello-seed"

            async def _thread(*_a, **_k):
                thread_calls["n"] += 1

            async def _reload(_db, message_id, **_k):
                return _db._session.get(Message, message_id)

            async def _serialize(_db, message, viewer_user_id=None):
                return SimpleNamespace(id=message.id, content=message.content, sender_id=sender.id)

            async def _ident(*_a, **_k):
                return {}

            async def _name(*_a, **_k):
                return "alice"

            def _serializer(_identity_map, _serialized):
                return lambda message: {"id": message.id, "content": message.content}

            async def _publish(user_id, event, payload):
                published.append((user_id, event, payload))

            fail_db = MsgSession(Session(), fail_commit=True)
            with patch("api.routers.chat.prepare_direct_message_send", new=_prepare), patch(
                "core.services.chat_service.sync_direct_message_threading",
                new=_thread,
            ), patch(
                "core.services.chat_service.reload_direct_message",
                new=_reload,
            ), patch(
                "api.routers.chat._serialize_direct_message_with_accountant_contract",
                new=_serialize,
            ), patch(
                "api.routers.chat.load_accountant_chat_identity_map",
                new=_ident,
            ), patch(
                "api.routers.chat.resolve_direct_sender_display_name",
                new=_name,
            ), patch(
                "api.routers.chat._build_direct_message_accountant_serializer",
                new=_serializer,
            ), patch(
                "api.routers.chat.publish_user_event",
                new=_publish,
            ):
                with self.assertRaisesRegex(RuntimeError, "injected send_message_commit_failure"):
                    await send_message(
                        MessageSend(receiver_id=9, content="hello-seed"),
                        current_user=sender,
                        db=fail_db,
                    )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)
                self.assertEqual(published, [])

                ok_db = MsgSession(Session())
                first = await send_message(
                    MessageSend(receiver_id=9, content="hello-seed"),
                    current_user=sender,
                    db=ok_db,
                )
                self.assertEqual(first.id, 1)
                self.assertEqual(published[-1][0], 9)
                self.assertEqual(published[-1][1], "chat:message")
                self.assertGreaterEqual(ok_db.commit_count, 1)

                again = await send_message(
                    MessageSend(receiver_id=9, content="hello-seed"),
                    current_user=sender,
                    db=ok_db,
                )
                self.assertEqual(again.id, 2)
                self.assertEqual(len(published), 2)
                self.assertEqual(thread_calls["n"], 2)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 2)
            engine.dispose()

    async def test_set_user_role_persists_then_denies_and_fails_before_commit_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync, role=ModelUserRole.STANDARD)
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(
            data="set_user_role_9_MIDDLE_MANAGER",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        with patch("core.admin_authority.current_server", return_value="iran"), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_profile_return_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ):
            await handle_set_user_role(
                callback,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            reloaded, fresh = _reload_user(Session)
            self.assertEqual(reloaded.role, ModelUserRole.STANDARD)
            fresh.close()

            fail_db = RoleSession(sync, fail_commit=True)
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(fail_db)):
                with self.assertRaisesRegex(RuntimeError, "injected admin_role_commit_failure"):
                    await handle_set_user_role(
                        callback,
                        user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    )
            failed, failed_fresh = _reload_user(Session)
            self.assertEqual(failed.role, ModelUserRole.STANDARD)
            failed_fresh.close()

            ok_db = RoleSession(sync)
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(ok_db)):
                await handle_set_user_role(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
                await handle_set_user_role(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            changed, later = _reload_user(Session)
            self.assertEqual(changed.role, ModelUserRole.MIDDLE_MANAGER)
            self.assertGreaterEqual(ok_db.commit_count, 2)
            self.assertTrue(any("نقش کاربر تغییر کرد" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_user_unblock_clears_restriction_then_denies_without_write_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        until = datetime.utcnow() + timedelta(days=2)
        sync.get(MappedAccountUser, 9).trading_restricted_until = until
        sync.commit()
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(
            data="user_unblock_9",
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ), patch(
            "bot.handlers.admin_users.send_delayed_removal_notification",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.asyncio.create_task",
            side_effect=lambda coro: coro.close() or SimpleNamespace(),
        ):
            await handle_user_unblock(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            still, peek = _reload_user(Session)
            self.assertEqual(still.trading_restricted_until, until)
            peek.close()

            with patch("core.admin_authority.current_server", return_value="foreign"):
                await handle_user_unblock(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            blocked, blocked_fresh = _reload_user(Session)
            self.assertEqual(blocked.trading_restricted_until, until)
            blocked_fresh.close()

            with patch("core.admin_authority.current_server", return_value="iran"):
                await handle_user_unblock(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            cleared, later = _reload_user(Session)
            self.assertIsNone(cleared.trading_restricted_until)
            self.assertGreaterEqual(db.commit_count, 1)
            self.assertTrue(any("رفع مسدودیت" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_confirm_telegram_admin_broadcast_persists_then_replays_key_injects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "broadcast-confirm.sqlite")
            engine = _overlap_engine(db_path)
            _exec_sql(engine, ADMIN_SQL)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            creation_key = "p1-00b-broadcast-confirm-key"
            state = FakeState(
                content="hello-seed-broadcast",
                audience_type=TelegramAdminBroadcastAudienceType.ALL.value,
                target_groups=[],
                selected_user_ids=[],
                creation_key=creation_key,
                confirm_completed=False,
            )
            published = {"n": 0}

            async def _publish(*_a, **_k):
                published["n"] += 1

            fail_db = YieldingSession(Session())
            original_commit = fail_db.commit

            async def _fail_once():
                raise RuntimeError("injected broadcast_confirm_commit_failure")

            fail_db.commit = _fail_once
            with patch(
                "core.services.telegram_admin_broadcast_service.resolve_telegram_admin_broadcast_recipients",
                new=AsyncMock(return_value=()),
            ), patch(
                "bot.handlers.admin_broadcast.answer_callback_query_via_runtime",
                new=AsyncMock(),
            ), patch(
                "bot.handlers.admin_broadcast.edit_callback_message_via_runtime",
                new=AsyncMock(),
            ), patch(
                "bot.handlers.admin_broadcast._publish_confirm_result",
                new=_publish,
            ), patch(
                "bot.handlers.admin_broadcast._load_broadcast_by_creation_key",
                new=AsyncMock(return_value=None),
            ):
                await confirm_telegram_admin_broadcast(
                    _broadcast_callback("tgb:confirm"),
                    FakeState(**state.data),
                    user=SimpleNamespace(id=1, role=UserRole.STANDARD),
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM telegram_admin_broadcasts").fetchone()[0],
                        0,
                    )

                with patch(
                    "bot.handlers.admin_broadcast.AsyncSessionLocal",
                    return_value=_SessionBox(fail_db),
                ):
                    await confirm_telegram_admin_broadcast(
                        _broadcast_callback("tgb:confirm"),
                        FakeState(**state.data),
                        user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                    )
                await fail_db.rollback()
                fail_db.close()
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(
                        conn.execute("SELECT COUNT(*) FROM telegram_admin_broadcasts").fetchone()[0],
                        0,
                    )

                ok_db = YieldingSession(Session())
                with patch(
                    "bot.handlers.admin_broadcast.AsyncSessionLocal",
                    return_value=_SessionBox(ok_db),
                ):
                    await confirm_telegram_admin_broadcast(
                        _broadcast_callback("tgb:confirm"),
                        state,
                        user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                    )
                    await confirm_telegram_admin_broadcast(
                        _broadcast_callback("tgb:confirm"),
                        FakeState(confirm_completed=True, creation_key=creation_key),
                        user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                    )
            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM telegram_admin_broadcasts").fetchone()[0]
                key = conn.execute(
                    "SELECT creation_key FROM telegram_admin_broadcasts"
                ).fetchone()[0]
            self.assertEqual(count, 1)
            self.assertEqual(key, creation_key)
            self.assertEqual(published["n"], 1)
            fail_db.commit = original_commit
            engine.dispose()

    async def test_user_unlimit_clears_caps_then_denies_without_write_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        row = sync.get(MappedAccountUser, 9)
        row.max_daily_trades = 2
        row.max_active_commodities = 3
        row.max_daily_requests = 4
        row.limitations_expire_at = datetime.utcnow() + timedelta(days=1)
        sync.commit()
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(data="user_unlimit_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ), patch(
            "bot.handlers.admin_users.send_delayed_removal_notification",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.asyncio.create_task",
            side_effect=lambda coro: coro.close() or SimpleNamespace(),
        ):
            await handle_user_unlimit(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            still, peek = _reload_user(Session)
            self.assertEqual(still.max_daily_trades, 2)
            peek.close()
            with patch("core.admin_authority.current_server", return_value="iran"):
                await handle_user_unlimit(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            cleared, later = _reload_user(Session)
            self.assertIsNone(cleared.max_daily_trades)
            self.assertIsNone(cleared.max_active_commodities)
            self.assertIsNone(cleared.max_daily_requests)
            self.assertIsNone(cleared.limitations_expire_at)
            self.assertTrue(any("محدودیت‌ها برداشته شد" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_admin_max_block_set_persists_then_denies_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(data="admin_max_block_set_9_10", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "core.admin_authority.current_server",
            return_value="iran",
        ):
            await handle_admin_max_block_set(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            still, peek = _reload_user(Session)
            self.assertEqual(still.max_blocked_users, 5)
            peek.close()
            await handle_admin_max_block_set(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            changed, later = _reload_user(Session)
            self.assertEqual(changed.max_blocked_users, 10)
            self.assertTrue(any("سقف بلاک به 10" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_user_block_actions_persists_restriction_then_denies_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(data="user_block_apply_9_30", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ), patch(
            "bot.handlers.admin_users.send_telegram_notification",
            new=AsyncMock(),
        ):
            await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            still, peek = _reload_user(Session)
            self.assertIsNone(still.trading_restricted_until)
            peek.close()
            with patch("core.admin_authority.current_server", return_value="iran"):
                await handle_user_block_actions(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
            blocked, later = _reload_user(Session)
            self.assertIsNotNone(blocked.trading_restricted_until)
            self.assertGreaterEqual(later.query(Notification).count(), 1)
            later.close()
        sync.close()
        engine.dispose()

    async def test_limit_confirm_persists_caps_then_denies_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        expire_at = datetime.utcnow() + timedelta(days=3)
        state = FakeState(
            limit_target_user_id=9,
            limit_expire_at=expire_at,
            limit_max_trades=4,
            limit_max_commodities=2,
            limit_max_requests=1,
        )
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        callback = SimpleNamespace(data="limit_confirm_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.configured_telegram_delivery_runtime",
            return_value=SimpleNamespace(mode="isolated"),
        ), patch(
            "bot.handlers.admin_users.send_telegram_notification",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors",
            new=AsyncMock(),
        ):
            await handle_limit_confirm(
                callback,
                user=SimpleNamespace(role=UserRole.STANDARD),
                state=FakeState(**state.data),
            )
            still, peek = _reload_user(Session)
            self.assertIsNone(still.max_daily_trades)
            peek.close()
            with patch("core.admin_authority.current_server", return_value="iran"):
                await handle_limit_confirm(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=state,
                )
            limited, later = _reload_user(Session)
            self.assertEqual(limited.max_daily_trades, 4)
            self.assertEqual(limited.max_active_commodities, 2)
            self.assertEqual(limited.max_daily_requests, 1)
            self.assertGreaterEqual(later.query(Notification).count(), 1)
            later.close()
        sync.close()
        engine.dispose()

    async def test_update_message_edits_then_denies_and_fails_injects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "msg-update.sqlite")
            engine = _engine(db_path)
            Message.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sync = Session()
            now = datetime.now(timezone.utc)
            sync.add(
                Message(
                    sender_id=1,
                    receiver_id=9,
                    content="old-text",
                    message_type=MessageType.TEXT,
                    is_read=False,
                    created_at=now,
                )
            )
            sync.commit()
            sender = SimpleNamespace(id=1)

            async def _reload(_db, message_id, **_k):
                return _db._session.get(Message, message_id)

            async def _serialize(_db, message, viewer_user_id=None):
                return SimpleNamespace(id=message.id, content=message.content)

            fail_db = MsgSession(Session(), fail_commit=True)
            with patch(
                "core.services.chat_service.ensure_direct_message_chat_link",
                new=AsyncMock(),
            ), patch(
                "core.services.chat_service.reload_direct_message",
                new=_reload,
            ), patch(
                "api.routers.chat._serialize_direct_message_with_accountant_contract",
                new=_serialize,
            ):
                with self.assertRaisesRegex(RuntimeError, "injected send_message_commit_failure"):
                    await update_message(1, MessageUpdate(content="new-text"), current_user=sender, db=fail_db)
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT content FROM messages WHERE id=1").fetchone()[0], "old-text")
                from fastapi import HTTPException

                ok_db = MsgSession(Session())
                with self.assertRaises(HTTPException) as denied:
                    await update_message(
                        1,
                        MessageUpdate(content="new-text"),
                        current_user=SimpleNamespace(id=9),
                        db=ok_db,
                    )
                self.assertEqual(denied.exception.status_code, 403)
                updated = await update_message(
                    1,
                    MessageUpdate(content="new-text"),
                    current_user=sender,
                    db=ok_db,
                )
                self.assertEqual(updated.content, "new-text")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT content FROM messages WHERE id=1").fetchone()[0], "new-text")
            sync.close()
            engine.dispose()

    async def test_delete_message_soft_deletes_then_denies_injects(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "msg-delete.sqlite")
            engine = _engine(db_path)
            Message.__table__.create(engine, checkfirst=True)
            Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
            sync = Session()
            now = datetime.now(timezone.utc)
            sync.add(
                Message(
                    sender_id=1,
                    receiver_id=9,
                    content="to-delete",
                    message_type=MessageType.TEXT,
                    is_read=False,
                    created_at=now,
                    is_deleted=False,
                )
            )
            sync.commit()
            sender = SimpleNamespace(id=1)

            async def _reload(_db, message_id, **_k):
                return _db._session.get(Message, message_id)

            ok_db = MsgSession(Session())
            with patch(
                "core.services.chat_service.ensure_direct_message_chat_link",
                new=AsyncMock(),
            ), patch(
                "core.services.chat_service.reload_direct_message",
                new=_reload,
            ):
                from fastapi import HTTPException

                with self.assertRaises(HTTPException) as denied:
                    await delete_message(1, current_user=SimpleNamespace(id=9), db=ok_db)
                self.assertEqual(denied.exception.status_code, 403)
                await delete_message(1, current_user=sender, db=ok_db)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT is_deleted FROM messages WHERE id=1").fetchone()[0], 1)
            sync.close()
            engine.dispose()

    async def test_process_custom_max_block_persists_then_denies_injects(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        answers = []

        async def _answer(message, user, text=None, **_k):
            answers.append(text or "")
            return SimpleNamespace(message_id=70)

        message = SimpleNamespace(
            text="18",
            bot=SimpleNamespace(),
            chat=SimpleNamespace(id=1),
        )
        state = FakeState(custom_max_block_user_id=9, anchor_id=1)
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.delete_user_message",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.answer_incoming_message_via_runtime",
            new=_answer,
        ), patch(
            "bot.handlers.admin_users.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard",
            return_value="KB",
        ), patch(
            "bot.handlers.admin_users.update_anchor",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors",
            new=AsyncMock(),
        ), patch(
            "core.admin_authority.current_server",
            return_value="iran",
        ):
            await process_custom_max_block(
                message,
                user=SimpleNamespace(role=UserRole.STANDARD),
                state=FakeState(custom_max_block_user_id=9, anchor_id=1),
            )
            still, peek = _reload_user(Session)
            self.assertEqual(still.max_blocked_users, 5)
            peek.close()
            await process_custom_max_block(
                SimpleNamespace(text="not-a-number", bot=SimpleNamespace(), chat=SimpleNamespace(id=1)),
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                state=FakeState(custom_max_block_user_id=9, anchor_id=1),
            )
            invalid, peek2 = _reload_user(Session)
            self.assertEqual(invalid.max_blocked_users, 5)
            peek2.close()
            await process_custom_max_block(
                message,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                state=state,
            )
            changed, later = _reload_user(Session)
            self.assertEqual(changed.max_blocked_users, 18)
            self.assertTrue(any("سقف بلاک: 18" in text for text in answers))
            later.close()
        sync.close()
        engine.dispose()

    async def test_create_commodity_persists_then_rejects_digits_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "commodity-create.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as digits:
                await create_commodity(
                    commodity_data=schemas.CommodityCreate(name="ربع403"),
                    aliases=[],
                    db=db,
                    source="bot",
                )
            self.assertEqual(digits.exception.status_code, 400)
            created = await create_commodity(
                commodity_data=schemas.CommodityCreate(name="ربع"),
                aliases=["ربع طلا"],
                db=db,
                source="bot",
            )
            self.assertEqual(created.name, "ربع")
            with self.assertRaises(HTTPException) as conflict:
                await create_commodity(
                    commodity_data=schemas.CommodityCreate(name="ربع"),
                    aliases=[],
                    db=db,
                    source="bot",
                )
            self.assertEqual(conflict.exception.status_code, 409)
            with sqlite3.connect(db_path) as conn:
                names = [row[0] for row in conn.execute("SELECT name FROM commodities").fetchall()]
                aliases = [row[0] for row in conn.execute("SELECT alias FROM commodity_aliases").fetchall()]
            self.assertEqual(names, ["ربع"])
            self.assertEqual(aliases, ["ربع طلا"])
            engine.dispose()

    async def test_update_commodity_name_persists_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "commodity-update.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await update_commodity_name(
                    99,
                    schemas.CommodityCreate(name="نیم"),
                    db=db,
                    source="bot",
                )
            self.assertEqual(missing.exception.status_code, 404)
            updated = await update_commodity_name(
                1,
                schemas.CommodityCreate(name="نیم"),
                db=db,
                source="bot",
            )
            self.assertEqual(updated.name, "نیم")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT name FROM commodities WHERE id=1").fetchone()[0], "نیم")
            sync.close()
            engine.dispose()

    async def test_delete_commodity_removes_then_404s_and_locks_imam(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "commodity-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.add(Commodity(name=IMAM_COMMODITY_NAME))
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await delete_commodity(99, db=db, source="bot")
            self.assertEqual(missing.exception.status_code, 404)
            with self.assertRaises(HTTPException) as locked:
                await delete_commodity(2, db=db, source="bot")
            self.assertEqual(locked.exception.status_code, 409)
            await delete_commodity(1, db=db, source="bot")
            with sqlite3.connect(db_path) as conn:
                names = [row[0] for row in conn.execute("SELECT name FROM commodities").fetchall()]
            self.assertEqual(names, [IMAM_COMMODITY_NAME])
            sync.close()
            engine.dispose()

    async def test_add_alias_to_commodity_persists_then_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "alias-add.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.add(Commodity(name="نیم"))
            sync.commit()
            db = CrudSession(Session())
            added = await add_alias_to_commodity(
                1,
                alias=schemas.CommodityAliasCreate(alias="ربع طلا"),
                db=db,
                source="bot",
            )
            self.assertEqual(added.alias, "ربع طلا")
            with self.assertRaises(HTTPException) as conflict:
                await add_alias_to_commodity(
                    1,
                    alias=schemas.CommodityAliasCreate(alias="نیم"),
                    db=db,
                    source="bot",
                )
            self.assertEqual(conflict.exception.status_code, 409)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT alias FROM commodity_aliases WHERE commodity_id=1").fetchone()[0],
                    "ربع طلا",
                )
            sync.close()
            engine.dispose()

    async def test_update_alias_persists_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "alias-update.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await update_alias(
                    99,
                    schemas.CommodityAliasCreate(alias="نیم طلا"),
                    db=db,
                    source="bot",
                )
            self.assertEqual(missing.exception.status_code, 404)
            updated = await update_alias(
                1,
                schemas.CommodityAliasCreate(alias="نیم طلا"),
                db=db,
                source="bot",
            )
            self.assertEqual(updated.alias, "نیم طلا")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT alias FROM commodity_aliases WHERE id=1").fetchone()[0], "نیم طلا")
            sync.close()
            engine.dispose()

    async def test_delete_alias_removes_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "alias-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await delete_alias(99, db=db, source="bot")
            self.assertEqual(missing.exception.status_code, 404)
            await delete_alias(1, db=db, source="bot")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodity_aliases").fetchone()[0], 0)
            sync.close()
            engine.dispose()

    async def test_create_market_override_persists_then_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "override-create.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            actor = SimpleNamespace(id=1)
            payload = MarketScheduleOverrideUpsert(
                date="2026-09-10",
                override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                note="تعطیل",
            )
            with self.assertRaises(HTTPException) as bad_date:
                await create_market_override(
                    MarketScheduleOverrideUpsert(
                        date="not-a-date",
                        override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                    ),
                    db=db,
                    current_user=actor,
                )
            self.assertEqual(bad_date.exception.status_code, 400)
            created = await create_market_override(payload, db=db, current_user=actor)
            self.assertEqual(created.date, "2026-09-10")
            with self.assertRaises(HTTPException) as duplicate:
                await create_market_override(payload, db=db, current_user=actor)
            self.assertEqual(duplicate.exception.status_code, 400)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_schedule_overrides").fetchone()[0], 1)
            engine.dispose()

    async def test_update_market_override_persists_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "override-update.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(
                MarketScheduleOverride(
                    date=date(2026, 9, 10),
                    override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                    created_by_user_id=1,
                    created_at=datetime.now(timezone.utc),
                )
            )
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await update_market_override(
                    99,
                    MarketScheduleOverrideUpsert(
                        date="2026-09-11",
                        override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
                    ),
                    db=db,
                )
            self.assertEqual(missing.exception.status_code, 404)
            updated = await update_market_override(
                1,
                MarketScheduleOverrideUpsert(
                    date="2026-09-11",
                    override_type=MarketScheduleOverrideType.OPEN_ALL_DAY,
                    note="باز",
                ),
                db=db,
            )
            self.assertEqual(updated.date, "2026-09-11")
            self.assertEqual(updated.override_type, MarketScheduleOverrideType.OPEN_ALL_DAY)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT note FROM market_schedule_overrides WHERE id=1").fetchone()[0],
                    "باز",
                )
            sync.close()
            engine.dispose()

    async def test_delete_market_override_removes_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "override-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(
                MarketScheduleOverride(
                    date=date(2026, 9, 10),
                    override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                    created_by_user_id=1,
                    created_at=datetime.now(timezone.utc),
                )
            )
            sync.commit()
            db = CrudSession(Session())
            with self.assertRaises(HTTPException) as missing:
                await delete_market_override(99, db=db)
            self.assertEqual(missing.exception.status_code, 404)
            result = await delete_market_override(1, db=db)
            self.assertEqual(result, {"success": True})
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM market_schedule_overrides").fetchone()[0], 0)
            sync.close()
            engine.dispose()

    async def test_bot_alias_add_name_persists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-add.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_alias_add_name(_commodity_message("ربع طلا"), FakeState(commodity_id=1), user=None)
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodity_aliases").fetchone()[0], 0)
                await handle_alias_add_name(
                    _commodity_message("ربع طلا"),
                    FakeState(commodity_id=1),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT alias FROM commodity_aliases").fetchone()[0], "ربع طلا")
            sync.close()
            engine.dispose()

    async def test_bot_alias_edit_name_persists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-edit.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_alias_edit_name(
                    _commodity_message("نیم طلا"),
                    FakeState(alias_id=1, commodity_id=1),
                    user=None,
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT alias FROM commodity_aliases").fetchone()[0], "ربع طلا")
                await handle_alias_edit_name(
                    _commodity_message("نیم طلا"),
                    FakeState(alias_id=1, commodity_id=1),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT alias FROM commodity_aliases").fetchone()[0], "نیم طلا")
            sync.close()
            engine.dispose()

    async def test_bot_alias_delete_yes_persists_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_alias_delete_yes(
                    _commodity_callback("alias_delete_confirm_yes_1_1"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=FakeState(alias_to_delete_id=1, commodity_id=1),
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodity_aliases").fetchone()[0], 1)
                await handle_alias_delete_yes(
                    _commodity_callback("alias_delete_confirm_yes_1_1"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=FakeState(alias_to_delete_id=1, commodity_id=1),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodity_aliases").fetchone()[0], 0)
            sync.close()
            engine.dispose()

    async def test_bot_commodity_edit_name_persists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-commodity-edit.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_commodity_edit_name(_commodity_message("نیم"), FakeState(commodity_id=1), user=None)
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT name FROM commodities").fetchone()[0], "ربع")
                await handle_commodity_edit_name(
                    _commodity_message("نیم"),
                    FakeState(commodity_id=1),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT name FROM commodities").fetchone()[0], "نیم")
            sync.close()
            engine.dispose()

    async def test_bot_add_aliases_and_create_persists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-commodity-create.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_add_aliases_and_create(
                    _commodity_message("ربع طلا"),
                    FakeState(name="ربع"),
                    user=None,
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodities").fetchone()[0], 0)
                await handle_add_aliases_and_create(
                    _commodity_message("ربع طلا"),
                    FakeState(name="ربع"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT name FROM commodities").fetchone()[0], "ربع")
                self.assertIn("ربع طلا", [row[0] for row in conn.execute("SELECT alias FROM commodity_aliases")])
            engine.dispose()

    async def test_bot_delete_yes_removes_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-commodity-delete.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            for item in patches:
                item.start()
            try:
                await handle_delete_yes(
                    _commodity_callback("comm_delete_confirm_yes_1"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=FakeState(commodity_to_delete_id=1),
                )
                with sqlite3.connect(db_path) as conn:
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodities").fetchone()[0], 1)
                await handle_delete_yes(
                    _commodity_callback("comm_delete_confirm_yes_1"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=FakeState(commodity_to_delete_id=1),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM commodities").fetchone()[0], 0)
            sync.close()
            engine.dispose()

    async def test_update_settings_persists_then_rejects_invalid_hours(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "settings-update.sqlite")
            engine, Session = _admin_crud_store(db_path)

            def _box():
                return _SessionBox(CrudSession(Session()))

            with patch("core.db.AsyncSessionLocal", side_effect=_box):
                with self.assertRaises(HTTPException) as invalid:
                    await update_settings(
                        TradingSettingsUpdate(market_open_time_local="18:00", market_close_time_local="09:00")
                    )
                self.assertEqual(invalid.exception.status_code, 400)
                updated = await update_settings(TradingSettingsUpdate(offer_min_quantity=7))
                self.assertEqual(updated.offer_min_quantity, 7)
            with sqlite3.connect(db_path) as conn:
                raw = conn.execute("SELECT value FROM trading_settings WHERE key='offer_min_quantity'").fetchone()
            self.assertIsNotNone(raw)
            self.assertEqual(json.loads(raw[0]), 7)
            engine.dispose()

    async def test_reset_settings_restores_defaults_after_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "settings-reset.sqlite")
            engine, Session = _admin_crud_store(db_path)

            def _box():
                return _SessionBox(CrudSession(Session()))

            with patch("core.db.AsyncSessionLocal", side_effect=_box):
                await update_settings(TradingSettingsUpdate(offer_min_quantity=9))
                reset = await reset_settings()
                self.assertEqual(reset.offer_min_quantity, 5)
            with sqlite3.connect(db_path) as conn:
                raw = conn.execute("SELECT value FROM trading_settings WHERE key='offer_min_quantity'").fetchone()
            self.assertEqual(json.loads(raw[0]), 5)
            engine.dispose()

    async def test_bot_alias_add_start_sets_fsm_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-add-start.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            denied = FakeState()
            ok = FakeState()
            for item in patches:
                item.start()
            try:
                await handle_alias_add_start(
                    _commodity_callback("alias_add_1"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertIsNone(denied.state)
                await handle_alias_add_start(
                    _commodity_callback("alias_add_1"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(ok.state, CommodityManagement.awaiting_alias_add_name)
            self.assertEqual(ok.data["commodity_id"], 1)
            engine.dispose()

    async def test_bot_alias_edit_start_sets_fsm_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-edit-start.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            denied = FakeState()
            ok = FakeState()
            for item in patches:
                item.start()
            try:
                await handle_alias_edit_start(
                    _commodity_callback("alias_edit_1_2"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertIsNone(denied.state)
                await handle_alias_edit_start(
                    _commodity_callback("alias_edit_1_2"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(ok.state, CommodityManagement.awaiting_alias_edit_name)
            self.assertEqual(ok.data["alias_id"], 2)
            self.assertEqual(ok.data["commodity_id"], 1)
            engine.dispose()

    async def test_bot_alias_delete_start_sets_fsm_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-alias-delete-start.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            denied = FakeState()
            ok = FakeState()
            for item in patches:
                item.start()
            try:
                await handle_alias_delete_start(
                    _commodity_callback("alias_delete_1_2"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertIsNone(denied.state)
                await handle_alias_delete_start(
                    _commodity_callback("alias_delete_1_2"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(ok.state, CommodityManagement.awaiting_alias_delete_confirm)
            self.assertEqual(ok.data["alias_to_delete_id"], 2)
            engine.dispose()

    async def test_bot_commodity_edit_start_sets_fsm_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-commodity-edit-start.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            denied = FakeState()
            ok = FakeState()
            for item in patches:
                item.start()
            try:
                await handle_commodity_edit_start(
                    _commodity_callback("comm_edit_name_1"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertIsNone(denied.state)
                await handle_commodity_edit_start(
                    _commodity_callback("comm_edit_name_1"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(ok.state, CommodityManagement.awaiting_commodity_edit_name)
            self.assertEqual(ok.data["commodity_id"], 1)
            engine.dispose()

    async def test_bot_add_start_sets_fsm_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-add-start.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            denied = FakeState()
            ok = FakeState()
            for item in patches:
                item.start()
            try:
                await handle_add_start(
                    _commodity_callback("comm_add_new"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertIsNone(denied.state)
                await handle_add_start(
                    _commodity_callback("comm_add_new"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(ok.state, CommodityManagement.awaiting_add_name)
            engine.dispose()

    async def test_bot_add_name_advances_fsm_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-add-name.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            denied = FakeState()
            ok = FakeState()
            for item in patches:
                item.start()
            try:
                await handle_add_name(_commodity_message("ربع"), denied, user=None)
                self.assertIsNone(denied.state)
                await handle_add_name(
                    _commodity_message("ربع"),
                    ok,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(ok.state, CommodityManagement.awaiting_add_aliases)
            self.assertEqual(ok.data["name"], "ربع")
            engine.dispose()

    async def test_bot_delete_confirm_sets_fsm_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-delete-confirm.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            denied = FakeState()
            ok = FakeState()
            for item in patches:
                item.start()
            try:
                await handle_delete_confirm(
                    _commodity_callback("comm_delete_1"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertIsNone(denied.state)
                await handle_delete_confirm(
                    _commodity_callback("comm_delete_1"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(ok.state, CommodityManagement.awaiting_delete_confirmation)
            self.assertEqual(ok.data["commodity_to_delete_id"], 1)
            engine.dispose()

    async def test_bot_cancel_fsm_clears_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-cancel-fsm.sqlite")
            engine, Session = _admin_crud_store(db_path)
            db = CrudSession(Session())
            patches = _bot_commodity_patches(db)
            denied = FakeState(commodity_id=1)
            await denied.set_state(CommodityManagement.awaiting_alias_add_name)
            ok = FakeState(commodity_id=1)
            await ok.set_state(CommodityManagement.awaiting_alias_add_name)

            async def _clear(state):
                await state.clear()

            for item in patches:
                item.start()
            try:
                with patch("bot.handlers.admin_commodities.clear_state_retain_anchor", new=_clear):
                    await handle_cancel_fsm(
                        _commodity_callback("comm_fsm_cancel"),
                        denied,
                        user=SimpleNamespace(role=UserRole.STANDARD),
                    )
                    self.assertEqual(denied.state, CommodityManagement.awaiting_alias_add_name)
                    await handle_cancel_fsm(
                        _commodity_callback("comm_fsm_cancel"),
                        ok,
                        user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertIsNone(ok.state)
            self.assertEqual(ok.data, {})
            engine.dispose()

    async def test_start_invitation_creation_sets_fsm_then_denies_without_user(self):
        denied = FakeState()
        ok = FakeState()
        patches = _invitation_patches()
        for item in patches:
            item.start()
        try:
            await start_invitation_creation(_commodity_message("➕ ارسال لینک دعوت"), denied, user=None)
            self.assertIsNone(denied.state)
            await start_invitation_creation(
                _commodity_message("➕ ارسال لینک دعوت"),
                ok,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, InvitationCreation.awaiting_account_name)
        self.assertEqual(ok.data["inviter_role"], UserRole.SUPER_ADMIN.value)

    async def test_start_invitation_inline_sets_fsm_then_denies_without_user(self):
        denied = FakeState()
        ok = FakeState()
        patches = _invitation_patches()
        for item in patches:
            item.start()
        try:
            await start_invitation_creation_inline(
                _commodity_callback("create_invitation_inline"),
                denied,
                user=None,
            )
            self.assertIsNone(denied.state)
            await start_invitation_creation_inline(
                _commodity_callback("create_invitation_inline"),
                ok,
                user=SimpleNamespace(role=UserRole.MIDDLE_MANAGER),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, InvitationCreation.awaiting_account_name)
        self.assertEqual(ok.data["inviter_role"], UserRole.MIDDLE_MANAGER.value)

    async def test_process_invitation_account_name_advances_then_rejects_invalid(self):
        invalid = FakeState()
        await invalid.set_state(InvitationCreation.awaiting_account_name)
        ok = FakeState()
        await ok.set_state(InvitationCreation.awaiting_account_name)
        patches = _invitation_patches()
        for item in patches:
            item.start()
        try:
            await process_invitation_account_name(
                _commodity_message("ab"),
                invalid,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
            )
            self.assertEqual(invalid.state, InvitationCreation.awaiting_account_name)
            self.assertNotIn("account_name", invalid.data)
            await process_invitation_account_name(
                _commodity_message("ali_user"),
                ok,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, InvitationCreation.awaiting_mobile_number)
        self.assertEqual(ok.data["account_name"], "ali_user")

    async def test_process_invitation_mobile_advances_then_rejects_invalid(self):
        invalid = FakeState(inviter_role=UserRole.SUPER_ADMIN.value)
        await invalid.set_state(InvitationCreation.awaiting_mobile_number)
        ok = FakeState(inviter_role=UserRole.SUPER_ADMIN.value)
        await ok.set_state(InvitationCreation.awaiting_mobile_number)
        patches = _invitation_patches()
        for item in patches:
            item.start()
        try:
            await process_invitation_mobile(
                _commodity_message("123"),
                invalid,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
            )
            self.assertEqual(invalid.state, InvitationCreation.awaiting_mobile_number)
            self.assertNotIn("mobile_number", invalid.data)
            await process_invitation_mobile(
                _commodity_message("09123456789"),
                ok,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, InvitationCreation.awaiting_role)
        self.assertEqual(ok.data["mobile_number"], "09123456789")

    async def test_cancel_invitation_creation_clears_then_denies_without_user(self):
        denied = FakeState(inviter_role=UserRole.SUPER_ADMIN.value)
        await denied.set_state(InvitationCreation.awaiting_account_name)
        ok = FakeState(inviter_role=UserRole.SUPER_ADMIN.value, last_prompt_message_id=9)
        await ok.set_state(InvitationCreation.awaiting_account_name)
        patches = _invitation_patches()
        for item in patches:
            item.start()
        try:
            await cancel_invitation_creation(
                _commodity_callback("comm_fsm_cancel"),
                denied,
                bot=SimpleNamespace(),
                user=None,
            )
            self.assertEqual(denied.data, {})
            await cancel_invitation_creation(
                _commodity_callback("comm_fsm_cancel"),
                ok,
                bot=SimpleNamespace(),
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.data, {})
        self.assertIsNone(ok.state)

    async def test_user_edit_role_prompts_then_denies_non_admin(self):
        edits = []

        async def _edit(*_a, **_k):
            edits.append(1)

        callback = SimpleNamespace(data="user_edit_role_9", answer=AsyncMock(), message=SimpleNamespace())
        with patch("bot.handlers.admin_users.answer_callback_query_via_runtime", new=AsyncMock()), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=_edit,
        ), patch(
            "bot.handlers.admin_users.get_user_role_edit_keyboard",
            return_value="ROLE-KB",
        ):
            await handle_user_edit_role(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            self.assertEqual(edits, [])
            await handle_user_edit_role(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(edits, [1])

    async def test_user_delete_request_redirects_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        redirects = []

        async def _redirect(*_a, **_k):
            redirects.append(1)

        callback = SimpleNamespace(data="user_ask_delete_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users._show_user_delete_webapp_redirect",
            new=_redirect,
        ):
            await handle_user_delete_request(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            self.assertEqual(redirects, [])
            await handle_user_delete_request(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(redirects, [1])
        reloaded, fresh = _reload_user(Session)
        self.assertFalse(reloaded.is_deleted)
        fresh.close()
        sync.close()
        engine.dispose()

    async def test_user_delete_confirm_redirects_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        redirects = []

        async def _redirect(*_a, **_k):
            redirects.append(1)

        callback = SimpleNamespace(data="user_delete_confirm_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users._show_user_delete_webapp_redirect",
            new=_redirect,
        ):
            await handle_user_delete_confirm(
                callback,
                user=SimpleNamespace(role=UserRole.STANDARD),
                state=FakeState(),
            )
            self.assertEqual(redirects, [])
            await handle_user_delete_confirm(
                callback,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                state=FakeState(),
            )
        self.assertEqual(redirects, [1])
        reloaded, fresh = _reload_user(Session)
        self.assertFalse(reloaded.is_deleted)
        fresh.close()
        sync.close()
        engine.dispose()

    async def test_limit_cancel_clears_fsm_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        denied = FakeState(limit_user_id=9)
        ok = FakeState(limit_user_id=9)

        async def _clear(state):
            await state.clear()

        callback = SimpleNamespace(data="limit_cancel_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.clear_state_retain_anchors",
            new=_clear,
        ):
            display = _user_admin_display_patches()
            for item in display:
                item.start()
            try:
                await handle_limit_cancel(callback, user=SimpleNamespace(role=UserRole.STANDARD), state=denied)
                self.assertEqual(denied.data.get("limit_user_id"), 9)
                await handle_limit_cancel(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(display):
                    item.stop()
        self.assertEqual(ok.data, {})
        sync.close()
        engine.dispose()

    async def test_user_block_settings_shows_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        edits = []

        async def _edit(*_a, **_k):
            edits.append(1)

        callback = SimpleNamespace(data="user_block_settings_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=_edit,
        ), patch(
            "bot.handlers.admin_users.get_block_settings_keyboard",
            return_value="BLOCK-KB",
        ):
            await handle_user_block_settings(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            self.assertEqual(edits, [])
            await handle_user_block_settings(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(edits, [1])
        sync.close()
        engine.dispose()

    async def test_admin_set_max_block_prompts_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        edits = []

        async def _edit(*_a, **_k):
            edits.append(1)

        callback = SimpleNamespace(data="admin_set_max_block_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=_edit,
        ), patch(
            "bot.handlers.admin_users.get_max_block_options_keyboard",
            return_value="MAX-KB",
        ):
            await handle_admin_set_max_block(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            self.assertEqual(edits, [])
            await handle_admin_set_max_block(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(edits, [1])
        sync.close()
        engine.dispose()

    async def test_admin_max_block_custom_sets_fsm_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        denied = FakeState()
        ok = FakeState()
        callback = SimpleNamespace(data="admin_max_block_custom_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)):
            display = _user_admin_display_patches()
            for item in display:
                item.start()
            try:
                await handle_admin_max_block_custom(
                    callback,
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertIsNone(denied.state)
                await handle_admin_max_block_custom(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(display):
                    item.stop()
        self.assertEqual(ok.state, UserManagement.awaiting_custom_max_block)
        self.assertEqual(ok.data["custom_max_block_user_id"], 9)
        sync.close()
        engine.dispose()

    async def test_create_market_message_persists_notifies_then_rejects_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market-create.sqlite")
            engine, Session = _market_message_store(db_path)
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, account_name="admin")
            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                with self.assertRaises(HTTPException) as empty:
                    await create_market_message(
                        AdminMarketMessageCreate(content="   "),
                        current_user=actor,
                        db=CrudSession(Session()),
                    )
                self.assertEqual(empty.exception.status_code, 400)
                created = await create_market_message(
                    AdminMarketMessageCreate(content="بازار باز است"),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(created.content, "بازار باز است")
            self.assertTrue(created.is_active)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT content, is_active FROM admin_market_messages WHERE id=1").fetchone(),
                    ("بازار باز است", 1),
                )
                self.assertGreaterEqual(
                    conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id=2").fetchone()[0],
                    1,
                )
            engine.dispose()

    async def test_clear_current_market_message_deactivates_then_noop_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "market-clear.sqlite")
            engine, Session = _market_message_store(db_path)
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, account_name="admin")
            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                empty = await clear_current_market_message(current_user=actor, db=CrudSession(Session()))
                self.assertIsNone(empty)
                await create_market_message(
                    AdminMarketMessageCreate(content="بازار بسته است"),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
                cleared = await clear_current_market_message(current_user=actor, db=CrudSession(Session()))
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertFalse(cleared.is_active)
            self.assertEqual(cleared.content, "بازار بسته است")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute("SELECT is_active FROM admin_market_messages WHERE id=1").fetchone()[0],
                    0,
                )
            engine.dispose()

    async def test_start_telegram_admin_broadcast_opens_menu_then_denies_non_admin(self):
        denied = FakeState(leftover=1)
        ok = FakeState(leftover=1)
        answers = []

        async def _answer(*_a, **_k):
            answers.append(1)
            return SimpleNamespace(message_id=90)

        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            with patch("bot.handlers.admin_broadcast.answer_incoming_message_via_runtime", new=_answer):
                await start_telegram_admin_broadcast(
                    _commodity_message("📣 ارسال پیام همگانی بات"),
                    denied,
                    user=SimpleNamespace(role=UserRole.STANDARD),
                )
                self.assertEqual(denied.data.get("leftover"), 1)
                self.assertEqual(answers, [])
                await start_telegram_admin_broadcast(
                    _commodity_message("📣 ارسال پیام همگانی بات"),
                    ok,
                    user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.data, {})
        self.assertEqual(answers, [1])

    async def test_cancel_telegram_admin_broadcast_clears_then_denies_non_admin(self):
        denied = FakeState(audience_type="all")
        ok = FakeState(audience_type="all")
        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            await cancel_telegram_admin_broadcast(
                _broadcast_callback("tgb:cancel"),
                denied,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertEqual(denied.data.get("audience_type"), "all")
            await cancel_telegram_admin_broadcast(
                _broadcast_callback("tgb:cancel"),
                ok,
                user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.data, {})
        self.assertIsNone(ok.state)

    async def test_choose_all_recipients_asks_text_then_denies_non_admin(self):
        denied = FakeState()
        ok = FakeState()
        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            await choose_all_recipients(
                _broadcast_callback("tgb:all"),
                denied,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertIsNone(denied.state)
            await choose_all_recipients(
                _broadcast_callback("tgb:all"),
                ok,
                user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, AdminBroadcast.awaiting_message_text)
        self.assertEqual(ok.data["audience_type"], TelegramAdminBroadcastAudienceType.ALL.value)

    async def test_choose_group_recipients_opens_picker_then_denies_non_admin(self):
        denied = FakeState()
        ok = FakeState()
        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            await choose_group_recipients(
                _broadcast_callback("tgb:groups"),
                denied,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertNotIn("audience_type", denied.data)
            await choose_group_recipients(
                _broadcast_callback("tgb:groups"),
                ok,
                user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.data["audience_type"], TelegramAdminBroadcastAudienceType.GROUP.value)
        self.assertEqual(ok.data["target_groups"], [])

    async def test_finish_group_selection_asks_text_then_rejects_empty(self):
        empty = FakeState(target_groups=[])
        ok = FakeState(target_groups=[TELEGRAM_ADMIN_BROADCAST_GROUP_ORDINARY])
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            with patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new=_answer):
                await finish_group_selection(
                    _broadcast_callback("tgb:groups_done"),
                    empty,
                    user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                )
                self.assertIsNone(empty.state)
                self.assertIn("حداقل یک گروه انتخاب کنید.", answers)
                await finish_group_selection(
                    _broadcast_callback("tgb:groups_done"),
                    ok,
                    user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, AdminBroadcast.awaiting_message_text)

    async def test_choose_selected_recipients_asks_search_then_denies_non_admin(self):
        denied = FakeState()
        ok = FakeState()
        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            await choose_selected_recipients(
                _broadcast_callback("tgb:selected"),
                denied,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertIsNone(denied.state)
            await choose_selected_recipients(
                _broadcast_callback("tgb:selected"),
                ok,
                user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, AdminBroadcast.awaiting_search_query)
        self.assertEqual(ok.data["audience_type"], TelegramAdminBroadcastAudienceType.SELECTED.value)

    async def test_process_selected_recipient_search_stores_query_then_denies_non_admin(self):
        denied = FakeState(selected_user_ids=[])
        ok = FakeState(selected_user_ids=[])
        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            await process_selected_recipient_search(
                _commodity_message("ali"),
                denied,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertNotIn("last_search_query", denied.data)
            await process_selected_recipient_search(
                _commodity_message("ali"),
                ok,
                user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.data["last_search_query"], "ali")

    async def test_search_again_resets_query_state_then_denies_non_admin(self):
        denied = FakeState(selected_user_ids=[9])
        ok = FakeState(selected_user_ids=[9])
        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            await search_again(
                _broadcast_callback("tgb:search_again"),
                denied,
                user=SimpleNamespace(role=UserRole.STANDARD),
            )
            self.assertIsNone(denied.state)
            await search_again(
                _broadcast_callback("tgb:search_again"),
                ok,
                user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, AdminBroadcast.awaiting_search_query)
        self.assertEqual(ok.data["selected_user_ids"], [9])

    async def test_finish_selected_recipients_asks_text_then_rejects_empty(self):
        empty = FakeState(selected_user_ids=[])
        ok = FakeState(selected_user_ids=[9])
        answers = []

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            with patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new=_answer):
                await finish_selected_recipients(
                    _broadcast_callback("tgb:selected_done"),
                    empty,
                    user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                )
                self.assertIsNone(empty.state)
                self.assertIn("حداقل یک کاربر انتخاب کنید.", answers)
                await finish_selected_recipients(
                    _broadcast_callback("tgb:selected_done"),
                    ok,
                    user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, AdminBroadcast.awaiting_message_text)

    async def test_process_broadcast_message_text_drafts_then_rejects_empty(self):
        empty = FakeState(audience_type=TelegramAdminBroadcastAudienceType.ALL.value)
        ok = FakeState(audience_type=TelegramAdminBroadcastAudienceType.ALL.value)
        answers = []

        async def _answer(*_a, **kwargs):
            answers.append(kwargs.get("source_key") or "")
            return SimpleNamespace(message_id=90)

        patches = _broadcast_flow_patches()
        for item in patches:
            item.start()
        try:
            with patch("bot.handlers.admin_broadcast.answer_incoming_message_via_runtime", new=_answer):
                await process_broadcast_message_text(
                    _commodity_message("   "),
                    empty,
                    user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                )
                self.assertIsNone(empty.state)
                self.assertIn("admin-broadcast-content-empty", answers)
                await process_broadcast_message_text(
                    _commodity_message("پیام همگانی تست"),
                    ok,
                    user=SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN),
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.state, AdminBroadcast.awaiting_confirmation)
        self.assertEqual(ok.data["content"], "پیام همگانی تست")
        self.assertTrue(ok.data.get("creation_key"))

    async def test_user_limit_start_prompts_duration_then_stores_expiry(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        denied = FakeState()
        first = FakeState()
        dur = FakeState()
        callback_start = SimpleNamespace(data="user_limit_9", answer=AsyncMock(), message=SimpleNamespace())
        callback_dur = SimpleNamespace(data="user_limit_dur_9_60", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)):
            display = _user_admin_display_patches()
            for item in display:
                item.start()
            try:
                await handle_user_limit_start(
                    callback_start,
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertEqual(denied.data, {})
                await handle_user_limit_start(
                    callback_start,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=first,
                )
                self.assertNotIn("limit_expire_at", first.data)
                await handle_user_limit_start(
                    callback_dur,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=dur,
                )
            finally:
                for item in reversed(display):
                    item.stop()
        self.assertEqual(dur.data["limit_target_user_id"], 9)
        self.assertIsNotNone(dur.data.get("limit_expire_at"))
        sync.close()
        engine.dispose()

    async def test_set_trades_sets_fsm_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        denied = FakeState()
        ok = FakeState()
        callback = SimpleNamespace(data="limit_set_trades_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)):
            display = _user_admin_display_patches()
            for item in display:
                item.start()
            try:
                await handle_set_trades(callback, user=SimpleNamespace(role=UserRole.STANDARD), state=denied)
                self.assertIsNone(denied.state)
                await handle_set_trades(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=ok)
            finally:
                for item in reversed(display):
                    item.stop()
        self.assertEqual(ok.state, UserLimitations.awaiting_limit_value)
        self.assertEqual(ok.data["limit_editing"], "trades")
        sync.close()
        engine.dispose()

    async def test_set_commodities_sets_fsm_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        denied = FakeState()
        ok = FakeState()
        callback = SimpleNamespace(data="limit_set_commodities_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)):
            display = _user_admin_display_patches()
            for item in display:
                item.start()
            try:
                await handle_set_commodities(callback, user=SimpleNamespace(role=UserRole.STANDARD), state=denied)
                self.assertIsNone(denied.state)
                await handle_set_commodities(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=ok)
            finally:
                for item in reversed(display):
                    item.stop()
        self.assertEqual(ok.state, UserLimitations.awaiting_limit_value)
        self.assertEqual(ok.data["limit_editing"], "commodities")
        sync.close()
        engine.dispose()

    async def test_set_requests_sets_fsm_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        denied = FakeState()
        ok = FakeState()
        callback = SimpleNamespace(data="limit_set_requests_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)):
            display = _user_admin_display_patches()
            for item in display:
                item.start()
            try:
                await handle_set_requests(callback, user=SimpleNamespace(role=UserRole.STANDARD), state=denied)
                self.assertIsNone(denied.state)
                await handle_set_requests(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN), state=ok)
            finally:
                for item in reversed(display):
                    item.stop()
        self.assertEqual(ok.state, UserLimitations.awaiting_limit_value)
        self.assertEqual(ok.data["limit_editing"], "requests")
        sync.close()
        engine.dispose()

    async def test_process_limit_value_stores_then_rejects_invalid(self):
        invalid = FakeState(limit_editing="trades", limit_target_user_id=9)
        await invalid.set_state(UserLimitations.awaiting_limit_value)
        ok = FakeState(limit_editing="trades", limit_target_user_id=9)
        await ok.set_state(UserLimitations.awaiting_limit_value)
        display = _user_admin_display_patches()
        for item in display:
            item.start()
        try:
            with patch("bot.keyboards.get_limit_settings_keyboard", return_value="LIM-KB"):
                await process_limit_value(
                    _commodity_message("abc"),
                    invalid,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
                self.assertEqual(invalid.state, UserLimitations.awaiting_limit_value)
                self.assertNotIn("limit_max_trades", invalid.data)
                await process_limit_value(
                    _commodity_message("4"),
                    ok,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
        finally:
            for item in reversed(display):
                item.stop()
        self.assertIsNone(ok.state)
        self.assertEqual(ok.data["limit_max_trades"], 4)

    async def test_start_search_user_sets_fsm_then_denies_non_admin(self):
        denied = FakeState()
        ok = FakeState()
        display = _user_admin_display_patches()
        for item in display:
            item.start()
        try:
            await start_search_user(_commodity_message("🔍 جستجوی کاربر"), denied, user=SimpleNamespace(role=UserRole.STANDARD))
            self.assertIsNone(denied.state)
            await start_search_user(
                _commodity_message("🔍 جستجوی کاربر"),
                ok,
                user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
            )
        finally:
            for item in reversed(display):
                item.stop()
        self.assertEqual(ok.state, UserManagement.awaiting_search_query)

    async def test_user_search_cancel_clears_then_denies_non_admin(self):
        denied = FakeState()
        await denied.set_state(UserManagement.awaiting_search_query)
        ok = FakeState()
        await ok.set_state(UserManagement.awaiting_search_query)

        async def _clear(state):
            await state.clear()

        display = _user_admin_display_patches()
        for item in display:
            item.start()
        try:
            with patch("bot.handlers.admin_users.clear_state_retain_anchors", new=_clear):
                await handle_user_search_cancel(
                    _commodity_callback("user_search_cancel"),
                    denied,
                    user=SimpleNamespace(role=UserRole.STANDARD),
                )
                self.assertEqual(denied.state, UserManagement.awaiting_search_query)
                await handle_user_search_cancel(
                    _commodity_callback("user_search_cancel"),
                    ok,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                )
        finally:
            for item in reversed(display):
                item.stop()
        self.assertIsNone(ok.state)

    async def test_process_search_query_finds_user_then_rejects_unknown(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        missing = FakeState()
        await missing.set_state(UserManagement.awaiting_search_query)
        ok = FakeState()
        await ok.set_state(UserManagement.awaiting_search_query)
        class SearchSession(RoleSession):
            def __init__(self, sync_session, *, found):
                super().__init__(sync_session)
                self.found = found

            async def execute(self, statement, params=None):
                blob = f"{statement} {getattr(statement, 'text', '')}"
                if "FROM users" in blob and "notifications" not in blob.lower():
                    loaded = self._session.get(MappedAccountUser, 9) if self.found else None
                    return SimpleNamespace(scalar_one_or_none=lambda: loaded)
                return self._session.execute(statement)

        async def _clear(state):
            await state.clear()

        display = _user_admin_display_patches()
        for item in display:
            item.start()
        try:
            with patch("bot.handlers.admin_users.clear_state_retain_anchors", new=_clear):
                with patch(
                    "bot.handlers.admin_users.AsyncSessionLocal",
                    return_value=_SessionBox(SearchSession(sync, found=False)),
                ):
                    await process_search_query(
                        _commodity_message("missing"),
                        missing,
                        user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    )
                self.assertIsNone(missing.state)
                with patch(
                    "bot.handlers.admin_users.AsyncSessionLocal",
                    return_value=_SessionBox(SearchSession(sync, found=True)),
                ):
                    await process_search_query(
                        _commodity_message("ali"),
                        ok,
                        user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    )
        finally:
            for item in reversed(display):
                item.stop()
        self.assertIsNone(ok.state)
        sync.close()
        engine.dispose()

    async def test_user_settings_shows_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        edits = []

        async def _edit(*_a, **_k):
            edits.append(1)

        callback = SimpleNamespace(data="user_settings_9", answer=AsyncMock(), message=SimpleNamespace())
        db = RoleSession(sync)
        with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
            "bot.handlers.admin_users.answer_callback_query_via_runtime",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.admin_users.edit_callback_message_via_runtime",
            new=_edit,
        ), patch(
            "bot.handlers.admin_users.get_user_profile_text",
            new=AsyncMock(return_value="profile"),
        ), patch(
            "bot.handlers.admin_users.get_user_settings_keyboard",
            return_value="SET-KB",
        ):
            await handle_user_settings(callback, user=SimpleNamespace(role=UserRole.STANDARD))
            self.assertEqual(edits, [])
            await handle_user_settings(callback, user=SimpleNamespace(role=UserRole.SUPER_ADMIN))
        self.assertEqual(edits, [1])
        sync.close()
        engine.dispose()

    async def test_create_broadcast_persists_messages_then_rejects_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "admin-broadcast.sqlite")
            engine, Session = _management_broadcast_store(db_path)
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, account_name="admin")
            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                with self.assertRaises(HTTPException) as empty:
                    await create_broadcast(
                        AdminBroadcastCreate(content="   ", target_groups=["users"]),
                        current_user=actor,
                        db=CrudSession(Session()),
                    )
                self.assertEqual(empty.exception.status_code, 400)
                created = await create_broadcast(
                    AdminBroadcastCreate(content="پیام مدیریت تست", target_groups=["users"]),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(created.content, "پیام مدیریت تست")
            self.assertIn("users", created.target_groups)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM admin_broadcast_messages").fetchone()[0], 1)
                self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 1)
                self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0], 1)
            engine.dispose()

    async def test_manage_commodities_lists_then_denies_without_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-manage-commodities.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            sent = []
            db = CrudSession(Session())
            patches = _bot_commodity_nav_patches(db, sent)
            for item in patches:
                item.start()
            try:
                await handle_manage_commodities(_commodity_message("📦 مدیریت کالاها"), user=None, state=FakeState())
                self.assertEqual(sent, [])
                message = _commodity_message("📦 مدیریت کالاها")
                message.bot = _capture_bot(sent)
                await handle_manage_commodities(
                    message,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=FakeState(),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertTrue(any("ربع" in text or "مدیریت کالاها" in text for text in sent))
            sync.close()
            engine.dispose()

    async def test_back_to_list_clears_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-back-to-list.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            sent = []
            db = CrudSession(Session())
            patches = _bot_commodity_nav_patches(db, sent)
            denied = FakeState(anchor_id=3)
            ok = FakeState(anchor_id=3)
            for item in patches:
                item.start()
            try:
                await handle_back_to_list(
                    _commodity_callback("comm_back_to_list"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertEqual(sent, [])
                callback = _commodity_callback("comm_back_to_list")
                callback.bot = _capture_bot(sent)
                await handle_back_to_list(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertTrue(any("ربع" in text or "مدیریت کالاها" in text for text in sent))
            sync.close()
            engine.dispose()

    async def test_manage_aliases_lists_then_denies_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "bot-manage-aliases.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            commodity = Commodity(name="ربع")
            sync.add(commodity)
            sync.flush()
            sync.add(CommodityAlias(commodity_id=commodity.id, alias="ربع طلا"))
            sync.commit()
            sent = []
            db = CrudSession(Session())
            patches = _bot_commodity_nav_patches(db, sent)
            for item in patches:
                item.start()
            try:
                await handle_manage_aliases(
                    _commodity_callback("comm_manage_aliases_1"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=FakeState(),
                )
                self.assertEqual(sent, [])
                callback = _commodity_callback("comm_manage_aliases_1")
                callback.bot = _capture_bot(sent)
                await handle_manage_aliases(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=FakeState(),
                )
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertTrue(any("ربع" in text for text in sent))
            sync.close()
            engine.dispose()

    async def test_users_menu_stores_anchor_then_denies_non_admin(self):
        answers = []

        async def _answer(*_a, **_k):
            answers.append(_k.get("source_key") or "")
            return SimpleNamespace(message_id=92)

        denied = FakeState()
        ok = FakeState()
        patches = _user_admin_nav_patches()
        for item in patches:
            item.start()
        try:
            with patch("bot.handlers.admin_users.answer_incoming_message_via_runtime", new=_answer):
                await handle_users_menu(
                    _commodity_message("👥 مدیریت کاربران"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertEqual(denied.data, {})
                self.assertIn("admin-users-menu-denied", answers)
                await handle_users_menu(
                    _commodity_message("👥 مدیریت کاربران"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.data["users_menu_id"], 92)
        self.assertIn("admin-users-menu", answers)

    async def test_users_list_command_renders_then_denies_non_admin(self):
        sent = []
        engine, Session, sync = _make_store()
        _persist_user(sync)
        target = sync.get(MappedAccountUser, 9)
        denied = FakeState()
        ok = FakeState()
        message = _commodity_message("📋 لیست کاربران")
        message.bot = _capture_bot(sent)
        patches = _user_admin_nav_patches()
        for item in patches:
            item.start()
        try:
            with patch(
                "bot.handlers.admin_users.AsyncSessionLocal",
                return_value=_SessionBox(UsersListSession([target])),
            ):
                await handle_users_list_command(
                    message,
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertEqual(sent, [])
                await handle_users_list_command(
                    message,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertTrue(any("لیست کاربران" in text for text in sent))
        sync.close()
        engine.dispose()

    async def test_users_pagination_renders_page_then_denies_non_admin(self):
        sent = []
        engine, Session, sync = _make_store()
        _persist_user(sync)
        target = sync.get(MappedAccountUser, 9)
        callback = SimpleNamespace(
            data="users_page_1",
            answer=AsyncMock(),
            bot=_capture_bot(sent),
            message=SimpleNamespace(chat=SimpleNamespace(id=1), message_id=10),
        )
        patches = _user_admin_nav_patches()
        for item in patches:
            item.start()
        try:
            with patch(
                "bot.handlers.admin_users.AsyncSessionLocal",
                return_value=_SessionBox(UsersListSession([target])),
            ):
                await handle_users_pagination(
                    callback,
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=FakeState(),
                )
                self.assertEqual(sent, [])
                await handle_users_pagination(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=FakeState(),
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertTrue(any("لیست کاربران" in text for text in sent))
        sync.close()
        engine.dispose()

    async def test_view_user_profile_edits_then_denies_non_admin(self):
        engine, Session, sync = _make_store()
        _persist_user(sync)
        edits = []

        async def _edit(*_a, **_k):
            edits.append(1)

        callback = SimpleNamespace(
            data="user_profile_9",
            answer=AsyncMock(),
            message=SimpleNamespace(reply_markup=None),
        )
        db = RoleSession(sync)
        patches = _user_admin_nav_patches()
        for item in patches:
            item.start()
        try:
            with patch("bot.handlers.admin_users.AsyncSessionLocal", return_value=_SessionBox(db)), patch(
                "bot.handlers.admin_users.edit_callback_message_via_runtime",
                new=_edit,
            ):
                await handle_view_user_profile(
                    callback,
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=FakeState(),
                )
                self.assertEqual(edits, [])
                await handle_view_user_profile(
                    callback,
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=FakeState(),
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(edits, [1])
        sync.close()
        engine.dispose()

    async def test_back_to_admin_clears_then_denies_non_admin(self):
        answers = []

        async def _answer(*_a, **_k):
            answers.append(_k.get("source_key") or "")
            return SimpleNamespace(message_id=94)

        denied = FakeState(users_menu_id=3, leftover=1)
        ok = FakeState(users_menu_id=3, leftover=1)
        patches = _user_admin_nav_patches()
        for item in patches:
            item.start()
        try:
            with patch("bot.handlers.admin_users.answer_incoming_message_via_runtime", new=_answer):
                await handle_back_to_admin(
                    _commodity_message("🔙 بازگشت به پنل مدیریت"),
                    user=SimpleNamespace(role=UserRole.STANDARD),
                    state=denied,
                )
                self.assertEqual(denied.data["users_menu_id"], 3)
                await handle_back_to_admin(
                    _commodity_message("🔙 بازگشت به پنل مدیریت"),
                    user=SimpleNamespace(role=UserRole.SUPER_ADMIN),
                    state=ok,
                )
        finally:
            for item in reversed(patches):
                item.stop()
        self.assertEqual(ok.data, {})
        self.assertIn("admin-users-back-to-panel", answers)

    async def test_get_current_market_message_reads_row_then_none_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "get-market-current.sqlite")
            engine, Session = _market_message_store(db_path)
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, account_name="admin")
            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                empty = await get_current_market_message(current_user=actor, db=CrudSession(Session()))
                self.assertIsNone(empty)
                await create_market_message(
                    AdminMarketMessageCreate(content="بازار باز است"),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
                current = await get_current_market_message(current_user=actor, db=CrudSession(Session()))
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(current.content, "بازار باز است")
            self.assertTrue(current.is_active)
            engine.dispose()

    async def test_get_market_message_history_lists_then_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "get-market-history.sqlite")
            engine, Session = _market_message_store(db_path)
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, account_name="admin")
            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                empty = await get_market_message_history(current_user=actor, db=CrudSession(Session()))
                self.assertEqual(empty, [])
                await create_market_message(
                    AdminMarketMessageCreate(content="تاریخچه بازار"),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
                history = await get_market_message_history(current_user=actor, db=CrudSession(Session()))
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(history[0].content, "تاریخچه بازار")
            engine.dispose()

    async def test_get_broadcast_history_lists_then_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "get-broadcast-history.sqlite")
            engine, Session = _management_broadcast_store(db_path)
            actor = SimpleNamespace(id=1, role=UserRole.SUPER_ADMIN, account_name="admin")
            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                empty = await get_broadcast_history(current_user=actor, db=CrudSession(Session()))
                self.assertEqual(empty, [])
                await create_broadcast(
                    AdminBroadcastCreate(content="پخش تاریخچه", target_groups=["users"]),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
                history = await get_broadcast_history(current_user=actor, db=CrudSession(Session()))
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(history[0].content, "پخش تاریخچه")
            engine.dispose()

    async def test_read_commodity_returns_row_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "read-commodity.sqlite")
            engine, Session = _admin_crud_store(db_path)
            sync = Session()
            sync.add(Commodity(name="ربع"))
            sync.commit()
            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                with self.assertRaises(HTTPException) as missing:
                    await read_commodity(99, db=CrudSession(Session()))
                self.assertEqual(missing.exception.status_code, 404)
                loaded = await read_commodity(1, db=CrudSession(Session()))
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(loaded.name, "ربع")
            sync.close()
            engine.dispose()

    async def test_read_all_commodities_lists_then_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "read-all-commodities.sqlite")
            engine, Session = _admin_crud_store(db_path)
            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                empty = await read_all_commodities(db=CrudSession(Session()))
                self.assertEqual(empty, [])
                sync = Session()
                sync.add(Commodity(name="ربع"))
                sync.commit()
                sync.close()
                items = await read_all_commodities(db=CrudSession(Session()))
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(items[0].name, "ربع")
            engine.dispose()

    async def test_get_settings_reads_persisted_quantity(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "get-settings.sqlite")
            engine, Session = _admin_crud_store(db_path)

            def _box():
                return _SessionBox(CrudSession(Session()))

            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                with patch("core.db.AsyncSessionLocal", side_effect=_box):
                    updated = await update_settings(TradingSettingsUpdate(offer_min_quantity=7))
                    loaded = await get_settings()
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertEqual(updated.offer_min_quantity, 7)
            self.assertEqual(loaded.offer_min_quantity, 7)
            engine.dispose()

    async def test_get_market_state_returns_schedule_view(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "get-market-state.sqlite")
            engine, Session = _admin_crud_store(db_path)
            MarketRuntimeState.__table__.create(engine, checkfirst=True)

            def _box():
                return _SessionBox(CrudSession(Session()))

            patches = _isolated_market_patches()
            for item in patches:
                item.start()
            try:
                with patch("core.db.AsyncSessionLocal", side_effect=_box):
                    await update_settings(TradingSettingsUpdate(offer_min_quantity=3))
                    state = await get_market_state(db=CrudSession(Session()))
            finally:
                for item in reversed(patches):
                    item.stop()
            self.assertIn(state.is_open, (True, False))
            self.assertIsInstance(state.offers_since_last_open, int)
            engine.dispose()

    async def test_list_market_overrides_lists_then_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "list-overrides.sqlite")
            engine, Session = _admin_crud_store(db_path)
            empty = await list_market_overrides(db=CrudSession(Session()))
            self.assertEqual(empty, [])
            created = await create_market_override(
                MarketScheduleOverrideUpsert(
                    date="2026-09-10",
                    override_type=MarketScheduleOverrideType.CLOSED_ALL_DAY,
                ),
                db=CrudSession(Session()),
                current_user=SimpleNamespace(id=1),
            )
            listed = await list_market_overrides(db=CrudSession(Session()))
            self.assertEqual(listed[0].date, created.date)
            self.assertEqual(listed[0].override_type, MarketScheduleOverrideType.CLOSED_ALL_DAY)
            engine.dispose()

    async def test_create_group_persists_then_fails_before_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-create.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            fail_db = CrudSession(Session(), fail_commit=True, fail_label="injected_group_create_commit_failure")
            with self.assertRaisesRegex(RuntimeError, "injected_group_create_commit_failure"):
                await create_group(GroupCreateRequest(title="گروه تست"), current_user=actor, db=fail_db)
            await fail_db.rollback()
            fail_db._session.close()
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM chats").fetchone()[0], 0)
            created = await create_group(
                GroupCreateRequest(title="گروه تست"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(created.group.title, "گروه تست")
            self.assertEqual(created.group.member_count, 1)
            self.assertEqual(created.group.current_user_role, "admin")
            again = await create_group(
                GroupCreateRequest(title="گروه دوم"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(again.group.id, 2)
            with sqlite3.connect(db_path) as conn:
                titles = [row[0] for row in conn.execute("SELECT title FROM chats ORDER BY id")]
                self.assertEqual(titles, ["گروه تست", "گروه دوم"])
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM chat_members").fetchone()[0], 2)
            engine.dispose()

    async def test_patch_group_renames_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-patch.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            created = await create_group(
                GroupCreateRequest(title="گروه یک"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            patched = await patch_group(
                created.group.id,
                GroupUpdateRequest(title="گروه دو"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(patched.title, "گروه دو")
            again = await patch_group(
                created.group.id,
                GroupUpdateRequest(title="گروه دو"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(again.title, "گروه دو")
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT title FROM chats WHERE id=1").fetchone()[0], "گروه دو")
            with self.assertRaises(HTTPException) as raised:
                await patch_group(
                    99,
                    GroupUpdateRequest(title="غایب"),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            self.assertEqual(raised.exception.status_code, 404)
            engine.dispose()

    async def test_post_group_member_adds_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-add.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            created = await create_group(
                GroupCreateRequest(title="گروه اعضا"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            added = await post_group_member(
                created.group.id,
                GroupMemberAddRequest(user_id=2),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(added.user_id, 2)
            self.assertEqual(added.member_count, 2)
            self.assertFalse(added.removed)
            replay = await post_group_member(
                created.group.id,
                GroupMemberAddRequest(user_id=2),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(replay.member_count, 2)
            with sqlite3.connect(db_path) as conn:
                rows = conn.execute(
                    "SELECT user_id FROM chat_members WHERE chat_id=1 ORDER BY user_id"
                ).fetchall()
                self.assertEqual([row[0] for row in rows], [1, 2])
            with self.assertRaises(HTTPException) as raised:
                await post_group_member(
                    99,
                    GroupMemberAddRequest(user_id=2),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            self.assertEqual(raised.exception.status_code, 404)
            engine.dispose()

    async def test_delete_group_member_removes_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-remove.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            created = await create_group(
                GroupCreateRequest(title="گروه حذف"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            await post_group_member(
                created.group.id,
                GroupMemberAddRequest(user_id=2),
                current_user=actor,
                db=CrudSession(Session()),
            )
            removed = await delete_group_member(
                created.group.id,
                2,
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(removed.removed)
            self.assertEqual(removed.member_count, 1)
            with sqlite3.connect(db_path) as conn:
                active = conn.execute(
                    "SELECT COUNT(*) FROM chat_members WHERE chat_id=1 AND membership_status='ACTIVE'"
                ).fetchone()[0]
                self.assertEqual(active, 1)
            with self.assertRaises(HTTPException) as raised:
                await delete_group_member(99, 2, current_user=actor, db=CrudSession(Session()))
            self.assertEqual(raised.exception.status_code, 404)
            engine.dispose()

    async def test_promote_group_admin_promotes_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-promote.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            created = await create_group(
                GroupCreateRequest(title="گروه ارتقا"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            await post_group_member(
                created.group.id,
                GroupMemberAddRequest(user_id=2),
                current_user=actor,
                db=CrudSession(Session()),
            )
            promoted = await promote_group_admin(
                created.group.id,
                2,
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(promoted.role, "admin")
            again = await promote_group_admin(
                created.group.id,
                2,
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(again.role, "admin")
            with sqlite3.connect(db_path) as conn:
                role = conn.execute(
                    "SELECT role FROM chat_members WHERE chat_id=1 AND user_id=2"
                ).fetchone()[0]
                self.assertIn(str(role).lower(), ("admin", "chatmemberrole.admin"))
            with self.assertRaises(HTTPException) as raised:
                await promote_group_admin(99, 2, current_user=actor, db=CrudSession(Session()))
            self.assertEqual(raised.exception.status_code, 404)
            engine.dispose()

    async def test_demote_group_admin_demotes_then_rejects_last_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-demote.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            created = await create_group(
                GroupCreateRequest(title="گروه تنزل"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            await post_group_member(
                created.group.id,
                GroupMemberAddRequest(user_id=2),
                current_user=actor,
                db=CrudSession(Session()),
            )
            await promote_group_admin(
                created.group.id,
                2,
                current_user=actor,
                db=CrudSession(Session()),
            )
            demoted = await demote_group_admin(
                created.group.id,
                2,
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(demoted.role, "member")
            with sqlite3.connect(db_path) as conn:
                role = conn.execute(
                    "SELECT role FROM chat_members WHERE chat_id=1 AND user_id=2"
                ).fetchone()[0]
                self.assertIn(str(role).lower(), ("member", "chatmemberrole.member"))
            with self.assertRaises(HTTPException) as raised:
                await demote_group_admin(
                    created.group.id,
                    1,
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            self.assertEqual(raised.exception.status_code, 400)
            engine.dispose()

    async def test_post_group_leave_leaves_then_rejects_last_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-leave.sqlite")
            engine, Session = _group_chat_store(db_path)
            admin = SimpleNamespace(id=1)
            member = SimpleNamespace(id=2)
            created = await create_group(
                GroupCreateRequest(title="گروه خروج"),
                current_user=admin,
                db=CrudSession(Session()),
            )
            await post_group_member(
                created.group.id,
                GroupMemberAddRequest(user_id=2),
                current_user=admin,
                db=CrudSession(Session()),
            )
            left = await post_group_leave(
                created.group.id,
                current_user=member,
                db=CrudSession(Session()),
            )
            self.assertTrue(left.left)
            self.assertEqual(left.member_count, 1)
            with sqlite3.connect(db_path) as conn:
                active = conn.execute(
                    "SELECT COUNT(*) FROM chat_members WHERE chat_id=1 AND membership_status='ACTIVE'"
                ).fetchone()[0]
                self.assertEqual(active, 1)
            with self.assertRaises(HTTPException) as raised:
                await post_group_leave(
                    created.group.id,
                    current_user=admin,
                    db=CrudSession(Session()),
                )
            self.assertEqual(raised.exception.status_code, 400)
            engine.dispose()

    async def test_pin_room_conversation_pins_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-pin.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            created = await create_group(
                GroupCreateRequest(title="گروه پین"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            pinned = await pin_room_conversation(
                created.group.id,
                ConversationPinUpdateRequest(pinned=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(pinned.is_pinned)
            self.assertEqual(pinned.chat_id, created.group.id)
            again = await pin_room_conversation(
                created.group.id,
                ConversationPinUpdateRequest(pinned=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(again.is_pinned)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT is_pinned FROM chat_members WHERE chat_id=1 AND user_id=1"
                    ).fetchone()[0],
                    1,
                )
            with self.assertRaises(HTTPException) as raised:
                await pin_room_conversation(
                    99,
                    ConversationPinUpdateRequest(pinned=True),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            self.assertEqual(raised.exception.status_code, 404)
            engine.dispose()

    async def test_mute_room_conversation_mutes_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-mute.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            created = await create_group(
                GroupCreateRequest(title="گروه میوت"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            muted = await mute_room_conversation(
                created.group.id,
                ConversationMuteUpdateRequest(muted=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(muted.is_muted)
            again = await mute_room_conversation(
                created.group.id,
                ConversationMuteUpdateRequest(muted=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(again.is_muted)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT is_muted FROM chat_members WHERE chat_id=1 AND user_id=1"
                    ).fetchone()[0],
                    1,
                )
            with self.assertRaises(HTTPException) as raised:
                await mute_room_conversation(
                    99,
                    ConversationMuteUpdateRequest(muted=True),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            self.assertEqual(raised.exception.status_code, 404)
            engine.dispose()

    async def test_get_groups_lists_created_then_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-list.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            empty = await get_groups(current_user=actor, db=CrudSession(Session()))
            self.assertEqual(list(empty), [])
            await create_group(
                GroupCreateRequest(title="گروه فهرست"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            listed = await get_groups(current_user=actor, db=CrudSession(Session()))
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0].title, "گروه فهرست")
            self.assertEqual(listed[0].current_user_role, "admin")
            engine.dispose()

    async def test_get_group_detail_reads_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "group-detail.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = SimpleNamespace(id=1)
            created = await create_group(
                GroupCreateRequest(title="گروه جزئیات"),
                current_user=actor,
                db=CrudSession(Session()),
            )
            detail = await get_group_detail(
                created.group.id,
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertEqual(detail.group.title, "گروه جزئیات")
            self.assertEqual(len(detail.members), 1)
            self.assertEqual(detail.members[0].user_id, 1)
            with self.assertRaises(HTTPException) as raised:
                await get_group_detail(99, current_user=actor, db=CrudSession(Session()))
            self.assertEqual(raised.exception.status_code, 404)
            engine.dispose()

    async def test_pin_direct_conversation_pins_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "direct-pin.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = _load_store_user(Session, 1)
            pinned = await pin_direct_conversation(
                2,
                ConversationPinUpdateRequest(pinned=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(pinned.is_pinned)
            self.assertEqual(pinned.chat_id, 1)
            again = await pin_direct_conversation(
                2,
                ConversationPinUpdateRequest(pinned=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(again.is_pinned)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT is_pinned FROM chat_members WHERE user_id=1"
                    ).fetchone()[0],
                    1,
                )
            with self.assertRaises(HTTPException) as raised:
                await pin_direct_conversation(
                    99,
                    ConversationPinUpdateRequest(pinned=True),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            self.assertIn(raised.exception.status_code, (404, 400))
            engine.dispose()

    async def test_mute_direct_conversation_mutes_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "direct-mute.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = _load_store_user(Session, 1)
            muted = await mute_direct_conversation(
                2,
                ConversationMuteUpdateRequest(muted=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(muted.is_muted)
            again = await mute_direct_conversation(
                2,
                ConversationMuteUpdateRequest(muted=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(again.is_muted)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT is_muted FROM chat_members WHERE user_id=1"
                    ).fetchone()[0],
                    1,
                )
            with self.assertRaises(HTTPException) as raised:
                await mute_direct_conversation(
                    99,
                    ConversationMuteUpdateRequest(muted=True),
                    current_user=actor,
                    db=CrudSession(Session()),
                )
            self.assertIn(raised.exception.status_code, (404, 400))
            engine.dispose()

    async def test_delete_direct_conversation_hides_then_404s(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "direct-hide.sqlite")
            engine, Session = _group_chat_store(db_path)
            actor = _load_store_user(Session, 1)
            await pin_direct_conversation(
                2,
                ConversationPinUpdateRequest(pinned=True),
                current_user=actor,
                db=CrudSession(Session()),
            )
            hidden = await delete_direct_conversation(
                2,
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(hidden.hidden)
            self.assertEqual(hidden.target_id, 2)
            again = await delete_direct_conversation(
                2,
                current_user=actor,
                db=CrudSession(Session()),
            )
            self.assertTrue(again.hidden)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT is_hidden FROM chat_members WHERE user_id=1"
                    ).fetchone()[0],
                    1,
                )
            with self.assertRaises(HTTPException) as raised:
                await delete_direct_conversation(99, current_user=actor, db=CrudSession(Session()))
            self.assertIn(raised.exception.status_code, (404, 400))
            engine.dispose()

    async def test_channel_trade_handlers_gateway_confirm_is_not_crud_evidence(self):
        self.assertTrue(UNEVIDENCED_TRADE_GATEWAY_SEEDS.isdisjoint(SLICE_EXECUTED_SEED_IDS))

        redis_helpers._memory_fallback["confirmations"].clear()
        answers: list[str] = []
        execute = AsyncMock(return_value=None)
        offer = _trade_offer()
        session = SimpleNamespace(refresh=AsyncMock(), rollback=AsyncMock())

        async def _answer(callback, text=None, show_alert=False):
            answers.append(text or "")

        async def _isolated_redis():
            return IsolatedConfirmRedis()

        bot = SimpleNamespace(send_message=AsyncMock())
        channel_data = SimpleNamespace(offer_id=7, amount=1)
        public_data = SimpleNamespace(offer_public_id="ofr_slice", amount=1)
        user = _trade_user()
        patches = [
            patch("bot.handlers.trade_execute.answer_callback_query_via_runtime", new=_answer),
            patch("bot.handlers.trade_execute.AsyncSessionLocal", return_value=_SessionBox(session)),
            patch("bot.handlers.trade_execute._load_callback_offer", new=AsyncMock(return_value=offer)),
            patch(
                "bot.handlers.trade_execute.is_trade_blocked_by_principals",
                new=AsyncMock(return_value=(False, None, None, None)),
            ),
            patch("bot.handlers.trade_execute.is_remote_home", return_value=False),
            patch(
                "bot.handlers.trade_execute.validate_offer_trade_amount",
                return_value=(True, None, 1, [1]),
            ),
            patch("bot.utils.redis_helpers.get_redis_client", new=_isolated_redis),
            patch(
                "bot.handlers.trade_execute._execute_confirmed_channel_trade_via_shared_command",
                new=execute,
            ),
            patch("bot.handlers.trade_execute.edit_callback_reply_markup_via_runtime", new=AsyncMock()),
            patch("bot.handlers.trade_execute.upsert_trade_suggestion_record", new=AsyncMock()),
            patch("bot.handlers.trade_execute.schedule_trade_suggestion_cleanup", new=AsyncMock()),
            patch("bot.handlers.trade_execute.schedule_trade_suggestion_pending_reset", new=AsyncMock()),
            patch("bot.handlers.trade_execute.build_trade_amount_buttons", return_value="KB"),
        ]
        for item in patches:
            item.start()
        try:
            await handle_channel_trade(
                _trade_callback(),
                channel_data,
                user=_trade_user(id=2),
                bot=bot,
            )
            self.assertEqual(execute.await_count, 0)
            self.assertGreaterEqual(session.refresh.await_count, 1)

            await handle_channel_trade(_trade_callback(), channel_data, user=user, bot=bot)
            self.assertEqual(execute.await_count, 0)
            self.assertIn("برای تایید دوباره روی همان دکمه بزنید", answers[-1])

            await handle_channel_trade(_trade_callback(), channel_data, user=user, bot=bot)
            self.assertEqual(execute.await_count, 1)

            redis_helpers._memory_fallback["confirmations"].clear()
            await handle_channel_trade_public(_trade_callback(), public_data, user=user, bot=bot)
            self.assertEqual(execute.await_count, 1)
            await handle_channel_trade_public(_trade_callback(), public_data, user=user, bot=bot)
            self.assertEqual(execute.await_count, 2)

            execute.side_effect = RuntimeError("injected channel_trade_execute_failure")
            redis_helpers._memory_fallback["confirmations"].clear()
            await handle_channel_trade(_trade_callback(), channel_data, user=user, bot=bot)
            with self.assertRaisesRegex(RuntimeError, "injected channel_trade_execute_failure"):
                await handle_channel_trade(_trade_callback(), channel_data, user=user, bot=bot)
        finally:
            for item in reversed(patches):
                item.stop()
            redis_helpers._memory_fallback["confirmations"].clear()
        self.assertGreaterEqual(session.refresh.await_count, 1)


if __name__ == "__main__":
    unittest.main()
