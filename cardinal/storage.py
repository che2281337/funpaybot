"""
Хранилище настроек и данных бота (JSON-файлы в папке storage/).
Все изменения из Telegram сразу сохраняются на диск.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
from typing import Any

logger = logging.getLogger("FPC.storage")

DEFAULT_CONFIG: dict[str, Any] = {
    "funpay": {
        "golden_key": "",
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/128.0.0.0 Safari/537.36",
        "proxy": "",
    },
    "telegram": {
        "token": "",
        "password_hash": "",
        "admins": [],
        "proxy": "",
    },
    "settings": {
        "auto_raise": True,
        "auto_response": True,
        "auto_delivery": True,
        "auto_restore": True,
        "auto_disable": False,
        "greetings": True,
        "greeting_text": "Привет, $username! 👋\nСпасибо, что написали. Я скоро отвечу.",
        "greeting_cooldown_days": 2,
        "review_replies": True,
        "order_confirm_reply": False,
        "order_confirm_text": "$username, спасибо за подтверждение заказа $order_id! Буду рад отзыву ⭐",
        "eternal_online": True,
        "runner_delay": 6,
        "blacklist": [],
        "blacklist_block_delivery": True,
        "blacklist_block_response": True,
        "notify": {
            "messages": True,
            "my_messages": False,
            "orders": True,
            "order_status": True,
            "reviews": True,
            "raise": False,
            "delivery": True,
            "commands": False,
        },
    },
}

DEFAULT_AUTO_RESPONSE: dict[str, Any] = {
    "!помощь|!help": {
        "response": "Команды:\n!помощь — список команд\n!продавец — позвать продавца",
        "notify": False,
    },
    "!продавец": {
        "response": "$username, я передал продавцу, что вы его ждёте. Он скоро ответит!",
        "notify": True,
    },
}

DEFAULT_REVIEW_REPLIES: dict[str, Any] = {
    "5": {"enabled": True, "text": "Спасибо за отличный отзыв, $username! ❤️ Буду рад видеть вас снова."},
    "4": {"enabled": True, "text": "Спасибо за отзыв, $username! Стараюсь стать лучше."},
    "3": {"enabled": False, "text": "Спасибо за отзыв. Напишите, что можно улучшить."},
    "2": {"enabled": False, "text": "Жаль, что что-то пошло не так. Напишите мне в чат — разберёмся."},
    "1": {"enabled": False, "text": "Жаль, что что-то пошло не так. Напишите мне в чат — разберёмся."},
}

DEFAULT_TEMPLATES: list[str] = [
    "Здравствуйте! Сейчас всё сделаю.",
    "Заказ выполнен ✅ Подтвердите, пожалуйста, выполнение заказа и оставьте отзыв ⭐",
    "Минутку, уже отвечаю.",
]


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _merge_defaults(data: dict, defaults: dict) -> dict:
    """Дополняет data недостающими ключами из defaults (рекурсивно)."""
    for key, value in defaults.items():
        if key not in data:
            data[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(data[key], dict):
            _merge_defaults(data[key], value)
    return data


class JsonFile:
    def __init__(self, path: str, default: Any):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load(default)

    def _load(self, default: Any) -> Any:
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(default, dict) and isinstance(data, dict):
                    _merge_defaults(data, default)
                return data
            except (json.JSONDecodeError, OSError):
                logger.exception(f"Не удалось прочитать {self.path}, используются значения по умолчанию.")
        return copy.deepcopy(default)

    def save(self):
        with self.lock:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)


class Storage:
    def __init__(self, root: str = "storage"):
        self.root = root
        self.goods_dir = os.path.join(root, "goods")
        os.makedirs(self.goods_dir, exist_ok=True)
        os.makedirs(os.path.join(root, "cache"), exist_ok=True)

        self.config = JsonFile(os.path.join(root, "config.json"), DEFAULT_CONFIG)
        self.auto_response = JsonFile(os.path.join(root, "auto_response.json"), DEFAULT_AUTO_RESPONSE)
        self.review_replies = JsonFile(os.path.join(root, "review_replies.json"), DEFAULT_REVIEW_REPLIES)
        self.templates = JsonFile(os.path.join(root, "templates.json"), DEFAULT_TEMPLATES)
        self.auto_delivery = JsonFile(os.path.join(root, "auto_delivery.json"), {})
        self.old_users = JsonFile(os.path.join(root, "cache", "old_users.json"), {})
        self.lots_cache = JsonFile(os.path.join(root, "cache", "lots.json"), {})
        self.goods_lock = threading.RLock()
        # создаём файлы с настройками по умолчанию, чтобы их можно было отредактировать вручную
        for file in (self.config, self.auto_response, self.review_replies, self.templates, self.auto_delivery):
            if not os.path.exists(file.path):
                file.save()
        self._migrate()
        self._apply_env()

    def _migrate(self):
        """Старые версии хранили один прокси в funpay.proxy и флаг telegram.use_proxy."""
        cfg = self.config.data
        if "use_proxy" in cfg["telegram"]:
            if cfg["telegram"].pop("use_proxy") and cfg["funpay"].get("proxy") and not cfg["telegram"].get("proxy"):
                cfg["telegram"]["proxy"] = cfg["funpay"]["proxy"]
            self.config.save()

    def _apply_env(self):
        """Позволяет задать ключевые параметры через переменные окружения (удобно для Docker / хостинга)."""
        cfg = self.config.data
        changed = False
        env_map = {
            "FUNPAY_GOLDEN_KEY": ("funpay", "golden_key"),
            "FUNPAY_USER_AGENT": ("funpay", "user_agent"),
            "FUNPAY_PROXY": ("funpay", "proxy"),
            "TELEGRAM_TOKEN": ("telegram", "token"),
            "TELEGRAM_PROXY": ("telegram", "proxy"),
        }
        for env in ("FUNPAY_PROXY", "TELEGRAM_PROXY"):
            if os.environ.get(env):
                from .proxy import parse_proxy
                try:
                    os.environ[env] = parse_proxy(os.environ[env], "socks5")
                except ValueError:
                    logger.error(f"{env} задан в неверном формате — игнорирую.")
                    os.environ.pop(env)
        for env, (section, key) in env_map.items():
            if (value := os.environ.get(env)) and cfg[section][key] != value:
                cfg[section][key] = value
                changed = True
        if (password := os.environ.get("TELEGRAM_PASSWORD")) and cfg["telegram"]["password_hash"] != hash_password(password):
            cfg["telegram"]["password_hash"] = hash_password(password)
            changed = True
        if changed:
            self.config.save()

    # ---------- удобные геттеры ----------
    @property
    def settings(self) -> dict:
        return self.config.data["settings"]

    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: Any):
        with self.config.lock:
            self.settings[key] = value
            self.config.save()

    def toggle_setting(self, key: str) -> bool:
        with self.config.lock:
            self.settings[key] = not self.settings.get(key)
            self.config.save()
            return self.settings[key]

    def notify_enabled(self, key: str) -> bool:
        return bool(self.settings["notify"].get(key))

    def toggle_notify(self, key: str) -> bool:
        with self.config.lock:
            self.settings["notify"][key] = not self.settings["notify"].get(key)
            self.config.save()
            return self.settings["notify"][key]

    def get_proxy(self, target: str) -> str:
        """target: "telegram" или "funpay". Пустая строка — без прокси."""
        return self.config.data[target].get("proxy") or ""

    @property
    def admins(self) -> list[int]:
        return self.config.data["telegram"]["admins"]

    def add_admin(self, user_id: int):
        with self.config.lock:
            if user_id not in self.admins:
                self.admins.append(user_id)
                self.config.save()

    def remove_admin(self, user_id: int):
        with self.config.lock:
            if user_id in self.admins:
                self.admins.remove(user_id)
                self.config.save()

    def check_password(self, password: str) -> bool:
        stored = self.config.data["telegram"]["password_hash"]
        return bool(stored) and stored == hash_password(password)

    def is_blacklisted(self, username: str | None) -> bool:
        if not username:
            return False
        return username.lower() in [i.lower() for i in self.settings.get("blacklist", [])]

    # ---------- товары автовыдачи ----------
    def goods_path(self, name: str) -> str:
        return os.path.join(self.goods_dir, name)

    def read_goods(self, name: str) -> list[str]:
        with self.goods_lock:
            path = self.goods_path(name)
            if not os.path.exists(path):
                return []
            with open(path, encoding="utf-8") as f:
                return [line.rstrip("\n") for line in f if line.strip()]

    def write_goods(self, name: str, goods: list[str]):
        with self.goods_lock:
            with open(self.goods_path(name), "w", encoding="utf-8") as f:
                f.write("\n".join(goods) + ("\n" if goods else ""))

    def add_goods(self, name: str, goods: list[str]) -> int:
        with self.goods_lock:
            current = self.read_goods(name)
            current.extend(g for g in goods if g.strip())
            self.write_goods(name, current)
            return len(current)

    def take_goods(self, name: str, amount: int) -> tuple[list[str], int]:
        """Забирает amount товаров из файла. Возвращает (выданные товары, остаток)."""
        with self.goods_lock:
            current = self.read_goods(name)
            if len(current) < amount:
                return [], len(current)
            taken, rest = current[:amount], current[amount:]
            self.write_goods(name, rest)
            return taken, len(rest)

    def return_goods(self, name: str, goods: list[str]):
        with self.goods_lock:
            self.write_goods(name, goods + self.read_goods(name))
