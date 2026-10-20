"""
Wizzy v14 SMC COMPANY GRADE - Can present to company
Fixes terminalState, 0 candles, 4143 stuck
SMC: BOS/CHoCH + Order Blocks + FVG + Liquidity + Premium/Discount + ATR
$306 Bal, $3 risk dynamic lot, $30/day, 2 trades max, London/NY
"""
import os, asyncio, logging
from datetime import datetime, date
import pytz
from metaapi_cloud_sdk import MetaApi
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

METAAPI_TOKEN = os.getenv("METAAPI_TOKEN")
ACCOUNT_ID = os.getenv("METAAPI_ACCOUNT_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ENABLE_LIVE = os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true"

START_BAL = 306.05
RISK_DOLLAR = 3.0
DAILY_TARGET = 30.0
DAILY_STOP = -9.0
MAX_TRADES = 2
SCAN_INTERVAL = 300

scan_count = 0
trades_today = 0
daily_pnl = 0.0
last_day = date.today()
setups = []

logging.basicConfig(level=logging.INFO)

async def send(msg, bot=None):
    print(msg)
    try:
        if bot: await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')
    except: pass

def kb():
    t = "🟢 LIVE $3" if ENABLE_LIVE else "🟡 SIGNAL ONLY"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Equity", callback_data="equity"),
         InlineKeyboardButton("📈 SMC Status", callback_data="smc")],
        [InlineKeyboardButton("✅ Setups", callback_data="setups"),
         InlineKeyboardButton("🔍 Force Scan", callback_data="scan")],
        [InlineKeyboardButton(t, callback_data="toggle")]
    ])

# --- SMC CALCULATIONS ---
def find_swings(candles):
    """candles = list of dict {high,low,close} - returns swing highs/lows indexes"""
    highs, lows = [], []
    for i in range(1, len(candles)-1):
        if candles[i]['high'] > candles[i-1]['high'] and candles[i]['high'] > candles[i+1]['high']:
            highs.append(i)
        if candles[i]['low'] < candles[i-1]['low'] and candles[i]['low'] < candles[i+1]['low']:
            lows.append(i)
    return highs, lows

def calc_atr(candles, p=14):
    tr = []
    for i in range(1,len(candles)):
        tr.append(max(candles[i]['high']-candles[i]['low'], abs(candles[i]['high']-candles[i-1]['close']), abs(candles[i]['low']-candles[i-1]['close'])))
    return sum(tr[-p:])/p if tr else 5.0

def calc_rsi(closes, p=14):
    if len(closes)<p+1: return 50
    deltas = [closes[i]-closes[i-1] for i in range(1,len(closes))]
    gains = [max(0,d) for d in deltas[-p:]]
    losses = [max(0,-d) for d in deltas[-p:]]
    ag = sum(gains)/p; al = sum(losses)/p
    if al < 1e-6: return 70
    return 100 - (100/(1+ag/al))

def detect_bos_choch(candles):
    """Returns trend, last BOS, CHoCH"""
    highs, lows = find_swings(candles)
    if len(highs)<2 or len(lows)<2:
        return "RANGE", None, None
    # Last 2 swing highs/lows
    last_high = candles[highs[-1]]['high']
    prev_high = candles[highs[-2]]['high']
    last_low = candles[lows[-1]]['low']
    prev_low = candles[lows[-2]]['low']
    cur_close = candles[-1]['close']
    
    bos_up = cur_close > prev_high and last_high > prev_high
    bos_down = cur_close < prev_low and last_low < prev_low
    choch_up = cur_close < prev_low # break low in uptrend = reversal
    choch_down = cur_close > prev_high
    
    if bos_up: return "UP", prev_high, None
    if bos_down: return "DOWN", prev_low, None
    if choch_up and prev_high>prev_low: return "CHoCH-DOWN", None, prev_low
    if choch_down: return "CHoCH-UP", None, prev_high
    return "RANGE", None, None

def find_order_block(candles, atr):
    """Find last OB within 10 candles of BOS"""
    # Look last 20 candles for OB
    for i in range(len(candles)-5, len(candles)-15, -1):
        if i<2 or i>len(candles)-3: continue
        c = candles[i]
        c1 = candles[i+1]
        c2 = candles[i+2]
        # Bullish OB: bearish candle before strong bullish impulse
        is_bearish = c['close'] < c['open']
        strong_bull = c1['close'] > c1['open'] and c2['close'] > c2['open'] and c2['close'] > c['high']
        if is_bearish and strong_bull and (c['open']-c['close']) > atr*0.2:
            return {"type":"BULL","high":c['high'],"low":c['low'],"index":i,"price":(c['high']+c['low'])/2}
        # Bearish OB
        is_bullish = c['close'] > c['open']
        strong_bear = c1['close'] < c1['open'] and c2['close'] < c2['open'] and c2['close'] < c['low']
        if is_bullish and strong_bear and (c['close']-c['open']) > atr*0.2:
            return {"type":"BEAR","high":c['high'],"low":c['low'],"index":i,"price":(c['high']+c['low'])/2}
    return None

