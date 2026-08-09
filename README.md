# Telegram-бот: изображение → текст → озвучка + построчный перевод

Языки: арабский, английский, русский, чеченский.

## Что делает

| Функция | Как работает |
|---|---|
| 🖼 Фото → текст + озвучка | Tesseract OCR (ara+rus+eng) → автоопределение языка → Azure TTS → voice message |
| 🔊 Текст → озвучка | Azure Neural TTS, формат OGG/Opus (нативный для Telegram) |
| 🌐 Построчный перевод | Azure Translator, вывод «оригинал / перевод» построчно |

## Важные ограничения по чеченскому

1. **OCR** — отдельного пакета Tesseract для чеченского нет. Используется русский (`rus`): алфавит совпадает почти полностью, спецсимвол `Ӏ` иногда распознаётся как `1`, `I` или `Т`.
2. **TTS** — своего голоса нет ни у одного крупного провайдера. Используется русский голос `ru-RU-DmitryNeural` как приближение: звучит с акцентом, но разборчиво. Бот честно помечает это в подписи к аудио.
3. **Перевод** — Azure Translator **не поддерживает** чеченский. Бот выдаёт понятное сообщение вместо мусорного перевода. Чтобы включить чеченский перевод, нужен **Google Translate v3** (чеченский добавлен в 2024) или **Yandex Translate API**. Точка расширения — `services/translate.py`.

Определение чеченского vs русского — эвристика по спецсимволам (`Ӏ`, диграфы `аь/оь/уь/къ/кх/хь`) плюс список частотных служебных слов. Точность на тестовом наборе 12/12.

## Локальный запуск

```bash
# Tesseract с языковыми пакетами
sudo apt install tesseract-ocr tesseract-ocr-ara tesseract-ocr-rus tesseract-ocr-eng

python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # заполнить токены
python bot.py
```

## Токены

| Переменная | Где взять |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) → `/newbot` |
| `AZURE_SPEECH_KEY` + `AZURE_SPEECH_REGION` | portal.azure.com → Speech service → тариф **F0** (бесплатно, ~500k символов/мес) |
| `AZURE_TRANSLATOR_KEY` | portal.azure.com → Translator → тариф **F0** (бесплатно, 2M символов/мес). Опционально — без него работает только озвучка |

## Деплой на VPS (Hostinger)

```bash
# на сервере, от root
mkdir -p /opt/ttsbot
# скопировать файлы: scp -r ./* root@ВАШ_IP:/opt/ttsbot/
cd /opt/ttsbot
bash deploy/install.sh
nano .env          # вписать токены
systemctl restart ttsbot
journalctl -u ttsbot -f
```

Бот работает через **long polling** — домен, SSL и открытые порты не нужны.

### Предполётная проверка (обязательно перед первым запуском)

```bash
cd /opt/ttsbot && ./venv/bin/python check.py
```

Скрипт проверяет: версию Tesseract и наличие пакетов `ara/rus/eng`, валидность Telegram-токена, доступность Azure Speech из этой сети, наличие всех нужных голосов в вашем регионе (и предлагает замену, если голоса нет), работу Translator.

Регион Speech-ресурса: **Qatar Central** (`qatarcentral`). TTS в этом регионе поддерживается, эндпоинт `https://qatarcentral.tts.speech.microsoft.com/cognitiveservices/v1`.

Если Azure недоступен из датацентра Hostinger — варианты: сменить локацию VPS, проксировать запросы, либо перейти на локальный TTS (Silero — бесплатно, хорошо для русского, но арабский не умеет).

## Структура

```
bot.py                  точка входа, long polling
config.py               настройки + таблица языков и голосов
services/ocr.py         Tesseract + предобработка + определение языка
services/tts.py         Azure Speech REST, кэш токена
services/translate.py   Azure Translator, построчно
handlers/main.py        хендлеры
handlers/ui.py          клавиатуры, FSM
deploy/                 install.sh + systemd unit
```

## Что дальше (идеи)

- Google Translate v3 для чеченского перевода
- Кэш аудио по хешу текста — экономия квоты Azure
- Выбор голоса (мужской/женский) и скорости речи
- Обработка PDF, а не только фото
- Rate limiting на пользователя
