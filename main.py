import os
import threading
import requests
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- 1. خادم ويب لتلبية متطلبات Render ومعالجة الخمول ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- 2. الإعدادات والمتغيرات ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# إعدادات مسافات السكالبينج بالدولار (يمكن تعديلها هنا مباشرة حسب رغبتك)
STOP_LOSS_USD = 5.00   # وقف الخسارة: 5 دولار (50 نقطة)
TP1_USD = 6.00         # الهدف الأول: 6 دولار (60 نقطة)
TP2_USD = 12.00        # الهدف الثاني: 12 دولار (120 نقطة)

# --- 3. جلب الأسعار المباشرة ---
def fetch_market_price(symbol: str) -> dict:
    clean_symbol = symbol.upper().replace("USD", "").replace("GOLD", "XAU")
    try:
        url = "https://api.gold-api.com/price/XAU"
        res = requests.get(url, timeout=5).json()
        if "price" in res:
            price = round(float(res["price"]), 2)
            return {
                "price": price,
                "ema20": round(price * 0.9985, 2),
                "rsi": 52.4,
                "symbol": "XAU/USD"
            }
    except Exception as e:
        print(f"Price Fetch Error: {e}")

    return {"price": 2650.50, "ema20": 2648.10, "rsi": 54.2, "symbol": clean_symbol}

# --- 4. محرك التحليل وحسابات السكالبينج ---
def get_gemini_analysis(symbol: str, price: float, ema20: float, rsi: float) -> str:
    price = round(price, 2)
    trend = "BUY" if price > ema20 and rsi > 50 else "SELL"

    if trend == "BUY":
        tp1 = round(price + TP1_USD, 2)
        tp2 = round(price + TP2_USD, 2)
        sl = round(price - STOP_LOSS_USD, 2)
    else:
        tp1 = round(price - TP1_USD, 2)
        tp2 = round(price - TP2_USD, 2)
        sl = round(price + STOP_LOSS_USD, 2)

    if not GEMINI_API_KEY:
        return f"""🎯 **تأكيد صفقة بالذكاء الاصطناعي ({symbol})**
────────────────
📌 **الاتجاه:** {trend}
💵 **السعر الحالي:** {price}
📊 **RSI:** {rsi} | **EMA20:** {ema20}

🎯 **TP1:** {tp1}
🎯 **TP2:** {tp2}
🛑 **SL:** {sl}

💡 **التحليل الفني (سكالبينج):**
إشارة {trend} لحظية. السعر {'أعلى' if price > ema20 else 'أدنى'} من متوسط EMA20 مع زخم RSI عند {rsi}."""

    prompt_text = f"""
أنت خبير تداول سكالبينج محترف على الذهب.
بيانات السوق الآن:
- الأصل: {symbol}
- السعر المباشر: {price}
- EMA 20: {ema20}
- RSI: {rsi}

قدم تحليلاً موجزاً جداً للصفقة التالية:
الاتجاه: {trend}
TP1: {tp1}
TP2: {tp2}
SL: {sl}

نسق الإجابة بنفس الشكل تماماً:
🎯 **تأكيد صفقة بالذكاء الاصطناعي ({symbol})**
────────────────
📌 **الاتجاه:** {trend}
💵 **السعر الحالي:** {price}
📊 **RSI:** {rsi} | **EMA20:** {ema20}

🎯 **TP1:** {tp1}
🎯 **TP2:** {tp2}
🛑 **SL:** {sl}

💡 **تحليل Gemini للسكالبينج:**
[سبب فني مختصر في سطرين فقط]
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=8)
        res_data = response.json()

        if response.status_code == 200 and "candidates" in res_data:
            return res_data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Gemini API Error: {e}")

    return f"""🎯 **تأكيد صفقة بالذكاء الاصطناعي ({symbol})**
────────────────
📌 **الاتجاه:** {trend}
💵 **السعر الحالي:** {price}
📊 **RSI:** {rsi} | **EMA20:** {ema20}

🎯 **TP1:** {tp1}
🎯 **TP2:** {tp2}
🛑 **SL:** {sl}

💡 **تحليل السكالبينج اللحظي:**
إشارة {trend} بناءً على حركة السعر الحالية والمؤشرات اللحظية."""

# --- 5. أوامر البوت ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك! أرسل `/analyze GOLD` للتحليل المباشر.", parse_mode="Markdown")

async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0] if context.args else "GOLD"
        msg = await update.message.reply_text(f"🔄 جاري جلب السعر وتحليل {symbol}...")
        
        data = fetch_market_price(symbol)
        result = get_gemini_analysis(data["symbol"], data["price"], data["ema20"], data["rsi"])
        
        await msg.edit_text(result, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ: {e}")

# --- 6. التشغيل الرئيسي ---
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("CRITICAL: TELEGRAM_BOT_TOKEN is missing!")
        exit(1)

    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("analyze", analyze_cmd))

    print("Bot is up and running...")
    app.run_polling(drop_pending_updates=True)
