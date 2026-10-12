import os
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- 1. خادم الويب (Keep-Alive) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Smart ICT/SMC Bot is running!")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- 2. الإعدادات ومتغيرات البيئة ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- 3. جلب بيانات السوق والشمعات الحقيقية ---
def fetch_market_data() -> dict:
    price = 4180.00
    candles_text = "Market Structure Data"
    
    try:
        price_res = requests.get("https://api.gold-api.com/price/XAU", timeout=4).json()
        if "price" in price_res and price_res["price"] > 0:
            price = round(float(price_res["price"]), 2)
    except Exception as e:
        print(f"Price API Error: {e}")

    try:
        url = "https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=15m&limit=20"
        res = requests.get(url, timeout=5).json()
        if isinstance(res, list) and len(res) > 0:
            candles_summary = [f"High: {c[2]}, Low: {c[3]}, Close: {c[4]}" for c in res]
            candles_text = "\n".join(candles_summary[-12:])
    except Exception as e:
        print(f"Candles API Warning: {e}")

    return {
        "price": price,
        "candles_text": candles_text,
        "symbol": "XAU/USD"
    }

# --- 4. محرك التحليل الذكي (BUY / SELL / WAIT) ---
def get_professional_ict_analysis(symbol: str, price: float, candles_text: str) -> str:
    # تحليل احتياطي ذكي في حال تأخر الاستجابة
    fallback_analysis = f"""🎯 **تحليل صفقات المؤسسات الذكية ({symbol})**
────────────────
📌 **الاتجاه الفني:** WAIT
💵 **السعر الحالي المباشر:** {price}

🎯 **TP1:** N/A
🎯 **TP2:** N/A
🛑 **SL:** N/A

💡 **فلسفة التحليل (ICT / SMC):**
السوق يمر بمرحلة تذبذب عرضي أو عدم وضوح في الهيكل حالياً، يفضل الانتظار لحين كسر مناطق السيولة أو تشكل Order Block حقيقي."""

    if not GEMINI_API_KEY:
        return fallback_analysis

    prompt_text = f"""
أنت متداول مؤسسات محترف تطبق بدقة مفاهيم **ICT (Inner Circle Trader)** و **SMC (Smart Money Concepts)**.
ممنوع إعطاء صفقات عشوائية بتاتاً. يجب أن تكون الدقة عالية جداً.

بيانات الذهب المباشرة ({symbol}):
- السعر الحالي: {price}
- حركة الشمعات الأخيرة:
{candles_text}

المطلوب بدقة:
1. ادرس بنية السوق والسيولة ومناطق الـ (Order Blocks / FVG).
2. إذا كانت الفرصة **صاعدة قوية**، حدد الاتجاه **BUY** مع حساب TP1، TP2، SL بدقة.
3. إذا كانت الفرصة **هابطة واضحة**، حدد الاتجاه **SELL** مع حساب TP1، TP2، SL بدقة.
4. إذا لم يكن هناك إعداد دخول مثالي وآمن، **يجب اختيار الاتجاه WAIT** (حالة انتظار وعدم دخول أي صفقة لحماية رأس المال).

التنسيق الإجباري للإجابة:
🎯 **تحليل صفقات المؤسسات الذكية ({symbol})**
────────────────
📌 **الاتجاه الفني:** [BUY / SELL / WAIT]
💵 **السعر الحالي المباشر:** {price}

🎯 **TP1:** [السعر أو N/A]
🎯 **TP2:** [السعر أو N/A]
🛑 **SL:** [السعر أو N/A]

💡 **فلسفة التحليل (ICT / SMC):**
[تحليل فني دقيق ومختصر يوضح سبب اختيار BUY أو SELL أو لماذا تم اختيار WAIT]
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=12)
        res_data = response.json()
        if response.status_code == 200 and "candidates" in res_data:
            return res_data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Gemini API Error: {e}")

    return fallback_analysis

# --- 5. أوامر بوت التلغرام ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧠 **أهلاً بك في بوت التحليل الاحترافي (ICT & SMC)**\n\nالبوت يحلل السوق بالكامل ويعطيك صفقات BUY أو SELL أو يوجهك لـ WAIT عند غياب الفرص الآمنة.\n\nأرسل الأمر للتحليل الفوري:\n`/analyze GOLD`", parse_mode="Markdown")

async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0] if context.args else "GOLD"
        msg = await update.message.reply_text(f"🔍 جاري فحص بنية السوق والسيولة لـ {symbol}...")
        
        data = fetch_market_data()
        analysis_result = get_professional_ict_analysis(data["symbol"], data["price"], data["candles_text"])
        
        await msg.edit_text(analysis_result, parse_mode="Markdown")
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

    print("Smart ICT Bot is running...")
    app.run_polling(drop_pending_updates=True)
