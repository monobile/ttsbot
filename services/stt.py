"""Распознавание речи через Azure Speech.

Два API, потому что у каждого свои ограничения:
  1. Fast transcription — длинное аудио, автоопределение языка из списка
     кандидатов, принимает OGG/Opus как есть. Доступен не во всех регионах.
  2. REST для короткого аудио — не более 60 секунд и ОДИН язык на запрос,
     зато доступен везде. Используется как фолбэк.

Голосовые сообщения Telegram приходят в OGG/Opus — конвертация не нужна
ни для одного из вариантов.
"""
import json
import logging

import aiohttp

from config import LANGUAGES, settings

log = logging.getLogger(__name__)

_FAST_PATH = "/speechtotext/transcriptions:transcribe?api-version=2024-11-15"
_SHORT_HOST = "https://{region}.stt.speech.microsoft.com"
_SHORT_PATH = "/speech/recognition/conversation/cognitiveservices/v1?language={loc}&format=detailed"

# Локали Azure STT. Чеченского у Azure нет — в списке кандидатов его быть не может.
STT_LOCALES = {"ar": "ar-SA", "ru": "ru-RU", "en": "en-US"}

_SHORT_LIMIT_SEC = 60
_MAX_BYTES = 200 * 1024 * 1024


class SttError(Exception):
    pass


class SttUnavailable(SttError):
    """API недоступен в этом регионе — есть смысл попробовать другой."""


class AzureStt:
    def __init__(self) -> None:
        self._api: str | None = None

    def _fast_url(self) -> str:
        return f"https://{settings.azure_speech_region}.api.cognitive.microsoft.com{_FAST_PATH}"

    def _short_url(self, locale: str) -> str:
        host = _SHORT_HOST.format(region=settings.azure_speech_region)
        return host + _SHORT_PATH.format(loc=locale)

    async def _fast(
        self, session: aiohttp.ClientSession, audio: bytes, lang_hint: str | None
    ) -> tuple[str, str]:
        locales = (
            [STT_LOCALES[lang_hint]]
            if lang_hint in STT_LOCALES
            else list(STT_LOCALES.values())
        )
        definition = {"locales": locales, "profanityFilterMode": "None"}

        form = aiohttp.FormData()
        form.add_field("audio", audio, filename="voice.ogg", content_type="audio/ogg")
        form.add_field("definition", json.dumps(definition), content_type="application/json")

        headers = {"Ocp-Apim-Subscription-Key": settings.azure_speech_key}
        async with session.post(self._fast_url(), headers=headers, data=form) as resp:
            body = await resp.text()
            if resp.status in (400, 404, 405):
                raise SttUnavailable(f"Fast transcription недоступен (HTTP {resp.status})")
            if resp.status == 401:
                raise SttError("Ключ Azure Speech отклонён (401).")
            if resp.status == 429:
                raise SttError("Превышен лимит запросов Azure Speech (429).")
            if resp.status != 200:
                raise SttError(f"Azure STT вернул HTTP {resp.status}: {body[:200]}")
            data = json.loads(body)

        phrases = data.get("combinedPhrases") or []
        text = " ".join(p.get("text", "") for p in phrases).strip()
        # Локаль берём из первой распознанной фразы
        locale = ""
        for ph in data.get("phrases") or []:
            if ph.get("locale"):
                locale = ph["locale"]
                break
        return text, locale

    async def _short(
        self, session: aiohttp.ClientSession, audio: bytes, lang_hint: str | None
    ) -> tuple[str, str]:
        # Один запрос = один язык. Без подсказки перебираем кандидатов
        # и берём результат с наибольшей уверенностью.
        candidates = (
            [STT_LOCALES[lang_hint]] if lang_hint in STT_LOCALES else list(STT_LOCALES.values())
        )
        headers = {
            "Ocp-Apim-Subscription-Key": settings.azure_speech_key,
            "Content-Type": 'audio/ogg; codecs="opus"',
            "Accept": "application/json",
        }

        best: tuple[float, str, str] = (-1.0, "", "")
        for locale in candidates:
            async with session.post(self._short_url(locale), headers=headers, data=audio) as resp:
                if resp.status in (400, 404, 415):
                    raise SttUnavailable(f"Короткий REST недоступен (HTTP {resp.status})")
                if resp.status == 401:
                    raise SttError("Ключ Azure Speech отклонён (401).")
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)

            if data.get("RecognitionStatus") != "Success":
                continue
            nbest = data.get("NBest") or []
            conf = float(nbest[0].get("Confidence", 0)) if nbest else 0.0
            text = data.get("DisplayText", "")
            if conf > best[0]:
                best = (conf, text, locale)

        if best[0] < 0:
            raise SttError(
                "Речь не распознана. Возможные причины: тишина, слишком короткая запись "
                "или язык не из числа поддерживаемых (арабский, русский, английский)."
            )
        return best[1], best[2]

    async def transcribe(
        self, audio: bytes, duration_sec: int = 0, lang_hint: str | None = None
    ) -> tuple[str, str]:
        """Возвращает (текст, код языка ar/ru/en)."""
        if not settings.azure_speech_key:
            raise SttError("Azure Speech не настроен.")
        if len(audio) > _MAX_BYTES:
            raise SttError("Аудио слишком большое.")

        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            order = ["fast", "short"] if self._api is None else [self._api]
            # Короткий REST не потянет длинную запись — не пытаемся
            if duration_sec > _SHORT_LIMIT_SEC and "short" in order and len(order) == 1:
                raise SttError(
                    f"Запись длиннее {_SHORT_LIMIT_SEC} секунд, а доступен только короткий API. "
                    "Пришлите запись покороче."
                )

            last_err: Exception | None = None
            for api in order:
                if api == "short" and duration_sec > _SHORT_LIMIT_SEC:
                    continue
                try:
                    text, locale = (
                        await self._fast(session, audio, lang_hint)
                        if api == "fast"
                        else await self._short(session, audio, lang_hint)
                    )
                except SttUnavailable as e:
                    log.info("%s", e)
                    last_err = e
                    continue
                if self._api != api:
                    log.info("Azure STT: используется %s", api)
                    self._api = api
                if not text.strip():
                    raise SttError("Речь не распознана — запись пустая или неразборчивая.")
                return text, _locale_to_lang(locale, text)

        raise SttError(f"Azure STT недоступен в этом регионе. Последняя ошибка: {last_err}")


def _locale_to_lang(locale: str, text: str) -> str:
    """Локаль Azure (ru-RU) -> внутренний код (ru). Без локали — по тексту."""
    if locale:
        short = locale.split("-")[0]
        if short in LANGUAGES:
            return short
    from services.ocr import detect_language

    return detect_language(text)


stt = AzureStt()
