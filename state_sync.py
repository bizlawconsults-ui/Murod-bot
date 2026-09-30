"""
Персистентность локальных JSON-файлов состояния бота через Telegram
"Избранное" (Saved Messages) первого аккаунта — без Railway Volume и
любой другой внешней инфраструктуры.

Как это работает:
- Все *.json файлы состояния (список ниже) не трогаем по отдельности —
  раз в STATE_SYNC_INTERVAL_MIN минут (и один раз при штатной остановке
  процесса) собираем их все в один .zip и отправляем сообщением в Saved
  Messages первого аккаунта с фиксированной подписью STATE_BACKUP_TAG.
  Старые бэкапы после этого подчищаются — оставляем только последние
  несколько, чтобы Saved Messages не засорялись.
- При старте процесса, ДО того как остальной код бота успеет прочитать
  локальные json (через load_json), ищем в Saved Messages последнее
  сообщение с этой меткой, скачиваем .zip и распаковываем поверх
  локальной папки — так что после редеплоя на Railway (где диск
  эфемерный) бот стартует с тем состоянием, на котором остановился.
- Если в Saved Messages ничего не нашли (первый запуск) — просто
  работаем с пустыми файлами, как и раньше без этого модуля.

Всё лучшее-старание (best-effort): любая ошибка здесь (нет сети, Telegram
недоступен и т.п.) не должна ронять бота — она просто логируется, и бот
продолжает работать с локальными файлами как есть.
"""
import asyncio
import os
import zipfile
from telethon import TelegramClient
from telethon.sessions import StringSession

STATE_FILES = [
    'replied.json',
    'groups.json',
    'daily_stats.json',
]

STATE_SYNC_ENABLED = os.getenv('STATE_SYNC_ENABLED', '1') != '0'
STATE_BACKUP_TAG = '#boostcent_state_backup'
STATE_ARCHIVE_NAME = 'bot_state_backup.zip'
STATE_SYNC_INTERVAL_MIN = int(os.getenv('STATE_SYNC_INTERVAL_MIN', '15'))
STATE_BACKUPS_TO_KEEP = int(os.getenv('STATE_BACKUPS_TO_KEEP', '2'))


def _zip_state(archive_path: str):
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname in STATE_FILES:
            if os.path.exists(fname):
                zf.write(fname)


def _unzip_state(archive_path: str):
    with zipfile.ZipFile(archive_path, 'r') as zf:
        zf.extractall('.')


async def restore_state(api_id: int, api_hash: str, session_string: str):
    """Скачивает и распаковывает последний бэкап состояния из Saved
    Messages ДО того, как остальной код бота прочитает локальные json.
    Использует отдельное короткоживущее подключение — не мешает клиенту,
    который потом будет работать в run_account()."""
    if not STATE_SYNC_ENABLED:
        return
    client = TelegramClient(StringSession(session_string), api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            print("⚠️ [state_sync] Сессия для восстановления состояния недействительна — пропускаю.")
            return
        async for msg in client.iter_messages('me', search=STATE_BACKUP_TAG, limit=5):
            if msg.file:
                path = await msg.download_media(file=STATE_ARCHIVE_NAME)
                _unzip_state(path)
                print(f"♻️ [state_sync] Состояние восстановлено из Saved Messages "
                      f"(бэкап от {msg.date}).")
                return
        print("ℹ️ [state_sync] Бэкап состояния в Saved Messages не найден — старт с чистого листа.")
    except Exception as e:
        print(f"⚠️ [state_sync] Не удалось восстановить состояние из Telegram: {e}")
    finally:
        await client.disconnect()


async def _cleanup_old_backups(client: TelegramClient):
    try:
        old_backups = []
        async for msg in client.iter_messages('me', search=STATE_BACKUP_TAG, limit=50):
            if msg.file:
                old_backups.append(msg)
        # iter_messages отдаёт от новых к старым — оставляем самые свежие,
        # остальные (старее) удаляем, чтобы Saved Messages не пухли.
        stale = old_backups[STATE_BACKUPS_TO_KEEP:]
        if stale:
            await client.delete_messages('me', [m.id for m in stale])
    except Exception as e:
        print(f"⚠️ [state_sync] Не удалось подчистить старые бэкапы: {e}")


async def backup_state(client: TelegramClient):
    """Заливает текущее состояние в Saved Messages одним архивом и
    подчищает старые бэкапы. Вызывается и периодически, и один раз перед
    остановкой процесса."""
    if not STATE_SYNC_ENABLED:
        return
    existing = [f for f in STATE_FILES if os.path.exists(f)]
    if not existing:
        return
    try:
        _zip_state(STATE_ARCHIVE_NAME)
        await client.send_file('me', STATE_ARCHIVE_NAME, caption=STATE_BACKUP_TAG)
        await _cleanup_old_backups(client)
    except Exception as e:
        print(f"⚠️ [state_sync] Не удалось сохранить состояние в Telegram: {e}")


async def state_sync_loop(client: TelegramClient):
    """Фоновая задача: периодически бэкапит состояние в Saved Messages."""
    if not STATE_SYNC_ENABLED:
        return
    print(f"💾 [state_sync] Синхронизация состояния с Saved Messages включена "
          f"(каждые {STATE_SYNC_INTERVAL_MIN} мин, храню последние {STATE_BACKUPS_TO_KEEP} бэкапа).")
    while True:
        await asyncio.sleep(STATE_SYNC_INTERVAL_MIN * 60)
        await backup_state(client)
