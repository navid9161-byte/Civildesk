"""مدل «جستجوی معنایی»: تبدیل متن به بردار با یک مدل چندزبانه‌ی کوچک که روی CPU اجرا می‌شود.

مدل: multilingual-e5-small (نسخه‌ی فشرده‌ی ONNX، حدود ۱۳۰ مگابایت، فارسی را خوب می‌فهمد).
در Docker هنگام ساخت ایمیج دانلود می‌شود تا روی سرور ایرانی نیازی به اینترنت خارجی نباشد.
اگر مدل در دسترس نباشد، برنامه بدون خطا فقط با جستجوی کلیدواژه‌ای کار می‌کند.

دانلود دستی:  python -m civildesk.embedder
"""
from __future__ import annotations

import logging
import os
import threading

import numpy as np

log = logging.getLogger(__name__)

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_SOURCE = "Xenova/multilingual-e5-small"
MODEL_FILE = "onnx/model_quantized.onnx"
DIM = 384

_lock = threading.Lock()
_model = None
_failed = False


def model_dir() -> str:
    return os.getenv("CIVILDESK_MODEL_DIR", "models")


def _register() -> None:
    from fastembed import TextEmbedding
    from fastembed.common.model_description import ModelSource, PoolingType

    if not any(m["model"] == MODEL_NAME for m in TextEmbedding.list_supported_models()):
        TextEmbedding.add_custom_model(
            model=MODEL_NAME, pooling=PoolingType.MEAN, normalization=True,
            sources=ModelSource(hf=MODEL_SOURCE), dim=DIM, model_file=MODEL_FILE,
        )


def _load(local_only: bool):
    from fastembed import TextEmbedding

    _register()
    return TextEmbedding(MODEL_NAME, cache_dir=model_dir(), local_files_only=local_only)


def get_model():
    """مدل را یک بار بارگذاری می‌کند؛ اگر ممکن نبود None برمی‌گرداند."""
    global _model, _failed
    if _model is not None or _failed or os.getenv("CIVILDESK_NO_EMBEDDINGS"):
        return _model
    with _lock:
        if _model is None and not _failed:
            try:
                try:
                    _model = _load(local_only=True)
                except Exception:
                    _model = _load(local_only=False)  # اولین اجرا: دانلود
                log.info("مدل جستجوی معنایی بارگذاری شد")
            except Exception as e:
                _failed = True
                log.warning("مدل جستجوی معنایی در دسترس نیست؛ فقط جستجوی کلیدواژه‌ای فعال است (%s)", e)
    return _model


def available() -> bool:
    return get_model() is not None


def status() -> str:
    """وضعیت بدون بارگذاری مدل: ready | failed | pending."""
    if _model is not None:
        return "ready"
    if _failed or os.getenv("CIVILDESK_NO_EMBEDDINGS"):
        return "failed"
    return "pending"


def embed_passages(texts: list[str]) -> np.ndarray | None:
    m = get_model()
    if m is None or not texts:
        return None
    with _lock:
        vecs = list(m.embed([f"passage: {t}" for t in texts], batch_size=32))
    return np.asarray(vecs, dtype=np.float32)


def embed_query(text: str) -> np.ndarray | None:
    m = get_model()
    if m is None:
        return None
    with _lock:
        return np.asarray(next(iter(m.embed([f"query: {text}"]))), dtype=np.float32)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _model = _load(local_only=False)
    print("OK:", embed_query("آزمایش").shape)
