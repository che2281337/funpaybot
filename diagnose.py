"""
Диагностика подключения к FunPay. Ничего не меняет на аккаунте.
Запуск: diagnose.bat (Windows) или venv/bin/python diagnose.py
Вывод можно присылать разработчику: golden_key, PHPSESSID и csrf-токен скрыты.
"""
from __future__ import annotations

import json
import os
import sys
import time

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

from FunPayAPI import Account  # noqa: E402
from FunPayAPI.common import utils  # noqa: E402
from cardinal.fp import configure_account  # noqa: E402
from cardinal.proxy import mask, system_proxies, to_requests  # noqa: E402


def hide(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, s[:3] + "***")
    return text


def timed(name: str, func, secrets: list[str]):
    started = time.time()
    try:
        result = func()
        print(f"  ✅ {name}: {result} ({round((time.time() - started) * 1000)} мс)")
    except Exception as e:
        text = e.short_str() if hasattr(e, "short_str") else f"{type(e).__name__}: {e}"
        print(f"  ❌ {name}: {hide(text[:300], secrets)} ({round((time.time() - started) * 1000)} мс)")


def main():
    with open("storage/config.json", encoding="utf-8") as f:
        full_cfg = json.load(f)
    cfg = full_cfg["funpay"]
    print(f"Python {sys.version.split()[0]}, requests {requests.__version__}")
    print(f"User-Agent: {cfg.get('user_agent')}")
    print(f"Прокси для FunPay: {mask(cfg.get('proxy'))}")
    print(f"Прокси для Telegram: {mask(full_cfg.get('telegram', {}).get('proxy'))}")
    print(f"Системный прокси (VPN, для FunPay НЕ используется): "
          f"{ {k: mask(v) for k, v in system_proxies().items()} or 'нет'}")

    acc = configure_account(Account(cfg["golden_key"], cfg.get("user_agent") or None, requests_timeout=20,
                                    proxy=to_requests(cfg.get("proxy"))))
    secrets = [cfg["golden_key"]]
    print("\nПроверка FunPay:")
    timed("вход в аккаунт", lambda: f"{acc.get(update_phpsessid=True).username} (ID {acc.id})", secrets)
    secrets += [acc.phpsessid or "", acc.csrf_token or ""]
    if not acc.is_initiated:
        return
    timed("получение событий (runner)", lambda: acc.runner_request(acc.get_payload_data(
        last_msg_event_tag=utils.random_tag(), last_order_event_tag=utils.random_tag())).status_code, secrets)
    timed("чаты", lambda: f"{len(acc.request_chats())} шт.", secrets)
    timed("продажи", lambda: f"{len(acc.get_sales()[1])} шт.", secrets)
    timed("профиль и лоты", lambda: f"{len(acc.get_user(acc.id).get_lots())} активных лотов", secrets)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nДиагностика упала: {type(e).__name__}: {e}")
    if os.name == "nt":
        input("\nНажмите Enter, чтобы закрыть...")
