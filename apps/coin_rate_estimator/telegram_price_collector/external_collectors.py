from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
import json
import random
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    OpenerDirector,
    Request,
    build_opener,
    urlopen,
)
from zoneinfo import ZoneInfo

from .models import ExternalMarketObservation
from .normalization import (
    IMAM_COIN_FINENESS,
    IMAM_COIN_GRAMS,
    IME_GOLD_BAR_CERTIFICATE_GRAMS,
    IME_GOLD_BAR_FINENESS,
    MESGHAL_750_GRAMS,
    STANDARD_GOLD_FINENESS,
    ime_coin_irr_per_coin_to_irt_per_coin,
    ime_gold_bar_irr_per_certificate_to_irt_per_mesghal_750,
    imam_intrinsic_from_mesghal_750,
)


WALLEX_HISTORY_URL = "https://api.wallex.ir/v1/udf/history"
WALLEX_DEPTH_URL = "https://api.wallex.ir/v1/depth"
WALLEX_SYMBOL = "USDTTMN"
BINANCE_BOOK_TICKER_URL = "https://data-api.binance.vision/api/v3/ticker/bookTicker"
BINANCE_PAXG_SYMBOLS = ("PAXGUSDC", "PAXGUSDT")
IME_BASE_URL = "https://cdn.ime.co.ir"
IME_GOLD_BAR_SYMBOL = "CD1GOB0001"
IME_GOLD_COIN_SYMBOL = "CD1GOC0001"


