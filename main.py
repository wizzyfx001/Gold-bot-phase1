"""
WIZZY AUTONOMOUS TRADING BOT
VERSION: 1.0 DEMO PILOT

PLATFORM:
GitHub + Railway + MetaApi + MT5 + Telegram

FEATURES:
- Multi-symbol scanning
- H1 trend analysis
- M15 breakout strategy
- ATR stop loss and take profit
- Automatic demo order execution
- Broker specification checks
- Risk-based position sizing
- Open position monitoring
- Telegram notifications
- Pause and resume controls

IMPORTANT:
This version is for demo execution testing.
Profitability has not been established.
"""

import os
import asyncio
import logging
import math

from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN

from metaapi_cloud_sdk import MetaApi

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler
)


# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

log = logging.getLogger("WIZZY")


# ==========================================
# ENVIRONMENT VARIABLES
# ==========================================

METAAPI_TOKEN = os.environ["METAAPI_TOKEN"]

ACCOUNT_ID = os.environ["METAAPI_ACCOUNT_ID"]

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])


# ==========================================
# DEMO TRADING CONFIGURATION
# ==========================================

# Demo order submission is enabled by default.
# Verify the connected account is DEMO before deployment.

ENABLE_DEMO_TRADING = True

# Separate confirmation required from Railway.
DEMO_CONFIRMED = (
    os.getenv(
        "DEMO_ACCOUNT_CONFIRMED",
        "false"
    ).lower() == "true"
)


# ==========================================
# TRADING CONFIGURATION
# ==========================================

SYMBOLS = [
    s.strip().upper()
    for s in os.getenv(
        "SYMBOLS",
        "EURUSD,GBPUSD,AUDUSD,XAUUSD"
    ).split(",")
    if s.strip()
]

RISK_USD = float(
    os.getenv("RISK_USD", "3")
)

MAX_DAILY_LOSS = float(
    os.getenv("MAX_DAILY_LOSS", "9")
)

MAX_OPEN_POSITIONS = int(
    os.getenv("MAX_OPEN_POSITIONS", "1")
)

SCAN_SECONDS = max(
    60,
    int(os.getenv("SCAN_SECONDS", "300"))
)

MAX_SPREAD_ATR = float(
    os.getenv("MAX_SPREAD_ATR", "0.12")
)

MAGIC_TAG = "WIZZYDEMO"


# ==========================================
# GLOBAL STATE
# ==========================================

api = None
account = None
conn = None

scan_lock = asyncio.Lock()

scan_number = 0

last_report = "Bot starting..."

recent_signals = []

paused = False

uncertain = False

day_key = None
day_start_equity = None

seen_bars = {}

last_positions = {}

unresolved_orders = []


# ==========================================
# HELPER FUNCTIONS
# ==========================================

def field(obj, *names, default=None):

    for name in names:

        if isinstance(obj, dict):
            value = obj.get(name)

        else:
            value = getattr(obj, name, None)

        if value is not None:
            return value

    return default


def number(obj, *names, default=None):

    value = field(
        obj,
        *names,
        default=default
    )

    try:
        return float(value)

    except (TypeError, ValueError):
        return default


def utc_now():

    return datetime.now(timezone.utc)


# ==========================================
# CANDLE PROCESSING
# ==========================================

def candle_time(candle):

    value = field(
        candle,
        "time",
        "brokerTime"
    )

    if isinstance(value, datetime):

        if value.tzinfo is None:
            return value.replace(
                tzinfo=timezone.utc
            )

        return value.astimezone(timezone.utc)

    if isinstance(value, str):

        try:

            return datetime.fromisoformat(
                value.replace("Z", "+00:00")
            ).astimezone(timezone.utc)

        except ValueError:
            return None

    return None


