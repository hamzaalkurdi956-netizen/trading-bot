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
        self.wfile.write(b"Smart ICT Ultimate Institutional Bot is running!")

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

VERSION = "v10-ultimate-institutional"
MIN_RR = 1.5          
MAX_RISK_ATR = 3.0    
MAX_SL_POINTS = 40.0  

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
        # جدول محفظة التداول التجريبي (Paper Trading)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                balance REAL
            )
        """)
        conn.execute("INSERT OR IGNORE INTO paper_account (id, balance) VALUES (1, 10000.0)")
        conn.commit()


# =====================================================
# Market Data Fetching (XAU/USD & DXY Proxy)
# =====================================================
def _binance_klines(symbol, interval: str, limit: int = 150):
    for host in ("https://data-api.binance.vision", "https://api.binance.com"):
        try:
            r = requests.get(
                f"{host}/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=8,
            ).json()
            if isinstance(r, list) and len(r) > 30:
                return [
                    {"t": int(c[0]), "o": float(c[1]), "h": float(c[2]),
                     "l": float(c[3]), "c": float(c[4])}
                    for c in r[:-1]  
                ]
        except Exception as e:
            print(f"Binance {host} {symbol} error: {e}")
    return None


def _yahoo_klines(ticker, interval: str, rng: str):
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
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
        print(f"Yahoo {ticker} error: {e}")
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


def fetch_market_data():
    # جلب بيانات الذهب ومؤشر الدولار DXY للتأكيد
    ltf = _binance_klines("PAXGUSDT", "15m", 150) or _yahoo_klines("GC=F", "15m", "5d")
    h1 = _binance_klines("PAXGUSDT", "1h", 500) or _yahoo_klines("GC=F", "60m", "3mo")
    d1 = _binance_klines("PAXGUSDT", "1d", 200) or _yahoo_klines("GC=F", "1d", "1y")
    
    dxy_candles = _yahoo_klines("DX-Y.NYB", "60m", "5d")

    if not _good(ltf) or not _good(h1):
        return None

    htf = h1[-150:]
    h4 = resample(h1, 4)[-120:]
    price = ltf[-1]["c"]

    # حساب اتجاه مؤشر الدولار قصير المدى للفلتر العكسي
    dxy_trend = "NEUTRAL"
    if dxy_candles and len(dxy_candles) > 10:
        dxy_diff = dxy_candles[-1]["c"] - dxy_candles[-6]["c"]
        dxy_trend = "BULL" if dxy_diff > 0 else "BEAR"

    age_min = round((time.time() * 1000 - ltf[-1]["t"] - 900_000) / 60000) if ltf[-1].get("t") else 0
    return {"symbol": "XAU/USD", "price": price, "ltf": ltf, "htf": htf,
            "dxy_trend": dxy_trend, "age_min": age_min,
            "h4": h4 if len(h4) >= 30 else None,
            "d1": d1 if d1 and len(d1) >= 55 else None}


# =====================================================
# Technical Analysis & Gann & DXY Filter
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

    facts = {"htf_structure": htf_struct, "ltf_structure": ltf_struct}
    if not highs or not lows:
        return {"dir": "WAIT", "why": "No swings.", "facts": facts}

    swing_high, swing_low = highs[-1][1], lows[-1][1]
    rng = max(abs(swing_high - swing_low), 0.0001)
    pos = (price - swing_low) / rng  

    if htf_struct == "BULL": bias = "BUY"
    elif htf_struct == "BEAR": bias = "SELL"
    elif price > ema50: bias = "BUY"
    else: bias = "SELL"

    # فلتر مؤشر الدولار DXY (الذهب يتحرك عكس الدولار غالباً)
    if data["dxy_trend"] == "BULL" and bias == "BUY":
        return {"dir": "WAIT", "why": "تعارض مع مؤشر الدولار (DXY صاعد والذهب شراء).", "facts": facts}
    if data["dxy_trend"] == "BEAR" and bias == "SELL":
        return {"dir": "WAIT", "why": "تعارض مع مؤشر الدولار (DXY هابط والذهب بيع).", "facts": facts}

    zones = [z for z in find_fvgs(ltf, 60) + find_order_blocks(ltf, a, 60) if z["type"] == bias]
    if not zones:
        return {"dir": "WAIT", "why": "لا توجد مناطق FVG/OB قريبة.", "facts": facts}

    z = min(zones, key=lambda x: abs(x["low"] - price))
    entry = z["high"] if bias == "BUY" else z["low"]
    risk = abs(entry - (z["low"] if bias == "BUY" else z["high"])) + 0.3 * a

    if risk > MAX_SL_POINTS or risk <= 0:
        return {"dir": "WAIT", "why": "مخاطرة غير مناسبة أو الستوب واسع.", "facts": facts}

    tp1 = entry + 1.5 * risk if bias == "BUY" else entry - 1.5 * risk
    tp2 = entry + 3.0 * risk if bias == "BUY" else entry - 3.0 * risk

    return {
        "dir": bias, "entry": round(entry, 2), "mode": "Limit",
        "sl": round(entry - risk if bias == "BUY" else entry + risk, 2),
        "tp1": round(tp1, 2), "tp2": round(tp2, 2),
        "rr": 1.5, "why": f"توافق ICT/SMC مع فلتر DXY ({data['dxy_trend']}).", "facts": facts
    }


# =====================================================
# Database State & Paper Trading Management
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


def get_paper_balance():
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT balance FROM paper_account WHERE id = 1")
        return cur.fetchone()[0]


def update_paper_balance(amount):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE paper_account SET balance = balance + ? WHERE id = 1", (amount,))
        conn.commit()


def session_ok(dt):
    wd, h, m = dt.weekday(), dt.hour, dt.minute
    total_minutes = h * 60 + m
    if wd >= 5: return False, "السوق مغلق (عطلة نهاية الأسبوع)"
    
    london_open = (7 * 60 <= total_minutes <= 10 * 60)
    ny_open = (12 * 60 <= total_minutes <= 15 * 60)
    london_close = (15 * 60 <= total_minutes <= 17 * 60)
    
    if london_open or ny_open or london_close:
        return True, ""
    return False, "خارج فترات Kill Zones (سيولة ضعيفة)"


def blocked_reason(now=None):
    now = now or _dt.datetime.now(_dt.timezone.utc)
    if get_paused_until() > time.time(): return "البوت متوقف مؤقتاً"
    ok, why = session_ok(now)
    return why if not ok else None


def active_signal(direction):
    with _lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM signals WHERE dir = ? AND status IN ('pending', 'open')", (direction,))
            for row in cur.fetchall():
                if time.time() - row["ts"] < 3 * 3600:
                    return dict(row)
    return None


def record_signal(sig, data):
    ex = active_signal(sig["dir"])
    if ex: return ex, False
    with _lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO signals (ts, t0, dir, entry, sl, tp1, tp2, rr, score, status, result, r)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, 'open', NULL, NULL)
            """, (time.time(), data["ltf"][-1].get("t") or int(time.time() * 1000), 
                  sig["dir"], sig["entry"], sig["sl"], sig["tp1"], sig["tp2"], sig["rr"]))
            conn.commit()
            cur.execute("SELECT * FROM signals WHERE id = ?", (cur.lastrowid,))
            return dict(cur.fetchone()), True


