"""
Автоподнятие лотов.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from FunPayAPI.common import exceptions
from FunPayAPI.common.enums import SubCategoryTypes

from .utils import esc, human_time

if TYPE_CHECKING:
    from .core import Cardinal

logger = logging.getLogger("FPC.raise")


class Raiser:
    def __init__(self, cardinal: Cardinal):
        self.cardinal = cardinal
        self.next_raise: dict[int, float] = {}
        self.category_names: dict[int, str] = {}
        self.last_result: dict[int, str] = {}
        self.nodes: dict[int, list[int]] = {}
        self.wakeup = threading.Event()

    def categories(self) -> dict[int, str]:
        """Категории (игры), в которых есть активные лоты, которые можно поднять: {ID игры: название}."""
        groups: dict[int, dict] = {}
        for lot in self.cardinal.lots.profile_lots(force=True):
            subcat = lot.subcategory
            if subcat is None or subcat.type is not SubCategoryTypes.COMMON:
                continue
            group = groups.setdefault(subcat.category.id, {"name": subcat.category.name, "nodes": []})
            if subcat.id not in group["nodes"]:
                group["nodes"].append(subcat.id)
        self.nodes = {gid: g["nodes"] for gid, g in groups.items()}
        return {gid: g["name"] for gid, g in groups.items()}

    def raise_category(self, category_id: int) -> tuple[bool, int]:
        """Пытается поднять лоты категории. Возвращает (успех, сколько ждать до следующей попытки)."""
        name = self.category_names.get(category_id, str(category_id))
        try:
            wait = self.cardinal.account.raise_lots(category_id, subcategories=self.nodes.get(category_id))
        except exceptions.RaiseError as e:
            wait = e.wait_time or 60
            self.last_result[category_id] = f"⏳ {e.error_message or 'Подождите'}"
            logger.info(f"Категория «{name}»: {e.error_message or 'ещё рано'}. Следующая попытка через {human_time(wait)}.")
            return False, wait
        except Exception as e:
            self.last_result[category_id] = f"❌ Ошибка: {e}"
            logger.warning(f"Не удалось поднять лоты категории «{name}»: {e}")
            logger.debug("TRACEBACK", exc_info=True)
            return False, 120
        self.last_result[category_id] = f"✅ Поднято в {time.strftime('%H:%M')}"
        logger.info(f"Лоты категории «{name}» подняты!")
        if self.cardinal.storage.notify_enabled("raise"):
            self.cardinal.notify(f"⬆️ Лоты категории <b>{esc(name)}</b> подняты.", kind="raise")
        # FunPay обычно сразу сообщает, через сколько можно поднимать снова
        return True, max(60, int(wait)) if wait else 300

    def raise_now(self) -> list[str]:
        """Принудительно попробовать поднять все категории (из Telegram)."""
        report = []
        self.category_names = self.categories()
        for cid, name in self.category_names.items():
            ok, wait = self.raise_category(cid)
            self.next_raise[cid] = time.time() + wait
            report.append(f"{'✅' if ok else '⏳'} {name}: {'поднято' if ok else 'через ' + human_time(wait)}")
            time.sleep(1)
        return report

    def loop(self):
        last_categories_update = 0.0
        while True:
            try:
                if not self.cardinal.storage.setting("auto_raise"):
                    self.wakeup.wait(15)
                    self.wakeup.clear()
                    continue
                if time.time() - last_categories_update > 600 or not self.category_names:
                    self.category_names = self.categories()
                    last_categories_update = time.time()
                for cid in list(self.category_names):
                    if self.next_raise.get(cid, 0) > time.time():
                        continue
                    ok, wait = self.raise_category(cid)
                    self.next_raise[cid] = time.time() + wait
                    time.sleep(1)
                upcoming = [t for cid, t in self.next_raise.items() if cid in self.category_names]
                sleep_for = min(upcoming) - time.time() if upcoming else 300
                sleep_for = max(5, min(sleep_for, 300))
            except Exception:
                logger.exception("Ошибка в цикле автоподнятия")
                sleep_for = 60
            self.wakeup.wait(sleep_for)
            self.wakeup.clear()

    def status(self) -> str:
        if not self.category_names:
            return "Нет категорий с активными лотами (или данные ещё не загружены)."
        lines = []
        for cid, name in self.category_names.items():
            nxt = self.next_raise.get(cid)
            left = f"след. попытка через {human_time(nxt - time.time())}" if nxt and nxt > time.time() else "скоро"
            lines.append(f"• <b>{esc(name)}</b> — {esc(self.last_result.get(cid, '—'))} ({left})")
        return "\n".join(lines)
