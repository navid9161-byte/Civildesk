FROM python:3.12-slim

# Tesseract برای خواندن PDFهای اسکن‌شده (فارسی و انگلیسی)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fas tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# مدل جستجوی معنایی (حدود ۱۳۰ مگابایت) فقط وقتی دانلود می‌شود که WITH_SEMANTIC=1 باشد
# (در liara.json → build.args). در حالت سبک لازم نیست و ساخت را کند می‌کند؛
# محدودیت زمان ساخت لیارا در پلن پایه ۵ دقیقه است.
ARG WITH_SEMANTIC=0
ENV CIVILDESK_MODEL_DIR=/opt/models
COPY civildesk/embedder.py /tmp/embedder.py
RUN if [ "$WITH_SEMANTIC" = "1" ]; then \
      mkdir -p /tmp/dl/civildesk && touch /tmp/dl/civildesk/__init__.py \
      && cp /tmp/embedder.py /tmp/dl/civildesk/ && cd /tmp/dl \
      && (timeout 150 python -m civildesk.embedder || echo "WARNING: embedding model not downloaded; semantic search disabled"); \
    else echo "Semantic model skipped (WITH_SEMANTIC=0)"; fi \
 && rm -rf /tmp/dl /tmp/embedder.py

COPY civildesk ./civildesk
ENV CIVILDESK_DB=/data/civildesk.db \
    CIVILDESK_DOCS_DIR=/data/docs \
    HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
# پورت پیش‌فرض ۸۰ (همان پیش‌فرض لیارا)؛ با متغیر PORT قابل تغییر است
EXPOSE 80
CMD ["sh", "-c", "exec uvicorn civildesk.main:app --host 0.0.0.0 --port ${PORT:-80} --proxy-headers --forwarded-allow-ips='*'"]
