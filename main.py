"""
FunPay бот с управлением через Telegram.
Запуск: python main.py
"""
from __future__ import annotations

import getpass
import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from cardinal.core import Cardinal  # noqa: E402
from cardinal.storage import Storage, hash_password  # noqa: E402


def setup_logging():
    os.makedirs("logs", exist_ok=True)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)-7s %(name)s: %(message)s", "%d.%m %H:%M:%S")
    file_handler = RotatingFileHandler("logs/log.txt", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.INFO)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)
    root.addHandler(console)
    for noisy in ("urllib3", "TeleBot", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def ask(prompt: str, validator=None, secret: bool = False, default: str = "") -> str:
    while True:
        value = (getpass.getpass(prompt) if secret else input(prompt)).strip()
        if not value and default:
            return default
        if validator is None or validator(value):
            return value
        print("  ❌ Неверное значение, попробуйте ещё раз.")


def first_setup(storage: Storage):
    cfg = storage.config.data
    need = not cfg["funpay"]["golden_key"] or not cfg["telegram"]["token"] or not cfg["telegram"]["password_hash"]
    if not need:
        return
    if not sys.stdin or not sys.stdin.isatty():
        print("Бот не настроен. Заполните storage/config.json или задайте переменные окружения "
              "FUNPAY_GOLDEN_KEY, TELEGRAM_TOKEN, TELEGRAM_PASSWORD.")
        sys.exit(1)
    print("=" * 60)
    print(" Первичная настройка FunPay-бота")
    print("=" * 60)
    if not cfg["funpay"]["golden_key"]:
        print("\n1) golden_key — cookie с сайта funpay.com (F12 → Application → Cookies → golden_key).")
        cfg["funpay"]["golden_key"] = ask("   golden_key: ", lambda v: bool(re.fullmatch(r"[a-z0-9]{32}", v)))
        print("\n   User-Agent браузера, в котором вы вошли на FunPay (можно узнать на whatsmyua.info).")
        cfg["funpay"]["user_agent"] = ask("   User-Agent [Enter — по умолчанию]: ",
                                          default=cfg["funpay"]["user_agent"])
    if not cfg["telegram"]["token"]:
        print("\n2) Токен Telegram-бота — создайте бота у @BotFather и скопируйте токен.")
        cfg["telegram"]["token"] = ask("   Токен: ", lambda v: bool(re.fullmatch(r"\d+:[\w-]{30,}", v)))
    if not cfg["telegram"]["password_hash"]:
        print("\n3) Пароль для входа в панель Telegram (минимум 6 символов). Его нужно будет отправить боту.")
        password = ask("   Пароль: ", lambda v: len(v) >= 6, secret=True)
        cfg["telegram"]["password_hash"] = hash_password(password)
    proxy = ask("\n4) Прокси для FunPay (http://user:pass@ip:port) [Enter — без прокси]: ", default="-")
    cfg["funpay"]["proxy"] = "" if proxy == "-" else proxy
    storage.config.save()
    print("\n✅ Настройки сохранены в storage/config.json. Запускаю бота...\n")


def main():
    setup_logging()
    storage = Storage("storage")
    first_setup(storage)

    from tg_bot.bot import TGBot

    cardinal = Cardinal(storage)
    cardinal.tg = TGBot(cardinal)
    try:
        cardinal.run()
    except KeyboardInterrupt:
        print("\nОстановлено.")


if __name__ == "__main__":
    main()
