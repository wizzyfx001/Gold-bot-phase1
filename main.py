"""
WIZZY AUTONOMOUS TRADING BOT
STAGE 2.1 - EURUSD MARKET DATA TEST

Platform:
GitHub + Railway + MetaApi + MT5 + Telegram

Purpose:
- Connect to MetaApi
- Verify MT5 account connection
- Find EURUSD broker symbol
- Retrieve H1 and M15 candles
- Calculate ATR and market trend
- Send Telegram reports

IMPORTANT:
No trade execution.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from metaapi_cloud_sdk import MetaApi
from telegram import Bot


# =====================================
# CONFIGURATION
# =====================================

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOLS = ["EURUSD"]

SCAN_INTERVAL = 300
CANDLE_LIMIT = 60


# =====================================
# LOGGING
# =====================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("WIZZY")

# Reduce noisy MetaApi websocket logs.
logging.getLogger("socketio").setLevel(logging.WARNING)
logging.getLogger("engineio").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


# =====================================
# TELEGRAM
# =====================================

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
            "Telegram notification failed: %s",
            error
        )


# =====================================
# METAAPI CONNECTION
# =====================================

async def connect_account():

    logger.info("Connecting to MetaApi...")

    api = MetaApi(METAAPI_TOKEN)

    account = await (
        api.metatrader_account_api.get_account(
            ACCOUNT_ID
        )
    )

    if account.state != "DEPLOYED":

        logger.info("Deploying MetaApi account...")

        await account.deploy()
        await account.wait_deployed()

    await account.wait_connected()

    connection = account.get_rpc_connection()

    await connection.connect()
    await connection.wait_synchronized()

    logger.info("MetaApi connection established")

    return account, connection


# =====================================
# DATA HELPERS
# =====================================

def get_value(obj, key):

    if isinstance(obj, dict):
        return obj.get(key)

    return getattr(obj, key, None)


def parse_time(raw):

    if isinstance(raw, datetime):
        result = raw

    elif isinstance(raw, str):

        try:
            result = datetime.fromisoformat(
                raw.replace("Z", "+00:00")
            )

        except ValueError:
            return None

    else:
        return None

    if result.tzinfo is None:
        result = result.replace(
            tzinfo=timezone.utc
        )

    return result.astimezone(timezone.utc)


def clean_candles(raw, timeframe_minutes):

    now = datetime.now(timezone.utc)

    candles = []

    for candle in raw:

        try:

            timestamp = parse_time(
                get_value(candle, "time")
            )

            if timestamp is None:
                continue

            o = float(get_value(candle, "open"))
            h = float(get_value(candle, "high"))
            l = float(get_value(candle, "low"))
            c = float(get_value(candle, "close"))

            if not (
                l <= min(o, c)
                and max(o, c) <= h
            ):
                continue

            # Ignore unfinished candles.
            close_time = timestamp + timedelta(
                minutes=timeframe_minutes
            )

            if close_time > now:
                continue

            candles.append({
                "time": timestamp,
                "open": o,
                "high": h,
                "low": l,
                "close": c
            })

        except (TypeError, ValueError):
            continue

    candles.sort(
        key=lambda item: item["time"]
    )

    return candles


# =====================================
# ATR CALCULATION
# =====================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    ranges = []

    for index in range(1, len(candles)):

        current = candles[index]
        previous = candles[index - 1]

        true_range = max(
            current["high"] - current["low"],
            abs(
                current["high"] -
                previous["close"]
            ),
            abs(
                current["low"] -
                previous["close"]
            )
        )

        ranges.append(true_range)

    return sum(
        ranges[-period:]
    ) / period


# =====================================
# SYMBOL RESOLVER
# =====================================

async def resolve_eurusd(connection):

    available = await connection.get_symbols()

    names = [
        str(symbol)
        for symbol in available
    ]

    exact = [
        symbol
        for symbol in names
        if symbol.upper() == "EURUSD"
    ]

    if exact:
        return exact[0]

    matches = [
        symbol
        for symbol in names
        if (
            symbol.upper().startswith("EURUSD")
            and len(symbol) <= 10
        )
    ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        raise RuntimeError(
            "Multiple EURUSD symbol matches: "
            + ", ".join(matches)
        )

    raise RuntimeError(
        "EURUSD symbol not found on broker"
    )


# =====================================
# MARKET ANALYSIS
# =====================================

def analyze_market(h1, m15):

    if len(h1) < 50:
        return None, "Insufficient H1 candles"

    if len(m15) < 20:
        return None, "Insufficient M15 candles"

    closes = [
        candle["close"]
        for candle in h1
    ]

    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50

    if sma20 > sma50:
        trend = "UP"

    elif sma20 < sma50:
        trend = "DOWN"

    else:
        trend = "RANGE"

    atr = calculate_atr(m15)

    if atr is None:
        return None, "ATR unavailable"

    return {
        "trend": trend,
        "atr": atr,
        "price": m15[-1]["close"],
        "h1_time": h1[-1]["time"],
        "m15_time": m15[-1]["time"],
        "h1_count": len(h1),
        "m15_count": len(m15)
    }, None


# =====================================
# EURUSD MARKET SCANNER
# =====================================

async def scan_market(account, connection):

    logger.info("Starting EURUSD scan")

    symbol = await resolve_eurusd(connection)

    logger.info("Resolved symbol: %s", symbol)

    # Request only 60 candles per timeframe.
    # This is a diagnostic test, not a guarantee
    # that MetaApi historical requests will work.

    logger.info("Requesting H1 candles...")

    raw_h1 = await asyncio.wait_for(
        account.get_historical_candles(
            symbol,
            "1h",
            None,
            CANDLE_LIMIT
        ),
        timeout=90
    )

    logger.info("Requesting M15 candles...")

    raw_m15 = await asyncio.wait_for(
        account.get_historical_candles(
            symbol,
            "15m",
            None,
            CANDLE_LIMIT
        ),
        timeout=90
    )

    h1 = clean_candles(raw_h1, 60)
    m15 = clean_candles(raw_m15, 15)

    result, error = analyze_market(h1, m15)

    if error:

        return (
            "WIZZY EURUSD SCANNER\n\n"
            f"Symbol: {symbol}\n"
            f"Status: {error}\n"
            f"H1 candles: {len(h1)}\n"
            f"M15 candles: {len(m15)}"
        )

    now = datetime.now(timezone.utc)

    # Age since the latest candle CLOSED.
    latest_close = (
        result["m15_time"] +
        timedelta(minutes=15)
    )

    age_minutes = (
        now - latest_close
    ).total_seconds() / 60

    if age_minutes <= 30:
        data_status = "CURRENT"
    else:
        data_status = "STALE"

    return (
        "WIZZY EURUSD SCANNER\n\n"
        f"UTC: {now:%Y-%m-%d %H:%M}\n"
        f"Symbol: {symbol}\n\n"
        f"Trend H1: {result['trend']}\n"
        f"Price M15: {result['price']:.5f}\n"
        f"ATR M15: {result['atr']:.5f}\n\n"
        f"H1 candles: {result['h1_count']}\n"
        f"M15 candles: {result['m15_count']}\n"
        f"Data: {data_status}\n"
        f"Last closed candle age: "
        f"{age_minutes:.0f} minutes\n\n"
        "Trading: DISABLED"
    )


# =====================================
# MAIN LOOP
# =====================================

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
        "WIZZY BOT STAGE 2.1 STARTING\n\n"
        "Testing EURUSD historical market data.\n"
        "Automatic trading is disabled."
    )

    while True:

        connection = None

        try:

            account, connection = await connect_account()

            # Confirm the account remains accessible.
            info = await connection.get_account_information()

            balance = get_value(info, "balance")

            logger.info(
                "Account balance: %s",
                balance
            )

            report = await scan_market(
                account,
                connection
            )

            await send_message(report)

        except asyncio.TimeoutError:

            logger.exception(
                "Market data request timed out"
            )

            await send_message(
                "WIZZY STAGE 2.1\n\n"
                "ERROR: Historical market data timed out.\n"
                "MetaApi did not return candles within "
                "the configured timeout.\n\n"
                "Trading remains disabled."
            )

        except Exception as error:

            logger.exception(
                "Stage 2.1 scan failed"
            )

            await send_message(
                "WIZZY STAGE 2.1 ERROR\n\n"
                f"{type(error).__name__}\n"
                f"{str(error)[:350]}\n\n"
                "Trading remains disabled."
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

        await asyncio.sleep(SCAN_INTERVAL)


# =====================================
# START
# =====================================

if __name__ == "__main__":
    asyncio.run(main())
