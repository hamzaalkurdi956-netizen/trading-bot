import os
import json
import re
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# =====================================================
# (section)
# =====================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Smart ICT/SMC Bot is running!")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthCheckHandler).serve_forever()


# =====================================================
# (section)
# =====================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")        # اختياري: مراجع ثانٍ
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # اختياري: تأكيد Claude
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

VERSION = "v4-ema-bias"
MIN_RR = 1.5          # أقل نسبة ربح/خسارة مقبولة للصفقة
MAX_RISK_ATR = 3.0    # أقصى حجم للستوب بوحدات ATR


# =====================================================
# (section)
# =====================================================
def _binance_klines(interval: str, limit: int = 150):
    for host in ("https://api.binance.com", "https://api.binance.us"):
        try:
            r = requests.get(
                f"{host}/api/v3/klines",
                params={"symbol": "PAXGUSDT", "interval": interval, "limit": limit},
                timeout=6,
            ).json()
            if isinstance(r, list) and len(r) > 30:
                return [
                    {"o": float(c[1]), "h": float(c[2]), "l": float(c[3]), "c": float(c[4])}
                    for c in r[:-1]  # حذف الشمعة غير المغلقة
                ]
        except Exception as e:
            print(f"Binance {host} {interval} error: {e}")
    return None


def _yahoo_klines(interval: str, rng: str):
    try:
        r = requests.get(
            "https://query1.finance.yahoo.com/v8/finance/chart/GC=F",
            params={"interval": interval, "range": rng},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        ).json()
        q = r["chart"]["result"][0]["indicators"]["quote"][0]
        out = []
        for o, h, l, c in zip(q["open"], q["high"], q["low"], q["close"]):
            if None not in (o, h, l, c):
                out.append({"o": o, "h": h, "l": l, "c": c})
        return out[:-1] if len(out) > 30 else None
    except Exception as e:
        print(f"Yahoo {interval} error: {e}")
        return None


def fetch_market_data():
    ltf = _binance_klines("15m") or _yahoo_klines("15m", "5d")
    htf = _binance_klines("1h") or _yahoo_klines("60m", "1mo")
    if not ltf or not htf:
        return None

    price = ltf[-1]["c"]
    try:
        p = requests.get("https://api.gold-api.com/price/XAU", timeout=4).json()
        live = float(p.get("price", 0))
        # (section)
        if live > 0 and abs(live - price) / price < 0.015:
            price = round(live, 2)
    except Exception as e:
        print(f"Price API error: {e}")

    return {"symbol": "XAU/USD", "price": price, "ltf": ltf, "htf": htf}


# =====================================================
# (section)
# =====================================================
def atr(candles, n=14):
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c["h"] - c["l"], abs(c["h"] - p["c"]), abs(c["l"] - p["c"])))
    return sum(trs[-n:]) / n


def ema(values, n):
    k = 2 / (n + 1)
    e = values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def swings(candles, k=2):
    """قمم وقيعان محورية (Fractals). تُرجع قوائم (index, price)."""
    highs, lows = [], []
    for i in range(k, len(candles) - k):
        w = candles[i - k : i + k + 1]
        if candles[i]["h"] == max(x["h"] for x in w):
            highs.append((i, candles[i]["h"]))
        if candles[i]["l"] == min(x["l"] for x in w):
            lows.append((i, candles[i]["l"]))
    return highs, lows


def structure(candles):
    """BULL / BEAR / RANGE حسب HH-HL أو LH-LL."""
    highs, lows = swings(candles)
    if len(highs) < 2 or len(lows) < 2:
        return "RANGE", highs, lows
    hh = highs[-1][1] > highs[-2][1]
    hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]
    ll = lows[-1][1] < lows[-2][1]
    if hh and hl:
        return "BULL", highs, lows
    if lh and ll:
        return "BEAR", highs, lows
    return "RANGE", highs, lows


