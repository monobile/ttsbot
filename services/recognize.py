"""Выбор движка распознавания: Azure Vision Read, с фолбэком на Tesseract.

Tesseract запускается в отдельном потоке — распознавание на 2400px занимает
секунды и блокировало бы event loop бота.
"""
import asyncio
import logging

from services import ocr, vision

log = logging.getLogger(__name__)


async def recognize(raw: bytes, lang_hint: str | None = None) -> tuple[str, str, str]:
    """Возвращает (текст, код языка, название движка)."""
    if vision.enabled():
        try:
            text = await vision.vision.read_text(raw)
            text = ocr._cleanup(text)
            if len(text.strip()) >= 2:
                return text, (lang_hint or ocr.detect_language(text)), "Azure Vision"
            log.info("Azure Vision вернул пустой текст — пробуем Tesseract")
        except vision.VisionError as e:
            log.warning("Azure Vision не сработал (%s) — фолбэк на Tesseract", e)

    text, lang = await asyncio.to_thread(ocr.extract_text, raw, lang_hint)
    return text, lang, "Tesseract"
