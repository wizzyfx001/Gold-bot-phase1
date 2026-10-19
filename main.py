"""
Wizzy Gold Bot - VERBOSE MONITORING VERSION
Bal $306.05 | Target $30/day (60k Naira) | Prop Safe 1% Risk
Features: Live scan logs, Near Miss tracking, Buttons
"""
import os
import asyncio
import logging
from datetime import datetime
from typing import List, Dict
import pytz

from metaapi_cloud_sdk import MetaApi
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIG FROM RAILWAY VARIABLES ---
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID") or os.getenv("METAAPI_ACCOUNT_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Trading Config
BALANCE = 306.05
DAILY_TARGET = 30.0
RISK_PCT = 0.01  # 1% = $3.06 per trade - prop safe
PAIRS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD"]
SCAN_INTERVAL = 300  # 5 mins

# Storage
setups_found: List[Dict] = []
near_misses: List[Dict] = []
last_prices: Dict[str, float] = {}
scan_count = 0

logging.basicConfig(level=logging.INFO)

async def send_verbose(message: str, bot=None):
    """Send verbose log to Telegram"""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
    try:
        if bot:
            await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
        else:
            from telegram import Bot
            b = Bot(token=TELEGRAM_TOKEN)
            await b.send_message(chat_id=CHAT_ID, text=message, parse_mode='Markdown')
    except Exception as e:
        print(f"Telegram send error: {e}")

def get_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Equity & Balance", callback_data="equity"),
         InlineKeyboardButton("📈 MT5 Status", callback_data="mt5")],
        [InlineKeyboardButton("✅ Setups Found", callback_data="setups"),
         InlineKeyboardButton("⚠️ Near Misses", callback_data="near")],
        [InlineKeyboardButton("🔍 Force Scan Now", callback_data="scan"),
         InlineKeyboardButton("📝 Daily Report", callback_data="report")]
    ])

