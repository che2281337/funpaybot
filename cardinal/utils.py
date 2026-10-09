from __future__ import annotations

import html
import re
from datetime import datetime
from typing import Any

VARIABLES_HELP = (
    "<b>Переменные:</b>\n"
    "<code>$username</code> — ник покупателя/собеседника\n"
    "<code>$chat_name</code> — название чата\n"
    "<code>$order_id</code> — ID заказа (#ABCD1234)\n"
    "<code>$order_title</code> — название заказа\n"
    "<code>$order_link</code> — ссылка на заказ\n"
    "<code>$product</code> — выданный товар (автовыдача)\n"
    "<code>$date</code>, <code>$time</code> — текущие дата и время\n"
    "<code>$message_text</code> — текст сообщения покупателя"
)


def format_text(text: str, **values: Any) -> str:
    """Подставляет переменные вида $name в текст."""
    now = datetime.now()
    values.setdefault("date", now.strftime("%d.%m.%Y"))
    values.setdefault("time", now.strftime("%H:%M"))
    if values.get("order_id") and "order_link" not in values:
        oid = str(values["order_id"]).lstrip("#")
        values["order_link"] = f"https://funpay.com/orders/{oid}/"
    if values.get("order_id"):
        values["order_id"] = "#" + str(values["order_id"]).lstrip("#")
    # длинные имена заменяем первыми, чтобы $order_id не сломал $order_id_x и т.п.
    for key in sorted(values, key=len, reverse=True):
        if values[key] is None:
            continue
        text = text.replace(f"${key}", str(values[key]))
    return text


def esc(text: Any) -> str:
    return html.escape(str(text)) if text is not None else ""


def normalize_command(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def chat_link(chat_id: int | str) -> str:
    return f"https://funpay.com/chat/?node={chat_id}"


def order_link(order_id: str) -> str:
    return f"https://funpay.com/orders/{order_id.lstrip('#')}/"


def lot_edit_link(lot_id: int) -> str:
    return f"https://funpay.com/lots/offerEdit?offer={lot_id}"


def split_text(text: str, limit: int = 4000) -> list[str]:
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


def human_time(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} ч {m} мин"
    if m:
        return f"{m} мин {s} сек"
    return f"{s} сек"