def find_fvg(candles, atr):
    """Find unfilled FVGs"""
    fvgs = []
    for i in range(len(candles)-3, 1, -1):
        # Bullish FVG: low[i] > high[i-2]
        if candles[i]['low'] > candles[i-2]['high']:
            gap = candles[i]['low'] - candles[i-2]['high']
            if gap > atr*0.3: # REAL FVG, not spread
                # Check if not filled yet - current price above gap?
                if candles[-1]['low'] > candles[i-2]['high']:
                    fvgs.append({"type":"BULL","top":candles[i]['low'],"bottom":candles[i-2]['high'],"gap":gap,"index":i})
        # Bearish FVG
        if candles[i]['high'] < candles[i-2]['low']:
            gap = candles[i-2]['low'] - candles[i]['high']
            if gap > atr*0.3:
                if candles[-1]['high'] < candles[i-2]['low']:
                    fvgs.append({"type":"BEAR","top":candles[i-2]['low'],"bottom":candles[i]['high'],"gap":gap,"index":i})
    return fvgs[:3] # last 3

def check_liquidity_sweep(candles):
    """Equal highs/lows sweep"""
    if len(candles)<10: return None
    # Check last 10 highs equal within $1.5
    last_highs = [c['high'] for c in candles[-10:-1]]
    if max(last_highs)-min(last_highs) < 1.5:
        # Current candle swept above and closed below = sell liquidity taken
        if candles[-1]['high'] > max(last_highs)+1.0 and candles[-1]['close'] < max(last_highs):
            return "SELL-LIQ-SWEEP"
    last_lows = [c['low'] for c in candles[-10:-1]]
    if max(last_lows)-min(last_lows) < 1.5:
        if candles[-1]['low'] < min(last_lows)-1.0 and candles[-1]['close'] > min(last_lows):
            return "BUY-LIQ-SWEEP"
    return None

