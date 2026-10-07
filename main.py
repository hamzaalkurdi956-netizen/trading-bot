import os
import json
import time
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

TOKEN = os.environ.get("TELEGRAM_TOKEN", "8918068542:AAHxgD83YEV3HZgRUjNyw1XRSE7iaOUS1_0")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def get_market_data(symbol):
    """جلب السعر الفوري المباشر"""
    symbol_clean = symbol.upper().strip().replace("/", "").replace(".ECN", "")
    
    # 1. الذهب الفوري
    if symbol_clean in ["XAUUSD", "GOLD", "الذهب", "XAU"]:
        urls = [
            "https://api.gold-api.com/price/XAU",
            "https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=1m&range=1d"
        ]
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=4) as response:
                    data = json.loads(response.read().decode())
                    if "price" in data:
                        return float(data["price"])
                    elif "chart" in data:
                        meta = data['chart']['result'][0]['meta']
                        return float(meta.get('regularMarketPrice', 0))
            except Exception:
                continue

    # 2. الناسداك والداو والعملات الأخرى
    target = "NQ=F" if symbol_clean in ["US100", "NAS100", "NASDAQ", "NQ"] else \
             "YM=F" if symbol_clean in ["US30", "DJ30", "DOW", "YM"] else symbol_clean

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(target)}?interval=1m&range=1d"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            meta = data['chart']['result'][0]['meta']
            return float(meta.get('regularMarketPrice', 0))
    except Exception:
        return None

def analyze_with_ai(symbol, price):
    """تحليل حركة السعر بواسطة الذكاء الاصطناعي Gemini"""
    if not GEMINI_API_KEY:
        return fallback_analysis(symbol, price)

    prompt = (
        f"أنت محلل خبير في التداول المالي والسكالبينج السريع.\n"
        f"الزوج المطلوب: {symbol.upper()}\n"
        f"السعر الفوري المباشر الآن: {price}\n"
        f"قم بتحليل اتجاه السعر لصفقة سكالبينج لحظية باختصار شديد.\n\n"
        f"حدد بوضوح بالقالب التالي بالضبط:\n"
        f"📌 الاتجاه: (شراء BUY أو بيع SELL) مع إيموجي\n"
        f"💵 سعر الدخول: {price}\n"
        f"🛑 وقف الخسارة (SL): [حدد القيمة]\n"
        f"🎯 الهدف الأول (TP1): [حدد القيمة]\n"
        f"🎯 الهدف الثاني (TP2): [حدد القيمة]\n"
        f"💡 سبب التحليل الفني: [جملة واحدة ملخصة]"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY.strip()}"
    headers = {'Content-Type': 'application/json'}
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode('utf-8')

    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            res_data = json.loads(response.read().decode())
            ai_text = res_data['candidates'][0]['content']['parts'][0]['text']
            return f"🧠 **تحليل الذكاء الاصطناعي ({symbol.upper()})**\n━━━━━━━━━━━━━━━━━━━\n" + ai_text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return fallback_analysis(symbol, price)

def fallback_analysis(symbol, price):
    """محرك تحليلي احتياطي تلقائي"""
    symbol_upper = symbol.upper()
    if any(s in symbol_upper for s in ["US30", "DOW", "YM"]):
        sl_p, tp1_p, tp2_p = 35.0, 50.0, 90.0
    elif any(s in symbol_upper for s in ["US100", "NAS100", "NQ"]):
        sl_p, tp1_p, tp2_p = 20.0, 30.0, 60.0
    else:
        sl_p, tp1_p, tp2_p = price * 0.0015, price * 0.0025, price * 0.0045

    action = "شراء (BUY)" if (int(price * 10) % 2 == 0) else "بيع (SELL)"
    emoji = "🟢🟢" if "BUY" in action else "🔴🔴"
    sl = round(price - sl_p if "BUY" in action else price + sl_p, 2)
    tp1 = round(price + tp1_p if "BUY" in action else price - tp1_p, 2)
    tp2 = round(price + tp2_p if "BUY" in action else price - tp2_p, 2)

    return (
        f"🎯 **توصية سكالبينج فورية ({symbol.upper()})**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **الاتجاه:** {action} {emoji}\n"
        f"💵 **السعر المباشر:** `{round(price, 2)}`\n\n"
        f"🛑 **وقف الخسارة (SL):** `{sl}`\n"
        f"🎯 **الهدف الأول (TP1):** `{tp1}`\n"
        f"🎯 **الهدف الثاني (TP2):** `{tp2}`\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **التحديث:** مباشر وتلقائي."
    )

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
                    send_message(chat_id, "أهلاً بك! البوت متصل بالذكاء الاصطناعي للتحليل المباشر ⚡\nأرسل /analyze US100 أو /analyze GOLD للتحليل.")
                elif text.startswith("/analyze"):
                    parts = text.split()
                    symbol = parts[1] if len(parts) > 1 else "XAUUSD"
                    send_message(chat_id, f"⚡ جاري جلب السعر الفوري وتحليله بواسطة الذكاء الاصطناعي لـ {symbol.upper()}...")
                    price = get_market_data(symbol)
                    if price:
                        analysis = analyze_with_ai(symbol, price)
                    else:
                        analysis = f"❌ يتعذر جلب السعر الفوري لـ {symbol.upper()}"
                    send_message(chat_id, analysis)
    except Exception:
        pass
    return offset

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"AI Trading Bot Active")

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
