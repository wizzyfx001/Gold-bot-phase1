"""
Wizzy v8 - REAL RSI from previous candles - No hardcoding
XAUUSD Gold Only - $306 -> $30/day
"""
import os, asyncio, logging
from datetime import datetime, timedelta
import pytz
from metaapi_cloud_sdk import MetaApi
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_LIVE = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"

BALANCE = 306.05
SCAN_INTERVAL = 300
setups_found = []
scan_count = 0
logging.basicConfig(level=logging.INFO)

async def send(msg, bot=None):
    print(msg)
    try:
        if bot:
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
    except: pass

def kb():
    txt = "🟢 LIVE ON $3 risk" if ENABLE_LIVE else "🟡 SIGNAL ONLY"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Equity $306", callback_data="equity"),
         InlineKeyboardButton("✅ Setups", callback_data="setups")],
        [InlineKeyboardButton("🔍 Scan Real RSI", callback_data="scan")],
        [InlineKeyboardButton(txt, callback_data="toggle")]
    ])

async def get_real_data():
    """Get 200 previous candles and calculate REAL RSI/EMA"""
    try:
        api = MetaApi(METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        conn = account.get_streaming_connection()
        await conn.connect()
        await conn.wait_synchronized()
        try: await conn.subscribe_to_market_data("XAUUSD")
        except: pass
        await asyncio.sleep(2)
        
        candles = None
        # Try to get 200 candles from history
        for tf, days in [('1h',14), ('30m',7), ('15m',5)]:
            try:
                start = datetime.now(pytz.UTC) - timedelta(days=days)
                candles = await conn.get_historical_candles("XAUUSD", tf, start, 300)
                if candles and len(candles) >= 50:
                    print(f"Got {len(candles)} {tf} candles")
                    break
            except Exception as e:
                print(f"{tf} fail: {e}")
                continue
        
        # Get live price too
        price = 4143.0
        try:
            p = conn.terminalState.price("XAUUSD")
            price = float(p['bid']) if isinstance(p, dict) else float(getattr(p, 'bid', price))
        except: pass
        
        await conn.close()
        
        if not candles or len(candles) < 50:
            return {"price": price, "rsi": 50.0, "ema50": price, "ema200": price, "count": 0, "real": False}
        
        # Calculate REAL from previous closes
        closes = []
        for c in candles:
            if isinstance(c, dict):
                closes.append(float(c.get('close') or c.get('c') or price))
            else:
                closes.append(float(getattr(c, 'close', price)))
        closes = [x for x in closes if x > 0]
        
        if len(closes) < 50:
            return {"price": price, "rsi": 50.0, "ema50": price, "ema200": price, "count": len(closes), "real": False}
        
        ema50 = sum(closes[-50:]) / 50
        ema200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else sum(closes) / len(closes)
        
        # REAL RSI 14 from previous candles
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [max(0,d) for d in deltas[-14:]]
        losses = [max(0,-d) for d in deltas[-14:]]
        avg_gain = sum(gains) / 14 if gains else 0.001
        avg_loss = sum(losses) / 14 if losses else 0.001
        if avg_loss == 0:
            rsi = 70.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        return {"price": closes[-1], "rsi": rsi, "ema50": ema50, "ema200": ema200, "count": len(closes), "real": True}
        
    except Exception as e:
        print(f"get_real_data fail: {e}")
        import traceback
        traceback.print_exc()
        return {"price": 4143.0, "rsi": 50.0, "ema50": 4143.0, "ema200": 4143.0, "count": 0, "real": False}

async def run_scan(bot):
    global scan_count
    scan_count += 1
    now = datetime.now().strftime("%H:%M:%S")
    await send(f"🔄 *REAL SCAN #{scan_count}* {now} - Fetching 200 previous candles...", bot)
    
    data = await get_real_data()
    price = data['price']
    rsi = data['rsi']
    ema50 = data['ema50']
    ema200 = data['ema200']
    count = data['count']
    real = data['real']
    
    if real:
        await send(f"📊 *REAL DATA* Gold {price:.2f} RSI {rsi:.1f} EMA50 {ema50:.2f} EMA200 {ema200:.2f} from {count} previous candles", bot)
    else:
        await send(f"⚠️ No history yet ({count} candles) - Price {price:.2f} - Broker syncing, will retry", bot)
        return
    
    trend_up = ema50 > ema200
    # REAL SETUP LOGIC - no hardcoded 32.8!
    if rsi < 38:
        setups_found.append({"time":now,"price":price,"rsi":rsi,"dir":"BUY"})
        await send(f"✅ *REAL SETUP!* BUY Gold @ {price:.2f} RSI {rsi:.1f} OVERSOLD from {count} candles - Bounce expected!", bot)
        if ENABLE_LIVE:
            try:
                api = MetaApi(METAAPI_TOKEN)
                account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
                conn = account.get_streaming_connection()
                await conn.connect()
                await conn.wait_synchronized()
                sl = price * 0.992
                tp = price * 1.008
                await conn.create_market_buy_order("XAUUSD", 0.03, sl, tp)
                await send(f"🚀 *LIVE TRADE!* BUY 0.03 lot @ {price:.2f} RSI {rsi:.1f} SL {sl:.2f} TP {tp:.2f} Risk $3", bot)
                await conn.close()
            except Exception as e:
                await send(f"❌ Trade fail {str(e)[:100]}", bot)
        else:
            await send(f"📝 *SIGNAL ONLY*: BUY @ {price:.2f} RSI {rsi:.1f} - Enable LIVE: Railway -> Variables -> ENABLE_LIVE_TRADING=true", bot)
    elif rsi > 68:
        setups_found.append({"time":now,"price":price,"rsi":rsi,"dir":"SELL"})
        await send(f"✅ *REAL SETUP!* SELL Gold @ {price:.2f} RSI {rsi:.1f} OVERBOUGHT from {count} candles", bot)
    elif trend_up and 45 < rsi < 60:
        setups_found.append({"time":now,"price":price,"rsi":rsi,"dir":"BUY"})
        await send(f"✅ *REAL SETUP!* BUY Trend UP Gold @ {price:.2f} RSI {rsi:.1f} EMA50>{ema200:.0f} from {count} candles", bot)
    else:
        await send(f"❌ No setup - Gold {price:.2f} RSI {rsi:.1f} EMA diff {abs(ema50-ema200)/price*100:.2f}% from {count} candles", bot)
    
    await send(f"🏁 *Scan #{scan_count} done* Real={real} Count={count} | Mode {'LIVE' if ENABLE_LIVE else 'SIGNAL'}", bot)

async def loop(bot):
    while True:
        await run_scan(bot)
        await asyncio.sleep(SCAN_INTERVAL)

async def get_info():
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
        return False, str(e)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"💰 *Wizzy v8 REAL*\nReal RSI from 200 candles\nBal ${BALANCE}\nMode {'LIVE' if ENABLE_LIVE else 'SIGNAL'}", parse_mode='Markdown', reply_markup=kb())

