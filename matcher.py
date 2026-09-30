"""
Поиск ключевых слов в сообщениях + фильтр рекламы/продавцов.

Ключевые слова (по умолчанию):
  UZ: nakrutka, nak, telegram nomer, akk
  RU: накрутка, нак, телеграм номер, акк

Правила записи:
  - "слово"   -> только целое слово (nak не сработает на "nakladnoy")
  - "слово*"  -> начало слова, любые окончания (nakrutk* поймает
                 nakrutka, nakrutkasi, nakrutkaga; накрутк* — накрутка,
                 накрутку, накрутки)
  - "a b"     -> фраза, между словами любые пробелы

Свой список можно задать через переменную KEYWORDS (через запятую),
например: KEYWORDS=nakrutk*,nak,telegram nomer,akk,накрутк*,нак,акк
Слово "yoki" сюда НЕ входит — это просто "или" между вариантами.
"""
import os
import re

DEFAULT_KEYWORDS = [
    'nakrutk*', 'nak', 'telegram nomer', 'akk',
    'накрутк*', 'нак', 'телеграм номер', 'акк',
]


def _normalize(text: str) -> str:
    return text.lower().replace('ё', 'е')


def _compile(keyword: str):
    kw = _normalize(keyword.strip())
    prefix = kw.endswith('*')
    core = kw.rstrip('*').strip()
    if not core:
        return None
    body = r'\s+'.join(re.escape(part) for part in core.split())
    pattern = r'(?<!\w)' + body + (r'\w*' if prefix else r'(?!\w)')
    return re.compile(pattern, re.IGNORECASE)


def _load_keywords() -> list:
    raw = os.getenv('KEYWORDS', '')
    items = [w.strip() for w in raw.split(',') if w.strip()]
    return items or DEFAULT_KEYWORDS


_KEYWORDS = _load_keywords()
_PATTERNS = [(k, p) for k in _KEYWORDS if (p := _compile(k)) is not None]


def find_keyword(text: str):
    """Возвращает первое совпавшее ключевое слово (как в списке) или None."""
    low = _normalize(text)
    for kw, pattern in _PATTERNS:
        if pattern.search(low):
            return kw.rstrip('*')
    return None


# ── Фильтр "это продавец/реклама, а не клиент" ────────────────────────
# Тем, кто сам предлагает накрутку/аккаунты, писать бессмысленно (это
# конкуренты) — а лишние сообщения повышают риск бана. Список можно
# расширить через SKIP_PHRASES (через запятую), отключить — SKIP_SELLERS=0.
_DEFAULT_SKIP_PHRASES = [
    # UZ
    'qilib beraman', 'qilib beramiz', 'qilaman', 'qilamiz', 'sotaman',
    'sotiladi', 'sotamiz', 'arzon narxda', 'narxlar', 'buyurtma qabul',
    'shaxsiyga yozing', 'lsga yozing', 'lichkaga yozing', 'aksiya',
    # RU
    'продам', 'продаю', 'продается', 'продаётся', 'предлагаю', 'предлагаем',
    'делаю накрутк', 'принимаю заказ', 'принимаем заказ', 'прайс',
    'недорого', 'дешево', 'дёшево', 'пишите в лс', 'пишите в личку',
    'акция', 'скидк',
]

_URL_RE = re.compile(r'(https?://|t\.me/|www\.)', re.IGNORECASE)


def _load_skip_phrases() -> list:
    extra = [w.strip().lower() for w in os.getenv('SKIP_PHRASES', '').split(',') if w.strip()]
    return [_normalize(p) for p in _DEFAULT_SKIP_PHRASES + extra]


_SKIP_PHRASES = _load_skip_phrases()
_SKIP_ENABLED = os.getenv('SKIP_SELLERS', '1') != '0'


def is_seller_or_ad(text: str) -> bool:
    if not _SKIP_ENABLED:
        return False
    low = _normalize(text)
    if _URL_RE.search(low):
        return True
    return any(p in low for p in _SKIP_PHRASES)
