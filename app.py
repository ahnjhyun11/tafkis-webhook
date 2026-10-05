import os
import imaplib
import email
import threading
import time
from flask import Flask, request
import requests

app = Flask(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASS")

def send_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

@app.route("/")
def home():
    return "TAFKIS Email->Telegram Bot Running"

@app.route("/test_telegram")
def test_telegram():
    send_telegram("TAFKIS.bot 연결 성공!\n\n이제 TradingView에서 BSE 70+PI LONG 뜨면 이 채팅방으로 바로 알림 올 거야.\n\n테스트 완료 - 서버 정상 작동 중")
    return f"전송 성공 to {CHAT_ID}"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    send_telegram(f"TradingView Webhook\n{data}")
    return "ok"

def check_gmail():
    while True:
        try:
            if not GMAIL_USER or not GMAIL_APP_PASS:
                time.sleep(30)
                continue
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(GMAIL_USER, GMAIL_APP_PASS)
            mail.select("INBOX")
            _, data = mail.search(None, '(UNSEEN FROM "TradingView")')
            for num in data[0].split():
                _, msg_data = mail.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(msg_data[0][1])
                subject = msg["Subject"] or ""
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            payload = part.get_payload(decode=True)
                            if payload:
                                body = payload.decode(errors="ignore")
                            break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode(errors="ignore")
                text = f"{subject}\n{body}"
                side = "BUY" if "LONG" in text.upper() or "BUY" in text.upper() else "SELL" if "SHORT" in text.upper() else "ALERT"
                send_telegram(f"TAFKIS BSE 알림 [{side}]\n\n{subject}\n\n{body[:500]}")
                mail.store(num, '+FLAGS', '\\Seen')
            mail.logout()
        except Exception as e:
            print(f"Gmail check error: {e}")
        time.sleep(30)

threading.Thread(target=check_gmail, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
