import json
import time
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

TOKEN = "8918068542:AAHxgD83YEV3HZgRUjNyw1XRSE7iaOUS1_0"
BASE_URL = f"https://api.telegram.org/bot{TOKEN}/"

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    server = HTTPServer(('0.0.0.0', 10000), HealthCheckHandler)
    server.serve_forever()

def send_message(chat_id, text):
    url = BASE_URL + "sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            pass
    except Exception as e:
        print("Error sending message:", e)

def get_price_data(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1mo&interval=1h"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            closes = data['chart']['result'][0]['indicators']['quote'][0]['close']
            highs = data['chart']['result'][0]['indicators']['quote'][0]['high']
            lows = data['chart']['result'][0]['indicators']['quote'][0]['low']
            closes = [c for c in closes if c is not None]
            highs = [h for h in highs if h is not None]
            lows = [l for l in lows if l is not None]
            return closes, highs, lows
    except Exception as e:
        return None, None, None

def calculate_ema(prices, period):
    k = 2 / (period + 1)
    ema = [prices[0]]
    for price in prices[1:]:
        ema.append((price * k) + (ema[-1] * (1 - k)))
    return ema[-1]

def calculate_rsi(prices, period=14):
    gains, losses = [], []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i-1]
        gains.append(change if change > 0 else 0)
        losses.append(abs(change) if change < 0 else 0)
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def analyze_market(symbol):
    closes, highs, lows = get_price_data(symbol)
    if not closes or len(closes) < 30:
        return None

    current_price = closes[-1]
    ema9 = calculate_ema(closes, 9)
    ema21 = calculate_ema(closes, 21)
    rsi = calculate_rsi(closes)
    atr = (max(highs[-14:]) - min(lows[-14:])) / 14

    buy_score = 0
    sell_score = 0

    if ema9 > ema21: buy_score += 1
    else: sell_score += 1

    if rsi < 35: buy_score += 1
    elif rsi > 65: sell_score += 1

    if buy_score >= 1 and buy_score >= sell_score:
        direction = "شراء (BUY) 🟩"
        confidence = "قوية 🔥" if buy_score == 2 else "متوسطة ⚡"
        sl = current_price - (1.5 * atr)
        tp1 = current_price + (1.5 * atr)
        tp2 = current_price + (3.0 * atr)
    else:
        direction = "بيع (SELL) 🟥"
        confidence = "قوية 🔥" if sell_score == 2 else "متوسطة ⚡"
        sl = current_price + (1.5 * atr)
        tp1 = current_price - (1.5 * atr)
        tp2 = current_price - (3.0 * atr)

    return {
        "symbol": symbol,
        "price": current_price,
        "direction": direction,
        "confidence": confidence,
        "rsi": round(rsi, 2),
        "ema9": round(ema9, 4),
        "ema21": round(ema21, 4),
        "sl": round(sl, 4),
        "tp1": round(tp1, 4),
        "tp2": round(tp2, 4)
    }

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()
    print("✅ البوت يعمل الآن...")
    last_update_id = 0
    while True:
        try:
            url = BASE_URL + f"getUpdates?offset={last_update_id + 1}&timeout=30"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as resp:
                res = json.loads(resp.read().decode())
                for result in res.get("result", []):
                    last_update_id = result["update_id"]
                    message = result.get("message", {})
                    text = message.get("text", "")
                    chat_id = message.get("chat", {}).get("id")

                    if text.startswith("/analyze") and chat_id:
                        parts = text.split()
                        if len(parts) < 2:
                            send_message(chat_id, "⚠️ يرجى إدخال رمز الزوج.\nأمثلة:\n`/analyze GC=F` (الذهب)\n`/analyze EURUSD=X` (اليورو دولار)")
                        else:
                            symbol = parts[1].upper()
                            send_message(chat_id, f"🔍 جاري تحليل {symbol}...")
                            data = analyze_market(symbol)
                            if not data:
                                send_message(chat_id, "❌ متعذر جلب بيانات هذا الرمز.")
                            else:
                                report = (
                                    f"🚨 **توصية صفقة جديدة ({data['symbol']})** 🚨\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"📌 **الاتجاه:** {data['direction']}\n"
                                    f"🎯 **قوة الإشارة:** {data['confidence']}\n"
                                    f"💵 **سعر الدخول:** `{data['price']:.4f}`\n\n"
                                    f"🛑 **وقف الخسارة (SL):** `{data['sl']}`\n"
                                    f"🎯 **الهدف الأول (TP1):** `{data['tp1']}`\n"
                                    f"🎯 **الهدف الثاني (TP2):** `{data['tp2']}`\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"📐 **التحليل الفني:**\n"
                                    f"• مؤشر RSI: `{data['rsi']}`\n"
                                    f"• متوسط EMA9: `{data['ema9']}`\n"
                                    f"• متوسط EMA21: `{data['ema21']}`\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"⚠️ *ملاحظة:* يرجى الالتزام بإدارة المخاطر."
                                )
                                send_message(chat_id, report)
        except Exception as e:
            time.sleep(2)

if __name__ == "__main__":
    main()
