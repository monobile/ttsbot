#!/usr/bin/env python3
"""Предполётная проверка: ключ Azure, регион, наличие нужных голосов, Telegram-токен.

Запуск на сервере:  ./venv/bin/python check.py
"""
import asyncio
import sys

import aiohttp

from config import LANGUAGES, settings

VOICES_URL = "https://{r}.tts.speech.microsoft.com/cognitiveservices/voices/list"
TG_URL = "https://api.telegram.org/bot{t}/getMe"


async def check_telegram(session: aiohttp.ClientSession) -> bool:
    try:
        async with session.get(TG_URL.format(t=settings.bot_token)) as r:
            data = await r.json()
            if data.get("ok"):
                print(f"✅ Telegram: бот @{data['result']['username']}")
                return True
            print(f"❌ Telegram: {data.get('description')}")
    except Exception as e:  # noqa: BLE001
        print(f"❌ Telegram недоступен: {e}")
    return False


async def check_speech(session: aiohttp.ClientSession) -> bool:
    region = settings.azure_speech_region
    url = VOICES_URL.format(r=region)
    headers = {"Ocp-Apim-Subscription-Key": settings.azure_speech_key}
    try:
        async with session.get(url, headers=headers) as r:
            if r.status == 401:
                print(f"❌ Azure Speech: ключ отклонён (401). Проверьте ключ и регион «{region}».")
                return False
            if r.status != 200:
                print(f"❌ Azure Speech: HTTP {r.status} для региона «{region}».")
                return False
            voices = await r.json()
    except Exception as e:  # noqa: BLE001
        print(f"❌ Azure Speech недоступен из этой сети: {e}")
        return False

    names = {v["ShortName"] for v in voices}
    print(f"✅ Azure Speech: регион {region}, доступно голосов: {len(names)}")

    ok = True
    for code, meta in LANGUAGES.items():
        voice = meta["voice"]
        if voice in names:
            print(f"   ✅ {meta['name']}: {voice}")
        else:
            ok = False
            locale = "-".join(voice.split("-")[:2])
            alts = sorted(n for n in names if n.startswith(locale))
            print(f"   ❌ {meta['name']}: голос {voice} НЕ доступен в этом регионе")
            if alts:
                print(f"      Замените в config.py на один из: {', '.join(alts[:6])}")
    return ok


async def check_vision(session: aiohttp.ClientSession) -> None:
    from services import vision as vis

    if not vis.enabled():
        print("⚠️  Azure Vision: не настроен — OCR только через Tesseract")
        return
    print(f"   endpoint: {settings.azure_vision_endpoint}")
    # 1x1 PNG слишком мал для Vision (минимум 50x50) — рисуем пустой квадрат
    try:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (60, 60), "white").save(buf, format="PNG")
        probe = buf.getvalue()
    except Exception as e:  # noqa: BLE001
        print(f"❌ Azure Vision: не удалось создать тестовое изображение: {e}")
        return

    try:
        await vis.vision.read_text(probe)
        print(f"✅ Azure Vision: работает (API: {vis.vision._api})")
    except vis.VisionError as e:
        print(f"❌ Azure Vision: {e}")
    except Exception as e:  # noqa: BLE001
        print(f"❌ Azure Vision: неожиданная ошибка: {e}")


async def check_translator(session: aiohttp.ClientSession) -> None:
    if not settings.azure_translator_key:
        print("⚠️  Azure Translator: ключ не задан — построчный перевод отключён")
        return
    url = "https://api.cognitive.microsofttranslator.com/translate"
    params = {"api-version": "3.0", "from": "ru", "to": "en"}
    headers = {
        "Ocp-Apim-Subscription-Key": settings.azure_translator_key,
        "Ocp-Apim-Subscription-Region": settings.azure_translator_region,
        "Content-Type": "application/json",
    }
    try:
        async with session.post(url, params=params, headers=headers, json=[{"Text": "проверка"}]) as r:
            if r.status == 200:
                data = await r.json()
                print(f"✅ Azure Translator: работает ({data[0]['translations'][0]['text']})")
            else:
                print(f"❌ Azure Translator: HTTP {r.status} — проверьте ключ и AZURE_TRANSLATOR_REGION")
    except Exception as e:  # noqa: BLE001
        print(f"❌ Azure Translator недоступен: {e}")


def check_tesseract() -> bool:
    try:
        import pytesseract

        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
        langs = set(pytesseract.get_languages(config=""))
    except Exception as e:  # noqa: BLE001
        print(f"❌ Tesseract: {e}")
        return False

    print(f"✅ Tesseract: {pytesseract.get_tesseract_version()}")
    ok = True
    for pack in ("ara", "rus", "eng"):
        if pack in langs:
            print(f"   ✅ пакет {pack}")
        else:
            ok = False
            print(f"   ❌ пакет {pack} отсутствует → apt install tesseract-ocr-{pack}")
    return ok


async def main() -> int:
    print("=" * 52)
    print("Предполётная проверка бота")
    print("=" * 52)
    tess = check_tesseract()
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        tg = await check_telegram(session)
        sp = await check_speech(session)
        await check_vision(session)
        await check_translator(session)
    print("=" * 52)
    if tg and sp and tess:
        print("Всё готово — можно запускать: systemctl restart ttsbot")
        return 0
    print("Есть проблемы — см. пункты с ❌ выше")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
