import os
import json
import re
import threading
import requests
import sqlite3
import time
import datetime as _dt
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# =====================================================
# Health Check Server
# =====================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Smart ICT/SMC + Gann + KillZones Bot is running!")

    def log_message(self, format, *args):
        return


def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthCheckHandler).serve_forever()


# =====================================================
# Configuration & Globals
# =====================================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")        
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

VERSION = "v9-pro-killzones"
MIN_RR = 1.5          
MAX_RISK_ATR = 3.0    
MAX_SL_POINTS = 40.0  # أقصى مسافة مسموحة لوقف الخسارة بالنقاط

DATA_DIR = os.environ.get("DATA_DIR", ".")
DB_FILE = os.path.join(DATA_DIR, "bot_data.db")
SCAN_MINUTES = int(os.environ.get("SCAN_MINUTES", "15"))
_lock = threading.Lock()


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL,
                t0 INTEGER,
                dir TEXT,
                entry REAL,
                sl REAL,
                tp1 REAL,
                tp2 REAL,
                rr REAL,
                score REAL,
                status TEXT,
                result TEXT,
                r REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS subscribers (
                chat_id INTEGER PRIMARY KEY
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value REAL
            )
        """)
        conn.commit()


# =====================================================
# Market Data Fetching
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
                    for c in r[:-1]  
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
    return None


def fetch_market_data():
    got, source = None, None
    for name in ("binance", "yahoo"):   
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
    if source == "binance":   
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
# Technical Indicators & Analysis (ICT / SMC / Gann)
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
    highs, lows = [], []
    for i in range(k, len(candles) - k):
        w = candles[i - k : i + k + 1]
        if candles[i]["h"] == max(x["h"] for x in w):
            highs.append((i, candles[i]["h"]))
        if candles[i]["l"] == min(x["l"] for x in w):
            lows.append((i, candles[i]["l"]))
    return highs, lows


def structure(candles):
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


def calc_gann_levels(price):
    if price <= 0:
        return [], []
    root = price ** 0.5
    steps = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]
    supports, resistances = [], []
    for s in steps:
        sup = (root - s) ** 2
        if sup > 0:
            supports.append(round(sup, 2))
        res = (root + s) ** 2
        resistances.append(round(res, 2))
    return sorted(supports, reverse=True), sorted(resistances)


def find_fvgs(candles, lookback=40):
    res = []
    start = max(2, len(candles) - lookback)
    for i in range(start, len(candles)):
        a, c = candles[i - 2], candles[i]
        after = candles[i + 1 :]
        if c["l"] > a["h"]:  
            lo, hi = a["h"], c["l"]
            if not any(x["l"] <= lo for x in after):
                res.append({"type": "BUY", "low": lo, "high": hi, "kind": "FVG"})
        if c["h"] < a["l"]:  
            lo, hi = c["h"], a["l"]
            if not any(x["h"] >= hi for x in after):
                res.append({"type": "SELL", "low": lo, "high": hi, "kind": "FVG"})
    return res


def find_order_blocks(candles, a, lookback=40):
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
    pos = (price - swing_low) / rng  
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
        bias = "SELL"   
    elif price > ema50 + 1.0 * atr(htf):
        bias = "BUY"
    else:
        return {"dir": "WAIT", "why": "الاتجاه العام غير واضح.", "facts": facts}

    facts["bias"] = bias
    if (bias == "BUY" and pos > 0.65) or (bias == "SELL" and pos < 0.35):
        return {"dir": "WAIT", "why": f"الاتجاه {bias} لكن السعر بعيد عن منطقة الدخول.", "facts": facts}

    zones = [z for z in find_fvgs(ltf, 60) + find_order_blocks(ltf, a, 60) if z["type"] == bias]

    def dist(z):
        if z["low"] <= price <= z["high"]:
            return 0
        return (z["low"] - price) if price < z["low"] else (price - z["high"])

    near = [z for z in zones if dist(z) <= 3 * a]
    if not near:
        return {"dir": "WAIT", "why": f"الاتجاه {bias} لكن لا توجد منطقة قريبة.", "facts": facts}

    z = min(near, key=dist)
    facts["entry_zone"] = f'{z["kind"]} {round(z["low"], 2)}-{round(z["high"], 2)}'

    if dist(z) == 0:
        entry, mode = price, "دخول فوري"
    else:
        entry = z["high"] if bias == "BUY" else z["low"]
        mode = "أمر معلّق Limit"

    if bias == "BUY":
        sl = min(z["low"], swing_low if swing_low < entry else z["low"]) - 0.3 * a
        risk = entry - sl
        tp1, tp2 = entry + 1.5 * risk, entry + 3 * risk
    else:
        sl = max(z["high"], swing_high if swing_high > entry else z["high"]) + 0.3 * a
        risk = sl - entry
        tp1, tp2 = entry - 1.5 * risk, entry - 3 * risk

    if risk > MAX_SL_POINTS:
        return {"dir": "WAIT", "why": f"الستوب واسع جداً ({round(risk, 1)} نقطة).", "facts": facts}

    if risk <= 0 or risk > MAX_RISK_ATR * a:
        return {"dir": "WAIT", "why": "المخاطرة غير مناسبة أو الستوب بعيد.", "facts": facts}

    return {
        "dir": bias,
        "entry": round(entry, 2),
        "mode": mode,
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr": round(abs(tp1 - entry) / risk, 2),
        "why": f"اتجاه {htf_struct}، هيكل 15m: {ltf_struct}.",
        "facts": facts,
    }


SCORE_MIN = 0.35
WEIGHTS = {
    "ICT/SMC": 2.0, 
    "الاتجاه EMA": 1.5, 
    "الزخم RSI/MACD": 1.0,
    "دعم/مقاومة+فيبو": 1.0, 
    "مربع جان (Gann)": 1.2, 
    "حركة السعر": 0.8, 
    "بولينجر": 0.5,
    "الإطار الأكبر 4H/D": 1.5
}


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
        return 1.0, "صاعد قوي"
    if p < e20[-1] < e50[-1] and slope < 0:
        return -1.0, "هابط قوي"
    if p > e50[-1]:
        return 0.5, "فوق EMA50"
    if p < e50[-1]:
        return -0.5, "تحت EMA50"
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
    return clamp(v), f"RSI {r1h:.0f}"


def vote_sr_fib(data, a15, a1h):
    price, htf = data["price"], data["htf"]
    levels = sr_levels(htf) + sr_levels(data["ltf"])
    tol = max(1.5 * a15, 0.4 * a1h)
    sup = [x for x in levels if x <= price]
    res = [x for x in levels if x > price]
    v, notes = 0.0, []
    if sup and price - max(sup) <= tol:
        v += 1
        notes.append("قرب دعم")
    if res and min(res) - price <= tol:
        v -= 1
        notes.append("قرب مقاومة")
    return clamp(v), " | ".join(notes) or "لا مستوى"


def vote_gann(data):
    price = data["price"]
    a15 = atr(data["ltf"])
    supports, resistances = calc_gann_levels(price)
    tol = 1.5 * a15
    v = 0.0
    notes = []
    near_sup = [s for s in supports if abs(price - s) <= tol]
    near_res = [r for r in resistances if abs(price - r) <= tol]
    if near_sup:
        v += 1.0
        notes.append(f"دعم جان عند {near_sup[0]}")
    if near_res:
        v -= 1.0
        notes.append(f"مقاومة جان عند {near_res[0]}")
    return clamp(v), " | ".join(notes) or "خارج مستويات جان الرئيسية"


def _candle_pattern(p, c):
    body, rng = abs(c["c"] - c["o"]), c["h"] - c["l"]
    if rng <= 0:
        return 0
    if p["c"] < p["o"] and c["c"] > c["o"] and c["c"] >= p["o"]:
        return 1
    if p["c"] > p["o"] and c["c"] < c["o"] and c["c"] <= p["o"]:
        return -1
    return 0


def vote_price_action(ltf, htf):
    a = _candle_pattern(ltf[-2], ltf[-1])
    c = _candle_pattern(htf[-2], htf[-1])
    v = 0.6 * a + 0.6 * c
    return clamp(v), "حركة السعر"


def vote_bollinger(ltf):
    pb = pct_b([x["c"] for x in ltf])
    v = 0.6 if pb < 0.1 else -0.6 if pb > 0.9 else 0.0
    return v, f"%B = {pb:.2f}"


def vote_higher(data):
    h4, d1 = data.get("h4"), data.get("d1")
    parts, notes = [], []
    if h4:
        st, _, _ = structure(h4)
        v = 0.6 if st == "BULL" else -0.6 if st == "BEAR" else 0
        parts.append(v)
    if d1:
        parts.append(0.5)
    if not parts:
        return 0.0, "-"
    return clamp(sum(parts) / len(parts)), "الإطار الأكبر"


def build_signal(data: dict) -> dict:
    ltf, htf, price = data["ltf"], data["htf"], data["price"]
    a15, a1h = atr(ltf), atr(htf)
    if a15 < 0.15 or (data.get("age_min") or 0) > 90:
        return {"dir": "WAIT", "facts": {"atr15m": round(a15, 2)}, "votes": [], "score": 0.0,
                "why": "بيانات غير موثوقة أو قديمة."}
    smc = smc_signal(data)

    results = [
        ("ICT/SMC",) + vote_smc(smc),
        ("الاتجاه EMA",) + vote_trend(htf),
        ("الزخم RSI/MACD",) + vote_momentum(ltf, htf),
        ("دعم/مقاومة+فيبو",) + vote_sr_fib(data, a15, a1h),
        ("مربع جان (Gann)",) + vote_gann(data),
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
        return {**base, "dir": "WAIT", "why": f"توافق ضعيف: الدرجة {score:+.2f}."}

    direction = "BUY" if score > 0 else "SELL"
    
    if smc["dir"] == direction:
        entry, sl, tp1, tp2 = smc["entry"], smc["sl"], smc["tp1"], smc["tp2"]
        mode = smc.get("mode", "")
        risk = abs(entry - sl)
    else:
        mode = "دخول فوري"
        entry = price
        risk = 1.0 * a15
        sl = price - risk if direction == "BUY" else price + risk
        tp1 = price + (1 if direction == "BUY" else -1) * 1.5 * risk
        tp2 = price + (1 if direction == "BUY" else -1) * 3.0 * risk

    if risk > MAX_SL_POINTS:
        return {**base, "dir": "WAIT", "why": f"الستوب واسع جداً ({round(risk, 1)} نقطة)."}

    return {
        **base,
        "dir": direction,
        "entry": round(entry, 2),
        "mode": mode,
        "sl": round(sl, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr": round(abs(tp1 - entry) / max(risk, 1e-9), 2),
        "why": f"توافق المدارس مع مربع جان، الدرجة {score:+.2f}.",
    }


# =====================================================
# Database State & SQLite Management
# =====================================================
def get_paused_until():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_state WHERE key = 'paused_until'")
        row = cur.fetchone()
        return row[0] if row else 0.0


def set_paused_until(val):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO bot_state (key, value) VALUES ('paused_until', ?)", (val,))
        conn.commit()


def session_ok(dt):
    """فلتر فترات الـ Kill Zones الحصرية لـ ICT (بتوقيت UTC):
    - افتتاح لندن: 07:00 إلى 10:00 UTC
    - افتتاح نيويورك: 12:00 إلى 15:00 UTC
    - إغلاق لندن: 15:00 إلى 17:00 UTC
    """
    wd, h, m = dt.weekday(), dt.hour, dt.minute
    total_minutes = h * 60 + m

    if wd >= 5:
        return False, "السوق مغلق (عطلة نهاية الأسبوع)"

    london_open = (7 * 60 <= total_minutes <= 10 * 60)
    ny_open = (12 * 60 <= total_minutes <= 15 * 60)
    london_close = (15 * 60 <= total_minutes <= 17 * 60)

    if london_open or ny_open or london_close:
        return True, ""

    return False, "خارج فترات الـ Kill Zones (سيولة ضعيفة / تذبذب)"


def blocked_reason(now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if get_paused_until() > time.time():
        return "البوت في وضع إيقاف مؤقت"
    ok, why = session_ok(now)
    if not ok:
        return why
    return None


def active_signal(direction):
    with _lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM signals WHERE dir = ? AND status IN ('pending', 'open')", (direction,))
            rows = cur.fetchall()
            for row in rows:
                s = dict(row)
                if time.time() - s["ts"] < 3 * 3600:
                    return s
    return None


def record_signal(sig, data):
    ex = active_signal(sig["dir"])
    if ex:
        return ex, False
    with _lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            last = data["ltf"][-1].get("t") or int(time.time() * 1000)
            limit = "Limit" in sig.get("mode", "")
            cur.execute("""
                INSERT INTO signals (ts, t0, dir, entry, sl, tp1, tp2, rr, score, status, result, r)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """, (time.time(), last, sig["dir"], sig["entry"], sig["sl"], sig["tp1"], sig["tp2"], sig["rr"], sig.get("score"), 'pending' if limit else 'open'))
            conn.commit()
            sig_id = cur.lastrowid
            
            cur.execute("SELECT * FROM signals WHERE id = ?", (sig_id,))
            row = cur.fetchone()
            s = dict(row) if row else {}
    return s, True


def update_outcomes(candles):
    closed = []
    if not candles or "t" not in candles[0]:
        return closed
    with _lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM signals WHERE status IN ('pending', 'open')")
            signals = [dict(row) for row in cur.fetchall()]

            changed = False
            for s in signals:
                buy = s["dir"] == "BUY"
                risk = abs(s["entry"] - s["sl"]) or 1e-9
                for c in candles:
                    if c["t"] <= s["t0"]:
                        continue
                    if s["status"] == "pending":
                        if c["l"] <= s["entry"] <= c["h"]:
                            cur.execute("UPDATE signals SET status = 'open' WHERE id = ?", (s["id"],))
                            s["status"] = "open"
                            changed = True
                        continue

                    hit_sl = c["l"] <= s["sl"] if buy else c["h"] >= s["sl"]
                    hit_tp1 = c["h"] >= s["tp1"] if buy else c["l"] <= s["tp1"]
                    hit_tp2 = c["h"] >= s["tp2"] if buy else c["l"] <= s["tp2"]

                    if hit_sl:
                        final_r = 0.0 if (buy and s["sl"] >= s["entry"] or not buy and s["sl"] <= s["entry"]) else -1.0
                        res_label = "BE" if final_r == 0.0 else "SL"
                        cur.execute("UPDATE signals SET status = 'closed', result = ?, r = ? WHERE id = ?", (res_label, final_r, s["id"]))
                        s.update(status="closed", result=res_label, r=final_r)
                        changed = True
                        closed.append(dict(s))
                        break
                    elif hit_tp2:
                        final_r = round(abs(s["tp2"] - s["entry"]) / risk, 2)
                        cur.execute("UPDATE signals SET status = 'closed', result = 'TP2', r = ? WHERE id = ?", (final_r, s["id"]))
                        s.update(status="closed", result="TP2", r=final_r)
                        changed = True
                        closed.append(dict(s))
                        break
                    elif hit_tp1 and s["sl"] != s["entry"]:
                        new_sl = s["entry"]
                        cur.execute("UPDATE signals SET sl = ? WHERE id = ?", (new_sl, s["id"]))
                        s["sl"] = new_sl
                        changed = True

                if s["status"] in ("pending", "open") and time.time() - s["ts"] > 24 * 3600:
                    res = "UNFILLED" if s["status"] == "pending" else "EXPIRED"
                    cur.execute("UPDATE signals SET status = 'closed', result = ?, r = 0.0 WHERE id = ?", (res, s["id"]))
                    s.update(status="closed", result=res, r=0.0)
                    changed = True
                    closed.append(dict(s))

            if changed:
                conn.commit()
    return closed


def result_text(s):
    icon = {"TP1": "✅", "TP2": "🚀", "SL": "❌", "BE": "🛡️", "EXPIRED": "⏱", "UNFILLED": "➖"}.get(s["result"], "")
    return f'{icon} نتيجة الصفقة #{s["id"]} ({s["dir"]}): {s["result"]} ({s["r"]:+.2f}R)'


def stats_text():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM signals")
        sigs = [dict(row) for row in cur.fetchall()]

    done = [s for s in sigs if s["status"] == "closed" and s["result"] in ("TP1", "TP2", "SL", "BE", "EXPIRED")]
    active = [s for s in sigs if s["status"] in ("pending", "open")]
    if not sigs:
        return "📊 لا توجد صفقات مسجلة بعد."
    wins = [s for s in done if s["r"] > 0]
    losses = [s for s in done if s["r"] < 0]
    lines = ["📊 إحصائيات الصفقات (KillZones + Gann)", "────────────────",
             f"المسجلة: {len(sigs)} | المفتوحة: {len(active)} | المغلقة: {len(done)}"]
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
    return "\n".join(lines)


# =====================================================
# Telegram Handlers & Background Scanner
# =====================================================
def tg_send(chat_id, text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=10,
        )
    except Exception as e:
        print(f"Telegram send error: {e}")


def broadcast(text):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM subscribers")
        subs = [row[0] for row in cur.fetchall()]
    for cid in subs:
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
    rec, new = record_signal(sig, data)
    if new:
        broadcast(f"🔔 إشارة جديدة #{rec['id']}\n\n" + format_message(data, sig, []))


def scanner_loop():
    time.sleep(30)
    while True:
        try:
            scan_once()
        except Exception as e:
            print(f"Scanner error: {e}")
        time.sleep(SCAN_MINUTES * 60)


def format_message(data, sig, notes):
    f = sig["facts"]
    lines = [
        f'🎯 تحليل ICT KillZones + Gann ({data["symbol"]})',
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
            f'⚖️ R:R: 1:{sig["rr"]}',
        ]
    lines += ["", f'💡 السبب: {sig["why"]}', ""]
    if sig.get("votes"):
        lines.append(f'🏫 المدارس (الدرجة {sig["score"]:+.2f}):')
        for v in sig["votes"]:
            arrow = "🟢" if v["vote"] >= 0.3 else "🔴" if v["vote"] <= -0.3 else "⚪"
            lines.append(f'{arrow} {v["school"]}: {v["vote"]:+.1f}')
    if notes:
        lines += [""] + notes
    lines += ["", f'🔖 النسخة: {VERSION} | فترات السيولة مفعلة']
    return "\n".join(lines)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧠 بوت التداول (مع فترات Kill Zones ومربع جان)\n\n"
        "/analyze  تحليل فوري (أضف force لتجاوز فلتر الجلسات)\n"
        "/subscribe  اشتراك بالتنبيهات\n"
        "/unsubscribe  إيقاف التنبيهات\n"
        "/stats  إحصائيات الصفقات\n"
        "/pause 60  إيقاف مؤقت\n"
        "/resume  استئناف"
    )


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        force = any(a.lower() == "force" for a in (context.args or []))
        msg = await update.message.reply_text("🔍 جاري فحص سيولة Kill Zones والسوق...")
        data = fetch_market_data()
        if not data:
            await msg.edit_text("❌ تعذّر جلب البيانات.")
            return
        for s in update_outcomes(data["ltf"]):
            broadcast(result_text(s))

        sig = build_signal(data)
        if sig["dir"] in ("BUY", "SELL") and not force:
            why = blocked_reason()
            if why:
                sig = {**sig, "dir": "WAIT", "why": f"{why}. (أرسل /analyze force للتجاوز)"}

        notes = []
        if sig["dir"] in ("BUY", "SELL"):
            rec, new = record_signal(sig, data)
            notes.append(f"📝 صفقة #{rec['id']}" if new else f"ℹ️ مسجلة سابقاً #{rec['id']}")
        await msg.edit_text(format_message(data, sig, notes))
    except Exception as e:
        await update.message.reply_text(f"حدث خطأ: {e}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(stats_text())


async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO subscribers (chat_id) VALUES (?)", (cid,))
        conn.commit()
    await update.message.reply_text("✅ تم الاشتراك بنجاح واستلام تنبيهات Kill Zones.")


async def unsubscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (cid,))
        conn.commit()
    await update.message.reply_text("تم إلغاء الاشتراك.")


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        mins = int(context.args[0]) if context.args else 60
    except ValueError:
        mins = 60
    set_paused_until(time.time() + mins * 60)
    await update.message.reply_text(f"⏸ تم الإيقاف لمدة {mins} دقيقة.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_paused_until(0)
    await update.message.reply_text("▶️ تم الاستئناف.")


# =====================================================
# Main Entry Point
# =====================================================
if __name__ == "__main__":
    if not TELEGRAM_TOKEN:
        print("CRITICAL: TELEGRAM_BOT_TOKEN is missing!")
        raise SystemExit(1)

    init_db()

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

    print("Smart ICT KillZones Bot is running...")
    app.run_polling(drop_pending_updates=True)
