import os
import imaplib
import email
import threading
import time
from datetime import datetime
import requests
import pandas as pd
from flask import Flask, request

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS")

# 감시할 종목 리스트 - 여기서 추가/삭제 가능
WATCHLIST_KRX = ["005930"]  # 삼성전자, 더 추가하면 , "000660" 이런식으로
WATCHLIST_CRYPTO = ["SOLUSDT", "BTCUSDT"]  # 바이낸스 심볼

# 수동 PER/PBR (네이버금융 값) - 005930 기준, 자동 가져오기로 나중에 업그레이드 가능
PER_INPUT = {"005930": 10.5, "000660": 8.2}
PBR_INPUT = {"005930": 1.15, "000660": 1.3}

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[SKIP TG] {text[:100]}")
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
        print(f"TG sent: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

def get_ohlcv_krx(ticker, limit=300):
    """야후 파이낸스로 KRX 일봉 가져오기"""
    try:
        # yfinance 없이도 돌아가게 requests로 직접
        # Yahoo chart API
        symbol = f"{ticker}.KS"
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1y"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=15)
        data = resp.json()
        result = data['chart']['result'][0]
        timestamps = result['timestamp']
        quotes = result['indicators']['quote'][0]
        df = pd.DataFrame({
            'timestamp': timestamps,
            'open': quotes['open'],
            'high': quotes['high'],
            'low': quotes['low'],
            'close': quotes['close'],
            'volume': quotes['volume']
        })
        df = df.dropna()
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='s')
        return df.tail(limit)
    except Exception as e:
        print(f"KRX fetch error {ticker}: {e}")
        return None

def get_ohlcv_binance(symbol, limit=300):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
        resp = requests.get(url, timeout=15)
        data = resp.json()
        df = pd.DataFrame(data, columns=['ts','open','high','low','close','volume','ct','qa','nt','tb','tq','ig'])
        df['close'] = df['close'].astype(float)
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['volume'] = df['volume'].astype(float)
        return df
    except Exception as e:
        print(f"Binance fetch error {symbol}: {e}")
        return None

def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def calc_sma(s, period):
    return s.rolling(period).mean()

def calc_ema(s, period):
    return s.ewm(span=period, adjust=False).mean()

def find_swing_lows(lows, pivot_len=5):
    swing_lows = []
    for i in range(pivot_len, len(lows)-pivot_len):
        window = lows[i-pivot_len:i+pivot_len+1]
        if lows[i] == min(window):
            swing_lows.append((i, lows[i]))
    return swing_lows

def find_swing_highs(highs, pivot_len=5):
    swing_highs = []
    for i in range(pivot_len, len(highs)-pivot_len):
        window = highs[i-pivot_len:i+pivot_len+1]
        if highs[i] == max(window):
            swing_highs.append((i, highs[i]))
    return swing_highs

def calc_bse_taf(df, ticker="005930"):
    if df is None or len(df) < 210:
        return None
    close = df['close']
    high = df['high']
    low = df['low']
    volume = df['volume']
    
    # === BSE ===
    rsi = calc_rsi(close, 14)
    rsi_last = rsi.iloc[-1]
    rsi_score = 100 - rsi_last
    
    ma200 = calc_sma(close, 200).iloc[-1]
    disp = (close.iloc[-1] - ma200) / ma200 * 100 if ma200 else 0
    disp_score = max(0, min(100, -disp * 4 + 30))
    bb_score = 85 if rsi_last < 30 else 60 if rsi_last < 40 else 35
    tech = rsi_score * 0.5 + disp_score * 0.3 + bb_score * 0.2
    
    vol_sma20 = calc_sma(volume, 20).iloc[-1]
    vol_last = volume.iloc[-1]
    foreign_score = min(100, vol_last / vol_sma20 * 15 + 10) if vol_sma20 else 50
    supply = foreign_score * 0.6 + 60 * 0.25 + 70 * 0.15
    
    vix_score = min(100, (50 - rsi_last) * 1.5 + 30)
    sentiment = vix_score * 0.4 + 80 * 0.4 + 75 * 0.2
    
    # PER/PBR - KRX는 수동 입력, 크립토는 중립 78
    if ticker in PER_INPUT:
        per = PER_INPUT[ticker]
        pbr = PBR_INPUT.get(ticker, 1.2)
        per_score = max(0, min(100, (12 - per) * 12))
        pbr_score = max(0, min(100, (1.5 - pbr) * 100))
        valuation = per_score * 0.5 + pbr_score * 0.3 + 78 * 0.2
    else:
        valuation = 70  # 크립토는 중립
        per_score = pbr_score = 50
    
    bse_score = tech * 0.35 + supply * 0.25 + valuation * 0.25 + sentiment * 0.15
    
    # === TAF ===
    pivot_len = 5
    lows = low.values
    highs = high.values
    
    swing_lows = find_swing_lows(lows, pivot_len)
    swing_highs = find_swing_highs(highs, pivot_len)
    
    taf_grade = "P0"
    if len(swing_lows) >= 2 and len(swing_highs) >= 1:
        swing_low_val = swing_lows[-1][1]
        prev_swing_low = swing_lows[-2][1]
        prev_swing_high = swing_highs[-1][1] if swing_highs else high.iloc[-20:].max()
        
        higher_low = swing_low_val > prev_swing_low
        lower_low = swing_low_val < prev_swing_low
        breakout = close.iloc[-1] > prev_swing_high
        support_hold = close.iloc[-1] >= swing_low_val
        
        macd_line = calc_ema(close, 12) - calc_ema(close, 26)
        macd_sig = calc_ema(macd_line, 9)
        momentum_up = macd_line.iloc[-1] > macd_sig.iloc[-1] and macd_line.iloc[-1] > macd_line.iloc[-2]
        momentum_down = macd_line.iloc[-1] < macd_sig.iloc[-1]
        oversold = rsi_last <= 35
        bull_div = lower_low and rsi.iloc[-1] > rsi.iloc[-2]
        
        sma20 = calc_sma(close, 20).iloc[-1]
        sma50 = calc_sma(close, 50).iloc[-1]
        
        if lower_low and (momentum_down or close.iloc[-1] < swing_low_val):
            taf_grade = "P4"
        elif higher_low and breakout and momentum_up and close.iloc[-1] > sma20:
            taf_grade = "P1"
        elif not lower_low and support_hold and (momentum_up or bull_div):
            taf_grade = "P2"
        elif close.iloc[-1] < sma50 and sma20 < sma50 and oversold and support_hold:
            taf_grade = "P3"
    
    is_bse_pass = bse_score >= 70
    tse_long = taf_grade == "P1" and is_bse_pass
    
    return {
        "bse_score": bse_score,
        "taf_grade": taf_grade,
        "tse_long": tse_long,
        "rsi": rsi_last,
        "close": close.iloc[-1],
        "per_score": per_score if 'per_score' in locals() else 0,
        "pbr_score": pbr_score if 'pbr_score' in locals() else 0
    }

