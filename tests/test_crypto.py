"""ژورنال معاملات ارز دیجیتال (داده‌ها ساختگی‌اند)."""
import pytest

from civildesk import crypto, db


def _trade(**kw):
    base = {"opened_at": "2026-10-01 10:00", "exchange": "Bybit", "entry_type": "futures", "symbol": "btc/usdt",
            "strategy": "شکست", "side": "long", "entry_price": 60000, "margin": 100, "leverage": 10, "entry_fee": 0.055,
            "sl": 59400, "tp": 61800}
    return crypto.save_trade({**base, **kw})


def test_trade_metrics_partial_exits_and_balance():
    crypto.save_settings({"start_balance": 1000, "start_date": "2026-09-30"})
    t = _trade(exits=[{"price": 61800, "pct": 50, "type": "tp", "fee": 0.055, "at": "2026-10-01 14:00"},
                      {"price": 60000, "pct": 50, "type": "be", "fee": 0.055, "at": "2026-10-01 16:30"}])
    assert t["symbol"] == "BTCUSDT"
    j = crypto.journal()
    r = j["trades"][0]
    assert r["order_value"] == 1000 and abs(r["qty"] - 1000 / 60000) < 1e-12
    assert abs(r["planned_r"] - 3) < 1e-9 and abs(r["planned_sl_pct"] - 1) < 1e-9
    assert abs(r["position_to_acc"] - 10) < 1e-9  # ۱۰۰ از ۱۰۰۰
    assert abs(r["gross"] - 15) < 1e-9
    fees = 0.55 + (1000 / 60000 / 2) * (61800 + 60000) * 0.00055
    assert abs(r["fees"] - fees) < 1e-9 and abs(r["net"] - (15 - fees)) < 1e-9
    assert r["status"] == "closed" and r["result"] == "win" and r["duration_min"] == 390
    assert abs(r["new_balance"] - (1000 + 15 - fees)) < 1e-9
    assert abs(j["stats"]["balance"] - (1000 + 15 - fees)) < 1e-9


def test_stats_transfers_open_and_losses():
    crypto.save_settings({"start_balance": 500, "start_date": "2026-09-01"})
    crypto.save_transfer({"at": "2026-09-05 09:00", "amount": 500, "note": "واریز"})
    _trade(opened_at="2026-09-10 10:00", exits=[{"price": 59400, "pct": 100, "type": "sl", "at": "2026-09-10 11:00"}])
    _trade(opened_at="2026-09-11 10:00", side="short", sl=60600, tp=58200,
           exits=[{"price": 58200, "pct": 100, "type": "tp", "at": "2026-09-12 10:00"}])
    _trade(opened_at="2026-09-20 10:00", symbol="ETHUSDT", entry_price=3000, sl=None, tp=None)  # باز
    j = crypto.journal()
    st = j["stats"]
    assert st["total"] == 3 and st["closed"] == 2 and st["open"] == 1 and st["wins"] == 1 and st["losses"] == 1
    loss = next(r for r in j["trades"] if r["result"] == "loss")
    assert loss["balance_before"] == 1000  # شروع + واریز
    assert -1.2 < loss["actual_r"] < -0.9  # حدود −۱R
    short = next(r for r in j["trades"] if r["side"] == "short")
    assert 28.5 < short["net"] < 30  # ۳٪ حرکت به نفع شورت با اهرم ۱۰ روی ۱۰۰ دلار، منهای کارمزد
    assert st["win_rate"] == 50 and st["profit_factor"] > 2.5
    assert {g["key"] for g in st["by_side"]} == {"لانگ", "شورت"}
    assert st["deposits"] == 500 and "ETHUSDT" in crypto.get_settings()["pairs"]


def test_validation_and_jalali_dates():
    with pytest.raises(db.ValidationError):
        crypto.save_trade({"symbol": "BTCUSDT", "entry_price": 1})
    t = _trade(opened_at="۱۴۰۵/۰۷/۲۰ ۰۹:۱۵")
    assert t["opened_at"] == "2026-10-12 09:15"


def test_parse_screenshot_text():
    text = """BTCUSDT Perpetual
Long 20x Cross
Entry Price 62,150.5
Mark Price 62,400.1
Margin
150.25 USDT
Size 0.048
TP/SL
Stop Loss 61,500
Take Profit 64,000
2026-10-11 21:34:05"""
    f = crypto.parse_text(text)
    assert f["symbol"] == "BTCUSDT" and f["side"] == "long" and f["leverage"] == 20 and f["entry_type"] == "futures"
    assert f["entry_price"] == 62150.5 and f["margin"] == 150.25 and f["qty"] == 0.048
    assert f["sl"] == 61500 and f["tp"] == 64000 and f["opened_at"] == "2026-10-11 21:34"
    assert "pnl" not in f  # «Take Profit» سود/زیان نیست
    assert crypto.parse_text("Realized PnL\n-12.35 USDT")["pnl"] == -12.35
