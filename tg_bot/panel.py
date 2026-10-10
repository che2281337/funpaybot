"""
Панель управления автоматизацией: автоподнятие, автоответ, ответы на отзывы, приветствие, автовыдача,
заготовки, уведомления, чёрный список, настройки.
"""
from __future__ import annotations

import io
import logging
import os
import re
import sys
import threading
import time
import uuid

from telebot import types as tg

from cardinal.proxy import FORMATS_HELP, check_proxy, mask, parse_proxy, proxy_type
from cardinal.utils import VARIABLES_HELP, esc

from .base import BACK, Ctx, btn, kb, onoff

logger = logging.getLogger("FPC.tg")

NOTIFY_NAMES = {
    "messages": "Новые сообщения",
    "my_messages": "Мои сообщения (с сайта)",
    "orders": "Новые заказы",
    "order_status": "Подтверждения / возвраты",
    "reviews": "Отзывы",
    "delivery": "Автовыдача",
    "commands": "Все команды автоответа",
    "raise": "Автоподнятие",
}

SETTINGS_NAMES = {
    "eternal_online": "Вечный онлайн",
    "auto_restore": "Автовосстановление лотов после продажи",
    "auto_disable": "Выключать лот, когда товары закончились",
    "blacklist_block_delivery": "ЧС: не выдавать товар",
    "blacklist_block_response": "ЧС: не отвечать (автоответ/приветствие)",
}


