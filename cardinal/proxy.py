"""
Прокси: разбор строки, проверка работоспособности, преобразование для requests / telebot.
Поддерживаются HTTP(S), SOCKS5 (в т.ч. socks5h — DNS через прокси) и SOCKS4.
"""
from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

import requests

SCHEMES = ("http", "https", "socks5", "socks5h", "socks4", "socks4a")

FORMATS_HELP = (
    "<b>Поддерживаемые форматы:</b>\n"
    "<code>socks5://user:pass@1.2.3.4:1080</code>\n"
    "<code>http://user:pass@1.2.3.4:8080</code>\n"
    "<code>1.2.3.4:1080</code>\n"
    "<code>1.2.3.4:1080:user:pass</code>\n"
    "<code>user:pass@1.2.3.4:1080</code>\n"
    "Типы: <code>http</code>, <code>https</code>, <code>socks5</code>, <code>socks5h</code> "
    "(DNS через прокси), <code>socks4</code>."
)


def parse_proxy(text: str, default_scheme: str = "http") -> str:
    """
    Приводит прокси к виду scheme://[user:pass@]host:port.
    :raises ValueError: если строка не похожа на прокси.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Пустая строка")
    scheme = default_scheme.lower()
    if "://" in text:
        scheme, text = text.split("://", 1)
        scheme = scheme.lower()
    if scheme == "socks":
        scheme = "socks5"
    if scheme not in SCHEMES:
        raise ValueError(f"Неизвестный тип прокси: {scheme}")
    text = text.rstrip("/")

    user = password = None
    four_parts = re.fullmatch(r"([^@:/\s]+):(\d{1,5}):([^:]+):(.+)", text)  # host:port:user:pass
    if four_parts:
        hostport = f"{four_parts.group(1)}:{four_parts.group(2)}"
        user, password = four_parts.group(3), four_parts.group(4)
    elif "@" in text:
        auth, hostport = text.rsplit("@", 1)
        if ":" not in auth:
            raise ValueError("Логин и пароль нужно указывать как user:pass")
        user, password = auth.split(":", 1)
    else:
        hostport = text

    m = re.fullmatch(r"(\[[0-9a-fA-F:]+\]|[A-Za-z0-9.\-]+):(\d{1,5})", hostport)
    if not m or not 0 < int(m.group(2)) < 65536:
        raise ValueError("Нужен адрес вида host:port")
    auth = f"{quote(unquote(user), safe='')}:{quote(unquote(password), safe='')}@" if user is not None else ""
    return f"{scheme}://{auth}{m.group(1)}:{m.group(2)}"


def to_requests(proxy: Optional[str]) -> Optional[dict]:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def system_proxies() -> dict:
    """Прокси, заданные в системе (настройки Windows / переменные окружения), — обычно их ставят VPN-программы."""
    import urllib.request
    try:
        found = urllib.request.getproxies()
    except Exception:
        return {}
    return {k: v for k, v in found.items() if k != "no" and v}


def mask(proxy: Optional[str]) -> str:
    """Скрывает пароль для показа в Telegram / логах."""
    if not proxy:
        return "не задан"
    parts = urlsplit(proxy)
    if parts.password:
        return proxy.replace(f":{parts.password}@", ":****@", 1)
    return proxy


def proxy_type(proxy: Optional[str]) -> str:
    if not proxy:
        return "—"
    return proxy.split("://", 1)[0].upper()


def check_proxy(proxy: str, timeout: float = 12, use_system: bool = False) -> dict:
    """
    Проверяет прокси: внешний IP, доступность FunPay и Telegram.
    Возвращает {"ok": bool, "ip": str|None, "ping": float|None, "funpay": bool, "telegram": bool, "error": str|None}.
    """
    session = requests.Session()
    # без прокси: для FunPay бот ходит напрямую, а для Telegram — через системные настройки (VPN)
    session.trust_env = use_system and not proxy
    proxies = to_requests(proxy)
    result = {"ok": False, "ip": None, "ping": None, "funpay": False, "telegram": False, "error": None}
    try:
        start = time.time()
        r = session.get("https://api.ipify.org?format=json", proxies=proxies, timeout=timeout)
        result["ping"] = round((time.time() - start) * 1000)
        result["ip"] = r.json().get("ip")
        result["ok"] = True
    except requests.exceptions.InvalidSchema as e:
        if "SOCKS" in str(e):
            result["error"] = "Не установлена поддержка SOCKS. Выполните: pip install PySocks"
        else:
            result["error"] = str(e)
        return result
    except Exception as e:
        result["error"] = _short_error(e)
    for key, url in (("funpay", "https://funpay.com/"), ("telegram", "https://api.telegram.org/")):
        try:
            session.get(url, proxies=proxies, timeout=timeout)
            result[key] = True
            result["ok"] = True
        except Exception as e:
            if not result["error"]:
                result["error"] = _short_error(e)
    return result


def _short_error(e: Exception) -> str:
    text = str(e)
    for hint, msg in (("timed out", "таймаут подключения"), ("Connection refused", "соединение отклонено"),
                      ("407", "неверный логин/пароль прокси (407)"), ("authentication", "ошибка авторизации прокси"),
                      ("Name or service not known", "хост не найден")):
        if hint.lower() in text.lower():
            return msg
    return text[:200]
