"""
Тексты для сообщений в группах (русский / узбекский) + определение языка.

Один и тот же пул из 20 текстов на каждый язык используется и для ответа
на сообщение с ключевым словом, и для обычного рекламного поста раз в сутки.
Юзернейм бота берётся из BOOSTCENT_BOT (по умолчанию @Boostcent_bot)
и подставляется в тексты вместо {bot}.
Поправь тексты под себя — особенно про цены и бесплатный раздел.
"""
import os
import random
import re

BOT = os.getenv('BOOSTCENT_BOT', '@Boostcent_bot')

_CYRILLIC_RE = re.compile(r'[а-яА-ЯёЁ]')
_LATIN_RE = re.compile(r'[a-zA-Z]')


def detect_language(text: str) -> str:
    cyr = len(_CYRILLIC_RE.findall(text))
    lat = len(_LATIN_RE.findall(text))
    return 'ru' if cyr >= lat else 'uz'


TEXTS = {
    'ru': [
        "Нужна накрутка подписчиков, просмотров или лайков? Всё есть в {bot}, цены в сумах.",
        "В {bot} можно заказать накрутку для Telegram, Instagram и YouTube: подписчики, просмотры, лайки, реакции.",
        "Продвигаете канал или аккаунт? Загляните в {bot}: подписчики, просмотры и реакции, оплата в сумах.",
        "Накрутка для Telegram, Instagram и YouTube — в {bot}. Заказ прямо в боте, без лишней переписки.",
        "Для Telegram и Instagram в {bot} есть бесплатные услуги с дневным лимитом, а платные оплачиваются в сумах.",
        "Нужны просмотры и реакции на посты? Посмотрите {bot}.",
        "Подписчики, лайки, просмотры и реакции для соцсетей — {bot}. Цены в сумах, заказ занимает пару минут.",
        "Раскручиваете канал? В {bot} есть накрутка подписчиков и просмотров для Telegram.",
        "Instagram: подписчики, лайки, просмотры — всё можно заказать в {bot}.",
        "YouTube: просмотры, лайки, подписчики — заказ в {bot}, цены в сумах.",
        "Хотите попробовать накрутку? В {bot} есть бесплатные услуги с дневным лимитом для Telegram и Instagram.",
        "Если нужна накрутка реакций на посты в Telegram, зайдите в {bot}.",
        "Накрутка через бота: выбираете услугу в {bot}, отправляете ссылку и оплачиваете в сумах.",
        "Нужно поднять охваты? Подписчики, просмотры, лайки, реакции — в {bot}.",
        "Для тех, кто продвигает аккаунты и каналы: {bot} — накрутка для Telegram, Instagram, YouTube.",
        "Цены в сумах, заказ в боте: {bot}. Подписчики, просмотры, лайки, реакции.",
        "Ищете, где заказать накрутку? Попробуйте {bot}: Telegram, Instagram и YouTube в одном месте.",
        "Для новых каналов и аккаунтов: подписчики и просмотры в {bot}. Есть бесплатный раздел с дневным лимитом.",
        "Всем, кто занимается продвижением: накрутка реакций, лайков и подписчиков — {bot}.",
        "Накрутка Telegram, Instagram, YouTube — {bot}. Заходите, смотрите услуги и цены.",
    ],
    'uz': [
        "Obunachi, ko'rish yoki layk kerakmi? Hammasi {bot} da, narxlar so'mda.",
        "{bot} orqali Telegram, Instagram va YouTube uchun nakrutka buyurtma qilishingiz mumkin: obunachi, ko'rish, layk, reaksiya.",
        "Kanal yoki akkauntni ko'tarayapsizmi? {bot} ga kiring: obunachi, ko'rish va reaksiyalar, to'lov so'mda.",
        "Telegram, Instagram va YouTube uchun nakrutka — {bot} da. Buyurtma to'g'ridan-to'g'ri bot orqali.",
        "{bot} da Telegram va Instagram uchun kunlik limitli bepul xizmatlar bor, pullik xizmatlar so'mda.",
        "Postlarga ko'rish va reaksiya kerakmi? {bot} ga qarab ko'ring.",
        "Ijtimoiy tarmoqlar uchun obunachi, layk, ko'rish, reaksiya — {bot}. Narxlar so'mda, buyurtma bir necha daqiqada.",
        "Kanalni ko'tarayapsizmi? {bot} da Telegram uchun obunachi va ko'rish nakrutkasi bor.",
        "Instagram: obunachi, layk, ko'rish — hammasini {bot} orqali buyurtma qilish mumkin.",
        "YouTube: ko'rish, layk, obunachi — buyurtma {bot} da, narxlar so'mda.",
        "Nakrutkani sinab ko'rmoqchimisiz? {bot} da Telegram va Instagram uchun kunlik limitli bepul xizmatlar bor.",
        "Telegramdagi postlarga reaksiya nakrutkasi kerak bo'lsa, {bot} ga kiring.",
        "Bot orqali nakrutka: {bot} da xizmatni tanlaysiz, havolani yuborasiz va so'mda to'laysiz.",
        "Qamrovni oshirmoqchimisiz? Obunachi, ko'rish, layk, reaksiya — {bot} da.",
        "Akkaunt va kanal targ'ib qilayotganlar uchun: {bot} — Telegram, Instagram, YouTube nakrutkasi.",
        "Narxlar so'mda, buyurtma bot orqali: {bot}. Obunachi, ko'rish, layk, reaksiya.",
        "Nakrutkani qayerdan buyurtma qilishni qidiryapsizmi? {bot} ni ko'ring: Telegram, Instagram va YouTube bir joyda.",
        "Yangi kanal va akkauntlar uchun: obunachi va ko'rish {bot} da. Kunlik limitli bepul bo'lim ham bor.",
        "Targ'ibot bilan shug'ullanayotganlarga: reaksiya, layk va obunachi nakrutkasi — {bot}.",
        "Telegram, Instagram, YouTube nakrutkasi — {bot}. Kiring, xizmat va narxlarni ko'ring.",
    ],
}


def pick_text(lang: str, avoid_idx=None):
    """Возвращает (индекс, текст). avoid_idx — индекс прошлого текста в этой
    группе, чтобы два раза подряд не слать одно и то же."""
    lang = lang if lang in TEXTS else 'uz'
    pool = TEXTS[lang]
    choices = [i for i in range(len(pool)) if i != avoid_idx] or list(range(len(pool)))
    i = random.choice(choices)
    return i, pool[i].format(bot=BOT)