class PanelMixin:
    def register_panel(self):
        self.register({
            # автоподнятие
            "rs": self.cb_raise, "rs_t": self.cb_raise_toggle, "rs_now": self.cb_raise_now,
            # автоответ
            "ar": self.cb_ar, "ar_t": self.cb_ar_toggle, "ar_add": self.cb_ar_add, "ar_v": self.cb_ar_view,
            "ar_e": self.cb_ar_edit, "ar_n": self.cb_ar_notify, "ar_d": self.cb_ar_delete,
            # отзывы
            "rv": self.cb_rv, "rv_t": self.cb_rv_toggle, "rv_v": self.cb_rv_view, "rv_s": self.cb_rv_star_toggle,
            "rv_e": self.cb_rv_edit,
            # приветствие и ответ на подтверждение
            "gr": self.cb_gr, "gr_t": self.cb_gr_toggle, "gr_e": self.cb_gr_edit, "gr_cd": self.cb_gr_cooldown,
            "oc_t": self.cb_oc_toggle, "oc_e": self.cb_oc_edit,
            # автовыдача
            "ad": self.cb_ad, "ad_t": self.cb_ad_toggle, "ad_add": self.cb_ad_add, "ad_v": self.cb_ad_view,
            "ad_e": self.cb_ad_edit, "ad_g": self.cb_ad_goods, "ad_f": self.cb_ad_file, "ad_c": self.cb_ad_clear,
            "ad_on": self.cb_ad_on, "ad_m": self.cb_ad_multi, "ad_d": self.cb_ad_delete,
            "ad_dy": self.cb_ad_delete_yes, "ad_k": self.cb_ad_key,
            # заготовки
            "tp": self.cb_tp, "tp_add": self.cb_tp_add, "tp_d": self.cb_tp_delete,
            # уведомления / ЧС / настройки
            "nt": self.cb_nt, "nt_t": self.cb_nt_toggle,
            "bl": self.cb_bl, "bl_add": self.cb_bl_add, "bl_d": self.cb_bl_delete,
            "set": self.cb_set, "set_t": self.cb_set_toggle, "rd": self.cb_runner_delay, "adm": self.cb_admins, "adm_d": self.cb_admin_delete,
            "logs": self.cb_logs, "pwd": self.cb_password, "gk": self.cb_golden_key,
            "ua": self.cb_user_agent,
            "rst": self.cb_restart, "rsty": self.cb_restart_yes,
            # прокси
            "px": self.cb_px, "px_set": self.cb_px_set, "px_ty": self.cb_px_type, "px_chk": self.cb_px_check,
            "px_off": self.cb_px_off, "px_offy": self.cb_px_off_yes,
            "px_force": self.cb_px_force,
        })
        self.register_states({
            "ar_key": self.st_ar_key, "ar_resp": self.st_ar_resp, "ar_edit": self.st_ar_edit,
            "rv_edit": self.st_rv_edit,
            "gr_edit": self.st_gr_edit, "gr_cd": self.st_gr_cooldown, "oc_edit": self.st_oc_edit,
            "ad_key": self.st_ad_key, "ad_resp": self.st_ad_resp, "ad_goods": self.st_ad_goods,
            "ad_edit": self.st_ad_edit, "ad_rekey": self.st_ad_rekey,
            "tp_add": self.st_tp_add, "bl_add": self.st_bl_add,
            "pwd": self.st_password, "gk": self.st_golden_key,
            "ua": self.st_user_agent,
            "px": self.st_px,
        })
        self.pending_proxy: dict[int, str] = {}

    # ------------------------------------------------------------------ helpers
    def _text_only(self, message: tg.Message) -> str | None:
        if message.content_type != "text" or not message.text:
            self.bot.reply_to(message, "Нужен текст. Попробуйте ещё раз или /cancel.")
            return None
        return message.text

    def _done(self, message: tg.Message, text: str, back: str):
        self.states.pop(message.from_user.id, None)
        self.bot.send_message(message.chat.id, text, reply_markup=kb(btn(BACK, back)))

    # ================================================================== автоподнятие
    def cb_raise(self, ctx: Ctx):
        on = self.storage.setting("auto_raise")
        ctx.show(f"⬆️ <b>Автоподнятие лотов</b> — {onoff(on)} {'включено' if on else 'выключено'}\n\n"
                 f"Бот сам поднимает лоты во всех категориях, как только FunPay это разрешает.\n"
                 f"<i>Поднимаются только активные лоты: выключенные FunPay не поднимает и не показывает "
                 f"покупателям. Сначала включите их в «📦 Лоты».</i>\n\n"
                 f"{self.c.raiser.status()}",
                 kb([btn(f"{onoff(on)} {'Выключить' if on else 'Включить'}", "rs_t"),
                     btn("🚀 Поднять сейчас", "rs_now")],
                    [btn("🔄 Обновить", "rs"), btn(BACK, "menu")]))

    def cb_raise_toggle(self, ctx: Ctx):
        self.storage.toggle_setting("auto_raise")
        self.c.raiser.wakeup.set()
        self.cb_raise(ctx)

    def cb_raise_now(self, ctx: Ctx):
        ctx.answer("⏳ Поднимаю...")
        report = self.c.raiser.raise_now()
        ctx.show("🚀 <b>Результат поднятия</b>\n\n" + (esc("\n".join(report)) or "Нет лотов для поднятия."),
                 kb(btn(BACK, "rs")))

    # ================================================================== автоответ
    def _ar_keys(self) -> list[str]:
        return list(self.storage.auto_response.data)

    def cb_ar(self, ctx: Ctx):
        on = self.storage.setting("auto_response")
        keys = self._ar_keys()
        rows = [btn(f"{'🔔' if self.storage.auto_response.data[k].get('notify') else '▫️'} {k[:50]}", f"ar_v:{i}")
                for i, k in enumerate(keys)]
        rows.insert(0, [btn(f"{onoff(on)} {'Выключить' if on else 'Включить'}", "ar_t"), btn("➕ Команда", "ar_add")])
        rows.append(btn(BACK, "menu"))
        ctx.show(f"🤖 <b>Автоответ на команды</b> — {onoff(on)}\n\n"
                 f"Если покупатель напишет команду (например <code>!помощь</code>), бот сразу ответит заданным текстом.\n"
                 f"Несколько вариантов команды — через <code>|</code>, например <code>привет|здравствуйте</code>.\n"
                 f"🔔 — присылать уведомление в Telegram при срабатывании.\n\nКоманд: {len(keys)}", kb(*rows))

    def cb_ar_toggle(self, ctx: Ctx):
        self.storage.toggle_setting("auto_response")
        self.cb_ar(ctx)

    def cb_ar_add(self, ctx: Ctx):
        self.ask(ctx, "ar_key", "➕ Введите команду (или несколько через <code>|</code>):\n"
                                "Например: <code>!гарантия|гарантия</code>")

    def st_ar_key(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        key = text.strip()
        if self.c.find_command(key.split("|")[0]):
            self.bot.reply_to(message, "⚠️ Такая команда уже есть. Введите другую или /cancel.")
            return
        self.set_state(message.from_user.id, "ar_resp", key=key)
        self.bot.send_message(message.chat.id, f"✏️ Теперь введите текст ответа на <code>{esc(key)}</code>.\n\n"
                                               f"{VARIABLES_HELP}")

    def st_ar_resp(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        with self.storage.auto_response.lock:
            self.storage.auto_response.data[state["key"]] = {"response": text, "notify": False}
            self.storage.auto_response.save()
        self._done(message, f"✅ Команда <code>{esc(state['key'])}</code> добавлена.", "ar")

    def _ar_get(self, ctx: Ctx, idx: str) -> str | None:
        keys = self._ar_keys()
        if int(idx) >= len(keys):
            ctx.answer("Команда не найдена", alert=True)
            return None
        return keys[int(idx)]

    def cb_ar_view(self, ctx: Ctx, idx: str):
        if not (key := self._ar_get(ctx, idx)):
            return
        data = self.storage.auto_response.data[key]
        ctx.show(f"🤖 <b>Команда:</b> <code>{esc(key)}</code>\n\n<b>Ответ:</b>\n{esc(data.get('response', ''))}",
                 kb([btn("✏️ Изменить ответ", f"ar_e:{idx}"),
                     btn(f"{'🔔' if data.get('notify') else '🔕'} Уведомлять", f"ar_n:{idx}")],
                    [btn("🗑 Удалить", f"ar_d:{idx}"), btn(BACK, "ar")]))

    def cb_ar_edit(self, ctx: Ctx, idx: str):
        if not (key := self._ar_get(ctx, idx)):
            return
        self.ask(ctx, "ar_edit", f"✏️ Новый текст ответа на <code>{esc(key)}</code>:\n\n{VARIABLES_HELP}", key=key)

    def st_ar_edit(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        with self.storage.auto_response.lock:
            if state["key"] in self.storage.auto_response.data:
                self.storage.auto_response.data[state["key"]]["response"] = text
                self.storage.auto_response.save()
        self._done(message, "✅ Ответ изменён.", "ar")

    def cb_ar_notify(self, ctx: Ctx, idx: str):
        if not (key := self._ar_get(ctx, idx)):
            return
        with self.storage.auto_response.lock:
            data = self.storage.auto_response.data[key]
            data["notify"] = not data.get("notify")
            self.storage.auto_response.save()
        self.cb_ar_view(ctx, idx)

    def cb_ar_delete(self, ctx: Ctx, idx: str):
        if not (key := self._ar_get(ctx, idx)):
            return
        with self.storage.auto_response.lock:
            self.storage.auto_response.data.pop(key, None)
            self.storage.auto_response.save()
        ctx.answer("🗑 Удалено")
        self.cb_ar(ctx)

    # ================================================================== ответы на отзывы
    def cb_rv(self, ctx: Ctx):
        on = self.storage.setting("review_replies")
        data = self.storage.review_replies.data
        rows = [[btn(f"{onoff(on)} {'Выключить' if on else 'Включить'}", "rv_t")]]
        for star in ("5", "4", "3", "2", "1"):
            cfg = data.get(star, {})
            rows.append(btn(f"{onoff(cfg.get('enabled'))} {'⭐' * int(star)}", f"rv_v:{star}"))
        rows.append(btn(BACK, "menu"))
        ctx.show(f"⭐ <b>Автоответ на отзывы</b> — {onoff(on)}\n\nКогда покупатель оставляет отзыв, бот публикует "
                 f"ответ на него. Текст можно задать для каждой оценки отдельно.", kb(*rows))

    def cb_rv_toggle(self, ctx: Ctx):
        self.storage.toggle_setting("review_replies")
        self.cb_rv(ctx)

    def cb_rv_view(self, ctx: Ctx, star: str):
        cfg = self.storage.review_replies.data.setdefault(star, {"enabled": False, "text": ""})
        ctx.show(f"{'⭐' * int(star)} — {onoff(cfg.get('enabled'))}\n\n<b>Текст ответа:</b>\n"
                 f"{esc(cfg.get('text') or '(пусто)')}",
                 kb([btn(f"{onoff(cfg.get('enabled'))} Вкл/выкл", f"rv_s:{star}"),
                     btn("✏️ Изменить текст", f"rv_e:{star}")],
                    btn(BACK, "rv")))

    def cb_rv_star_toggle(self, ctx: Ctx, star: str):
        with self.storage.review_replies.lock:
            cfg = self.storage.review_replies.data.setdefault(star, {"enabled": False, "text": ""})
            cfg["enabled"] = not cfg.get("enabled")
            self.storage.review_replies.save()
        self.cb_rv_view(ctx, star)

    def cb_rv_edit(self, ctx: Ctx, star: str):
        self.ask(ctx, "rv_edit", f"✏️ Текст ответа на отзыв {'⭐' * int(star)}:\n\n{VARIABLES_HELP}", star=star)

    def st_rv_edit(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        with self.storage.review_replies.lock:
            cfg = self.storage.review_replies.data.setdefault(state["star"], {"enabled": True, "text": ""})
            cfg["text"] = text
            self.storage.review_replies.save()
        self._done(message, "✅ Текст ответа на отзыв сохранён.", f"rv_v:{state['star']}")

    # ================================================================== приветствие / ответ на подтверждение
    def cb_gr(self, ctx: Ctx):
        s = self.storage.settings
        ctx.show(f"👋 <b>Приветствие</b> — {onoff(s['greetings'])}\n"
                 f"Отправляется, когда покупатель пишет впервые (или не писал {s['greeting_cooldown_days']} дн.).\n\n"
                 f"<b>Текст:</b>\n{esc(s['greeting_text'])}\n\n"
                 f"✅ <b>Ответ на подтверждение заказа</b> — {onoff(s['order_confirm_reply'])}\n"
                 f"<b>Текст:</b>\n{esc(s['order_confirm_text'])}",
                 kb([btn(f"{onoff(s['greetings'])} Приветствие", "gr_t"), btn("✏️ Текст приветствия", "gr_e")],
                    btn(f"⏳ Задержка повторного приветствия: {s['greeting_cooldown_days']} дн.", "gr_cd"),
                    [btn(f"{onoff(s['order_confirm_reply'])} Ответ на подтв.", "oc_t"),
                     btn("✏️ Текст ответа", "oc_e")],
                    btn(BACK, "menu")))

    def cb_gr_toggle(self, ctx: Ctx):
        self.storage.toggle_setting("greetings")
        self.cb_gr(ctx)

    def cb_gr_edit(self, ctx: Ctx):
        self.ask(ctx, "gr_edit", f"✏️ Новый текст приветствия:\n\n{VARIABLES_HELP}")

    def st_gr_edit(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        self.storage.set_setting("greeting_text", text)
        self._done(message, "✅ Приветствие сохранено.", "gr")

    def cb_gr_cooldown(self, ctx: Ctx):
        self.ask(ctx, "gr_cd", "⏳ Через сколько дней молчания приветствовать покупателя снова? (число, можно 0.5)")

    def st_gr_cooldown(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        try:
            value = float(text.replace(",", "."))
            assert value >= 0
        except (ValueError, AssertionError):
            self.bot.reply_to(message, "Нужно неотрицательное число.")
            return
        self.storage.set_setting("greeting_cooldown_days", value)
        self._done(message, "✅ Сохранено.", "gr")

    def cb_oc_toggle(self, ctx: Ctx):
        self.storage.toggle_setting("order_confirm_reply")
        self.cb_gr(ctx)

    def cb_oc_edit(self, ctx: Ctx):
        self.ask(ctx, "oc_edit", f"✏️ Текст, который бот отправит после подтверждения заказа:\n\n{VARIABLES_HELP}")

    def st_oc_edit(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        self.storage.set_setting("order_confirm_text", text)
        self._done(message, "✅ Сохранено.", "gr")

    # ================================================================== автовыдача
    def _ad_keys(self) -> list[str]:
        return list(self.storage.auto_delivery.data)

    def _ad_get(self, ctx: Ctx, idx: str) -> str | None:
        keys = self._ad_keys()
        if int(idx) >= len(keys):
            ctx.answer("Не найдено", alert=True)
            return None
        return keys[int(idx)]

    def cb_ad(self, ctx: Ctx):
        on = self.storage.setting("auto_delivery")
        rows = [[btn(f"{onoff(on)} {'Выключить' if on else 'Включить'}", "ad_t"), btn("➕ Добавить лот", "ad_add")]]
        for i, key in enumerate(self._ad_keys()):
            cfg = self.storage.auto_delivery.data[key]
            count = len(self.storage.read_goods(cfg["goods_file"])) if cfg.get("goods_file") else "∞"
            rows.append(btn(f"{onoff(cfg.get('enabled', True))} {key[:40]} [{count}]", f"ad_v:{i}"))
        rows.append(btn(BACK, "menu"))
        ctx.show(f"🚚 <b>Автовыдача</b> — {onoff(on)}\n\n"
                 f"После оплаты бот отправляет покупателю товар из файла (по одной строке на штуку).\n"
                 f"Лот определяется по <b>части названия</b> заказа. В скобках — сколько товаров осталось.",
                 kb(*rows))

    def cb_ad_toggle(self, ctx: Ctx):
        self.storage.toggle_setting("auto_delivery")
        self.cb_ad(ctx)

    def cb_ad_add(self, ctx: Ctx, lot_id: str | None = None):
        title = ""
        if lot_id:
            title = self.c.lots.get_cached(int(lot_id)).get("title", "")
        if title:
            self.set_state(ctx.user_id, "ad_resp", key=title)
            ctx.answer()
            ctx.send(f"🚚 Лот: <code>{esc(title)}</code>\n\nВведите текст сообщения с товаром.\n"
                     f"<code>$product</code> заменится на товар из файла.\n"
                     f"Если товар один и тот же (например, инструкция) — просто напишите текст без $product.\n\n"
                     f"{VARIABLES_HELP}\n\n<i>Для отмены — /cancel</i>")
            return
        self.ask(ctx, "ad_key", "🚚 Введите название лота (или его уникальную часть), как оно отображается на "
                                "FunPay:")

    def st_ad_key(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        self.set_state(message.from_user.id, "ad_resp", key=text.strip())
        self.bot.send_message(message.chat.id, "✏️ Введите текст сообщения с товаром. "
                                               "<code>$product</code> заменится на товар из файла.\n\n"
                                               f"Пример: <code>Спасибо за покупку, $username!\nВаш товар:\n$product</code>\n\n"
                                               f"{VARIABLES_HELP}")

    def st_ad_resp(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        key = state["key"]
        cfg = {"response": text, "enabled": True, "multi": True}
        if "$product" in text:
            cfg["goods_file"] = f"{uuid.uuid4().hex[:10]}.txt"
            self.storage.write_goods(cfg["goods_file"], [])
        with self.storage.auto_delivery.lock:
            self.storage.auto_delivery.data[key] = cfg
            self.storage.auto_delivery.save()
        idx = self._ad_keys().index(key)
        self.states.pop(message.from_user.id, None)
        extra = "\n\nТеперь добавьте товары кнопкой «➕ Товары»." if cfg.get("goods_file") else ""
        self.bot.send_message(message.chat.id, f"✅ Автовыдача для <code>{esc(key)}</code> настроена.{extra}",
                              reply_markup=kb(btn("➡️ Открыть", f"ad_v:{idx}")))

    def cb_ad_view(self, ctx: Ctx, idx: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        cfg = self.storage.auto_delivery.data[key]
        goods_line = "товар не из файла (отправляется только текст)"
        if cfg.get("goods_file"):
            goods = self.storage.read_goods(cfg["goods_file"])
            goods_line = f"<b>{len(goods)}</b> шт."
        rows = [[btn(f"{onoff(cfg.get('enabled', True))} Вкл/выкл", f"ad_on:{idx}"),
                 btn("✏️ Текст", f"ad_e:{idx}")]]
        if cfg.get("goods_file"):
            rows.append([btn("➕ Товары", f"ad_g:{idx}"), btn("📄 Скачать товары", f"ad_f:{idx}")])
            rows.append([btn(f"{onoff(cfg.get('multi', True))} Выдавать по кол-ву", f"ad_m:{idx}"),
                         btn("🧹 Очистить товары", f"ad_c:{idx}")])
        rows.append([btn("🏷 Изменить название", f"ad_k:{idx}"), btn("🗑 Удалить", f"ad_d:{idx}")])
        rows.append(btn(BACK, "ad"))
        ctx.show(f"🚚 <b>{esc(key)}</b> — {onoff(cfg.get('enabled', True))}\n\n"
                 f"📦 Товаров: {goods_line}\n"
                 f"🔢 Выдавать по количеству в заказе: {onoff(cfg.get('multi', True))}\n\n"
                 f"<b>Сообщение:</b>\n{esc(cfg.get('response', ''))}", kb(*rows))

    def cb_ad_edit(self, ctx: Ctx, idx: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        self.ask(ctx, "ad_edit", f"✏️ Новый текст сообщения для <code>{esc(key)}</code>:\n\n{VARIABLES_HELP}", key=key)

    def st_ad_edit(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        with self.storage.auto_delivery.lock:
            cfg = self.storage.auto_delivery.data.get(state["key"])
            if cfg is None:
                return self._done(message, "Лот не найден.", "ad")
            cfg["response"] = text
            if "$product" in text and not cfg.get("goods_file"):
                cfg["goods_file"] = f"{uuid.uuid4().hex[:10]}.txt"
                self.storage.write_goods(cfg["goods_file"], [])
            self.storage.auto_delivery.save()
        self._done(message, "✅ Текст сохранён.", f"ad_v:{self._ad_keys().index(state['key'])}")

    def cb_ad_key(self, ctx: Ctx, idx: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        self.ask(ctx, "ad_rekey", f"🏷 Новое название (часть названия) лота вместо <code>{esc(key)}</code>:", key=key)

    def st_ad_rekey(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        with self.storage.auto_delivery.lock:
            data = self.storage.auto_delivery.data
            if state["key"] in data:
                data[text.strip()] = data.pop(state["key"])
                self.storage.auto_delivery.save()
        self._done(message, "✅ Название изменено.", "ad")

    def cb_ad_goods(self, ctx: Ctx, idx: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        self.ask(ctx, "ad_goods", f"➕ Отправьте товары для <code>{esc(key)}</code>:\n"
                                  f"• текстом — <b>каждая строка = 1 товар</b>;\n"
                                  f"• или .txt файлом (тоже по строке на товар).", key=key)

    def st_ad_goods(self, message: tg.Message, state: dict):
        cfg = self.storage.auto_delivery.data.get(state["key"])
        if not cfg or not cfg.get("goods_file"):
            return self._done(message, "Лот не найден.", "ad")
        if message.content_type == "document":
            info = self.bot.get_file(message.document.file_id)
            content = self.bot.download_file(info.file_path).decode("utf-8", errors="ignore")
        elif message.content_type == "text":
            content = message.text
        else:
            self.bot.reply_to(message, "Нужен текст или .txt файл.")
            return
        goods = [line.strip() for line in content.replace("\r", "").split("\n") if line.strip()]
        total = self.storage.add_goods(cfg["goods_file"], goods)
        self._done(message, f"✅ Добавлено товаров: {len(goods)}. Всего: {total}.",
                   f"ad_v:{self._ad_keys().index(state['key'])}")

    def cb_ad_file(self, ctx: Ctx, idx: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        goods = self.storage.read_goods(self.storage.auto_delivery.data[key]["goods_file"])
        if not goods:
            ctx.answer("Товаров нет", alert=True)
            return
        ctx.answer()
        file = io.BytesIO("\n".join(goods).encode("utf-8"))
        file.name = "goods.txt"
        self.bot.send_document(ctx.chat_id, file, caption=f"📦 {esc(key)}: {len(goods)} шт.")

    def cb_ad_clear(self, ctx: Ctx, idx: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        self.storage.write_goods(self.storage.auto_delivery.data[key]["goods_file"], [])
        ctx.answer("🧹 Товары удалены")
        self.cb_ad_view(ctx, idx)

    def _ad_toggle_field(self, ctx: Ctx, idx: str, field: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        with self.storage.auto_delivery.lock:
            cfg = self.storage.auto_delivery.data[key]
            cfg[field] = not cfg.get(field, True)
            self.storage.auto_delivery.save()
        self.cb_ad_view(ctx, idx)

    def cb_ad_on(self, ctx: Ctx, idx: str):
        self._ad_toggle_field(ctx, idx, "enabled")

    def cb_ad_multi(self, ctx: Ctx, idx: str):
        self._ad_toggle_field(ctx, idx, "multi")

    def cb_ad_delete(self, ctx: Ctx, idx: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        ctx.show(f"🗑 Удалить автовыдачу для <code>{esc(key)}</code> вместе с товарами?",
                 kb([btn("✅ Удалить", f"ad_dy:{idx}"), btn("❌ Отмена", f"ad_v:{idx}")]))

    def cb_ad_delete_yes(self, ctx: Ctx, idx: str):
        if not (key := self._ad_get(ctx, idx)):
            return
        with self.storage.auto_delivery.lock:
            cfg = self.storage.auto_delivery.data.pop(key)
            self.storage.auto_delivery.save()
        if cfg.get("goods_file") and os.path.exists(self.storage.goods_path(cfg["goods_file"])):
            os.remove(self.storage.goods_path(cfg["goods_file"]))
        ctx.answer("🗑 Удалено")
        self.cb_ad(ctx)

    # ================================================================== заготовки
    def cb_tp(self, ctx: Ctx):
        templates = self.storage.templates.data
        rows = [btn(f"🗑 {t.replace(chr(10), ' ')[:55]}", f"tp_d:{i}") for i, t in enumerate(templates)]
        rows.insert(0, btn("➕ Добавить заготовку", "tp_add"))
        rows.append(btn(BACK, "menu"))
        lines = [f"{i + 1}. {esc(t)}" for i, t in enumerate(templates)]
        ctx.show("📝 <b>Заготовки ответов</b>\nИх можно отправить покупателю одной кнопкой из уведомления.\n"
                 "Нажмите на заготовку, чтобы удалить её.\n\n" + ("\n".join(lines) or "<i>Пусто</i>"), kb(*rows))

    def cb_tp_add(self, ctx: Ctx):
        self.ask(ctx, "tp_add", f"➕ Введите текст заготовки:\n\n{VARIABLES_HELP}")

    def st_tp_add(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        with self.storage.templates.lock:
            self.storage.templates.data.append(text)
            self.storage.templates.save()
        self._done(message, "✅ Заготовка добавлена.", "tp")

    def cb_tp_delete(self, ctx: Ctx, idx: str):
        with self.storage.templates.lock:
            if int(idx) < len(self.storage.templates.data):
                self.storage.templates.data.pop(int(idx))
                self.storage.templates.save()
        ctx.answer("🗑 Удалено")
        self.cb_tp(ctx)

    # ================================================================== уведомления
    def cb_nt(self, ctx: Ctx):
        rows = [btn(f"{onoff(self.storage.notify_enabled(k))} {name}", f"nt_t:{k}") for k, name in NOTIFY_NAMES.items()]
        rows.append(btn(BACK, "menu"))
        ctx.show("🔔 <b>Уведомления в Telegram</b>\nНажмите, чтобы включить/выключить.", kb(*rows))

    def cb_nt_toggle(self, ctx: Ctx, key: str):
        self.storage.toggle_notify(key)
        self.cb_nt(ctx)

    # ================================================================== чёрный список
    def cb_bl(self, ctx: Ctx):
        bl = self.storage.settings.get("blacklist", [])
        rows = [btn(f"🗑 {name}", f"bl_d:{i}") for i, name in enumerate(bl)]
        rows.insert(0, btn("➕ Добавить", "bl_add"))
        rows.append(btn(BACK, "menu"))
        ctx.show("⛔ <b>Чёрный список</b>\nПользователям из списка бот не выдаёт товар и не отвечает автоматически "
                 "(настраивается в «Настройках»). Нажмите на ник, чтобы удалить.", kb(*rows))

    def cb_bl_add(self, ctx: Ctx):
        self.ask(ctx, "bl_add", "➕ Введите ник пользователя FunPay:")

    def st_bl_add(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        with self.storage.config.lock:
            bl = self.storage.settings.setdefault("blacklist", [])
            if text.strip() not in bl:
                bl.append(text.strip())
            self.storage.config.save()
        self._done(message, f"✅ {esc(text.strip())} добавлен в ЧС.", "bl")

    def cb_bl_delete(self, ctx: Ctx, idx: str):
        with self.storage.config.lock:
            bl = self.storage.settings.setdefault("blacklist", [])
            if int(idx) < len(bl):
                bl.pop(int(idx))
            self.storage.config.save()
        self.cb_bl(ctx)

    # ================================================================== настройки
    def cb_set(self, ctx: Ctx):
        rows = [btn(f"{onoff(self.storage.setting(k))} {name}", f"set_t:{k}") for k, name in SETTINGS_NAMES.items()]
        rows += [[btn("👥 Администраторы", "adm"), btn("📄 Логи", "logs")],
                 [btn("🔑 Сменить пароль", "pwd"), btn("🍪 Сменить golden_key", "gk")],
                 btn("🧭 Сменить User-Agent", "ua"),
                 btn(f"🔁 Проверка FunPay: каждые {self.storage.setting('runner_delay', 6)} сек", "rd"),
                 btn(f"🌐 Прокси (Telegram: {proxy_type(self.storage.get_proxy('telegram'))})", "px"),
                 [btn("🔄 Перезапуск", "rst"), btn(BACK, "menu")]]
        ctx.show("🛠 <b>Настройки</b>\n\n<b>Вечный онлайн</b> — бот постоянно держит аккаунт в сети.\n"
                 "<b>Автовосстановление</b> — если лот выключился после продажи, бот включит его снова.\n"
                 "<b>Выключать лот</b> — когда закончились товары автовыдачи.", kb(*rows))

    def cb_runner_delay(self, ctx: Ctx, value: str | None = None):
        if value:
            self.storage.set_setting("runner_delay", int(value))
            ctx.answer(f"✅ Каждые {value} сек")
            self.cb_set(ctx)
            return
        current = self.storage.setting("runner_delay", 6)
        options = (2, 3, 4, 6, 10)
        ctx.show("🔁 <b>Как часто проверять новые сообщения и заказы на FunPay?</b>\n\n"
                 "Чем чаще, тем быстрее приходят уведомления и срабатывают автоответы. "
                 "Слишком частые запросы (2 сек) могут не понравиться FunPay. Оптимально — 3–4 сек.",
                 kb([btn(("✅ " if v == current else "") + f"{v} сек", f"rd:{v}") for v in options],
                    btn(BACK, "set")))

    def cb_set_toggle(self, ctx: Ctx, key: str):
        if key in SETTINGS_NAMES:
            self.storage.toggle_setting(key)
        self.cb_set(ctx)

    def cb_admins(self, ctx: Ctx):
        rows = []
        for admin in self.storage.admins:
            mark = " (вы)" if admin == ctx.user_id else ""
            rows.append(btn(f"🗑 {admin}{mark}", f"adm_d:{admin}"))
        rows.append(btn(BACK, "set"))
        ctx.show("👥 <b>Администраторы</b>\nЧтобы добавить администратора — пусть напишет боту пароль.\n"
                 "Нажмите на ID, чтобы забрать доступ.", kb(*rows))

    def cb_admin_delete(self, ctx: Ctx, admin_id: str):
        self.storage.remove_admin(int(admin_id))
        ctx.answer("Доступ отозван")
        if int(admin_id) == ctx.user_id:
            ctx.show("🚪 Вы вышли из панели.")
            return
        self.cb_admins(ctx)

    def cb_logs(self, ctx: Ctx):
        path = os.path.join("logs", "log.txt")
        if not os.path.exists(path):
            ctx.answer("Лог-файл не найден", alert=True)
            return
        ctx.answer()
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 2_000_000))
            data = f.read()
        file = io.BytesIO(data)
        file.name = "log.txt"
        self.bot.send_document(ctx.chat_id, file, caption="📄 Последние логи")

    def cb_password(self, ctx: Ctx):
        self.ask(ctx, "pwd", "🔑 Введите новый пароль для входа в панель (минимум 6 символов):")

    def st_password(self, message: tg.Message, state: dict):
        from cardinal.storage import hash_password
        if not (text := self._text_only(message)):
            return
        if len(text) < 6:
            self.bot.reply_to(message, "Слишком короткий пароль.")
            return
        with self.storage.config.lock:
            self.storage.config.data["telegram"]["password_hash"] = hash_password(text)
            self.storage.config.save()
        try:
            self.bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        self._done(message, "✅ Пароль изменён.", "set")

    def cb_user_agent(self, ctx: Ctx):
        current = self.storage.config.data["funpay"].get("user_agent") or "—"
        self.ask(ctx, "ua", "🧭 Отправьте <b>User-Agent</b> браузера, в котором выполнен вход на FunPay "
                            "(именно того, откуда взят golden_key).\n\n"
                            "Как узнать: откройте в этом браузере https://www.whatsmyua.info и скопируйте строку, "
                            "или нажмите F12 → Console и введите <code>navigator.userAgent</code>.\n\n"
                            f"Сейчас: <code>{esc(current)}</code>")

    def st_user_agent(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        ua = text.strip().strip("'\"")
        if not ua.startswith("Mozilla/") or len(ua) < 40:
            self.bot.reply_to(message, "Не похоже на User-Agent — он начинается с <code>Mozilla/5.0</code>. "
                                       "Попробуйте ещё раз или /cancel.")
            return
        with self.storage.config.lock:
            self.storage.config.data["funpay"]["user_agent"] = ua
            self.storage.config.save()
        self.c.account.user_agent = ua
        self.states.pop(message.from_user.id, None)
        try:
            self.c.account.get(update_phpsessid=True)
            result = f"✅ User-Agent сохранён, сессия FunPay обновлена (аккаунт <b>{esc(self.c.account.username)}</b>)."
        except Exception as e:
            result = f"✅ User-Agent сохранён, но обновить сессию не удалось: <code>{esc(e)}</code>"
        self.bot.send_message(message.chat.id, result, reply_markup=kb(btn(BACK, "set")))

    def cb_golden_key(self, ctx: Ctx):
        self.ask(ctx, "gk", "🍪 Отправьте новый <code>golden_key</code> (32 символа). После этого бот перезапустится.")

    def st_golden_key(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        key = text.strip()
        if not re.fullmatch(r"[a-z0-9]{32}", key):
            self.bot.reply_to(message, "Похоже, это не golden_key (нужно 32 символа: строчные латинские буквы и цифры).")
            return
        with self.storage.config.lock:
            self.storage.config.data["funpay"]["golden_key"] = key
            self.storage.config.save()
        try:
            self.bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        self.states.pop(message.from_user.id, None)
        self.bot.send_message(message.chat.id, "✅ golden_key сохранён. Перезапускаюсь...")
        self._restart()

    # ================================================================== прокси
    PX_TARGETS = {"tg": ("telegram", "📱 Telegram"), "fp": ("funpay", "🛒 FunPay")}

    def cb_px(self, ctx: Ctx):
        tg_proxy = self.storage.get_proxy("telegram")
        fp_proxy = self.storage.get_proxy("funpay")
        rows = [[btn("✏️ Прокси для Telegram", "px_set:tg")]
                + ([btn("🗑 Убрать", "px_off:tg")] if tg_proxy else []),
                [btn("✏️ Прокси для FunPay", "px_set:fp")]
                + ([btn("🗑 Убрать", "px_off:fp")] if fp_proxy else []),
                [btn("🧪 Проверить Telegram", "px_chk:tg"), btn("🧪 Проверить FunPay", "px_chk:fp")],
                btn(BACK, "set")]
        tg_line = (f"<code>{esc(mask(tg_proxy))}</code> ({proxy_type(tg_proxy)})" if tg_proxy
                   else "не задан — системное подключение (VPN, если включён)")
        fp_line = f"<code>{esc(mask(fp_proxy))}</code> ({proxy_type(fp_proxy)})" if fp_proxy else "напрямую (без прокси)"
        ctx.show(f"🌐 <b>Прокси</b>\n\n"
                 f"📱 <b>Telegram:</b> {tg_line}\n"
                 f"🛒 <b>FunPay:</b> {fp_line}\n\n"
                 f"Прокси для Telegram нужен, если Telegram заблокирован. Для FunPay прокси обычно не нужен.\n"
                 f"Поддерживаются <b>SOCKS5</b>, HTTP и SOCKS4. Изменения применяются сразу, без перезапуска.",
                 kb(*rows))

    def cb_px_set(self, ctx: Ctx, target: str):
        name = self.PX_TARGETS[target][1]
        ctx.show(f"🌐 <b>Прокси для {name}</b> — выберите тип\n\n<i>SOCKS5h — то же, что SOCKS5, но DNS-запросы "
                 f"тоже идут через прокси.</i>",
                 kb([btn("SOCKS5", f"px_ty:{target}:socks5"), btn("SOCKS5h", f"px_ty:{target}:socks5h")],
                    [btn("HTTP", f"px_ty:{target}:http"), btn("SOCKS4", f"px_ty:{target}:socks4")],
                    btn(BACK, "px")))

    def cb_px_type(self, ctx: Ctx, target: str, scheme: str):
        name = self.PX_TARGETS[target][1]
        self.ask(ctx, "px", f"✏️ Отправьте прокси <b>{scheme.upper()}</b> для <b>{name}</b>.\n"
                            f"Если в строке указан другой тип (например <code>http://</code>), будет использован он.\n\n"
                            f"{FORMATS_HELP}", scheme=scheme, target=target)

    def _px_report(self, proxy: str, res: dict) -> str:
        lines = [f"🧪 <b>Проверка</b> <code>{esc(mask(proxy) if proxy else 'без прокси')}</code>\n"]
        if res["ip"]:
            lines.append(f"🌍 Внешний IP: <code>{esc(res['ip'])}</code> ({res['ping']} мс)")
        else:
            lines.append("🌍 Внешний IP: ❌ не удалось определить")
        lines.append(f"{'✅' if res['telegram'] else '❌'} Telegram")
        lines.append(f"{'✅' if res['funpay'] else '❌'} FunPay")
        if res["error"]:
            lines.append(f"\n⚠️ {esc(res['error'])}")
        return "\n".join(lines)

    def st_px(self, message: tg.Message, state: dict):
        if not (text := self._text_only(message)):
            return
        target = state["target"]
        try:
            proxy = parse_proxy(text, state.get("scheme", "socks5"))
        except ValueError as e:
            self.bot.reply_to(message, f"❌ {esc(e)}\n\n{FORMATS_HELP}")
            return
        try:
            self.bot.delete_message(message.chat.id, message.message_id)  # в сообщении может быть пароль
        except Exception:
            pass
        self.states.pop(message.from_user.id, None)
        wait = self.bot.send_message(message.chat.id, "⏳ Проверяю прокси (до 30 сек)...")
        res = check_proxy(proxy)
        report = self._px_report(proxy, res)
        try:
            self.bot.delete_message(message.chat.id, wait.message_id)
        except Exception:
            pass
        retry = kb([btn("✏️ Ввести другой", f"px_set:{target}"), btn(BACK, "px")])
        if target == "tg" and not res["telegram"]:
            # без связи с Telegram бота нельзя будет настроить — такой прокси не сохраняем
            self.bot.send_message(message.chat.id, report + "\n\n❗ Через этот прокси Telegram недоступен. "
                                                            "Прокси <b>не сохранён</b>, иначе бот потеряет связь.",
                                  reply_markup=retry)
            return
        if target == "fp" and not res["funpay"]:
            self.pending_proxy[message.from_user.id] = proxy
            self.bot.send_message(message.chat.id, report + "\n\n❗ Через этот прокси FunPay недоступен. "
                                                            "Прокси <b>не сохранён</b>.",
                                  reply_markup=kb([btn("💾 Сохранить всё равно", "px_force"),
                                                   btn("✏️ Ввести другой", "px_set:fp")], btn(BACK, "px")))
            return
        self.c.set_proxy(self.PX_TARGETS[target][0], proxy)
        self.bot.send_message(message.chat.id, report + f"\n\n✅ Прокси для {self.PX_TARGETS[target][1]} "
                                                        f"сохранён и применён.",
                              reply_markup=kb(btn("➡️ К настройкам прокси", "px")))

    def cb_px_force(self, ctx: Ctx):
        proxy = self.pending_proxy.pop(ctx.user_id, None)
        if not proxy:
            ctx.answer("Нечего сохранять — введите прокси заново.", alert=True)
            return
        self.c.set_proxy("funpay", proxy)
        ctx.answer("💾 Сохранено")
        self.cb_px(ctx)

    def cb_px_check(self, ctx: Ctx, target: str):
        ctx.answer("⏳ Проверяю...")
        proxy = self.storage.get_proxy(self.PX_TARGETS[target][0])
        if proxy:
            res = check_proxy(proxy)
        else:
            res = check_proxy("", use_system=target == "tg")
        title = f"{self.PX_TARGETS[target][1]}: " + ("через прокси" if proxy else
                                                     "системное подключение" if target == "tg" else "напрямую")
        ctx.show(f"<b>{title}</b>\n" + self._px_report(proxy, res),
                 kb([btn("🔄 Ещё раз", f"px_chk:{target}"), btn(BACK, "px")]))

    def cb_px_off(self, ctx: Ctx, target: str):
        warn = ("\n\n⚠️ Если Telegram у вас заблокирован, без прокси бот потеряет связь. "
                "Бот сначала проверит, доступен ли Telegram без прокси." if target == "tg" else "")
        ctx.show(f"🗑 Убрать прокси для {self.PX_TARGETS[target][1]}?{warn}",
                 kb([btn("✅ Убрать", f"px_offy:{target}"), btn("❌ Отмена", "px")]))

    def cb_px_off_yes(self, ctx: Ctx, target: str):
        if target == "tg":
            ctx.answer("⏳ Проверяю Telegram без прокси...")
            if not check_proxy("", use_system=True)["telegram"]:
                ctx.show("❌ Без прокси Telegram недоступен — прокси оставлен, иначе бот потеряет связь.",
                         kb(btn(BACK, "px")))
                return
        self.c.set_proxy(self.PX_TARGETS[target][0], "")
        ctx.answer("Прокси убран")
        self.cb_px(ctx)

    def cb_restart(self, ctx: Ctx):
        ctx.show("🔄 Перезапустить бота?", kb([btn("✅ Да", "rsty"), btn("❌ Нет", "set")]))

    def cb_restart_yes(self, ctx: Ctx):
        ctx.show("🔄 Перезапускаюсь...")
        self._restart()

    def _restart(self):
        def do():
            time.sleep(1)
            logger.warning("Перезапуск по команде из Telegram.")
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=do, daemon=True).start()
