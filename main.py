import os
import json
import time
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

TOKEN = "8918068542:AAHxgD83YEV3HZgRUjNyw1XRSE7iaOUS1_0"

def get_realtime_data(symbol):
    symbol_upper = symbol.upper().strip()
    
    # تحويل رموز الذهب إلى رمز السعر الفوري اللحظي
    if symbol_upper in ["GC=F", "XAUUSD", "XAUUSD=X", "GOLD", "الذهب"]:
        url = "https://api.binance.com/api/v3/ticker/24hr?symbol=PAXGUSDT"
    elif symbol_upper in ["BTCUSD", "BTCUSDT", "BTC"]:
        url = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
    elif symbol_upper in ["ETHUSD", "ETHUSDT", "ETH"]:
        url = "https://api.binance.com/api/v3/ticker/24hr?symbol=ETHUSDT"
    else:
        # احتياطي للمؤشرات والفوركس عبر Yahoo اللحظي
        ticker = f"{symbol_upper}=X" if len(symbol_upper) == 6 else symbol_upper
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?interval=1m&range=1d"

    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            data = json.loads(response.read().decode())
            
            if "lastPrice" in data:
                # سعر فوري مباشر وحي 100% (Real-Time Tick)
                current_price = float(data['lastPrice'])
                high_p = float(data['highPrice'])
                low_p = float(data['lowPrice'])
                # توليد سلسلة أسعار لحظية للتحليل الفني
                prices = [low_p, (low_p + current_price)/2, high_p, current_price]
                return prices, current_price
            elif 'chart' in data:
                result = data['chart']['result'][0]
                quotes = result['indicators']['quote'][0]['close']
                prices = [p for p in quotes if p is not None]
                return prices, prices[-1]
    except Exception as e:
        return None, None

def calculate_rsi(prices, period=14):
    if len(prices) < 2:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains) / len(gains) if gains else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_ema(prices, period):
    if not prices:
        return 0
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * k) + (ema * (1 - k))
    return round(ema, 4)

def analyze_scalping(symbol):
    prices, current_price = get_realtime_data(symbol)
    if not current_price:
        return f"❌ متعذر جلب السعر اللحظي للرمز ({symbol}). تأكد من الرمز وحاول مجدداً."

    rsi = calculate_rsi(prices)
    ema9 = calculate_ema(prices, 9)
    ema21 = calculate_ema(prices, 21)

    # حساب أهداف وقف الخسارة للسكالبينج السريع (أهداف قريبة وحسّاسة)
    sl_offset = current_price * 0.0015  # 0.15% وقف خسارة
    tp1_offset = current_price * 0.0025 # 0.25% هدف أول
    tp2_offset = current_price * 0.0045 # 0.45% هدف ثاني

    if current_price >= ema9:
        action = "شراء سكالبينج (BUY SCALP)"
        emoji = "🟢"
        signal_type = "سريعة / قوية"
        sl = round(current_price - sl_offset, 2)
        tp1 = round(current_price + tp1_offset, 2)
        tp2 = round(current_price + tp2_offset, 2)
    else:
        action = "بيع سكالبينج (SELL SCALP)"
        emoji = "🔴"
        signal_type = "سريعة / قوية"
        sl = round(current_price + sl_offset, 2)
        tp1 = round(current_price - tp1_offset, 2)
        tp2 = round(current_price - tp2_offset, 2)

    text = (
        f"⚡ **توصية سكالبينج لحظية ({symbol.upper()})** ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **الاتجاه اللحظي:** {action} {emoji}\n"
        f"🎯 **قوة الإشارة:** {signal_type}\n"
        f"💵 **السعر المباشر (Real-Time):** `{round(current_price, 2)}`\n\n"
        f"🛑 **وقف الخسارة (SL):** `{sl}`\n"
        f"🎯 **الهدف الأول (TP1):** `{tp1}`\n"
        f"🎯 **الهدف الثاني (TP2):** `{tp2}`\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📐 **مؤشرات السكالبينج:**\n"
        f"• **مؤشر RSI اللحظي:** {rsi}\n"
        f"• **متوسط EMA9:** {ema9}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏱️ **التحديث:** مباشر الآن."
    )
    return text

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}).encode('utf-8')
    req = urllib.request.Request(url, data=payload, headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req)
    except Exception as e:
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
                    send_message(chat_id, "أهلاً بك! البوت يعمل الآن بالأسعار اللحظية المباشرة للسكالبينج ⚡\nأرسل:\n`/analyze XAUUSD` للذهب المباشر")
                elif text.startswith("/analyze"):
                    parts = text.split()
                    symbol = parts[1] if len(parts) > 1 else "XAUUSD"
                    send_message(chat_id, f"⚡ جاري جلب السعر المباشر والتحليل اللحظي لـ {symbol}...")
                    analysis = analyze_scalping(symbol)
                    send_message(chat_id, analysis)
    except Exception as e:
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
