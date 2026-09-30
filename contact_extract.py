"""
Достаём из текста сообщения контакт автора — @username или узбекский
номер телефона. Используется только для уведомления тебе (лид-алерт),
чтобы ты сразу видел, как ещё можно связаться с человеком.
"""
import re

# @username — от 4 до 32 символов после @, латиница/цифры/подчёркивание,
# первый символ — буква (телеграм не разрешает username с цифры).
USERNAME_RE = re.compile(r'(?<!\w)@([A-Za-z][A-Za-z0-9_]{3,31})')

# Узбекский номер: с +998/998 или без (локальные 9 цифр), с произвольными
# пробелами/дефисами между группами. Отрицательный lookahead/lookbehind на
# цифру — чтобы не зацепить середину более длинного числа (id, цена и т.п.).
PHONE_RE = re.compile(
    r'(?<!\d)(?:\+?998[\s\-]?)?(\d{2})[\s\-]?(\d{3})[\s\-]?(\d{2})[\s\-]?(\d{2})(?!\d)'
)


def extract_contact(text: str):
    """Возвращает {'username': str|None, 'phone': str|None} или None, если
    в тексте нет ни того, ни другого."""
    uname_m = USERNAME_RE.search(text)
    phone_m = PHONE_RE.search(text)

    username = uname_m.group(1) if uname_m else None
    phone = None
    if phone_m:
        digits = ''.join(phone_m.groups())
        if len(digits) == 9:
            phone = '+998' + digits

    if not username and not phone:
        return None
    return {'username': username, 'phone': phone}
