"""
Параллельная обработка событий: события разных чатов обрабатываются одновременно,
события одного чата — строго по очереди (чтобы приветствие не обогнало автоответ и т.п.).
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Hashable

logger = logging.getLogger("FPC.dispatch")


class KeyedExecutor:
    def __init__(self, max_workers: int = 8, name: str = "events"):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=name)
        self._lock = threading.Lock()
        self._queues: dict[Hashable, deque] = {}

    def submit(self, key: Hashable, func: Callable, *args: Any):
        with self._lock:
            queue = self._queues.get(key)
            if queue is not None:
                queue.append((func, args))  # для этого ключа уже идёт обработка — встанем в очередь
                return
            self._queues[key] = deque([(func, args)])
        self._pool.submit(self._drain, key)

    def _drain(self, key: Hashable):
        while True:
            with self._lock:
                queue = self._queues[key]
                if not queue:
                    del self._queues[key]
                    return
                func, args = queue.popleft()
            try:
                func(*args)
            except Exception:
                logger.exception("Ошибка при обработке события")

    def shutdown(self, wait: bool = True):
        self._pool.shutdown(wait=wait)
