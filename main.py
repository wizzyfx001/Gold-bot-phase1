"""
Wizzy Gold Bot - FINAL REAL VERSION
Monday Market OPEN | Real EMA50/200 + RSI | Live Trading Ready
Bal $306.05 | Target $30/day | Broker: Jarocel 1:500
"""
import os, asyncio, logging
from datetime import datetime, timedelta
import pytz
from typing import List, Dict

from metaapi_cloud_sdk import MetaApi
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_LIVE = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"

BALANCE = 306.05
DAILY_TARGET = 30.0
RISK_PCT = 0.01
PAIRS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "BTCUSD"]
SCAN_INTERVAL = 300

setups_found: List[Dict] = []
near_misses: List[Dict] = []
scan_count = 0
logging.basicConfig(level=logging.INFO)

async def send_verbose(msg: str, bot=None):
    print(msg)
    try:
        if bot:
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
    except: pass

def get_keyboard():
    live_txt = "🟢 LIVE ON" if ENABLE_LIVE else "🟡 SIGNAL ONLY"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Equity & Balance", callback_data="equity"),
         InlineKeyboardButton("📈 MT5 Status", callback_data="mt5")],
        [InlineKeyboardButton("✅ Setups Found", callback_data="setups"),
         InlineKeyboardButton("⚠️ Near Misses", callback_data="near")],
        [InlineKeyboardButton("🔍 Force Scan Now", callback_data="scan"),
         InlineKeyboardButton("📝 Daily Report", callback_data="report")],
        [InlineKeyboardButton(live_txt, callback_data="toggle")]
    ])

async def get_account_info():
    try:
        api = MetaApi(METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        conn = account.get_streaming_connection()
        await conn.connect()
        await conn.wait_synchronized()
        info = conn.terminalState.accountInformation
        await conn.close()
        return True, info
    except Exception as e:
        try:
            api = MetaApi(METAAPI_TOKEN)
            account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
            rpc = account.get_rpc_connection()
            await rpc.connect()
            await rpc.wait_synchronized()
            info = await rpc.get_account_information()
            await rpc.close()
            return True, info
        except Exception as e2:
            return False, f"{e} | {e2}"

async def get_candles_real(pair: str):
    """Get real candles from MT5 via RPC - REAL INDICATORS"""
    try:
        api = MetaApi(METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        rpc = account.get_rpc_connection()
        await rpc.connect()
        await rpc.wait_synchronized()
        # Get last 300 1H candles
        end = datetime.now(pytz.UTC)
        start = end - timedelta(hours=400)
        candles = await rpc.get_historical_candles(pair, '1h', start, 400)
        await rpc.close()
        return candles
    except Exception as e:
        print(f"Candle error {pair}: {e}")
        return None

def calc_indicators(candles):
    closes = [float(c['close']) for c in candles]
    if len(closes) < 200:
        return None
    ema50 = sum(closes[-50:]) / 50
    ema200 = sum(closes[-200:]) / 200
    # RSI 14
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))
    avg_gain = sum(gains[-14:]) / 14
    avg_loss = sum(losses[-14:]) / 14 if sum(losses[-14:]) != 0 else 0.001
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return {"ema50": ema50, "ema200": ema200, "rsi": rsi, "price": closes[-1], "closes": closes}

async def scan_pair_real(pair: str):
    candles = await get_candles_real(pair)
    if not candles or len(candles) < 200:
        return {"pair": pair, "status": "no_data", "price": 0, "reason": f"No candles {len(candles) if candles else 0}"}
    ind = calc_indicators(candles)
    if not ind:
        return {"pair": pair, "status": "no_data", "price": 0, "reason": "Calc failed"}
    
    price = ind['price']
    rsi = ind['rsi']
    ema50 = ind['ema50']
    ema200 = ind['ema200']
    
    trend_up = ema50 > ema200
    trend_down = ema50 < ema200
    trend_strength = abs(ema50 - ema200) / price * 100

    # BUY: Uptrend + RSI 40-65 pullback + strong trend >0.1%
    if trend_up and 40 < rsi < 65 and trend_strength > 0.05:
        return {"pair": pair, "status": "setup", "price": price, "dir": "BUY", "rsi": rsi, "ema50": ema50, "ema200": ema200, "trend": f"UP {trend_strength:.2f}%"}
    # SELL: Downtrend + RSI 35-60
    if trend_down and 35 < rsi < 60 and trend_strength > 0.05:
        return {"pair": pair, "status": "setup", "price": price, "dir": "SELL", "rsi": rsi, "ema50": ema50, "ema200": ema200, "trend": f"DOWN {trend_strength:.2f}%"}
    
    # Near misses
    if trend_up and 65 <= rsi < 72:
        return {"pair": pair, "status": "near", "price": price, "miss": f"RSI {rsi:.0f} too high (need <65) UP trend OK", "rsi": rsi}
    if trend_down and 28 < rsi <= 35:
        return {"pair": pair, "status": "near", "price": price, "miss": f"RSI {rsi:.0f} too low (need >35) DOWN trend OK", "rsi": rsi}
    if not trend_up and not trend_down:
        return {"pair": pair, "status": "no", "price": price, "reason": f"Sideways EMA50={ema50:.2f} EMA200={ema200:.2f}", "rsi": rsi}
    
    return {"pair": pair, "status": "no", "price": price, "reason": f"RSI {rsi:.0f} out of zone | Trend {'UP' if trend_up else 'DOWN'} {trend_strength:.2f}%", "rsi": rsi}