def clean_candles(raw, minutes):

    candles = []

    for candle in raw:

        timestamp = candle_time(candle)

        o = number(candle, "open")
        h = number(candle, "high")
        l = number(candle, "low")
        c = number(candle, "close")

        values = (o, h, l, c)

        if timestamp is None:
            continue

        if any(
            value is None or not math.isfinite(value)
            for value in values
        ):
            continue

        if not (
            l <= min(o, c)
            <= max(o, c)
            <= h
        ):
            continue

        candles.append({

            "time": timestamp,
            "open": o,
            "high": h,
            "low": l,
            "close": c

        })

    candles = sorted(

        {
            candle["time"]: candle
            for candle in candles
        }.values(),

        key=lambda x: x["time"]

    )

    # Exclude candles that have not closed.

    cutoff = utc_now() - timedelta(seconds=10)

    return [

        candle
        for candle in candles

        if candle["time"] + timedelta(
            minutes=minutes
        ) <= cutoff

    ]


# ==========================================
# ATR CALCULATION
# ==========================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    true_ranges = []

    for i in range(1, len(candles)):

        current = candles[i]

        previous = candles[i - 1]

        tr = max(

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

        true_ranges.append(tr)

    return sum(
        true_ranges[-period:]
    ) / period


# ==========================================
# STRATEGY ENGINE
# ==========================================

def detect_signal(h1, m15):

    """
    Initial test strategy:

    H1 trend filter:
    SMA20 versus SMA50.

    M15 entry:
    Breakout of previous 20 candles.

    This is a testable strategy hypothesis,
    not a proven profitable system.
    """

    if len(h1) < 55:
        return None

    if len(m15) < 35:
        return None

    atr = calculate_atr(m15)

    if atr is None or atr <= 0:
        return None

    closes = [
        candle["close"]
        for candle in h1
    ]

    sma20 = sum(
        closes[-20:]
    ) / 20

    sma50 = sum(
        closes[-50:]
    ) / 50

    current = m15[-1]

    previous = m15[-21:-1]

    highest = max(
        candle["high"]
        for candle in previous
    )

    lowest = min(
        candle["low"]
        for candle in previous
    )

    # BUY CONDITIONS

    if (

        sma20 > sma50

        and current["close"] > highest

        and current["close"] > current["open"]

        and current["close"] - highest < 0.65 * atr

    ):

        return "BUY", atr

    # SELL CONDITIONS

    if (

        sma20 < sma50

        and current["close"] < lowest

        and current["close"] < current["open"]

        and lowest - current["close"] < 0.65 * atr

    ):

        return "SELL", atr

    return None


# ==========================================
# POSITION SIZING
# ==========================================

def calculate_lot(spec, entry, sl, symbol):

    """
    Initial implementation for standard
    USD-denominated, linearly priced symbols.

    Reject unsupported instruments.

    Never force minimum lot when it exceeds
    the configured risk budget.
    """

    if not (

        symbol.endswith("USD")

        or symbol.startswith("XAUUSD")

    ):

        return None, "Unsupported quote currency"

    contract = number(
        spec,
        "contractSize",
        "contract_size"
    )

    minimum = number(
        spec,
        "minVolume",
        "min_volume"
    )

    maximum = number(
        spec,
        "maxVolume",
        "max_volume"
    )

    step = number(
        spec,
        "volumeStep",
        "volume_step"
    )

    specifications = (
        contract,
        minimum,
        maximum,
        step
    )

    if not all(

        value is not None and value > 0

        for value in specifications

    ):

        return None, "Missing broker specifications"

    stop_distance = abs(entry - sl)

    loss_per_lot = stop_distance * contract

    if loss_per_lot <= 0:

        return None, "Invalid stop distance"

    # Reserve 15% of risk budget for costs.

    allowed_risk = RISK_USD * 0.85

    raw_lot = allowed_risk / loss_per_lot

    steps = math.floor(

        (raw_lot - minimum) / step + 1e-9

    )

    if steps < 0:

        return None, "Minimum lot exceeds risk"

    lot = min(

        maximum,

        minimum + steps * step

    )

    lot = float(

        Decimal(str(lot)).quantize(

            Decimal("0.000001"),

            rounding=ROUND_DOWN

        )

    )

    if lot * loss_per_lot > allowed_risk:

        return None, "Risk limit exceeded"

    return lot, None


# ==========================================
# TELEGRAM NOTIFICATIONS
# ==========================================

async def notify(bot, message):

    log.info(message)

    try:

        await bot.send_message(

            chat_id=CHAT_ID,

            text=message[:3900]

        )

    except Exception as error:

        log.warning(
            "Telegram error: %s",
            error
        )


# ==========================================
# METAAPI CONNECTION
# ==========================================

async def connect_metaapi():

    global api, account, conn

    api = MetaApi(METAAPI_TOKEN)

    account = await (
        api.metatrader_account_api.get_account(
            ACCOUNT_ID
        )
    )

    await account.deploy()

    await account.wait_connected()

    conn = account.get_rpc_connection()

    await conn.connect()

    await conn.wait_synchronized()

    log.info("MetaApi connected successfully")


# ==========================================
# ACCOUNT INFORMATION
# ==========================================

async def account_snapshot():

    info = await conn.get_account_information()

    equity = number(info, "equity")

    balance = number(info, "balance")

    currency = str(

        field(
            info,
            "currency",
            default=""
        )

    ).upper()

    if equity is None or balance is None:

        raise RuntimeError(
            "Account balance/equity unavailable"
        )

    return info, equity, balance, currency


# ==========================================
# OPEN POSITIONS
# ==========================================

async def get_positions():

    return await conn.get_positions()


# ==========================================
# SYMBOL RESOLVER
# ==========================================

async def resolve_symbols():

    available = await conn.get_symbols()

    names = [
        str(symbol)
        for symbol in available
    ]

    resolved = []

    for wanted in SYMBOLS:

        matches = [

            name
            for name in names

            if name.upper() == wanted

        ]

        if not matches:

            matches = [

                name
                for name in names

                if (

                    name.upper().startswith(wanted)

                    and len(name) - len(wanted) <= 3

                )

            ]

        if len(matches) == 1:

            resolved.append(matches[0])

        else:

            log.warning(

                "Skipping %s: %d symbol matches",

                wanted,

                len(matches)

            )

    return list(dict.fromkeys(resolved))


# ==========================================
# MAIN MARKET SCANNER
# ==========================================

async def run_scan(bot):

    global scan_number
    global last_report
    global day_key
    global day_start_equity
    global uncertain
    global last_positions

    async with scan_lock:

        scan_number += 1

        try:

            info, equity, balance, currency = (
                await account_snapshot()
            )

            positions = await get_positions()

            position_ids = {

                str(field(p, "id", default=""))

                for p in positions

            }

            # Detect positions no longer open.

            for pid in list(last_positions):

                if pid not in position_ids:

                    await notify(

                        bot,

                        f"Position {pid} is no longer open.\n"
                        "Check MT5 history for TP, SL, "
                        "or manual closure."

                    )

            last_positions = {

                str(field(p, "id", default="")): p

                for p in positions

            }

            today = utc_now().date().isoformat()

            if day_key != today:

                day_key = today

                day_start_equity = equity

            drawdown = day_start_equity - equity

            # Demo trading authorization.

            trading_enabled = (

                ENABLE_DEMO_TRADING

                and DEMO_CONFIRMED

                and not paused

                and not uncertain

            )

            if currency != "USD":

                trading_enabled = False

            if drawdown >= MAX_DAILY_LOSS:

                trading_enabled = False

            if len(positions) >= MAX_OPEN_POSITIONS:

                trading_enabled = False

            resolved = await resolve_symbols()

            lines = [

                f"SCAN #{scan_number}",

                f"Balance: ${balance:.2f}",

                f"Equity: ${equity:.2f}",

                f"Open positions: {len(positions)}",

                f"Daily baseline drawdown: ${drawdown:.2f}",

                (
                    "Mode: DEMO AUTO"
                    if trading_enabled
                    else "Mode: OBSERVE / LOCKED"
                )

            ]

            if unresolved_orders:

                lines.append(
                    "Unresolved order: manual check required"
                )

            # Scan available instruments.

            for symbol in resolved:

                try:

                    raw_h1 = await (
                        account.get_historical_candles(

                            symbol,
                            "1h",
                            None,
                            120

                        )
                    )

                    raw_m15 = await (
                        account.get_historical_candles(

                            symbol,
                            "15m",
                            None,
                            120

                        )
                    )

                    h1 = clean_candles(raw_h1, 60)

                    m15 = clean_candles(raw_m15, 15)

                    if not h1 or not m15:

                        lines.append(
                            f"{symbol}: No valid candles"
                        )

                        continue

                    # Reject stale market data.

                    if (

                        utc_now() - h1[-1]["time"]
                        > timedelta(hours=3)

                        or

                        utc_now() - m15[-1]["time"]
                        > timedelta(minutes=45)

                    ):

                        lines.append(
                            f"{symbol}: Stale candles"
                        )

                        continue

                    result = detect_signal(h1, m15)

                    if result is None:

                        continue

                    direction, atr = result

                    bar_key = m15[-1]["time"].isoformat()

                    if seen_bars.get(symbol) == bar_key:

                        continue

                    seen_bars[symbol] = bar_key

                    quote = await conn.get_symbol_price(
                        symbol
                    )

                    bid = number(quote, "bid")

                    ask = number(quote, "ask")

                    if (

                        not bid

                        or not ask

                        or ask <= bid

                    ):

                        lines.append(
                            f"{symbol}: Invalid quote"
                        )

                        continue

                    spread = ask - bid

                    if spread > MAX_SPREAD_ATR * atr:

                        lines.append(
                            f"{symbol}: Spread too wide"
                        )

                        continue

                    entry = (

                        ask
                        if direction == "BUY"
                        else bid

                    )

                    stop_distance = max(

                        1.8 * atr,

                        spread * 4

                    )

                    if direction == "BUY":

                        sl = entry - stop_distance

                        tp = entry + stop_distance * 2

                    else:

                        sl = entry + stop_distance

                        tp = entry - stop_distance * 2

                    spec = await (
                        conn.get_symbol_specification(
                            symbol
                        )
                    )

                    digits = int(

                        number(
                            spec,
                            "digits",
                            default=5
                        )

                    )

                    sl = round(sl, digits)

                    tp = round(tp, digits)

                    lot, error = calculate_lot(

                        spec,

                        entry,

                        sl,

                        symbol

                    )

                    if error:

                        lines.append(

                            f"{symbol} {direction} "
                            f"rejected: {error}"

                        )

                        continue

                    setup_message = (

                        f"{symbol} {direction}\n"
                        f"Entry approximately: {entry:.5f}\n"
                        f"Lot: {lot}\n"
                        f"SL: {sl}\n"
                        f"TP: {tp}"

                    )

                    lines.append(setup_message)

                    recent_signals.append(
                        setup_message
                    )

                    del recent_signals[:-10]

                    if not trading_enabled:

                        continue

                    # Recheck broker positions before entry.

                    current_positions = (
                        await get_positions()
                    )

                    if current_positions:

                        lines.append(
                            "Skipped: existing position"
                        )

                        continue

                    if unresolved_orders:

                        lines.append(
                            "Skipped: unresolved order"
                        )

                        continue

                    pending_orders = (
                        await conn.get_orders()
                    )

                    if pending_orders:

                        lines.append(
                            "Skipped: pending orders exist"
                        )

                        continue

                                        # ==================================
                    # AUTOMATIC DEMO ORDER EXECUTION
                    # ==================================

                    try:

                        options = {
                            "comment": MAGIC_TAG,
                            "clientId": (
                                "WZ_"
                                + symbol
                                + "_"
                                + m15[-1]["time"].strftime("%m%d%H%M")
                            )[:32]
                        }

                        if direction == "BUY":

                            order_result = await conn.create_market_buy_order(
                                symbol,
                                lot,
                                sl,
                                tp,
                                options
                            )

                        else:

                            order_result = await conn.create_market_sell_order(
                                symbol,
                                lot,
                                sl,
                                tp,
                                options
                            )

                        lines.append(
                            f"DEMO ORDER RESPONSE: {str(order_result)[:160]}"
                        )

                        await notify(
                            bot,
                            f"DEMO ORDER RESPONSE\n"
                            f"{setup_message}\n\n"
                            f"{str(order_result)[:300]}"
                        )

                        trading_enabled = False

                    except Exception as error:

                        uncertain = True

                        unresolved_orders.append(
                            f"{symbol}: {error}"
                        )

                        lines.append(
                            f"ORDER STATUS UNCERTAIN: {str(error)[:90]}"
                        )

                        await notify(
                            bot,
                            "ORDER STATUS UNCERTAIN.\n"
                            "Trading locked.\n"
                            "Inspect MT5 positions and history."
                        )

                except Exception as error:

                    log.exception(
                        "Symbol error: %s",
                        symbol
                    )

                    lines.append(
                        f"{symbol}: {str(error)[:90]}"
                    )

            last_report = "\n".join(lines)

            log.info(last_report)

        except Exception as error:

            last_report = f"SCAN FAILED: {error}"

            log.exception("Main scanner error")

            await notify(
                bot,
                last_report
            )


# ==========================================
# AUTOMATIC SCANNING LOOP
# ==========================================

async def scanning_loop(bot):

    while True:

        await run_scan(bot)

        await asyncio.sleep(SCAN_SECONDS)


# ==========================================
# TELEGRAM CONTROL PANEL
# ==========================================

def keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Status",
                callback_data="status"
            ),
            InlineKeyboardButton(
                "Scan Now",
                callback_data="scan"
            )
        ],
        [
            InlineKeyboardButton(
                "Pause Entries",
                callback_data="pause"
            ),
            InlineKeyboardButton(
                "Resume Entries",
                callback_data="resume"
            )
        ]
    ])


