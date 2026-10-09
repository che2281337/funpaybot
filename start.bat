@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist venv (
    echo Создаю виртуальное окружение...
    python -m venv venv
    venv\Scripts\python -m pip install --upgrade pip
    venv\Scripts\python -m pip install -r requirements.txt
)
:loop
venv\Scripts\python main.py
echo Бот остановился. Перезапуск через 5 секунд... (Ctrl+C - выход)
timeout /t 5 >nul
goto loop
