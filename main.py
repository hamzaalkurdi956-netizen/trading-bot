import os
import json
import time
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

TOKEN = os.environ.get("TELEGRAM_TOKEN", "8918068542:AAHxgD83YEV3HZgRUjNyw1XRSE7iaOUS1_0").strip()

# ضع مفتاح Gemini الخاص بك هنا مباشرة بين التنصيص إذا أردت ضمان العمل فوراً
# أو اتركه يقرأ من Render تلقائياً
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GEMINI_KEY") or "اكتب_مفتاح_GEMINI_هنا"
GEMINI_API_KEY = GEMINI_API_KEY.strip()

def fetch_chart_data(symbol):
    """جلب بيانات حركة الشارت والأسعار التاريخية للرمز"""
    symbol_clean = symbol.upper().strip().replace("/", "").replace(".ECN", "")
    
    ticker_map = {
        "XAUUSD": "GC=F", "GOLD": "GC=F", "الذهب": "GC=F", "XAU": "GC=F",
        "US100": "NQ=F", "NAS100": "NQ=F", "NASDAQ": "NQ=F", "NQ": "NQ=F",
        "US30": "YM=F", "DJ30": "YM=F", "DOW": "YM=F", "YM": "YM=F"
    }
    
    target = ticker_map.get(symbol_clean, symbol_clean)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(target)}?interval=5m&range=1d"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.loads(response.read().decode())
            result = data['chart']['result'][0]
            closes = [c for c in result['indicators']['quote'][0]['close'] if c is not None]
            current_price = float(result['meta'].get('regularMarketPrice', closes[-1]))
            return current_price, closes
    except Exception as e:
        print(f"Fetch error: {e}")
        return None, []

def calculate_rsi(closes, period=14):
    """حساب مؤشر RSI14 برمجياً"""
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i-1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
            
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_ema(closes, period=20):
    """حساب مؤشر EMA20 برمجياً"""
    if not closes:
        return 0.0
    k = 2 / (period + 1)
    ema = closes[0]
    for price in closes[1:]:
        ema = (price * k) + (ema * (1 - k))
    return round(ema, 2)

def analyze_with_ai(symbol, price, closes):
    """تحليل الحركة الفنية عبر الربط المباشر مع Gemini"""
    rsi = calculate_rsi(closes)
    ema = calculate_ema(closes)
    recent_closes = [round(c, 2) for c in closes[-5:]] if closes else [price]

    if not GEMINI_API_KEY or "اكتب_مفتاح" in GEMINI_API_KEY:
        return fallback_analysis(symbol, price, ema, rsi)

    prompt = (
        f"أنت خبير تداول. قم بتحليل صفقة سكالبينج لـ {symbol.upper()}.\n"
        f"السعر الحالي: {price}\n"
        f"EMA20: {ema}\n"
        f"RSI14: {rsi}\n"
        f"آخر إغلاقات: {recent_closes}\n\n"
        f"اكتب النتيجة بالعربية بنفس الهيكل وبدون استخدام أي رموز Markdown مثل النجوم (*):\n"
        f"📌 الاتجاه: (BUY أو SELL)\n"
        f"💵 سعر الدخول: {price}\n"
        f"🛑 وقف الخسارة (SL): [القيمة]\n"
        f"🎯 الهدف الأول (TP1): [القيمة]\n"
        f"🎯 الهدف الثاني (TP2): [القيمة]\n"
        f"📊 المؤشرات: RSI {rsi} | EMA20 {ema}\n"
        f"💡 التبرير الفني: [شرح ملخص بناءً على الحركة]"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode('utf-8')

    try:
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=12) as response:
            res_data = json.loads(response.read().decode())
            ai_text = res_data['candidates'][0]['content']['parts'][0]['text']
            return f"🧠 تحليل الذكاء الاصطناعي ({symbol.upper()})\n━━━━━━━━━━━━━━━━━━━\n" + ai_text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return fallback_analysis(symbol, price, ema, rsi)

def fallback_analysis(symbol, price, ema, rsi):
    action = "شراء (BUY) 🟢" if price > ema else "بيع (SELL) 🔴"
    sl_p = price * 0.002
    tp1_p = price * 0.003
    tp2_p = price * 0.006

    sl = round(price - sl_p if "BUY" in action else price + sl_p, 2)
    tp1 = round(price + tp1_p if "BUY" in action else price - tp1_p, 2)
    tp2 = round(price + tp2_p if "BUY" in action else price - tp2_p, 2)

    return (
        f"🎯 توصية سكالبينج فنية ({symbol.upper()})\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 الاتجاه: {action}\n"
        f"💵 السعر: {round(price, 2)} | EMA20: {ema} | RSI: {rsi}\n\n"
        f"🛑 SL: {sl} | 🎯 TP1: {tp1} | 🎯 TP2: {tp2}"
    )

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode('utf-8')
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
                    send_message(chat_id, "أهلاً بك! البوت متصل بالذكاء الاصطناعي ومؤشرات الشارت المباشرة ⚡\nأرسل /analyze GOLD أو /analyze US100 للتحليل.")
                elif text.startswith("/analyze"):
                    parts = text.split()
                    symbol = parts[1] if len(parts) > 1 else "XAUUSD"
                    send_message(chat_id, f"📊 جاري قراءة حركة الشارت ومؤشرات RSI & EMA لـ {symbol.upper()}...")
                    price, closes = fetch_chart_data(symbol)
                    if price:
                        analysis = analyze_with_ai(symbol, price, closes)
                    else:
                        analysis = f"❌ يتعذر جلب بيانات الشارت لـ {symbol.upper()}"
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
