"""ساخت صفحه‌ی آزمایشی شبیه «برگ خلاصه مالی کل» صورت‌وضعیت (PDF متنی + نسخه‌ی اسکن‌شده)."""
import sys
import pymupdf

FA = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
f = lambda s: str(s).translate(FA)  # noqa: E731

def sheet_html():
    # ردیف‌ها: (بخش، span، ارکان، [درصد وزنی، مبلغ قرارداد] فقط برای ردیف اول بخش، قبل(درصد، مبلغ)، دوره(درصد، مبلغ)، تاکنون(درصد کل، درصد بخش، مبلغ))
    rows = [
        ("اجرا", 3, "پیمانکار", ("96.07%", "8,530,777,162,044"), ("10.55%", "900,259,004,733"), ("7.05%", "601,558,521,764"), ("16.91%", "17.60%", "1,501,817,526,497")),
        (None, 0, "مشاور", None, ("10.55%", "900,259,004,732"), ("6.86%", "584,965,145,707"), ("16.73%", "17.41%", "1,485,224,150,439")),
        (None, 0, "کارفرما", None, ("10.55%", "900,259,004,732"), ("", ""), ("", "", "")),
        ("مهندسی", 1, "کارفرما", ("2.38%", "211,650,204,822"), ("58.63%", "124,082,022,612"), ("", ""), ("1.40%", "58.63%", "124,082,022,612")),
        ("خرید (کالا)", 1, "کارفرما", ("1.19%", "105,825,102,411"), ("0.00%", "0"), ("", ""), ("0.00%", "0.00%", "0")),
        ("بیمه", 1, "کارفرما", ("0.36%", "31,747,530,723"), ("0.00%", "0"), ("", ""), ("0.00%", "0.00%", "0")),
    ]
    td = lambda s, extra="": f"<td{extra}>{f(s)}</td>"  # noqa: E731
    body = ""
    for i, (sec, span, role, contract, prev, per, tot) in enumerate(rows):
        body += "<tr>" + (td(str(i + 1 if i < 1 else i - 1), f" rowspan='{span}'") if sec else "")
        body += td(sec, f" rowspan='{span}'") if sec else ""
        if contract:
            body += td(contract[0], f" rowspan='{span}'") + td(contract[1], f" rowspan='{span}'")
        body += td(role) + td(prev[0]) + td(prev[1]) + td(per[0]) + td(per[1]) + td(tot[0]) + td(tot[1]) + td(tot[2]) + "<td></td></tr>"
    body += ("<tr>" + td("") + td("جمع") + td("100.00%") + td("8,880,000,000,000") + td("") + td("11.54%") + td("1,024,341,027,344")
             + td("") + td("") + td("18.13%") + td("") + td("1,609,306,173,051") + "<td></td></tr>")
    head = ("<tr><th rowspan='2'>ردیف</th><th rowspan='2'>بخش</th><th colspan='2'>مبلغ عملیات پیمان (قرارداد)</th>"
            "<th rowspan='2'>ارکان پروژه</th><th colspan='2'>پیشرفت تجمعی کار تا دوره قبل</th><th colspan='2'>پیشرفت کار طی دوره</th>"
            "<th colspan='3'>پیشرفت تجمعی کار تاکنون</th><th rowspan='2'>ملاحظات</th></tr>"
            "<tr><th>درصد وزنی</th><th>مبلغ قرارداد (ریال)</th><th>درصد از ردیف</th><th>مبلغ (ریال)</th><th>درصد از ردیف</th>"
            "<th>مبلغ (ریال)</th><th>درصد از کل قرارداد</th><th>درصد از هر بخش</th><th>مبلغ (ریال)</th></tr>")
    top = (f"<p style='text-align:center'><b>طرح توسعه میدان نفتی چنگوله</b></p>"
           f"<p>صورت وضعیت موقت شماره {f(2)} بخش اجرا — شماره قرارداد: {f('073-1404')}-م ت ن</p>"
           f"<p>دوره کارکرد: {f('1404/09/01')} لغایت {f('1404/10/30')}</p><p style='text-align:center'><b>برگ خلاصه مالی کل</b></p>")
    return f"<div dir='rtl'>{top}<table>{head}{body}</table></div>"

def build(out_pdf, out_scan_pdf):
    FONT, FDIR = "DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu"
    css = ("@font-face{font-family:dj;src:url(" + FONT + ");} body{font-family:dj;font-size:6.4pt}"
           "table{border-collapse:collapse;width:100%} td,th{border:0.8px solid black;padding:3px;text-align:center}")
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)  # A4 افقی
    page.insert_htmlbox(pymupdf.Rect(25, 30, 817, 570), sheet_html(), css=css,
                        archive=pymupdf.Archive(FDIR))
    doc.save(out_pdf)
    pix = page.get_pixmap(dpi=300, colorspace=pymupdf.csGRAY)
    from PIL import Image, ImageFilter
    import random
    im = Image.frombytes("L", (pix.width, pix.height), pix.samples).rotate(0.4, fillcolor=255).filter(ImageFilter.GaussianBlur(0.6))
    px = im.load(); random.seed(3)
    for _ in range(6000):
        x, y = random.randrange(im.width), random.randrange(im.height); px[x, y] = random.choice((60, 140, 200))
    png = out_scan_pdf.replace(".pdf", ".png"); im.save(png)
    s = pymupdf.open(); p = s.new_page(width=842, height=595); p.insert_image(p.rect, filename=png); s.save(out_scan_pdf)

if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
