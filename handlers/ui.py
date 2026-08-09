"""Клавиатуры и состояния FSM."""
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from config import LANGUAGES

BTN_PHOTO = "🖼 Фото → текст + озвучка"
BTN_VOICE = "🎧 Голосовое → текст"
BTN_TEXT = "🔊 Текст → озвучка"
BTN_TRANSLATE = "🌐 Построчный перевод"
BTN_LANG = "⚙️ Язык озвучки"


class Flow(StatesGroup):
    idle = State()
    await_text_for_tts = State()
    await_text_for_translate = State()
    await_translate_target = State()


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_PHOTO), KeyboardButton(text=BTN_VOICE)],
            [KeyboardButton(text=BTN_TEXT), KeyboardButton(text=BTN_TRANSLATE)],
            [KeyboardButton(text=BTN_LANG)],
        ],
        resize_keyboard=True,
    )


def lang_keyboard(prefix: str, exclude: str | None = None) -> InlineKeyboardMarkup:
    rows = []
    for code, meta in LANGUAGES.items():
        if code == exclude:
            continue
        rows.append([InlineKeyboardButton(text=meta["name"], callback_data=f"{prefix}:{code}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def auto_lang_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🔍 Определить автоматически", callback_data="setlang:auto")]]
    rows += lang_keyboard("setlang").inline_keyboard
    return InlineKeyboardMarkup(inline_keyboard=rows)


def after_ocr_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🌐 Перевести построчно", callback_data="tr_last")]]
    )
