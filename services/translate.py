"""Построчный перевод через Azure Translator.

ВАЖНО: Azure Translator НЕ поддерживает чеченский язык.
Из крупных провайдеров чеченский есть у Google Translate (v3) и Yandex.
Ниже — заготовка провайдера: сейчас работает Azure (ar/en/ru),
для чеченского возвращается понятная ошибка вместо мусорного перевода.
"""
import logging

import aiohttp

from config import LANGUAGES, settings

log = logging.getLogger(__name__)

_URL = "https://api.cognitive.microsofttranslator.com/translate"


class TranslateError(Exception):
    pass


def supports(lang_code: str) -> bool:
    return bool(LANGUAGES.get(lang_code, {}).get("translate"))


async def translate_lines(lines: list[str], src: str, dst: str) -> list[str]:
    """Переводит список строк, сохраняя соответствие 1:1 с оригиналом."""
    if not settings.azure_translator_key:
        raise TranslateError("Перевод не настроен: не задан AZURE_TRANSLATOR_KEY.")

    src_code = LANGUAGES.get(src, {}).get("translate")
    dst_code = LANGUAGES.get(dst, {}).get("translate")

    if not dst_code:
        raise TranslateError(
            f"Перевод на «{LANGUAGES[dst]['name']}» пока недоступен: "
            "Azure Translator не поддерживает чеченский язык.\n\n"
            "Чтобы включить его, нужен Google Translate v3 или Yandex Translate API."
        )
    if not src_code:
        raise TranslateError(
            f"Перевод с «{LANGUAGES[src]['name']}» пока недоступен "
            "(Azure Translator не поддерживает чеченский)."
        )

    params = {"api-version": "3.0", "from": src_code, "to": dst_code, "textType": "plain"}
    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_translator_key,
        "Ocp-Apim-Subscription-Region": settings.azure_translator_region,
        "Content-Type": "application/json",
    }

    results: list[str] = []
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # Azure принимает до 100 элементов и 50k символов за запрос — батчим по 40
        for i in range(0, len(lines), 40):
            batch = lines[i : i + 40]
            payload = [{"Text": line} for line in batch]
            async with session.post(_URL, params=params, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Translator error %s: %s", resp.status, body[:300])
                    raise TranslateError(f"Ошибка перевода (HTTP {resp.status}).")
                data = await resp.json()
            for item in data:
                results.append(item["translations"][0]["text"])

    return results
