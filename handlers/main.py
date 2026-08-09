"""Основные хендлеры бота."""
import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from config import LANGUAGES
from handlers.ui import (
    BTN_LANG,
    BTN_PHOTO,
    BTN_VOICE,
    BTN_TEXT,
    BTN_TRANSLATE,
    Flow,
    after_ocr_keyboard,
    auto_lang_keyboard,
    lang_keyboard,
    main_menu,
)
from services import ocr, recognize, translate
from services.stt import SttError, stt
from services.tts import TtsError, tts

log = logging.getLogger(__name__)
router = Router()

WELCOME = (
    "<b>Бот озвучки и перевода</b>\n\n"
    "Что умею:\n"
    "• 🖼 Распознаю текст с фото и озвучиваю его\n"
    "• 🎧 Расшифровываю голосовые сообщения в текст\n"
    "• 🔊 Озвучиваю присланный текст\n"
    "• 🌐 Перевожу построчно — оригинал и перевод рядом\n\n"
    "Языки: арабский, английский, русский, чеченский.\n"
    "Просто пришлите фото или текст — язык определю сам."
)


# ---- /start, меню ---------------------------------------------------------
@router.message(Command("start", "help"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.idle)
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(F.text == BTN_LANG)
@router.message(Command("lang"))
async def choose_lang(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    current = data.get("lang", "auto")
    label = "автоопределение" if current == "auto" else LANGUAGES[current]["name"]
    await message.answer(
        f"Текущий режим: <b>{label}</b>\n\nВыберите язык озвучки:",
        reply_markup=auto_lang_keyboard(),
    )


@router.callback_query(F.data.startswith("setlang:"))
async def set_lang(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data.split(":", 1)[1]
    await state.update_data(lang=code)
    label = "автоопределение" if code == "auto" else LANGUAGES[code]["name"]
    note = ""
    if code != "auto" and LANGUAGES[code].get("approx_tts"):
        note = (
            "\n\n⚠️ Чеченский: озвучка русским голосом (с акцентом, но разборчиво). "
            "Распознавание речи и перевод для чеченского недоступны."
        )
    await cb.message.edit_text(f"Готово. Режим озвучки: <b>{label}</b>{note}")
    await cb.answer()


@router.message(F.text == BTN_PHOTO)
async def ask_photo(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.idle)
    await message.answer("Пришлите фото с текстом 📷\n\nЛучше всего: хороший свет, страница целиком, без наклона.")


@router.message(F.text == BTN_VOICE)
async def ask_voice(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.idle)
    await message.answer(
        "Запишите или пришлите голосовое сообщение 🎤\n\n"
        "Языки: арабский, русский, английский. Чеченский распознавание речи не поддерживает."
    )


@router.message(F.text == BTN_TEXT)
async def ask_text(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.await_text_for_tts)
    await message.answer("Пришлите текст для озвучки.")


@router.message(F.text == BTN_TRANSLATE)
async def ask_translate(message: Message, state: FSMContext) -> None:
    await state.set_state(Flow.await_text_for_translate)
    await message.answer("Пришлите текст — переведу построчно.")


# ---- Голосовое / аудио → текст --------------------------------------------
@router.message(F.voice | F.audio | (F.document & F.document.mime_type.startswith("audio/")))
async def handle_voice(message: Message, state: FSMContext) -> None:
    media = message.voice or message.audio or message.document
    duration = getattr(media, "duration", 0) or 0

    status = await message.answer("🎧 Распознаю речь…")
    try:
        buf = await message.bot.download(media.file_id)
        audio = buf.read()

        data = await state.get_data()
        hint = data.get("lang")
        hint = None if hint in (None, "auto") else hint

        text, lang = await stt.transcribe(audio, duration, hint)
    except SttError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception:  # noqa: BLE001
        log.exception("STT pipeline failed")
        await status.edit_text("❌ Не удалось обработать аудио. Попробуйте другую запись.")
        return

    await state.update_data(last_text=text, last_lang=lang)
    await status.edit_text(
        f"<b>Расшифровка</b> ({LANGUAGES[lang]['name']}):\n\n"
        f"<pre>{html.escape(text[:3500])}</pre>",
        reply_markup=after_ocr_keyboard(),
    )


# ---- Фото → OCR → озвучка -------------------------------------------------
@router.message(F.photo | F.document)
async def handle_image(message: Message, state: FSMContext) -> None:
    if message.document and not (message.document.mime_type or "").startswith("image/"):
        await message.answer("Это не изображение. Пришлите фото или файл JPG/PNG.")
        return

    status = await message.answer("🔍 Распознаю текст…")
    try:
        file_id = message.photo[-1].file_id if message.photo else message.document.file_id
        buf = await message.bot.download(file_id)
        raw = buf.read()

        data = await state.get_data()
        hint = data.get("lang")
        hint = None if hint in (None, "auto") else hint

        text, lang, engine = await recognize.recognize(raw, hint)
    except ocr.OcrError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception:  # noqa: BLE001
        log.exception("OCR pipeline failed")
        await status.edit_text("❌ Не удалось обработать изображение. Попробуйте другое фото.")
        return

    await state.update_data(last_text=text, last_lang=lang)
    await status.edit_text(
        f"<b>Распознано</b> ({LANGUAGES[lang]['name']}, {engine}):\n\n"
        f"<pre>{html.escape(text[:3500])}</pre>",
        reply_markup=after_ocr_keyboard(),
    )
    await _send_voice(message, text, lang)


# ---- Текст → озвучка ------------------------------------------------------
@router.message(Flow.await_text_for_tts, F.text)
async def handle_tts_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    hint = data.get("lang")
    lang = hint if hint and hint != "auto" else ocr.detect_language(message.text)
    await state.update_data(last_text=message.text, last_lang=lang)
    await _send_voice(message, message.text, lang)
    await state.set_state(Flow.idle)


# ---- Построчный перевод ---------------------------------------------------
@router.message(Flow.await_text_for_translate, F.text)
async def handle_translate_text(message: Message, state: FSMContext) -> None:
    lang = ocr.detect_language(message.text)
    await state.update_data(last_text=message.text, last_lang=lang)
    await message.answer(
        f"Язык оригинала: <b>{LANGUAGES[lang]['name']}</b>\nНа какой язык перевести?",
        reply_markup=lang_keyboard("tr", exclude=lang),
    )
    await state.set_state(Flow.idle)


@router.callback_query(F.data == "tr_last")
async def translate_last(cb: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("last_text"):
        await cb.answer("Нет текста для перевода — пришлите фото или текст.", show_alert=True)
        return
    src = data["last_lang"]
    await cb.message.answer("На какой язык перевести?", reply_markup=lang_keyboard("tr", exclude=src))
    await cb.answer()


@router.callback_query(F.data.startswith("tr:"))
async def do_translate(cb: CallbackQuery, state: FSMContext) -> None:
    dst = cb.data.split(":", 1)[1]
    data = await state.get_data()
    text, src = data.get("last_text"), data.get("last_lang")
    if not text:
        await cb.answer("Текст потерялся — пришлите заново.", show_alert=True)
        return

    await cb.answer()
    status = await cb.message.answer("🌐 Перевожу…")
    lines = ocr.split_lines(text)
    try:
        translated = await translate.translate_lines(lines, src, dst)
    except translate.TranslateError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception:  # noqa: BLE001
        log.exception("Translate failed")
        await status.edit_text("❌ Ошибка перевода. Попробуйте позже.")
        return

    blocks = []
    for orig, tr in zip(lines, translated):
        blocks.append(f"<b>{html.escape(orig)}</b>\n{html.escape(tr)}")

    out = "\n\n".join(blocks)
    await status.delete()
    for chunk in _chunks(out, 3800):
        await cb.message.answer(chunk)


# ---- Свободный текст без состояния ---------------------------------------
@router.message(F.text & ~F.text.startswith("/"))
async def handle_free_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    hint = data.get("lang")
    lang = hint if hint and hint != "auto" else ocr.detect_language(message.text)
    await state.update_data(last_text=message.text, last_lang=lang)
    await _send_voice(message, message.text, lang, extra_kb=True)


# ---- Вспомогательное ------------------------------------------------------
async def _send_voice(message: Message, text: str, lang: str, extra_kb: bool = False) -> None:
    status = await message.answer("🔊 Озвучиваю…")
    # Арабский: снимаем огласовки. Tesseract часто ставит харакат неверно,
    # а голос ar-SA расставляет огласовки сам — неверный харакат портит озвучку.
    tts_text = ocr.strip_harakat(text) if lang == "ar" else text
    try:
        audio = await tts.synthesize(tts_text, lang)
    except TtsError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception:  # noqa: BLE001
        log.exception("TTS failed")
        await status.edit_text("❌ Ошибка озвучки. Попробуйте позже.")
        return

    await status.delete()
    caption = LANGUAGES[lang]["name"]
    if LANGUAGES[lang].get("approx_tts"):
        caption += " (русский голос — приближение)"
    await message.answer_voice(
        BufferedInputFile(audio, filename="speech.ogg"),
        caption=f"🔊 {caption}",
        reply_markup=after_ocr_keyboard() if extra_kb else None,
    )


def _chunks(text: str, size: int):
    parts, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > size:
            if cur:
                parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return parts
