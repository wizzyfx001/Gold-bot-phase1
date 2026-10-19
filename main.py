import os, asyncio, json, requests
from datetime import datetime
from google import genai as google_genai
from metaapi_cloud_sdk import MetaApi
import config

# Gemini new SDK
client = google_genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

TELE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(msg):
    if not TELE_TOKEN or not CHAT_ID: return
    try:
        url = f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg})
    except: pass

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
    # FIXED LINE HERE - metatrader not metatrading
    account = await api.metatrader_account_api.get_account(config.ACCOUNT_ID)
    conn = account.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()
    
    send_telegram(f"🤖 Gold Bot Started! Balance ${config.INITIAL_BALANCE_USD} DD ${config.DAILY_DD_USD} Target ${config.DAILY_TARGET_USD}")
    print("Bot started")

    while True:
        try:
            info = await conn.get_account_information()
            equity = info['equity']
            pnl = equity - config.INITIAL_BALANCE_USD
            print(f"Equity ${equity:.2f} PnL ${pnl:.2f}")

            if pnl <= -config.DAILY_DD_CUTOFF_USD:
                send_telegram(f"🚨 DAILY DD HIT {pnl:.2f} - Locked")
                await asyncio.sleep(3600); continue
            if pnl >= config.DAILY_TARGET_USD:
                send_telegram(f"🏆 TARGET HIT +${pnl:.2f} PHASE 1 PASSED!")
                await asyncio.sleep(3600); continue

            structured = {}
            for sym in config.SYMBOLS:
                try:
                    c = await conn.get_historical_candles(sym, '15m', 100)
                    structured[sym] = {"price": c[-1]['close']}
                except: pass

            prompt = f"Prop $306 DD ${config.DAILY_DD_USD} Max ${config.MAX_DD_USD} Target ${config.DAILY_TARGET_USD} Risk ${config.RISK_PER_TRADE_USD} Need RR>=1:2 Data {json.dumps(structured)} Return JSON only max 2: [{{\"symbol\":\"XAUUSD\",\"order_type\":\"BUY_LIMIT or SELL_LIMIT or MARKET_BUY or MARKET_SELL\",\"entry\":2642.5,\"sl\":2637,\"tp\":2655,\"confidence\":85,\"reason\":\"BOS H1 + OB M15\"}}] Conf>=75."

            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            txt = response.text.replace("```json","").replace("```","").strip()
            try: decisions = json.loads(txt)
            except: decisions = []

            for d in decisions:
                sym = d['symbol']; otype = d['order_type']; entry=float(d['entry']); sl=float(d['sl']); tp=float(d['tp']); conf=int(d.get('confidence',0))
                if conf<75: continue
                lot = calc_lot(entry, sl, sym)
                rr = abs(tp-entry)/abs(entry-sl) if abs(entry-sl)>0 else 0
                if rr<1.8: continue
                send_telegram(f"📌 SIGNAL {conf}% {otype} {sym} Entry {entry} SL {sl} TP {tp} Lot {lot} RR {rr:.1f}")
                try:
                    if "LIMIT" in otype:
                        if "BUY" in otype: await conn.create_limit_buy_order(sym, lot, entry, sl, tp)
                        else: await conn.create_limit_sell_order(sym, lot, entry, sl, tp)
                    else:
                        if "BUY" in otype: await conn.create_market_buy_order(sym, lot, sl, tp)
                        else: await conn.create_market_sell_order(sym, lot, sl, tp)
                    send_telegram(f"✅ Order placed {sym}")
                except Exception as e: send_telegram(f"❌ Fail {e}")
            
            await asyncio.sleep(300)
        except Exception as e:
            print(f"Error {e}"); await asyncio.sleep(30)

asyncio.run(main())
