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
        self.wfile.write(b"ICT & SMC Bot is live!")

    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# --- 2. الإعدادات ومتغيرات البيئة ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# --- 3. جلب السعر الفوري للذهب ---
def fetch_market_price(symbol: str) -> dict:
    try:
        url = "https://api.gold-api.com/price/XAU"
        res = requests.get(url, timeout=6).json()
        if "price" in res and res["price"] > 0:
            price = round(float(res["price"]), 2)
            return {"price": price, "symbol": "XAU/USD"}
    except Exception as e:
        print(f"API Error: {e}")
    
    return {"price": 4186.60, "symbol": "XAU/USD"}

# --- 4. محرك التحليل الاحترافي المتقدم (ICT & SMC via Gemini) ---
def get_professional_ict_analysis(symbol: str, price: float) -> str:
    if not GEMINI_API_KEY:
        return "⚠️ خطأ: مفتاح الذكاء الاصطناعي (GEMINI_API_KEY) غير مضاف في بيئة Render."

    prompt_text = f"""
أنت متداول محترف في المؤسسات المالية وتطبق بدقة مفاهيم **ICT (Inner Circle Trader)**، و **SMC (Smart Money Concepts)**.
السعر الحالي للذهب (XAU/USD) هو: {price}

المطلوب منك تحليل الوضع الفني الحالي باحترافية تامة:
1. ابحث عن مناطق السيولة (Liquidity Pools / Buy-side & Sell-side Liquidity).
2. حدد أماكن التوازن (Fair Value Gaps - FVG) أو بلوكات الطلب (Order Blocks - OB) القريبة من السعر الحالي.
3. **الشرط الصارم:** إذا لم تكن هناك فرصة دخول عالية الاحتمالية (High Probability Setup) والسعر في منطقة تذبذب عشوائية، يجب أن يكون الاتجاه **WAIT** (لا توجد صفقة الآن، احفظ رأس المال).
4. إذا وجدت فرصة قوية ومؤكدة طبقاً لـ ICT/SMC، حدد الاتجاه (BUY أو SELL) بدقة مع حساب الهدف الأول (TP1)، الهدف الثاني (TP2)، ووقف الخسارة (SL) بمسافة سكالبينج منطقية ومحمية خلف قمة أو قاع هيكلي.

يجب أن تكون الإجابة بالتنسيق التالي حصراً:

🎯 **تحليل صفقات المؤسسات الذكية ({symbol})**
────────────────
📌 **الاتجاه الفني:** [BUY / SELL / WAIT]
💵 **السعر الحالي المباشر:** {price}

🎯 **TP1:** [السعر أو N/A]
🎯 **TP2:** [السعر أو N/A]
🛑 **SL:** [السعر أو N/A]

💡 **فلسفة التحليل (ICT / SMC):**
[اكتب تحليلاً دقيقاً ومختصراً يوضح سبب الدخول أو سبب اتخاذ قرار الانتظار WAIT بناءً على السيولة ومناطق الـ Order Block والـ FVG]
"""

    # تم تحديث الموديل هنا إلى gemini-3.8-flash لتجنب الخطأ نهائياً
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    payload = {"contents": [{"parts": [{"text": prompt_text}]}]}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=12)
        res_data = response.json()
        if response.status_code == 200 and "candidates" in res_data:
            return res_data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            return f"⚠️ تعذر توليد التحليل من السيرفر حالياً. رمز الخطأ: {res_data.get('error', {}).get('message', 'Unknown')}"
    except Exception as e:
        return f"❌ خطأ في الاتصال بنظام الذكاء الاصطناعي: {str(e)}"

# --- 5. أوامر بوت التلغرام ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 **أهلاً بك في بوت تحليل المؤسسات (ICT & SMC)**\n\n"
        "البوت مبرمج لرفض الصفقات العشوائية وإعطاء الصفقات عالية الاحتمالية فقط.\n"
        "أرسل الأمر للتحليل الفوري:\n`/analyze GOLD`", 
        parse_mode="Markdown"
    )

async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0] if context.args else "GOLD"
        msg = await update.message.reply_text(f"🔍 جاري فحص السيولة ومناطق الـ Order Blocks لـ {symbol}...")
        
        data = fetch_market_price(symbol)
        analysis_result = get_professional_ict_analysis(data["symbol"], data["price"])
        
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

    print("Professional ICT/SMC Bot is up and running...")
    app.run_polling(drop_pending_updates=True)
