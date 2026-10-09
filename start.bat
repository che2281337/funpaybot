@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem Ищем настоящий Python: сначала лаунчер py, потом python (заглушка из Microsoft Store не подходит)
set PY=
py -3 --version >nul 2>&1 && set PY=py -3
if not defined PY (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set PY=python
)
if not defined PY (
    echo.
    echo [!] Python 3.10+ не найден.
    echo     1. Скачайте Python с https://www.python.org/downloads/
    echo     2. При установке поставьте галочку "Add python.exe to PATH"
    echo     3. Если всё равно не работает: Параметры Windows - Приложения -
    echo        Дополнительные параметры - Псевдонимы выполнения приложений -
    echo        выключите "python.exe" и "python3.exe" ^(App Installer^)
    echo.
    pause
    exit /b 1
)

if not exist venv\Scripts\python.exe (
    echo Создаю виртуальное окружение...
    %PY% -m venv venv || (echo Не удалось создать venv & pause & exit /b 1)
    venv\Scripts\python -m pip install --upgrade pip
    venv\Scripts\python -m pip install -r requirements.txt || (echo Не удалось установить зависимости & pause & exit /b 1)
)
:loop
venv\Scripts\python main.py
echo Бот остановился. Перезапуск через 5 секунд... (Ctrl+C - выход)
timeout /t 5 >nul
goto loop
