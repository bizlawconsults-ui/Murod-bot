"""
Boostcent Outreach — Telethon-юзербот, работает ТОЛЬКО в группах.

1. Увидел в группе сообщение с ключевым словом (см. matcher.py) — отвечает
   на него в этой же группе (reply) на русском или узбекском и присылает
   тебе уведомление о лиде.
2. Если в группе за последние PROMO_INTERVAL_HOURS (24 ч) бот ничего не
   писал, он один раз публикует в ней обычное рекламное сообщение.
В одной группе бот пишет не больше MAX_POSTS_PER_GROUP_24H (2) сообщений
за 24 часа. В личку бот никому не пишет. Раз в сутки присылает отчёт.
"""
import asyncio
import itertools
import json
import os
import random
import signal
import time
from datetime import date, datetime, timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

from telethon import TelegramClient, events  # noqa: E402
from telethon.errors import (  # noqa: E402
    AuthKeyUnregisteredError, ChannelPrivateError, ChatAdminRequiredError,
    ChatRestrictedError, ChatWriteForbiddenError, FloodWaitError,
    SlowModeWaitError, UserBannedInChannelError, UserDeactivatedBanError,
    UserDeactivatedError,
)
from telethon.sessions import StringSession  # noqa: E402
from telethon.tl.types import User  # noqa: E402

import state_sync  # noqa: E402
from contact_extract import extract_contact  # noqa: E402
from matcher import find_keyword, is_seller_or_ad  # noqa: E402
from messages import detect_language, pick_text  # noqa: E402

# ──────────────────────────────────────────────────────────────
# НАСТРОЙКИ
# ──────────────────────────────────────────────────────────────


def _require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise ValueError(f"❌ Переменная окружения '{key}' не задана")
    return val


def _load_session_strings() -> list:
    sessions = []
    i = 1
    while True:
        s = os.getenv(f'SESSION_STRING_{i}')
        if not s:
            break
        sessions.append(s)
        i += 1
    if not sessions:
        single = os.getenv('SESSION_STRING')
        if single:
            sessions.append(single)
    return sessions


API_ID = int(_require_env('API_ID'))
API_HASH = _require_env('API_HASH')

TZ_OFFSET = float(os.getenv('TZ_OFFSET_HOURS', '5'))        # Узбекистан = UTC+5
ACTIVE_START = int(os.getenv('ACTIVE_HOUR_START', '9'))
ACTIVE_END = int(os.getenv('ACTIVE_HOUR_END', '22'))
MIN_DELAY = int(os.getenv('MIN_DELAY_SEC', '90'))
MAX_DELAY = int(os.getenv('MAX_DELAY_SEC', '240'))
TARGET_MIN = int(os.getenv('TARGET_DAILY_MIN', '50'))
TARGET_MAX = int(os.getenv('TARGET_DAILY_MAX', '80'))
SKIP_WARMUP = os.getenv('SKIP_WARMUP', '0') == '1'
MAX_QUEUE = int(os.getenv('MAX_QUEUE', '200'))
MAX_LEAD_AGE_H = float(os.getenv('MAX_LEAD_AGE_HOURS', '2'))   # ответ на старое сообщение смысла не имеет
MAX_TEXT_LEN = int(os.getenv('MAX_TEXT_LEN', '500'))
PROMO_INTERVAL_H = float(os.getenv('PROMO_INTERVAL_HOURS', '24'))
MAX_POSTS = int(os.getenv('MAX_POSTS_PER_GROUP_24H', '2'))
REPORT_DAILY = os.getenv('REPORT_DAILY', '1') != '0'
REPORT_HOUR = int(os.getenv('REPORT_HOUR', '21'))             # локальное время (TZ_OFFSET_HOURS)
KW_GROUP_COOLDOWN_MIN = float(os.getenv('KW_GROUP_COOLDOWN_MIN', '5'))
GROUPS_REFRESH_MIN = float(os.getenv('GROUPS_REFRESH_MIN', '60'))
BLOCKED_DAYS = float(os.getenv('BLOCKED_GROUP_DAYS', '3'))
NOTIFY_LEADS = os.getenv('NOTIFY_LEADS', '1') != '0'
NOTIFY_RESULTS = os.getenv('NOTIFY_RESULTS', '0') == '1'
EXCLUDE_CHATS = {x.strip().lower() for x in os.getenv('EXCLUDE_CHATS', '').split(',') if x.strip()}