def find_fvgs(candles, lookback=40):
    """فجوات القيمة العادلة غير المملوءة."""
    res = []
    start = max(2, len(candles) - lookback)
    for i in range(start, len(candles)):
        a, c = candles[i - 2], candles[i]
        after = candles[i + 1 :]
        if c["l"] > a["h"]:  # صاعدة
            lo, hi = a["h"], c["l"]
            if not any(x["l"] <= lo for x in after):
                res.append({"type": "BUY", "low": lo, "high": hi, "kind": "FVG"})
        if c["h"] < a["l"]:  # هابطة
            lo, hi = c["h"], a["l"]
            if not any(x["h"] >= hi for x in after):
                res.append({"type": "SELL", "low": lo, "high": hi, "kind": "FVG"})
    return res


def find_order_blocks(candles, a, lookback=40):
    """آخر شمعة معاكسة قبل اندفاع قوي، غير مخترقة."""
    res = []
    start = max(1, len(candles) - lookback)
    for i in range(start, len(candles) - 1):
        c, nx = candles[i], candles[i + 1]
        after = candles[i + 2 :]
        if c["c"] < c["o"] and nx["c"] > c["h"] and (nx["c"] - nx["o"]) > a:
            if not any(x["c"] < c["l"] for x in after):
                res.append({"type": "BUY", "low": c["l"], "high": c["h"], "kind": "OB"})
        if c["c"] > c["o"] and nx["c"] < c["l"] and (nx["o"] - nx["c"]) > a:
            if not any(x["c"] > c["h"] for x in after):
                res.append({"type": "SELL", "low": c["l"], "high": c["h"], "kind": "OB"})
    return res


# =====================================================
# (section)
# =====================================================
def watch_levels(data: dict) -> list:
    """When the answer is WAIT: nearest unfilled buy zone below price and sell zone above price."""
    ltf, price = data["ltf"], data["price"]
    a = atr(ltf)
    zones = find_fvgs(ltf, 80) + find_order_blocks(ltf, a, 80)
    buys = [z for z in zones if z["type"] == "BUY" and z["high"] <= price]
    sells = [z for z in zones if z["type"] == "SELL" and z["low"] >= price]
    out = []
    if buys:
        z = max(buys, key=lambda x: x["high"])
        out.append(f'👀 BUY عند: {z["kind"]} {round(z["low"], 2)}-{round(z["high"], 2)}')
    if sells:
        z = min(sells, key=lambda x: x["low"])
        out.append(f'👀 SELL عند: {z["kind"]} {round(z["low"], 2)}-{round(z["high"], 2)}')
    return out


