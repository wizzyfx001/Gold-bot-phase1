"""
WIZZY AUTONOMOUS TRADING BOT
STAGE 2.3 - MULTI-SYMBOL LIVE MARKET SCANNER

GitHub -> Railway -> MetaApi G2 -> Headway MT5 Demo -> Telegram

Features:
- Persistent MetaApi streaming connection
- Five forex/gold instruments
- Automatic broker symbol detection
- Live bid/ask prices
- Spread calculation
- Stale quote detection
- Combined Telegram report every 5 minutes
- Connection monitoring and automatic reconnection

IMPORTANT:
AUTOMATIC TRADING IS DISABLED.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone

from metaapi_cloud_sdk import MetaApi
from telegram import Bot


# ==========================================
# CONFIGURATION
# ==========================================

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "XAUUSD"
]

REPORT_INTERVAL = 300
RECONNECT_DELAY = 30

MAX_QUOTE_AGE = 180
STARTUP_QUOTE_WAIT = 30


# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("WIZZY")

logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


# ==========================================
# TELEGRAM
# ==========================================

async def send_message(message):

    logger.info("%s", message)

    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials missing")
        return

    try:

        async with Bot(token=TELEGRAM_TOKEN) as bot:

            await bot.send_message(
                chat_id=CHAT_ID,
                text=message[:3900]
            )

    except Exception as error:

        logger.error(
            "Telegram error: %s",
            error
        )


# ==========================================
# HELPERS
# ==========================================

def get_value(obj, key, default=None):

    if isinstance(obj, dict):
        return obj.get(key, default)

    return getattr(obj, key, default)


def parse_time(value):

    if value is None:
        return None

    if isinstance(value, datetime):

        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)

        return value.astimezone(timezone.utc)

    if isinstance(value, (int, float)):

        try:
            return datetime.fromtimestamp(
                value,
                tz=timezone.utc
            )
        except Exception:
            return None

    if isinstance(value, str):

        try:

            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ).astimezone(timezone.utc)

        except Exception:
            return None

    return None


def pip_size_for(symbol):

    if symbol.startswith("XAUUSD"):
        return 0.01

    if symbol.startswith("USDJPY"):
        return 0.01

    return 0.0001


def price_digits_for(symbol):

    if symbol.startswith("XAUUSD"):
        return 2

    if symbol.startswith("USDJPY"):
        return 3

    return 5


# ==========================================
# SYMBOL DETECTION
# ==========================================

def resolve_symbols(available_symbols):

    """
    Resolve broker-specific names.

    Examples:
    EURUSD
    EURUSDm
    EURUSD.a
    XAUUSD
    XAUUSDm
    """

    available = [
        str(symbol)
        for symbol in available_symbols
    ]

    resolved = {}
    missing = []

    for requested in SYMBOLS:

        # Prefer exact matches.
        exact = next(
            (
                symbol
                for symbol in available
                if symbol.upper() == requested
            ),
            None
        )

        if exact:

            resolved[requested] = exact
            continue

        # Otherwise allow broker suffixes.
        matches = [
            symbol
            for symbol in available
            if symbol.upper().startswith(requested)
        ]

        if matches:

            matches.sort(
                key=lambda symbol: (
                    len(symbol),
                    symbol
                )
            )

            resolved[requested] = matches[0]

        else:

            missing.append(requested)

    return resolved, missing


# ==========================================
# METAAPI CONNECTION
# ==========================================

async def connect_stream():

    logger.info("Connecting to MetaApi G2...")

    api = MetaApi(METAAPI_TOKEN)

    account = await (
        api.metatrader_account_api.get_account(
            ACCOUNT_ID
        )
    )

    if account.state != "DEPLOYED":

        logger.info("Deploying account...")

        await account.deploy()
        await account.wait_deployed()

    await account.wait_connected()

    connection = account.get_streaming_connection()

    await connection.connect()

    await connection.wait_synchronized()

    logger.info("Streaming connection synchronized")

    return connection


# ==========================================
# SUBSCRIPTIONS
# ==========================================

async def subscribe_symbols(connection, resolved):

    successful = {}
    failed = {}

    for requested, broker_symbol in resolved.items():

        try:

            logger.info(
                "Subscribing to %s",
                broker_symbol
            )

            await connection.subscribe_to_market_data(
                broker_symbol
            )

            successful[requested] = broker_symbol

        except Exception as error:

            failed[requested] = (
                f"{type(error).__name__}: {error}"
            )

            logger.warning(
                "Subscription failed for %s: %s",
                broker_symbol,
                error
            )

    return successful, failed


# ==========================================
# MARKET REPORT
# ==========================================

def build_market_report(connection, subscriptions):

    state = connection.terminal_state

    now = datetime.now(timezone.utc)

    lines = [
        "WIZZY LIVE MARKET SCANNER",
        "",
        f"UTC: {now:%Y-%m-%d %H:%M:%S}",
        ""
    ]

    fresh_count = 0
    stale_count = 0
    missing_count = 0

    for requested, broker_symbol in subscriptions.items():

        lines.append(
            f"{requested} ({broker_symbol})"
        )

        try:

            price = state.price(broker_symbol)

            if price is None:

                lines.append("Status: WAITING FOR QUOTES")
                lines.append("")

                missing_count += 1
                continue

            bid = get_value(price, "bid")
            ask = get_value(price, "ask")

            if bid is None or ask is None:

                lines.append("Status: INCOMPLETE QUOTE")
                lines.append("")

                missing_count += 1
                continue

            bid = float(bid)
            ask = float(ask)

            if bid <= 0 or ask < bid:

                lines.append("Status: INVALID QUOTE")
                lines.append("")

                missing_count += 1
                continue

            pip_size = pip_size_for(requested)
            digits = price_digits_for(requested)

            spread = (ask - bid) / pip_size

            quote_time = parse_time(
                get_value(price, "time")
            )

            if quote_time is None:

                status = "QUOTE TIME UNKNOWN"
                missing_count += 1

            else:

                age = max(
                    0,
                    (now - quote_time).total_seconds()
                )

                if age > MAX_QUOTE_AGE:

                    status = f"STALE ({int(age)}s old)"
                    stale_count += 1

                else:

                    status = "LIVE"
                    fresh_count += 1

            lines.extend([
                f"Bid: {bid:.{digits}f}",
                f"Ask: {ask:.{digits}f}",
                f"Spread: {spread:.2f} "
                + (
                    "price units of $0.01"
                    if requested == "XAUUSD"
                    else "pips"
                ),
                f"Status: {status}",
                ""
            ])

        except Exception as error:

            missing_count += 1

            lines.extend([
                f"Status: ERROR ({type(error).__name__})",
                ""
            ])

            logger.exception(
                "Failed to read %s",
                broker_symbol
            )

    lines.extend([
        "------------------------",
        f"Fresh: {fresh_count}",
        f"Stale: {stale_count}",
        f"Unavailable: {missing_count}",
        "",
        "Trading: DISABLED"
    ])

    return "\n".join(lines)


# ==========================================
# MONITOR
# ==========================================

async def monitor_market(connection, subscriptions):

    state = connection.terminal_state

    logger.info(
        "Waiting %s seconds for initial quotes...",
        STARTUP_QUOTE_WAIT
    )

    await asyncio.sleep(STARTUP_QUOTE_WAIT)

    while True:

        if not get_value(
            state,
            "connected_to_broker",
            False
        ):

            raise ConnectionError(
                "Broker streaming connection lost"
            )

        report = build_market_report(
            connection,
            subscriptions
        )

        await send_message(report)

        await asyncio.sleep(REPORT_INTERVAL)


# ==========================================
# MAIN
# ==========================================

async def main():

    if not all([
        METAAPI_TOKEN,
        ACCOUNT_ID,
        TELEGRAM_TOKEN,
        CHAT_ID
    ]):

        logger.error(
            "Missing required Railway variables"
        )

        return

    await send_message(
        "WIZZY BOT STAGE 2.3 STARTING\n\n"
        "Multi-symbol live market scanner.\n"
        "Instruments: 5\n"
        "Trading: DISABLED"
    )

    while True:

        connection = None

        try:

            connection = await connect_stream()

            # MetaApi streaming connection supports
            # reading available symbols.
            symbols = await connection.get_symbols()

            resolved, missing = resolve_symbols(
                symbols
            )

            if not resolved:

                raise RuntimeError(
                    "No requested broker symbols found"
                )

            subscriptions, failed = (
                await subscribe_symbols(
                    connection,
                    resolved
                )
            )

            if not subscriptions:

                raise RuntimeError(
                    "All market subscriptions failed"
                )

            startup_lines = [
                "WIZZY STAGE 2.3 CONNECTED",
                "",
                "Subscribed symbols:"
            ]

            for requested, broker_symbol in subscriptions.items():

                startup_lines.append(
                    f"{requested} -> {broker_symbol}"
                )

            if missing:

                startup_lines.extend([
                    "",
                    "Missing symbols:",
                    ", ".join(missing)
                ])

            if failed:

                startup_lines.extend([
                    "",
                    "Failed subscriptions:"
                ])

                for requested, error in failed.items():

                    startup_lines.append(
                        f"{requested}: {error[:100]}"
                    )

            startup_lines.extend([
                "",
                "Market reports: Every 5 minutes",
                "Trading: DISABLED"
            ])

            await send_message(
                "\n".join(startup_lines)
            )

            await monitor_market(
                connection,
                subscriptions
            )

        except Exception as error:

            logger.exception(
                "Stage 2.3 error"
            )

            await send_message(
                "WIZZY STAGE 2.3 ERROR\n\n"
                f"{type(error).__name__}\n"
                f"{str(error)[:350]}\n\n"
                "Retrying in 30 seconds.\n"
                "Trading: DISABLED"
            )

        finally:

            if connection is not None:

                try:
                    await connection.close()

                except Exception as error:

                    logger.warning(
                        "Connection cleanup error: %s",
                        error
                    )

        await asyncio.sleep(RECONNECT_DELAY)


# ==========================================
# ENTRY POINT
# ==========================================

if __name__ == "__main__":

    asyncio.run(main())
