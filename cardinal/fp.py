"""
Надстройка над FunPayAPI из FunPay Cardinal. Саму библиотеку (папка FunPayAPI) не меняем,
чтобы её можно было обновлять простым копированием из свежей версии Cardinal.
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import FunPayAPI.updater.runner as runner_module
from FunPayAPI import Account

logger = logging.getLogger("FPC.fp")


# ---------------------------------------------------------------------- аккаунт
CONNECT_TIMEOUT = 5
READ_TIMEOUT = 20


def configure_account(account: Account, use_system_proxy: bool = False) -> Account:
    """
    - Системный прокси Windows / переменные окружения (их выставляют VPN-программы) для FunPay по умолчанию
      не используются — только прокси из настроек бота (или use_system_proxy=True).
    - Быстрое обнаружение обрыва: подключение ждём 5 сек (а не 15) и повторяем не 6 раз, а 3.
      POST-запросы после таймаута чтения не повторяем — иначе сообщение покупателю может уйти дважды.
    - Замеряем время запросов к runner/ (пинг FunPay для статуса).
    """
    account.session.trust_env = use_system_proxy
    account.requests_timeout = (CONNECT_TIMEOUT, READ_TIMEOUT)
    retry = Retry(total=4, connect=3, read=0, redirect=6, status=3, backoff_factor=0.5,
                  status_forcelist=[500, 502, 503, 504], allowed_methods={"GET", "POST"})
    account.session.mount("https://", HTTPAdapter(max_retries=retry))
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
