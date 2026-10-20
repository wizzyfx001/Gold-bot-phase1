"""
WIZZY AUTONOMOUS TRADING BOT

STAGE 2.2 - LIVE PRICE STREAMING

Platform:
GitHub + Railway + MetaApi G2 + MT5 + Telegram

Features:
- Persistent MetaApi streaming connection
- EURUSD live price monitoring
- Bid, ask and spread reporting
- Telegram notifications
- Automatic reconnection

TRADING IS DISABLED.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone

from metaapi_cloud_sdk import MetaApi
from telegram import Bot


# =====================================
# CONFIGURATION
# =====================================

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

SYMBOL = "EURUSD"

REPORT_INTERVAL = 60
RECONNECT_DELAY = 30


# =====================================
# LOGGING
# =====================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("WIZZY")

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
            "Telegram error: %s",
            error
        )


# =====================================
# DATA HELPER
# =====================================

def get_value(obj, key, default=None):

    if isinstance(obj, dict):
        return obj.get(key, default)

    return getattr(obj, key, default)


# =====================================
# METAAPI CONNECTION
# =====================================

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

    logger.info("Waiting for broker connection...")

    await account.wait_connected()

    connection = account.get_streaming_connection()

    await connection.connect()

    logger.info("Waiting for synchronization...")

    await connection.wait_synchronized()

    logger.info("Streaming connection synchronized")

    return connection


# =====================================
# LIVE PRICE MONITOR
# =====================================

async def monitor_prices(connection):

    state = connection.terminal_state

    logger.info("Subscribing to EURUSD quotes...")

    # MT5 G2 supports quote streaming.
    # Do not request extended candle subscriptions.

    await connection.subscribe_to_market_data(
        SYMBOL
    )

    await send_message(
        "WIZZY LIVE SCANNER\n\n"
        "Streaming connection established.\n"
        f"Symbol: {SYMBOL}\n"
        "Waiting for live quotes...\n\n"
        "Trading: DISABLED"
    )

    while True:

        try:

            connected = get_value(
                state,
                "connected_to_broker",
                False
            )

            if not connected:

                await send_message(
                    "WIZZY CONNECTION WARNING\n\n"
                    "Broker connection is unavailable.\n"
                    "Attempting reconnection."
                )

                return

            price = state.price(SYMBOL)

            if price is None:

                logger.warning(
                    "No price available for %s",
                    SYMBOL
                )

                await asyncio.sleep(REPORT_INTERVAL)
                continue

            bid = get_value(price, "bid")
            ask = get_value(price, "ask")

            if bid is None or ask is None:

                logger.warning("Incomplete price data")

                await asyncio.sleep(REPORT_INTERVAL)
                continue

            bid = float(bid)
            ask = float(ask)

            if bid <= 0 or ask <= bid:

                logger.warning("Invalid quote received")

                await asyncio.sleep(REPORT_INTERVAL)
                continue

            spread = ask - bid

            # EURUSD pip size.
            pip_size = 0.0001

            spread_pips = spread / pip_size

            now = datetime.now(timezone.utc)

            quote_time = get_value(
                price,
                "time",
                "Unavailable"
            )

            report = (
                "WIZZY LIVE MARKET SCANNER\n\n"
                f"Symbol: {SYMBOL}\n"
                f"Bid: {bid:.5f}\n"
                f"Ask: {ask:.5f}\n"
                f"Spread: {spread_pips:.2f} pips\n\n"
                f"Quote time: {quote_time}\n"
                f"Report UTC: {now:%Y-%m-%d %H:%M:%S}\n\n"
                "Connection: STREAMING\n"
                "Trading: DISABLED"
            )

            await send_message(report)

        except Exception as error:

            logger.exception("Price monitoring error")

            await send_message(
                "WIZZY PRICE ERROR\n\n"
                f"{type(error).__name__}: "
                f"{str(error)[:250]}"
            )

            return

        await asyncio.sleep(REPORT_INTERVAL)


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
            "Missing Railway environment variables"
        )

        return

    await send_message(
        "WIZZY BOT STAGE 2.2 STARTING\n\n"
        "Testing MetaApi G2 live price streaming.\n"
        "Symbol: EURUSD\n"
        "Trading: DISABLED"
    )

    while True:

        connection = None

        try:

            connection = await connect_stream()

            await monitor_prices(connection)

        except Exception as error:

            logger.exception(
                "Streaming connection error"
            )

            await send_message(
                "WIZZY STREAMING ERROR\n\n"
                f"{type(error).__name__}\n"
                f"{str(error)[:350]}\n\n"
                "Reconnection will be attempted."
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

        logger.info(
            "Reconnecting in %s seconds",
            RECONNECT_DELAY
        )

        await asyncio.sleep(RECONNECT_DELAY)


# =====================================
# START
# =====================================

if __name__ == "__main__":

    asyncio.run(main())