async def check_mt5_connection():
    try:
        api = MetaApi(METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        await account.wait_connected()
        connection = account.get_streaming_connection()
        await connection.connect()
        await connection.wait_synchronized()
        info = await connection.get_account_information()
        return True, info
    except Exception as e:
        return False, str(e)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🤖 *Wizzy Job Radar is online!*\n\nI'm ready to scout for new freelance jobs.\n\n"
        f"💰 *Gold Bot Started!* Bal ${BALANCE} Target ${DAILY_TARGET}\n"
        f"Risk: 1% per trade (${BALANCE*RISK_PCT:.2f})\nPairs: {', '.join(PAIRS)}\nScan: Every {SCAN_INTERVAL//60} mins",
        parse_mode='Markdown', reply_markup=get_keyboard()
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "equity":
        ok, info = await check_mt5_connection()
        if ok:
            bal = info.get('balance', BALANCE)
            eq = info.get('equity', bal)
            profit = eq - BALANCE
            await query.message.reply_text(f"📊 *Equity & Balance*\nBalance: ${bal:.2f}\nEquity: ${eq:.2f}\nToday PnL: ${profit:.2f} / ${DAILY_TARGET}\nProgress: {profit/DAILY_TARGET*100:.1f}%", parse_mode='Markdown', reply_markup=get_keyboard())
        else:
            await query.message.reply_text(f"❌ MT5 Error: {info}", reply_markup=get_keyboard())
    elif data == "mt5":
        ok, info = await check_mt5_connection()
        status = "🟢 Connected" if ok else f"🔴 {info}"
        await query.message.reply_text(f"📈 *MT5 Status: {status}*", parse_mode='Markdown', reply_markup=get_keyboard())
    elif data == "setups":
        txt = "✅ *Setups Found:*\n" + "\n".join([f"{s['time']} {s['pair']} {s['dir']} @ {s['price']}" for s in setups_found[-20:]]) if setups_found else "✅ No setups yet. Bot is scanning..."
        await query.message.reply_text(txt, parse_mode='Markdown', reply_markup=get_keyboard())
    elif data == "near":
        txt = "⚠️ *Near Misses:*\n" + "\n".join([f"{m['time']} {m['pair']} - Miss: {m['miss']} @ {m['price']}" for m in near_misses[-20:]]) if near_misses else "⚠️ No near misses yet."
        await query.message.reply_text(txt, parse_mode='Markdown', reply_markup=get_keyboard())
    elif data == "scan":
        await query.message.reply_text("🔍 *Force Scan triggered!*", parse_mode='Markdown')
        await run_scan_cycle(context.bot)
    elif data == "report":
        await query.message.reply_text(f"📝 *Daily Report*\nScans: {scan_count}\nSetups: {len(setups_found)}\nNear Misses: {len(near_misses)}", parse_mode='Markdown', reply_markup=get_keyboard())

async def scan_pair(pair: str, connection) -> Dict:
    try:
        price_data = await connection.get_symbol_price(pair)
        price = price_data.get('bid') or 0
        candles = await connection.get_historical_candles(pair, '1h', datetime.now().astimezone(pytz.utc).replace(hour=0), datetime.now().astimezone(pytz.utc))
        if not candles or len(candles) < 200:
            return {"pair": pair, "status": "no_data", "price": price, "reason": f"Not enough candles"}
        closes = [c['close'] for c in candles]
        ema50 = sum(closes[-50:]) / 50
        ema200 = sum(closes[-200:]) / 200
        gains = [max(0, closes[i]-closes[i-1]) for i in range(1, len(closes))]
        losses = [max(0, closes[i-1]-closes[i]) for i in range(1, len(closes))]
        avg_gain = sum(gains[-14:])/14 if gains else 0
        avg_loss = sum(losses[-14:])/14 if losses else 0.001
        rs = avg_gain/avg_loss
        rsi = 100 - (100/(1+rs))
        trend_up = ema50 > ema200
        trend_down = ema50 < ema200
        reasons = []
        if not trend_up and not trend_down:
            reasons.append("Trend FAIL (EMA50=EMA200)")
        if rsi > 70: reasons.append(f"RSI FAIL ({rsi:.0f} overbought)")
        if rsi < 30: reasons.append(f"RSI FAIL ({rsi:.0f} oversold)")
        if trend_up and 40 < rsi < 65:
            return {"pair": pair, "status": "setup", "price": price, "dir": "BUY", "rsi": rsi}
        if trend_down and 35 < rsi < 60:
            return {"pair": pair, "status": "setup", "price": price, "dir": "SELL", "rsi": rsi}
        if reasons:
            miss = ", ".join(reasons)
            if len(reasons) == 1:
                return {"pair": pair, "status": "near", "price": price, "miss": miss, "rsi": rsi}
            else:
                return {"pair": pair, "status": "no", "price": price, "reason": miss, "rsi": rsi}
        else:
            return {"pair": pair, "status": "no", "price": price, "reason": f"RSI {rsi:.0f} not in entry zone", "rsi": rsi}
    except Exception as e:
        return {"pair": pair, "status": "error", "price": 0, "reason": str(e)[:100]}

async def run_scan_cycle(bot):
    global scan_count
    scan_count += 1
    now = datetime.now().strftime("%H:%M:%S")
    await send_verbose(f"🔄 *Starting full market scan cycle* - {now} (Scan #{scan_count})", bot)
    try:
        api = MetaApi(METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        connection = account.get_streaming_connection()
        await connection.connect()
        await connection.wait_synchronized()
        found = 0; near = 0
        for pair in PAIRS:
            await send_verbose(f"👀 Scanning {pair}... Checking: Trend (EMA 50/200) | RSI | Structure", bot)
            result = await scan_pair(pair, connection)
            if result['status'] == 'setup':
                found += 1
                setups_found.append({"time": now, "pair": pair, "dir": result['dir'], "price": result['price'], "reason": f"RSI {result.get('rsi',0):.0f}"})
                await send_verbose(f"✅ *SETUP FOUND on {pair}!* Price: {result['price']:.2f} Dir: {result['dir']} RSI: {result.get('rsi',0):.0f}", bot)
            elif result['status'] == 'near':
                near += 1
                near_misses.append({"time": now, "pair": pair, "miss": result['miss'], "price": result['price']})
                await send_verbose(f"⚠️ *Near Miss on {pair}* - Price: {result['price']:.2f} - Missing: {result['miss']}", bot)
            elif result['status'] == 'no':
                await send_verbose(f"❌ No setup on {pair} - Price: {result['price']:.2f} - Reasons: {result['reason']}", bot)
            await asyncio.sleep(1)
        await send_verbose(f"🏁 *Scan cycle complete* - Checked {len(PAIRS)} pairs | Found: {found} | Near misses: {near} - Next in {SCAN_INTERVAL//60} mins...", bot)
        await connection.close()
    except Exception as e:
        await send_verbose(f"🔴 *Scan cycle failed:* {str(e)[:200]}", bot)

async def scan_loop(bot):
    while True:
        await run_scan_cycle(bot)
        await asyncio.sleep(SCAN_INTERVAL)

async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    try:
        from telegram import Bot
        b = Bot(token=TELEGRAM_TOKEN)
        await b.send_message(chat_id=CHAT_ID, text=f"🤖 Bot Started! Bal ${BALANCE} Target ${DAILY_TARGET}\n\n🔊 VERBOSE MODE ON - I'll talk every 5 mins!\nUse /start for buttons", reply_markup=get_keyboard())
    except: pass
    asyncio.create_task(scan_loop(app.bot))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
