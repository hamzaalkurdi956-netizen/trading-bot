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

VERSION = "v6-pro"
MIN_RR = 1.5          # أقل نسبة ربح/خسارة مقبولة للصفقة
MAX_RISK_ATR = 3.0    # أقصى حجم للستوب بوحدات ATR


# =====================================================
# (section)
# =====================================================
def _binance_klines(interval: str, limit: int = 150):
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            r = requests.get(
                f"{host}/api/v3/klines",
                params={"symbol": "PAXGUSDT", "interval": interval, "limit": limit},
                timeout=8,
            ).json()
            if isinstance(r, list) and len(r) > 30:
                return [
                    {"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
                     "l": float(c[3]), "c": float(c[4])}
                    for c in r[:-1]  # drop the unclosed candle
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
            timeout=10,
        ).json()
        res = r["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        out = []
        for t, o, h, l, c in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"]):
            if None not in (o, h, l, c):
                out.append({"t": int(t) * 1000, "o": o, "h": h, "l": l, "c": c})
        return out[:-1] if len(out) > 30 else None
    except Exception as e:
        print(f"Yahoo {interval} error: {e}")
        return None


def resample(candles, hours):
    """Aggregate 1H candles into N-hour candles (keeps only (almost) complete buckets)."""
    buckets, order = {}, []
    ms = hours * 3_600_000
    for i, c in enumerate(candles):
        key = c["t"] // ms if "t" in c else i // hours
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(c)
    out = []
    for k in order:
        g = buckets[k]
        if len(g) < hours - 1:
            continue
        out.append({"t": g[0].get("t"), "o": g[0]["o"], "h": max(x["h"] for x in g),
                    "l": min(x["l"] for x in g), "c": g[-1]["c"]})
    return out


def _good(candles):
    """Reject dead feeds: flat candles or almost no movement."""
    if not candles or len(candles) < 30:
        return False
    tail = candles[-40:]
    flat = sum(1 for x in tail if x["h"] == x["l"]) / len(tail)
    return flat < 0.3 and atr(candles) >= 0.15


def _from_source(name):
    if name == "binance":
        ltf = _binance_klines("15m", 150)
        h1 = _binance_klines("1h", 500)
        d1 = _binance_klines("1d", 200)
    else:
        ltf = _yahoo_klines("15m", "5d")
        h1 = _yahoo_klines("60m", "3mo")
        d1 = _yahoo_klines("1d", "1y")
    if _good(ltf) and _good(h1):
        return ltf, h1, d1 if _good(d1) else None
    print(f"Source {name} rejected (missing or flat data)")
    return None


def fetch_market_data():
    got, source = None, None
    for name in ("binance", "yahoo"):   # one consistent source for all timeframes
        got = _from_source(name)
        if got:
            source = name
            break
    if not got:
        return None
    ltf, h1, d1 = got

    htf = h1[-150:]
    h4 = resample(h1, 4)[-120:]

    price = ltf[-1]["c"]
    if source == "binance":   # PAXG tracks spot gold; futures (Yahoo) carry a premium, so keep their own close
        try:
            p = requests.get("https://api.gold-api.com/price/XAU", timeout=4).json()
            live = float(p.get("price", 0))
            if live > 0 and abs(live - price) / price < 0.005:
                price = round(live, 2)
        except Exception as e:
            print(f"Price API error: {e}")

    age_min = None
    if ltf[-1].get("t"):
        age_min = round((time.time() * 1000 - ltf[-1]["t"] - 900_000) / 60000)
    return {"symbol": "XAU/USD", "price": price, "ltf": ltf, "htf": htf,
            "source": source, "age_min": age_min,
            "h4": h4 if len(h4) >= 30 else None,
            "d1": d1 if d1 and len(d1) >= 55 else None}


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
def smc_signal(data: dict) -> dict:
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

    facts["bias"] = bias
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
SCORE_MIN = 0.35
WEIGHTS = {"ICT/SMC": 2.0, "الاتجاه EMA": 1.5, "الزخم RSI/MACD": 1.0,
           "دعم/مقاومة+فيبو": 1.0, "حركة السعر": 0.8, "بولينجر": 0.5,
           "الإطار الأكبر 4H/D": 1.5}


def clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


def ema_series(values, n):
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def rsi(closes, n=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / n
        al = (al * (n - 1) + losses[i]) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def macd_hist(closes):
    e12, e26 = ema_series(closes, 12), ema_series(closes, 26)
    macd = [a - b for a, b in zip(e12, e26)]
    sig = ema_series(macd, 9)
    return macd[-1] - sig[-1], macd[-2] - sig[-2]


def pct_b(closes, n=20, k=2):
    w = closes[-n:]
    m = sum(w) / n
    sd = (sum((x - m) ** 2 for x in w) / n) ** 0.5
    if sd == 0:
        return 0.5
    return (closes[-1] - (m - k * sd)) / (2 * k * sd)


def sr_levels(candles, k=3, keep=8):
    h, l = swings(candles, k)
    return sorted([x[1] for x in h[-keep:]] + [x[1] for x in l[-keep:]])


def vote_smc(smc):
    if smc["dir"] == "BUY":
        return 1.0, "إشارة ICT/SMC: BUY"
    if smc["dir"] == "SELL":
        return -1.0, "إشارة ICT/SMC: SELL"
    b = smc["facts"].get("bias")
    if b == "BUY":
        return 0.5, "اتجاه شراء بدون منطقة دخول"
    if b == "SELL":
        return -0.5, "اتجاه بيع بدون منطقة دخول"
    return 0.0, "لا اتجاه واضح"


def vote_trend(htf):
    c = [x["c"] for x in htf]
    e20, e50 = ema_series(c, 20), ema_series(c, 50)
    p, slope = c[-1], e50[-1] - e50[-6]
    if p > e20[-1] > e50[-1] and slope > 0:
        return 1.0, "السعر>EMA20>EMA50 والميل صاعد"
    if p < e20[-1] < e50[-1] and slope < 0:
        return -1.0, "السعر<EMA20<EMA50 والميل هابط"
    if p > e50[-1]:
        return 0.5, "السعر فوق EMA50"
    if p < e50[-1]:
        return -0.5, "السعر تحت EMA50"
    return 0.0, "-"


def vote_momentum(ltf, htf):
    c15, c1h = [x["c"] for x in ltf], [x["c"] for x in htf]
    r15, r1h = rsi(c15), rsi(c1h)
    h0, h1 = macd_hist(c1h)
    v = 0.0
    v += 0.4 if r1h > 52 else -0.4 if r1h < 48 else 0
    v += 0.3 if r15 > 52 else -0.3 if r15 < 48 else 0
    if h0 > 0 and h0 > h1:
        v += 0.3
    elif h0 < 0 and h0 < h1:
        v -= 0.3
    if r1h > 75 or r15 > 80:
        v = min(v, 0)      # تشبع شرائي: لا ملاحقة
    if r1h < 25 or r15 < 20:
        v = max(v, 0)      # تشبع بيعي
    return clamp(v), f"RSI 1H {r1h:.0f} | 15m {r15:.0f} | MACD {'↑' if h0 > h1 else '↓'}"


def vote_sr_fib(data, a15, a1h):
    price, htf = data["price"], data["htf"]
    levels = sr_levels(htf) + sr_levels(data["ltf"])
    tol = max(1.5 * a15, 0.4 * a1h)
    sup = [x for x in levels if x <= price]
    res = [x for x in levels if x > price]
    v, notes = 0.0, []
    if sup and price - max(sup) <= tol:
        v += 1
        notes.append(f"قرب دعم {max(sup):.1f}")
    if res and min(res) - price <= tol:
        v -= 1
        notes.append(f"قرب مقاومة {min(res):.1f}")
    seg = htf[-80:]
    hi_i = max(range(len(seg)), key=lambda i: seg[i]["h"])
    lo_i = min(range(len(seg)), key=lambda i: seg[i]["l"])
    hi, lo = seg[hi_i]["h"], seg[lo_i]["l"]
    if hi - lo > 2 * a1h:
        up = hi_i > lo_i
        retr = (hi - price) / (hi - lo) if up else (price - lo) / (hi - lo)
        f = 1.0 if 0.5 <= retr <= 0.786 else 0.5 if 0.382 <= retr < 0.5 else 0.0
        if f:
            v += f if up else -f
            notes.append(f"فيبو {retr:.2f}")
    return clamp(v), " | ".join(notes) or "لا مستوى قريب"


def _candle_pattern(p, c):
    body, rng = abs(c["c"] - c["o"]), c["h"] - c["l"]
    if rng <= 0:
        return 0
    lower = min(c["o"], c["c"]) - c["l"]
    upper = c["h"] - max(c["o"], c["c"])
    pbody = abs(p["c"] - p["o"])
    if p["c"] < p["o"] and c["c"] > c["o"] and c["c"] >= p["o"] and c["o"] <= p["c"] and body > pbody:
        return 1
    if p["c"] > p["o"] and c["c"] < c["o"] and c["c"] <= p["o"] and c["o"] >= p["c"] and body > pbody:
        return -1
    if lower >= 2 * max(body, 0.05 * rng) and upper <= max(body, 0.15 * rng) and (c["c"] - c["l"]) / rng > 0.6:
        return 1   # مطرقة
    if upper >= 2 * max(body, 0.05 * rng) and lower <= max(body, 0.15 * rng) and (c["h"] - c["c"]) / rng > 0.6:
        return -1  # شهاب
    return 0


def vote_price_action(ltf, htf):
    a = _candle_pattern(ltf[-2], ltf[-1])
    b = _candle_pattern(ltf[-3], ltf[-2])
    c = _candle_pattern(htf[-2], htf[-1])
    v = 0.6 * a + 0.2 * b + 0.6 * c
    names = {1: "صاعد", -1: "هابط", 0: "-"}
    return clamp(v), f"15m: {names[a]} | 1H: {names[c]}"


def vote_bollinger(ltf):
    pb = pct_b([x["c"] for x in ltf])
    v = 0.6 if pb < 0.1 else -0.6 if pb > 0.9 else 0.3 if pb < 0.25 else -0.3 if pb > 0.75 else 0.0
    return v, f"%B = {pb:.2f}"


def build_signal(data: dict) -> dict:
    ltf, htf, price = data["ltf"], data["htf"], data["price"]
    a15, a1h = atr(ltf), atr(htf)
    if a15 < 0.15 or (data.get("age_min") or 0) > 90:
        return {"dir": "WAIT", "facts": {"atr15m": round(a15, 2)}, "votes": [], "score": 0.0,
                "why": "بيانات غير موثوقة: الشموع شبه مسطحة أو قديمة (تحقق من مصدر البيانات)."}
    smc = smc_signal(data)

    results = [
        ("ICT/SMC",) + vote_smc(smc),
        ("الاتجاه EMA",) + vote_trend(htf),
        ("الزخم RSI/MACD",) + vote_momentum(ltf, htf),
        ("دعم/مقاومة+فيبو",) + vote_sr_fib(data, a15, a1h),
        ("حركة السعر",) + vote_price_action(ltf, htf),
        ("بولينجر",) + vote_bollinger(ltf),
        ("الإطار الأكبر 4H/D",) + vote_higher(data),
    ]
    total_w = sum(WEIGHTS.values())
    score = sum(WEIGHTS[n] * v for n, v, _ in results) / total_w

    facts = dict(smc["facts"])
    facts["score"] = round(score, 2)
    votes = [{"school": n, "vote": round(v, 2), "note": t} for n, v, t in results]
    base = {"facts": facts, "votes": votes, "score": round(score, 2)}

    if abs(score) < SCORE_MIN:
        return {**base, "dir": "WAIT",
                "why": f"توافق المدارس ضعيف: الدرجة {score:+.2f} (المطلوب ±{SCORE_MIN})."}

    direction = "BUY" if score > 0 else "SELL"
    sign = 1 if score > 0 else -1
    agree = sum(1 for _, v, _ in results if v * sign >= 0.3)
    if agree < 3:
        return {**base, "dir": "WAIT", "why": f"اتفقت {agree} مدارس فقط من {len(results)} (المطلوب 3 على الأقل)."}
    if results[0][1] * sign < 0 or results[1][1] * sign < 0:
        return {**base, "dir": "WAIT", "why": "تعارض مع ICT/SMC أو مع الاتجاه العام (EMA)."}

    if results[-1][1] * sign <= -0.7:
        return {**base, "dir": "WAIT", "why": "الصفقة تعاكس اتجاه الإطار الأكبر (4H/اليومي)."}

    # ---- المستويات ----
    if smc["dir"] == direction:
        entry, sl, tp1, tp2 = smc["entry"], smc["sl"], smc["tp1"], smc["tp2"]
        mode = smc.get("mode", "")
        risk = abs(entry - sl)
    else:
        mode = "دخول فوري بالسعر الحالي (توافق مدارس)"
        entry = price
        h, l = swings(ltf)
        if direction == "BUY":
            cand = [x[1] for x in l if x[1] < price]
            sl = (cand[-1] if cand else price) - 0.3 * a15
            risk = price - sl
        else:
            cand = [x[1] for x in h if x[1] > price]
            sl = (cand[-1] if cand else price) + 0.3 * a15
            risk = sl - price
        if risk < 1.0 * a15:
            risk = 1.0 * a15
            sl = price - risk if direction == "BUY" else price + risk
        if risk > 3.5 * a15:
            return {**base, "dir": "WAIT", "why": "الستوب بعيد جداً مقارنة بالتذبذب؛ المخاطرة غير مناسبة."}
        tp1 = price + sign * 1.5 * risk
        tp2 = price + sign * 3.0 * risk
        # هل يوجد حاجز (دعم/مقاومة على الساعة) يقطع الطريق قبل 1R؟
        lv = sr_levels(htf)
        ahead = [x for x in lv if (x - price) * sign > 0]
        if ahead:
            nearest = min(ahead, key=lambda x: abs(x - price))
            if abs(nearest - price) < 1.0 * risk:
                return {**base, "dir": "WAIT",
                        "why": f"حاجز قريب عند {nearest:.2f} يحدّ من الربح قبل 1R."}

    return {
        **base,
        "dir": direction,
        "entry": round(entry, 2),
        "mode": mode,
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr": round(abs(tp1 - entry) / max(risk, 1e-9), 2),
        "why": f"توافق {agree} مدارس من {len(results)}، الدرجة {score:+.2f}.",
    }


# =====================================================
# (section)
# =====================================================
import time
import datetime as _dt

DATA_DIR = os.environ.get("DATA_DIR", ".")
JOURNAL_FILE = os.path.join(DATA_DIR, "journal.json")
SUBS_FILE = os.path.join(DATA_DIR, "subs.json")
SCAN_MINUTES = int(os.environ.get("SCAN_MINUTES", "15"))
_lock = threading.Lock()


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception as e:
        print(f"Save error {path}: {e}")


# ---------- Higher timeframe (4H + daily) ----------
def vote_higher(data):
    h4, d1 = data.get("h4"), data.get("d1")
    parts, notes = [], []
    if h4:
        c = [x["c"] for x in h4]
        st, _, _ = structure(h4)
        e50 = ema_series(c, 50)[-1]
        v = (0.6 if st == "BULL" else -0.6 if st == "BEAR" else 0) + (0.4 if c[-1] > e50 else -0.4)
        parts.append(v)
        notes.append(f"4H {st}")
    if d1:
        c = [x["c"] for x in d1]
        e20, e50 = ema_series(c, 20)[-1], ema_series(c, 50)[-1]
        if c[-1] > e20 > e50:
            v = 1.0
        elif c[-1] < e20 < e50:
            v = -1.0
        else:
            v = 0.5 if c[-1] > e50 else -0.5
        parts.append(v)
        notes.append("يومي " + ("صاعد" if v > 0 else "هابط"))
    if not parts:
        return 0.0, "لا بيانات للإطار الأكبر"
    return clamp(sum(parts) / len(parts)), " | ".join(notes)


# ---------- Session / news / pause filters ----------
def session_ok(dt):
    """London + New York liquidity hours (UTC), Mon-Fri."""
    wd, h = dt.weekday(), dt.hour
    if wd >= 5:
        return False, "السوق مغلق (عطلة نهاية الأسبوع)"
    if wd == 4 and h >= 19:
        return False, "نهاية الأسبوع: سيولة ضعيفة"
    if not (7 <= h < 20):
        return False, "خارج ساعات لندن/نيويورك (سيولة ضعيفة)"
    return True, ""


def news_blocked(dt):
    # first Friday of the month: NFP window (covers both DST offsets)
    if dt.weekday() == 4 and dt.day <= 7 and (12 <= dt.hour < 15):
        return "احتمال صدور NFP (تقلب شديد)"
    for item in os.environ.get("NEWS_TIMES", "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            ev = _dt.datetime.strptime(item, "%Y-%m-%dT%H:%M").replace(tzinfo=_dt.timezone.utc)
            delta = (dt - ev).total_seconds()  # negative = before the event
            if -45 * 60 <= delta <= 30 * 60:
                return f"خبر مهم عند {item} UTC"
        except Exception:
            continue
    return None


def blocked_reason(now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    st = _load(JOURNAL_FILE, {"signals": [], "paused_until": 0})
    if st.get("paused_until", 0) > time.time():
        return "البوت في وضع إيقاف مؤقت (/resume للاستئناف)"
    ok, why = session_ok(now)
    if not ok:
        return why
    return news_blocked(now)


# ---------- Journal & results tracking ----------
def _state():
    st = _load(JOURNAL_FILE, {"signals": [], "paused_until": 0})
    st.setdefault("signals", [])
    st.setdefault("paused_until", 0)
    return st


def active_signal(direction):
    with _lock:
        for s in reversed(_state()["signals"]):
            if s["dir"] == direction and s["status"] in ("pending", "open") and time.time() - s["ts"] < 3 * 3600:
                return s
    return None


def record_signal(sig, data):
    """Save a BUY/SELL signal. Returns (signal_dict, is_new)."""
    ex = active_signal(sig["dir"])
    if ex:
        return ex, False
    with _lock:
        st = _state()
        last = data["ltf"][-1].get("t") or int(time.time() * 1000)
        limit = "Limit" in sig.get("mode", "")
        s = {
            "id": len(st["signals"]) + 1, "ts": time.time(), "t0": last, "dir": sig["dir"],
            "entry": sig["entry"], "sl": sig["sl"], "tp1": sig["tp1"], "tp2": sig["tp2"],
            "rr": sig["rr"], "score": sig.get("score"), "status": "pending" if limit else "open",
            "result": None, "r": None,
        }
        st["signals"].append(s)
        _save(JOURNAL_FILE, st)
    return s, True


def update_outcomes(candles):
    """Walk candles after each active signal; returns the signals closed now."""
    closed = []
    if not candles or "t" not in candles[0]:
        return closed
    with _lock:
        st = _state()
        changed = False
        for s in st["signals"]:
            if s["status"] not in ("pending", "open"):
                continue
            buy = s["dir"] == "BUY"
            risk = abs(s["entry"] - s["sl"]) or 1e-9
            for c in candles:
                if c["t"] <= s["t0"]:
                    continue
                if s["status"] == "pending":
                    if c["l"] <= s["entry"] <= c["h"]:
                        s["status"] = "open"
                        changed = True
                    continue
                hit_sl = c["l"] <= s["sl"] if buy else c["h"] >= s["sl"]
                hit_tp = c["h"] >= s["tp1"] if buy else c["l"] <= s["tp1"]
                if hit_sl:  # conservative: SL first when both inside one candle
                    s.update(status="closed", result="SL", r=-1.0)
                elif hit_tp:
                    s.update(status="closed", result="TP1", r=round(abs(s["tp1"] - s["entry"]) / risk, 2))
                if s["status"] == "closed":
                    changed = True
                    closed.append(dict(s))
                    break
            if s["status"] in ("pending", "open") and time.time() - s["ts"] > 24 * 3600:
                if s["status"] == "pending":
                    s.update(status="closed", result="UNFILLED", r=0.0)
                else:
                    last = candles[-1]["c"]
                    r = ((last - s["entry"]) if buy else (s["entry"] - last)) / risk
                    s.update(status="closed", result="EXPIRED", r=round(r, 2))
                changed = True
                closed.append(dict(s))
        if changed:
            _save(JOURNAL_FILE, st)
    return closed


def result_text(s):
    icon = {"TP1": "✅", "SL": "❌", "EXPIRED": "⏱", "UNFILLED": "➖"}.get(s["result"], "")
    return f'{icon} نتيجة الصفقة #{s["id"]} ({s["dir"]}): {s["result"]} ({s["r"]:+.2f}R)'


def stats_text():
    sigs = _state()["signals"]
    done = [s for s in sigs if s["status"] == "closed" and s["result"] in ("TP1", "SL", "EXPIRED")]
    active = [s for s in sigs if s["status"] in ("pending", "open")]
    if not sigs:
        return "📊 لا توجد صفقات مسجلة بعد."
    wins = [s for s in done if s["r"] > 0]
    losses = [s for s in done if s["r"] <= 0]
    lines = ["📊 إحصائيات الصفقات", "────────────────",
             f"المسجلة: {len(sigs)} | المفتوحة/المعلّقة: {len(active)} | المغلقة: {len(done)}"]
    if done:
        gp = sum(s["r"] for s in wins)
        gl = -sum(s["r"] for s in losses)
        lines += [
            f"✅ رابحة: {len(wins)} | ❌ خاسرة: {len(losses)}",
            f"نسبة النجاح: {100 * len(wins) / len(done):.0f}%",
            f"متوسط النتيجة: {sum(s['r'] for s in done) / len(done):+.2f}R",
            f"عامل الربح: {gp / gl:.2f}" if gl > 0 else "عامل الربح: —",
        ]
    lines.append("")
    for s in sigs[-5:]:
        res = s["result"] or s["status"]
        lines.append(f'#{s["id"]} {s["dir"]} @ {s["entry"]} → {res}')
    lines += ["", "ملاحظة: النتيجة تُحسب عند TP1 أو SL، والعينة الصغيرة لا تعني الكثير إحصائياً."]
    return "\n".join(lines)


def lot_size(sig):
    try:
        bal = float(os.environ.get("ACCOUNT_BALANCE", "0"))
        pct = float(os.environ.get("RISK_PCT", "1"))
    except ValueError:
        return None
    if bal <= 0:
        return None
    per_lot_loss = abs(sig["entry"] - sig["sl"]) * 100  # 1 lot = 100 oz
    if per_lot_loss <= 0:
        return None
    return round(bal * pct / 100 / per_lot_loss, 2), pct, bal


# ---------- Telegram push + background scanner ----------
def tg_send(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=10,
        )
    except Exception as e:
        print(f"Telegram send error: {e}")


def broadcast(text):
    for cid in _load(SUBS_FILE, []):
        tg_send(cid, text)


def scan_once():
    data = fetch_market_data()
    if not data:
        return
    for s in update_outcomes(data["ltf"]):
        broadcast(result_text(s))
    if blocked_reason():
        return
    sig = build_signal(data)
    if sig["dir"] not in ("BUY", "SELL") or active_signal(sig["dir"]):
        return
    sig, notes = finalize(data, sig)
    if sig["dir"] in ("BUY", "SELL"):
        rec, new = record_signal(sig, data)
        if new:
            broadcast("🔔 إشارة جديدة #%d\n\n" % rec["id"] + format_message(data, sig, notes))


def scanner_loop():
    time.sleep(30)
    while True:
        try:
            scan_once()
        except Exception as e:
            print(f"Scanner error: {e}")
        time.sleep(SCAN_MINUTES * 60)


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

    if sig["dir"] in ("BUY", "SELL"):
        ls = lot_size(sig)
        if ls:
            lines.append(f"📦 حجم مقترح: {ls[0]} لوت (مخاطرة {ls[1]:g}% من {ls[2]:g}$)")
    lines += ["", f'💡 السبب: {sig["why"]}', ""]
    if sig.get("votes"):
        lines.append(f'🏫 المدارس (الدرجة {sig["score"]:+.2f}):')
        for v in sig["votes"]:
            arrow = "🟢" if v["vote"] >= 0.3 else "🔴" if v["vote"] <= -0.3 else "⚪"
            lines.append(f'{arrow} {v["school"]}: {v["vote"]:+.1f} — {v["note"]}')
        lines.append("")
    lines.append(
        f'📊 1H: {f.get("htf_structure")} | 15m: {f.get("ltf_structure")} | '
        f'ATR: {f.get("atr15m")} | EMA50(1H): {f.get("htf_ema50")}'
    )
    if notes:
        lines += [""] + notes
    lines += ["", f'🔖 النسخة: {VERSION} | المصدر: {data.get("source")} | عمر آخر شمعة: {data.get("age_min")} د | شموع: {len(data["ltf"])}/{len(data["htf"])}', "⚠️ تحليل آلي تعليمي وليس نصيحة مالية. التزم بإدارة رأس المال."]
    return "\n".join(lines)


# =====================================================
# (section)
# =====================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 بوت التحليل متعدد المدارس (ICT/SMC + EMA + RSI/MACD + دعم/مقاومة/فيبو + شموع + بولينجر + 4H/يومي)\n\n"
        "/analyze  تحليل فوري (أضف force لتجاهل فلتر الجلسات)\n"
        "/subscribe  استلام الإشارات تلقائياً\n"
        "/unsubscribe  إيقاف التنبيهات\n"
        "/stats  إحصائيات نتائج الصفقات\n"
        "/pause 60  إيقاف الإشارات لمدة 60 دقيقة\n"
        "/resume  استئناف الإشارات\n\n"
        "⚠️ تحليل آلي تعليمي وليس نصيحة مالية."
    )


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        force = any(a.lower() == "force" for a in (context.args or []))
        msg = await update.message.reply_text("🔍 جاري فحص بنية السوق والسيولة...")
        data = fetch_market_data()
        if not data:
            await msg.edit_text("❌ تعذّر جلب بيانات الشموع الآن. لن أعطي تحليلاً بدون بيانات حقيقية، حاول لاحقاً.")
            return
        for s in update_outcomes(data["ltf"]):
            broadcast(result_text(s))

        sig = build_signal(data)
        if sig["dir"] in ("BUY", "SELL") and not force:
            why = blocked_reason()
            if why:
                sig = {**sig, "dir": "WAIT", "why": f"{why}. (أرسل /analyze force لتجاهل الفلتر)"}

        sig, notes = finalize(data, sig)
        if sig["dir"] == "WAIT":
            notes = watch_levels(data) + notes
        else:
            rec, new = record_signal(sig, data)
            notes = notes + [f"📝 سُجّلت كصفقة #{rec['id']}" if new else f"ℹ️ صفقة مماثلة مسجلة سابقاً #{rec['id']}"]
        await msg.edit_text(format_message(data, sig, notes))
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ: {e}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(stats_text())


async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = _load(SUBS_FILE, [])
    cid = update.effective_chat.id
    if cid not in subs:
        subs.append(cid)
        _save(SUBS_FILE, subs)
    await update.message.reply_text(f"✅ تم الاشتراك. سيفحص البوت السوق كل {SCAN_MINUTES} دقيقة ويرسل الإشارات والنتائج تلقائياً.")


async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = [c for c in _load(SUBS_FILE, []) if c != update.effective_chat.id]
    _save(SUBS_FILE, subs)
    await update.message.reply_text("تم إيقاف التنبيهات.")


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        mins = int(context.args[0]) if context.args else 60
    except ValueError:
        mins = 60
    with _lock:
        st = _state()
        st["paused_until"] = time.time() + mins * 60
        _save(JOURNAL_FILE, st)
    await update.message.reply_text(f"⏸ تم إيقاف الإشارات {mins} دقيقة.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with _lock:
        st = _state()
        st["paused_until"] = 0
        _save(JOURNAL_FILE, st)
    await update.message.reply_text("▶️ تم استئناف الإشارات.")


# =====================================================
# (section)
# =====================================================
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("CRITICAL: TELEGRAM_BOT_TOKEN is missing!")
        raise SystemExit(1)

    threading.Thread(target=start_health_server, daemon=True).start()
    threading.Thread(target=scanner_loop, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("analyze", analyze_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))

    print("Smart ICT Bot is running...")
    app.run_polling(drop_pending_updates=True)
