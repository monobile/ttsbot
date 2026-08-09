"""OCR через Tesseract.

Ключевой принцип: НИКОГДА не запускать Tesseract в многоязычном режиме
с арабским вместе с латиницей/кириллицей. Модели конкурируют за глифы
и на выходе получается мусор вида "Baal Glas" внутри арабского текста.

Поэтому: сначала определяем письменность (OSD), потом распознаём
одним подходящим набором языков.
"""
import io
import logging
import re

import pytesseract
from PIL import Image, ImageOps

from config import settings

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

# Огласовки (харакат) и татвиль — снимаются перед озвучкой, см. strip_harakat
_HARAKAT = re.compile(r"[\u064B-\u0652\u0670\u06D6-\u06ED\u0640]")
_BIDI_MARKS = re.compile(r"[\u200e\u200f\u202a-\u202e]")

# Наборы языков по письменности. Арабский — всегда ОДИН, без примесей.
_SCRIPT_LANGS = {
    "Arabic": "ara",
    "Cyrillic": "rus+eng",
    "Latin": "eng+rus",
}


class OcrError(Exception):
    pass


def _preprocess(raw: bytes) -> Image.Image:
    """Предобработка. Апскейл до 2400px по длинной стороне — на 1600 качество заметно хуже."""
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    w, h = img.size
    target = settings.ocr_upscale
    if max(w, h) < target:
        scale = target / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def _tess_config(psm: int = 6) -> str:
    cfg = f"--psm {psm}"
    if settings.tessdata_dir:
        cfg += f" --tessdata-dir {settings.tessdata_dir}"
    return cfg


def _detect_script(img: Image.Image) -> str | None:
    """Определяет письменность через OSD Tesseract. Возвращает 'Arabic'/'Cyrillic'/'Latin'."""
    try:
        osd = pytesseract.image_to_osd(img)
    except Exception as e:  # noqa: BLE001
        log.warning("OSD failed: %s", e)
        return None
    script, conf = None, 0.0
    for line in osd.splitlines():
        if line.startswith("Script:"):
            script = line.split(":", 1)[1].strip()
        elif line.startswith("Script confidence:"):
            try:
                conf = float(line.split(":", 1)[1])
            except ValueError:
                pass
    log.info("OSD script=%s confidence=%.2f", script, conf)
    # Низкая уверенность — не доверяем, уйдём в перебор вариантов
    if script and conf >= 5.0:
        return script
    return None


def _score(text: str, langs: str) -> int:
    """Сколько символов ожидаемой письменности распознано минус штраф за чужие."""
    arb = len(_ARABIC.findall(text))
    cyr = len(_CYRILLIC.findall(text))
    lat = len(_LATIN.findall(text))
    if langs == "ara":
        return arb - 2 * (cyr + lat)
    return cyr + lat - 2 * arb


def _best_effort_ocr(img: Image.Image) -> tuple[str, str]:
    """OSD не сработал: пробуем арабский и латиницу/кириллицу, выбираем по счёту."""
    results = []
    for langs in ("ara", "rus+eng"):
        try:
            text = pytesseract.image_to_string(img, lang=langs, config=_tess_config())
        except pytesseract.TesseractError:
            continue
        results.append((_score(text, langs), langs, text))
    if not results:
        raise OcrError("Ошибка распознавания. Проверьте языковые пакеты Tesseract.")
    results.sort(reverse=True, key=lambda r: r[0])
    log.info("best-effort OCR scores: %s", [(r[1], r[0]) for r in results])
    return results[0][2], results[0][1]


def detect_language(text: str) -> str:
    """Возвращает код языка: ar / ce / ru / en."""
    if not text.strip():
        return "en"
    if len(_ARABIC.findall(text)) >= 3:
        return "ar"
    cyr = len(_CYRILLIC.findall(text))
    lat = len(_LATIN.findall(text))
    if cyr > lat:
        markers = len(_CHECHEN_MARKERS.findall(text))
        if markers >= 1 or "Ӏ" in text or "ӏ" in text:
            return "ce"
        words = re.findall(r"[\w\u0400-\u04FF]+", text.lower())
        hits = sum(1 for w in words if w in _CHECHEN_WORDS)
        if len(words) >= 3 and (hits >= 3 or hits / len(words) >= 0.34):
            return "ce"
        return "ru"
    return "en"


def strip_harakat(text: str) -> str:
    """Снимает огласовки и bidi-метки.

    Зачем: Tesseract на полностью огласованном тексте (مُشَكَّل) часто ставит
    харакат неверно, а голос Azure ar-SA расставляет огласовки сам.
    Неверный харакат из OCR активно портит озвучку — лучше его убрать.
    """
    text = _HARAKAT.sub("", text)
    text = _BIDI_MARKS.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", text)


def extract_text(raw: bytes, lang_hint: str | None = None) -> tuple[str, str]:
    """Распознаёт текст. Возвращает (текст, код языка)."""
    from config import LANGUAGES

    if len(raw) > settings.max_image_mb * 1024 * 1024:
        raise OcrError(f"Изображение больше {settings.max_image_mb} МБ.")

    try:
        img = _preprocess(raw)
    except Exception as e:  # noqa: BLE001
        raise OcrError("Не удалось открыть изображение. Пришлите JPG или PNG.") from e

    if lang_hint and lang_hint in LANGUAGES:
        # Явное указание пользователя важнее автоматики
        langs = "ara" if lang_hint == "ar" else "rus+eng"
        try:
            text = pytesseract.image_to_string(img, lang=langs, config=_tess_config())
        except pytesseract.TesseractNotFoundError as e:
            raise OcrError("Tesseract не найден на сервере.") from e
        except pytesseract.TesseractError as e:
            raise OcrError("Ошибка распознавания.") from e
    else:
        script = _detect_script(img)
        if script in _SCRIPT_LANGS:
            langs = _SCRIPT_LANGS[script]
            try:
                text = pytesseract.image_to_string(img, lang=langs, config=_tess_config())
            except pytesseract.TesseractNotFoundError as e:
                raise OcrError("Tesseract не найден на сервере.") from e
            except pytesseract.TesseractError:
                text, langs = _best_effort_ocr(img)
        else:
            text, langs = _best_effort_ocr(img)

    text = _cleanup(text)
    if len(text.strip()) < 2:
        raise OcrError(
            "Текст на изображении не распознан.\n\n"
            "Попробуйте: хороший свет, страница целиком в кадре, без наклона и блика."
        )

    return text, (lang_hint or detect_language(text))


def _cleanup(text: str) -> str:
    lines = []
    for line in text.splitlines():
        line = _BIDI_MARKS.sub("", line).strip()
        line = re.sub(r"[|_~^`]{2,}", "", line)
        if line:
            lines.append(line)
    return "\n".join(lines)


def split_lines(text: str) -> list[str]:
    chunks: list[str] = []
    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        if len(raw_line) > 200:
            parts = re.split(r"(?<=[.!?؟।])\s+", raw_line)
            chunks.extend(p.strip() for p in parts if p.strip())
        else:
            chunks.append(raw_line)
    return chunks
