@echo off
chcp 65001 >nul
title Cloud HDR AI - модель
cd /d "%~dp0"

rem  ---------------------------------------------------------------------------
rem  Полностью локальный режим. Без этих переменных transformers при каждом
rem  запуске ходит на huggingface.co проверять обновления — и без интернета
rem  запуск подвисает на таймауте вместо того, чтобы взять файлы из папки рядом.
rem  ---------------------------------------------------------------------------
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set HF_HUB_DISABLE_TELEMETRY=1

rem  ---------------------------------------------------------------------------
rem  Первым делом смотрим на свою копию Python рядом с программой: в пакете
rem  «всё в одном» она лежит в runtime\ вместе с torch, и у человека, который
rem  просто скачал архив, никакого системного Python может не быть вовсе.
rem  ---------------------------------------------------------------------------
set "PY=%~dp0..\runtime\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -m cloudhdr_ai serve --host 127.0.0.1 --port 8080

echo.
echo   Модель остановлена.
pause
