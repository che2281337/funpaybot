"""
Управление лотами из Telegram: список, карточка лота, вкл/выкл, редактирование, копирование, удаление,
пошаговое создание нового лота.
"""
from __future__ import annotations

import logging
import re

from telebot import types as tg

from cardinal.utils import esc, lot_edit_link

from .base import BACK, Ctx, btn, kb, onoff, url_btn

logger = logging.getLogger("FPC.tg")

PAGE_SIZE = 8
EDITABLE = {
    "price": ("💰 цену", "Введите новую цену (за 1 шт.), например <code>149.9</code>:"),
    "amount": ("🔢 количество", "Введите количество товара (целое число) или <code>-</code>, чтобы убрать:"),
    "title": ("✏️ название", "Введите новое название (краткое описание) лота:"),
    "desc": ("📝 описание", "Введите новое подробное описание лота:"),
    "raw": ("🔧 поля", "Введите поля в формате <code>имя=значение</code>, по одному на строку.\n"
                      "Например: <code>fields[method]=Подарком</code>\n"
                      "Список текущих полей можно посмотреть кнопкой «🔍 Все поля»."),
}


def parse_price(text: str) -> float:
    value = float(text.replace(",", ".").replace("₽", "").strip())
    if value <= 0:
        raise ValueError
    return value