def _admin_target():
    raw = os.getenv('ADMIN_USERNAME', '').strip()
    if not raw:
        return 'me'                       # Избранное аккаунта-рассыльщика
    if raw.lstrip('-').isdigit():
        return int(raw)
    return raw


ADMIN_TARGET = _admin_target()

# ──────────────────────────────────────────────────────────────
# СОСТОЯНИЕ (replied.json, groups.json, daily_stats.json)
# ──────────────────────────────────────────────────────────────

REPLIED_FILE = 'replied.json'
GROUPS_FILE = 'groups.json'
STATS_FILE = 'daily_stats.json'

replied: dict = {}     # {user_id: {ts, status, group, kw, account}} — кому уже отвечали
groups: dict = {}      # {chat_id: {title, last_post, last_kind, last_idx, last_kw_reply, blocked_until}}
stats: dict = {}       # {'first_run': {idx: date}, 'days': {idx: {date,count,target}}}
_queued: set = set()   # ключи заданий в очередях: ('u', user_id) / ('g', chat_id)
_paused_until: dict = {}   # {account_idx: unix_ts} — пауза после большого FloodWait
_dead: set = set()         # аккаунты, которые забанены/разлогинены
_groups_count: dict = {}   # {account_idx: сколько групп доступно для записи}
_counter = itertools.count()

PRIO_REPLY = 0
PRIO_PROMO = 1


def load_json(path: str, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: str, data):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now_local() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=TZ_OFFSET)


def in_active_hours() -> bool:
    h = now_local().hour
    if ACTIVE_START <= ACTIVE_END:
        return ACTIVE_START <= h < ACTIVE_END
    return h >= ACTIVE_START or h < ACTIVE_END


# ── Прогрев: лимит сообщений в день растёт с первого запуска аккаунта ─

def _day_index(idx: int) -> int:
    first = stats.setdefault('first_run', {})
    key = str(idx)
    today = now_local().date()
    try:
        first_date = date.fromisoformat(first[key])
    except (KeyError, ValueError):
        first_date = today
        first[key] = today.isoformat()
        save_json(STATS_FILE, stats)
    return (today - first_date).days + 1


def _range_for_day(day: int):
    if SKIP_WARMUP:
        lo, hi = TARGET_MIN, TARGET_MAX
    elif day <= 3:
        lo, hi = 5, 10
    elif day <= 7:
        lo, hi = 15, 25
    elif day <= 14:
        lo, hi = 30, 45
    else:
        lo, hi = TARGET_MIN, TARGET_MAX
    hi = max(1, min(hi, TARGET_MAX))
    lo = min(lo, hi)
    return lo, hi


def today_entry(idx: int) -> dict:
    key = str(idx)
    today = now_local().date().isoformat()
    days = stats.setdefault('days', {})
    entry = days.get(key)
    if not entry or entry.get('date') != today:
        lo, hi = _range_for_day(_day_index(idx))
        entry = {'date': today, 'count': 0, 'target': random.randint(lo, hi)}
        days[key] = entry
        save_json(STATS_FILE, stats)
    return entry


def increment_daily(idx: int):
    entry = today_entry(idx)
    entry['count'] += 1
    save_json(STATS_FILE, stats)


def group_state(chat_id, title: str = '') -> dict:
    g = groups.setdefault(str(chat_id), {})
    if title:
        g['title'] = title
    return g


def mark_user(job: dict, status: str):
    replied[str(job['sender_id'])] = {
        'ts': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'status': status,
        'group': job['group'],
        'kw': job['kw'],
        'account': job['account'],
    }
    save_json(REPLIED_FILE, replied)


def posts_in_window(chat_id) -> int:
    """Сколько сообщений бот написал в группе за последние 24 часа."""
    g = groups.get(str(chat_id))
    if not g:
        return 0
    cutoff = time.time() - 24 * 3600
    g['posts'] = [t for t in g.get('posts', []) if t > cutoff]
    return len(g['posts'])


def bump(name: str, n: int = 1):
    """Счётчики событий за день — для ежедневного отчёта."""
    ev = stats.setdefault('events', {})
    day = ev.setdefault(now_local().date().isoformat(), {})
    day[name] = day.get(name, 0) + n
    for old in sorted(ev)[:-7]:
        ev.pop(old, None)
    save_json(STATS_FILE, stats)


