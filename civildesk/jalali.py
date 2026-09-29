"""تبدیل تاریخ میلادی ↔ شمسی (الگوریتم محاسباتی، بدون وابستگی خارجی).

همه‌ی تاریخ‌ها در پایگاه داده به صورت رشته‌ی شمسی «YYYY/MM/DD» (و زمان «YYYY/MM/DD HH:MM»)
ذخیره می‌شوند؛ چون صفرِ پیشوند دارند، مقایسه‌ی رشته‌ای همان ترتیب زمانی را می‌دهد.
"""
from __future__ import annotations

import datetime as dt
import re

WEEKDAYS_FA = ["دوشنبه", "سه‌شنبه", "چهارشنبه", "پنج‌شنبه", "جمعه", "شنبه", "یکشنبه"]
MONTHS_FA = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]

_DATE_RE = re.compile(r"^\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?\s*$")
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = (
        355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
        + (gy2 + 399) // 400 + gd + g_d_m[gm - 1]
    )
    jy = -1595 + 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd
    days += (jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186
    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        days -= 1
        gy += 100 * (days // 36524)
        days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1
    leap = (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0
    month_days = [0, 31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    while gm <= 12 and gd > month_days[gm]:
        gd -= month_days[gm]
        gm += 1
    return gy, gm, gd


def to_jalali(d: dt.date | dt.datetime, with_time: bool = False) -> str:
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    s = f"{jy:04d}/{jm:02d}/{jd:02d}"
    if with_time and isinstance(d, dt.datetime):
        s += f" {d.hour:02d}:{d.minute:02d}"
    return s


def normalize(value: str | None, with_time: bool = False) -> str | None:
    """ورودی شمسی را به قالب استاندارد درمی‌آورد (ارقام فارسی و جداکننده‌ی «-» هم پذیرفته می‌شود).

    خطای ValueError برای ورودی نامعتبر.
    """
    if value is None or str(value).strip() == "":
        return None
    m = _DATE_RE.match(str(value).translate(_FA_DIGITS))
    if not m:
        raise ValueError(f"تاریخ نامعتبر: {value!r} (قالب درست: 1405/07/05)")
    jy, jm, jd = int(m[1]), int(m[2]), int(m[3])
    if not (1 <= jm <= 12 and 1 <= jd <= (31 if jm <= 6 else 30)):
        raise ValueError(f"تاریخ نامعتبر: {value!r}")
    s = f"{jy:04d}/{jm:02d}/{jd:02d}"
    if with_time:
        hh, mm = (int(m[4]), int(m[5])) if m[4] else (9, 0)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(f"ساعت نامعتبر: {value!r}")
        s += f" {hh:02d}:{mm:02d}"
    return s


def parse(value: str) -> dt.date:
    """رشته‌ی شمسی → date میلادی."""
    norm = normalize(value)
    if norm is None:
        raise ValueError("تاریخ خالی است")
    jy, jm, jd = (int(x) for x in norm.split("/"))
    return dt.date(*jalali_to_gregorian(jy, jm, jd))


def weekday_fa(d: dt.date) -> str:
    return WEEKDAYS_FA[d.weekday()]


def long_format(d: dt.date) -> str:
    jy, jm, jd = gregorian_to_jalali(d.year, d.month, d.day)
    return f"{weekday_fa(d)} {jd} {MONTHS_FA[jm - 1]} {jy}"


def add_days(jdate: str, days: int) -> str:
    return to_jalali(parse(jdate) + dt.timedelta(days=days))


def days_between(a: str, b: str) -> int:
    """تعداد روز از a تا b (مثبت اگر b بعد از a باشد)."""
    return (parse(b) - parse(a)).days
