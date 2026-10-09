"""
Telegram-панель: уведомления, чаты, заказы, баланс, статус, главное меню.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Optional

from telebot import types as tg

from FunPayAPI import types
from FunPayAPI.common.enums import OrderStatuses
from cardinal.utils import chat_link, esc, human_time, order_link

from .base import BACK, BaseBot, Ctx, btn, kb, onoff, url_btn
from .lots_ui import LotsMixin
from .panel import PanelMixin

if TYPE_CHECKING:
    from cardinal.core import Cardinal

logger = logging.getLogger("FPC.tg")

STARS = {1: "⭐", 2: "⭐⭐", 3: "⭐⭐⭐", 4: "⭐⭐⭐⭐", 5: "⭐⭐⭐⭐⭐"}


class TGBot(LotsMixin, PanelMixin, BaseBot):
    def __init__(self, cardinal: Cardinal):
        super().__init__(cardinal)
        self.register({
            "menu": self.cb_menu,
            "st": self.cb_status,
            "bal": self.cb_balance,
            "chats": self.cb_chats,
            "chat": self.cb_chat,
            "rep": self.cb_reply,
            "rpu": self.cb_reply_user,
            "tpl": self.cb_templates_for_chat,
            "tps": self.cb_send_template,
            "orders": self.cb_orders,
            "ord": self.cb_order,
            "ref": self.cb_refund,
            "refy": self.cb_refund_yes,
            "rvr": self.cb_review_reply,
        })
        self.register_states({
            "reply": self.st_reply,
            "review_reply": self.st_review_reply,
        })
        self.register_lots()
        self.register_panel()

    # ================================================================== уведомления
    def chat_name(self, chat_id: int) -> Optional[str]:
        chat = self.c.account.get_chat_by_id(int(chat_id)) if self.c.account.is_initiated else None
        return chat.name if chat else None

    def kb_chat_actions(self, chat_id: int | str, chat_name: Optional[str], compact: bool = False):
        if compact:
            return kb([btn("✉️ Ещё", f"rep:{chat_id}"), btn("📜 История", f"chat:{chat_id}")])
        return kb(
            [btn("✉️ Ответить", f"rep:{chat_id}"), btn("📝 Заготовки", f"tpl:{chat_id}")],
            [btn("📜 История", f"chat:{chat_id}"), url_btn("🌐 Открыть чат", chat_link(chat_id))],
        )

    @staticmethod
    def format_message(msg: types.Message, show_author: bool = True) -> str:
        if msg.image_link:
            body = f'🖼 <a href="{esc(msg.image_link)}">Изображение</a>'
        else:
            body = esc(msg.text)
        if msg.author_id == 0:
            return f"🔔 <i>{body}</i>"
        if not show_author:
            return body
        author = msg.author or msg.chat_name or "?"
        badge = f" [{esc(msg.badge)}]" if msg.badge else ""
        return f"<b>{esc(author)}{badge}:</b> {body}"

    def notify_new_messages(self, chat_id: int | str, chat_name: Optional[str], messages: list[types.Message]):
        is_support = any(m.badge for m in messages)
        header = "🆘 <b>Сообщение от поддержки!</b>" if is_support else "💬 <b>Новое сообщение</b>"
        lines = [f"{header} в чате <a href=\"{chat_link(chat_id)}\">{esc(chat_name or chat_id)}</a>", ""]
        lines += [self.format_message(m) for m in messages]
        lines += ["", "<i>↩️ Ответьте реплаем на это сообщение текстом или фото — оно уйдёт покупателю.</i>"]
        self.broadcast("\n".join(lines), keyboard=self.kb_chat_actions(chat_id, chat_name),
                       reply_to_chat=(chat_id, chat_name) if isinstance(chat_id, int) else None)

    def notify_command(self, msg: types.Message, data: dict):
        self.broadcast(f"🤖 <b>{esc(msg.author)}</b> отправил команду <code>{esc(msg.text)}</code> — автоответ отправлен.",
                       keyboard=self.kb_chat_actions(msg.chat_id, msg.chat_name),
                       reply_to_chat=(msg.chat_id, msg.chat_name))

    def notify_order(self, order: types.OrderShortcut, delivery_result: Optional[str]):
        chat = self.c.account.get_chat_by_name(order.buyer_username)
        text = (f"🛒 <b>Новый заказ</b> <a href=\"{order_link(order.id)}\">#{esc(order.id)}</a>\n\n"
                f"👤 Покупатель: <b>{esc(order.buyer_username)}</b>\n"
                f"📦 Товар: {esc(order.description)}\n"
                f"🔢 Кол-во: {order.amount or 1}\n"
                f"💰 Сумма: <b>{order.price} ₽</b>\n"
                f"🗂 {esc(order.subcategory_name)}")
        if delivery_result:
            text += f"\n\n🚚 Автовыдача: {esc(delivery_result)}"
        rows = [[btn("✉️ Написать покупателю", f"rpu:{order.buyer_username}"), btn("📝 Заготовки", f"tpl:{chat.id}")]
                if chat else [btn("✉️ Написать покупателю", f"rpu:{order.buyer_username}")],
                [btn("📄 Заказ", f"ord:{order.id}"), url_btn("🌐 На FunPay", order_link(order.id))]]
        self.broadcast(text, keyboard=kb(*rows), reply_to_chat=(chat.id, chat.name) if chat else None)

    def notify_review(self, order: types.Order, review: Optional[types.Review], changed: bool = False):
        if not review or not review.stars:
            return
        title = "✏️ <b>Отзыв изменён</b>" if changed else "🌟 <b>Новый отзыв</b>"
        text = (f"{title} {STARS.get(review.stars, '')}\n\n"
                f"👤 {esc(order.buyer_username)} · заказ <a href=\"{order_link(order.id)}\">#{esc(order.id)}</a>\n"
                f"📦 {esc(order.short_description or '')}\n\n"
                f"💬 {esc(review.text or '(без текста)')}")
        self.broadcast(text, keyboard=kb(
            [btn("💬 Ответить на отзыв", f"rvr:{order.id}"), btn("✉️ Написать", f"rpu:{order.buyer_username}")],
            url_btn("🌐 Открыть заказ", order_link(order.id)),
        ))

    # ================================================================== главное меню
    def cb_menu(self, ctx: Ctx):
        s = self.storage.settings
        text = (f"⚙️ <b>Панель управления</b>\n"
                f"Аккаунт: <b>{esc(self.c.account.username or '...')}</b>\n\n"
                f"{onoff(s['auto_raise'])} Автоподнятие  {onoff(s['auto_response'])} Автоответ\n"
                f"{onoff(s['auto_delivery'])} Автовыдача  {onoff(s['review_replies'])} Ответы на отзывы\n"
                f"{onoff(s['greetings'])} Приветствие  {onoff(s['eternal_online'])} Вечный онлайн")
        ctx.show(text, kb(
            [btn("⬆️ Автоподнятие", "rs"), btn("🤖 Автоответ", "ar")],
            [btn("⭐ Ответы на отзывы", "rv"), btn("👋 Приветствие", "gr")],
            [btn("🚚 Автовыдача", "ad"), btn("📝 Заготовки", "tp")],
            [btn("📦 Лоты", "lots:0"), btn("➕ Новый лот", "lot_new")],
            [btn("💬 Чаты", "chats"), btn("🛒 Заказы", "orders")],
            [btn("💰 Баланс", "bal"), btn("📊 Статус", "st")],
            [btn("🔔 Уведомления", "nt"), btn("⛔ Чёрный список", "bl")],
            [btn("🛠 Настройки", "set")],
        ))

    def cb_status(self, ctx: Ctx):
        a = self.c.account
        st = self.c.stats
        text = (f"📊 <b>Статус</b>\n\n"
                f"👤 Аккаунт: <b>{esc(a.username)}</b> (ID {a.id})\n"
                f"⏱ Аптайм: {human_time(time.time() - self.c.start_time)}\n"
                f"🛒 Активных продаж: {a.active_sales}\n\n"
                f"За эту сессию:\n"
                f"💬 Сообщений: {st['messages']}\n"
                f"🛒 Заказов: {st['orders']}\n"
                f"🚚 Выдано товаров: {st['delivered']}\n"
                f"🤖 Автоответов: {st['auto_responses']}\n"
                f"⭐ Ответов на отзывы: {st['reviews_answered']}\n\n"
                f"⬆️ <b>Автоподнятие:</b>\n{self.c.raiser.status()}")
        ctx.show(text, kb([btn("🔄 Обновить", "st"), btn(BACK, "menu")]))

    def cb_balance(self, ctx: Ctx):
        ctx.answer("⏳ Загружаю баланс...")
        b = self.c.get_balance()
        text = (f"💰 <b>Баланс</b>\n\n"
                f"🇷🇺 <b>{b.total_rub} ₽</b> (доступно к выводу: {b.available_rub} ₽)\n"
                f"🇺🇸 <b>{b.total_usd} $</b> (доступно: {b.available_usd} $)\n"
                f"🇪🇺 <b>{b.total_eur} €</b> (доступно: {b.available_eur} €)\n\n"
                f"<i>Обновлено в {time.strftime('%H:%M:%S')}</i>")
        ctx.show(text, kb([btn("🔄 Обновить", "bal"), url_btn("💸 Вывод", "https://funpay.com/account/balance")],
                          btn(BACK, "menu")))

    # ================================================================== чаты
    def cb_chats(self, ctx: Ctx):
        ctx.answer("⏳ Загружаю чаты...")
        chats = list(self.c.account.get_chats(update=True).values())
        rows = []
        for chat in chats[:25]:
            mark = "🔵 " if chat.unread else ""
            last = (chat.last_message_text or "").replace("\n", " ")[:28]
            rows.append(btn(f"{mark}{chat.name}: {last}", f"chat:{chat.id}"))
        rows.append([btn("🔄 Обновить", "chats"), btn(BACK, "menu")])
        ctx.show("💬 <b>Последние чаты</b> (🔵 — непрочитанные)", kb(*rows))

    def cb_chat(self, ctx: Ctx, chat_id: str):
        chat_id = int(chat_id)
        ctx.answer("⏳ Загружаю историю...")
        name = self.chat_name(chat_id)
        history = self.c.account.get_chat_history(chat_id, interlocutor_username=name)[-15:]
        lines = [f"📜 <b>Чат с {esc(name or chat_id)}</b>", ""]
        for m in history:
            prefix = "🟦 " if m.author_id == self.c.account.id else ""
            lines.append(prefix + self.format_message(m))
        if not history:
            lines.append("<i>Сообщений нет</i>")
        text = "\n".join(lines)
        if len(text) > 4000:
            text = text[-4000:]
        sent = ctx.send(text, self.kb_chat_actions(chat_id, name))
        self.reply_map[(sent.chat.id, sent.message_id)] = (chat_id, name)

    def cb_reply(self, ctx: Ctx, chat_id: str):
        chat_id = int(chat_id)
        name = self.chat_name(chat_id)
        self.ask(ctx, "reply", f"✉️ Напишите сообщение для <b>{esc(name or chat_id)}</b>.\n"
                               f"Можно отправить текст или фото (с подписью).", chat_id=chat_id, chat_name=name)

    def cb_reply_user(self, ctx: Ctx, username: str):
        chat = self.c.account.get_chat_by_name(username, make_request=True)
        if not chat:
            ctx.answer("Чат с покупателем не найден", alert=True)
            return
        self.cb_reply(ctx, str(chat.id))

    def st_reply(self, message: tg.Message, state: dict):
        if self.send_to_funpay(message, state["chat_id"], state.get("chat_name")):
            self.states.pop(message.from_user.id, None)

    def cb_templates_for_chat(self, ctx: Ctx, chat_id: str):
        templates = self.storage.templates.data
        if not templates:
            ctx.answer("Заготовок нет. Добавьте их в Панели → Заготовки", alert=True)
            return
        rows = [btn(t.replace("\n", " ")[:60], f"tps:{chat_id}:{i}") for i, t in enumerate(templates)]
        rows.append(btn("❌ Закрыть", "close"))
        ctx.send("📝 <b>Выберите заготовку</b> — она сразу отправится в чат:", kb(*rows))
        ctx.answer()

    def cb_send_template(self, ctx: Ctx, chat_id: str, idx: str):
        from cardinal.utils import format_text
        chat_id_int = int(chat_id)
        templates = self.storage.templates.data
        if int(idx) >= len(templates):
            ctx.answer("Заготовка не найдена", alert=True)
            return
        name = self.chat_name(chat_id_int)
        text = format_text(templates[int(idx)], username=name, chat_name=name)
        self.c.account.send_message(chat_id_int, text, name)
        ctx.answer("✅ Отправлено")
        ctx.show(f"✅ Заготовка отправлена в чат <b>{esc(name or chat_id)}</b>:\n\n{esc(text)}",
                 self.kb_chat_actions(chat_id_int, name, compact=True))

    # ================================================================== заказы
    def cb_orders(self, ctx: Ctx):
        ctx.answer("⏳ Загружаю заказы...")
        _, orders = self.c.account.get_sells(state="paid")
        rows = [btn(f"#{o.id} · {o.buyer_username} · {o.price}₽ · {o.description[:25]}", f"ord:{o.id}")
                for o in orders[:30]]
        rows.append([btn("🔄 Обновить", "orders"), btn(BACK, "menu")])
        text = f"🛒 <b>Активные заказы</b> (оплачены, ждут выполнения): {len(orders)}"
        if not orders:
            text += "\n\n<i>Нет активных заказов 🎉</i>"
        ctx.show(text, kb(*rows))

    def cb_order(self, ctx: Ctx, order_id: str):
        ctx.answer("⏳ Загружаю заказ...")
        o = self.c.account.get_order(order_id)
        status = {OrderStatuses.PAID: "🟡 Оплачен", OrderStatuses.CLOSED: "🟢 Закрыт",
                  OrderStatuses.REFUNDED: "🔴 Возврат"}.get(o.status, str(o.status))
        text = (f"📄 <b>Заказ #{esc(o.id)}</b> — {status}\n\n"
                f"👤 Покупатель: <b>{esc(o.buyer_username)}</b>\n"
                f"📦 {esc(o.short_description or '')}\n"
                f"💰 Сумма: <b>{o.sum} ₽</b>\n"
                f"🗂 {esc(o.subcategory.fullname if o.subcategory else '')}")
        if o.full_description:
            text += f"\n\n📝 {esc(o.full_description[:1500])}"
        if o.review and o.review.stars:
            text += f"\n\n{STARS.get(o.review.stars, '')} {esc(o.review.text or '')}"
            if o.review.reply:
                text += f"\n↪️ <i>{esc(o.review.reply)}</i>"
        rows = [[btn("✉️ Написать", f"rpu:{o.buyer_username}"), url_btn("🌐 На FunPay", order_link(o.id))]]
        if o.status is OrderStatuses.PAID:
            rows.append(btn("↩️ Оформить возврат", f"ref:{o.id}"))
        if o.review and o.review.stars:
            rows.append(btn("💬 Ответить на отзыв", f"rvr:{o.id}"))
        rows.append(btn("⬅️ К заказам", "orders"))
        ctx.show(text, kb(*rows))

    def cb_refund(self, ctx: Ctx, order_id: str):
        ctx.show(f"⚠️ Точно оформить <b>возврат</b> по заказу #{esc(order_id)}? Деньги вернутся покупателю.",
                 kb([btn("✅ Да, вернуть", f"refy:{order_id}"), btn("❌ Нет", f"ord:{order_id}")]))

    def cb_refund_yes(self, ctx: Ctx, order_id: str):
        self.c.account.refund(order_id)
        ctx.answer("✅ Возврат оформлен", alert=True)
        logger.warning(f"Оформлен возврат по заказу #{order_id} через Telegram.")
        ctx.show(f"↩️ Возврат по заказу #{esc(order_id)} оформлен.", kb(btn("⬅️ К заказам", "orders")))

    # ================================================================== ответ на отзыв вручную
    def cb_review_reply(self, ctx: Ctx, order_id: str):
        self.ask(ctx, "review_reply", f"💬 Напишите ответ на отзыв к заказу #{esc(order_id)}:", order_id=order_id)

    def st_review_reply(self, message: tg.Message, state: dict):
        if message.content_type != "text":
            self.bot.reply_to(message, "Нужен текст.")
            return
        self.c.account.send_review(state["order_id"], message.text, 5)
        self.states.pop(message.from_user.id, None)
        self.bot.reply_to(message, f"✅ Ответ на отзыв к заказу #{esc(state['order_id'])} опубликован.")
