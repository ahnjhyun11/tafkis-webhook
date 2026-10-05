import os
import threading
import time
from datetime import datetime
import requests
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

WATCHLIST_KRX = ["005930"]
WATCHLIST_CRYPTO = ["SOLUSDT", "BTCUSDT"]

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[SKIP] {text[:80]}")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=10)
        print(f"TG {r.status_code}")
    except Exception as e:
        print(f"TG error {e}")

def get_krx_close(ticker):
    try:
        symbol = f"{ticker}.KS"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1y"
        resp = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=15)
        j = resp.json()
        closes = j['chart']['result'][0]['indicators']['quote'][0]['close']
        closes = [c for c in closes if c is not None]
        return closes[-250:] if len(closes)>250 else closes
    except Exception as e:
        print(f"KRX {ticker} error {e}")
        return []

def get_binance_close(symbol):
    # Railway US West에서 api.binance.com 차단 -> data-api.binance.vision 우회 + Bybit fallback
    endpoints = [
        f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval=1d&limit=250",
        f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit=250",
    ]
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=15)
            data = resp.json()
            # data가 리스트여야 정상
            if isinstance(data, list) and len(data) > 100:
                closes = [float(k[4]) for k in data]
                print(f"{symbol} from {url} ok {len(closes)}")
                return closes
            else:
                print(f"{symbol} {url} returned {data}")
        except Exception as e:
            print(f"Binance {symbol} {url} error {e}")
            continue
    
    # Fallback: Bybit
    try:
        url = f"https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}&interval=D&limit=250"
        resp = requests.get(url, timeout=15)
        j = resp.json()
        if j.get('result', {}).get('list'):
            klines = j['result']['list'][::-1]  # bybit는 최신이 먼저 옴, 뒤집기
            closes = [float(k[4]) for k in klines]
            print(f"{symbol} from Bybit ok {len(closes)}")
            return closes
    except Exception as e:
        print(f"Bybit {symbol} error {e}")
    
    return []

def sma(data, period):
    if len(data) < period:
        return None
    return sum(data[-period:]) / period

def rsi(data, period=14):
    if len(data) < period+1:
        return 50
    gains = []
    losses = []
    for i in range(1, period+1):
        diff = data[-i] - data[-i-1]
        if diff > 0:
            gains.append(diff)
        else:
            losses.append(-diff)
    avg_gain = sum(gains)/period if gains else 0
    avg_loss = sum(losses)/period if losses else 0.0001
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100/(1+rs))

def check_ticker_simple(closes, ticker="005930"):
    if len(closes) < 210:
        return None
    close = closes[-1]
    rsi_last = rsi(closes, 14)
    ma200 = sma(closes, 200)
    ma20 = sma(closes, 20)
    ma50 = sma(closes, 50)
    if not ma200 or not ma20 or not ma50:
        return None
    
    rsi_score = 100 - rsi_last
    disp = (close - ma200)/ma200*100
    disp_score = max(0, min(100, -disp*4+30))
    bb_score = 85 if rsi_last < 30 else 60 if rsi_last < 40 else 35
    tech = rsi_score*0.5 + disp_score*0.3 + bb_score*0.2
    bse = tech*0.6 + 70*0.4
    
    recent_lows = closes[-20:]
    prev_lows = closes[-40:-20]
    higher_low = min(recent_lows) > min(prev_lows)
    breakout = close > max(closes[-40:-5])
    momentum = close > ma20 and ma20 > ma50
    
    taf = "P1" if (higher_low and breakout and momentum) else "P0"
    long_signal = bse >= 70 and taf == "P1"
    
    return {"bse": bse, "rsi": rsi_last, "close": close, "taf": taf, "long": long_signal, "higher_low": higher_low, "breakout": breakout}

last_alert = {}

def monitor():
    time.sleep(10)
    while True:
        try:
            print(f"[{datetime.now()}] monitor tick")
            for t in WATCHLIST_KRX:
                closes = get_krx_close(t)
                res = check_ticker_simple(closes, t)
                if not res:
                    continue
                print(f"{t} BSE:{res['bse']:.1f} TAF:{res['taf']} LONG:{res['long']}")
                if res['long']:
                    key = f"KRX_{t}"
                    if key in last_alert and time.time() - last_alert[key] < 86400:
                        continue
                    send_telegram(f"🚨 TAFKIS BSE 70+PI LONG\n종목: {t} (삼성전자)\nBSE: {res['bse']:.1f}\nTAF: {res['taf']}\nRSI: {res['rsi']:.1f}\n종가: {res['close']}\n#직접감시")
                    last_alert[key]=time.time()
            for s in WATCHLIST_CRYPTO:
                closes = get_binance_close(s)
                res = check_ticker_simple(closes, s)
                if not res:
                    print(f"{s} no data")
                    continue
                print(f"{s} BSE:{res['bse']:.1f} TAF:{res['taf']} LONG:{res['long']}")
                if res['long']:
                    key = f"CRY_{s}"
                    if key in last_alert and time.time() - last_alert[key] < 86400:
                        continue
                    send_telegram(f"🚨 TAFKIS BSE 70+PI LONG\n종목: {s}\nBSE: {res['bse']:.1f}\nTAF: {res['taf']}\nRSI: {res['rsi']:.1f}\n가격: ${res['close']:.2f}")
                    last_alert[key]=time.time()
            time.sleep(3600)
        except Exception as e:
            print(f"monitor error {e}")
            import traceback; traceback.print_exc()
            time.sleep(300)

@app.route("/")
def home():
    return "TAFKIS Direct Monitor Running ✅ No TradingView needed"

@app.route("/test_telegram")
def test_telegram():
    send_telegram("✅ TAFKIS 직접감시 연결 성공!\n\nBSE 70+PI 감시 중\n종목: 005930, SOLUSDT, BTCUSDT\n1시간마다 체크")
    return f"전송 성공 to {CHAT_ID}"

@app.route("/check_now")
def check_now():
    out=[]
    for t in WATCHLIST_KRX:
        c=get_krx_close(t)
        r=check_ticker_simple(c,t)
        out.append(f"{t}: BSE {r['bse']:.1f} TAF {r['taf']} LONG {r['long']} Close {r['close']}" if r else f"{t}: fail (len={len(c)})")
    for s in WATCHLIST_CRYPTO:
        c=get_binance_close(s)
        r=check_ticker_simple(c,s)
        out.append(f"{s}: BSE {r['bse']:.1f} TAF {r['taf']} LONG {r['long']} Close ${r['close']:.2f} len={len(c)}" if r else f"{s}: fail len={len(c)}")
    return "<br>".join(out)

threading.Thread(target=monitor, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
