"""Конфигурация бота. Все секреты — только через .env"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _req(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Переменная окружения {name} не задана (см. .env.example)")
    return val


@dataclass(frozen=True)
class Settings:
    bot_token: str
    azure_speech_key: str
    azure_speech_region: str
    azure_translator_key: str
    azure_translator_region: str
    azure_vision_key: str
    azure_vision_endpoint: str
    tesseract_cmd: str
    tessdata_dir: str
    ocr_upscale: int
    max_image_mb: int
    max_tts_chars: int


settings = Settings(
    bot_token=_req("TELEGRAM_BOT_TOKEN"),
    azure_speech_key=_req("AZURE_SPEECH_KEY"),
    azure_speech_region=os.getenv("AZURE_SPEECH_REGION", "qatarcentral"),
    azure_translator_key=os.getenv("AZURE_TRANSLATOR_KEY", ""),
    azure_translator_region=os.getenv("AZURE_TRANSLATOR_REGION", "westeurope"),
    azure_vision_key=os.getenv("AZURE_VISION_KEY", ""),
    # По умолчанию собирается из региона; можно задать полный URL напрямую
    azure_vision_endpoint=os.getenv("AZURE_VISION_ENDPOINT")
    or (
        f"https://{os.getenv('AZURE_VISION_REGION', 'qatarcentral')}.api.cognitive.microsoft.com"
        if os.getenv("AZURE_VISION_KEY")
        else ""
    ),
    tesseract_cmd=os.getenv("TESSERACT_CMD", "/usr/bin/tesseract"),
    # Пусто = системная папка tessdata. Задавать только если модели лежат отдельно —
    # тогда ВСЕ нужные .traineddata должны быть в этой папке, иначе проходы упадут.
    tessdata_dir=os.getenv("TESSDATA_DIR", ""),
    # 2400px по длинной стороне: на 1600 арабский распознаётся заметно хуже
    ocr_upscale=int(os.getenv("OCR_UPSCALE", "2400")),
    max_image_mb=int(os.getenv("MAX_IMAGE_MB", "10")),
    max_tts_chars=int(os.getenv("MAX_TTS_CHARS", "3000")),
)

# ---- Языки ----------------------------------------------------------------
# code: (название, tesseract-код, azure-голос, azure-код перевода)
LANGUAGES = {
    "ar": {
        "name": "العربية / Арабский",
        "tesseract": "ara",
        "voice": "ar-SA-HamedNeural",
        "translate": "ar",
        "rtl": True,
    },
    "en": {
        "name": "English / Английский",
        "tesseract": "eng",
        "voice": "en-US-GuyNeural",
        "translate": "en",
        "rtl": False,
    },
    "ru": {
        "name": "Русский",
        "tesseract": "rus",
        "voice": "ru-RU-DmitryNeural",
        "translate": "ru",
        "rtl": False,
    },
    "ce": {
        # Чеченский: своего голоса и движка перевода у Azure нет.
        # OCR — через русский пакет (алфавит почти совпадает).
        # TTS — русский голос как приближение (озвучка с акцентом).
        "name": "Нохчийн / Чеченский",
        "tesseract": "rus",
        "voice": "ru-RU-DmitryNeural",
        "translate": None,  # Azure Translator не поддерживает чеченский
        "rtl": False,
        "approx_tts": True,
    },
}