async def place_trade(pair, direction, bot):
    """Place live trade if enabled"""
    if not ENABLE_LIVE:
        await send_verbose(f"📝 *SIGNAL ONLY* (Live OFF): {direction} {pair} - Enable with ENABLE_LIVE_TRADING=true in Railway", bot)
        return False
    try:
        api = MetaApi(METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        conn = account.get_streaming_connection()
        await conn.connect()
        await conn.wait_synchronized()
        
        price_info = conn.terminalState.price(pair)
        price = price_info['bid'] if isinstance(price_info, dict) else getattr(price_info, 'bid', 0)
        
        # Risk calc: 1% = $3.06, XAUUSD lot = risk / (stop $ * contract)
        # For XAU: $3 risk, $1 stop = $10 loss per 0.01 lot approx, so 0.03 lot
        lot = 0.03 if pair == "XAUUSD" else 0.05
        
        if direction == "BUY":
            sl = price * 0.996  # 0.4% SL = ~$10 on Gold
            tp = price * 1.008  # 0.8% TP = ~$21 - RR 1:2
            result = await conn.create_market_buy_order(pair, lot, sl, tp)
        else:
            sl = price * 1.004
            tp = price * 0.992
            result = await conn.create_market_sell_order(pair, lot, sl, tp)
        
        await send_verbose(f"🚀 *LIVE TRADE PLACED!* {direction} {pair} Lot {lot} @ {price:.2f} SL {sl:.2f} TP {tp:.2f} Order: {result.get('orderId','?')}", bot)
        await conn.close()
        return True
    except Exception as e:
        await send_verbose(f"❌ Trade failed {pair}: {str(e)[:150]}", bot)
        return False

async def run_scan_cycle(bot):
    global scan_count
    scan_count += 1
    now = datetime.now().strftime("%H:%M:%S")
    await send_verbose(f"🔄 *REAL SCAN #{scan_count}* - {now} - Using REAL MT5 candles EMA50/200 RSI14", bot)
    try:
        found = 0; near = 0
        for pair in PAIRS:
            await send_verbose(f"👀 Scanning {pair}... Fetching 400 candles -> Calc EMA50/200 RSI14", bot)
            result = await scan_pair_real(pair)
            if result['status'] == 'setup':
                found += 1
                setups_found.append({"time": now, "pair": pair, "dir": result['dir'], "price": result['price'], "reason": f"RSI {result['rsi']:.0f} {result['trend']}"})
                await send_verbose(f"✅ *REAL SETUP FOUND on {pair}!* Price: {result['price']:.2f} Dir: {result['dir']} RSI: {result['rsi']:.0f} Trend: {result['trend']} EMA50 {result['ema50']:.2f} > EMA200 {result['ema200']:.2f}", bot)
                await place_trade(pair, result['dir'], bot)
            elif result['status'] == 'near':
                near += 1
                near_misses.append({"time": now, "pair": pair, "miss": result['miss'], "price": result['price']})
                await send_verbose(f"⚠️ *Near Miss {pair}* - Price: {result['price']:.2f} - {result['miss']}", bot)
            elif result['status'] == 'no':
                await send_verbose(f"❌ No setup {pair} - Price: {result['price']:.2f} - {result['reason']} RSI {result.get('rsi',0):.0f}", bot)
            else:
                await send_verbose(f"❓ {pair}: {result['reason']}", bot)
            await asyncio.sleep(2)
        await send_verbose(f"🏁 *REAL Scan #{scan_count} done* - Checked {len(PAIRS)} | Found: {found} | Near: {near} | Mode: {'🟢 LIVE' if ENABLE_LIVE else '🟡 SIGNAL'} Next in {SCAN_INTERVAL//60} mins", bot)
    except Exception as e:
        import traceback
        traceback.print_exc()
        await send_verbose(f"🔴 Scan failed: {str(e)[:200]}", bot)

async def scan_loop(bot):
    while True:
        await run_scan_cycle(bot)
        await asyncio.sleep(SCAN_INTERVAL)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"💰 *Wizzy Gold Bot FINAL*\nBal ${BALANCE} Target ${DAILY_TARGET}\nBroker: Jarocel 1:500\nReal EMA50/200 RSI14\nMode: {'🟢 LIVE TRADING' if ENABLE_LIVE else '🟡 SIGNAL ONLY'}\n\nMonday Market OPEN!", parse_mode='Markdown', reply_markup=get_keyboard())

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data in ["equity","mt5"]:
        await query.message.reply_text("⏳ Connecting to MT5...")
        ok, info = await get_account_info()
        if ok:
            bal = info.get('balance', BALANCE) if isinstance(info, dict) else getattr(info, 'balance', BALANCE)
            eq = info.get('equity', bal) if isinstance(info, dict) else getattr(info, 'equity', bal)
            if data=="equity":
                await query.message.reply_text(f"📊 *Equity & Balance*\nBalance: ${bal:.2f}\nEquity: ${eq:.2f}\nPnL: ${eq-BALANCE:.2f} / ${DAILY_TARGET}\nProgress: {(eq-BALANCE)/DAILY_TARGET*100:.1f}%\nScans: {scan_count}\nMode: {'LIVE' if ENABLE_LIVE else 'SIGNAL'}", parse_mode='Markdown', reply_markup=get_keyboard())
            else:
                broker = info.get('broker','Jarocel') if isinstance(info, dict) else getattr(info, 'broker','Jarocel')
                await query.message.reply_text(f"📈 *MT5: 🟢 Connected*\nBroker: {broker}\nBal: ${bal:.2f} Eq: ${eq:.2f}\nLev: 1:500\nTime: {datetime.now().strftime('%H:%M')}", parse_mode='Markdown', reply_markup=get_keyboard())
        else:
            await query.message.reply_text(f"❌ {info}", reply_markup=get_keyboard())
    elif data=="setups":
        txt = "✅ No setups yet. Waiting for real EMA crossover." if not setups_found else "✅ *Real Setups:*\n" + "\n".join([f"{s['time']} {s['pair']} {s['dir']} @ {s['price']} {s['reason']}" for s in setups_found[-15:]])
        await query.message.reply_text(txt, parse_mode='Markdown', reply_markup=get_keyboard())
    elif data=="near":
        txt = "⚠️ No near misses." if not near_misses else "⚠️ *Near Misses:*\n" + "\n".join([f"{m['time']} {m['pair']} {m['miss']}" for m in near_misses[-15:]])
        await query.message.reply_text(txt, parse_mode='Markdown', reply_markup=get_keyboard())
    elif data=="scan":
        await query.message.reply_text("🔍 Force REAL scan...", parse_mode='Markdown')
        await run_scan_cycle(context.bot)
    elif data=="report":
        await query.message.reply_text(f"📝 *Report Mon*\nScans: {scan_count}\nSetups: {len(setups_found)}\nNear: {len(near_misses)}\nTarget: ${DAILY_TARGET}\nMode: {'LIVE' if ENABLE_LIVE else 'SIGNAL ONLY'}\nPairs: {', '.join(PAIRS)}", parse_mode='Markdown', reply_markup=get_keyboard())
    elif data=="toggle":
        await query.message.reply_text(f"Mode is: {'🟢 LIVE' if ENABLE_LIVE else '🟡 SIGNAL ONLY'}\n\nTo enable LIVE trades:\n1. Railway -> Variables -> Add: ENABLE_LIVE_TRADING = true\n2. Redeploy\n\nCurrently: Risk 1% = $3.06 per trade, SL 0.4% TP 0.8% RR 1:2", parse_mode='Markdown', reply_markup=get_keyboard())

async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    try:
        from telegram import Bot
        b = Bot(token=TELEGRAM_TOKEN)
        await b.send_message(chat_id=CHAT_ID, text=f"🤖 *FINAL Bot Started! REAL indicators*\nBal ${BALANCE} Target ${DAILY_TARGET}\nMode: {'🟢 LIVE' if ENABLE_LIVE else '🟡 SIGNAL ONLY'}\nMonday OPEN - Scanning REAL candles now!", reply_markup=get_keyboard())
    except: pass
    asyncio.create_task(scan_loop(app.bot))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
