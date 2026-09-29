"""نوشتن پاسخ از روی بندهای پیدا‌شده در اسناد.

سه حالت، به ترتیب اولویت:
1. Claude — اگر ANTHROPIC_API_KEY تنظیم شده باشد
2. هر مدل سازگار با OpenAI Chat Completions — مثلاً مدل متن‌باز روی Ollama خودتان
   (CIVILDESK_LLM_BASE_URL، CIVILDESK_LLM_MODEL و در صورت نیاز CIVILDESK_LLM_API_KEY)
3. بدون مدل — «پاسخ استخراجی»: مرتبط‌ترین جمله‌ها عیناً از متن اسناد با ذکر منبع
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from . import documents
from .config import settings

log = logging.getLogger(__name__)

ANSWER_SYSTEM = """تو دستیار پاسخ‌گویی به پرسش‌های یک مهندس عمران از روی اسناد خود او هستی \
(مقررات ملی ساختمان، نشریات، قراردادها، دفترچه‌های فنی و ...).

قواعد:
- فقط و فقط از روی «بندهای منبع» که داده می‌شود پاسخ بده؛ از دانسته‌های خودت چیزی اضافه نکن.
- بعد از هر جمله یا عدد، شماره‌ی بند منبع را در کروشه بیاور، مثل [۲].
- اعداد، واحدها و شماره‌ی بندها را دقیقاً همان‌طور که در متن آمده بنویس.
- اگر پاسخ در بندها نیست یا ناقص است، صریح بگو «در اسناد پیدا نشد» یا بگو کدام بخش پیدا نشد.
- متن بعضی بندها از OCR آمده و ممکن است غلط تایپی داشته باشد؛ اگر عددی مشکوک است، بگو که با صفحه‌ی اصلی چک شود.
- کوتاه، دقیق و به فارسی روان پاسخ بده.
"""


def provider() -> str | None:
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"):
        return "claude"
    if os.getenv("CIVILDESK_LLM_BASE_URL"):
        return "openai"
    return None


def _sources_block(results: list[dict[str, Any]]) -> str:
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"[{i}] «{r['title']}» — صفحه {r['page']}\n{r['text']}")
    return "\n\n".join(parts)


def _ask_claude(question: str, sources: str) -> str:
    from . import agent

    client = agent.get_client()
    kwargs: dict[str, Any] = dict(
        model=settings.claude_model,
        max_tokens=16000,
        system=ANSWER_SYSTEM,
        messages=[{"role": "user", "content": f"بندهای منبع:\n\n{sources}\n\n---\nپرسش: {question}"}],
        thinking={"type": "adaptive"},
    )
    if settings.claude_fallbacks:
        resp = client.beta.messages.create(betas=[agent.FALLBACK_BETA], fallbacks="default", **kwargs)
    else:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError("مدل به این پرسش پاسخ نداد")
    return "\n".join(b.text for b in resp.content if b.type == "text").strip()


def _ask_openai_compatible(question: str, sources: str) -> str:
    base = os.environ["CIVILDESK_LLM_BASE_URL"].rstrip("/")
    headers = {"Content-Type": "application/json"}
    if key := os.getenv("CIVILDESK_LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    body = {
        "model": os.getenv("CIVILDESK_LLM_MODEL", "qwen2.5:7b"),
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"بندهای منبع:\n\n{sources}\n\n---\nپرسش: {question}"},
        ],
    }
    timeout = float(os.getenv("CIVILDESK_LLM_TIMEOUT", "300"))
    r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def answer(question: str, doc_ids: list[int] | None = None, limit: int = 6) -> dict[str, Any]:
    found = documents.search(question, doc_ids=doc_ids, limit=limit)
    results = found["results"]
    out: dict[str, Any] = {**found, "mode": "extractive", "answer": None, "error": None}
    if not results:
        out["answer"] = "در اسناد بارگذاری‌شده مطلب مرتبطی پیدا نشد. واژه‌های دیگری امتحان کنید."
        return out
    p = provider()
    if p:
        try:
            sources = _sources_block(results)
            out["answer"] = _ask_claude(question, sources) if p == "claude" else _ask_openai_compatible(question, sources)
            out["mode"] = p
            return out
        except Exception as e:  # اگر مدل در دسترس نبود، پاسخ استخراجی
            log.warning("تولید پاسخ ناموفق بود: %s", e)
            out["error"] = f"مدل هوش مصنوعی در دسترس نبود ({type(e).__name__})؛ مرتبط‌ترین بخش‌های اسناد نمایش داده شد."
    out["answer"] = extractive_answer(results, found.get("terms") or [])
    return out


def extractive_answer(results: list[dict[str, Any]], terms: list[str], max_lines: int = 4) -> str:
    """بدون مدل: جمله‌هایی از متن اسناد که بیشترین واژه‌های پرسش را دارند (بدون تکرار)."""
    import re

    from . import textnorm

    scored, seen = [], set()
    for i, r in enumerate(results[:5], 1):
        for pos, sent in enumerate(re.split(r"(?<=[.!?؟؛])\s+|\n", r["text"])):
            sent = sent.strip()
            norm = textnorm.normalize(sent)
            if len(norm) < 15 or norm in seen:
                continue
            hits = sum(1 for t in terms if t in norm)
            if hits == 0:
                continue
            seen.add(norm)
            # امتیاز: تعداد واژه‌های مشترک، سپس رتبه‌ی منبع؛ تیترهای کوتاه امتیاز کمتر
            scored.append((hits - (0.5 if len(norm) < 40 else 0), -i, -pos, sent, i))
    if not scored:
        return "\n".join(f"• {r['highlights'][0]} [{i}]" for i, r in enumerate(results[:2], 1) if r["highlights"])
    best = max(x[0] for x in scored)
    keep = sorted((x for x in scored if x[0] >= max(1, best * 0.5)), reverse=True)[:max_lines]
    return "\n".join(f"• {sent} [{i}]" for *_, sent, i in keep)
