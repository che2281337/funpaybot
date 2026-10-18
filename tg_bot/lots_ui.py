"""
Управление лотами из Telegram: список, карточка лота, вкл/выкл, редактирование, копирование, удаление,
копирование.
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
            "lot_ad": self.cb_lot_autodelivery, "lot_open": self.cb_lot_open,
        })
        self.register_states({
            "lot_edit": self.st_lot_edit, "lot_open": self.st_lot_open,
        })

    # ================================================================== список
    def cb_lots(self, ctx: Ctx, page: str = "0", deep: bool = False):
        ctx.answer("⏳ Загружаю лоты...")
        lots = self.c.lots.all_lots(deep=deep)
        page_i = max(0, int(page))
        pages = max(1, (len(lots) + PAGE_SIZE - 1) // PAGE_SIZE)
        page_i = min(page_i, pages - 1)
        rows = [btn("🔎 Открыть по ID", "lot_open")]
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