def mark_posted(job: dict, text_idx: int):
    g = group_state(job['chat_id'], job['group'])
    posts_in_window(job['chat_id'])
    g.setdefault('posts', []).append(time.time())
    bump('reply_sent' if job['kind'] == 'reply' else 'promo_sent')
    g['last_post'] = time.time()
    g['last_kind'] = job['kind']
    g['last_idx'] = text_idx
    if job['kind'] == 'reply':
        g['last_kw_reply'] = time.time()
    save_json(GROUPS_FILE, groups)


def block_group(chat_id, seconds: float):
    bump('groups_blocked')
    g = group_state(chat_id)
    g['blocked_until'] = time.time() + seconds
    save_json(GROUPS_FILE, groups)


# ──────────────────────────────────────────────────────────────
# УВЕДОМЛЕНИЯ ТЕБЕ
# ──────────────────────────────────────────────────────────────

def _who(job: dict) -> str:
    name = job.get('first_name') or 'без имени'
    if job.get('username'):
        return f"{name} (@{job['username']})"
    return f"{name} (tg://user?id={job['sender_id']})"


async def notify(client: TelegramClient, text: str):
    try:
        await client.send_message(ADMIN_TARGET, text, link_preview=False)
    except Exception as e:
        print(f"⚠️ Не удалось отправить уведомление ({ADMIN_TARGET}): {e}")
        if ADMIN_TARGET != 'me':
            try:
                await client.send_message('me', text, link_preview=False)
            except Exception:
                pass


async def notify_lead(client: TelegramClient, job: dict, will_reply: bool):
    contact = extract_contact(job['text']) or {}
    lines = [
        "🎯 Новый лид",
        f"👤 {_who(job)}",
        f"💬 Группа: {job['group']}",
        f"🔑 Слово: {job['kw']}",
        "↩️ Отвечаю в группе" if will_reply else "⏸ Ответ в группе не отправляю (кулдаун, лимит сообщений за 24 ч или ограничение группы)",
    ]
    if contact.get('phone'):
        lines.append(f"📞 В тексте: {contact['phone']}")
    if contact.get('username') and contact['username'] != job['username']:
        lines.append(f"✉️ В тексте: @{contact['username']}")
    lines.append("")
    lines.append(job['text'][:400])
    await notify(client, "\n".join(lines))


# ──────────────────────────────────────────────────────────────
# ОТПРАВКА В ГРУППЫ
# ──────────────────────────────────────────────────────────────

async def _wait_until_allowed(idx: int, job: dict) -> bool:
    """Ждём, пока можно писать: не пауза, активные часы, не выбран дневной
    лимит. False — заявка протухла (ответ на слишком старое сообщение)."""
    while True:
        if job['kind'] == 'reply' and (time.time() - job['ts']) / 3600 > MAX_LEAD_AGE_H:
            print(f"⏭️ [{idx}] Лид {job['sender_id']} протух — пропускаю")
            return False
        if time.time() < _paused_until.get(idx, 0):
            await asyncio.sleep(60)
            continue
        if not in_active_hours():
            await asyncio.sleep(120)
            continue
        entry = today_entry(idx)
        if entry['count'] >= entry['target']:
            await asyncio.sleep(300)
            continue
        return True


async def detect_group_lang(client: TelegramClient, entity) -> str:
    """Язык группы — по последним сообщениям; не получилось — случайный."""
    try:
        parts = []
        async for m in client.iter_messages(entity, limit=40):
            if m.raw_text:
                parts.append(m.raw_text)
        if parts:
            return detect_language(' '.join(parts))
    except Exception as e:
        print(f"⚠️ Не удалось определить язык группы: {e!r}")
    return random.choice(['ru', 'uz'])


BLOCK_ERRORS = (ChatWriteForbiddenError, UserBannedInChannelError,
                ChatAdminRequiredError, ChannelPrivateError, ChatRestrictedError)


def _promo_due(chat_id) -> bool:
    g = groups.get(str(chat_id), {})
    return (time.time() - g.get('last_post', 0) >= PROMO_INTERVAL_H * 3600
            and time.time() >= g.get('blocked_until', 0)
            and posts_in_window(chat_id) < MAX_POSTS)


