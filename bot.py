import asyncio
import logging
import sqlite3
from contextlib import closing

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter, TelegramBadRequest

# ==== НАСТРОЙКИ ====
BOT_TOKEN = "ВСТАВЬ_СЮДА_ТОКЕН_БОТА"          # токен от @BotFather
ADMIN_IDS = {123456789}                        # твой telegram user_id (можно несколько через запятую)
DB_PATH = "subscribers.db"
# ====================

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                joined_at TEXT DEFAULT CURRENT_TIMESTAMP,
                active INTEGER DEFAULT 1
            )
            """
        )
        conn.commit()


def add_subscriber(user_id: int, username: str, full_name: str):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO subscribers (user_id, username, full_name, active)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET active = 1, username = excluded.username, full_name = excluded.full_name
            """,
            (user_id, username, full_name),
        )
        conn.commit()


def deactivate_subscriber(user_id: int):
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute("UPDATE subscribers SET active = 0 WHERE user_id = ?", (user_id,))
        conn.commit()


def get_active_subscribers() -> list[int]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute("SELECT user_id FROM subscribers WHERE active = 1").fetchall()
    return [r[0] for r in rows]


def get_stats() -> tuple[int, int]:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        active = conn.execute("SELECT COUNT(*) FROM subscribers WHERE active = 1").fetchone()[0]
    return total, active


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@dp.message(CommandStart())
async def cmd_start(message: Message):
    add_subscriber(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await message.answer("Привет! Ты подписан(а) на рассылку ✅")


@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    total, active = get_stats()
    await message.answer(f"Всего в базе: {total}\nАктивных подписчиков: {active}")


@dp.message(Command("broadcast"))
async def cmd_broadcast(message: Message):
    """
    Использование:
    1) Ответь этой командой (реплаем) на сообщение, которое нужно разослать —
       так можно рассылать текст, фото, видео, документы, с подписью и т.д.
    2) Либо напиши текст сразу после команды: /broadcast Привет всем!
    """
    if not is_admin(message.from_user.id):
        return

    source_message = message.reply_to_message
    text_after_command = message.text.partition(" ")[2].strip() if message.text else ""

    if not source_message and not text_after_command:
        await message.answer(
            "Отправь команду /broadcast ответом (reply) на сообщение для рассылки,\n"
            "или так: /broadcast текст сообщения"
        )
        return

    subscribers = get_active_subscribers()
    if not subscribers:
        await message.answer("Подписчиков пока нет.")
        return

    status_msg = await message.answer(f"Начинаю рассылку на {len(subscribers)} чел...")

    sent = 0
    failed = 0

    for user_id in subscribers:
        try:
            if source_message:
                await source_message.copy_to(chat_id=user_id)
            else:
                await bot.send_message(chat_id=user_id, text=text_after_command)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                if source_message:
                    await source_message.copy_to(chat_id=user_id)
                else:
                    await bot.send_message(chat_id=user_id, text=text_after_command)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            # пользователь заблокировал бота — отключаем его из активных
            deactivate_subscriber(user_id)
            failed += 1
        except TelegramBadRequest:
            failed += 1
        except Exception as e:
            logging.warning(f"Не удалось отправить {user_id}: {e}")
            failed += 1

        # небольшая пауза, чтобы не словить общий флуд-лимит Telegram (~30 сообщений/сек)
        await asyncio.sleep(0.05)

    await status_msg.edit_text(f"Готово ✅\nОтправлено: {sent}\nОшибок: {failed}")


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
