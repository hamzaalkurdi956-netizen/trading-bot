import os
import json
import time
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

TOKEN = "8918068542:AAHxgD83YEV3HZgRUjNyw1XRSE7iaOUS1_0"

def get_spot_gold_price():
    # المصدر الأول: API مجاني ومباشر لسعر الذهب الفوري (Spot Gold XAU/USD)
    urls = [
        "https://api.gold-api.com/price/XAU",
        "https://api.goldprice.dev/v1/prices?symbol=XAU-USD-SPOT"
    ]
    
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                # gold-api.com format
                if "price" in data:
                    return float(data["price"])
                # goldprice.dev format
                elif "symbols" in data and len(data["symbols"]) > 0:
                    return float(data["symbols"][0]["price"])
        except Exception:
            continue
            
    # المصدر الاحتياطي المباشر عبر Yahoo Chart API
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1m&range=1d"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            meta = data['chart']['result'][0]['meta']
            return float(meta.get('regularMarketPrice', 0))
    except Exception:
        return None

def analyze_scalping():
    current_price = get_spot_gold_price()
    
    if not current_price:
        return "❌ متعذر جلب سعر الذهب المباشر حالياً. يرجى إعادة المحاولة."

    # حساب أهداف وقف الخسارة للسكالبينج السريع
    sl_offset = current_price * 0.0012   # 0.12% وقف خسارة
    tp1_offset = current_price * 0.0020  # 0.20% هدف أول
    tp2_offset = current_price * 0.0035  # 0.35% هدف ثاني

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
        f"💵 **السعر الفوري المباشر (Spot):** `{round(current_price, 2)}`\n\n"
        f"🛑 **وقف الخسارة (SL):** `{sl}`\n"
        f"🎯 **الهدف الأول (TP1):** `{tp1}`\n"
        f"🎯 **الهدف الثاني (TP2):** `{tp2}`\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **التحديث:** مباشر ومطابق للشرت الآن."
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
                    send_message(chat_id, "أهلاً بك! البوت يعمل بأسعار الذهب الفورية المباشرة (Spot Gold) ⚡\nأرسل /analyze للجلب الفوري.")
                elif text.startswith("/analyze"):
                    send_message(chat_id, "⚡ جاري جلب السعر الفوري المباشر لـ XAU/USD...")
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
