"""صفحه‌ی اسکن‌شده‌ای که کج (۹۰ درجه) در اسکنر گذاشته شده: فقط تصویر، بدون لایه‌ی متنی."""
import io

import pymupdf
from PIL import Image

from tests.fixtures.automation_letter import CSS, FONT_DIR, _html

TEXT = """صورت وضعیت موقت شماره ۶ بخش اجرا
عنوان پروژه: احداث راه های ارتباطی بین چاه ها و تاسیسات فرآورش سیار میدان نفتی
دوره کارکرد از ۱۴۰۵/۰۲/۰۱ لغایت ۱۴۰۵/۰۵/۰۴ و شماره قرارداد ۱۴۰۴-۰۷۳
اجرای عملیات خاکبرداری و خاکریزی بازه شماره یک از کیلومتر ۱۵ تا کیلومتر ۲۵ انجام شد
اجرای عملیات ابنیه فنی و کالورت های بازه دوم و سوم در حال انجام است
تامین آب و برق برای محل کار و شبکه توزیع داخلی کارگاه و محل اقامت کارگران پیمانکار
خرید و حمل مصالح سنگی و قیر برای آسفالت و زیر اساس و اساس راه
برگزاری جلسات هماهنگی با کارفرما و مشاور درباره برنامه زمانبندی و پیشرفت کار
تهیه و تجهیز محل اقامت کارکنان و ساخت مخزن سپتیک و شبکه فاضلاب
پیمانکار موظف است گزارش پیشرفت کار را در پایان هر ماه به مشاور ارسال کند"""


def upright_png() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()  # A4
    page.insert_htmlbox(pymupdf.Rect(40, 40, 555, 800), _html("\n\n".join(TEXT.split("\n"))), css=CSS,
                        archive=pymupdf.Archive(FONT_DIR))
    return page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY).tobytes("png")


def build(turn: int = 270) -> bytes:
    """PDF تک‌صفحه‌ای که محتوایش turn درجه (پادساعتگرد) چرخیده است."""
    img = Image.open(io.BytesIO(upright_png())).rotate(turn, expand=True, fillcolor=255)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)  # عمودی، مثل صفحه‌های اسکن
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)
    doc.set_metadata({"producer": "Hewlett-Packard"})
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()
