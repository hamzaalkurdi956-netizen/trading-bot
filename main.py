import os
import json
import time
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

TOKEN = "8918068542:AAHxgD83YEV3HZgRUjNyw1XRSE7iaOUS1_0"

def get_crypto_or_forex_data(symbol):
    symbol_upper = symbol.upper().strip()
    
    # Map gold symbols to Yahoo Finance valid ticker or alternative
    if symbol_upper in ["GC=F", "XAUUSD", "XAUUSD=X", "GOLD", "الذهب"]:
        ticker = "GC=F"
    elif not symbol_upper.endswith("=X") and len(symbol_upper) == 6 and not symbol_upper.startswith("^"):
        ticker = f"{symbol_upper}=X"
    else:
        ticker = symbol_upper

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}?range=5d&interval=1h"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode())
            result = data['chart']['result'][0]
            quotes = result['indicators']['quote'][0]['close']
            prices = [p for p in quotes if p is not None]
            
            # Apply adjustment multiplier if contract pricing differs significantly from spot
            current_price = prices[-1]
            return prices, current_price, ticker
    except Exception as e:
        return None, None, ticker

def calculate_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calculate_ema(prices, period):
    if len(prices) < period:
        return prices[-1]
    k = 2 / (period + 1)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * k) + (ema * (1 - k))
    return round(ema, 4)

def analyze_symbol(symbol):
    prices, current_price, actual_ticker = get_crypto_or_forex_data(symbol)
    if not prices or len(prices) < 20:
        return f"❌ متعذر جلب بيانات هذا الرمز ({symbol}). جرب رمزاً آخر مثل EURUSD=X أو BTC-USD."

    rsi = calculate_rsi(prices)
    ema9 = calculate_ema(prices, 9)
    ema21 = calculate_ema(prices, 21)

    if current_price > ema9 and rsi < 70:
        action = "شراء (BUY)"
        emoji = "🟢"
        signal_type = "قوية" if rsi < 60 else "متوسطة"
        sl = round(current_price * 0.995, 4)
        tp1 = round(current_price * 1.008, 4)
        tp2 = round(current_price * 1.015, 4)
    elif current_price < ema9 and rsi > 30:
        action = "بيع (SELL)"
        emoji = "🔴"
        signal_type = "قوية" if rsi > 40 else "متوسطة"
        sl = round(current_price * 1.005, 4)
        tp1 = round(current_price * 0.992, 4)
        tp2 = round(current_price * 0.985, 4)
    else:
        action = "محايد (NEUTRAL)"
        emoji = "⚪"
        signal_type = "ضعيفة"
        sl, tp1, tp2 = current_price, current_price, current_price

    text = (
        f"🚨 **توصية صفقة جديدة ({symbol.upper()})** 🚨\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **الاتجاه:** {action} {emoji}\n"
        f"🎯 **قوة الإشارة:** {signal_type}\n"
        f"💵 **سعر الدخول:** {round(current_price, 4)}\n\n"
        f"🛑 **وقف الخسارة (SL):** {sl}\n"
        f"🎯 **الهدف الأول (TP1):** {tp1}\n"
        f"🎯 **الهدف الثاني (TP2):** {tp2}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📐 **التحليل الفني:**\n"
        f"• **مؤشر RSI:** {rsi}\n"
        f"• **متوسط EMA9:** {ema9}\n"
        f"• **متوسط EMA21:** {ema21}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ **ملاحظة:** يرجى الالتزام بإدارة المخاطر."
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
                    send_message(chat_id, "أهلاً بك! أرسل الأمر بالطريقة التالية للتحليل:\n`/analyze GC=F`\nأو\n`/analyze EURUSD=X`")
                elif text.startswith("/analyze"):
                    parts = text.split()
                    symbol = parts[1] if len(parts) > 1 else "GC=F"
                    send_message(chat_id, f"🔍 جاري تحليل {symbol}...")
                    analysis = analyze_symbol(symbol)
                    send_message(chat_id, analysis)
    except Exception as e:
        pass
    return offset

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is active")

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
