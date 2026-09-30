"""
Запусти локально ОДИН РАЗ на НОВОМ аккаунте (не на основном лид-боте!),
чтобы получить SESSION_STRING для Railway.

1. pip install telethon python-dotenv
2. Создай .env с API_ID и API_HASH (можно взять с https://my.telegram.org
   с того же приложения, что и у основного бота — это ОК, api_id/api_hash
   не привязаны к конкретному аккаунту)
3. python3 auth.py
4. Введи номер телефона НОВОГО аккаунта и код из Telegram
5. Скопируй строку, которая распечатается, в Railway → SESSION_STRING
"""
import os
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from dotenv import load_dotenv

load_dotenv()

api_id   = int(os.getenv('API_ID'))
api_hash = os.getenv('API_HASH')

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session_string = client.session.save()
    me = client.get_me()
    print(f"✅ Авторизован как: {me.first_name} (id: {me.id})")
    print("\n📋 Скопируй это значение в Railway → SESSION_STRING:\n")
    print(session_string)
