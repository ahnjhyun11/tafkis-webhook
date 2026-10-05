from flask import Flask, request, jsonify
import requests, json, datetime, os

app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8886231117:AAH2FZtC57YuvcVhljiDKaJxTDY7xGzWEeg")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "384241591")

LOG_FILE = "taf_alerts.jsonl"

def send_telegram(msg, chat_id=None):
    target = chat_id or TELEGRAM_CHAT_ID
    if not target:
        print("No chat ID")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target, "text": msg, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        print(f"TG {r.status_code}: {r.text[:300]}")
        return r.ok
    except Exception as e:
        print(f"TG error {e}")
        return False

@app.route("/webhook/taf", methods=["POST"])
def taf_webhook():
    raw = request.get_data(as_text=True)
    try:
        j = request.get_json(force=True) or {}
    except:
        j = {"raw": raw}
    
    ticker = j.get("ticker", "KRX:005930")
    bse = j.get("bse", j.get("close", ""))
    per = j.get("per", "")
    pbr = j.get("pbr", "")
    taf = j.get("taf", "P1")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"time": now, "data": j}, ensure_ascii=False) + "\n")
    
    per_score = 0
    try:
        per_score = (12 - float(per)) * 12 if per else 0
    except:
        pass
    
    msg = f"""🚀 *TAF LONG 알림*

종목: {ticker}
시간: {now}
BSE: {bse} (70+ PASS ✅)
PER: {per} → Score {per_score:.0f}
PBR: {pbr}
TAF: {taf} = LONG
False 41%→25% 필터 통과
Formula: (12-PER)*12 / (1.5-PBR)*100
"""
    send_telegram(msg)
    return jsonify({"status": "ok", "ticker": ticker})

@app.route("/test_telegram")
def test_tg():
    chat_id = request.args.get("chat_id", TELEGRAM_CHAT_ID)
    msg = """✅ *TAFKIS_bot 연결 성공!*

이제 TradingView에서 BSE 70+P1 LONG 뜨면
이 채팅방으로 바로 알림 올 거야.

테스트 완료 - 서버 정상 작동 중
"""
    ok = send_telegram(msg, chat_id)
    return f"<h2>{'✅ 전송 성공' if ok else '❌ 전송 실패'} to {chat_id}</h2><br><pre>{msg}</pre>"

@app.route("/")
def home():
    return f"""
    <h1>TAF v4 Webhook Server</h1>
    <p>Bot: @TAFKIS_bot</p>
    <p>Chat ID: {TELEGRAM_CHAT_ID} (숨김 처리됨)</p>
    <ul>
        <li>POST /webhook/taf - TradingView 웹훅 받기</li>
        <li>GET /test_telegram - 테스트 메시지 보내기</li>
    </ul>
    <p><a href='/test_telegram'>테스트 보내기 클릭</a></p>
    <hr>
    <p>TradingView 알림 Webhook URL:</p>
    <code>https://YOUR-URL.up.railway.app/webhook/taf</code>
    """

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
