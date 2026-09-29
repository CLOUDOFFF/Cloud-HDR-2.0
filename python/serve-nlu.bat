@echo off
chcp 65001 >nul
title Cloud HDR AI - понимание команд
cd /d "%~dp0"

rem  ---------------------------------------------------------------------------
rem  Сервис разбирает команды: textcat решает, что за команда, ner размечает её
rem  цель. Модель лежит рядом, в models\nlu, и в интернет не ходит — переменные
rem  ниже выключают проверку обновлений у huggingface, которую тянет за собой
rem  часть зависимостей.
rem  ---------------------------------------------------------------------------
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
set HF_HUB_DISABLE_TELEMETRY=1
set PYTHONIOENCODING=utf-8

rem  Своя копия Python из пакета «всё в одном» — главнее системной: см. serve.bat.
set "PY=%~dp0..\runtime\python.exe"
if not exist "%PY%" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

if not exist "models\nlu\model-best" (
  echo.
  echo   [!] Модель понимания команд не обучена.
  echo       Из папки python\ выполните:
  echo.
  echo         python -m cloudhdr_nlu train
  echo.
  echo   Интерфейс будет работать и без неё — разбор останется в браузере.
  echo.
  pause
  exit /b 1
)

"%PY%" -m cloudhdr_nlu serve --host 127.0.0.1 --port 4478

echo.
echo   Сервис понимания команд остановлен.
pause