class ExternalSourceError(RuntimeError):
    pass


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _http_json(
    url: str,
    *,
    params: dict[str, object] | None = None,
    form: dict[str, object] | None = None,
    timeout: float = 20.0,
    opener: OpenerDirector | None = None,
    headers: dict[str, str] | None = None,
) -> object:
    if params:
        url = f"{url}?{urlencode(params)}"
    request_headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "market-research-poc/0.3 (+read-only public data)",
    }
    if headers:
        request_headers.update(headers)
    body = None
    if form is not None:
        body = urlencode(form).encode("ascii")
        request_headers["Content-Type"] = (
            "application/x-www-form-urlencoded; charset=UTF-8"
        )
    request = Request(url, data=body, headers=request_headers)
    try:
        open_request = opener.open if opener is not None else urlopen
        with open_request(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read().decode(charset)
    except HTTPError as exc:
        try:
            error_body = exc.read(500).decode("utf-8", errors="replace").strip()
        except OSError:
            error_body = ""
        safe_url = urlsplit(url)
        detail = f"; response={error_body!r}" if error_body else ""
        raise ExternalSourceError(
            f"GET {safe_url.scheme}://{safe_url.netloc}{safe_url.path} "
            f"failed: HTTP {exc.code}{detail}"
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        safe_url = urlsplit(url)
        raise ExternalSourceError(
            f"GET {safe_url.scheme}://{safe_url.netloc}{safe_url.path} failed: {exc}"
        ) from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ExternalSourceError(f"GET {url} returned invalid JSON") from exc


def _ime_http_session(*, timeout: float) -> tuple[OpenerDirector, CookieJar]:
    """Open the official board once so IME/WAF cookies bind SignalR requests."""

    cookie_jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    request = Request(
        f"{IME_BASE_URL}/",
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "market-research-poc/0.3 (+read-only public data)",
        },
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ExternalSourceError(f"IME session bootstrap failed: {exc}") from exc
    return opener, cookie_jar


def _fetch_ime_financial_snapshot(*, timeout: float) -> list[dict[str, object]]:
    """Read the same public certificate snapshot requested by IME's page."""

    opener, _ = _ime_http_session(timeout=timeout)
    payload = _http_json(
        f"{IME_BASE_URL}/getFinancialMarketData",
        params={"param": "gavahi"},
        timeout=timeout,
        opener=opener,
        headers={
            "Referer": f"{IME_BASE_URL}/",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    if not isinstance(payload, (dict, list)):
        raise ExternalSourceError("IME financial snapshot response is malformed")
    items = list(_walk_dicts(payload))
    if not items:
        raise ExternalSourceError("IME financial snapshot is empty")
    return items


def _as_decimal(value: object, *, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ExternalSourceError(f"{field} is missing or not numeric")
    cleaned = str(value).strip().replace(",", "").replace("٬", "")
    cleaned = cleaned.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789"))
    try:
        parsed = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ExternalSourceError(f"{field} is not numeric: {value!r}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ExternalSourceError(f"{field} must be a positive finite value")
    return parsed


def _wallex_observation(
    *,
    observed_at: datetime,
    quote_kind: str,
    price: object,
    interval_seconds: int,
    volume: object | None,
) -> ExternalMarketObservation:
    parsed_price = _as_decimal(price, field=f"Wallex {quote_kind}")
    parsed_volume = None
    if volume is not None:
        try:
            parsed_volume = Decimal(str(volume))
        except InvalidOperation:
            parsed_volume = None
    return ExternalMarketObservation(
        source="WALLEX_PUBLIC_API",
        instrument="USDT_IRT",
        symbol=WALLEX_SYMBOL,
        observed_at_utc=iso_utc(observed_at),
        interval_seconds=interval_seconds,
        quote_kind=quote_kind,
        raw_price=parsed_price,
        raw_currency="TMN",
        raw_unit="TMN_PER_USDT",
        normalized_price=parsed_price,
        normalized_currency="IRT",
        normalized_unit="IRT_PER_USDT",
        volume=parsed_volume,
        conversion_formula="identity: Wallex TMN is Iranian toman (IRT)",
    )


def _binance_paxg_midpoint(payload: object, *, symbol: str) -> Decimal:
    if not isinstance(payload, dict) or str(payload.get("symbol") or "") != symbol:
        raise ExternalSourceError(f"Binance {symbol} response is malformed")
    bid = _as_decimal(payload.get("bidPrice"), field=f"Binance {symbol} bid")
    ask = _as_decimal(payload.get("askPrice"), field=f"Binance {symbol} ask")
    if ask < bid:
        raise ExternalSourceError(f"Binance {symbol} crossed book")
    midpoint = (bid + ask) / Decimal("2")
    spread_ratio = (ask - bid) / midpoint
    if spread_ratio > Decimal("0.005"):
        raise ExternalSourceError(f"Binance {symbol} spread is too wide")
    return midpoint


def fetch_binance_paxg_live(
    *, timeout: float = 8.0, observed_at: datetime | None = None
) -> list[ExternalMarketObservation]:
    """Return one corroborated gold-ounce proxy from two PAXG books.

    PAXG represents one fine troy ounce of London Good Delivery gold.  The
    stablecoin books are still a proxy for XAU/USD, so both markets must agree
    within a narrow relative band and downstream inference labels the result
    as estimated rather than observed XAU/USD.
    """

    midpoints = {
        symbol: _binance_paxg_midpoint(
            _http_json(
                BINANCE_BOOK_TICKER_URL,
                params={"symbol": symbol},
                timeout=timeout,
            ),
            symbol=symbol,
        )
        for symbol in BINANCE_PAXG_SYMBOLS
    }
    low = min(midpoints.values())
    high = max(midpoints.values())
    center = sum(midpoints.values(), Decimal("0")) / Decimal(len(midpoints))
    if (high - low) / center > Decimal("0.005"):
        raise ExternalSourceError("Binance PAXG stablecoin books diverged")
    stamp = observed_at or datetime.now(timezone.utc)
    return [
        ExternalMarketObservation(
            source="BINANCE_PUBLIC_API",
            instrument="PAXG_USD_PROXY",
            symbol="+".join(BINANCE_PAXG_SYMBOLS),
            observed_at_utc=iso_utc(stamp),
            quote_kind="MID",
            raw_price=center,
            raw_currency="USDC_USDT_BASKET",
            raw_unit="STABLECOIN_PER_PAXG",
            normalized_price=center,
            normalized_currency="USD_PROXY",
            normalized_unit="USD_PROXY_PER_TROY_OUNCE",
            conversion_formula=(
                "mean(mid(PAXGUSDC),mid(PAXGUSDT)); "
                "PAXG represents one fine troy ounce; proxy, not direct XAUUSD"
            ),
        )
    ]


def fetch_wallex_history(
    *,
    start: datetime,
    end: datetime,
    resolution_minutes: int = 1,
    chunk_days: int = 1,
) -> list[ExternalMarketObservation]:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must be timezone-aware")
    if start >= end:
        raise ValueError("start must be before end")
    if resolution_minutes <= 0 or chunk_days <= 0:
        raise ValueError("resolution_minutes and chunk_days must be positive")

    observations: list[ExternalMarketObservation] = []
    cursor = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    seen: set[tuple[int, str]] = set()
    keys = ("t", "o", "h", "l", "c", "v")

    while cursor < end_utc:
        chunk_end = min(cursor + timedelta(days=chunk_days), end_utc)
        payload = _http_json(
            WALLEX_HISTORY_URL,
            params={
                "symbol": WALLEX_SYMBOL,
                "resolution": resolution_minutes,
                "from": int(cursor.timestamp()),
                "to": int(chunk_end.timestamp()),
            },
        )
        if not isinstance(payload, dict):
            raise ExternalSourceError("Wallex history response is not an object")
        if payload.get("s") == "no_data":
            cursor = chunk_end
            continue
        if payload.get("s") != "ok":
            raise ExternalSourceError(f"Wallex history returned status {payload.get('s')!r}")
        arrays = [payload.get(key) for key in keys]
        if not all(isinstance(item, list) for item in arrays):
            raise ExternalSourceError("Wallex history is missing OHLCV arrays")
        lengths = {len(item) for item in arrays if isinstance(item, list)}
        if len(lengths) != 1:
            raise ExternalSourceError("Wallex OHLCV arrays have different lengths")

        timestamps, opens, highs, lows, closes, volumes = arrays
        assert isinstance(timestamps, list)
        assert isinstance(opens, list)
        assert isinstance(highs, list)
        assert isinstance(lows, list)
        assert isinstance(closes, list)
        assert isinstance(volumes, list)
        for timestamp, open_price, high, low, close, volume in zip(
            timestamps, opens, highs, lows, closes, volumes
        ):
            try:
                epoch = int(timestamp)
            except (TypeError, ValueError) as exc:
                raise ExternalSourceError("Wallex candle timestamp is invalid") from exc
            if epoch < int(start.timestamp()) or epoch > int(end.timestamp()):
                continue
            try:
                candle_volume = Decimal(str(volume))
            except InvalidOperation as exc:
                raise ExternalSourceError("Wallex candle volume is invalid") from exc
            # UDF may emit a carried close for a minute with no trade. It must
            # remain missing for the model rather than looking newly observed.
            if not candle_volume.is_finite() or candle_volume <= 0:
                continue
            candle_time = datetime.fromtimestamp(epoch, tz=timezone.utc)
            dedupe_key = (epoch, "CLOSE")
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            observations.append(
                _wallex_observation(
                    observed_at=candle_time,
                    quote_kind="CLOSE",
                    price=close,
                    interval_seconds=resolution_minutes * 60,
                    volume=candle_volume,
                )
            )
        cursor = chunk_end
    return observations


def fetch_wallex_live() -> list[ExternalMarketObservation]:
    payload = _http_json(WALLEX_DEPTH_URL, params={"symbol": WALLEX_SYMBOL})
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        raise ExternalSourceError("Wallex depth response is malformed")
    result = payload["result"]
    asks = result.get("ask")
    bids = result.get("bid")
    if not isinstance(asks, list) or not isinstance(bids, list) or not asks or not bids:
        raise ExternalSourceError("Wallex depth has no bid/ask rows")
    try:
        best_ask = min(_as_decimal(row.get("price"), field="Wallex ask") for row in asks)
        best_bid = max(_as_decimal(row.get("price"), field="Wallex bid") for row in bids)
    except AttributeError as exc:
        raise ExternalSourceError("Wallex depth rows are malformed") from exc
    if best_ask < best_bid:
        raise ExternalSourceError("Wallex best ask is below best bid")
    observed_at = datetime.now(timezone.utc)
    return [
        _wallex_observation(
            observed_at=observed_at,
            quote_kind=kind,
            price=price,
            interval_seconds=0,
            volume=None,
        )
        for kind, price in (
            ("BID", best_bid),
            ("ASK", best_ask),
            ("MID", (best_bid + best_ask) / Decimal("2")),
        )
    ]


def _walk_dicts(value: object) -> Iterable[dict[str, object]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_dicts(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_dicts(nested)


def _casefold_item(item: dict[str, object]) -> dict[str, object]:
    return {str(key).casefold(): value for key, value in item.items()}


def _first_field(item: dict[str, object], aliases: tuple[str, ...]) -> object | None:
    folded = _casefold_item(item)
    for alias in aliases:
        if alias.casefold() in folded:
            return folded[alias.casefold()]
    return None


def _ime_symbol(item: dict[str, object]) -> str | None:
    value = _first_field(
        item,
        ("ContractCode", "Symbol", "InstrumentID", "ContractSymbol", "CommodityCode"),
    )
    if value is None:
        return None
    return str(value).strip().upper().replace(" ", "")


def _ime_has_target(items: Iterable[dict[str, object]]) -> bool:
    target_aliases = {
        IME_GOLD_BAR_SYMBOL,
        IME_GOLD_COIN_SYMBOL,
        "CD1G0B0001",
        "CD1G0C0001",
        "GOLDBAR",
        "GOLDCOIN",
    }
    return any(_ime_symbol(item) in target_aliases for item in items)


def _fetch_ime_long_poll_items(
    *,
    opener: OpenerDirector,
    negotiate: dict[str, object],
    connection_data: str,
    timeout: float,
    max_polls: int = 4,
) -> list[dict[str, object]]:
    """Read the legacy SignalR feed exactly as IME's public page does.

    The public IME endpoint currently negotiates WebSockets but intermittently
    rejects the upgrade.  Long polling is an advertised SignalR transport and
    delivers the same initial CDC snapshot without HTML scraping.
    """

    token = negotiate.get("ConnectionToken")
    if not token:
        raise ExternalSourceError("IME negotiate response has no connection token")
    common: dict[str, object] = {
        "transport": "longPolling",
        "clientProtocol": "2.1",
        "connectionToken": str(token),
        "connectionData": connection_data,
    }
    signalr_headers = {
        "Referer": f"{IME_BASE_URL}/",
        "X-Requested-With": "XMLHttpRequest",
    }
    state = _http_json(
        f"{IME_BASE_URL}/realTimeServer/connect",
        params=common,
        timeout=timeout,
        opener=opener,
        headers=signalr_headers,
        form={},
    )
    if not isinstance(state, dict):
        raise ExternalSourceError("IME long-poll connect response is malformed")
    started = _http_json(
        f"{IME_BASE_URL}/realTimeServer/start",
        params=common,
        timeout=timeout,
        opener=opener,
        headers=signalr_headers,
    )
    if not isinstance(started, dict) or started.get("Response") != "started":
        raise ExternalSourceError("IME long-poll start was not acknowledged")

    messages: list[object] = [state]
    candidates = [item for message in messages for item in _walk_dicts(message)]
    polls = 0
    while not _ime_has_target(candidates) and polls < max_polls:
        cursor = state.get("C")
        if not cursor:
            break
        poll_form: dict[str, object] = {"messageId": str(cursor)}
        if state.get("G"):
            poll_form["groupsToken"] = str(state["G"])
        state = _http_json(
            f"{IME_BASE_URL}/realTimeServer/poll",
            params=common,
            timeout=timeout,
            opener=opener,
            headers=signalr_headers,
            form=poll_form,
        )
        if not isinstance(state, dict):
            raise ExternalSourceError("IME long-poll response is malformed")
        messages.append(state)
        candidates = [item for message in messages for item in _walk_dicts(message)]
        polls += 1
    return candidates


def _fetch_ime_sse_items(
    *,
    opener: OpenerDirector,
    negotiate: dict[str, object],
    connection_data: str,
    timeout: float,
) -> list[dict[str, object]]:
    """Use SignalR's official serverSentEvents fallback contract."""

    token = negotiate.get("ConnectionToken")
    if not token:
        raise ExternalSourceError("IME negotiate response has no connection token")
    common: dict[str, object] = {
        "transport": "serverSentEvents",
        "clientProtocol": "2.1",
        "connectionToken": str(token),
        "connectionData": connection_data,
    }
    connect_params = dict(common)
    connect_params["tid"] = random.randint(0, 10)
    connect_url = (
        f"{IME_BASE_URL}/realTimeServer/connect?{urlencode(connect_params)}"
    )
    request = Request(
        connect_url,
        headers={
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Referer": f"{IME_BASE_URL}/",
            "User-Agent": "market-research-poc/0.3 (+read-only public data)",
        },
    )
    messages: list[object] = []
    started = False
    deadline = time.monotonic() + max(8.0, timeout * 2)
    try:
        with opener.open(request, timeout=timeout) as response:
            for _ in range(80):
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if time.monotonic() >= deadline:
                    break
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "initialized":
                    continue
                try:
                    decoded = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                messages.append(decoded)
                candidates = [
                    item
                    for message in messages
                    for item in _walk_dicts(message)
                ]
                if not started and any(item.get("S") == 1 for item in candidates):
                    start = _http_json(
                        f"{IME_BASE_URL}/realTimeServer/start",
                        params=common,
                        timeout=timeout,
                        opener=opener,
                        headers={
                            "Referer": f"{IME_BASE_URL}/",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                    )
                    if (
                        not isinstance(start, dict)
                        or start.get("Response") != "started"
                    ):
                        raise ExternalSourceError(
                            "IME SSE start was not acknowledged"
                        )
                    started = True
                if started and _ime_has_target(candidates):
                    return candidates
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ExternalSourceError(f"IME server-sent events failed: {exc}") from exc
    if not started:
        raise ExternalSourceError("IME server-sent events had no init message")
    return [
        item for message in messages for item in _walk_dicts(message)
    ]


async def _fetch_ime_hub_items(hub_name: str, *, timeout: float) -> list[dict[str, object]]:
    opener, cookie_jar = await asyncio.to_thread(
        _ime_http_session,
        timeout=timeout,
    )
    connection_data = _json_dumps([{"name": hub_name}])
    negotiate = await asyncio.to_thread(
        _http_json,
        f"{IME_BASE_URL}/realTimeServer/negotiate",
        params={"clientProtocol": "2.1", "connectionData": connection_data},
        timeout=timeout,
        opener=opener,
    )
    if not isinstance(negotiate, dict) or not negotiate.get("ConnectionToken"):
        raise ExternalSourceError(f"IME {hub_name} negotiate response has no token")
    long_poll_detail = "unknown long-poll failure"
    try:
        return await asyncio.to_thread(
            _fetch_ime_long_poll_items,
            opener=opener,
            negotiate=negotiate,
            connection_data=connection_data,
            timeout=timeout,
        )
    except ExternalSourceError as long_poll_error:
        # Keep the negotiated WebSocket transport as a fallback for a future
        # server-side change, while preferring the transport proven by IME's
        # current public board.
        long_poll_detail = str(long_poll_error)
    sse_detail = "unknown server-sent events failure"
    try:
        return await asyncio.to_thread(
            _fetch_ime_sse_items,
            opener=opener,
            negotiate=negotiate,
            connection_data=connection_data,
            timeout=timeout,
        )
    except ExternalSourceError as sse_error:
        sse_detail = str(sse_error)
    try:
        import websockets
    except ImportError as exc:
        raise ExternalSourceError(
            f"IME {hub_name} long-poll failed: {long_poll_detail}; "
            f"server-sent events failed: {sse_detail}; "
            "websocket fallback dependency is unavailable"
        ) from exc
    parameters: dict[str, object] = {
        "transport": "webSockets",
        "clientProtocol": "2.1",
        "connectionToken": str(negotiate["ConnectionToken"]),
        "connectionData": connection_data,
    }
    websocket_parameters = dict(parameters)
    websocket_parameters["tid"] = random.randint(0, 10)
    uri = (
        "wss://cdn.ime.co.ir/realTimeServer/connect?"
        f"{urlencode(websocket_parameters)}"
    )
    cookie_header = "; ".join(
        f"{cookie.name}={cookie.value}" for cookie in cookie_jar
    )
    messages: list[object] = []
    try:
        async with websockets.connect(
            uri,
            origin=IME_BASE_URL,
            additional_headers={
                "Cookie": cookie_header,
                "Referer": f"{IME_BASE_URL}/",
            },
            open_timeout=timeout,
            close_timeout=3,
            ping_interval=None,
            proxy=None,
            user_agent_header="market-research-poc/0.3 (+read-only public data)",
        ) as websocket:
            start_task = asyncio.create_task(
                asyncio.to_thread(
                    _http_json,
                    f"{IME_BASE_URL}/realTimeServer/start",
                    params=parameters,
                    timeout=timeout,
                    opener=opener,
                )
            )
            for _ in range(12):
                try:
                    frame = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                try:
                    decoded = json.loads(frame)
                except (json.JSONDecodeError, TypeError):
                    continue
                messages.append(decoded)
                candidates = [item for message in messages for item in _walk_dicts(message)]
                if any(_ime_symbol(item) in {IME_GOLD_BAR_SYMBOL, IME_GOLD_COIN_SYMBOL} for item in candidates):
                    break
            await start_task
    except (OSError, TimeoutError, asyncio.TimeoutError, websockets.WebSocketException) as exc:
        raise ExternalSourceError(
            f"IME {hub_name} long-poll failed: {long_poll_detail}; "
            f"server-sent events failed: {sse_detail}; "
            f"websocket failed: {exc}"
        ) from exc
    return [item for message in messages for item in _walk_dicts(message)]


def _ime_quote_fields(item: dict[str, object]) -> list[tuple[str, object]]:
    aliases = {
        "OPEN": ("FirstTradedPrice", "FirstPrice", "OpeningPrice"),
        "HIGH": ("HighestTradePrice", "HighTradedPrice", "HighestPrice", "HighPrice"),
        "LOW": ("LowestTradePrice", "LowTradedPrice", "LowestPrice", "LowPrice"),
        "CLOSE": (
            "ClosingPrice",
            "LastSettlementPrice",
            "SettlementPrice",
            "FinalPrice",
        ),
        "LAST": ("LastTradedPrice", "LastPrice", "Last"),
        "BID": ("BestBidPrice", "BidPrice1", "BidPrice", "BuyPrice"),
        "ASK": ("BestAskPrice", "AskPrice1", "AskPrice", "SellPrice"),
    }
    found: list[tuple[str, object]] = []
    for kind, names in aliases.items():
        value = _first_field(item, names)
        if value not in (None, "", 0, "0"):
            found.append((kind, value))
    return found


def _ime_item_observed_at(item: dict[str, object], fallback: datetime) -> datetime:
    value = _first_field(item, ("LastUpdate", "LastUpdateTime", "TradeDateTime"))
    if value in (None, ""):
        return fallback
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Tehran"))
    return parsed.astimezone(timezone.utc)


def parse_ime_items(
    items: Iterable[dict[str, object]],
    *,
    observed_at: datetime | None = None,
) -> list[ExternalMarketObservation]:
    now = observed_at or datetime.now(timezone.utc)
    output: list[ExternalMarketObservation] = []
    selected: dict[str, dict[str, object]] = {}
    aliases = {
        "CD1G0B0001": IME_GOLD_BAR_SYMBOL,
        "CD1G0C0001": IME_GOLD_COIN_SYMBOL,
        "GOLDBAR": IME_GOLD_BAR_SYMBOL,
        "GOLDCOIN": IME_GOLD_COIN_SYMBOL,
    }
    for item in items:
        symbol = _ime_symbol(item)
        if symbol in aliases:
            symbol = aliases[symbol]
        if symbol in {IME_GOLD_BAR_SYMBOL, IME_GOLD_COIN_SYMBOL}:
            selected[symbol] = item

    for symbol, item in selected.items():
        item_observed_at = _ime_item_observed_at(item, now)
        for kind, raw_value in _ime_quote_fields(item):
            raw_price = _as_decimal(raw_value, field=f"IME {symbol} {kind}")
            if symbol == IME_GOLD_BAR_SYMBOL:
                normalized = ime_gold_bar_irr_per_certificate_to_irt_per_mesghal_750(
                    raw_price
                )
                observation = ExternalMarketObservation(
                    source="IME_REALTIME_BOARD",
                    instrument="IME_GOLD_BAR",
                    symbol=symbol,
                    observed_at_utc=iso_utc(item_observed_at),
                    quote_kind=kind,
                    raw_price=raw_price,
                    raw_currency="IRR",
                    raw_unit="IRR_PER_CERTIFICATE_0_1G_995",
                    raw_fineness=IME_GOLD_BAR_FINENESS,
                    raw_weight_gram=IME_GOLD_BAR_CERTIFICATE_GRAMS,
                    normalized_price=normalized,
                    normalized_currency="IRT",
                    normalized_unit="IRT_PER_MESGHAL_750",
                    normalized_fineness=STANDARD_GOLD_FINENESS,
                    normalized_weight_gram=MESGHAL_750_GRAMS,
                    conversion_formula="Q_IRR_per_0.1g_995 * (750/995) * 4.3318",
                )
            else:
                normalized = ime_coin_irr_per_coin_to_irt_per_coin(raw_price)
                observation = ExternalMarketObservation(
                    source="IME_REALTIME_BOARD",
                    instrument="IME_GOLD_COIN_IMAM",
                    symbol=symbol,
                    observed_at_utc=iso_utc(item_observed_at),
                    quote_kind=kind,
                    raw_price=raw_price,
                    raw_currency="IRR",
                    raw_unit="IRR_PER_COIN",
                    raw_fineness=IMAM_COIN_FINENESS,
                    raw_weight_gram=IMAM_COIN_GRAMS,
                    normalized_price=normalized,
                    normalized_currency="IRT",
                    normalized_unit="IRT_PER_COIN",
                    conversion_formula="Q_IRR_per_coin / 10",
                )
            output.append(observation)

    bar_by_kind = {
        row.quote_kind: row
        for row in output
        if row.symbol == IME_GOLD_BAR_SYMBOL and row.normalized_price is not None
    }
    coin_by_kind = {
        row.quote_kind: row
        for row in output
        if row.symbol == IME_GOLD_COIN_SYMBOL and row.normalized_price is not None
    }
    for kind in sorted(set(bar_by_kind) & set(coin_by_kind)):
        # Bubble extrema move inversely with the underlying gold value:
        # maximum coin minus minimum gold, and minimum coin minus maximum gold.
        bar_kind = {"HIGH": "LOW", "LOW": "HIGH"}.get(kind, kind)
        bar = bar_by_kind.get(bar_kind)
        if bar is None:
            continue
        coin = coin_by_kind[kind]
        assert bar.normalized_price is not None
        assert coin.normalized_price is not None
        intrinsic = imam_intrinsic_from_mesghal_750(bar.normalized_price)
        bubble = coin.normalized_price - intrinsic
        output.append(
            ExternalMarketObservation(
                source="IME_DERIVED",
                instrument="IME_GOLD_COIN_IMAM_BUBBLE",
                symbol=f"{IME_GOLD_COIN_SYMBOL}:{IME_GOLD_BAR_SYMBOL}",
                observed_at_utc=max(bar.observed_at_utc, coin.observed_at_utc),
                quote_kind=kind,
                raw_price=coin.normalized_price,
                raw_currency="IRT",
                raw_unit="IRT_PER_COIN",
                normalized_price=bubble,
                normalized_currency="IRT",
                normalized_unit="IRT_BUBBLE_PER_COIN",
                conversion_formula=(
                    "coin_quote_irt - mesghal_quote_750_irt * 2.253; "
                    "HIGH uses coin HIGH/gold LOW and LOW uses coin LOW/gold HIGH"
                ),
            )
        )
    return output


async def fetch_ime_live(
    *, timeout: float = 12.0, handshake_attempts: int = 4
) -> list[ExternalMarketObservation]:
    if handshake_attempts <= 0:
        raise ValueError("handshake_attempts must be positive")
    errors: list[str] = []
    combined: list[dict[str, object]] = []
    try:
        snapshot_items = await asyncio.to_thread(
            _fetch_ime_financial_snapshot, timeout=timeout
        )
        snapshot = parse_ime_items(snapshot_items)
        snapshot_symbols = {row.symbol for row in snapshot}
        if (
            IME_GOLD_BAR_SYMBOL in snapshot_symbols
            and IME_GOLD_COIN_SYMBOL in snapshot_symbols
        ):
            return snapshot
        errors.append("financial snapshot did not contain both gold contracts")
    except ExternalSourceError as exc:
        errors.append(f"financial snapshot: {exc}")
    # CDC gold/coin rows are published by marketsHub.  New negotiations are
    # intentional: the CDN occasionally returns a token rejected by a
    # different backend during connect, while the next fresh session works.
    for attempt in range(1, handshake_attempts + 1):
        try:
            combined.extend(await _fetch_ime_hub_items("marketshub", timeout=timeout))
            parsed = parse_ime_items(combined)
            symbols = {row.symbol for row in parsed}
            if IME_GOLD_BAR_SYMBOL in symbols and IME_GOLD_COIN_SYMBOL in symbols:
                return parsed
        except ExternalSourceError as exc:
            errors.append(f"attempt {attempt}: {exc}")
        if attempt < handshake_attempts:
            await asyncio.sleep(0.35 * attempt)
    parsed = parse_ime_items(combined)
    if parsed:
        return parsed
    detail = "; ".join(errors) if errors else "target symbols were absent"
    raise ExternalSourceError(f"IME board returned no gold/coin quotes: {detail}")
