"""
WIZZY AUTONOMOUS TRADING BOT
STAGE 2 - MULTI-SYMBOL SCANNER

Railway + MetaApi + MT5 + Telegram

No trade execution in this stage.
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

SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "XAUUSD"
]

SCAN_INTERVAL = 300

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("WIZZY")


# =====================================
# TELEGRAM
# =====================================

async def send_message(message):

    print(message, flush=True)

    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials missing")
        return

    try:
        async with Bot(token=TELEGRAM_TOKEN) as bot:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=message[:4000]
            )

    except Exception as error:
        logger.error("Telegram error: %s", error)


# =====================================
# METAAPI CONNECTION
# =====================================

async def connect_account():

    logger.info("Connecting to MetaApi...")

    api = MetaApi(METAAPI_TOKEN)

    account = await api.metatrader_account_api.get_account(
        ACCOUNT_ID
    )

    if account.state != "DEPLOYED":
        await account.deploy()
        await account.wait_deployed()

    await account.wait_connected()

    connection = account.get_rpc_connection()

    await connection.connect()
    await connection.wait_synchronized()

    logger.info("MetaApi connection successful")

    return account, connection


# =====================================
# CANDLE HELPERS
# =====================================

def value(obj, key):

    if isinstance(obj, dict):
        return obj.get(key)

    return getattr(obj, key, None)


def parse_time(raw):

    if isinstance(raw, datetime):
        dt = raw

    elif isinstance(raw, str):
        dt = datetime.fromisoformat(
            raw.replace("Z", "+00:00")
        )

    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc)


def clean_candles(raw):

    candles = []

    for c in raw:

        try:
            t = parse_time(value(c, "time"))

            if t is None:
                continue

            candles.append({
                "time": t,
                "open": float(value(c, "open")),
                "high": float(value(c, "high")),
                "low": float(value(c, "low")),
                "close": float(value(c, "close"))
            })

        except (TypeError, ValueError):
            continue

    candles.sort(key=lambda x: x["time"])

    return candles


# =====================================
# ATR
# =====================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    ranges = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        ranges.append(tr)

    return sum(ranges[-period:]) / period


# =====================================
# SYMBOL RESOLVER
# =====================================

async def resolve_symbols(connection):

    available = await connection.get_symbols()

    available = [str(s) for s in available]

    resolved = {}

    for wanted in SYMBOLS:

        exact = [
            s for s in available
            if s.upper() == wanted
        ]

        if exact:
            resolved[wanted] = exact[0]
            continue

        suffix_matches = [
            s for s in available
            if s.upper().startswith(wanted)
            and len(s) - len(wanted) <= 4
        ]

        if len(suffix_matches) == 1:
            resolved[wanted] = suffix_matches[0]

        elif len(suffix_matches) > 1:
            logger.warning(
                "%s has multiple matches: %s",
                wanted,
                suffix_matches
            )

        else:
            logger.warning(
                "Symbol unavailable: %s",
                wanted
            )

    return resolved


# =====================================
# MARKET ANALYSIS
# =====================================

def analyze_market(h1, m15):

    if len(h1) < 50 or len(m15) < 20:
        return None

    # Use completed candles only.

    now = datetime.now(timezone.utc)

    h1 = [
        c for c in h1
        if c["time"] + timedelta(hours=1) <= now
    ]

    m15 = [
        c for c in m15
        if c["time"] + timedelta(minutes=15) <= now
    ]

    if len(h1) < 50 or len(m15) < 20:
        return None

    closes = [c["close"] for c in h1]

    sma20 = sum(closes[-20:]) / 20
    sma50 = sum(closes[-50:]) / 50

    if sma20 > sma50:
        trend = "UP"

    elif sma20 < sma50:
        trend = "DOWN"

    else:
        trend = "RANGE"

    atr = calculate_atr(m15)

    return {
        "trend": trend,
        "atr": atr,
        "price": m15[-1]["close"],
        "h1_time": h1[-1]["time"],
        "m15_time": m15[-1]["time"],
        "h1_count": len(h1),
        "m15_count": len(m15)
    }


# =====================================
# MAIN SCANNER
# =====================================

async def scan_market(account, connection):

    logger.info("Starting market scan")

    resolved = await resolve_symbols(connection)

    report = [
        "WIZZY MARKET SCANNER",
        "",
        f"UTC: {datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
        f"Symbols found: {len(resolved)}/{len(SYMBOLS)}",
        ""
    ]

    for wanted, symbol in resolved.items():

        try:

            logger.info("Scanning %s", symbol)

            raw_h1 = await account.get_historical_candles(
                symbol,
                "1h",
                None,
                120
            )

            raw_m15 = await account.get_historical_candles(
                symbol,
                "15m",
                None,
                120
            )

            h1 = clean_candles(raw_h1)
            m15 = clean_candles(raw_m15)

            result = analyze_market(h1, m15)

            if result is None:

                report.append(
                    f"{wanted}: INSUFFICIENT CANDLES"
                )

                continue

            age = (
                datetime.now(timezone.utc)
                - result["m15_time"]
            ).total_seconds() / 60

            status = (
                "CURRENT"
                if age <= 45
                else "STALE"
            )

            report.append(
                f"{symbol}\n"
                f"Trend: {result['trend']}\n"
                f"Price: {result['price']:.5f}\n"
                f"ATR M15: {result['atr']:.5f}\n"
                f"Candles: H1={result['h1_count']} "
                f"M15={result['m15_count']}\n"
                f"Data: {status} ({age:.0f}m)\n"
            )

        except Exception as error:

            logger.exception(
                "Scanner error for %s",
                symbol
            )

            report.append(
                f"{symbol}: ERROR {str(error)[:100]}"
            )

    return "\n".join(report)


# =====================================
# MAIN LOOP
# =====================================

async def main():

    await send_message(
        "WIZZY BOT STAGE 2 STARTING\n"
        "Connecting to MetaApi..."
    )

    while True:

        connection = None

        try:

            account, connection = await connect_account()

            result = await scan_market(
                account,
                connection
            )

            await send_message(result)

        except Exception as error:

            logger.exception("Main loop error")

            await send_message(
                "WIZZY STAGE 2 ERROR\n"
                f"{type(error).__name__}: {str(error)[:300]}"
            )

        finally:

            if connection is not None:

                try:
                    await connection.close()

                except Exception:
                    logger.exception(
                        "Connection close error"
                    )

        await asyncio.sleep(SCAN_INTERVAL)


# =====================================
# START
# =====================================

if __name__ == "__main__":

    asyncio.run(main())