def update_outcomes(candles):
    closed = []
    if not candles: return closed
    with _lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM signals WHERE status IN ('pending', 'open')")
            for s in cur.fetchall():
                buy = s["dir"] == "BUY"
                risk = abs(s["entry"] - s["sl"]) or 1e-9
                for c in candles:
                    if c["t"] <= s["t0"]: continue
                    hit_sl = c["l"] <= s["sl"] if buy else c["h"] >= s["sl"]
                    hit_tp1 = c["h"] >= s["tp1"] if buy else c["l"] <= s["tp1"]
                    
                    if hit_sl:
                        final_r = 0.0 if (buy and s["sl"] >= s["entry"] or not buy and s["sl"] <= s["entry"]) else -1.0
                        res = "BE" if final_r == 0.0 else "SL"
                        cur.execute("UPDATE signals SET status = 'closed', result = ?, r = ? WHERE id = ?", (res, final_r, s["id"]))
                        update_paper_balance(final_r * 100) # افتراض 100$ لكل 1R في التداول التجريبي
                        s_dict = dict(s)
                        s_dict.update({"status": "closed", "result": res, "r": final_r})
                        closed.append(s_dict)
                        break
                    elif hit_tp1 and s["sl"] != s["entry"]:
                        cur.execute("UPDATE signals SET sl = ? WHERE id = ?", (s["entry"], s["id"]))
            conn.commit()
    return closed


