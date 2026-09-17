# VK Subtitles Bot

VK-бот для автоматической обработки видео: скачивание, распознавание речи (Whisper), генерация субтитров и опциональная склейка с видео. Все задачи ставятся в персистентную SQLite-очередь и обрабатываются строго по одной (экономия CPU/RAM).

## Возможности

- Принимает **любую ссылку** на видео (http/https) — мгновенно отвечает и ставит задачу в очередь
- Очередь на **SQLite** (`jobs.sqlite3`) — переживает перезапуск бота, обработка строго последовательная
- Распознавание речи через **faster-whisper** (модель `medium`, CPU, int8)
- Точная синхронизация субтитров (`word_timestamps`) — без «плавающих» таймкодов
- Готовые файлы сохраняются в `video_subtitled/` **навсегда** (без автоудаления)
- Команда `/cancel` отменяет все задачи пользователя в очереди

## Установка

```bash
pip install -r requirements.txt
```

Дополнительно нужен бинарник **ffmpeg** в PATH:
- Windows: `winget install ffmpeg` или `choco install ffmpeg`

## Настройка токена

Токен задаётся переменной окружения:

```powershell
$env:VK_TOKEN = "токен_сообщества"
python bot.py
```

## Боты

| Файл | Что делает |
|---|---|
| `bot.py` | Распознавание + склейка (субтитры вшиваются в кадр) |
| `bot_subtitles_ru.py` | Распознавание + склейка (русскоязычные субтитры) |
| `bot_subtitles_only.py` | Только субтитры, без склейки |
| `transcribe_move.py` | Транскрипция с переводом в английский; исходное видео и `.srt` переносятся в `video_subtitled/` без удаления |

Любой бот запускается одинаково:

```bash
python bot.py
```

## Как это работает

1. Пользователь присылает ссылку → задача пишется в `jobs.sqlite3` (status=`queued`)
2. Фоновый воркер берёт старейшую задачу (`ORDER BY id DESC ... LIMIT 1`), ставит `processing`
3. Пайплайн: `yt-dlp` скачивание → `faster-whisper` транскрипция (`.srt`) → при `merge=True` ffmpeg-склейка (субтитры вшиты в кадр)
4. Статус `done`/`failed`/`cancelled` + `result`/`error` записываются в БД
5. Пользователю уходят сообщения о прогрессе и готовом файле

## Структура проекта

```
vk-subtitles-bot/
├── bot.py, bot_subtitles_only.py, bot_subtitles_ru.py  # VK-боты
├── transcribe_move.py                                  # бот: транскрипция + перенос файлов
├── config.py          # пути, настройки Whisper и ffmpeg
├── queue_manager.py   # SQLite-очередь + последовательный воркер
├── downloader.py      # yt-dlp (загрузка видео)
├── translator.py      # faster-whisper → .srt
├── merger.py          # ffmpeg-склейка + очистка временных файлов
├── pipeline.py        # связка этапов обработки
├── video_for_whisper/ # рабочая папка (скачанные видео), очищается
├── video_subtitled/   # результаты: сохраняются навсегда
└── jobs.sqlite3       # очередь задач
```

## Настройки (`config.py`)

| Параметр | Значение по умолчанию | Описание |
|---|---|---|
| `WHISPER_MODEL` | `medium` | модель whisper (small/medium/large-v3…) |
| `WHISPER_DEVICE` | `cpu` | устройство (cpu/cuda) |
| `WHISPER_COMPUTE_TYPE` | `int8` | точность вычислений |
| `WHISPER_WORD_TIMESTAMPS` | `True` | точное выравнивание таймкодов по словам |
| `SUBTITLE_SHIFT_SECONDS` | `0.0` | ручной сдвиг субтитров (например `0.3` — позже) |
| `BURN_SUBTITLES` | `True` | вшивать субтитры в кадр; `False` — мягкие (поток mov_text) |

> **Про перевод RU→EN:** whisper умеет переводить только в английский (`task="translate"`, используется в `transcribe_move.py`). Полноценный перевод в русский требует внешнего API — не реализован.

## Полезное

- `jobs.sqlite3`: статусы задач — `queued / processing / done / failed / cancelled`
- Модель Whisper скачивается автоматически при первом запуске (~1.5 ГБ для `medium`)
- ffmpeg-склейка работает с неограниченной длиной видео (stream-copy / перекодирование зависит от `BURN_SUBTITLES`)