from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core.trading_settings import TradingSettings
from models.market_schedule_override import MarketScheduleOverride, MarketScheduleOverrideType


DEFAULT_MARKET_TIMEZONE = "Asia/Tehran"
NEXT_TRANSITION_SEARCH_DAYS = 14


@dataclass(slots=True)
class MarketScheduleEvaluation:
    is_open: bool
    reason: str
    next_transition_at: datetime | None
    timezone: str
    current_transition_at: datetime | None = None


@dataclass(slots=True)
class _DayRule:
    source: str
    all_day_open: bool = False
    all_day_closed: bool = False
    open_time_local: time | None = None
    close_time_local: time | None = None


def get_market_timezone_name(trading_settings: TradingSettings) -> str:
    timezone_name = (trading_settings.market_timezone or DEFAULT_MARKET_TIMEZONE).strip()
    if not timezone_name:
        return DEFAULT_MARKET_TIMEZONE
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return DEFAULT_MARKET_TIMEZONE
    return timezone_name


def get_market_timezone(trading_settings: TradingSettings) -> ZoneInfo:
    return ZoneInfo(get_market_timezone_name(trading_settings))


def _parse_local_time(value: str | None) -> time | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return time.fromisoformat(normalized)
    except ValueError:
        return None


def _normalize_closed_weekdays(trading_settings: TradingSettings) -> set[int]:
    normalized: set[int] = set()
    for raw_value in trading_settings.market_closed_weekdays:
        try:
            weekday = int(raw_value)
        except (TypeError, ValueError):
            continue
        if 0 <= weekday <= 6:
            normalized.add(weekday)
    return normalized


def _override_mapping(overrides: Sequence[MarketScheduleOverride]) -> dict[date, MarketScheduleOverride]:
    return {override.date: override for override in overrides if getattr(override, "date", None) is not None}


def _resolve_day_rule(
    target_date: date,
    trading_settings: TradingSettings,
    overrides_by_date: dict[date, MarketScheduleOverride],
) -> _DayRule:
    override = overrides_by_date.get(target_date)
    if override is not None:
        if override.override_type == MarketScheduleOverrideType.CLOSED_ALL_DAY:
            return _DayRule(source="override_closed_all_day", all_day_closed=True)
        if override.override_type == MarketScheduleOverrideType.OPEN_ALL_DAY:
            return _DayRule(source="override_open_all_day", all_day_open=True)
        if override.override_type == MarketScheduleOverrideType.CUSTOM_HOURS:
            open_time_local = override.open_time_local
            close_time_local = override.close_time_local
            if open_time_local is None or close_time_local is None or open_time_local >= close_time_local:
                return _DayRule(source="invalid_schedule", all_day_closed=True)
            return _DayRule(
                source="override_custom_hours",
                open_time_local=open_time_local,
                close_time_local=close_time_local,
            )

    if target_date.weekday() in _normalize_closed_weekdays(trading_settings):
        return _DayRule(source="weekly_closed_day", all_day_closed=True)

    open_time_local = _parse_local_time(trading_settings.market_open_time_local)
    close_time_local = _parse_local_time(trading_settings.market_close_time_local)
    if open_time_local is None or close_time_local is None or open_time_local >= close_time_local:
        return _DayRule(source="invalid_schedule", all_day_closed=True)

    return _DayRule(
        source="daily_window",
        open_time_local=open_time_local,
        close_time_local=close_time_local,
    )


def _coerce_local_datetime(current_time: datetime | None, timezone_info: ZoneInfo) -> datetime:
    if current_time is None:
        return datetime.now(timezone_info)
    if current_time.tzinfo is None:
        return current_time.replace(tzinfo=timezone_info)
    return current_time.astimezone(timezone_info)


def _is_open_for_rule(local_dt: datetime, rule: _DayRule) -> bool:
    if rule.source == "invalid_schedule":
        return False
    if rule.all_day_open:
        return True
    if rule.all_day_closed:
        return False
    current_time_local = local_dt.timetz().replace(tzinfo=None)
    return bool(rule.open_time_local and rule.close_time_local and rule.open_time_local <= current_time_local < rule.close_time_local)


def _is_market_open_at(
    local_dt: datetime,
    trading_settings: TradingSettings,
    overrides_by_date: dict[date, MarketScheduleOverride],
) -> bool:
    if not trading_settings.market_schedule_enabled:
        return True
    day_rule = _resolve_day_rule(local_dt.date(), trading_settings, overrides_by_date)
    return _is_open_for_rule(local_dt, day_rule)


def _reason_for_local_time(local_dt: datetime, day_rule: _DayRule) -> str:
    if day_rule.source == "invalid_schedule":
        return "invalid_schedule"
    if day_rule.all_day_open:
        return day_rule.source
    if day_rule.all_day_closed:
        return day_rule.source
    current_time_local = local_dt.timetz().replace(tzinfo=None)
    if day_rule.open_time_local is not None and current_time_local < day_rule.open_time_local:
        return f"before_{day_rule.source}_open"
    if day_rule.close_time_local is not None and current_time_local >= day_rule.close_time_local:
        return f"after_{day_rule.source}_close"
    return f"{day_rule.source}_open"