def stats_digest():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT * FROM signals")
        sigs = [dict(row) for row in cur.fetchall()]
    
    bal = get_paper_balance()
    done = [s for s in sigs if s["status"] == "closed"]
    wins = [s for s in done if s["r"] > 0]
    
    lines = [
        "📊 التقرير الإحصائي المؤسسي الشامل",
        "────────────────────────",
        f"💰 رصيد المحفظة التجريبية: ${bal:.2f}",
        f"📈 إجمالي الصفقات المغلقة: {len(done)}",
        f"✅ صفقات رابحة: {len(wins)} | ❌ صفقات خاسرة: {len(done) - len(wins)}",
        f"🎯 نسبة النجاح: {(len(wins)/len(done)*100) if done else 0:.1f}%",
        f"🚀 النسخة: {VERSION} (مفعل بالكامل)"
    ]
    return "\n".join(lines)


# =====================================================
# Telegram Handlers & Background Scanner
# =====================================================
def broadcast(text):
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        cur.execute("SELECT chat_id FROM subscribers")
        for row in cur.fetchall():
            try:
                requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": row[0], "text": text}, timeout=10)
            except Exception: pass


def scan_once():
    data = fetch_market_data()
    if not data: return
    for s in update_outcomes(data["ltf"]):
        icon = "✅" if s["r"] > 0 else "❌"
        broadcast(f'{icon} إغلاق صفقة #{s["id"]} ({s["dir"]}): النتيجة {s["result"]} ({s["r"]:+.2f}R)')
    
    if blocked_reason(): return
    sig = smc_signal(data)
    if sig["dir"] in ("BUY", "SELL") and not active_signal(sig["dir"]):
        rec, new = record_signal(sig, data)
        if new:
            broadcast(f"🔔 صفقة تجريبية جديدة #{rec['id']}\n\nالاتجاه: {sig['dir']}\nالدخول: {sig['entry']}\nالهدف: {sig['tp1']}\nالستوب: {sig['sl']}")


def scanner_loop():
    time.sleep(30)
    while True:
        try: scan_once()
        except Exception as e: print(f"Scanner error: {e}")
        time.sleep(SCAN_MINUTES * 60)


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 بوت التداول المؤسسي الشامل (v10)\n\n"
        "/analyze - تحليل فوري مع فلتر DXY\n"
        "/subscribe - تلقي التنبيهات\n"
        "/stats - تقرير الأداء والمحفظة التجريبية\n"
        "/pause 60 - إيقاف مؤقت\n"
        "/resume - استئناف"
    )


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = await update.message.reply_text("🔍 جاري الفحص المؤسسي (ICT KillZones + DXY + Gann)...")
        data = fetch_market_data()
        if not data:
            await msg.edit_text("❌ تعذّر جلب بيانات السوق.")
            return
        sig = smc_signal(data)
        await msg.edit_text(f"🎯 تحليل السوق:\nاتجاه الذهب: {sig['dir']}\nمؤشر الدولار DXY: {data['dxy_trend']}\nالسبب: {sig['why']}")
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(stats_digest())


async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO subscribers (chat_id) VALUES (?)", (update.effective_chat.id,))
        conn.commit()
    await update.message.reply_text("✅ تم الاشتراك بالتنبيهات بنجاح.")


async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mins = int(context.args[0]) if context.args else 60
    set_paused_until(time.time() + mins * 60)
    await update.message.reply_text(f"⏸ تم الإيقاف لمدة {mins} دقيقة.")


async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    set_paused_until(0)
    await update.message.reply_text("▶️ تم الاستئناف.")


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
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))

    print("Smart ICT Ultimate Institutional Bot is running...")
    app.run_polling(drop_pending_updates=True)