# 중복 알림 방지
last_alert_time = {}

def check_all_tickers():
    while True:
        try:
            print(f"[{datetime.now()}] Checking tickers...")
            # KRX
            for ticker in WATCHLIST_KRX:
                result = calc_bse_taf(get_ohlcv_krx(ticker), ticker)
                if not result:
                    continue
                print(f"{ticker}: BSE {result['bse_score']:.1f} TAF {result['taf_grade']} LONG {result['tse_long']}")
                key = f"KRX_{ticker}"
                if result['tse_long']:
                    # 24시간 내 중복 방지
                    if key in last_alert_time and time.time() - last_alert_time[key] < 86400:
                        continue
                    send_telegram(f"🚨 *TAFKIS BSE REAL 70+PI LONG*\n\n종목: *{ticker}* (삼성전자)\nBSE: {result['bse_score']:.1f} / 70\nTAF: {result['taf_grade']}\nRSI: {result['rsi']:.1f}\n종가: {result['close']:,}\n\n조건: Higher Low + Breakout + Momentum UP\n#KRX #005930")
                    last_alert_time[key] = time.time()
            
            # Crypto
            for symbol in WATCHLIST_CRYPTO:
                result = calc_bse_taf(get_ohlcv_binance(symbol), symbol)
                if not result:
                    continue
                print(f"{symbol}: BSE {result['bse_score']:.1f} TAF {result['taf_grade']} LONG {result['tse_long']}")
                key = f"CRYPTO_{symbol}"
                if result['tse_long']:
                    if key in last_alert_time and time.time() - last_alert_time[key] < 86400:
                        continue
                    send_telegram(f"🚨 *TAFKIS BSE 70+P1 LONG*\n\n종목: *{symbol}*\nBSE: {result['bse_score']:.1f}\nTAF: {result['taf_grade']}\nRSI: {result['rsi']:.1f}\n종가: ${result['close']:.2f}\n\n#Crypto #{symbol}")
                    last_alert_time[key] = time.time()
            
            # 매일 장 마감 후 체크 - 1시간마다
            time.sleep(3600)
            
        except Exception as e:
            print(f"BSE check error: {e}")
            import traceback; traceback.print_exc()
            time.sleep(300)

@app.route("/")
def home():
    return "TAFKIS Direct Monitor Running - No TradingView needed!"

@app.route("/test_telegram")
def test_telegram():
    send_telegram("✅ *TAFKIS Direct Monitor 연결 성공!*\n\n이제 서버가 TradingView 없이 직접 BSE 70+PI 계산해서 감시해\n\n감시 종목: 005930, SOLUSDT, BTCUSDT\n체크 주기: 1시간")
    return f"전송 성공 to {CHAT_ID}"

@app.route("/check_now")
def check_now():
    """수동으로 지금 바로 체크"""
    out = []
    for ticker in WATCHLIST_KRX:
        r = calc_bse_taf(get_ohlcv_krx(ticker), ticker)
        out.append(f"{ticker}: {r}")
    for sym in WATCHLIST_CRYPTO:
        r = calc_bse_taf(get_ohlcv_binance(sym), sym)
        out.append(f"{sym}: {r}")
    return "<br>".join([str(x) for x in out])

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    send_telegram(f"TradingView Webhook\n{data}")
    return "ok"

# 백그라운드 스레드 시작
threading.Thread(target=check_all_tickers, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