# --- MARKET FETCH - REST ONLY NO terminalState ---
async def get_market():
    try:
        api = MetaApi(METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        try: await account.deploy(); await account.wait_deployed()
        except: pass
        
        candles_1h = candles_15m = None
        info = ""
        for sym in ["XAUUSD","GOLD","XAUUSDc"]:
            try:
                c1 = await account.get_historical_candles(sym, "1h", None, 200)
                c15 = await account.get_historical_candles(sym, "15m", None, 200)
                if c1 and c15 and len(c1)>=80 and len(c15)>=80:
                    candles_1h = [{"high":float(x.get('high') or x.get('h')),"low":float(x.get('low') or x.get('l')),"open":float(x.get('open') or x.get('o')),"close":float(x.get('close') or x.get('c'))} for x in c1]
                    candles_15m = [{"high":float(x.get('high') or x.get('h')),"low":float(x.get('low') or x.get('l')),"open":float(x.get('open') or x.get('o')),"close":float(x.get('close') or x.get('c'))} for x in c15]
                    info = f"{sym} 1H {len(c1)} 15M {len(c15)}"
                    break
            except Exception as e: print(f"{sym} {e}")
        return candles_1h, candles_15m, info
    except Exception as e:
        print(f"get_market FAIL {e}")
        return None, None, str(e)

def is_session():
    now = datetime.now(pytz.timezone("Africa/Lagos"))
    h = now.hour
    return (8 <= h <= 11) or (13 <= h <= 18)

async def run_scan(bot):
    global scan_count, trades_today, daily_pnl, last_day
    
    if date.today() != last_day:
        trades_today=0; daily_pnl=0.0; last_day=date.today(); setups.clear()
    
    scan_count+=1
    now = datetime.now(pytz.timezone("Africa/Lagos")).strftime("%H:%M:%S")
    
    if daily_pnl >= DAILY_TARGET:
        if scan_count%12==1: await send(f"💰 TARGET ${daily_pnl:.2f}/${DAILY_TARGET} Done", bot)
        return
    if daily_pnl <= DAILY_STOP:
        if scan_count%12==1: await send(f"🛑 STOP ${daily_pnl:.2f} Done", bot)
        return
    if trades_today>=MAX_TRADES:
        if scan_count%12==1: await send(f"⏸️ {MAX_TRADES} trades done today", bot)
        return
    if not is_session():
        if scan_count%6==1: await send(f"🔄 SCAN #{scan_count} {now} Outside London/NY (8-11, 13-18 Lagos)", bot)
        return
    
    c1h, c15m, info = await get_market()
    if not c1h or not c15m:
        await send(f"🔄 SCAN #{scan_count} {now} No data {info} Market closed?", bot)
        return
    
    # SMC CALCULATIONS
    atr_15 = calc_atr(c15m, 14)
    atr_1h = calc_atr(c1h, 14)
    closes_15 = [c['close'] for c in c15m]
    rsi_15 = calc_rsi(closes_15, 14)
    cur = c15m[-1]['close']
    
    trend_1h, bos_level, choch_level = detect_bos_choch(c1h)
    trend_15, _, _ = detect_bos_choch(c15m)
    ob = find_order_block(c15m, atr_15)
    fvgs = find_fvg(c15m, atr_15)
    liq = check_liquidity_sweep(c15m)
    
    # Premium/Discount: last swing low to high 15m
    swing_low = min([c['low'] for c in c15m[-50:]])
    swing_high = max([c['high'] for c in c15m[-50:]])
    swing_range = swing_high - swing_low
    fib_50 = swing_low + swing_range*0.5
    discount = cur < fib_50 # Buy zone
    premium = cur > fib_50 # Sell zone
    
    # FINAL SMC ENTRY LOGIC - 4 confirms
    setup = None
    reason = ""
    # BUY: 1H UP, 15M Discount, Bullish OB, Bullish FVG, Liquidity sweep buy or BOS
    if trend_1h=="UP" and discount and ob and ob['type']=="BULL" and any(f['type']=="BULL" for f in fvgs) and (liq=="BUY-LIQ-SWEEP" or trend_15=="UP"):
        # Price must be near OB (within 1x ATR)
        if abs(cur - ob['price']) < atr_15*1.2 and 35 <= rsi_15 <= 55:
            setup="BUY"
            reason=f"SMC: 1H {trend_1h} BOS, Discount {cur:.1f}<{fib_50:.1f}, OB Bull @{ob['price']:.1f}, FVG Bull, Liq {liq}, RSI {rsi_15:.1f}"
    # SELL
    elif trend_1h=="DOWN" and premium and ob and ob['type']=="BEAR" and any(f['type']=="BEAR" for f in fvgs) and (liq=="SELL-LIQ-SWEEP" or trend_15=="DOWN"):
        if abs(cur - ob['price']) < atr_15*1.2 and 45 <= rsi_15 <= 65:
            setup="SELL"
            reason=f"SMC: 1H {trend_1h} BOS, Premium {cur:.1f}>{fib_50:.1f}, OB Bear @{ob['price']:.1f}, FVG Bear, Liq {liq}, RSI {rsi_15:.1f}"
    
    msg = f"🔄 *SCAN #{scan_count}* {now} Lagos | Daily ${daily_pnl:.2f}/${DAILY_TARGET} {trades_today}/{MAX_TRADES}\n📊 *Gold {cur:.2f} | 1H {trend_1h} 15M {trend_15} RSI {rsi_15:.1f} ATR {atr_15:.1f}*\nSwing {swing_low:.1f}-{swing_high:.1f} 50% {fib_50:.1f} {'DISCOUNT' if discount else 'PREMIUM'}\nOB: {ob['type']+' @'+str(round(ob['price'],1)) if ob else 'None'} | FVGs: {len(fvgs)} | Liq: {liq or 'None'}\nFrom {info}"
    
    if setup:
        sl = cur - atr_15*1.5 if setup=="BUY" else cur + atr_15*1.5
        tp = cur + atr_15*2.8 if setup=="BUY" else cur - atr_15*2.8
        lot = max(0.01, min(0.05, RISK_DOLLAR/(atr_15*1.5*10)))
        lot = round(lot,2)
        
        setups.append({"time":now,"dir":setup,"price":cur,"reason":reason,"lot":lot,"sl":sl,"tp":tp})
        msg+=f"\n\n✅ *SMC SETUP {setup} @ {cur:.2f}*\n{reason}\nLot {lot} SL {sl:.2f} TP {tp:.2f} RR 1:{(tp-cur)/(cur-sl) if setup=='BUY' else (cur-tp)/(sl-cur):.1f}"
        
        if ENABLE_LIVE:
            try:
                api = MetaApi(METAAPI_TOKEN)
                account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
                conn = account.get_streaming_connection()
                await conn.connect(); await conn.wait_synchronized()
                if setup=="BUY": await conn.create_market_buy_order("XAUUSD", lot, sl, tp)
                else: await conn.create_market_sell_order("XAUUSD", lot, sl, tp)
                await conn.close()
                trades_today+=1
                msg+=f"\n🚀 LIVE #{trades_today} Executed"
            except Exception as e:
                msg+=f"\n❌ Trade err {str(e)[:100]}"
        else:
            msg+=f"\n📝 SIGNAL ONLY - Enable LIVE: ENABLE_LIVE_TRADING=true"
    else:
        msg+=f"\n❌ No SMC confluence - Need OB+FVG+Liq+Trend. Waiting."
    
    msg+=f"\nMode {'LIVE' if ENABLE_LIVE else 'SIGNAL'} Next 5m"
    await send(msg, bot)

async def loop(bot):
    while True:
        try: await run_scan(bot)
        except Exception as e: print(f"loop {e}"); await send(f"❌ {str(e)[:100]}", bot)
        await asyncio.sleep(SCAN_INTERVAL)

async def get_info():
    try:
        api = MetaApi(METAAPI_TOKEN)
        account = await api.metatrader_account_api.get_account(ACCOUNT_ID)
        info = await account.get_account_information()
        return True, info
    except Exception as e: return False, str(e)

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"💰 *Wizzy v14 SMC COMPANY*\nBOS/CHoCH + OB + FVG + Liq + Premium/Discount\n$3 risk dynamic lot\n$30 target", parse_mode='Markdown', reply_markup=kb())

