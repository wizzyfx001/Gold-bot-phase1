"""
WIZZY AUTONOMOUS TRADING BOT
STAGE 1 - CONNECTION TEST

Railway + MetaApi + Telegram

Purpose:
1. Start on Railway
2. Connect to MetaApi
3. Retrieve MT5 account information
4. Send account status to Telegram
"""

import os
import asyncio
import logging

from metaapi_cloud_sdk import MetaApi
from telegram import Bot


# =====================================
# LOGGING
# =====================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("WIZZY")


# =====================================
# ENVIRONMENT VARIABLES
# =====================================

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


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
                text=message
            )

    except Exception as error:
        logger.error("Telegram error: %s", error)


# =====================================
# METAAPI ACCOUNT CONNECTION
# =====================================

async def check_account():

    if not METAAPI_TOKEN or not ACCOUNT_ID:
        return "ERROR: MetaApi credentials missing."

    try:

        logger.info("Connecting to MetaApi...")

        api = MetaApi(METAAPI_TOKEN)

        account = await api.metatrader_account_api.get_account(
            ACCOUNT_ID
        )

        logger.info("Account found")

        # Deploy only when necessary.

        if account.state != "DEPLOYED":

            logger.info("Deploying MetaApi account...")

            await account.deploy()

            await account.wait_deployed()

        logger.info("Waiting for broker connection...")

        await account.wait_connected()

        logger.info("MetaApi connected")

        # Use RPC connection for account information.

        connection = account.get_rpc_connection()

        try:

            await connection.connect()

            await connection.wait_synchronized()

            info = await connection.get_account_information()

            if isinstance(info, dict):

                balance = info.get("balance", "Unknown")
                equity = info.get("equity", "Unknown")
                currency = info.get("currency", "Unknown")

            else:

                balance = getattr(
                    info,
                    "balance",
                    "Unknown"
                )

                equity = getattr(
                    info,
                    "equity",
                    "Unknown"
                )

                currency = getattr(
                    info,
                    "currency",
                    "Unknown"
                )

            return (
                "WIZZY BOT CONNECTED\n\n"
                f"Balance: {balance}\n"
                f"Equity: {equity}\n"
                f"Currency: {currency}\n\n"
                "Stage 1 connection test successful."
            )

        finally:

            await connection.close()

    except Exception as error:

        logger.exception("MetaApi connection failed")

        return (
            "WIZZY CONNECTION ERROR\n\n"
            f"{type(error).__name__}: {str(error)[:500]}"
        )


# =====================================
# MAIN LOOP
# =====================================

async def main():

    logger.info("WIZZY BOT STARTING")

    await send_message(
        "WIZZY AUTONOMOUS BOT\n"
        "Stage 1 starting...\n"
        "Checking MetaApi connection."
    )

    while True:

        try:

            result = await check_account()

            logger.info(result)

            await send_message(result)

        except Exception:

            logger.exception("Unexpected main loop error")

        # Check account every 10 minutes.

        await asyncio.sleep(600)


# =====================================
# START
# =====================================

if __name__ == "__main__":

    asyncio.run(main())
