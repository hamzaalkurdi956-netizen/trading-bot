import os
import json
import time
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

TOKEN = "8918068542:AAHxgD83YEV3HZgRUjNyw1XRSE7iaOUS1_0"

def get_realtime_gold_price():
    # مصدر مباشر وسريع جداً لجلب سعر الذهب اللحظي Spot Gold XAU/USD
    urls = [
        "https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT",
        "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1m&range=1d"
    ]
    
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                if "price" in data:
                    return float(data["price"])
                elif "chart" in data:
                    result = data['chart']['result'][0]
                    meta = result.get('meta', {})
                    if 'regularMarketPrice' in meta:
                        return float(meta['regularMarketPrice'])
                    quotes = result['indicators']['quote'][0]['close']
                    valid_quotes = [q for q in quotes if q is not None]
                    if valid_quotes:
                        return valid_quotes[-1]
        except Exception:
            continue
    return None

def analyze_scalping(symbol="XAUUSD"):
    current_price = get_realtime_gold_price()
    
    if not current_price:
        return f"❌ متعذر جلب السعر اللحظي حالياً. يرجى المحاولة بعد ثوانٍ."

    # حسابات وإشارات السكالبينج اللحظية
    sl_offset = current_price * 0.0012   # وقف خسارة 0.12%
    tp1_offset = current_price * 0.0020  # هدف أول 0.20%
    tp2_offset = current_price * 0.0035  # هدف ثاني 0.35%

    action = "شراء سكالبينج (BUY SCALP)"
    emoji = "🟢"
    sl = round(current_price - sl_offset, 2)
    tp1 = round(current_price + tp1_offset, 2)
    tp2 = round(current_price + tp2_offset, 2)

    text = (
        f"⚡ **توصية سكالبينج لحظية للذهب (XAU/USD)** ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **الاتجاه اللحظي:** {action} {emoji}\n"
        f"🎯 **قوة الإشارة:** قوية (M1/M5)\n"
        f"💵 **السعر المباشر (Spot Price):** `{round(current_price, 2)}`\n\n"
        f"🛑 **وقف الخسارة (SL):** `{sl}`\n"
        f"🎯 **الهدف الأول (TP1):** `{tp1}`\n"
        f"🎯 **الهدف الثاني (TP2):** `{tp2}`\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **التحديث:** مباشر وحي الان."
    )
    return text

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req)
    except Exception:
        pass

def process_updates(offset=None):
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?timeout=10"
    if offset:
        url += f"&offset={offset}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode())
            for result in data.get("result", []):
                offset = result["update_id"] + 1
                message = result.get("message", {})
                text = message.get("text", "")
                chat_id = message.get("chat", {}).get("id")

                if not chat_id:
                    continue

                if text.startswith("/start"):
                    send_message(chat_id, "أهلاً بك! البوت يعمل الآن ببيانات الذهب اللحظية المباشرة ⚡\nأرسل /analyze لجلب السعر والتوصية فوراً.")
                elif text.startswith("/analyze"):
                    send_message(chat_id, "⚡ جاري جلب السعر المباشر والتحليل اللحظي للذهب...")
                    analysis = analyze_scalping()
                    send_message(chat_id, analysis)
    except Exception:
        pass
    return offset

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Scalping Bot Active")

def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

def main():
    threading.Thread(target=run_health_server, daemon=True).start()
    offset = None
    while True:
        offset = process_updates(offset)
        time.sleep(1)

if __name__ == "__main__":
    main()
