"""OCR через Azure Vision Read — качественнее Tesseract, особенно на арабице.

Поддерживаются два API, потому что доступность зависит от региона ресурса:
  1. Image Analysis 4.0 — синхронный, один запрос. Доступен не во всех регионах.
  2. Read 3.2 — асинхронный (запрос + опрос результата), доступен шире.

Модуль сам определяет, какой работает, и запоминает выбор на время жизни процесса.
"""
import asyncio
import logging

import aiohttp

from config import settings

log = logging.getLogger(__name__)

_IA40_PATH = "/computervision/imageanalysis:analyze?api-version=2024-02-01&features=read"
_READ32_PATH = "/vision/v3.2/read/analyze"

# Azure Vision требует минимум 50x50 px и не более 20 МБ
_MAX_BYTES = 20 * 1024 * 1024


class VisionError(Exception):
    pass


class VisionUnavailable(VisionError):
    """API недоступен в этом регионе — есть смысл попробовать другой."""


def enabled() -> bool:
    return bool(settings.azure_vision_key and settings.azure_vision_endpoint)


class AzureVision:
    def __init__(self) -> None:
        # None = ещё не выяснили; "ia40" / "read32" = рабочий вариант
        self._api: str | None = None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Ocp-Apim-Subscription-Key": settings.azure_vision_key,
            "Content-Type": "application/octet-stream",
        }

    def _url(self, path: str) -> str:
        return settings.azure_vision_endpoint.rstrip("/") + path

    async def _image_analysis_40(self, session: aiohttp.ClientSession, raw: bytes) -> str:
        async with session.post(self._url(_IA40_PATH), headers=self._headers, data=raw) as resp:
            body = await resp.text()
            if resp.status in (400, 404, 405):
                # Неверный api-version или фича недоступна в регионе
                raise VisionUnavailable(f"Image Analysis 4.0 недоступен (HTTP {resp.status})")
            if resp.status == 401:
                raise VisionError("Ключ Azure Vision отклонён (401). Проверьте ключ и регион.")
            if resp.status == 429:
                raise VisionError("Превышен лимит запросов Azure Vision (429).")
            if resp.status != 200:
                raise VisionError(f"Azure Vision вернул HTTP {resp.status}: {body[:200]}")
            data = await resp.json(content_type=None)

        blocks = (data.get("readResult") or {}).get("blocks") or []
        lines = [ln.get("text", "") for b in blocks for ln in b.get("lines", [])]
        return "\n".join(l for l in lines if l.strip())

    async def _read_32(self, session: aiohttp.ClientSession, raw: bytes) -> str:
        async with session.post(self._url(_READ32_PATH), headers=self._headers, data=raw) as resp:
            if resp.status in (400, 404, 405):
                raise VisionUnavailable(f"Read 3.2 недоступен (HTTP {resp.status})")
            if resp.status == 401:
                raise VisionError("Ключ Azure Vision отклонён (401). Проверьте ключ и регион.")
            if resp.status != 202:
                body = await resp.text()
                raise VisionError(f"Read 3.2 вернул HTTP {resp.status}: {body[:200]}")
            op_url = resp.headers.get("Operation-Location")

        if not op_url:
            raise VisionError("Read 3.2 не вернул Operation-Location.")

        poll_headers = {"Ocp-Apim-Subscription-Key": settings.azure_vision_key}
        for attempt in range(30):
            await asyncio.sleep(0.6 if attempt < 5 else 1.2)
            async with session.get(op_url, headers=poll_headers) as resp:
                if resp.status != 200:
                    raise VisionError(f"Опрос Read 3.2 вернул HTTP {resp.status}")
                data = await resp.json(content_type=None)
            status = (data.get("status") or "").lower()
            if status == "succeeded":
                pages = (data.get("analyzeResult") or {}).get("readResults") or []
                lines = [ln.get("text", "") for p in pages for ln in p.get("lines", [])]
                return "\n".join(l for l in lines if l.strip())
            if status == "failed":
                raise VisionError("Read 3.2: распознавание завершилось ошибкой.")

        raise VisionError("Read 3.2: превышено время ожидания результата.")

    async def read_text(self, raw: bytes) -> str:
        """Распознаёт текст. Бросает VisionError, если не удалось."""
        if not enabled():
            raise VisionError("Azure Vision не настроен.")
        if len(raw) > _MAX_BYTES:
            raise VisionError("Изображение больше 20 МБ — предел Azure Vision.")

        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            order = ["ia40", "read32"] if self._api is None else [self._api]
            last_err: Exception | None = None

            for api in order:
                try:
                    if api == "ia40":
                        text = await self._image_analysis_40(session, raw)
                    else:
                        text = await self._read_32(session, raw)
                except VisionUnavailable as e:
                    log.info("%s", e)
                    last_err = e
                    continue
                except VisionError:
                    raise
                if self._api != api:
                    log.info("Azure Vision: используется %s", api)
                    self._api = api
                return text

        raise VisionError(
            "Ни Image Analysis 4.0, ни Read 3.2 недоступны в этом регионе. "
            f"Последняя ошибка: {last_err}"
        )


vision = AzureVision()
