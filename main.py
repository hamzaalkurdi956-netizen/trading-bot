import os
import json
import time
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import yfinance as yf
import pandas as pd

TOKEN = os.environ.get("TELEGRAM_TOKEN", "8918068542:AAHxgD83YEV3HZgRUjNyw1XRSE7iaOUS1_0")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

def get_market_analysis_data(symbol):
    """جلب بيانات الشمعات وتحديث المؤشرات الفنية (RSI & EMA)"""
    symbol_clean = symbol.upper().strip().replace("/", "").replace(".ECN", "")
    
    ticker_map = {
        "XAUUSD": "GC=F", "GOLD": "GC=F", "الذهب": "GC=F", "XAU": "GC=F",
        "US100": "NQ=F", "NAS100": "NQ=F", "NASDAQ": "NQ=F", "NQ": "NQ=F",
        "US30": "YM=F", "DJ30": "YM=F", "DOW": "YM=F", "YM": "YM=F"
    }
    
    target = ticker_map.get(symbol_clean, symbol_clean)
    
    try:
        df = yf.download(tickers=target, period="1d", interval="5m", progress=False)
        if df.empty or len(df) < 20:
            df = yf.download(tickers=target, period="5d", interval="15m", progress=False)

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close_series = df['Close']
        current_price = float(close_series.iloc[-1])

        # حساب مؤشر EMA 20
        ema20 = float(close_series.ewm(span=20, adjust=False).mean().iloc[-1])

        # حساب مؤشر RSI 14
        delta = close_series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        rsi14 = float((100 - (100 / (1 + rs))).iloc[-1])

        return {
            "price": current_price,
            "ema20": round(ema20, 2),
            "rsi14": round(rsi14, 2),
            "recent_closes": [round(x, 2) for x in close_series.tail(5).tolist()]
        }
    except Exception as e:
        print(f"Error fetching data: {e}")
        return None

def analyze_with_ai(symbol, data):
    """تحليل الحركة والمؤشرات باستخدام Gemini 2.5 Flash"""
    price = data["price"]
    ema20 = data["ema20"]
    rsi14 = data["rsi14"]
    closes = data["recent_closes"]

    if not GEMINI_API_KEY:
        return fallback_analysis(symbol, price, ema20, rsi14)

    prompt = (
        f"أنت خبير تداول واستراتيجيات السكالبينج الاحترافية (SMC & Technical Analysis).\n"
        f"الزوج: {symbol.upper()}\n"
        f"السعر الحالي: {price}\n"
        f"مؤشر EMA 20: {ema20}\n"
        f"مؤشر RSI (14): {rsi14}\n"
        f"إغلاقات الشموع الأخيرة: {closes}\n\n"
        f"بناءً على الشروط الفنية:\n"
        f"- إذا كان السعر فوق EMA20 وRSI معتدل إلى مرتفع، فضل الشراء (BUY).\n"
        f"- إذا كان السعر تحت EMA20 وRSI مائل للهبوط، فضل البيع (SELL).\n"
        f"- حدد وقف الخسارة والأهداف بدقة ملائمة لصفقة سكالبينج.\n\n"
        f"أخرج النتيجة بالقالب التالي حصراً وبشكل منظم:\n"
        f"📌 الاتجاه: (شراء BUY أو بيع SELL)\n"
        f"💵 سعر الدخول: {price}\n"
        f"🛑 وقف الخسارة (SL): [القيمة]\n"
        f"🎯 الهدف الأول (TP1): [القيمة]\n"
        f"🎯 الهدف الثاني (TP2): [القيمة]\n"
        f"📊 RSI: {rsi14} | EMA20: {ema20}\n"
        f"💡 التبرير الفني: [جملة واضحة تشرح سبب الصفقة بناءً على المؤشرات]"
    )

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode('utf-8')

    try:
        req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=12) as response:
            res_data = json.loads(response.read().decode())
            ai_text = res_data['candidates'][0]['content']['parts'][0]['text']
            return f"🧠 **تحليل الشارت والذكاء الاصطناعي ({symbol.upper()})**\n━━━━━━━━━━━━━━━━━━━\n" + ai_text
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return fallback_analysis(symbol, price, ema20, rsi14)

def fallback_analysis(symbol, price, ema20, rsi14):
    symbol_upper = symbol.upper()
    action = "شراء (BUY) 🟢" if price > ema20 else "بيع (SELL) 🔴"
    sl_p = price * 0.002
    tp1_p = price * 0.003
    tp2_p = price * 0.006

    sl = round(price - sl_p if "BUY" in action else price + sl_p, 2)
    tp1 = round(price + tp1_p if "BUY" in action else price - tp1_p, 2)
    tp2 = round(price + tp2_p if "BUY" in action else price - tp2_p, 2)

    return (
        f"🎯 **توصية فنية مبسطة ({symbol.upper()})**\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **الاتجاه:** {action}\n"
        f"💵 **السعر:** `{round(price, 2)}` | **EMA20:** `{ema20}` | **RSI:** `{rsi14}`\n\n"
        f"🛑 **SL:** `{sl}` | 🎯 **TP1:** `{tp1}` | 🎯 **TP2:** `{tp2}`"
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
                    send_message(chat_id, "أهلاً بك! البوت متصل بالذكاء الاصطناعي ومؤشرات الشارت الفنية ⚡\nأرسل /analyze GOLD أو /analyze US100 للتحليل.")
                elif text.startswith("/analyze"):
                    parts = text.split()
                    symbol = parts[1] if len(parts) > 1 else "XAUUSD"
                    send_message(chat_id, f"📊 جاري قراءة بيانات الشارت والمؤشرات الفنية لـ {symbol.upper()}...")
                    market_data = get_market_analysis_data(symbol)
                    if market_data:
                        analysis = analyze_with_ai(symbol, market_data)
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