async def process_job(idx: int, client: TelegramClient, job: dict):
    chat_id = job['chat_id']
    flood_retries = 0
    while True:
        if not await _wait_until_allowed(idx, job):
            if job['kind'] == 'reply':
                mark_user(job, 'expired')
            return

        g = groups.get(str(chat_id), {})
        if time.time() < g.get('blocked_until', 0):
            return                                   # группа временно закрыта для нас
        if posts_in_window(chat_id) >= MAX_POSTS:
            bump('skipped_group_cap')
            print(f"⏭️ [{idx}] «{job['group']}»: уже {MAX_POSTS} сообщ. за 24 ч — пропускаю")
            return
        if job['kind'] == 'promo' and not _promo_due(chat_id):
            return                                   # пока ждали, в группе уже писали

        if job['kind'] == 'reply':
            text_idx, text = job['text_idx'], job['reply_text']
            reply_to = job['reply_to']
        else:
            lang = await detect_group_lang(client, job['entity'])
            text_idx, text = pick_text(lang, g.get('last_idx'))
            reply_to = None

        try:
            await client.send_message(job['entity'], text, reply_to=reply_to, link_preview=False)
        except (FloodWaitError, SlowModeWaitError) as e:
            flood_retries += 1
            if e.seconds > 600 or flood_retries > 3:
                block_group(chat_id, e.seconds + 60)
                if job['kind'] == 'reply':
                    mark_user(job, f'failed:{type(e).__name__}')
                print(f"⏳ [{idx}] {type(e).__name__} {e.seconds}с в «{job['group']}» — группа отложена")
                return
            print(f"⏳ [{idx}] {type(e).__name__} {e.seconds}с — жду")
            await asyncio.sleep(e.seconds + 10)
            continue
        except BLOCK_ERRORS as e:
            block_group(chat_id, BLOCKED_DAYS * 86400)
            bump('send_failed')
            print(f"🚫 [{idx}] Нельзя писать в «{job['group']}» ({type(e).__name__}) — пропускаю на {BLOCKED_DAYS:g} дн.")
            if job['kind'] == 'reply':
                mark_user(job, f'failed:{type(e).__name__}')
            return
        except (AuthKeyUnregisteredError, UserDeactivatedBanError, UserDeactivatedError):
            raise
        except Exception as e:
            bump('send_failed')
            print(f"⚠️ [{idx}] Ошибка отправки в «{job['group']}»: {e!r}")
            if job['kind'] == 'reply':
                mark_user(job, f'failed:{type(e).__name__}')
            else:
                block_group(chat_id, 3600)           # не долбим проблемную группу
            return

        if job['kind'] == 'reply':
            mark_user(job, 'replied')
        mark_posted(job, text_idx)
        increment_daily(idx)
        entry = today_entry(idx)
        print(f"💬 [{idx}] {job['kind']} в «{job['group']}» — {entry['count']}/{entry['target']} сегодня")
        if NOTIFY_RESULTS:
            await notify(client, f"✅ {job['kind']} в группе «{job['group']}»")
        await asyncio.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
        return


async def sender_worker(idx: int, client: TelegramClient, queue: asyncio.PriorityQueue):
    while True:
        _, _, job = await queue.get()
        try:
            await process_job(idx, client, job)
        except asyncio.CancelledError:
            raise
        except (AuthKeyUnregisteredError, UserDeactivatedBanError, UserDeactivatedError) as e:
            _dead.add(idx)
            print(f"❌ [{idx}] Аккаунт недоступен ({type(e).__name__}) — воркер остановлен")
            return
        except Exception as e:
            print(f"⚠️ [{idx}] Ошибка воркера: {e!r}")
        finally:
            _queued.discard(job['key'])


def enqueue(queue: asyncio.PriorityQueue, prio: int, job: dict) -> bool:
    if queue.full() or job['key'] in _queued:
        return False
    _queued.add(job['key'])
    queue.put_nowait((prio, next(_counter), job))
    return True


# ──────────────────────────────────────────────────────────────
# РЕКЛАМНЫЙ ПОСТ РАЗ В СУТКИ В КАЖДОЙ ГРУППЕ
# ──────────────────────────────────────────────────────────────

def _can_write(entity) -> bool:
    if getattr(entity, 'left', False) or getattr(entity, 'deactivated', False):
        return False
    rights = getattr(entity, 'default_banned_rights', None)
    is_admin = getattr(entity, 'creator', False) or getattr(entity, 'admin_rights', None)
    if rights and rights.send_messages and not is_admin:
        return False
    return True