def _candidate_transition_datetimes(
    now_local: datetime,
    trading_settings: TradingSettings,
    overrides_by_date: dict[date, MarketScheduleOverride],
) -> list[datetime]:
    candidates: set[datetime] = set()
    for offset in range(NEXT_TRANSITION_SEARCH_DAYS + 1):
        target_date = now_local.date() + timedelta(days=offset)
        midnight = datetime.combine(target_date, time.min, tzinfo=now_local.tzinfo)
        if midnight > now_local:
            candidates.add(midnight)

        day_rule = _resolve_day_rule(target_date, trading_settings, overrides_by_date)
        if day_rule.source == "invalid_schedule":
            continue
        if day_rule.open_time_local is not None:
            open_dt = datetime.combine(target_date, day_rule.open_time_local, tzinfo=now_local.tzinfo)
            if open_dt > now_local:
                candidates.add(open_dt)
        if day_rule.close_time_local is not None:
            close_dt = datetime.combine(target_date, day_rule.close_time_local, tzinfo=now_local.tzinfo)
            if close_dt > now_local:
                candidates.add(close_dt)

    return sorted(candidates)


def _next_transition_at(
    now_local: datetime,
    trading_settings: TradingSettings,
    overrides_by_date: dict[date, MarketScheduleOverride],
) -> datetime | None:
    if not trading_settings.market_schedule_enabled:
        return None
    current_state = _is_market_open_at(now_local, trading_settings, overrides_by_date)
    for candidate in _candidate_transition_datetimes(now_local, trading_settings, overrides_by_date):
        before_candidate = candidate - timedelta(microseconds=1)
        before_state = _is_market_open_at(before_candidate, trading_settings, overrides_by_date)
        after_state = _is_market_open_at(candidate, trading_settings, overrides_by_date)
        if before_state != after_state and after_state != current_state:
            return candidate
        if before_state != after_state:
            return candidate
    return None


def _current_transition_at(
    now_local: datetime,
    trading_settings: TradingSettings,
    overrides_by_date: dict[date, MarketScheduleOverride],
) -> datetime | None:
    if not trading_settings.market_schedule_enabled:
        return None
    current_state = _is_market_open_at(now_local, trading_settings, overrides_by_date)
    candidates: set[datetime] = set()
    for offset in range(-NEXT_TRANSITION_SEARCH_DAYS, 1):
        target_date = now_local.date() + timedelta(days=offset)
        midnight = datetime.combine(target_date, time.min, tzinfo=now_local.tzinfo)
        if midnight <= now_local:
            candidates.add(midnight)

        day_rule = _resolve_day_rule(target_date, trading_settings, overrides_by_date)
        if day_rule.source == "invalid_schedule":
            continue
        if day_rule.open_time_local is not None:
            open_dt = datetime.combine(target_date, day_rule.open_time_local, tzinfo=now_local.tzinfo)
            if open_dt <= now_local:
                candidates.add(open_dt)
        if day_rule.close_time_local is not None:
            close_dt = datetime.combine(target_date, day_rule.close_time_local, tzinfo=now_local.tzinfo)
            if close_dt <= now_local:
                candidates.add(close_dt)

    for candidate in sorted(candidates, reverse=True):
        before_candidate = candidate - timedelta(microseconds=1)
        before_state = _is_market_open_at(before_candidate, trading_settings, overrides_by_date)
        after_state = _is_market_open_at(candidate, trading_settings, overrides_by_date)
        if before_state != after_state and after_state == current_state:
            return candidate
    return None


def evaluate_market_schedule(
    trading_settings: TradingSettings,
    *,
    current_time: datetime | None = None,
    overrides: Sequence[MarketScheduleOverride] = (),
) -> MarketScheduleEvaluation:
    timezone_info = get_market_timezone(trading_settings)
    timezone_name = get_market_timezone_name(trading_settings)
    now_local = _coerce_local_datetime(current_time, timezone_info)
    if not trading_settings.market_schedule_enabled:
        return MarketScheduleEvaluation(
            is_open=True,
            reason="schedule_disabled",
            next_transition_at=None,
            timezone=timezone_name,
            current_transition_at=None,
        )

    overrides_by_date = _override_mapping(overrides)
    day_rule = _resolve_day_rule(now_local.date(), trading_settings, overrides_by_date)
    return MarketScheduleEvaluation(
        is_open=_is_open_for_rule(now_local, day_rule),
        reason=_reason_for_local_time(now_local, day_rule),
        next_transition_at=_next_transition_at(now_local, trading_settings, overrides_by_date),
        timezone=timezone_name,
        current_transition_at=_current_transition_at(now_local, trading_settings, overrides_by_date),
    )
