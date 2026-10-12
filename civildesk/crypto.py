"""مدیریت معاملات ارز دیجیتال: ژورنال معاملات به سبک «Trade City Pro Journal».

ساختار همان فایل اکسل کاربر است: ورود (صرافی، جفت‌ارز، استراتژی، جهت، قیمت، مارجین، اهرم، کارمزد، حالت ذهنی،
چک‌لیست)، مدیریت ریسک (حد ضرر، حد سود، درصد ریسک حساب، R برنامه‌ریزی‌شده)، خروج (یک یا چند خروج پله‌ای با قیمت،
درصد بسته‌شده، نوع خروج، کارمزد و زمان) و نتیجه (سود/زیان ناخالص و خالص، کارمزد، R واقعی، تغییر حساب، موجودی
جدید، مدت معامله)، به‌اضافه‌ی واریز/برداشت و تنظیمات (فهرست‌های پایه).

همه‌ی مبالغ به دلار (USDT) و زمان‌ها میلادی‌اند (مثل صرافی‌ها)؛ در صفحه تاریخ شمسی هم نشان داده می‌شود.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from pathlib import Path
from typing import Any

from . import db, jalali

SETTINGS_KEY = "crypto_settings"
DEFAULT_SETTINGS: dict[str, Any] = {
    "start_date": None, "start_balance": 0.0, "risk_per_trade": 1.0,
    "pairs": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"],
    "exchanges": ["Bybit", "Binance", "BingX", "Toobit", "LBank"],
    "fees": [0.02, 0.05, 0.055, 0.1],
    "mental_states": ["آرام", "مطمئن", "عجول", "ترس", "طمع", "خسته", "انتقام‌جو"],
    "strategies": [],
    "mistakes": ["ورود زودهنگام", "جابه‌جایی حد ضرر", "حجم بیش از حد", "خروج زودهنگام", "معامله‌ی خارج از استراتژی"],
    "trackings": [],
    "check_items": ["روند تایم بالاتر", "حد ضرر مشخص", "ریسک به ریوارد مناسب", "تأیید استراتژی"],
    "time_frames": ["1m", "5m", "15m", "1h", "4h", "1D"],
}
EXIT_TYPES = {"tp": "حد سود", "sl": "حد ضرر", "manual": "دستی", "be": "سربه‌سر", "liq": "لیکوئید", "trail": "حد ضرر متحرک"}
TRADE_FIELDS = ("tag", "opened_at", "exchange", "entry_type", "symbol", "strategy", "side", "entry_price", "margin",
                "leverage", "order_type", "time_frame", "entry_fee", "entry_mood", "checklist", "sl", "tp", "exits",
                "rating", "tracking", "mistake", "memo", "shots")
_FLOATS = {"entry_price", "margin", "leverage", "entry_fee", "sl", "tp"}
_JSON = {"checklist", "exits", "shots"}


def migrate(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS crypto_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tag TEXT, opened_at TEXT NOT NULL, exchange TEXT, entry_type TEXT,
            symbol TEXT NOT NULL, strategy TEXT, side TEXT NOT NULL, entry_price REAL NOT NULL, margin REAL NOT NULL,
            leverage REAL DEFAULT 1, order_type TEXT, time_frame TEXT, entry_fee REAL DEFAULT 0, entry_mood TEXT,
            checklist TEXT, sl REAL, tp REAL, exits TEXT, rating INTEGER, tracking TEXT, mistake TEXT, memo TEXT,
            shots TEXT, created_at TEXT, updated_at TEXT)""")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS crypto_transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, amount REAL NOT NULL, exchange TEXT, note TEXT,
            shot TEXT, created_at TEXT)""")


# ───────────────────────── تنظیمات ─────────────────────────


def get_settings() -> dict[str, Any]:
    with db.connect() as conn:
        raw = db.kv_get(conn, SETTINGS_KEY)
    s = dict(DEFAULT_SETTINGS)
    if raw:
        try:
            s.update(json.loads(raw))
        except ValueError:
            pass
    return s


def save_settings(data: dict[str, Any]) -> dict[str, Any]:
    s = get_settings()
    for k in DEFAULT_SETTINGS:
        if k not in data:
            continue
        v = data[k]
        if isinstance(DEFAULT_SETTINGS[k], list):
            if isinstance(v, str):
                v = [x.strip() for x in re.split(r"[\n،,]+", v) if x.strip()]
            v = [(_num(x) if k == "fees" else str(x).strip()) for x in v if str(x).strip()]
        elif k in ("start_balance", "risk_per_trade"):
            v = _num(v) or 0.0
        elif k == "start_date":
            v = _when(v) if v else None
        s[k] = v
    with db.connect() as conn:
        db.kv_set(conn, SETTINGS_KEY, json.dumps(s, ensure_ascii=False))
    return s


# ───────────────────────── ورودی‌ها ─────────────────────────


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    t = str(v).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬", "0123456789.,")).replace(",", "").strip()
    try:
        return float(t)
    except ValueError:
        raise db.ValidationError(f"عدد نامعتبر: {v}") from None


def _when(v: Any) -> str:
    """زمان به قالب «YYYY-MM-DD HH:MM» (میلادی)؛ تاریخ شمسی هم پذیرفته می‌شود."""
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    t = str(v or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")).strip().replace("T", " ")
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", t)
    if not m:
        raise db.ValidationError(f"زمان نامعتبر: {v} (مثلاً 2026-10-12 14:30)")
    y, mo, d = int(m[1]), int(m[2]), int(m[3])
    hh, mm = (int(m[4]), int(m[5])) if m[4] else (0, 0)
    if y < 1700:  # شمسی
        y, mo, d = jalali.jalali_to_gregorian(y, mo, d)
    try:
        return dt.datetime(y, mo, d, hh, mm).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        raise db.ValidationError(f"زمان نامعتبر: {v}") from None


def _clean_trade(data: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in TRADE_FIELDS:
        if k not in data:
            continue
        v = data[k]
        if k in _FLOATS:
            v = _num(v)
        elif k == "opened_at":
            v = _when(v)
        elif k == "side":
            v = "short" if str(v).lower() in ("short", "sell", "فروش", "شورت") else "long"
        elif k == "symbol":
            v = re.sub(r"[^A-Z0-9]", "", str(v or "").upper())
        elif k == "rating":
            v = int(_num(v) or 0) or None
        elif k == "exits":
            exits = []
            for e in v or []:
                price = _num(e.get("price"))
                if price is None:
                    continue
                exits.append({"price": price, "pct": min(100.0, max(0.0, _num(e.get("pct")) or 100.0)),
                              "type": e.get("type") if e.get("type") in EXIT_TYPES else "manual",
                              "fee": _num(e.get("fee")) if e.get("fee") not in (None, "") else None,
                              "at": _when(e["at"]) if e.get("at") else None, "mood": (e.get("mood") or None)})
            v = exits
        elif k in ("checklist", "shots"):
            v = [str(x) for x in (v or []) if str(x).strip()]
        else:
            v = (str(v).strip() or None) if v is not None else None
        out[k] = v
    if not partial:
        for req, label in (("opened_at", "زمان ورود"), ("symbol", "جفت‌ارز"), ("entry_price", "قیمت ورود"), ("margin", "مارجین")):
            if not out.get(req):
                raise db.ValidationError(f"«{label}» لازم است")
        out.setdefault("side", "long")
    if out.get("leverage") is not None and out["leverage"] <= 0:
        raise db.ValidationError("اهرم باید بزرگ‌تر از صفر باشد")
    if out.get("entry_type") == "spot":
        out["leverage"] = 1.0
    return out


def _row(r) -> dict[str, Any]:
    d = dict(r)
    for k in _JSON:
        if k in d:
            d[k] = json.loads(d[k] or "[]")
    return d


def save_trade(data: dict[str, Any], trade_id: int | None = None) -> dict[str, Any]:
    vals = _clean_trade(data, partial=trade_id is not None)
    for k in _JSON:
        if k in vals:
            vals[k] = json.dumps(vals[k], ensure_ascii=False)
    now = db.now_str()
    with db.connect() as conn:
        if trade_id:
            if not conn.execute("SELECT 1 FROM crypto_trades WHERE id=?", (trade_id,)).fetchone():
                raise db.NotFound("معامله پیدا نشد")
            if vals:
                conn.execute(f"UPDATE crypto_trades SET {', '.join(f'{k}=?' for k in vals)}, updated_at=? WHERE id=?",
                             (*vals.values(), now, trade_id))
        else:
            vals["created_at"] = vals["updated_at"] = now
            trade_id = conn.execute(f"INSERT INTO crypto_trades ({', '.join(vals)}) VALUES ({', '.join('?' * len(vals))})",
                                    tuple(vals.values())).lastrowid
        row = _row(conn.execute("SELECT * FROM crypto_trades WHERE id=?", (trade_id,)).fetchone())
    _remember_lists(vals)
    return row


def _remember_lists(vals: dict[str, Any]) -> None:
    """جفت‌ارز، صرافی و استراتژی جدید به فهرست‌های تنظیمات اضافه می‌شود (برای انتخاب سریع بعدی)."""
    s = get_settings()
    changed = False
    for field, key in (("symbol", "pairs"), ("exchange", "exchanges"), ("strategy", "strategies")):
        v = vals.get(field)
        if v and v not in s[key]:
            s[key] = [*s[key], v]
            changed = True
    if changed:
        with db.connect() as conn:
            db.kv_set(conn, SETTINGS_KEY, json.dumps(s, ensure_ascii=False))


def delete_trade(trade_id: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM crypto_trades WHERE id=?", (trade_id,))


def save_transfer(data: dict[str, Any], transfer_id: int | None = None) -> dict[str, Any]:
    amount = _num(data.get("amount"))
    if not amount:
        raise db.ValidationError("مبلغ لازم است (واریز مثبت، برداشت منفی)")
    vals = {"at": _when(data.get("at") or dt.datetime.now()), "amount": amount,
            "exchange": (data.get("exchange") or None), "note": (data.get("note") or None), "shot": data.get("shot")}
    with db.connect() as conn:
        if transfer_id:
            conn.execute("UPDATE crypto_transfers SET at=?, amount=?, exchange=?, note=?, shot=? WHERE id=?",
                         (*vals.values(), transfer_id))
        else:
            transfer_id = conn.execute("INSERT INTO crypto_transfers (at, amount, exchange, note, shot, created_at) "
                                       "VALUES (?, ?, ?, ?, ?, ?)", (*vals.values(), db.now_str())).lastrowid
        return dict(conn.execute("SELECT * FROM crypto_transfers WHERE id=?", (transfer_id,)).fetchone())


def delete_transfer(transfer_id: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM crypto_transfers WHERE id=?", (transfer_id,))


# ───────────────────────── محاسبات ─────────────────────────

WEEKDAYS = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه"]


def _parse(t: str | None) -> dt.datetime | None:
    return dt.datetime.strptime(t, "%Y-%m-%d %H:%M") if t else None


def metrics(t: dict[str, Any], balance: float) -> dict[str, Any]:
    """ستون‌های محاسبه‌شده‌ی ژورنال برای یک معامله (balance: موجودی حساب هنگام ورود)."""
    s = -1 if t["side"] == "short" else 1
    entry, margin, lev = t["entry_price"], t["margin"], t.get("leverage") or 1
    value = margin * lev
    qty = value / entry if entry else 0
    fee_in = (t.get("entry_fee") or 0) / 100
    sl, tp = t.get("sl"), t.get("tp")
    sl_pct = abs(entry - sl) / entry if sl else None
    risk = value * sl_pct + value * fee_in if sl_pct else None  # ضرر در صورت خوردن حد ضرر (به‌اضافه‌ی کارمزد ورود)
    m: dict[str, Any] = {
        "order_value": value, "qty": qty,
        "position_to_acc": 100 * margin / balance if balance > 0 else None,
        "planned_sl_pct": 100 * sl_pct if sl_pct else None,
        "acc_risked": 100 * risk / balance if risk and balance > 0 else None,
        "planned_r": abs(tp - entry) / abs(entry - sl) if sl and tp and entry != sl else None,
    }
    exits = t.get("exits") or []
    closed = min(100.0, sum(e["pct"] for e in exits))
    gross, fees = 0.0, 0.0
    for e in exits:
        part = qty * e["pct"] / 100
        gross += part * (e["price"] - entry) * s
        fee_out = (e["fee"] if e.get("fee") is not None else (t.get("entry_fee") or 0)) / 100
        fees += part * e["price"] * fee_out + value * fee_in * e["pct"] / 100
    net = gross - fees
    last = max((e["at"] for e in exits if e.get("at")), default=None)
    status = "open" if closed < 99.999 else "closed"
    if not exits:
        status = "open"
    opened, closed_at = _parse(t["opened_at"]), _parse(last)
    m.update({
        "closed_pct": closed, "status": status, "gross": gross if exits else None, "fees": fees if exits else None,
        "net": net if exits else None, "exit_price": (sum(e["price"] * e["pct"] for e in exits) / closed) if closed else None,
        "actual_r": net / risk if exits and risk else None,
        "gain_pct": 100 * net / (margin * closed / 100) if exits and margin and closed else None,
        "acc_change": 100 * net / balance if exits and balance > 0 else None,
        "closed_at": last,
        "duration_min": int((closed_at - opened).total_seconds() // 60) if closed_at and opened and status == "closed" else None,
        "weekday": WEEKDAYS[opened.weekday()] if opened else None,
        "result": None,
    })
    if status == "closed":
        # سربه‌سر: حرکت قیمت تقریباً صفر (فقط کارمزد پرداخت شده)
        m["result"] = "be" if abs(gross) <= max(0.001 * margin, fees) and abs(net) <= fees + 0.001 * margin else (
            "win" if net > 0 else "loss")
    return m


def journal() -> dict[str, Any]:
    """همه‌ی معاملات و انتقال‌ها با ستون‌های محاسبه‌شده، موجودی پله‌به‌پله و آمار کلی."""
    s = get_settings()
    with db.connect() as conn:
        trades = [_row(r) for r in conn.execute("SELECT * FROM crypto_trades ORDER BY opened_at, id")]
        transfers = [dict(r) for r in conn.execute("SELECT * FROM crypto_transfers ORDER BY at, id")]
    start = float(s.get("start_balance") or 0)
    start_at = s.get("start_date") or "0000-00-00 00:00"
    # رویدادهای تغییر موجودی: انتقال‌ها و هر خروج (سود/زیان خالص همان خروج)
    events: list[tuple[str, int, float, str, int]] = [(x["at"], 0, x["amount"], "transfer", x["id"]) for x in transfers]
    for t in trades:
        for e in t.get("exits") or []:
            net = metrics(dict(t, exits=[e]), 1.0)["net"] or 0.0  # سود/زیان خالص همین خروج
            events.append((e.get("at") or t["opened_at"], 1, net, "exit", t["id"]))
    events.sort(key=lambda x: (x[0], x[1]))

    def balance_at(when: str) -> float:
        b = start
        for at, _, amount, _, _ in events:
            if at <= when:
                b += amount
            else:
                break
        return b

    rows = []
    for t in trades:
        bal = balance_at(t["opened_at"]) if t["opened_at"] >= start_at else start
        m = metrics(t, bal)
        m["balance_before"] = bal
        m["new_balance"] = bal + (m["net"] or 0) if m["status"] == "closed" else None
        rows.append({**t, **m})
    equity = [{"at": start_at if s.get("start_date") else (events[0][0] if events else None), "balance": start, "kind": "start"}]
    b = start
    for at, _, amount, kind, ref in events:
        b += amount
        equity.append({"at": at, "balance": b, "kind": kind, "ref": ref, "change": amount})
    stats = _stats(rows, transfers, start, b, equity)
    rows.sort(key=lambda r: (r["opened_at"], r["id"]), reverse=True)
    return {"settings": s, "trades": rows, "transfers": list(reversed(transfers)), "stats": stats, "equity": equity,
            "exit_types": EXIT_TYPES}


def _group(rows: list[dict], key) -> list[dict[str, Any]]:
    groups: dict[str, list[dict]] = {}
    for r in rows:
        k = key(r)
        for kk in (k if isinstance(k, list) else [k]):
            groups.setdefault(kk or "—", []).append(r)
    out = []
    for k, rs in groups.items():
        wins = sum(1 for r in rs if r["result"] == "win")
        losses = sum(1 for r in rs if r["result"] == "loss")
        out.append({"key": k, "n": len(rs), "wins": wins, "losses": losses,
                    "win_rate": 100 * wins / (wins + losses) if wins + losses else None,
                    "net": sum(r["net"] or 0 for r in rs),
                    "avg_r": _avg([r["actual_r"] for r in rs if r["actual_r"] is not None])})
    return sorted(out, key=lambda g: -g["n"])


def _avg(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _stats(rows: list[dict], transfers: list[dict], start: float, balance: float, equity: list[dict]) -> dict[str, Any]:
    closed = [r for r in rows if r["status"] == "closed"]
    wins = [r for r in closed if r["result"] == "win"]
    losses = [r for r in closed if r["result"] == "loss"]
    realized = sum(r["net"] or 0 for r in rows)
    gross_win, gross_loss = sum(r["net"] for r in wins), -sum(r["net"] for r in losses)
    peak, max_dd = None, 0.0
    for p in equity:  # بیشترین افت از قله (فقط اثر معاملات، نه برداشت‌ها)
        if p["kind"] == "transfer":
            if peak is not None:
                peak += p["change"]
            continue
        peak = p["balance"] if peak is None or p["balance"] > peak else peak
        if peak > 0:
            max_dd = max(max_dd, 100 * (peak - p["balance"]) / peak)
    streak = best = worst = 0
    for r in sorted(closed, key=lambda r: r["closed_at"] or r["opened_at"]):
        if r["result"] == "win":
            streak = streak + 1 if streak > 0 else 1
        elif r["result"] == "loss":
            streak = streak - 1 if streak < 0 else -1
        else:
            streak = 0
        best, worst = max(best, streak), min(worst, streak)
    deposits = sum(x["amount"] for x in transfers if x["amount"] > 0)
    withdrawals = -sum(x["amount"] for x in transfers if x["amount"] < 0)
    month = lambda r: (r["closed_at"] or r["opened_at"])[:7]  # noqa: E731
    return {
        "start_balance": start, "balance": balance, "realized": realized, "deposits": deposits, "withdrawals": withdrawals,
        "total": len(rows), "closed": len(closed), "open": len(rows) - len(closed), "wins": len(wins), "losses": len(losses),
        "breakeven": len(closed) - len(wins) - len(losses),
        "win_rate": 100 * len(wins) / (len(wins) + len(losses)) if wins or losses else None,
        "profit_factor": gross_win / gross_loss if gross_loss else None,
        "avg_win": _avg([r["net"] for r in wins]), "avg_loss": _avg([r["net"] for r in losses]),
        "avg_r": _avg([r["actual_r"] for r in closed if r["actual_r"] is not None]),
        "expectancy": realized / len(closed) if closed else None,
        "best": max(closed, key=lambda r: r["net"])["id"] if closed else None,
        "worst": min(closed, key=lambda r: r["net"])["id"] if closed else None,
        "fees": sum(r["fees"] or 0 for r in rows), "max_drawdown": max_dd,
        "return_pct": 100 * realized / (start + deposits) if start + deposits > 0 else None,
        "win_streak": best, "loss_streak": -worst,
        "avg_duration_min": _avg([r["duration_min"] for r in closed if r["duration_min"] is not None]),
        "by_symbol": _group(closed, lambda r: r["symbol"]),
        "by_strategy": _group(closed, lambda r: r.get("strategy")),
        "by_side": _group(closed, lambda r: "لانگ" if r["side"] == "long" else "شورت"),
        "by_weekday": _group(closed, lambda r: r["weekday"]),
        "by_month": sorted(_group(closed, month), key=lambda g: g["key"]),
        "by_exchange": _group(closed, lambda r: r.get("exchange")),
        "by_mistake": _group([r for r in closed if r.get("mistake")], lambda r: [m.strip() for m in r["mistake"].split("،") if m.strip()]),
        "by_mood": _group(closed, lambda r: r.get("entry_mood")),
        "by_exit": _group(closed, lambda r: EXIT_TYPES.get((r["exits"][-1] if r["exits"] else {}).get("type"), "—")),
    }


# ───────────────────────── خواندن عکس معامله ─────────────────────────

_SYMBOL = re.compile(r"\b([A-Z0-9]{2,12})\s*[/\-]?\s*(USDT|USDC|BUSD|FDUSD|USD|IRT|TMN|BTC|ETH)\b(?:\s*(?:PERP|Perpetual))?")
_NUMBER = r"([-+]?\d[\d,]*(?:\.\d+)?)"
# برچسب‌ها ← فیلد (انگلیسی صرافی‌های خارجی و فارسی صرافی‌های داخلی)
_LABELS: list[tuple[str, str]] = [
    ("entry_price", r"(?:avg\.?\s*)?(?:entry|open(?:ing)?|filled|average|avg)\s*price|قیمت\s*(?:ورود|خرید|میانگین)|میانگین\s*قیمت"),
    ("exit_price", r"(?:close|closing|exit|avg\.?\s*close)\s*price|قیمت\s*(?:خروج|فروش|بستن)"),
    ("margin", r"(?:initial\s*)?margin(?!\s*ratio)|مارجین|وجه\s*تضمین"),
    ("qty", r"(?:qty|quantity|size|position\s*size|amount|filled)(?!\s*price)|مقدار|حجم"),
    ("sl", r"stop\s*loss|\bSL\b|حد\s*ضرر"),
    ("tp", r"take\s*profit|\bTP\b|حد\s*سود"),
    ("pnl", r"(?:realized|closed|net|unrealized)?\s*\bp\s*[&n]\s*l\b|(?<!take )(?<!take)\bprofit\b(?!\s*price)|سود\s*و\s*زیان|(?<!حد\s)سود\s*(?:خالص)?(?!\s*(?:حد|هدف))"),
    ("fee", r"(?:trading\s*)?fees?|کارمزد"),
]
_TIME = re.compile(r"(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})[\sT,]+(\d{1,2}):(\d{2})")
_TIME_J = re.compile(r"(14\d\d)[-/.](\d{1,2})[-/.](\d{1,2})(?:[\s,،-]+(\d{1,2}):(\d{2}))?")


def shots_dir() -> Path:
    from .documents import docs_dir

    d = docs_dir() / "crypto"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_shot(data: bytes, filename: str) -> str:
    ext = (Path(filename).suffix or ".png").lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        raise db.ValidationError("فقط عکس (png، jpg، webp) پذیرفته می‌شود")
    name = f"{uuid.uuid4().hex}{ext}"
    (shots_dir() / name).write_bytes(data)
    return name


def shot_path(name: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.(png|jpe?g|webp|bmp)", name):
        raise db.NotFound("عکس پیدا نشد")
    p = shots_dir() / name
    if not p.exists():
        raise db.NotFound("عکس پیدا نشد")
    return p


def _ocr(path: Path) -> str:
    from PIL import Image, ImageOps

    from . import documents

    if not documents.ocr_available():
        return ""
    img = Image.open(path).convert("L")
    if img.width < 1400:  # عکس‌های گوشی: بزرگ‌تر برای خوانش بهتر رقم‌های ریز
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    if sum(img.resize((64, 64)).getdata()) / 4096 < 110:  # تم تیره‌ی صرافی‌ها ← متن تیره روی زمینه‌ی روشن
        img = ImageOps.invert(img)
    tmp = path.with_suffix(".ocr.png")
    img.save(tmp)
    try:
        texts = [documents._tesseract(str(tmp), "eng", "6")]
        try:
            texts.append(documents._tesseract(str(tmp), "fas", "6"))
        except Exception:
            pass
        return "\n".join(texts)
    finally:
        tmp.unlink(missing_ok=True)


def _first_number(s: str) -> float | None:
    m = re.search(_NUMBER, s.replace(" ", "") if len(s) < 40 else s)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_text(text: str) -> dict[str, Any]:
    """حدس فیلدهای معامله از متن OCR عکس؛ هرچه پیدا نشد خالی می‌ماند تا کاربر کامل کند."""
    t = text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٫٬", "0123456789.,"))
    lines = [ln.strip() for ln in t.split("\n") if ln.strip()]
    out: dict[str, Any] = {}
    sm = _SYMBOL.search(t.upper())
    if sm:
        out["symbol"] = sm.group(1) + ("USDT" if sm.group(2) in ("USDT", "USD") else sm.group(2))
    low = t.lower()
    if re.search(r"\bshort\b|sell\s*/?\s*short|open\s*short|فروش|شورت", low):
        out["side"] = "short"
    elif re.search(r"\blong\b|buy\s*/?\s*long|open\s*long|خرید|لانگ", low):
        out["side"] = "long"
    lm = re.search(r"(?<![\d.])(\d{1,3})(?:\.\d+)?\s*[x×X](?![a-z])", t) or re.search(r"(?:leverage|اهرم)\D{0,6}(\d{1,3})", low)
    if lm:
        out["leverage"] = float(lm.group(1))
    if re.search(r"\bspot\b|اسپات", low):
        out["entry_type"] = "spot"
    elif re.search(r"perp|futures|فیوچرز|cross|isolated", low):
        out["entry_type"] = "futures"
    for i, ln in enumerate(lines):
        for field, pat in _LABELS:
            if field in out:
                continue
            m = re.search(pat, ln, re.I)
            if not m:
                continue
            rest = ln[m.end():]
            v = _first_number(rest)
            if v is None and i + 1 < len(lines) and re.match(r"\s*[-+$]?\s*\d", lines[i + 1]):
                v = _first_number(lines[i + 1])  # مقدار زیر برچسب (چیدمان ستونی)، نه سطر برچسب بعدی
            if v is not None:
                out[field] = v
    times = [_TIME.search(t)]
    if times[0]:
        y, mo, d, hh, mm = (int(x) for x in times[0].groups())
        try:
            out["opened_at"] = dt.datetime(y, mo, d, hh, mm).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            pass
    elif (jm := _TIME_J.search(t)):
        try:
            out["opened_at"] = _when(f"{jm[1]}/{jm[2]}/{jm[3]} {jm[4] or '00'}:{jm[5] or '00'}")
        except db.ValidationError:
            pass
    # قیمت ورود و مقدار ولی بدون مارجین: مارجین = ارزش ÷ اهرم
    if "margin" not in out and out.get("entry_price") and out.get("qty"):
        out["margin"] = round(out["entry_price"] * out["qty"] / (out.get("leverage") or 1), 4)
    for k in ("exchange",):
        for name in DEFAULT_SETTINGS["exchanges"] + ["Nobitex", "Wallex", "Ramzinex", "OKX", "MEXC", "KuCoin", "Bitunix",
                                                     "نوبیتکس", "والکس", "رمزینکس"]:
            if name.lower() in low:
                out[k] = name
                break
    return out


def scan(data: bytes, filename: str) -> dict[str, Any]:
    """عکس معامله ← پیش‌نویس فرم (ذخیره نمی‌شود تا کاربر تأیید کند)."""
    name = save_shot(data, filename)
    text = _ocr(shot_path(name))
    return {"shot": name, "fields": parse_text(text), "text": text[:4000]}
