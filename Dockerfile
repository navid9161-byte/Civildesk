FROM python:3.12-slim

# Tesseract برای خواندن PDFهای اسکن‌شده (فارسی و انگلیسی)
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fas tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# مدل جستجوی معنایی هنگام ساخت دانلود می‌شود تا سرور به اینترنت خارجی نیاز نداشته باشد
ENV CIVILDESK_MODEL_DIR=/opt/models
COPY civildesk/embedder.py /tmp/embedder.py
RUN mkdir -p /tmp/dl/civildesk && touch /tmp/dl/civildesk/__init__.py \
 && cp /tmp/embedder.py /tmp/dl/civildesk/ && cd /tmp/dl && python -m civildesk.embedder \
 && rm -rf /tmp/dl /tmp/embedder.py

COPY civildesk ./civildesk
ENV CIVILDESK_DB=/data/civildesk.db \
    CIVILDESK_DOCS_DIR=/data/docs \
    HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
CMD ["uvicorn", "civildesk.main:app", "--host", "0.0.0.0", "--port", "8000"]
