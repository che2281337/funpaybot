"""
Работа с лотами: список своих лотов (включая неактивные), включение/выключение, редактирование, копирование, удаление.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from FunPayAPI import types
from FunPayAPI.common.enums import SubCategoryTypes

if TYPE_CHECKING:
    from .core import Cardinal

logger = logging.getLogger("FPC.lots")


class LotsManager:
    PROFILE_TTL = 60

    def __init__(self, cardinal: Cardinal):
        self.cardinal = cardinal
        self.lock = threading.RLock()
        self._profile_lots: list[types.LotShortcut] = []
        self._profile_time = 0.0

    @property
    def account(self):
        return self.cardinal.account

    @property
    def cache(self) -> dict:
        return self.cardinal.storage.lots_cache.data

    def save_cache(self):
        self.cardinal.storage.lots_cache.save()

    def profile_lots(self, force: bool = False) -> list[types.LotShortcut]:
        """Активные лоты со страницы профиля (кэш на минуту)."""
        with self.lock:
            if force or time.time() - self._profile_time > self.PROFILE_TTL:
                profile = self.account.get_user(self.account.id)
                self._profile_lots = profile.get_lots()
                self._profile_time = time.time()
                self._update_cache_from_profile(self._profile_lots)
            return self._profile_lots

    def _update_cache_from_profile(self, lots: list[types.LotShortcut]):
        active_ids = set()
        for lot in lots:
            active_ids.add(str(lot.id))
            entry = self.cache.setdefault(str(lot.id), {})
            entry.update({
                "title": lot.description or lot.title or f"Лот {lot.id}",
                "price": lot.price,
                "server": lot.server,
                "subcategory_id": lot.subcategory.id,
                "subcategory_name": lot.subcategory.fullname,
                "currency": lot.subcategory.type is SubCategoryTypes.CURRENCY,
                "category_id": lot.subcategory.category.id,
                "active": True,
            })
        for lot_id, entry in self.cache.items():
            if lot_id not in active_ids:
                entry["active"] = False
        self.save_cache()

    def all_lots(self, deep: bool = False) -> list[dict]:
        """
        Все известные лоты (активные + неактивные).
        :param deep: дополнительно просканировать страницы /lots/<id>/trade, чтобы найти выключенные лоты.
        """
        self.profile_lots(force=deep)
        if deep:
            subcats = {e["subcategory_id"]: e for e in self.cache.values()
                       if e.get("subcategory_id") and not e.get("currency")}
            for subcat_id, sample in subcats.items():
                try:
                    ids = self.account.get_my_subcategory_lot_ids(subcat_id)
                except Exception:
                    logger.debug(f"Не удалось получить лоты подкатегории {subcat_id}", exc_info=True)
                    continue
                for lot_id in ids:
                    entry = self.cache.setdefault(str(lot_id), {})
                    if "title" not in entry:
                        entry.update({
                            "title": f"Лот {lot_id}",
                            "subcategory_id": subcat_id,
                            "subcategory_name": sample.get("subcategory_name", ""),
                            "category_id": sample.get("category_id"),
                            "active": False,
                        })
                time.sleep(0.5)
            self.save_cache()
        result = []
        for lot_id, entry in self.cache.items():
            if entry.get("deleted"):
                continue
            result.append({"id": int(lot_id), **entry})
        result.sort(key=lambda e: (not e.get("active"), e.get("subcategory_name", ""), e.get("title", "")))
        return result

    def get_cached(self, lot_id: int) -> dict:
        return self.cache.get(str(lot_id), {})

    def remember(self, lot_id: int, fields: types.LotFields, **extra):
        entry = self.cache.setdefault(str(lot_id), {})
        entry.update({
            "title": fields.title_ru or entry.get("title") or f"Лот {lot_id}",
            "price": fields.price,
            "active": fields.active,
            **extra,
        })
        if fields.fields.get("node_id"):
            entry.setdefault("subcategory_id", int(fields.fields["node_id"]))
        self.save_cache()

    def get_fields(self, lot_id: int) -> types.LotFields:
        fields = self.account.get_lot_fields(lot_id)
        self.remember(lot_id, fields)
        return fields

    def save(self, fields: types.LotFields):
        self.account.save_lot(fields)
        self.remember(fields.lot_id, fields)
        self._profile_time = 0

    def set_active(self, lot_id: int, active: bool) -> types.LotFields:
        fields = self.account.get_lot_fields(lot_id)
        fields.active = active
        self.save(fields)
        return fields

    def edit(self, lot_id: int, **changes) -> types.LotFields:
        fields = self.account.get_lot_fields(lot_id)
        raw = changes.pop("raw", None)
        for key, value in changes.items():
            setattr(fields, key, value)
        fields.renew_fields()
        if raw:
            fields.edit_fields(raw)
            # синхронизируем свойства с сырыми полями, чтобы renew_fields их не затёр
            fields = types.LotFields(lot_id, fields.fields)
        self.save(fields)
        return fields

    def copy(self, lot_id: int) -> None:
        """Создаёт копию лота (новый лот с теми же полями)."""
        fields = self.account.get_lot_fields(lot_id)
        new_fields = dict(fields.fields)
        new_fields["offer_id"] = "0"
        new_fields.pop("deleted", None)
        self.account.save_lot(types.LotFields(0, new_fields))
        self._profile_time = 0

    def create(self, fields: types.LotFields):
        self.account.save_lot(fields)
        self._profile_time = 0

    def delete(self, lot_id: int):
        self.account.delete_lot(lot_id)
        self.cache.pop(str(lot_id), None)
        self.save_cache()
        self._profile_time = 0

    def find_by_title(self, title: str) -> list[int]:
        """Ищет ID лотов по названию заказа (для автовосстановления)."""
        title = (title or "").lower()
        result = []
        for lot_id, entry in self.cache.items():
            lot_title = (entry.get("title") or "").lower()
            if lot_title and (lot_title in title or title.startswith(lot_title)):
                result.append(int(lot_id))
        return result