# ==========================================
# TELEGRAM AUTHORIZATION
# ==========================================

async def authorized(update):

    chat = (
        str(update.effective_chat.id)
        if update.effective_chat
        else ""
    )

    if chat != CHAT_ID:

        if update.callback_query:

            await update.callback_query.answer(
                "Unauthorized",
                show_alert=True
            )

        return False

    return True


# ==========================================
# TELEGRAM START COMMAND
# ==========================================

async def start(update, context):

    if not await authorized(update):
        return

    await update.message.reply_text(
        "WIZZY AUTONOMOUS DEMO BOT\n\n"
        + last_report,
        reply_markup=keyboard()
    )


# ==========================================
# TELEGRAM BUTTON HANDLER
# ==========================================

async def button(update, context):

    global paused

    if not await authorized(update):
        return

    query = update.callback_query

    await query.answer()

    if query.data == "pause":

        paused = True

    elif query.data == "resume":

        paused = False

    elif query.data == "scan":

        await run_scan(context.bot)

    await query.message.reply_text(
        f"Paused: {paused}\n"
        f"Demo authorized: "
        f"{ENABLE_DEMO_TRADING and DEMO_CONFIRMED}\n\n"
        + last_report,
        reply_markup=keyboard()
    )


# ==========================================
# MAIN APPLICATION
# ==========================================

async def main():

    await connect_metaapi()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            button
        )
    )

    await app.initialize()

    await app.start()

    await app.updater.start_polling()

    await notify(
        app.bot,
        "WIZZY AUTONOMOUS DEMO BOT STARTED\n\n"
        f"Automatic demo execution: "
        f"{ENABLE_DEMO_TRADING and DEMO_CONFIRMED}\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"Risk budget: ${RISK_USD}\n"
        f"Maximum open positions: {MAX_OPEN_POSITIONS}\n"
        f"Scan interval: {SCAN_SECONDS}s\n\n"
        "Waiting for valid trading opportunities."
    )

    task = asyncio.create_task(
        scanning_loop(app.bot)
    )

    try:

        await asyncio.Event().wait()

    finally:

        task.cancel()

        await app.updater.stop()

        await app.stop()

        await app.shutdown()

        if conn:

            await conn.close()


if __name__ == "__main__":

    asyncio.run(main())
                 
