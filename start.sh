#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -d venv ]; then
    echo "Создаю виртуальное окружение..."
    python3 -m venv venv
    venv/bin/pip install --upgrade pip
    venv/bin/pip install -r requirements.txt
fi
while true; do
    venv/bin/python main.py
    echo "Бот остановился. Перезапуск через 5 секунд... (Ctrl+C — выход)"
    sleep 5
done
