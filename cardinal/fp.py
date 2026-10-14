"""
Надстройка над FunPayAPI из FunPay Cardinal. Саму библиотеку (папка FunPayAPI) не меняем,
чтобы её можно было обновлять простым копированием из свежей версии Cardinal.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from bs4 import BeautifulSoup

import FunPayAPI.updater.runner as runner_module
from FunPayAPI import Account, types
from FunPayAPI.common.enums import SubCategoryTypes

logger = logging.getLogger("FPC.fp")


# ---------------------------------------------------------------------- аккаунт
def configure_account(account: Account) -> Account:
    """
    - Системный прокси Windows / переменные окружения (их выставляют VPN-программы) для FunPay не используются —
      только прокси из настроек бота.
    - Замеряем время запросов к runner/ (пинг FunPay для статуса).
    """
    account.session.trust_env = False
    account.last_runner_ms = None
    original = account.runner_request

    def timed_runner_request(payload: dict):
        started = time.time()
        try:
            return original(payload)
        finally:
            account.last_runner_ms = round((time.time() - started) * 1000)

    account.runner_request = timed_runner_request
    return account


# ---------------------------------------------------------------------- интервал опроса
class _DynamicSleepClock:
    """
    Подменяет модуль time внутри FunPayAPI.updater.runner, чтобы интервал опроса FunPay можно было менять
    на лету (быстрый режим во время переписки), не меняя саму библиотеку.

    Runner.listen() спит `requests_delay - время_итерации`. Мы передаём в него огромное число SENTINEL,
    а здесь пересчитываем: настоящая пауза = delay_fn() - время_итерации.
    """
    SENTINEL = 10_000.0

    def __init__(self, delay_fn: Callable[[], float]):
        self.delay_fn = delay_fn

    def sleep(self, seconds: float):
        if seconds >= self.SENTINEL / 2:
            spent = self.SENTINEL - seconds
            seconds = max(0.0, self.delay_fn() - spent)
        time.sleep(seconds)

    def __getattr__(self, name):
        return getattr(time, name)


def install_dynamic_delay(delay_fn: Callable[[], float]) -> float:
    """Возвращает значение, которое нужно передать в Runner.listen(requests_delay=...)."""
    runner_module.time = _DynamicSleepClock(delay_fn)
    return _DynamicSleepClock.SENTINEL


# ---------------------------------------------------------------------- создание лота
def _lot_form(html: str):
    bs = BeautifulSoup(html, "lxml")
    form = bs.find("form", class_="form-offer-editor")
    if form is None:
        offer_input = bs.find("input", {"name": "offer_id"})
        form = offer_input.find_parent("form") if offer_input else None
    if form is None:
        lead = bs.find("p", class_="lead")
        raise Exception("Форма лота не найдена на странице FunPay" + (f": {lead.text.strip()}" if lead else ""))
    return form


def _in_hidden_group(field) -> bool:
    group = field.find_parent(class_="form-group")
    return bool(group and "hidden" in group.get("class", []))


def parse_lot_form(html: str) -> dict:
    """Поля формы лота (как их отправил бы браузер)."""
    form = _lot_form(html)
    result = {}
    for field in form.find_all("input"):
        name = field.get("name")
        if not name or field.get("type") in ("checkbox", "radio", "submit", "button"):
            continue
        result[name] = field.get("value") or ""
    for field in form.find_all("input", {"type": ["checkbox", "radio"]}, checked=True):
        if field.get("name"):
            result[field["name"]] = "on" if field.get("type") == "checkbox" else (field.get("value") or "on")
    for field in form.find_all("textarea"):
        if field.get("name"):
            result[field["name"]] = field.text or ""
    for field in form.find_all("select"):
        if not field.get("name") or _in_hidden_group(field):
            continue
        option = field.find("option", selected=True) or field.find("option")
        result[field["name"]] = option.get("value", "") if option else ""
    return result


def parse_lot_form_selects(html: str) -> dict[str, dict]:
    """Выпадающие списки формы: {name: {"label": str, "options": [(value, text), ...]}}."""
    form = _lot_form(html)
    result = {}
    for field in form.find_all("select"):
        name = field.get("name")
        if not name or _in_hidden_group(field):
            continue
        label = ""
        group = field.find_parent(class_="form-group")
        if group and (lbl := group.find("label")):
            label = lbl.text.strip()
        options = [(o.get("value", ""), o.text.strip()) for o in field.find_all("option") if o.get("value")]
        result[name] = {"label": label or name, "options": options}
    return result


def new_lot_form(account: Account, node_id: int) -> tuple[types.LotFields, dict[str, dict]]:
    """Форма создания нового лота в разделе node_id: (поля, описание выпадающих списков)."""
    response = account.method("get", f"lots/offerEdit?node={node_id}", {}, {}, raise_not_200=True)
    html = response.content.decode()
    fields = parse_lot_form(html)
    fields["offer_id"] = "0"
    fields["node_id"] = str(node_id)
    if fields.get("csrf_token"):
        account.csrf_token = fields["csrf_token"]
    subcategory = account.get_subcategory(SubCategoryTypes.COMMON, node_id)
    return types.LotFields(0, fields, subcategory), parse_lot_form_selects(html)