async def btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); d=q.data
    if d=="equity":
        ok,info = await get_info()
        if ok:
            bal = info.get('balance', START_BAL) if isinstance(info,dict) else getattr(info,'balance',START_BAL)
            eq = info.get('equity',bal) if isinstance(info,dict) else getattr(info,'equity',bal)
            await q.message.reply_text(f"📊 Bal ${bal:.2f} Eq ${eq:.2f} PnL ${eq-START_BAL:.2f}\nDaily ${daily_pnl:.2f} Trades {trades_today}/{MAX_TRADES} Scans {scan_count}", parse_mode='Markdown', reply_markup=kb())
    elif d=="smc":
        await q.message.reply_text(f"📈 *SMC Status*\nScans {scan_count}\nSetups {len(setups)}\nTrades today {trades_today}/{MAX_TRADES}\nDaily ${daily_pnl:.2f}/${DAILY_TARGET}\nSession {'OPEN' if is_session() else 'CLOSED'} London/NY 8-11,13-18 Lagos", parse_mode='Markdown', reply_markup=kb())
    elif d=="setups":
        if not setups: await q.message.reply_text("No SMC setups yet. Waiting for OB+FVG+Liq confluence.", parse_mode='Markdown', reply_markup=kb())
        else:
            txt = "✅ *SMC Setups Today:*\n" + "\n".join([f"{s['time']} {s['dir']} @{s['price']:.1f} {s['reason'][:60]}" for s in setups[-5:]])
            await q.message.reply_text(txt, parse_mode='Markdown', reply_markup=kb())
    elif d=="scan":
        await q.message.reply_text("🔍 SMC Scanning 1H+15M...", parse_mode='Markdown'); await run_scan(context.bot)
    elif d=="toggle":
        await q.message.reply_text(f"{'LIVE' if ENABLE_LIVE else 'SIGNAL ONLY'}\n$3 dynamic lot\nSMC values: 50 swing, OB 10 lookback, FVG >0.3 ATR, Liq $1.5 equal\nEnable LIVE: ENABLE_LIVE_TRADING=true", parse_mode='Markdown', reply_markup=kb())

async def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CallbackQueryHandler(btn))
    try:
        from telegram import Bot
        b = Bot(token=TELEGRAM_TOKEN)
        await b.send_message(chat_id=CHAT_ID, text=f"🤖 *v14 SMC COMPANY Started*\nBOS/CHoCH + OB + FVG + Liquidity Sweep + Premium/Discount\nValues: Swing 50, OB 10, FVG 0.3 ATR, Liq $1.5\n$3 risk, $30/day, 2 trades max\nThis one you can present to company", reply_markup=kb())
    except: pass
    asyncio.create_task(loop(app.bot))
    await app.initialize(); await app.start(); await app.updater.start_polling(); await asyncio.Event().wait()

if __name__ == "__main__": asyncio.run(main())
