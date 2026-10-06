"""خواندن اعداد فارسی سلول‌های جدول (مثل «۱,۵۰۱,۸۱۷,۵۲۶,۴۹۷» یا «۱۷.۶۰%»).

Tesseract اعداد طولانی فارسی را خیلی بد می‌خواند. اعداد فقط از چند شکل ساخته شده‌اند (۱۰ رقم، جداکننده، ممیز و ٪)
و رقم‌های فارسی به هم نمی‌چسبند؛ پس هر شکل جدا (مؤلفه‌ی همبند) پیدا و با الگوهای رقم که از چند قلم فارسی
ساخته شده‌اند (data/digit_templates.npz، با tools/build_digit_templates.py) مقایسه می‌شود.
«۰» فارسی و جداکننده‌ی هزارگان هر دو کوچک‌اند؛ با جایشان در سطر از هم جدا می‌شوند.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

SIZE = 20
TEMPLATES = Path(__file__).parent / "data" / "digit_templates.npz"
LABELS = "0123456789,.%"


# ───────────────────────── جداسازی شکل‌ها ─────────────────────────


def components(ink: np.ndarray) -> list[tuple[int, int, int, int, np.ndarray]]:
    """مؤلفه‌های همبند (۸-همسایگی): (x0, y0, x1, y1, ماسک)."""
    h, w = ink.shape
    labels = np.zeros((h, w), dtype=np.int32)
    out = []
    ys, xs = np.nonzero(ink)
    n = 0
    for sy, sx in zip(ys, xs):
        if labels[sy, sx]:
            continue
        n += 1
        stack = [(sy, sx)]
        labels[sy, sx] = n
        pts = []
        while stack:
            y, x = stack.pop()
            pts.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and ink[yy, xx] and not labels[yy, xx]:
                        labels[yy, xx] = n
                        stack.append((yy, xx))
        p = np.array(pts)
        y0, x0 = p.min(axis=0)
        y1, x1 = p.max(axis=0) + 1
        out.append((int(x0), int(y0), int(x1), int(y1), labels[y0:y1, x0:x1] == n))
    return out


def glyphs(ink: np.ndarray) -> list[dict]:
    """شکل‌های یک عدد از چپ به راست؛ اجزای یک نویسه (مثل نقطه‌های ٪) یکی می‌شوند."""
    comps = [c for c in components(ink) if c[4].sum() >= 2]
    if comps:  # لکه‌های ریز اسکن (خیلی کوچک‌تر از ارقام) حذف می‌شوند؛ «۰» از آن‌ها بزرگ‌تر است
        line_h = float(np.percentile([c[3] - c[1] for c in comps], 75))
        comps = [c for c in comps if c[4].sum() >= max(3, 0.012 * line_h * line_h) or (c[3] - c[1]) >= 0.3 * line_h]
    comps.sort(key=lambda c: c[0])
    merged: list[list] = []
    for c in comps:
        if merged:
            m = merged[-1]
            overlap = min(m[2], c[2]) - max(m[0], c[0])
            if overlap > 0.5 * min(m[2] - m[0], c[2] - c[0]):
                m[0], m[1], m[2], m[3] = min(m[0], c[0]), min(m[1], c[1]), max(m[2], c[2]), max(m[3], c[3])
                m[4].append(c)
                continue
        merged.append([c[0], c[1], c[2], c[3], [c]])
    out = []
    for x0, y0, x1, y1, parts in merged:
        mask = np.zeros((y1 - y0, x1 - x0), dtype=bool)
        for px0, py0, px1, py1, pm in parts:
            mask[py0 - y0: py1 - y0, px0 - x0: px1 - x0] |= pm
        out.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "mask": mask})
    return out


def _bitmap(mask: np.ndarray) -> np.ndarray:
    """ماسک شکل ← تصویر ۲۰×۲۰ با حفظ نسبت ابعاد (وسط‌چین)."""
    h, w = mask.shape
    s = max(h, w)
    sq = np.zeros((s, s), dtype=np.float32)
    sq[(s - h) // 2: (s - h) // 2 + h, (s - w) // 2: (s - w) // 2 + w] = mask
    idx = (np.arange(SIZE) + 0.5) * s / SIZE
    # میانگین‌گیری ساده‌ی بلوکی
    edges = np.linspace(0, s, SIZE + 1).astype(int)
    out = np.zeros((SIZE, SIZE), dtype=np.float32)
    for i in range(SIZE):
        for j in range(SIZE):
            blk = sq[edges[i]: max(edges[i + 1], edges[i] + 1), edges[j]: max(edges[j + 1], edges[j] + 1)]
            out[i, j] = blk.mean()
    del idx
    return out.ravel()


def features(gs: list[dict]) -> np.ndarray:
    """بردار ویژگی هر شکل: تصویر کوچک + نسبت ابعاد، ارتفاع نسبی و جای عمودی در سطر."""
    if not gs:
        return np.zeros((0, SIZE * SIZE + 3), dtype=np.float32)
    hs = np.array([g["y1"] - g["y0"] for g in gs], dtype=float)
    tall = hs >= 0.7 * hs.max()  # ارقام قد بلند (۱ تا ۹)؛ «۰» و جداکننده‌ها کوتاه‌اند
    line_h = float(np.median(hs[tall]))
    top = float(np.median([g["y0"] for g, t in zip(gs, tall) if t]))
    rows = []
    for g in gs:
        h, w = g["y1"] - g["y0"], g["x1"] - g["x0"]
        geo = [min(3.0, w / max(h, 1)) / 3, h / line_h, ((g["y0"] + g["y1"]) / 2 - top) / line_h]
        rows.append(np.concatenate([_bitmap(g["mask"]), np.array(geo, dtype=np.float32) * 4]))
    return np.array(rows, dtype=np.float32)


# ───────────────────────── تشخیص ─────────────────────────


@lru_cache(maxsize=1)
def _templates() -> tuple[np.ndarray, np.ndarray] | None:
    if not TEMPLATES.exists():
        return None
    d = np.load(TEMPLATES)
    return d["X"].astype(np.float32), d["y"]


def classify(feats: np.ndarray, k: int = 3, allowed: str | None = None) -> list[tuple[str, float]]:
    t = _templates()
    if t is None or not len(feats) or t[0].shape[1] != feats.shape[1]:
        return []
    X, y = t
    if allowed:
        keep = np.isin(y, list(allowed))
        X, y = X[keep], y[keep]
    out = []
    for f in feats:
        d = ((X - f) ** 2).sum(axis=1)
        idx = np.argpartition(d, k)[:k]
        idx = idx[np.argsort(d[idx])]
        votes: dict[str, float] = {}
        for i in idx:
            votes[str(y[i])] = votes.get(str(y[i]), 0) + 1 / (1e-3 + d[i])
        lab = max(votes, key=votes.get)
        out.append((lab, float(d[idx[0]])))
    return out


def read_number(gray: np.ndarray, amount: bool = False) -> str:
    """تصویر خاکستری یک سلول عددی ← متن با ارقام لاتین، مثل «1,501,817,526,497» یا «17.60%».

    amount=True: سلول مبلغ است (فقط رقم و جداکننده).
    """
    return read_number_conf(gray, amount)[0]


def read_number_conf(gray: np.ndarray, amount: bool = False) -> tuple[str, float]:
    """مثل read_number، به‌همراه بدترین فاصله‌ی شکل‌ها تا الگو (عدد بزرگ‌تر = نامطمئن‌تر)."""
    if _templates() is None or gray.size == 0:
        return "", 0.0
    ink = gray < min(160, int(gray.mean()) - 10 if gray.mean() > 170 else 128)
    gs = glyphs(ink)
    if not gs or len(gs) > 30:
        return "", 0.0
    feats = features(gs)
    res = classify(feats, allowed="0123456789,." if amount else None)
    if not res:
        return "", 0.0
    labs = [lab for lab, _ in res]
    small = [f[-2] / 4 < 0.55 for f in feats]  # ارتفاع نسبی کم: ۰، جداکننده یا ممیز
    return fix_small(labs, small), max(d for _, d in res)


def fix_small(labs: list[str], small: list[bool]) -> str:
    """«۰»، جداکننده و ممیز شکل‌های کوچک مشابهی دارند؛ قالب عدد تکلیفشان را روشن می‌کند.

    مبلغ: از راست هر چهارمین نویسه جداکننده است (1,234,567). درصد: ممیز قبل از دو رقم آخر (17.60%).
    """
    raw = "".join(labs)
    n = len(labs)
    if labs and labs[-1] == "%" and n >= 2:
        body = n - 1
        dot = body - 3 if body >= 4 and small[body - 3] else -1
        out = []
        for i in range(body):
            if i == dot:
                out.append(".")
            elif small[i] or labs[i] in ",.":
                out.append("0")
            else:
                out.append(labs[i])
        return "".join(out) + "%"
    if n >= 5 and any(lab in ",." for lab in labs):
        seps = {i for i in range(n) if (n - 1 - i) % 4 == 3}
        if all(small[i] or labs[i] in ",." for i in seps) and not any(labs[i] in ",." and i not in seps and not small[i] for i in range(n)):
            return "".join("," if i in seps else ("0" if small[i] or labs[i] in ",." else labs[i]) for i in range(n))
    return raw


def available() -> bool:
    return _templates() is not None
