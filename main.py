import os, asyncio, json, requests
from datetime import datetime
import google.generativeai as genai
from metaapi_cloud_sdk import MetaApi
import config

# Keys from Railway ENV
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
model = genai.GenerativeModel("gemini-1.5-flash")

TELE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(msg):
    if not TELE_TOKEN or not CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    except Exception as e:
        print(f"Telegram error {e}")

def calc_lot(entry, sl, symbol):
    dist = abs(entry-sl)
    if dist==0: return 0.01
    if "XAU" in symbol: lot = config.RISK_PER_TRADE_USD / (dist*10)
    elif "US30" in symbol or "NAS" in symbol or "US500" in symbol: lot = config.RISK_PER_TRADE_USD / (dist*0.1)
    else: lot = config.RISK_PER_TRADE_USD / (dist*1000)
    return max(0.01, min(round(lot,2), 0.5))

async def main():
    token = os.getenv("METAAPI_TOKEN")
    api = MetaApi(token)
    account = await api.metatrading_account_api.get_account(config.ACCOUNT_ID)
    conn = account.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()
    
    send_telegram(f"🤖 *Gold Bot Started*\nBalance: ${config.INITIAL_BALANCE_USD}\nDaily DD: ${config.DAILY_DD_USD} (4% = 24k Naira)\nMax DD: ${config.MAX_DD_USD} (6% = 36k Naira)\nTarget: ${config.DAILY_TARGET_USD} (10% = 60k Naira)\nScanning: {', '.join(config.SYMBOLS)}")
    print(f"Bot started - Target ${config.DAILY_TARGET_USD}")

    while True:
        try:
            info = await conn.get_account_information()
            equity = info['equity']
            pnl = equity - config.INITIAL_BALANCE_USD
            print(f"[{datetime.now()}] Equity ${equity:.2f} PnL ${pnl:.2f}")

            if pnl <= -config.DAILY_DD_CUTOFF_USD:
                send_telegram(f"🚨 *DAILY DD BREACH WARNING*\nPnL: ${pnl:.2f}\nLimit: -${config.DAILY_DD_USD}\nBot locked till tomorrow to protect 24k Naira rule")
                await asyncio.sleep(3600); continue
            if pnl >= config.DAILY_TARGET_USD:
                send_telegram(f"🏆 *DAILY TARGET HIT - PHASE 1 PASSED*\nProfit: +${pnl:.2f} (60k Naira target)\nBot locked. 600k prop Phase 1 would be PASSED!")
                await asyncio.sleep(3600); continue

            structured = {}
            for sym in config.SYMBOLS:
                try:
                    c = await conn.get_historical_candles(sym, '15m', 100)
                    structured[sym] = {"price": c[-1]['close'], "closes": [x['close'] for x in c[-20:]]}
                except: pass

            prompt = f"""Prop challenge $306. Rules: Daily DD ${config.DAILY_DD_USD} Max ${config.MAX_DD_USD} Target ${config.DAILY_TARGET_USD} Risk ${config.RISK_PER_TRADE_USD}. Need RR >=1:2 SL swing TP liquidity. Data: {json.dumps(structured)} Return JSON only max 2: [{{"symbol":"XAUUSD","order_type":"BUY_LIMIT or SELL_LIMIT or MARKET_BUY or MARKET_SELL","entry":2642.5,"sl":2637,"tp":2655,"confidence":85,"reason":"BOS bullish H1 + OB retest M15","rr":2.3}}] If no A+ return []. Conf >=75. For LIMIT entry away from current price."""
            resp = model.generate_content(prompt)
            txt = resp.text.replace("```json","").replace("```","").strip()
            try: decisions = json.loads(txt)
            except: decisions = []

            for d in decisions:
                sym = d['symbol']; otype = d['order_type']; entry=float(d['entry']); sl=float(d['sl']); tp=float(d['tp']); conf=int(d.get('confidence',0))
                if conf<75: continue
                lot = calc_lot(entry, sl, sym)
                rr = abs(tp-entry)/abs(entry-sl) if abs(entry-sl)>0 else 0
                if rr<1.8: continue
                msg = f"📌 *AI SIGNAL {conf}%*\n{otype} {sym}\nEntry: {entry}\nSL: {sl}\nTP: {tp}\nLot: {lot}\nRR: {rr:.1f}\nReason: {d['reason']}"
                print(msg); send_telegram(msg)
                try:
                    if "LIMIT" in otype:
                        if "BUY" in otype: await conn.create_limit_buy_order(sym, lot, entry, sl, tp)
                        else: await conn.create_limit_sell_order(sym, lot, entry, sl, tp)
                        send_telegram(f"✅ PENDING {otype} placed for {sym} @ {entry}")
                    else:
                        if "BUY" in otype: await conn.create_market_buy_order(sym, lot, sl, tp)
                        else: await conn.create_market_sell_order(sym, lot, sl, tp)
                        send_telegram(f"✅ MARKET {otype} filled {sym}")
                except Exception as e: 
                    send_telegram(f"❌ Order failed {sym}: {e}")
            
            await asyncio.sleep(300)
        except Exception as e:
            print(f"Error {e}"); await asyncio.sleep(30)

asyncio.run(main())
