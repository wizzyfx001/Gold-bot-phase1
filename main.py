import os, asyncio, json, requests
from datetime import datetime
from google import genai
from metaapi_cloud_sdk import MetaApi
import config

print("ENV CHECK:", {
  "METAAPI_TOKEN": "SET" if os.getenv("METAAPI_TOKEN") else "MISSING",
  "METAAPI_ACCOUNT_ID": os.getenv("METAAPI_ACCOUNT_ID"),
  "GEMINI_API_KEY": "SET" if os.getenv("GEMINI_API_KEY") else "MISSING",
  "TELEGRAM": "SET" if os.getenv("TELEGRAM_BOT_TOKEN") else "MISSING"
})

genai_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
TELE_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(msg):
    try:
        if not TELE_TOKEN or not CHAT_ID: return
        requests.post(f"https://api.telegram.org/bot{TELE_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    except: pass

def calc_lot(entry, sl, symbol):
    dist = abs(entry-sl) or 1
    if "XAU" in symbol: lot = config.RISK_PER_TRADE_USD / (dist*10)
    elif "US30" in symbol or "NAS" in symbol or "US500" in symbol: lot = config.RISK_PER_TRADE_USD / (dist*0.1)
    else: lot = config.RISK_PER_TRADE_USD / (dist*1000)
    return max(0.01, min(round(lot,2), 0.5))

async def main():
    token = os.getenv("METAAPI_TOKEN")
    if not token:
        print("❌ METAAPI_TOKEN MISSING - Add in Railway Variables!")
        send_telegram("❌ METAAPI_TOKEN missing in Railway Variables")
        await asyncio.sleep(999999)
        return

    api = MetaApi(token)
    account = await api.metatrader_account_api.get_account(config.ACCOUNT_ID)
    conn = account.get_rpc_connection()
    await conn.connect()
    await conn.wait_synchronized()
    send_telegram(f"🤖 Bot Started! Bal ${config.INITIAL_BALANCE_USD} Target ${config.DAILY_TARGET_USD} (60k Naira)")
    print("Bot started OK")

    while True:
        try:
            info = await conn.get_account_information()
            equity = info['equity']
            pnl = equity - config.INITIAL_BALANCE_USD
            print(f"Equity ${equity:.2f} PnL ${pnl:.2f}")

            if pnl <= -config.DAILY_DD_CUTOFF_USD:
                send_telegram(f"🚨 Daily DD {pnl:.2f} locked")
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

            prompt = f"Prop $306 DD ${config.DAILY_DD_USD} Target ${config.DAILY_TARGET_USD} Risk ${config.RISK_PER_TRADE_USD} Data {json.dumps(structured)} Return JSON max2 [{{symbol,XAUUSD,order_type,BUY_LIMIT or SELL_LIMIT or MARKET_BUY or MARKET_SELL,entry,sl,tp,confidence,reason,rr}}] Conf>=75 RR>=1.8"

            resp = genai_client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            txt = resp.text.replace("```json","").replace("```","").strip()
            try: decisions = json.loads(txt)
            except: decisions = []

            for d in decisions:
                sym=d['symbol']; otype=d['order_type']; entry=float(d['entry']); sl=float(d['sl']); tp=float(d['tp']); conf=int(d.get('confidence',0))
                if conf<75: continue
                lot=calc_lot(entry,sl,sym)
                rr=abs(tp-entry)/abs(entry-sl) if abs(entry-sl)>0 else 0
                if rr<1.8: continue
                send_telegram(f"📌 {conf}% {otype} {sym} @{entry} SL {sl} TP {tp} Lot {lot} RR {rr:.1f}")
                try:
                    if "LIMIT" in otype:
                        if "BUY" in otype: await conn.create_limit_buy_order(sym, lot, entry, sl, tp)
                        else: await conn.create_limit_sell_order(sym, lot, entry, sl, tp)
                    else:
                        if "BUY" in otype: await conn.create_market_buy_order(sym, lot, sl, tp)
                        else: await conn.create_market_sell_order(sym, lot, sl, tp)
                    send_telegram(f"✅ Order placed {sym}")
                except Exception as e: send_telegram(f"❌ {e}")
            await asyncio.sleep(300)
        except Exception as e:
            print(f"Loop error {e}"); await asyncio.sleep(30)

asyncio.run(main())