# =====================================================
def build_signal(data: dict) -> dict:
    ltf, htf, price = data["ltf"], data["htf"], data["price"]
    a = atr(ltf)

    htf_struct, _, _ = structure(htf)
    ema50 = ema([c["c"] for c in htf][-100:], 50)
    ltf_struct, highs, lows = structure(ltf)

    facts = {
        "htf_structure": htf_struct,
        "ltf_structure": ltf_struct,
        "htf_ema50": round(ema50, 2),
        "atr15m": round(a, 2),
    }

    if not highs or not lows:
        return {"dir": "WAIT", "why": "No swings found to define structure.", "facts": facts}

    swing_high, swing_low = highs[-1][1], lows[-1][1]
    rng = max(abs(swing_high - swing_low), 0.0001)
    pos = (price - swing_low) / rng  # 0 = range low, 1 = range high
    facts.update({"swing_high": swing_high, "swing_low": swing_low,
                  "zone": "Discount" if pos < 0.5 else "Premium"})

    if htf_struct == "BULL":
        bias = "BUY"
    elif htf_struct == "BEAR":
        bias = "SELL"
    elif price > ema50 and ltf_struct == "BULL":
        bias = "BUY"
    elif price < ema50 and ltf_struct == "BEAR":
        bias = "SELL"
    elif price < ema50 - 1.0 * atr(htf):
        bias = "SELL"   # السعر تحت EMA50 بمسافة واضحة على الساعة
    elif price > ema50 + 1.0 * atr(htf):
        bias = "BUY"
    else:
        return {"dir": "WAIT", "why": "الاتجاه العام غير واضح (الساعة متذبذبة ولا يوجد تأكيد من EMA50 وهيكل 15m).", "facts": facts}

    if (bias == "BUY" and pos > 0.65) or (bias == "SELL" and pos < 0.35):
        return {"dir": "WAIT", "why": f"الاتجاه {bias} لكن السعر بعيد عن منطقة الدخول المناسبة (انتظر تصحيحاً نحو OB/FVG).", "facts": facts}

    zones = [z for z in find_fvgs(ltf, 60) + find_order_blocks(ltf, a, 60) if z["type"] == bias]

    def dist(z):
        if z["low"] <= price <= z["high"]:
            return 0
        return (z["low"] - price) if price < z["low"] else (price - z["high"])

    near = [z for z in zones if dist(z) <= 3 * a]
    if not near:
        return {"dir": "WAIT", "why": f"الاتجاه {bias} لكن لا توجد منطقة OB/FVG غير مملوءة قريبة من السعر.", "facts": facts}

    z = min(near, key=dist)
    facts["entry_zone"] = f'{z["kind"]} {round(z["low"], 2)}-{round(z["high"], 2)}'

    if dist(z) == 0:
        entry, mode = price, "دخول فوري (السعر داخل المنطقة)"
    else:
        entry = z["high"] if bias == "BUY" else z["low"]
        mode = "أمر معلّق Limit عند حافة المنطقة"

    if bias == "BUY":
        sl = min(z["low"], swing_low if swing_low < entry else z["low"]) - 0.3 * a
        risk = entry - sl
        tp1, tp2 = entry + 1.5 * risk, entry + 3 * risk
    else:
        sl = max(z["high"], swing_high if swing_high > entry else z["high"]) + 0.3 * a
        risk = sl - entry
        tp1, tp2 = entry - 1.5 * risk, entry - 3 * risk

    if risk <= 0 or risk > MAX_RISK_ATR * a:
        return {"dir": "WAIT", "why": "الستوب بعيد جداً مقارنة بالتذبذب؛ المخاطرة غير مناسبة.", "facts": facts}

    return {
        "dir": bias,
        "entry": round(entry, 2),
        "mode": mode,
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr": round(abs(tp1 - entry) / risk, 2),
        "why": f"اتجاه {htf_struct} على 1H، هيكل 15m: {ltf_struct}، منطقة الدخول {facts['entry_zone']}.",
        "facts": facts,
    }


# =====================================================
# (section)
# =====================================================
def _review_prompt(data, sig):
    recent = "\n".join(
        f'O:{c["o"]:.2f} H:{c["h"]:.2f} L:{c["l"]:.2f} C:{c["c"]:.2f}' for c in data["ltf"][-30:]
    )
    return f"""You are a strict ICT/SMC risk reviewer for XAU/USD.
A rule-based engine proposes this trade:
{json.dumps({k: v for k, v in sig.items() if k != "why"}, ensure_ascii=False)}

Last 30 closed 15m candles (oldest -> newest):
{recent}

Check: is the direction consistent with structure and liquidity? Is SL placed beyond a logical invalidation point? Is the entry chasing price?
Reply ONLY with JSON: {{"agree": true|false, "reason": "<one short Arabic sentence>"}}"""


def _parse_json(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else None


def review_with_claude(data, sig):
    if not ANTHROPIC_API_KEY:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 300,
                "messages": [{"role": "user", "content": _review_prompt(data, sig)}],
            },
            timeout=25,
        )
        return _parse_json(r.json()["content"][0]["text"])
    except Exception as e:
        print(f"Claude review error: {e}")
        return None


