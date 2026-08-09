"""Озвучка через Azure Speech REST API (без нативного SDK — проще на VPS)."""
import logging
import time
from xml.sax.saxutils import escape

import aiohttp

from config import LANGUAGES, settings

log = logging.getLogger(__name__)

_TOKEN_URL = "https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
_TTS_URL = "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"

# Telegram voice messages = OGG/Opus, отдаётся Azure напрямую
_OUTPUT_FORMAT = "ogg-48khz-16bit-mono-opus"


class TtsError(Exception):
    pass


class AzureTts:
    """Держит access token и переиспользует его (живёт 10 минут)."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._token_ts: float = 0.0

    async def _get_token(self, session: aiohttp.ClientSession) -> str:
        if self._token and time.time() - self._token_ts < 540:
            return self._token
        url = _TOKEN_URL.format(region=settings.azure_speech_region)
        headers = {"Ocp-Apim-Subscription-Key": settings.azure_speech_key}
        async with session.post(url, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.error("Azure token error %s: %s", resp.status, body[:300])
                raise TtsError(
                    "Не удалось авторизоваться в Azure Speech. "
                    "Проверьте AZURE_SPEECH_KEY / AZURE_SPEECH_REGION и доступность Azure с этого сервера."
                )
            self._token = await resp.text()
            self._token_ts = time.time()
            return self._token

    async def synthesize(self, text: str, lang_code: str) -> bytes:
        lang = LANGUAGES.get(lang_code) or LANGUAGES["ru"]
        voice = lang["voice"]
        locale = "-".join(voice.split("-")[:2])

        text = text.strip()[: settings.max_tts_chars]
        if not text:
            raise TtsError("Пустой текст — нечего озвучивать.")

        ssml = (
            f'<speak version="1.0" xml:lang="{locale}">'
            f'<voice name="{voice}">'
            f'<prosody rate="-5%">{escape(text)}</prosody>'
            f"</voice></speak>"
        )

        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            token = await self._get_token(session)
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
                "User-Agent": "tts-telegram-bot",
            }
            url = _TTS_URL.format(region=settings.azure_speech_region)
            async with session.post(url, headers=headers, data=ssml.encode("utf-8")) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error("Azure TTS error %s: %s", resp.status, body[:300])
                    raise TtsError(f"Ошибка синтеза речи (HTTP {resp.status}).")
                audio = await resp.read()

        if not audio:
            raise TtsError("Azure вернул пустой аудиофайл.")
        return audio


tts = AzureTts()
