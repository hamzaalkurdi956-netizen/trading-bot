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
        self.wfile.write(b"Real Chart ICT/SMC Bot is running!")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- 2. الإعدادات ومتغيرات البيئة ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- 3. جلب بيانات الشمعات الحقيقية للسوق (OHLCV) ---
def fetch_real_candles() -> dict:
    try:
        # جلب آخر 20 شمعة 15 دقيقة لـ PAXG/USDT (الذهب المباشر)
        url = "https://api.binance.com/api/v3/klines?symbol=PAXGUSDT&interval=15m&limit=20"
        res = requests.get(url, timeout=6).json()
        
        candles_summary = []
        for c in res:
            # open, high, low, close
            candles_summary.append(f"High: {c[2]}, Low: {c[3]}, Close: {c[4]}")
        
        current_price = float(res[-1][4])
        return {
            "price": current_price,
            "candles_text": "\n".join(candles_summary[-10:]), # أحدث 10 شمعات
            "symbol": "XAU/USD"
        }
    except Exception as e:
        print(f"Error Fetching Candles: {e}")
        return {
            "price": 4186.60,
            "candles_text": "Price: 4186.60 (Flat Movement)",
            "symbol": "XAU/USD"
        }

# --- 4. محرك التحليل الحقيقي المستند لشمعات الشارت ---
def get_professional_ict_analysis(symbol: str, price: float, candles_text: str) -> str:
    if not GEMINI_API_KEY:
        return "⚠️ خطأ: GEMINI_API_KEY غير مضاف في البيئة."

    prompt_text = f"""
أنت خبير تداول محترف في أسواق المال وتعتمد حصراً على **Smart Money Concepts (SMC)** و **ICT (Inner Circle Trader)**.

بيانات الشمعات الحقيقية المباشرة للذهب ({symbol}) لأحدث شمعات 15 دقيقة هي:
{candles_text}

السعر الحالي المباشر: {price}

قم بتحليل بيانات هذه الشمعات الحقيقية بدقة:
1. تحقق مما إذا كان هناك كسر هيكل (BOS) أو تغيير في السلوك (ChoCh) في أحدث الشمعات.
2. ابحث عن وجود FVG (Fair Value Gap) أو Order Block واضح.
3. **تنبيه حاسم:** إذا كانت الشمعات متداخلة وبدون اتجاه واضح، ضع الاتجاه **WAIT** (لا تدخل).
4. إذا وجد إعداد دخول موثوق، حدد (BUY أو SELL) وقم بحساب TP1, TP2, SL بناءً على القمم والقيعان الحقيقية الموضحة في الشمعات أعلاه.

تنسيق الإجابة:
🎯 **تحليل الشمعات الحقيقي (ICT / SMC)**
────────────────
📌 **الاتجاه الفني:** [BUY / SELL / WAIT]
💵 **السعر الحالي المباشر:** {price}

🎯 **TP1:** [السعر]
🎯 **TP2:** [السعر]
🛑 **SL:** [السعر]

💡 **قراءة الشارت المباشر:**
[اشرح بناءً على أسعار الشمعات المرفقة أعلاه أين تقع السيولة ولماذا اخترت هذا القرار]
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        res_data = response.json()
        if response.status_code == 200 and "candidates" in res_data:
            return res_data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Gemini API Error: {e}")

    return "⚠️ تعذر تحليل الشمعات حالياً، يرجى إعادة المحاولة."

# --- 5. أوامر بوت التلغرام ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 أهلاً بك! البوت الآن يحلل حركة الشمعات الحقيقية للشارت. أرسل `/analyze GOLD`.", parse_mode="Markdown")

async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0] if context.args else "GOLD"
        msg = await update.message.reply_text(f"📊 جاري قراءة شمعات الـ 15 دقيقة وفحص هيكل السوق لـ {symbol}...")
        
        data = fetch_real_candles()
        analysis_result = get_professional_ict_analysis(data["symbol"], data["price"], data["candles_text"])
        
        await msg.edit_text(analysis_result, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ أثناء فحص السوق: {e}")

# --- 6. التشغيل الرئيسي ---
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("CRITICAL: TELEGRAM_BOT_TOKEN is missing!")
        exit(1)

    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("analyze", analyze_cmd))

    print("Real-Candle ICT Bot is running...")
    app.run_polling(drop_pending_updates=True)