async def refresh_groups(client: TelegramClient) -> dict:
    """Возвращает {chat_id: entity} групп, где аккаунт состоит и может писать."""
    found = {}
    async for d in client.iter_dialogs():
        if not d.is_group or not _can_write(d.entity):
            continue
        title = d.name or str(d.id)
        if str(d.id) in EXCLUDE_CHATS or title.lower() in EXCLUDE_CHATS:
            continue
        found[d.id] = d.entity
        if str(d.id) not in groups:
            # Новая группа: размазываем первые посты по ближайшим суткам,
            # чтобы не писать во все группы сразу после запуска.
            g = group_state(d.id, title)
            g['last_post'] = time.time() - random.uniform(0, PROMO_INTERVAL_H * 3600)
        else:
            group_state(d.id, title)
    save_json(GROUPS_FILE, groups)
    print(f"📋 Групп, куда можно писать: {len(found)}")
    return found


async def promo_scheduler(idx: int, client: TelegramClient, queue: asyncio.PriorityQueue):
    await asyncio.sleep(20)
    chats: dict = {}
    last_refresh = 0.0
    while idx not in _dead:
        try:
            if time.time() - last_refresh > GROUPS_REFRESH_MIN * 60:
                chats = await refresh_groups(client)
                _groups_count[idx] = len(chats)
                last_refresh = time.time()
            for chat_id, entity in chats.items():
                if not _promo_due(chat_id):
                    continue
                g = groups[str(chat_id)]
                enqueue(queue, PRIO_PROMO, {
                    'kind': 'promo',
                    'key': ('g', chat_id),
                    'chat_id': chat_id,
                    'entity': entity,
                    'group': g.get('title') or str(chat_id),
                    'account': idx,
                    'ts': time.time(),
                })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ [{idx}] Ошибка планировщика: {e!r}")
        await asyncio.sleep(300)


# ──────────────────────────────────────────────────────────────
# ОБРАБОТЧИК СООБЩЕНИЙ (ответ по ключевому слову)
# ──────────────────────────────────────────────────────────────

def register_handlers(client: TelegramClient, me, idx: int, queue: asyncio.PriorityQueue):
    @client.on(events.NewMessage(incoming=True))
    async def on_message(event):
        try:
            if not event.is_group or event.message.fwd_from:
                return
            text = event.raw_text or ''
            if not text or len(text) > MAX_TEXT_LEN:
                return

            kw = find_keyword(text)
            if not kw or is_seller_or_ad(text):
                return

            sender = await event.get_sender()
            if (not isinstance(sender, User) or sender.bot or sender.deleted
                    or sender.is_self or sender.id == me.id):
                return

            uid = sender.id
            if str(uid) in replied or ('u', uid) in _queued:
                return

            chat_id = event.chat_id
            chat = await event.get_chat()
            title = getattr(chat, 'title', '') or str(chat_id)
            if str(chat_id) in EXCLUDE_CHATS or title.lower() in EXCLUDE_CHATS:
                return

            g = group_state(chat_id, title)
            lang = detect_language(text)
            text_idx, reply_text = pick_text(lang, g.get('last_idx'))
            job = {
                'kind': 'reply',
                'key': ('u', uid),
                'sender_id': uid,
                'chat_id': chat_id,
                'entity': await event.get_input_chat(),
                'reply_to': event.message.id,
                'reply_text': reply_text,
                'text_idx': text_idx,
                'first_name': sender.first_name or '',
                'username': sender.username or '',
                'text': text,
                'kw': kw,
                'group': title,
                'account': idx,
                'ts': time.time(),
            }

            cooling = time.time() - g.get('last_kw_reply', 0) < KW_GROUP_COOLDOWN_MIN * 60
            blocked = time.time() < g.get('blocked_until', 0)
            capped = posts_in_window(chat_id) >= MAX_POSTS
            will_reply = not cooling and not blocked and not capped and enqueue(queue, PRIO_REPLY, job)
            bump('leads')
            if not will_reply:
                bump('leads_no_reply')
            print(f"🎯 [{idx}] Лид {uid} «{kw}» в «{title}» "
                  f"({'ответ в очереди' if will_reply else 'без ответа'}, очередь: {queue.qsize()})")

            if NOTIFY_LEADS:
                await notify_lead(client, job, will_reply)
        except Exception as e:
            print(f"⚠️ [{idx}] Ошибка обработчика: {e!r}")


# ──────────────────────────────────────────────────────────────
# ЕЖЕДНЕВНЫЙ ОТЧЁТ
# ──────────────────────────────────────────────────────────────

