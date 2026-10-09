"""
Базовая часть Telegram-бота: авторизация, роутинг кнопок, состояния (ожидание ввода), рассылка уведомлений.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable, Optional

import telebot
from telebot import types as tg

from cardinal.proxy import to_requests
from cardinal.utils import split_text

if TYPE_CHECKING:
    from cardinal.core import Cardinal

logger = logging.getLogger("FPC.tg")


def btn(text: str, data: str) -> tg.InlineKeyboardButton:
    return tg.InlineKeyboardButton(text, callback_data=data[:64])


def url_btn(text: str, url: str) -> tg.InlineKeyboardButton:
    return tg.InlineKeyboardButton(text, url=url)


def kb(*rows: list[tg.InlineKeyboardButton] | tg.InlineKeyboardButton) -> tg.InlineKeyboardMarkup:
    markup = tg.InlineKeyboardMarkup()
    for row in rows:
        if isinstance(row, tg.InlineKeyboardButton):
            row = [row]
        if row:
            markup.row(*row)
    return markup


def onoff(value: Any) -> str:
    return "🟢" if value else "🔴"


BACK = "⬅️ Назад"
KEEP_STATE_PREFIXES = ("noop", "nls:", "nlskip", "nlc")
MAIN_REPLY_BUTTONS = {
    "💬 Чаты": "chats",
    "🛒 Заказы": "orders",
    "📦 Лоты": "lots:0",
    "💰 Баланс": "bal",
    "⚙️ Панель управления": "menu",
    "📊 Статус": "st",
}


class Ctx:
    """Контекст: откуда пришёл запрос (кнопка или сообщение) — чтобы редактировать или отправлять новое сообщение."""

    def __init__(self, bot: telebot.TeleBot, chat_id: int, user_id: int, message_id: Optional[int] = None,
                 call: Optional[tg.CallbackQuery] = None):
        self.bot = bot
        self.chat_id = chat_id
        self.user_id = user_id
        self.message_id = message_id
        self.call = call

    def answer(self, text: str = "", alert: bool = False):
        if self.call:
            try:
                self.bot.answer_callback_query(self.call.id, text[:200], show_alert=alert)
            except Exception:
                pass
            self.call = None

    def show(self, text: str, markup: Optional[tg.InlineKeyboardMarkup] = None, new: bool = False):
        """Редактирует текущее сообщение с меню (если это нажатие кнопки), иначе отправляет новое."""
        self.answer()
        text = text[:4096]
        if self.message_id and not new:
            try:
                self.bot.edit_message_text(text, self.chat_id, self.message_id, reply_markup=markup,
                                           disable_web_page_preview=True)
                return
            except telebot.apihelper.ApiTelegramException as e:
                if "message is not modified" in str(e):
                    return
        sent = self.bot.send_message(self.chat_id, text, reply_markup=markup, disable_web_page_preview=True)
        self.message_id = sent.message_id

    def send(self, text: str, markup: Optional[tg.InlineKeyboardMarkup] = None) -> tg.Message:
        self.answer()
        return self.bot.send_message(self.chat_id, text[:4096], reply_markup=markup, disable_web_page_preview=True)


class BaseBot:
    def __init__(self, cardinal: Cardinal):
        self.c = cardinal
        self.storage = cardinal.storage
        token = self.storage.config.data["telegram"]["token"]
        self.apply_proxy()
        self.bot = telebot.TeleBot(token, parse_mode="HTML", threaded=True, num_threads=6)
        self.states: dict[int, dict] = {}
        # (tg chat id, tg message id) -> (FunPay chat id, имя собеседника) — для ответа «реплаем» на уведомление
        self.reply_map: OrderedDict[tuple[int, int], tuple[int, str]] = OrderedDict()
        self.callbacks: dict[str, Callable] = {}
        self.state_handlers: dict[str, Callable] = {}
        self.login_attempts: dict[int, list[float]] = {}
        self._register_base()

    def apply_proxy(self):
        """Включает / выключает прокси для запросов к Telegram (действует сразу)."""
        telebot.apihelper.proxy = to_requests(self.storage.proxy) if self.storage.telegram_uses_proxy else None

    # ------------------------------------------------------------------ регистрация
    def on_callback(self, name: str):
        def decorator(func):
            self.callbacks[name] = func
            return func
        return decorator

    def register(self, mapping: dict[str, Callable]):
        self.callbacks.update(mapping)

    def register_states(self, mapping: dict[str, Callable]):
        self.state_handlers.update(mapping)

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.storage.admins

    def _register_base(self):
        bot = self.bot

        @bot.message_handler(func=lambda m: not self.is_admin(m.from_user.id), content_types=["text"])
        def auth(message: tg.Message):
            self.handle_auth(message)

        @bot.message_handler(commands=["start", "menu"], func=lambda m: self.is_admin(m.from_user.id))
        def start(message: tg.Message):
            self.states.pop(message.from_user.id, None)
            self.bot.send_message(message.chat.id, "🏠 Главное меню", reply_markup=self.reply_keyboard())
            self.dispatch("menu", self.ctx_from_message(message))

        @bot.message_handler(commands=["cancel"], func=lambda m: self.is_admin(m.from_user.id))
        def cancel(message: tg.Message):
            self.states.pop(message.from_user.id, None)
            self.bot.send_message(message.chat.id, "❌ Действие отменено.", reply_markup=self.reply_keyboard())

        @bot.message_handler(commands=["logout"], func=lambda m: self.is_admin(m.from_user.id))
        def logout(message: tg.Message):
            self.storage.remove_admin(message.from_user.id)
            self.bot.send_message(message.chat.id, "🚪 Вы вышли из панели управления.",
                                  reply_markup=tg.ReplyKeyboardRemove())

        simple_commands = {"balance": "bal", "lots": "lots:0", "chats": "chats", "orders": "orders",
                           "raise": "rs", "status": "st", "panel": "menu"}

        @bot.message_handler(commands=list(simple_commands), func=lambda m: self.is_admin(m.from_user.id))
        def commands(message: tg.Message):
            cmd = message.text.split()[0].lstrip("/").split("@")[0]
            self.states.pop(message.from_user.id, None)
            self.dispatch(simple_commands[cmd], self.ctx_from_message(message))

        @bot.message_handler(func=lambda m: self.is_admin(m.from_user.id),
                             content_types=["text", "photo", "document"])
        def admin_message(message: tg.Message):
            self.handle_admin_message(message)

        @bot.callback_query_handler(func=lambda c: True)
        def callback(call: tg.CallbackQuery):
            if not self.is_admin(call.from_user.id):
                self.bot.answer_callback_query(call.id, "⛔ Нет доступа. Отправьте пароль боту.", show_alert=True)
                return
            ctx = Ctx(self.bot, call.message.chat.id, call.from_user.id, call.message.message_id, call)
            # нажатие любой кнопки отменяет ожидание ввода (кроме кнопок, которые являются частью ввода)
            if not call.data.startswith(KEEP_STATE_PREFIXES):
                self.states.pop(call.from_user.id, None)
            self.dispatch(call.data, ctx)

        self.register({"noop": lambda ctx: ctx.answer(), "close": self.cb_close})

    def cb_close(self, ctx: Ctx):
        ctx.answer()
        try:
            self.bot.delete_message(ctx.chat_id, ctx.message_id)
        except Exception:
            pass

    def ctx_from_message(self, message: tg.Message) -> Ctx:
        return Ctx(self.bot, message.chat.id, message.from_user.id)

    def dispatch(self, data: str, ctx: Ctx):
        name, *args = data.split(":")
        handler = self.callbacks.get(name)
        if not handler:
            ctx.answer("Неизвестная команда")
            return
        try:
            handler(ctx, *args)
        except Exception as e:
            logger.exception(f"Ошибка при обработке «{data}»")
            ctx.answer()
            try:
                ctx.send(f"❌ Ошибка: <code>{telebot.formatting.escape_html(str(e))[:500]}</code>")
            except Exception:
                pass

    # ------------------------------------------------------------------ авторизация
    def handle_auth(self, message: tg.Message):
        user_id = message.from_user.id
        attempts = [t for t in self.login_attempts.get(user_id, []) if time.time() - t < 600]
        if len(attempts) >= 5:
            self.bot.send_message(message.chat.id, "⏳ Слишком много попыток. Попробуйте через 10 минут.")
            return
        if message.text and message.text.startswith("/start"):
            self.bot.send_message(message.chat.id, "🔐 Это панель управления FunPay-ботом.\nОтправьте пароль для входа.")
            return
        if message.text and self.storage.check_password(message.text.strip()):
            self.storage.add_admin(user_id)
            try:
                self.bot.delete_message(message.chat.id, message.message_id)
            except Exception:
                pass
            logger.warning(f"Новый администратор Telegram: {message.from_user.username} ({user_id})")
            self.bot.send_message(message.chat.id, "✅ Доступ открыт! Теперь сюда будут приходить уведомления.",
                                  reply_markup=self.reply_keyboard())
            self.dispatch("menu", self.ctx_from_message(message))
            for admin in self.storage.admins:
                if admin != user_id:
                    self.safe_send(admin, f"⚠️ Новый вход в панель: @{message.from_user.username} "
                                          f"(<code>{user_id}</code>)")
            return
        attempts.append(time.time())
        self.login_attempts[user_id] = attempts
        self.bot.send_message(message.chat.id, "❌ Неверный пароль.")

    def reply_keyboard(self) -> tg.ReplyKeyboardMarkup:
        markup = tg.ReplyKeyboardMarkup(resize_keyboard=True)
        names = list(MAIN_REPLY_BUTTONS)
        markup.row(names[0], names[1])
        markup.row(names[2], names[3])
        markup.row(names[4], names[5])
        return markup

    # ------------------------------------------------------------------ состояния
    def set_state(self, user_id: int, name: str, **data):
        self.states[user_id] = {"name": name, **data}

    def ask(self, ctx: Ctx, state: str, text: str, **data):
        """Просит ввести значение и переводит пользователя в состояние ожидания."""
        self.set_state(ctx.user_id, state, **data)
        ctx.answer()
        ctx.send(text + "\n\n<i>Для отмены — /cancel</i>")

    def handle_admin_message(self, message: tg.Message):
        user_id = message.from_user.id
        if message.content_type == "text" and message.text in MAIN_REPLY_BUTTONS:
            self.states.pop(user_id, None)
            self.dispatch(MAIN_REPLY_BUTTONS[message.text], self.ctx_from_message(message))
            return

        # Ответ «реплаем» на уведомление о сообщении — отправляем в чат FunPay
        if message.reply_to_message:
            key = (message.chat.id, message.reply_to_message.message_id)
            if key in self.reply_map:
                chat_id, chat_name = self.reply_map[key]
                self.send_to_funpay(message, chat_id, chat_name)
                return

        state = self.states.get(user_id)
        if state:
            handler = self.state_handlers.get(state["name"])
            if handler:
                try:
                    handler(message, state)
                except Exception as e:
                    logger.exception("Ошибка в обработчике ввода")
                    self.bot.send_message(message.chat.id, f"❌ Ошибка: <code>{telebot.formatting.escape_html(str(e))[:500]}</code>")
                return
        self.bot.send_message(message.chat.id, "🤔 Не понял. Используйте кнопки меню или ответьте (reply) на "
                                               "уведомление о сообщении, чтобы написать покупателю.",
                              reply_markup=self.reply_keyboard())

    # ------------------------------------------------------------------ отправка в FunPay
    def download_image(self, message: tg.Message) -> Optional[bytes]:
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
        elif message.content_type == "document" and (message.document.mime_type or "").startswith("image/"):
            file_id = message.document.file_id
        else:
            return None
        info = self.bot.get_file(file_id)
        return self.bot.download_file(info.file_path)

    def send_to_funpay(self, message: tg.Message, chat_id: int, chat_name: Optional[str]) -> bool:
        """Отправляет текст / фото из Telegram в чат FunPay."""
        try:
            if message.content_type in ("photo", "document"):
                image = self.download_image(message)
                if image is None:
                    self.bot.reply_to(message, "❌ Можно отправлять только изображения.")
                    return False
                self.c.send_image(chat_id, image, chat_name)
                if message.caption:
                    self.c.account.send_message(chat_id, message.caption, chat_name)
            else:
                self.c.account.send_message(chat_id, message.text, chat_name)
        except Exception as e:
            logger.exception("Не удалось отправить сообщение в FunPay")
            self.bot.reply_to(message, f"❌ Не отправлено: <code>{telebot.formatting.escape_html(str(e))[:300]}</code>")
            return False
        self.bot.reply_to(message, f"✅ Отправлено в чат <b>{telebot.formatting.escape_html(chat_name or str(chat_id))}</b>",
                          reply_markup=self.kb_chat_actions(chat_id, chat_name, compact=True))
        return True

    def kb_chat_actions(self, chat_id: int | str, chat_name: Optional[str], compact: bool = False):
        raise NotImplementedError

    # ------------------------------------------------------------------ рассылка
    def safe_send(self, user_id: int, text: str, markup=None) -> Optional[tg.Message]:
        for attempt in range(3):
            try:
                return self.bot.send_message(user_id, text, reply_markup=markup, disable_web_page_preview=True)
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    time.sleep(int(e.result_json.get("parameters", {}).get("retry_after", 3)))
                    continue
                if e.error_code in (400, 403):
                    logger.warning(f"Не удалось отправить сообщение админу {user_id}: {e.description}")
                    return None
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Ошибка отправки в Telegram: {e}")
                time.sleep(2)
        return None

    def broadcast(self, text: str, keyboard=None, reply_to_chat: Optional[tuple[int, str]] = None):
        parts = split_text(text)
        for admin in list(self.storage.admins):
            for i, part in enumerate(parts):
                sent = self.safe_send(admin, part, keyboard if i == len(parts) - 1 else None)
                if sent and reply_to_chat:
                    self.reply_map[(sent.chat.id, sent.message_id)] = reply_to_chat
        while len(self.reply_map) > 3000:
            self.reply_map.popitem(last=False)

    def start(self):
        try:
            me = self.bot.get_me()
            logger.info(f"Telegram-бот @{me.username} запущен.")
        except Exception as e:
            logger.error(f"Не удалось подключиться к Telegram: {e}")
        try:
            self.bot.set_my_commands([
                tg.BotCommand("menu", "Главное меню"),
                tg.BotCommand("chats", "Чаты"),
                tg.BotCommand("orders", "Активные заказы"),
                tg.BotCommand("lots", "Мои лоты"),
                tg.BotCommand("balance", "Баланс"),
                tg.BotCommand("raise", "Автоподнятие"),
                tg.BotCommand("status", "Статус бота"),
                tg.BotCommand("cancel", "Отменить ввод"),
            ])
        except Exception:
            pass
        threading.Thread(target=self._polling, name="telegram", daemon=True).start()

    def _polling(self):
        while True:
            try:
                self.bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
            except Exception:
                logger.exception("Telegram polling упал, перезапуск через 5 сек.")
                time.sleep(5)
