"""نامه‌ی آزمایشی شبیه خروجی اتوماسیون اداری: متن نامه در لایه‌ی متنی PDF، ولی کادر شماره/تاریخ تصویر است."""
import io

import pymupdf

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
CSS = "@font-face{font-family:dj;src:url(DejaVuSans.ttf);} body{font-family:dj;font-size:11pt;line-height:1.8}"
FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")

BODY = """جناب آقای مهندس کریمی
مدیرعامل محترم شرکت توسعه نفت و گاز مهران

موضوع: معرفی نماینده به شرکت توسعه نفت و گاز مهران

با سلام و احترام
احتراماً، بدینوسیله آقای مهندس ملکی به عنوان نماینده‌ی این شرکت در پروژه‌ی احداث راه ارتباطی معرفی می‌گردند.
کارت شناسایی نامبرده به پیوست حضورتان ارسال می‌گردد. خواهشمند است دستور فرمایید همکاری لازم صورت پذیرد.
"""


def _html(text: str) -> str:
    return "<div dir='rtl'>" + "".join(f"<p style='margin:0'>{line or '&nbsp;'}</p>" for line in text.split("\n")) + "</div>"


def header_png(number: str, date: str) -> bytes:
    """کادر سربرگ به‌صورت تصویر (مثل مهر/سربرگ اتوماسیون)."""
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=80)
    page.insert_htmlbox(pymupdf.Rect(5, 5, 195, 75),
                        _html(f"شماره: {number.translate(FA)}\nتاریخ: {date.translate(FA)}\nپیوست: دارد"),
                        css=CSS, archive=pymupdf.Archive(FONT_DIR))
    return page.get_pixmap(dpi=300).tobytes("png")


def build(number: str = "1405/1553", date: str = "1405/07/14") -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()  # A4
    page.insert_image(pymupdf.Rect(40, 30, 240, 110), stream=header_png(number, date))  # بالا چپ
    page.insert_htmlbox(pymupdf.Rect(50, 150, 545, 780), _html(BODY), css=CSS, archive=pymupdf.Archive(FONT_DIR))
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
