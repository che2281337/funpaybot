"""
Диагностика подключения к FunPay. Ничего не меняет на аккаунте.
Запуск: venv\\Scripts\\python diagnose.py   (Windows)   /   venv/bin/python diagnose.py
Вывод можно смело присылать разработчику: golden_key, PHPSESSID и csrf-токен скрыты.
"""
from __future__ import annotations

import json
import os
import re
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))

import requests  # noqa: E402

from FunPayAPI import Account  # noqa: E402
from FunPayAPI.common import utils  # noqa: E402
from cardinal.proxy import mask, system_proxies, to_requests  # noqa: E402


def hide(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, s[:3] + "***")
    return text


def short(body: str) -> str:
    body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body).strip()[:300]


def main():
    with open("storage/config.json", encoding="utf-8") as f:
        cfg = json.load(f)["funpay"]
    print(f"Python {sys.version.split()[0]}, requests {requests.__version__}")
    print(f"User-Agent: {cfg.get('user_agent')}")
    print(f"Прокси в настройках бота: {mask(cfg.get('proxy'))}")
    print(f"Системный прокси (VPN, бот его НЕ использует): "
          f"{ {k: mask(v) for k, v in system_proxies().items()} or 'нет'}")
    print(f"golden_key: длина {len(cfg['golden_key'])}")

    acc = Account(cfg["golden_key"], cfg.get("user_agent") or None, requests_timeout=20,
                  proxy=to_requests(cfg.get("proxy")))
    r = acc.method("get", "https://funpay.com/", {}, {}, exclude_phpsessid=True)
    print(f"\nGET / -> {r.status_code}, итоговый URL: {r.url}, редиректы: {[h.status_code for h in r.history]}")
    print(f"Куки от FunPay: {sorted({c for h in list(r.history) + [r] for c in h.cookies.get_dict()})}")

    acc.get(update_phpsessid=True)
    secrets = [cfg["golden_key"], acc.phpsessid or "", acc.csrf_token or ""]
    print(f"Аккаунт: {acc.username} (ID {acc.id}), PHPSESSID: {'есть' if acc.phpsessid else 'НЕТ'}, "
          f"csrf: {'есть' if acc.csrf_token else 'НЕТ'} (длина {len(acc.csrf_token or '')})")
    print(f"Ключи app-data: {sorted(acc.app_data)}")

    headers = {"accept": "*/*", "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
               "x-requested-with": "XMLHttpRequest"}

    def objects(tag_len=10, which=("orders_counters", "chat_bookmarks")):
        return json.dumps([{"type": t, "id": acc.id, "tag": utils.random_tag()[:tag_len], "data": False}
                           for t in which])

    variants = {
        "1. как сейчас (request=false)": {"objects": objects(), "request": "false", "csrf_token": acc.csrf_token},
        "2. request=False (старый)": {"objects": objects(), "request": False, "csrf_token": acc.csrf_token},
        "3. теги 8 символов": {"objects": objects(8), "request": "false", "csrf_token": acc.csrf_token},
        "4. без request": {"objects": objects(), "csrf_token": acc.csrf_token},
        "5. только orders_counters": {"objects": objects(which=("orders_counters",)), "request": "false",
                                      "csrf_token": acc.csrf_token},
        "6. только chat_bookmarks": {"objects": objects(which=("chat_bookmarks",)), "request": "false",
                                     "csrf_token": acc.csrf_token},
        "7. без csrf_token": {"objects": objects(), "request": "false"},
    }
    print("\nПроверка runner/:")
    for name, payload in variants.items():
        try:
            resp = acc.method("post", "runner/", dict(headers), payload)
            extra = "" if resp.status_code == 200 else f" | ответ: {hide(short(resp.text), secrets)}"
            if resp.is_redirect:
                extra += f" | редирект на {resp.headers.get('Location')}"
            print(f"  {name}: {resp.status_code}{extra}")
        except Exception as e:
            print(f"  {name}: ошибка {type(e).__name__}: {hide(str(e)[:200], secrets)}")

    print("\nПроверка других запросов:")
    for name, func in (("список заказов", lambda: len(acc.get_sells()[1])),
                       ("профиль/лоты", lambda: len(acc.get_user(acc.id).get_lots())),
                       ("чаты", lambda: len(acc.request_chats()))):
        try:
            print(f"  {name}: OK ({func()})")
        except Exception as e:
            text = e.short_str() if hasattr(e, "short_str") else f"{type(e).__name__}: {e}"
            print(f"  {name}: ОШИБКА {hide(text[:200], secrets)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nДиагностика упала: {type(e).__name__}: {e}")
    if os.name == "nt":
        input("\nНажмите Enter, чтобы закрыть...")