def review_with_gemini(data, sig):
    if not GEMINI_API_KEY:
        return None
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        )
        r = requests.post(
            url,
            json={"contents": [{"parts": [{"text": _review_prompt(data, sig)}]}]},
            timeout=20,
        )
        return _parse_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    except Exception as e:
        print(f"Gemini review error: {e}")
        return None


def finalize(data, sig):
    """يطبق الفيتو: أي مراجع يرفض الصفقة => WAIT."""
    notes = []
    if sig["dir"] in ("BUY", "SELL"):
        for name, fn in (("Claude", review_with_claude), ("Gemini", review_with_gemini)):
            rv = fn(data, sig)
            if rv is None:
                continue
            ok = bool(rv.get("agree"))
            notes.append(f'{"✅" if ok else "❌"} {name}: {rv.get("reason", "")}')
            if not ok:
                sig = {**sig, "dir": "WAIT", "why": f'رفضت مراجعة {name} الصفقة: {rv.get("reason", "")}'}
                break
        if not notes:
            notes.append("⚠️ لم تتم مراجعة خارجية (لا توجد مفاتيح API)")
    return sig, notes


# =====================================================
# (section)
# =====================================================
def format_message(data, sig, notes):
    f = sig["facts"]
    lines = [
        f'🎯 تحليل ICT/SMC ({data["symbol"]})',
        "────────────────",
        f'📌 الاتجاه: {sig["dir"]}',
        f'💵 السعر الحالي: {data["price"]}',
    ]
    if sig["dir"] in ("BUY", "SELL"):
        lines += [
            f'📍 الدخول: {sig["entry"]} ({sig.get("mode", "")})',
            f'🎯 TP1: {sig["tp1"]}',
            f'🎯 TP2: {sig["tp2"]}',
            f'🛑 SL: {sig["sl"]}',
            f'⚖️ R:R للهدف الأول: 1:{sig["rr"]}',
        ]
    else:
        lines += ["🎯 TP1: N/A", "🎯 TP2: N/A", "🛑 SL: N/A"]

    lines += ["", f'💡 السبب: {sig["why"]}', ""]
    lines.append(
        f'📊 1H: {f.get("htf_structure")} | 15m: {f.get("ltf_structure")} | '
        f'ATR: {f.get("atr15m")} | EMA50(1H): {f.get("htf_ema50")}'
    )
    if notes:
        lines += [""] + notes
    lines += ["", f'🔖 النسخة: {VERSION} | شموع: {len(data["ltf"])}/{len(data["htf"])}', "⚠️ تحليل آلي تعليمي وليس نصيحة مالية. التزم بإدارة رأس المال."]
    return "\n".join(lines)


# =====================================================
# (section)
# =====================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 أهلاً بك في بوت التحليل (ICT & SMC)\n\n"
        "البوت يحسب الهيكل والسيولة وOrder Blocks وFVG من شموع حقيقية، "
        "ثم يراجع الصفقة عبر Claude/Gemini قبل إرسالها.\n\n"
        "للتحليل: /analyze GOLD"
    )


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = await update.message.reply_text("🔍 جاري فحص بنية السوق والسيولة...")
        data = fetch_market_data()
        if not data:
            await msg.edit_text("❌ تعذّر جلب بيانات الشموع الآن. لن أعطي تحليلاً بدون بيانات حقيقية، حاول لاحقاً.")
            return
        sig = build_signal(data)
        sig, notes = finalize(data, sig)
        if sig["dir"] == "WAIT":
            notes = watch_levels(data) + notes
        await msg.edit_text(format_message(data, sig, notes))
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ: {e}")


# =====================================================
# (section)
# =====================================================
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("CRITICAL: TELEGRAM_BOT_TOKEN is missing!")
        raise SystemExit(1)

    threading.Thread(target=start_health_server, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("analyze", analyze_cmd))

    print("Smart ICT Bot is running...")
    app.run_polling(drop_pending_updates=True)