class LotsMixin:
    def register_lots(self):
        self.register({
            "lots": self.cb_lots, "lots_r": self.cb_lots_refresh, "lot": self.cb_lot, "lot_t": self.cb_lot_toggle,
            "lot_e": self.cb_lot_edit, "lot_f": self.cb_lot_fields, "lot_c": self.cb_lot_copy,
            "lot_cy": self.cb_lot_copy_yes, "lot_d": self.cb_lot_delete, "lot_dy": self.cb_lot_delete_yes,
            "lot_ad": self.cb_lot_autodelivery, "lot_new": self.cb_lot_new, "lot_open": self.cb_lot_open,
            "nls": self.cb_new_lot_select, "nlskip": self.cb_new_lot_skip, "nlc": self.cb_new_lot_create,
        })
        self.register_states({
            "lot_edit": self.st_lot_edit, "lot_open": self.st_lot_open,
            "nl_node": self.st_nl_node, "nl_title": self.st_nl_title, "nl_desc": self.st_nl_desc,
            "nl_price": self.st_nl_price, "nl_amount": self.st_nl_amount, "nl_select": self.st_nl_select,
        })

    # ================================================================== список
    def cb_lots(self, ctx: Ctx, page: str = "0", deep: bool = False):
        ctx.answer("⏳ Загружаю лоты...")
        lots = self.c.lots.all_lots(deep=deep)
        page_i = max(0, int(page))
        pages = max(1, (len(lots) + PAGE_SIZE - 1) // PAGE_SIZE)
        page_i = min(page_i, pages - 1)
        rows = [[btn("➕ Новый лот", "lot_new"), btn("🔎 Открыть по ID", "lot_open")]]
        for lot in lots[page_i * PAGE_SIZE:(page_i + 1) * PAGE_SIZE]:
            price = f" · {lot['price']}₽" if lot.get("price") is not None else ""
            rows.append(btn(f"{onoff(lot.get('active'))} {lot.get('title', '')[:40]}{price}", f"lot:{lot['id']}"))
        nav = []
        if page_i > 0:
            nav.append(btn("◀️", f"lots:{page_i - 1}"))
        nav.append(btn(f"{page_i + 1}/{pages}", "noop"))
        if page_i < pages - 1:
            nav.append(btn("▶️", f"lots:{page_i + 1}"))
        rows.append(nav)
        rows.append([btn("🔄 Обновить (+ выключенные)", "lots_r"), btn(BACK, "menu")])
        active = sum(1 for lot in lots if lot.get("active"))
        ctx.show(f"📦 <b>Мои лоты</b>: {len(lots)} (🟢 активных: {active})\n\n"
                 f"<i>Выключенные лоты FunPay не показывает в профиле — нажмите «Обновить», чтобы бот поискал их "
                 f"в ваших разделах.</i>", kb(*rows))

    def cb_lots_refresh(self, ctx: Ctx):
        self.cb_lots(ctx, "0", deep=True)

    def cb_lot_open(self, ctx: Ctx):
        self.ask(ctx, "lot_open", "🔎 Отправьте ID лота или ссылку на него\n"
                                  "(например <code>https://funpay.com/lots/offer?id=12345678</code>):")

    def st_lot_open(self, message: tg.Message, state: dict):
        if message.content_type != "text" or not (m := re.search(r"(\d{4,})\s*$", message.text.strip())):
            self.bot.reply_to(message, "Не нашёл ID лота. Попробуйте ещё раз или /cancel.")
            return
        self.states.pop(message.from_user.id, None)
        self.cb_lot(self.ctx_from_message(message), m.group(1))

    # ================================================================== карточка лота
    def cb_lot(self, ctx: Ctx, lot_id: str):
        ctx.answer("⏳ Загружаю лот...")
        lot_id_i = int(lot_id)
        f = self.c.lots.get_fields(lot_id_i)
        cached = self.c.lots.get_cached(lot_id_i)
        desc = f.description_ru or ""
        if len(desc) > 700:
            desc = desc[:700] + "…"
        text = (f"{onoff(f.active)} <b>{esc(f.title_ru or cached.get('title') or 'Без названия')}</b>\n"
                f"🆔 <code>{lot_id_i}</code> · {esc(cached.get('subcategory_name', ''))}\n\n"
                f"💰 Цена: <b>{f.price if f.price is not None else '—'} ₽</b>\n"
                f"🔢 Количество: {f.amount if f.amount is not None else '—'}\n"
                f"🔁 Деактивация после продажи: {onoff(f.deactivate_after_sale)}\n\n"
                f"📝 {esc(desc) or '<i>нет описания</i>'}")
        has_delivery = any(k.lower() in (f.title_ru or "").lower() for k in self.storage.auto_delivery.data)
        ctx.show(text, kb(
            btn(f"{'🔴 Выключить' if f.active else '🟢 Включить'}", f"lot_t:{lot_id}"),
            [btn("💰 Цена", f"lot_e:{lot_id}:price"), btn("🔢 Кол-во", f"lot_e:{lot_id}:amount")],
            [btn("✏️ Название", f"lot_e:{lot_id}:title"), btn("📝 Описание", f"lot_e:{lot_id}:desc")],
            [btn("🔧 Изменить поля", f"lot_e:{lot_id}:raw"), btn("🔍 Все поля", f"lot_f:{lot_id}")],
            [btn("📋 Копировать", f"lot_c:{lot_id}"), btn("🗑 Удалить", f"lot_d:{lot_id}")],
            btn(f"🚚 Автовыдача: {'настроена ✅' if has_delivery else 'настроить'}", f"lot_ad:{lot_id}"),
            [url_btn("🌐 На FunPay", lot_edit_link(lot_id_i)), btn("⬅️ К лотам", "lots:0")],
        ))

    def cb_lot_toggle(self, ctx: Ctx, lot_id: str):
        fields = self.c.lots.get_fields(int(lot_id))
        self.c.lots.set_active(int(lot_id), not fields.active)
        ctx.answer("🟢 Лот включён" if not fields.active else "🔴 Лот выключен")
        self.cb_lot(ctx, lot_id)

    def cb_lot_fields(self, ctx: Ctx, lot_id: str):
        fields = self.c.lots.get_fields(int(lot_id)).fields
        skip = {"csrf_token", "fields[desc][ru]", "fields[desc][en]"}
        lines = [f"<code>{esc(k)}</code> = {esc(str(v)[:80])}" for k, v in fields.items() if k not in skip]
        ctx.send(f"🔍 <b>Поля лота {lot_id}</b>\n\n" + "\n".join(lines),
                 kb(btn("❌ Закрыть", "close")))

    def cb_lot_edit(self, ctx: Ctx, lot_id: str, field: str):
        name, prompt = EDITABLE[field]
        self.ask(ctx, "lot_edit", f"Меняем {name} лота <code>{lot_id}</code>.\n{prompt}", lot_id=int(lot_id), field=field)

    def st_lot_edit(self, message: tg.Message, state: dict):
        if message.content_type != "text":
            self.bot.reply_to(message, "Нужен текст.")
            return
        text = message.text
        field, lot_id = state["field"], state["lot_id"]
        try:
            if field == "price":
                self.c.lots.edit(lot_id, price=parse_price(text))
            elif field == "amount":
                self.c.lots.edit(lot_id, amount=None if text.strip() == "-" else int(text.strip()))
            elif field == "title":
                self.c.lots.edit(lot_id, title_ru=text.strip())
            elif field == "desc":
                self.c.lots.edit(lot_id, description_ru=text)
            elif field == "raw":
                raw = {}
                for line in text.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        raw[k.strip()] = v.strip()
                if not raw:
                    self.bot.reply_to(message, "Не нашёл ни одной строки вида имя=значение.")
                    return
                self.c.lots.edit(lot_id, raw=raw)
        except ValueError:
            self.bot.reply_to(message, "❌ Неверное значение, попробуйте ещё раз или /cancel.")
            return
        self.states.pop(message.from_user.id, None)
        self.bot.send_message(message.chat.id, "✅ Лот сохранён на FunPay.",
                              reply_markup=kb(btn("➡️ Открыть лот", f"lot:{lot_id}")))

    def cb_lot_copy(self, ctx: Ctx, lot_id: str):
        ctx.show(f"📋 Создать копию лота <code>{lot_id}</code>? Будет создан новый лот с такими же полями.",
                 kb([btn("✅ Создать копию", f"lot_cy:{lot_id}"), btn("❌ Отмена", f"lot:{lot_id}")]))

    def cb_lot_copy_yes(self, ctx: Ctx, lot_id: str):
        self.c.lots.copy(int(lot_id))
        ctx.answer("✅ Копия создана", alert=True)
        self.cb_lots(ctx, "0", deep=True)

    def cb_lot_delete(self, ctx: Ctx, lot_id: str):
        ctx.show(f"🗑 <b>Удалить</b> лот <code>{lot_id}</code> с FunPay? Это действие необратимо.",
                 kb([btn("✅ Удалить", f"lot_dy:{lot_id}"), btn("❌ Отмена", f"lot:{lot_id}")]))

    def cb_lot_delete_yes(self, ctx: Ctx, lot_id: str):
        self.c.lots.delete(int(lot_id))
        ctx.answer("🗑 Лот удалён", alert=True)
        self.cb_lots(ctx, "0")

    def cb_lot_autodelivery(self, ctx: Ctx, lot_id: str):
        title = self.c.lots.get_cached(int(lot_id)).get("title", "")
        keys = list(self.storage.auto_delivery.data)
        for i, key in enumerate(keys):
            if key.lower() in title.lower():
                self.cb_ad_view(ctx, str(i))
                return
        self.cb_ad_add(ctx, lot_id)

    # ================================================================== новый лот
    def cb_lot_new(self, ctx: Ctx):
        self.ask(ctx, "nl_node", "➕ <b>Новый лот</b>\n\nОтправьте ссылку на раздел FunPay, в котором нужно создать лот,\n"
                                 "например <code>https://funpay.com/lots/1142/</code> (или просто число 1142).\n\n"
                                 "💡 Быстрее всего — скопировать уже существующий похожий лот кнопкой «📋 Копировать» "
                                 "в карточке лота.")

    def st_nl_node(self, message: tg.Message, state: dict):
        if message.content_type != "text" or not (m := re.search(r"(\d+)/?(?:trade)?/?\s*$", message.text.strip())):
            self.bot.reply_to(message, "Не нашёл ID раздела. Пример: https://funpay.com/lots/1142/")
            return
        node_id = int(m.group(1))
        fields, selects = self.c.account.get_new_lot_form(node_id)
        select_list = [(name, data["label"], data["options"]) for name, data in selects.items()
                       if len(data["options"]) > 1]
        self.set_state(message.from_user.id, "nl_title", node_id=node_id, fields=fields, selects=select_list,
                       sel_idx=0)
        self.bot.send_message(message.chat.id, f"✅ Раздел {node_id} найден.\n\n✏️ Введите <b>название</b> лота "
                                               f"(краткое описание):")

    def st_nl_title(self, message: tg.Message, state: dict):
        if message.content_type != "text":
            return
        state["fields"].title_ru = message.text.strip()
        state["name"] = "nl_desc"
        self.bot.send_message(message.chat.id, "📝 Введите <b>подробное описание</b> лота "
                                               "(или <code>-</code>, чтобы оставить пустым):")

    def st_nl_desc(self, message: tg.Message, state: dict):
        if message.content_type != "text":
            return
        state["fields"].description_ru = "" if message.text.strip() == "-" else message.text
        state["name"] = "nl_price"
        self.bot.send_message(message.chat.id, "💰 Введите <b>цену</b> за 1 шт. в рублях:")

    def st_nl_price(self, message: tg.Message, state: dict):
        if message.content_type != "text":
            return
        try:
            state["fields"].price = parse_price(message.text)
        except ValueError:
            self.bot.reply_to(message, "Нужна цена числом, например 99 или 149.5")
            return
        state["name"] = "nl_amount"
        self.bot.send_message(message.chat.id, "🔢 Введите <b>количество</b> товара (или <code>-</code>, если не нужно):")

    def st_nl_amount(self, message: tg.Message, state: dict):
        if message.content_type != "text":
            return
        text = message.text.strip()
        if text != "-":
            try:
                state["fields"].amount = int(text)
            except ValueError:
                self.bot.reply_to(message, "Нужно целое число или -")
                return
        state["name"] = "nl_select"
        self._nl_next_select(message.chat.id, state)

    def _nl_next_select(self, chat_id: int, state: dict, ctx: Ctx | None = None):
        selects = state["selects"]
        if state["sel_idx"] >= len(selects):
            self._nl_confirm(chat_id, state, ctx)
            return
        name, label, options = selects[state["sel_idx"]]
        current = state["fields"].fields.get(name, "")
        rows = [btn(("✅ " if value == current else "") + text[:40], f"nls:{i}")
                for i, (value, text) in enumerate(options[:60])]
        rows.append(btn("⏭ Оставить как есть", "nlskip"))
        text = (f"🔘 <b>{esc(label)}</b> ({state['sel_idx'] + 1}/{len(selects)})\nВыберите вариант"
                + (" или напишите его текстом:" if len(options) > 60 else ":"))
        if ctx:
            ctx.show(text, kb(*rows))
        else:
            self.bot.send_message(chat_id, text, reply_markup=kb(*rows))

    def _nl_state(self, ctx: Ctx) -> dict | None:
        state = self.states.get(ctx.user_id)
        if not state or not state["name"].startswith("nl_"):
            ctx.answer("Создание лота уже завершено или отменено.", alert=True)
            return None
        return state

    def cb_new_lot_select(self, ctx: Ctx, idx: str):
        if not (state := self._nl_state(ctx)):
            return
        name, label, options = state["selects"][state["sel_idx"]]
        state["fields"].edit_fields({name: options[int(idx)][0]})
        state["sel_idx"] += 1
        self._nl_next_select(ctx.chat_id, state, ctx)

    def cb_new_lot_skip(self, ctx: Ctx):
        if not (state := self._nl_state(ctx)):
            return
        state["sel_idx"] += 1
        self._nl_next_select(ctx.chat_id, state, ctx)

    def st_nl_select(self, message: tg.Message, state: dict):
        if message.content_type != "text" or state["sel_idx"] >= len(state["selects"]):
            return
        name, label, options = state["selects"][state["sel_idx"]]
        text = message.text.strip().lower()
        for value, option_text in options:
            if text in (value.lower(), option_text.lower()):
                state["fields"].edit_fields({name: value})
                state["sel_idx"] += 1
                self._nl_next_select(message.chat.id, state)
                return
        self.bot.reply_to(message, "Такого варианта нет. Выберите кнопкой или введите точно.")

    def _nl_confirm(self, chat_id: int, state: dict, ctx: Ctx | None = None):
        f = state["fields"]
        option_names = {}
        for name, label, options in state["selects"]:
            value = f.fields.get(name)
            option_names[label] = next((t for v, t in options if v == value), value)
        extra = "\n".join(f"• {esc(k)}: {esc(v)}" for k, v in option_names.items())
        text = (f"🆕 <b>Проверьте лот</b>\n\n"
                f"🗂 Раздел: {state['node_id']}\n"
                f"✏️ {esc(f.title_ru)}\n"
                f"💰 {f.price} ₽ · 🔢 {f.amount if f.amount is not None else '—'}\n"
                f"{extra}\n\n📝 {esc((f.description_ru or '')[:500])}")
        markup = kb([btn("✅ Создать лот", "nlc"), btn("❌ Отмена", "menu")])
        if ctx:
            ctx.show(text, markup)
        else:
            self.bot.send_message(chat_id, text, reply_markup=markup)

    def cb_new_lot_create(self, ctx: Ctx):
        if not (state := self._nl_state(ctx)):
            return
        f = state["fields"]
        f.active = True
        if not f.title_en:
            f.title_en = f.title_ru
        if not f.description_en:
            f.description_en = f.description_ru
        self.c.lots.create(f)
        self.states.pop(ctx.user_id, None)
        ctx.answer("✅ Лот создан!", alert=True)
        logger.info(f"Создан новый лот «{f.title_ru}» в разделе {state['node_id']} через Telegram.")
        self.cb_lots(ctx, "0", deep=True)
