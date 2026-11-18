import re
from urllib.parse import urlsplit


def normalize_chat_ref(value: str | int) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Укажите ID чата или @username")
    value = str(value).strip()
    if re.fullmatch(r"-?[1-9]\d*", value):
        return value
    if value.startswith(("https://", "http://")):
        url = urlsplit(value)
        if url.hostname not in {"t.me", "telegram.me"} or url.query or url.fragment:
            raise ValueError("Нужна ссылка на Telegram-чат")
        value = url.path.strip("/")
        if value.startswith("s/"):
            value = value[2:]
    value = value.removeprefix("@")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", value):
        raise ValueError("Укажите ID или @username чата; приглашения не поддерживаются")
    return "@" + value.lower()
