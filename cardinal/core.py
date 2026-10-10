"""
Ядро бота: подключение к FunPay, обработка событий (сообщения, заказы, отзывы),
автоответ, приветствие, автовыдача, автоответ на отзывы, вечный онлайн.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

import requests

from FunPayAPI import Account, Runner, types
from FunPayAPI.common import exceptions
from FunPayAPI.common.utils import RegularExpressions
from FunPayAPI.common.enums import MessageTypes, OrderStatuses, SubCategoryTypes
from FunPayAPI.updater import events

from .lots import LotsManager
from .proxy import mask, system_proxies, to_requests
from .raiser import Raiser
from .storage import Storage
from .utils import esc, format_text, normalize_command

if TYPE_CHECKING:
    from tg_bot.bot import TGBot

logger = logging.getLogger("FPC")

REVIEW_TYPES = (MessageTypes.NEW_FEEDBACK, MessageTypes.FEEDBACK_CHANGED)
CONFIRM_TYPES = (MessageTypes.ORDER_CONFIRMED, MessageTypes.ORDER_CONFIRMED_BY_ADMIN)


class Cardinal:
    VERSION = "1.0.0"

    def __init__(self, storage: Storage):
        self.storage = storage
        fp = storage.config.data["funpay"]
        self.account = Account(fp["golden_key"], fp.get("user_agent") or None, requests_timeout=15,
                               proxy=to_requests(fp.get("proxy")))
        self.runner: Optional[Runner] = None
        self.tg: Optional[TGBot] = None
        self.lots = LotsManager(self)
        self.raiser = Raiser(self)
        self.start_time = time.time()
        self.balance_lot_id: Optional[int] = None
        self.stats = {"messages": 0, "orders": 0, "delivered": 0, "auto_responses": 0, "reviews_answered": 0}

    # ------------------------------------------------------------------ запуск
    def init_account(self):
        attempts = 0
        notified = False
        while True:
            try:
                self.account.get(update_phpsessid=True)
                logger.info(f"Авторизован на FunPay как {self.account.username} (ID {self.account.id}).")
                return
            except exceptions.UnauthorizedError:
                logger.error("Неверный golden_key или user-agent! Замените golden_key в Telegram "
                             "(Панель → Настройки) или в storage/config.json.")
                if not notified:
                    self.notify("🔴 <b>Не удалось войти в FunPay</b>: неверный или устаревший golden_key.\n"
                                "Замените его: ⚙️ Панель управления → 🛠 Настройки → 🍪 Сменить golden_key.")
                    notified = True
                time.sleep(300)
            except Exception as e:
                attempts += 1
                wait = min(60, 5 * attempts)
                logger.warning(f"Не удалось подключиться к FunPay ({e}). Повтор через {wait} сек.")
                time.sleep(wait)

    def run(self):
        if (found := system_proxies()) and not self.storage.get_proxy("funpay"):
            logger.warning(f"В системе найден прокси (обычно его включает VPN): {mask(next(iter(found.values())))}. "
                           f"Для FunPay он НЕ используется — бот подключается напрямую. "
                           f"Если нужен прокси для FunPay, задайте его в настройках бота.")
        if self.tg:
            self.tg.start()
        self.init_account()
        self.runner = Runner(self.account)
        threading.Thread(target=self.raiser.loop, name="raiser", daemon=True).start()
        threading.Thread(target=self.online_loop, name="online", daemon=True).start()
        self.notify(f"🟢 <b>Бот запущен</b>\nАккаунт: <b>{esc(self.account.username)}</b>", kind="system")
        self.listen()

    def listen(self):
        # интервал читается на каждом круге — его можно менять из Telegram без перезапуска
        def delay() -> float:
            return max(2.0, float(self.storage.setting("runner_delay", 6)))
        for event in self.runner.listen(requests_delay=delay):
            try:
                self.process_event(event)
            except Exception:
                logger.exception("Ошибка при обработке события")

    def online_loop(self):
        """Вечный онлайн + регулярное обновление сессии (PHPSESSID / csrf)."""
        last_refresh = time.time()
        while True:
            time.sleep(120)
            try:
                if time.time() - last_refresh > 40 * 60:
                    self.account.get(update_phpsessid=True)
                    last_refresh = time.time()
                elif self.storage.setting("eternal_online"):
                    self.account.method("get", "https://funpay.com/", {}, {})
            except Exception as e:
                logger.debug(f"online_loop: {e}")

    # ------------------------------------------------------------------ прокси
    def set_proxy(self, target: str, proxy: str):
        """
        Сохраняет и сразу применяет прокси (пустая строка — без прокси). Перезапуск не нужен.
        :param target: "telegram" — для связи с Telegram (обход блокировки), "funpay" — для запросов к FunPay.
        """
        with self.storage.config.lock:
            self.storage.config.data[target]["proxy"] = proxy
            self.storage.config.save()
        if target == "funpay":
            self.account.proxy = to_requests(proxy)
        elif self.tg:
            self.tg.apply_proxy()
        logger.info(f"Прокси для {'Telegram' if target == 'telegram' else 'FunPay'}: {mask(proxy)}.")

    # ------------------------------------------------------------------ уведомления
    def notify(self, text: str, kind: str = "system", keyboard=None):
        if self.tg:
            self.tg.broadcast(text, keyboard=keyboard)
        else:
            logger.info(text)

    # ------------------------------------------------------------------ отправка сообщений
    def send_message(self, chat_id: int | str, text: str, chat_name: Optional[str] = None) -> bool:
        for attempt in range(3):
            try:
                self.account.send_message(chat_id, text, chat_name)
                return True
            except (requests.exceptions.ConnectionError, exceptions.RequestFailedError) as e:
                # запрос точно не дошёл до FunPay (или FunPay ответил ошибкой) — можно повторить
                if isinstance(e, exceptions.RequestFailedError) and e.status_code < 500 and e.status_code != 429:
                    logger.warning(f"Не удалось отправить сообщение в чат {chat_id}: {e.short_str()}")
                    return False
                logger.warning(f"Не удалось отправить сообщение в чат {chat_id} (попытка {attempt + 1}): {e}")
                time.sleep(2)
            except Exception as e:
                # таймаут ответа и прочее: сообщение могло уйти — не повторяем, чтобы не было дублей
                logger.warning(f"Ошибка при отправке сообщения в чат {chat_id}: {type(e).__name__}: {e}")
                return False
        return False

    def send_image(self, chat_id: int, image: bytes, chat_name: Optional[str] = None) -> None:
        self.account.send_image(chat_id, image, chat_name)

    # ------------------------------------------------------------------ события
    def process_event(self, event):
        if isinstance(event, events.NewMessageEvent):
            self.on_new_message(event)
        elif isinstance(event, events.NewOrderEvent):
            self.on_new_order(event.order)
        elif isinstance(event, events.OrderStatusChangedEvent):
            self.on_order_status_changed(event.order)

    def on_new_message(self, event: events.NewMessageEvent):
        msg = event.message
        self.stats["messages"] += 1
        logger.info(f"[{msg.chat_name}] {msg.author}: {msg.text or msg.image_link}")
        is_mine = msg.author_id == self.account.id
        is_system = msg.author_id == 0

        # Уведомление в Telegram: шлём одно уведомление на всю пачку сообщений чата.
        stack = event.stack.get_stack() if event.stack else [event]
        if stack and stack[-1] is event and self.tg:
            to_notify = [e.message for e in stack if self._should_notify_message(e.message)]
            if to_notify:
                self.tg.notify_new_messages(msg.chat_id, msg.chat_name, to_notify)

        if is_system:
            if msg.type in REVIEW_TYPES:
                threading.Thread(target=self.handle_review, args=(msg,), daemon=True).start()
            elif msg.type in CONFIRM_TYPES:
                self.handle_order_confirmed(msg)
            return
        if is_mine or not isinstance(msg.chat_id, int):
            return

        blacklisted = self.storage.is_blacklisted(msg.author)
        if not (blacklisted and self.storage.setting("blacklist_block_response")):
            self.handle_greeting(msg)
            self.handle_command(msg)
        self.storage.old_users.data[str(msg.chat_id)] = time.time()
        self.storage.old_users.save()

    def _should_notify_message(self, msg: types.Message) -> bool:
        if not self.storage.notify_enabled("messages"):
            return False
        if msg.author_id == self.account.id:
            # свои сообщения: только отправленные не ботом (например, с сайта), и если включено
            return not msg.by_bot and self.storage.notify_enabled("my_messages")
        if msg.type in REVIEW_TYPES:
            return False  # для отзывов отдельное уведомление
        if msg.type is MessageTypes.ORDER_PURCHASED:
            return False  # для заказов отдельное уведомление
        return True

    # ---------- приветствие
    def handle_greeting(self, msg: types.Message) -> bool:
        if not self.storage.setting("greetings"):
            return False
        last_seen = self.storage.old_users.data.get(str(msg.chat_id))
        cooldown = float(self.storage.setting("greeting_cooldown_days", 2)) * 86400
        if last_seen and time.time() - last_seen < cooldown:
            return False
        text = format_text(self.storage.setting("greeting_text", ""), username=msg.chat_name or msg.author,
                           chat_name=msg.chat_name, message_text=msg.text or "")
        if text.strip():
            logger.info(f"Отправляю приветствие в чат {msg.chat_name}")
            return self.send_message(msg.chat_id, text, msg.chat_name)
        return False

    # ---------- автоответ на команды
    def find_command(self, text: str) -> Optional[dict]:
        cmd = normalize_command(text)
        for key, data in self.storage.auto_response.data.items():
            aliases = [normalize_command(a) for a in key.split("|") if a.strip()]
            if cmd in aliases:
                return data
        return None

    def handle_command(self, msg: types.Message):
        if not self.storage.setting("auto_response") or not msg.text:
            return
        data = self.find_command(msg.text)
        if not data:
            return
        self.stats["auto_responses"] += 1
        text = format_text(data.get("response", ""), username=msg.chat_name or msg.author, chat_name=msg.chat_name,
                           message_text=msg.text)
        logger.info(f"Команда «{msg.text}» от {msg.author}: отправляю автоответ.")
        if text.strip():
            self.send_message(msg.chat_id, text, msg.chat_name)
        if data.get("notify") or self.storage.notify_enabled("commands"):
            if self.tg:
                self.tg.notify_command(msg, data)

    # ---------- отзывы
    def handle_review(self, msg: types.Message):
        found = RegularExpressions().ORDER_ID.search(msg.text or "")
        if not found:
            return
        order_id = found.group(0)[1:]
        try:
            order = self.account.get_order(order_id)
        except Exception:
            logger.exception(f"Не удалось получить заказ {order_id}")
            return
        if order.seller_id != self.account.id:
            return
        review = order.review
        if self.tg and self.storage.notify_enabled("reviews"):
            self.tg.notify_review(order, review, changed=msg.type is MessageTypes.FEEDBACK_CHANGED)
        if not review or not review.stars or not self.storage.setting("review_replies"):
            return
        reply_cfg = self.storage.review_replies.data.get(str(review.stars))
        if not reply_cfg or not reply_cfg.get("enabled") or not reply_cfg.get("text", "").strip():
            return
        text = format_text(reply_cfg["text"], username=order.buyer_username, order_id=order.id,
                           order_title=order.short_description or "", chat_name=order.buyer_username)
        try:
            self.account.send_review(order.id, text, 5)
            self.stats["reviews_answered"] += 1
            logger.info(f"Ответил на отзыв к заказу #{order.id} ({review.stars}⭐).")
        except Exception:
            logger.exception(f"Не удалось ответить на отзыв к заказу #{order.id}")

    def handle_order_confirmed(self, msg: types.Message):
        found = RegularExpressions().ORDER_ID.search(msg.text or "")
        order_id = found.group(0) if found else ""
        # Убеждаемся, что это наша продажа (а не наша покупка)
        if self.runner and order_id[1:] not in self.runner.saved_orders:
            return
        if self.tg and self.storage.notify_enabled("order_status"):
            self.tg.broadcast(f"✅ Заказ <b>{esc(order_id)}</b> подтверждён покупателем <b>{esc(msg.chat_name)}</b>.",
                              keyboard=self.tg.kb_chat_actions(msg.chat_id, msg.chat_name))
        if self.storage.setting("order_confirm_reply") and isinstance(msg.chat_id, int):
            text = format_text(self.storage.setting("order_confirm_text", ""), username=msg.chat_name,
                               order_id=order_id, chat_name=msg.chat_name)
            if text.strip():
                self.send_message(msg.chat_id, text, msg.chat_name)

    # ---------- заказы
    def on_new_order(self, order: types.OrderShortcut):
        self.stats["orders"] += 1
        logger.info(f"Новый заказ #{order.id} от {order.buyer_username}: {order.description} ({order.price} ₽)")
        delivery_result = None
        if self.storage.setting("auto_delivery") and order.status is OrderStatuses.PAID:
            delivery_result = self.deliver(order)
        if self.tg and self.storage.notify_enabled("orders"):
            self.tg.notify_order(order, delivery_result)
        if self.storage.setting("auto_restore"):
            threading.Thread(target=self.restore_lots, args=(order,), daemon=True).start()

    def on_order_status_changed(self, order: types.OrderShortcut):
        if order.status is OrderStatuses.REFUNDED and self.tg and self.storage.notify_enabled("order_status"):
            self.tg.broadcast(f"↩️ По заказу <b>#{esc(order.id)}</b> оформлен возврат ({esc(order.buyer_username)}, "
                              f"{order.price} ₽).")

    def get_order_chat(self, order: types.OrderShortcut) -> Optional[types.ChatShortcut]:
        chat = self.account.get_chat_by_name(order.buyer_username)
        if chat is None:
            try:
                chat = self.account.get_chat_by_name(order.buyer_username, make_request=True)
            except Exception:
                chat = None
        return chat

    # ---------- автовыдача
    def find_delivery(self, description: str) -> tuple[Optional[str], Optional[dict]]:
        description = (description or "").lower()
        best = (None, None)
        for key, cfg in self.storage.auto_delivery.data.items():
            if key.lower() in description:
                # выбираем самое длинное совпадение (наиболее точное)
                if best[0] is None or len(key) > len(best[0]):
                    best = (key, cfg)
        return best

    def deliver(self, order: types.OrderShortcut) -> Optional[str]:
        """Автовыдача товара. Возвращает текстовый результат для уведомления или None, если лот не настроен."""
        key, cfg = self.find_delivery(order.description)
        if not cfg or not cfg.get("enabled", True):
            return None
        if self.storage.is_blacklisted(order.buyer_username) and self.storage.setting("blacklist_block_delivery"):
            return "⛔ Покупатель в чёрном списке — товар не выдан."
        amount = order.amount or 1
        goods: list[str] = []
        left = None
        if cfg.get("goods_file"):
            amount_to_take = amount if cfg.get("multi", True) else 1
            goods, left = self.storage.take_goods(cfg["goods_file"], amount_to_take)
            if not goods:
                msg = f"❌ Товары закончились (нужно {amount_to_take}, осталось {left}). Выдайте вручную!"
                if self.storage.setting("auto_disable"):
                    self.disable_lots_by_title(order.description)
                return msg
        product = "\n".join(goods)
        text = format_text(cfg.get("response", "$product"), username=order.buyer_username, order_id=order.id,
                           order_title=order.description, product=product, chat_name=order.buyer_username)
        chat = self.get_order_chat(order)
        if not chat:
            if goods:
                self.storage.return_goods(cfg["goods_file"], goods)
            return "❌ Не удалось найти чат с покупателем — товар не выдан."
        if not self.send_message(chat.id, text, chat.name):
            if goods:
                self.storage.return_goods(cfg["goods_file"], goods)
            return "❌ Не удалось отправить товар (ошибка FunPay). Товар возвращён в файл."
        self.stats["delivered"] += 1
        result = f"✅ Товар выдан ({len(goods) or 1} шт.)."
        if left is not None:
            result += f" Осталось: {left}."
            if left == 0 and self.storage.setting("auto_disable"):
                self.disable_lots_by_title(order.description)
                result += " Лот деактивирован."
        logger.info(f"Автовыдача по заказу #{order.id}: {result}")
        return result

    def disable_lots_by_title(self, title: str):
        for lot_id in self.lots.find_by_title(title):
            try:
                self.lots.set_active(lot_id, False)
                logger.info(f"Лот {lot_id} деактивирован (товары закончились).")
            except Exception:
                logger.exception(f"Не удалось деактивировать лот {lot_id}")

    # ---------- автовосстановление
    def restore_lots(self, order: types.OrderShortcut):
        """Если после продажи лот выключился (деактивация после продажи) — включаем его обратно."""
        time.sleep(3)
        key, cfg = self.find_delivery(order.description)
        if cfg and cfg.get("goods_file") and not self.storage.read_goods(cfg["goods_file"]):
            return  # товаров нет — не восстанавливаем
        for lot_id in self.lots.find_by_title(order.description):
            try:
                fields = self.account.get_lot_fields(lot_id)
                if fields.active:
                    continue
                fields.active = True
                self.lots.save(fields)
                logger.info(f"Лот {lot_id} восстановлен после продажи.")
            except Exception:
                logger.exception(f"Не удалось восстановить лот {lot_id}")

    # ------------------------------------------------------------------ баланс
    def get_balance(self) -> types.Balance:
        """Баланс берётся со страницы любого чужого лота (FunPay показывает его в форме оплаты)."""
        candidates = [self.balance_lot_id] if self.balance_lot_id else []
        candidates.append(18853876)
        for lot_id in candidates:
            try:
                return self.account.get_balance(lot_id)
            except Exception:
                logger.debug(f"get_balance({lot_id}) не сработал", exc_info=True)
        my_ids = set(self.lots.cache)
        for subcat in self.account.subcategories[:15]:
            if subcat.type is not SubCategoryTypes.COMMON:
                continue
            try:
                lots = self.account.get_subcategory_public_lots(subcat.type, subcat.id)
            except Exception:
                continue
            for lot in lots[:3]:
                if str(lot.id) in my_ids:
                    continue
                try:
                    balance = self.account.get_balance(int(lot.id))
                    self.balance_lot_id = int(lot.id)
                    return balance
                except Exception:
                    continue
        raise RuntimeError("Не удалось получить баланс")
