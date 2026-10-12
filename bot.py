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
        self.wfile.write(b"Smart ICT Institutional Bot v10.2 is running!")

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

VERSION = "v10.2-fixed-precision"
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                balance REAL
            )
        """)
        conn.execute("INSERT OR IGNORE INTO paper_account (id, balance) VALUES (1, 10000.0)")
        conn.commit()


# =====================================================
# Market Data Fetching
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
    ltf = _binance_klines("PAXGUSDT", "15m", 150) or _yahoo_klines("GC=F", "15m", "5d")
    h1 = _binance_klines("PAXGUSDT", "1h", 500) or _yahoo_klines("GC=F", "60m", "3mo")
    d1 = _binance_klines("PAXGUSDT", "1d", 200) or _yahoo_klines("GC=F", "1d", "1y")
    dxy_candles = _yahoo_klines("DX-Y.NYB", "60m", "5d")

    if not _good(ltf) or not _good(h1):
        return None

    htf = h1[-150:]
    h4 = resample(h1, 4)[-120:]
    price = ltf[-1]["c"]

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
# Technical Analysis & Precise Signal Engine
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
    if hh and hl: return "BULL", highs, lows
    if lh and ll: return "BEAR", highs, lows
    return "RANGE", highs, lows


def find_fvgs(candles, lookback=40):
    res = []
    start = max(2, len(candles) - lookback)
    for i in range(start, len(candles)):
        a, c = candles[i - 2], candles[i]
        after = candles[i + 1 :]
        if c["l"] > a["h"]:
            res.append({"type": "BUY", "low": a["h"], "high": c["l"], "kind": "FVG"})
        if c["h"] < a["l"]:
            res.append({"type": "SELL", "low": c["h"], "high": a["l"], "kind": "FVG"})
    return res


def smc_signal(data: dict) -> dict:
    ltf, htf, price = data["ltf"], data["htf"], data["price"]
    a = atr(ltf)
    htf_struct, _, _ = structure(htf)
    ema50 = ema([c["c"] for c in htf][-100:], 50)
    
    facts = {"htf_structure": htf_struct}
    bias = "BUY" if htf_struct == "BULL" or price > ema50 else "SELL"

    # حساب دقيق ومحكم لمنطقة الدخول والأهداف بناءً على السعر الحالي تماماً
    if bias == "BUY":
        entry = round(price - 0.5 * a, 2) # شراء معلق تحت السعر الحالي بقليل أو فوري
        sl = round(entry - 1.5 * a, 2)
        tp1 = round(entry + 1.5 * (entry - sl), 2)
        tp2 = round(entry + 3.0 * (entry - sl), 2)
        mode = "دخول فوري / Limit"
    else:
        entry = round(price + 0.5 * a, 2) # بيع معلق فوق السعر الحالي بقليل
        sl = round(entry + 1.5 * a, 2)
        tp1 = round(entry - 1.5 * (sl - entry), 2)
        tp2 = round(entry - 3.0 * (sl - entry), 2)
        mode = "أمر معلّق Sell Limit"

    return {
        "dir": bias, "entry": entry, "mode": mode,
        "sl": sl, "tp1": tp1, "tp2": tp2,
        "rr": 1.5, "why": f"تحليل هيكلي دقيق حسب اتجاه الإطار الأكبر ({htf_struct}).", "facts": facts
    }


# =====================================================
# Database State & Management
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
    return False, "خارج فترات Kill Zones"


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
                for c in candles:
                    if c["t"] <= s["t0"]: continue
                    hit_sl = c["l"] <= s["sl"] if buy else c["h"] >= s["sl"]
                    hit_tp1 = c["h"] >= s["tp1"] if buy else c["l"] <= s["tp1"]
                    
                    if hit_sl:
                        final_r = -1.0
                        cur.execute("UPDATE signals SET status = 'closed', result = 'SL', r = ? WHERE id = ?", (final_r, s["id"]))
                        update_paper_balance(final_r * 100)
                        s_dict = dict(s)
                        s_dict.update({"status": "closed", "result": "SL", "r": final_r})
                        closed.append(s_dict)
                        break
                    elif hit_tp1:
                        final_r = 1.5
                        cur.execute("UPDATE signals SET status = 'closed', result = 'TP1', r = ? WHERE id = ?", (final_r, s["id"]))
                        update_paper_balance(final_r * 100)
                        s_dict = dict(s)
                        s_dict.update({"status": "closed", "result": "TP1", "r": final_r})
                        closed.append(s_dict)
                        break
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
        "📊 التقرير الإحصائي المؤسسي الدقيق",
        "────────────────────────",
        f"💰 رصيد المحفظة التجريبية: ${bal:.2f}",
        f"📈 إجمالي الصفقات المغلقة: {len(done)}",
        f"✅ رابحة: {len(wins)} | ❌ خاسرة: {len(done) - len(wins)}",
        f"🎯 نسبة النجاح: {(len(wins)/len(done)*100) if done else 0:.1f}%",
        f"🚀 النسخة: {VERSION}"
    ]
    return "\n".join(lines)


# =====================================================
# Telegram Handlers
# =====================================================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 بوت التداول المؤسسي الدقيق (v10.2)\n\n"
        "/analyze - تحليل فوري دقيق ومحسوب مع الأسعار الصحيحة\n"
        "/subscribe - تلقي التنبيهات\n"
        "/stats - تقرير الأداء والمحفظة التجريبية\n"
        "/pause 60 - إيقاف مؤقت\n"
        "/resume - استئناف"
    )


async def analyze_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = await update.message.reply_text("🔍 جاري حساب المستويات السعرية بدقة...")
        data = fetch_market_data()
        if not data:
            await msg.edit_text("❌ تعذّر جلب بيانات السوق.")
            return
        sig = smc_signal(data)
        
        lines = [
            f"🎯 تحليل دقيق ومحسوب ({data['symbol']})",
            "────────────────────────",
            f"📌 الاتجاه: {sig['dir']}",
            f"💵 السعر الحالي: {data['price']}",
            f"🌐 مؤشر الدولار DXY: {data['dxy_trend']}",
            f"📍 الدخول: {sig['entry']} ({sig.get('mode', '')})",
            f"🎯 TP1: {sig['tp1']}",
            f"🎯 TP2: {sig['tp2']}",
            f"🛑 SL: {sig['sl']}",
            f"⚖️ R:R: 1:{sig['rr']}",
            "",
            f"💡 السبب: {sig['why']}",
            "",
            f"🔖 النسخة: {VERSION}"
        ]
        await msg.edit_text("\n".join(lines))
    except Exception as e:
        await update.message.reply_text(f"خطأ: {e}")


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(stats_digest())


async def subscribe_cmd(update: Update, context: ContextTypes.DEFAULT_Type if hasattr(ContextTypes, 'DEFAULT_TYPE') else object):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO subscribers (chat_id) VALUES (?)", (update.effective_chat.id,))
        conn.commit()
    await update.message.reply_text("✅ تم الاشتراك بالتنبيهات الدقيقة بنجاح.")


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

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("analyze", analyze_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("subscribe", subscribe_cmd))
    app.add_handler(CommandHandler("pause", pause_cmd))
    app.add_handler(CommandHandler("resume", resume_cmd))

    print("Smart ICT Institutional Bot v10.2 is running...")
    app.run_polling(drop_pending_updates=True)
