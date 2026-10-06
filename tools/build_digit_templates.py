"""ساخت الگوهای رقم فارسی برای civildesk/digits.py از روی چند قلم.

    python tools/build_digit_templates.py FONT_DIR [--holdout NAME ...] [--out PATH]

هر قلم با چند اندازه، ضخامت و تاری رندر می‌شود؛ عددهای نمونه با همان روش برنامه تکه‌تکه و برچسب می‌خورند.
با --holdout، آن قلم‌ها کنار گذاشته و دقت روی‌شان گزارش می‌شود.
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from civildesk import digits  # noqa: E402

FA = "۰۱۲۳۴۵۶۷۸۹"
SEPS = {",": ",", "٬": ",", "،": ",", ".": ".", "٫": ".", "%": "%", "٪": "%"}


def samples(rng: random.Random, n: int) -> list[str]:
    out = []
    for _ in range(n):
        if rng.random() < 0.7:
            groups = [str(rng.randint(1, 999))] + [f"{rng.randint(0, 999):03d}" for _ in range(rng.randint(1, 4))]
            s = rng.choice(",,٬،") .join(groups)
        else:
            s = f"{rng.randint(0, 99)}{rng.choice('..٫')}{rng.randint(0, 99):02d}{rng.choice('%٪')}"
        out.append("".join(FA[int(c)] if c.isdigit() else c for c in s))
    return out


def label(s: str) -> list[str]:
    return [str(FA.index(c)) if c in FA else SEPS[c] for c in s]


def render(font_path: Path, text: str, size: int, stroke: int, blur: float, scale: float) -> np.ndarray:
    font = ImageFont.truetype(str(font_path), size)
    l, t, r, b = font.getbbox(text, stroke_width=stroke)
    im = Image.new("L", (r - l + 20, b - t + 20), 255)
    ImageDraw.Draw(im).text((10 - l, 10 - t), text, font=font, fill=0, stroke_width=stroke, stroke_fill=0)
    if scale != 1:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale)))).resize(im.size)
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    return np.asarray(im)


def build(fonts: list[Path], rng: random.Random, per_font: int) -> tuple[np.ndarray, np.ndarray, int]:
    X, y, skipped = [], [], 0
    for fp in fonts:
        for s in samples(rng, per_font):
            for size in (20, 30, 44):
                for stroke in (0, 1):
                    for blur, scale in ((0, 1), (0.7, 1), (0.6, 0.6)):
                        gray = render(fp, s, size, stroke, blur, scale)
                        gs = digits.glyphs(gray < 128)
                        labs = label(s)
                        if len(gs) != len(labs):
                            skipped += 1
                            continue
                        X.append(digits.features(gs))
                        y += labs
    return np.concatenate(X), np.array(y), skipped


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("font_dir")
    ap.add_argument("--holdout", nargs="*", default=[])
    ap.add_argument("--out", default=str(digits.TEMPLATES))
    ap.add_argument("--per-font", type=int, default=6)
    a = ap.parse_args()
    rng = random.Random(7)
    fonts = sorted(p for p in Path(a.font_dir).iterdir() if p.suffix.lower() in (".ttf", ".otf"))
    train = [f for f in fonts if not any(h.lower() in f.name.lower() for h in a.holdout)]
    test = [f for f in fonts if f not in train]
    X, y, skipped = build(train, rng, a.per_font)
    print(f"{len(train)} قلم، {len(y)} الگو، {skipped} نمونه‌ی ردشده")
    # تکرارهای خیلی نزدیک حذف می‌شوند تا فایل کوچک بماند
    Xq = np.round(X, 2)
    _, keep = np.unique(np.concatenate([Xq, y[:, None].astype("U1").view(np.uint32).astype(np.float32)], axis=1),
                        axis=0, return_index=True)
    X, y = X[np.sort(keep)], y[np.sort(keep)]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(a.out, X=X.astype(np.float16), y=y)
    print(f"ذخیره شد: {a.out} ({len(y)} الگو)")
    if test:
        digits.TEMPLATES = Path(a.out)
        digits._templates.cache_clear()
        Xt, yt, _ = build(test, random.Random(99), 6)
        pred = [lab for lab, _ in digits.classify(Xt)]
        acc = np.mean(np.array(pred) == yt)
        print(f"دقت روی قلم‌های کنارگذاشته ({', '.join(f.stem for f in test)}): {acc:.3f} روی {len(yt)} نویسه")
        conf: dict = {}
        for p, t in zip(pred, yt):
            if p != t:
                conf[(t, p)] = conf.get((t, p), 0) + 1
        print("بیشترین اشتباه‌ها:", sorted(conf.items(), key=lambda x: -x[1])[:8])


if __name__ == "__main__":
    main()