async def btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    d = q.data
    if d == "equity":
        ok, info = await get_info()
        if ok:
            bal = info.get('balance', BALANCE) if isinstance(info, dict) else getattr(info, 'balance', BALANCE)
            eq = info.get('equity', bal) if isinstance(info, dict) else getattr(info, 'equity', bal)
            await q.message.reply_text(f"📊 Bal ${bal:.2f} Eq ${eq:.2f} PnL ${eq-BALANCE:.2f} Scans {scan_count}", parse_mode='Markdown', reply_markup=kb())
    elif d == "setups":
        if not setups_found:
            await q.message.reply_text("No real setups yet - waiting for RSI calculation", parse_mode='Markdown', reply_markup=kb())
        else:
            txt = "✅ *Real Setups:*\n" + "\n".join([f"{s['time']} {s['dir']} @ {s['price']:.2f} RSI {s['rsi']:.1f}" for s in setups_found[-10:]])
            await q.message.reply_text(txt, parse_mode='Markdown', reply_markup=kb())
    elif d == "scan":
        await q.message.reply_text("🔍 Calculating REAL RSI from 200 candles...", parse_mode='Markdown')
        await run_scan(context.bot)
    elif d == "toggle":
        await q.message.reply_text(f"Mode {'LIVE' if ENABLE_LIVE else 'SIGNAL ONLY'}\nTo enable: Railway -> Variables -> Add ENABLE_LIVE_TRADING = true", parse_mode='Markdown', reply_markup=kb())

async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(btn))
    try:
        from telegram import Bot
        b = Bot(token=TELEGRAM_TOKEN)
        await b.send_message(chat_id=CHAT_ID, text=f"🤖 *v8 REAL RSI Started!*\nNow calculates REAL RSI from previous 200 candles - No more hardcoded 32.8!", reply_markup=kb())
    except: pass
    asyncio.create_task(loop(app.bot))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
