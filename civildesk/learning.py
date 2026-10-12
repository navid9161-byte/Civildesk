"""یادگیری از اصلاح‌های دستی کاربر.

وقتی کاربر شماره، تاریخ یا دسته‌ی سندی را اصلاح می‌کند، جای آن مقدار در متن سند پیدا و «برچسب» کنارش
(مثلاً «شماره ثبت دبیرخانه»، «تاریخ صدور») به خاطر سپرده می‌شود؛ برای دسته هم سطر عنوان سند. از آن به بعد
اسناد مشابه با همین الگوها خوانده می‌شوند و اسناد قبلی هم یک بار دوباره بررسی می‌شوند.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from typing import Any

from . import db, textnorm

log = logging.getLogger(__name__)

KEY = "learned_rules"
FIELDS = ("doc_no", "doc_date")
GENERIC = {"شماره", "تاریخ", "نامه", "سند", "به", "مورخ", "ش", "ت"}
SEP = r"\s*[/ا|\\\-.]\s*"
_lock = threading.Lock()


def load() -> dict[str, list[dict[str, Any]]]:
    with db.connect() as conn:
        raw = db.kv_get(conn, KEY)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return {k: data.get(k, []) for k in (*FIELDS, "category")}


def _save(rules: dict) -> None:
    with db.connect() as conn:
        db.kv_set(conn, KEY, json.dumps(rules, ensure_ascii=False))


def _words(s: str) -> list[str]:
    """کلمه‌های برچسب: بدون رقم و نشانه، با حروف یکسان‌شده."""
    s = re.sub(r"[\d۰-۹٠-٩:：.\-/|\\()«»\"'،,؛*_]+", " ", s)
    return [w for w in s.split() if re.search(r"[آ-یa-zA-Z]", w)]


def _value_regex(field: str, value: str) -> re.Pattern | None:
    if field == "doc_no":
        groups = re.findall(r"\d+", value)
        if not groups or sum(len(g) for g in groups) < 3:
            return None
        alts = [SEP.join(map(re.escape, groups))]
        if len(groups) > 1:
            alts.append(SEP.join(map(re.escape, groups[::-1])))  # PDFهای راست‌به‌چپ
        return re.compile(r"(?<!\d)(?:" + "|".join(alts) + r")(?!\d)")
    m = re.fullmatch(r"(\d{4})/(\d{2})/(\d{2})", value or "")
    if not m:
        return None
    y, mo, d = m.group(1), m.group(2).lstrip("0"), m.group(3).lstrip("0")
    return re.compile(rf"(?<!\d)(?:{y}{SEP}0?{mo}{SEP}0?{d}|0?{d}{SEP}0?{mo}{SEP}{y})(?!\d)")


def label_before(text: str, pos: int) -> list[str]:
    """برچسب قبل از مقدار: سه کلمه‌ی آخرِ همان سطر، وگرنه سطر قبل (در جدول‌ها برچسب بالای مقدار است)."""
    line_start = text.rfind("\n", 0, pos) + 1
    words = _words(text[max(line_start, pos - 40): pos])[-3:]
    if not words:
        prev = text[:line_start].rstrip("\n")
        words = _words(prev[prev.rfind("\n") + 1:][-40:])[-3:]
    return words


def head_line(text: str) -> str | None:
    """عنوان سند: اولین سطر معنادار (دست‌کم دو کلمه) بدون رقم، برای یادگیری دسته."""
    for ln in text.split("\n")[:15]:
        w = _words(ln)
        if len(w) >= 2:
            return " ".join(w[:6])
    return None


def _bump(rules: list[dict], match: dict[str, Any]) -> None:
    for r in rules:
        if all(r.get(k) == v for k, v in match.items()):
            r["n"] = r.get("n", 1) + 1
            rules.remove(r)
            rules.insert(0, r)
            return
    rules.insert(0, {**match, "n": 1})
    del rules[60:]


def learn(text: str, changes: dict[str, Any], category: str | None) -> list[str]:
    """یادگیری از مقدارهای اصلاح‌شده؛ خروجی: نام فیلدهایی که الگویشان یاد گرفته شد."""
    from .archive import _prep, strip_header

    body = _prep(strip_header(text) if text.startswith("سربرگ") else text)
    full = _prep(text)
    learned = []
    with _lock:
        rules = load()
        for field in FIELDS:
            value = changes.get(field)
            rx = _value_regex(field, value) if value else None
            if not rx:
                continue
            m = rx.search(full)
            if not m:
                continue
            words = label_before(full, m.start())
            if words:
                _bump(rules[field], {"label": " ".join(words), "cat": category})
                learned.append(field)
        cat = changes.get("category")
        head = head_line(body)
        if cat and head:
            _bump(rules["category"], {"head": head, "cat": cat})
            learned.append("category")
        if learned:
            _save(rules)
    return learned


def _label_regex(label: str) -> re.Pattern:
    return re.compile(r"[\s‌]*".join(re.escape(w) for w in label.split()))


def category_for(text: str) -> str | None:
    from .archive import _prep, strip_header

    rules = load()["category"]
    if not rules:
        return None
    head = head_line(_prep(strip_header(text)))
    norm = textnorm.normalize(head or "")
    for r in rules:
        if norm and textnorm.normalize(r["head"]) == norm:
            return r["cat"]
    return None


def apply(text: str, info: dict[str, Any], category: str) -> None:
    """مقدارهایی که با الگوهای یادگرفته پیدا می‌شوند؛ الگوی خاص (نه فقط «شماره»/«تاریخ») مقدار خودکار را عوض می‌کند."""
    from .archive import _DATE_RE, _date_from_match

    rules = load()
    top = text[:6000]
    for field in FIELDS:
        for r in rules[field]:
            if r.get("cat") and r["cat"] != category:
                continue  # «شماره قرارداد» برای قرارداد درست است، نه برای نامه
            generic = set(r["label"].split()) <= GENERIC
            if generic and info.get(field) and not (info[field].get("guessed")):
                continue
            found = None
            for m in _label_regex(r["label"]).finditer(top):
                after = top[m.end(): m.end() + 60]
                if field == "doc_no":
                    vm = re.match(r"[\s:.\n]*([0-9][\w/\-.]{0,24}(?:\s*/\s*[\w\-.]{1,12}){0,3})", after)
                    if vm:
                        v = re.sub(r"\s+", "", vm.group(1)).strip("/.-")
                        if sum(c.isdigit() for c in v) >= 3 and not _DATE_RE.fullmatch(v):
                            found = (m.end() + vm.start(1), v)
                else:
                    dm = _DATE_RE.search(after)
                    if dm and (d := _date_from_match(dm)):
                        found = (m.end() + dm.start(), d)
                if found:
                    break
            if found:
                info[field] = {"pos": found[0], "value": found[1], "learned": True}
                break


def reapply_async(exclude: int | None = None) -> None:
    """بعد از یادگیری: اسناد دیگر یک بار دوباره تحلیل می‌شوند (فقط از روی متن ذخیره‌شده، بدون OCR)."""
    def run() -> None:
        from . import archive

        with db.connect() as conn:
            ids = [r["id"] for r in conn.execute(
                "SELECT id FROM documents WHERE status='ready' AND id != ? ORDER BY id DESC LIMIT 500", (exclude or 0,))]
        changed = 0
        for i in ids:
            try:
                with db.connect() as conn:
                    before = conn.execute("SELECT doc_no, doc_date, category FROM documents WHERE id=?", (i,)).fetchone()
                archive.analyze(i)
                with db.connect() as conn:
                    after = conn.execute("SELECT doc_no, doc_date, category FROM documents WHERE id=?", (i,)).fetchone()
                changed += tuple(before) != tuple(after)
            except Exception:
                log.exception("بازبینی سند %s با الگوهای جدید ناموفق بود", i)
        if changed:
            log.info("الگوهای یادگرفته %d سند دیگر را اصلاح کرد", changed)

    if os.getenv("CIVILDESK_NO_WORKER"):  # آزمون‌ها: هم‌زمان
        run()
    else:
        threading.Thread(target=run, daemon=True, name="relearn").start()