def build_report() -> str:
    today = now_local().date().isoformat()
    ev = stats.get('events', {}).get(today, {})
    replies, promos = ev.get('reply_sent', 0), ev.get('promo_sent', 0)
    lines = [
        f"📊 Отчёт за {today}",
        f"💬 Сообщений в группах: {replies + promos} "
        f"(ответов по ключевым словам: {replies}, рекламных: {promos})",
        f"🎯 Лидов: {ev.get('leads', 0)}, из них без ответа: {ev.get('leads_no_reply', 0)}",
    ]
    if ev.get('skipped_group_cap'):
        lines.append(f"⏭️ Пропущено из-за лимита {MAX_POSTS} сообщ. в группе за 24 ч: {ev['skipped_group_cap']}")
    if ev.get('send_failed'):
        lines.append(f"⚠️ Ошибок отправки: {ev['send_failed']}")
    for key, entry in sorted(stats.get('days', {}).items()):
        if entry.get('date') == today:
            lines.append(f"👤 Аккаунт #{key}: {entry['count']} из {entry['target']} по дневному лимиту")
    total = sum(_groups_count.values())
    now = time.time()
    blocked = [g.get('title') or cid for cid, g in groups.items() if g.get('blocked_until', 0) > now]
    lines.append(f"📋 Групп для записи: {total}, временно закрытых: {len(blocked)}")
    if blocked:
        lines.append("🚫 Закрыты: " + ", ".join(blocked[:10]) + (" …" if len(blocked) > 10 else ""))
    return "\n".join(lines)


async def report_loop(client: TelegramClient):
    while True:
        await asyncio.sleep(120)
        try:
            now = now_local()
            today = now.date().isoformat()
            if now.hour >= REPORT_HOUR and stats.get('last_report') != today:
                await notify(client, build_report())
                stats['last_report'] = today
                save_json(STATS_FILE, stats)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"⚠️ Ошибка отчёта: {e!r}")


# ──────────────────────────────────────────────────────────────
# ЗАПУСК
# ──────────────────────────────────────────────────────────────

async def _wait_all_or_stop(client_tasks: list, stop: asyncio.Event):
    stop_task = asyncio.create_task(stop.wait())
    pending = set(client_tasks)
    while pending and not stop.is_set():
        done, _ = await asyncio.wait(pending | {stop_task}, return_when=asyncio.FIRST_COMPLETED)
        pending -= done
    stop_task.cancel()


async def main():
    global replied, groups, stats

    sessions = _load_session_strings()
    if not sessions:
        raise ValueError("❌ Не задан ни один SESSION_STRING_N (или SESSION_STRING) — некому логиниться.")
    print(f"👥 Аккаунтов: {len(sessions)}")

    # Восстанавливаем состояние из Избранного первого аккаунта ДО чтения json
    try:
        await state_sync.restore_state(API_ID, API_HASH, sessions[0])
    except Exception as e:
        print(f"⚠️ Восстановление состояния пропущено: {e}")

    replied = load_json(REPLIED_FILE, {})
    groups = load_json(GROUPS_FILE, {})
    stats = load_json(STATS_FILE, {})
    print(f"📂 Ответов людям уже дано: {len(replied)}, групп в памяти: {len(groups)}")

    clients = []
    background = []
    for idx, session in enumerate(sessions):
        client = TelegramClient(StringSession(session), API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            print(f"❌ Сессия #{idx} недействительна — пропускаю")
            await client.disconnect()
            continue
        me = await client.get_me()
        queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=MAX_QUEUE)
        register_handlers(client, me, idx, queue)
        background.append(asyncio.create_task(sender_worker(idx, client, queue)))
        background.append(asyncio.create_task(promo_scheduler(idx, client, queue)))
        clients.append(client)
        lo, hi = _range_for_day(_day_index(idx))
        print(f"✅ Аккаунт #{idx}: {me.first_name} (id {me.id}), "
              f"день {_day_index(idx)}, лимит сегодня {lo}–{hi}")

    if not clients:
        raise RuntimeError("❌ Ни одна сессия не авторизована — запускать нечего.")

    background.append(asyncio.create_task(state_sync.state_sync_loop(clients[0])))
    if REPORT_DAILY:
        background.append(asyncio.create_task(report_loop(clients[0])))

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    client_tasks = [asyncio.create_task(c.run_until_disconnected()) for c in clients]
    print("🚀 Boostcent Outreach запущен (только группы, без личных сообщений)")
    try:
        await _wait_all_or_stop(client_tasks, stop)
    finally:
        for t in background:
            t.cancel()
        try:
            await state_sync.backup_state(clients[0])
        except Exception:
            pass
        for c in clients:
            try:
                await c.disconnect()
            except Exception:
                pass


if __name__ == '__main__':
    asyncio.run(main())
