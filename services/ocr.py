"""OCR через Tesseract + эвристическое определение языка."""
import io
import logging
import re

import pytesseract
from PIL import Image, ImageOps

from config import OCR_ALL_LANGS, settings

log = logging.getLogger(__name__)
pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd

# Символы и диграфы, характерные для чеченского и отсутствующие в русском
_CHECHEN_MARKERS = re.compile(r"[ӀӃҌҶ]|[аоуюяАОУЮЯ]ь|(?:гӀ|кӀ|пӀ|тӀ|хӀ|цӀ|чӀ|къ|кх|хь|аь|оь|уь)", re.IGNORECASE)

# Частотные служебные слова чеченского — помогают, когда спецсимволов в тексте нет
_CHECHEN_WORDS = {
    "ду", "бу", "ву", "дара", "бара", "вара", "яра", "цхьа", "хӀун",
    "нохчийн", "мотт", "тхан", "тхо", "тхуна", "суна", "хьуна", "хьан",
    "цуьнан", "цара", "уьш", "хьо", "кхин", "дерриг", "дукха", "хӀинца",
    "мукъалахь", "ненан", "гур", "хила", "хилла", "болу", "йолу", "долу",
}
_ARABIC = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_LATIN = re.compile(r"[A-Za-z]")


class OcrError(Exception):
    pass


def _preprocess(raw: bytes) -> Image.Image:
    """Лёгкая предобработка — заметно поднимает качество на фото с телефона."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    img = img.convert("L")            # градации серого
    img = ImageOps.autocontrast(img)  # выравнивание контраста
    # Апскейл мелких изображений — Tesseract любит ~300 dpi
    w, h = img.size
    if max(w, h) < 1600:
        scale = 1600 / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def detect_language(text: str) -> str:
    """Возвращает код языка: ar / ce / ru / en."""
    if not text.strip():
        return "en"
    if len(_ARABIC.findall(text)) >= 3:
        return "ar"
    cyr = len(_CYRILLIC.findall(text))
    lat = len(_LATIN.findall(text))
    if cyr > lat:
        # Чеченский пишется кириллицей — отличаем по спецсимволам и диграфам
        markers = len(_CHECHEN_MARKERS.findall(text))
        if markers >= 1 or "Ӏ" in text or "ӏ" in text:
            return "ce"
        words = re.findall(r"[\w\u0400-\u04FF]+", text.lower())
        hits = sum(1 for w in words if w in _CHECHEN_WORDS)
        if len(words) >= 3 and (hits >= 3 or hits / len(words) >= 0.34):
            return "ce"
        return "ru"
    return "en"


def extract_text(raw: bytes, lang_hint: str | None = None) -> tuple[str, str]:
    """
    Распознаёт текст на изображении.
    Возвращает (текст, определённый_код_языка).
    """
    from config import LANGUAGES

    if len(raw) > settings.max_image_mb * 1024 * 1024:
        raise OcrError(f"Изображение больше {settings.max_image_mb} МБ.")

    try:
        img = _preprocess(raw)
    except Exception as e:  # noqa: BLE001
        raise OcrError("Не удалось открыть изображение. Пришлите JPG или PNG.") from e

    langs = OCR_ALL_LANGS
    if lang_hint and lang_hint in LANGUAGES:
        # Явная подсказка — сначала нужный язык, остальные как подстраховка
        primary = LANGUAGES[lang_hint]["tesseract"]
        langs = f"{primary}+{OCR_ALL_LANGS}"

    try:
        text = pytesseract.image_to_string(img, lang=langs, config="--psm 3")
    except pytesseract.TesseractError as e:
        log.exception("Tesseract failed")
        raise OcrError("Ошибка распознавания. Проверьте установку языковых пакетов Tesseract.") from e
    except pytesseract.TesseractNotFoundError as e:
        raise OcrError("Tesseract не найден на сервере. Установите его (см. README).") from e

    text = _cleanup(text)
    if len(text.strip()) < 2:
        raise OcrError(
            "Текст на изображении не распознан.\n\n"
            "Попробуйте: снимок при хорошем свете, страница целиком в кадре, без наклона и блика."
        )

    return text, (lang_hint or detect_language(text))


def _cleanup(text: str) -> str:
    """Убираем мусорные символы и лишние пустые строки."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        line = re.sub(r"[|_~^`]{2,}", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def split_lines(text: str) -> list[str]:
    """Разбивка на смысловые строки для построчного перевода."""
    chunks: list[str] = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        # Длинные абзацы дробим по знакам конца предложения (в т.ч. арабским)
        if len(raw_line) > 200:
            parts = re.split(r"(?<=[.!?؟।])\s+", raw_line)
            chunks.extend(p.strip() for p in parts if p.strip())
        else:
            chunks.append(raw_line)
    return chunks
