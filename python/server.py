"""
Cloud HDR Agent — Python-версия локального агента управления Windows.

Полный аналог server/server.js: тот же контракт API, тот же фронтенд,
та же модель безопасности (белый список приложений, песочница папок,
сессионный токен, rate limit, аудит).

    pip install -r requirements.txt
    python server.py        →  http://127.0.0.1:4477
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloudhdr_safety import APP_ORIGINS, APP_PORT, caller_allowed  # noqa: E402

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT.parent / "public"
CONFIG_PATH = ROOT.parent / "server" / "config.json"
VERSION = "1.0.0"

CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
PIN: Optional[str] = f"{secrets.randbelow(900000) + 100000}" if CONFIG.get("requirePin") else None

# отсоединяем дочерние процессы, чтобы агент не держал запущенные приложения
DETACHED = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0
NOWINDOW = 0x08000000 if sys.platform == "win32" else 0


# ----------------------------------------------------------------- утилиты ---

def expand(value: str) -> str:
    """Разворачивает %VAR% в путях Windows."""
    return os.path.expandvars(str(value))


def log(message: str) -> None:
    line = f"[{datetime.now().isoformat(timespec='seconds')}] {message}"
    print(line, flush=True)
    logging = CONFIG.get("logging") or {}
    if not logging.get("enabled"):
        return
    path = ROOT.parent / "server" / logging.get("file", "logs/actions.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def detach(args: list[str], cwd: Optional[str] = None, hidden: bool = True) -> Optional[int]:
    flags = DETACHED | (NOWINDOW if hidden else 0)
    process = subprocess.Popen(  # noqa: S603 — аргументы формируются только из белого списка
        args,
        cwd=cwd,
        creationflags=flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    return process.pid


# ------------------------------------------------- реестр приложений и папок --

APPS: dict[str, dict[str, Any]] = {
    "roblox": {
        "title": "Roblox", "icon": "game",
        "aliases": ["роблокс", "роблокc", "рублокс", "roblox"],
        "dynamic": "roblox", "uri": "roblox://", "image": "RobloxPlayerBeta.exe",
    },
    "discord": {
        "title": "Discord", "icon": "chat",
        "aliases": ["дискорд", "дискорт", "дс", "discord"],
        "paths": ["%LOCALAPPDATA%/Discord/Update.exe"],
        "args": ["--processStart", "Discord.exe"],
        "uri": "discord://", "image": "Discord.exe",
    },
    "chrome": {
        "title": "Google Chrome", "icon": "globe",
        "aliases": ["хром", "chrome", "гугл хром", "браузер"],
        "paths": [
            "%ProgramFiles%/Google/Chrome/Application/chrome.exe",
            "%ProgramFiles(x86)%/Google/Chrome/Application/chrome.exe",
            "%LOCALAPPDATA%/Google/Chrome/Application/chrome.exe",
        ],
        "shell": "chrome", "image": "chrome.exe",
    },
    "edge": {
        "title": "Microsoft Edge", "icon": "globe",
        "aliases": ["эдж", "edge"],
        "paths": [
            "%ProgramFiles(x86)%/Microsoft/Edge/Application/msedge.exe",
            "%ProgramFiles%/Microsoft/Edge/Application/msedge.exe",
        ],
        "shell": "msedge", "image": "msedge.exe",
    },
    "firefox": {
        "title": "Mozilla Firefox", "icon": "globe",
        "aliases": ["фаерфокс", "firefox", "мозилла"],
        "paths": ["%ProgramFiles%/Mozilla Firefox/firefox.exe", "%ProgramFiles(x86)%/Mozilla Firefox/firefox.exe"],
        "shell": "firefox", "image": "firefox.exe",
    },
    "steam": {
        "title": "Steam", "icon": "game",
        "aliases": ["стим", "steam"],
        "paths": ["%ProgramFiles(x86)%/Steam/steam.exe", "%ProgramFiles%/Steam/steam.exe"],
        "uri": "steam://open/main", "image": "steam.exe",
    },
    "telegram": {
        "title": "Telegram", "icon": "chat",
        "aliases": ["телеграм", "телега", "тг", "telegram"],
        "paths": ["%APPDATA%/Telegram Desktop/Telegram.exe", "%LOCALAPPDATA%/Programs/Telegram Desktop/Telegram.exe"],
        "uri": "tg://", "image": "Telegram.exe",
    },
    "spotify": {
        "title": "Spotify", "icon": "music",
        "aliases": ["спотифай", "spotify"],
        "paths": ["%APPDATA%/Spotify/Spotify.exe"],
        "uri": "spotify:", "image": "Spotify.exe",
    },
    "vscode": {
        "title": "Visual Studio Code", "icon": "code",
        "aliases": ["вскод", "vscode", "vs code", "код"],
        "paths": ["%LOCALAPPDATA%/Programs/Microsoft VS Code/Code.exe", "%ProgramFiles%/Microsoft VS Code/Code.exe"],
        "shell": "code", "image": "Code.exe",
    },
    "explorer": {
        "title": "Проводник", "icon": "folder",
        "aliases": ["проводник", "explorer", "файлы"],
        "paths": ["%WINDIR%/explorer.exe"], "image": None,
    },
    "notepad": {
        "title": "Блокнот", "icon": "note",
        "aliases": ["блокнот", "notepad"],
        "paths": ["%WINDIR%/system32/notepad.exe"], "shell": "notepad", "image": "notepad.exe",
    },
    "calc": {
        "title": "Калькулятор", "icon": "calc",
        "aliases": ["калькулятор", "calc"],
        "shell": "calc", "uri": "calculator://", "image": "CalculatorApp.exe",
    },
    "terminal": {
        "title": "Терминал", "icon": "code",
        "aliases": ["терминал", "консоль", "cmd", "powershell"],
        "paths": ["%LOCALAPPDATA%/Microsoft/WindowsApps/wt.exe", "%WINDIR%/system32/cmd.exe"], "image": None,
    },
    "settings": {
        "title": "Параметры Windows", "icon": "gear",
        "aliases": ["настройки", "параметры", "settings"],
        "uri": "ms-settings:", "image": None,
    },
    "taskmgr": {
        "title": "Диспетчер задач", "icon": "gear",
        "aliases": ["диспетчер задач", "taskmgr"],
        "paths": ["%WINDIR%/system32/Taskmgr.exe"], "image": None,
    },
    "paint": {
        "title": "Paint", "icon": "brush",
        "aliases": ["пейнт", "paint"],
        "paths": ["%WINDIR%/system32/mspaint.exe"], "shell": "mspaint", "image": "mspaint.exe",
    },
    "obs": {
        "title": "OBS Studio", "icon": "video",
        "aliases": ["обс", "obs"],
        "paths": ["%ProgramFiles%/obs-studio/bin/64bit/obs64.exe"], "image": "obs64.exe",
    },
    "epicgames": {
        "title": "Epic Games Launcher", "icon": "game",
        "aliases": ["эпик", "epic", "epic games"],
        "paths": ["%ProgramFiles(x86)%/Epic Games/Launcher/Portal/Binaries/Win64/EpicGamesLauncher.exe"],
        "uri": "com.epicgames.launcher://", "image": "EpicGamesLauncher.exe",
    },
}

FOLDERS: dict[str, dict[str, Any]] = {
    "desktop": {"title": "Рабочий стол", "aliases": ["рабочий стол", "десктоп", "desktop"], "path": "%USERPROFILE%/Desktop"},
    "downloads": {"title": "Загрузки", "aliases": ["загрузки", "загрузок", "downloads"], "path": "%USERPROFILE%/Downloads"},
    "documents": {"title": "Документы", "aliases": ["документы", "documents"], "path": "%USERPROFILE%/Documents"},
    "pictures": {"title": "Изображения", "aliases": ["изображения", "картинки", "pictures"], "path": "%USERPROFILE%/Pictures"},
    "music": {"title": "Музыка", "aliases": ["музыка", "music"], "path": "%USERPROFILE%/Music"},
    "videos": {"title": "Видео", "aliases": ["видео", "videos"], "path": "%USERPROFILE%/Videos"},
    "home": {"title": "Папка пользователя", "aliases": ["профиль", "home"], "path": "%USERPROFILE%"},
}


def resolve_roblox() -> Optional[str]:
    """Roblox лежит в %LOCALAPPDATA%\\Roblox\\Versions\\version-*\\RobloxPlayerBeta.exe."""
    base = Path(expand("%LOCALAPPDATA%/Roblox/Versions"))
    if not base.is_dir():
        return None
    candidates = [p for p in base.glob("*/RobloxPlayerBeta.exe") if p.is_file()]
    if not candidates:
        return None
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def resolve_executable(app: dict[str, Any]) -> Optional[str]:
    if app.get("dynamic") == "roblox":
        return resolve_roblox()
    for candidate in app.get("paths", []):
        path = Path(expand(candidate))
        if path.is_file():
            return str(path)
    return None


def find_app_key(raw: str) -> Optional[str]:
    needle = (raw or "").strip().lower()
    if needle in APPS:
        return needle
    for key, app in APPS.items():
        if needle == app["title"].lower() or needle in app.get("aliases", []):
            return key
    return None


def find_folder_key(raw: str) -> Optional[str]:
    needle = re.sub(r"^папк[аиуе]\s+", "", (raw or "").strip().lower())
    if needle in FOLDERS:
        return needle
    for key, folder in FOLDERS.items():
        if needle in folder.get("aliases", []):
            return key
    return None


# ----------------------------------------------------------- безопасность ----

class ExecError(HTTPException):
    def __init__(self, message: str, code: int = 400) -> None:
        super().__init__(status_code=code, detail=message)


def open_uri(uri: str) -> None:
    scheme = str(uri).split(":", 1)[0].lower()
    allowed = CONFIG.get("allowedUriSchemes", [])
    if not re.fullmatch(r"[a-z][a-z0-9+.-]{0,20}", scheme) or scheme not in allowed:
        raise ExecError(f'Протокол "{scheme}:" не разрешён', 403)
    if re.search(r"[\r\n\x00]", uri):
        raise ExecError("Некорректный URI")
    detach(["cmd.exe", "/c", "start", "", uri])


def powershell(script: str) -> None:
    detach(["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script])


def safe_folder(raw_path: str) -> Path:
    """Нормализует путь и проверяет вхождение в allowedFolderRoots."""
    if not raw_path:
        raise ExecError("Не указан путь к папке")
    if re.search(r'[\r\n\x00*?"<>|]', raw_path):
        raise ExecError("Недопустимые символы в пути")

    candidate = expand(raw_path.strip().strip("\"'")).replace("/", "\\")
    if candidate.startswith("\\\\"):
        raise ExecError("Сетевые пути (UNC) запрещены", 403)
    if not re.match(r"^[a-zA-Z]:\\", candidate):
        candidate = str(Path(expand("%USERPROFILE%")) / candidate)

    resolved = Path(candidate).resolve()
    roots = [Path(expand(root)).resolve() for root in CONFIG.get("allowedFolderRoots", [])]
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ExecError("Папка вне разрешённых каталогов. Добавьте её в allowedFolderRoots.", 403)

    forbidden = [Path(expand("%WINDIR%")).resolve(), Path(expand("%ProgramFiles%")).resolve()]
    if any(bad == resolved or bad in resolved.parents for bad in forbidden):
        raise ExecError("Системные каталоги открывать запрещено", 403)
    if not resolved.exists():
        raise ExecError(f"Папка не найдена: {resolved}", 404)
    if not resolved.is_dir():
        raise ExecError("Указанный путь — не папка")
    return resolved


def permission(name: str) -> None:
    if not CONFIG.get("permissions", {}).get(name):
        raise ExecError(f"Право «{name}» отключено в config.json", 403)


# ------------------------------------------------------------- действия ------

def _launch_steam_game(game: dict[str, Any]) -> dict[str, Any]:
    """Запуск игры из библиотеки — через appid, а не по exe."""
    import steam_games

    uri = steam_games.run_uri(game)
    open_uri(uri)
    return {"app": f"steam:{game['appid']}", "title": game["name"], "method": "steam", "detail": uri}


def act_open_app(target: str, via: str = "") -> dict[str, Any]:
    import steam_games

    permission("allowApps")

    # «Запусти В СТИМЕ кс 2» — Steam назван прямо, и искать надо только среди
    # игр. Без этой ветки запрос ушёл бы в общий разбор, где по слову «кс» может
    # найтись что-то посторонее: пользователь получил бы не то, что просил,
    # вместо внятного «такой игры в библиотеке нет».
    if via == "steam":
        found = steam_games.find(target)
        if not found["best"]:
            hint = ""
            if found["suggestions"]:
                hint = " В библиотеке есть: " + ", ".join(found["suggestions"]) + "."
            raise ExecError(f'Не нашёл «{target}» в библиотеке Steam.{hint}', 404)
        return _launch_steam_game(found["best"])

    key = find_app_key(target)
    if not key:
        # Игра из библиотеки Steam проверяется до отказа: запуск через steam://
        # единственный, при котором работают античит, облачные сохранения и
        # оверлей.
        game = steam_games.find(target)
        if game["best"]:
            return _launch_steam_game(game["best"])

        hint = ""
        if game["suggestions"]:
            hint = " В библиотеке Steam есть: " + ", ".join(game["suggestions"]) + "."
        raise ExecError(f'Приложение "{target}" не входит в белый список.{hint}', 404)

    app = APPS[key]
    exe = resolve_executable(app)
    if exe:
        pid = detach([exe, *app.get("args", [])], cwd=str(Path(exe).parent), hidden=False)
        return {"app": key, "title": app["title"], "method": "exe", "detail": exe, "pid": pid}
    if app.get("uri"):
        open_uri(app["uri"])
        return {"app": key, "title": app["title"], "method": "uri", "detail": app["uri"]}
    if app.get("shell"):
        detach(["cmd.exe", "/c", "start", "", app["shell"]])
        return {"app": key, "title": app["title"], "method": "shell", "detail": app["shell"]}
    raise ExecError(f'{app["title"]} не найден на этом компьютере', 404)


def act_open_folder(target: str) -> dict[str, Any]:
    permission("allowFolders")
    key = find_folder_key(target)
    raw = FOLDERS[key]["path"] if key else target
    resolved = safe_folder(raw)
    detach(["explorer.exe", str(resolved)], hidden=False)
    return {"method": "explorer", "title": FOLDERS[key]["title"] if key else resolved.name, "detail": str(resolved)}


def act_open_url(target: str) -> dict[str, Any]:
    permission("allowWeb")
    url = (target or "").strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url.lstrip("/")
    open_uri(url)
    return {"method": "browser", "title": re.sub(r"^https?://", "", url), "detail": url}


def act_web_search(query: str) -> dict[str, Any]:
    permission("allowWeb")
    from urllib.parse import quote

    text = (query or "").strip()
    if not text:
        raise ExecError("Пустой поисковый запрос")
    if len(text) > 300:
        raise ExecError("Слишком длинный запрос")
    url = CONFIG.get("searchEngine", "https://www.google.com/search?q=") + quote(text)
    open_uri(url)
    return {"method": "search", "title": text, "detail": url}


def act_close_app(target: str) -> dict[str, Any]:
    permission("allowClose")
    key = find_app_key(target)
    if not key:
        raise ExecError(f'Приложение "{target}" не входит в белый список', 404)
    app = APPS[key]
    if not app.get("image"):
        raise ExecError(f'{app["title"]} закрывать нельзя — это системный компонент', 403)
    detach(["taskkill.exe", "/IM", app["image"], "/T"])
    return {"app": key, "method": "taskkill", "title": app["title"], "detail": app["image"]}


VOLUME_KEYS = {"volume_up": 175, "volume_down": 174, "volume_mute": 173}


def _audio_endpoint():
    """
    Регулятор громкости Windows через Core Audio.

    Клавиши мультимедиа умеют только «на шаг вверх» и «на шаг вниз»: ни узнать
    текущий уровень, ни поставить нужный ими нельзя, и «поставь громкость на 30»
    превращалось в нажатия вслепую. comtypes даёт доступ к IAudioEndpointVolume,
    где уровень задаётся долей от нуля до единицы.
    """
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    speakers = AudioUtilities.GetSpeakers()
    interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return interface.QueryInterface(IAudioEndpointVolume)


def act_volume(intent: str, amount: Any, level: Any = None) -> dict[str, Any]:
    permission("allowSystem")

    try:
        endpoint = _audio_endpoint()
    except Exception:
        # pycaw не установлен или устройство недоступно — откатываемся к
        # клавишам. Точный уровень так не выставить, о чём и сообщаем.
        endpoint = None

    if intent == "volume_set":
        if level is None:
            raise ExecError("Не понял, какую громкость поставить. Например: «поставь громкость на 30».", 400)
        if endpoint is None:
            raise ExecError("Точная громкость недоступна: установите pycaw (pip install pycaw).", 501)
        target = min(100, max(0, int(level)))
        was = int(round(endpoint.GetMasterVolumeLevelScalar() * 100))
        endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
        if target > 0 and endpoint.GetMute():
            endpoint.SetMute(0, None)
        return {"method": "audio", "title": f"Громкость {target}%", "level": target,
                "detail": f"было {was}% → стало {target}%"}

    if intent == "volume_mute":
        if endpoint is not None:
            muted = not endpoint.GetMute()
            endpoint.SetMute(1 if muted else 0, None)
            return {"method": "audio", "title": "Звук выключен" if muted else "Звук включён",
                    "detail": "без звука" if muted else "звук вернулся"}
        powershell(f"$w = New-Object -ComObject WScript.Shell; $w.SendKeys([char]{VOLUME_KEYS[intent]})")
        return {"method": "volume", "title": "Звук переключён", "detail": "SendKeys 173"}

    # Шаг задаётся В ПРОЦЕНТАХ, а не числом нажатий клавиши.
    step = min(100, max(1, int(amount or 10)))
    label = "Громкость увеличена" if intent == "volume_up" else "Громкость уменьшена"

    if endpoint is not None:
        was = int(round(endpoint.GetMasterVolumeLevelScalar() * 100))
        target = min(100, max(0, was + step if intent == "volume_up" else was - step))
        endpoint.SetMasterVolumeLevelScalar(target / 100.0, None)
        if intent == "volume_up" and endpoint.GetMute():
            endpoint.SetMute(0, None)
        return {"method": "audio", "title": label, "level": target, "detail": f"{was}% → {target}%"}

    times = min(max(step // 2, 1), 10)
    code = VOLUME_KEYS[intent]
    powershell(f"$w = New-Object -ComObject WScript.Shell; 1..{times} | ForEach-Object {{ $w.SendKeys([char]{code}) }}")
    return {"method": "volume", "title": label, "detail": f"нажатий клавиши: {times}"}


def act_screenshot() -> dict[str, Any]:
    permission("allowSystem")
    directory = Path(expand(CONFIG.get("screenshotDir", "%USERPROFILE%\\Pictures\\CloudHDR")))
    directory.mkdir(parents=True, exist_ok=True)
    file = directory / f"cloudhdr-{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    escaped = str(file).replace("'", "''")
    powershell(
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$b = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
        "$img = New-Object System.Drawing.Bitmap $b.Width, $b.Height; "
        "$g = [System.Drawing.Graphics]::FromImage($img); "
        "$g.CopyFromScreen($b.Left, $b.Top, 0, 0, $img.Size); "
        f"$img.Save('{escaped}', [System.Drawing.Imaging.ImageFormat]::Png); "
        "$g.Dispose(); $img.Dispose();"
    )
    return {"method": "screenshot", "title": "Скриншот сохранён", "detail": str(file)}


def act_lock() -> dict[str, Any]:
    permission("allowSystem")
    detach(["rundll32.exe", "user32.dll,LockWorkStation"])
    return {"method": "lock", "title": "Рабочая станция заблокирована", "detail": "user32.dll,LockWorkStation"}


# ------------------------------------------------------------ запись экрана --
#
# Пишет экран встроенный в Windows Game Bar — тот же, что по Win+Alt+R. Выбран он
# не от бедности: ffmpeg пришлось бы устанавливать, а Game Bar есть в системе,
# умеет звук и не требует прав администратора. Файл ложится в «Видео\Captures».
#
# У Game Bar нет ни командной строки, ни COM-интерфейса — только горячая клавиша,
# и клавишу Win нельзя послать обычным SendKeys. Поэтому нажатие идёт через
# keybd_event из user32.
#
# Клавиша ничего не возвращает: при выключенной записи или защищённом от захвата
# окне Game Bar молча ничего не сделает, а агент отчитался бы об успехе. Поэтому
# исход ПРОВЕРЯЕТСЯ по появлению файла в папке записей.

_RECORDING: dict[str, Any] = {"active": False, "file": None, "started_at": None}


def _capture_dir() -> Path:
    return Path(os.environ.get("USERPROFILE", "C:\\")) / "Videos" / "Captures"


def _capture_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files = [p for p in directory.glob("*.mp4") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _send_gamebar_chord() -> None:
    import ctypes

    user32 = ctypes.windll.user32
    win, alt, key, up = 0x5B, 0x12, 0x52, 0x0002
    for code in (win, alt, key):
        user32.keybd_event(code, 0, 0, 0)
    time.sleep(0.07)
    for code in (key, alt, win):
        user32.keybd_event(code, 0, up, 0)


def _gamebar_enabled() -> bool:
    # Отказываем только при ЯВНОМ выключении: когда параметра нет, запись в
    # Windows включена по умолчанию, и придумывать отказ на этом основании
    # значило бы не давать работать там, где всё в порядке.
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\GameDVR") as key:
            value = winreg.QueryValueEx(key, "AppCaptureEnabled")[0]
        return int(value) != 0
    except OSError:
        return True


def act_record_start() -> dict[str, Any]:
    permission("allowSystem")
    if not _gamebar_enabled():
        raise ExecError(
            "Запись экрана выключена в параметрах Windows. Откройте «Параметры → Игры → Записи» "
            "и включите запись происходящего, затем повторите команду.", 409)

    directory = _capture_dir()
    directory.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in _capture_files(directory)}

    _send_gamebar_chord()

    # Game Bar создаёт файл сразу, но не мгновенно: ему надо поднять оверлей.
    # Пятнадцать секунд — это время ПЕРВОГО вызова, когда GameBar.exe ещё не
    # запущен; когда он уже поднят, цикл выходит через полсекунды.
    appeared: Optional[Path] = None
    for _ in range(75):
        time.sleep(0.2)
        appeared = next((p for p in _capture_files(directory) if p.name not in before), None)
        if appeared:
            break

    if not appeared:
        raise ExecError(
            "Game Bar не начал запись. Обычно причина одна из двух: запись выключена в "
            "«Параметры → Игры → Записи», либо активное окно защищено от захвата. "
            "Проверьте, что Win+Alt+R работает вручную.", 409)

    _RECORDING.update(active=True, file=str(appeared), started_at=time.time())
    return {"method": "gamebar", "title": "Запись экрана идёт", "detail": str(appeared),
            "file": appeared.name, "folder": str(directory)}


def act_record_stop() -> dict[str, Any]:
    permission("allowSystem")
    directory = _capture_dir()
    tracked = _RECORDING.get("file")

    _send_gamebar_chord()

    # На остановке Game Bar досводит файл: размер растёт ещё секунду-две. Ждём,
    # пока он перестанет меняться, — только тогда файл действительно готов.
    target: Optional[Path] = None
    previous, stable = -1, 0
    for _ in range(40):
        time.sleep(0.2)
        files = _capture_files(directory)
        if not files:
            continue
        target = next((p for p in files if str(p) == tracked), files[0])
        size = target.stat().st_size
        if size > 0 and size == previous:
            stable += 1
            if stable >= 3:
                break
        else:
            stable = 0
        previous = size

    seconds = None
    if _RECORDING.get("active") and _RECORDING.get("started_at"):
        seconds = int(time.time() - _RECORDING["started_at"])
    _RECORDING.update(active=False, file=None, started_at=None)

    if target is None:
        # Записи не было — но и ошибки нет: пользователь мог сказать «останови
        # запись», когда ничего не писалось. Сообщаем как есть.
        return {"method": "gamebar", "title": "Записи не было",
                "detail": "файл записи не найден", "folder": str(directory)}

    size = target.stat().st_size
    megabytes = round(size / (1024 * 1024), 1)
    detail = f"{target.name} · {megabytes} МБ"
    if seconds is not None:
        detail += f" · {seconds} с"
    return {"method": "gamebar", "title": "Запись сохранена", "detail": detail,
            "file": target.name, "path": str(target), "folder": str(directory), "bytes": size}


def execute(payload: "ExecRequest") -> dict[str, Any]:
    intent = payload.intent
    target = (payload.target or "")[:512]

    if intent == "open_app":
        return act_open_app(target, (payload.via or "").strip())
    if intent == "open_folder":
        return act_open_folder(target)
    if intent == "open_url":
        return act_open_url(target)
    if intent == "web_search":
        return act_web_search(payload.query or target)
    if intent == "close_app":
        if not payload.confirm:
            raise ExecError("Для завершения приложения требуется подтверждение пользователя", 428)
        return act_close_app(target)
    if intent in VOLUME_KEYS or intent == "volume_set":
        return act_volume(intent, payload.amount, payload.level)
    if intent == "screenshot":
        return act_screenshot()
    if intent == "record_screen":
        return act_record_start()
    if intent == "record_stop":
        return act_record_stop()
    if intent == "lock":
        return act_lock()
    raise ExecError(f"Неизвестное действие: {intent or '—'}")


# ------------------------------------------------------------- сессии --------

SESSIONS: dict[str, dict[str, Any]] = {}


def require_session(token: Optional[str]) -> dict[str, Any]:
    if not token or token not in SESSIONS:
        raise ExecError("Нет разрешения на управление системой. Обновите страницу.", 401)
    session = SESSIONS[token]
    now = time.time()
    limit = (CONFIG.get("rateLimit") or {}).get("actionsPerMinute", 30)
    session["hits"] = [t for t in session["hits"] if now - t < 60]
    if len(session["hits"]) >= limit:
        raise ExecError("Слишком много команд подряд. Подождите минуту.", 429)
    session["hits"].append(now)
    return session


# ---------------------------------------------------------------- API --------

class GrantRequest(BaseModel):
    pin: Optional[str] = None


class ExecRequest(BaseModel):
    intent: str
    target: Optional[str] = None
    query: Optional[str] = None
    amount: Optional[int] = None
    #: «steam» — цель искать только среди игр библиотеки Steam
    via: Optional[str] = None
    #: точная громкость в процентах для volume_set
    level: Optional[int] = None
    confirm: bool = False


app = FastAPI(title="Cloud HDR Agent", version=VERSION, docs_url=None, redoc_url=None)
# «Слушает только 127.0.0.1» от браузера не защищает: любой открытый в нём сайт
# тоже ходит на 127.0.0.1. Со звёздочкой здесь страница с example.com получала
# токен управления одним запросом. Теперь — только страница приложения.
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(APP_ORIGINS),
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Cloud-Token"],
)


@app.middleware("http")
async def only_own_page(request: Request, call_next):
    allowed, _ = caller_allowed(request.headers, APP_PORT)
    if not allowed:
        return JSONResponse(status_code=403, content={"ok": False, "error": "Запрос не со страницы Cloud HDR"})
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    import socket

    return {
        "ok": True,
        "name": "Cloud HDR Agent (Python)",
        "version": VERSION,
        "platform": sys.platform,
        "host": socket.gethostname(),
        "user": os.environ.get("USERNAME"),
        "requirePin": bool(PIN),
        "permissions": CONFIG.get("permissions", {}),
        "uptime": int(time.time() - START_TIME),
    }


@app.post("/api/session/grant")
async def grant(body: GrantRequest) -> dict[str, Any]:
    if PIN and (body.pin or "").strip() != PIN:
        raise ExecError("Неверный код подтверждения", 401)
    token = secrets.token_hex(24)
    SESSIONS[token] = {"created": time.time(), "hits": []}
    log(f"GRANT  сессия выдана ({token[:8]}…)")
    return {"ok": True, "token": token, "permissions": CONFIG.get("permissions", {})}


@app.post("/api/session/revoke")
async def revoke(x_cloud_token: Optional[str] = Header(None)) -> dict[str, Any]:
    SESSIONS.pop(x_cloud_token or "", None)
    log("REVOKE разрешение отозвано")
    return {"ok": True}


@app.get("/api/apps")
async def apps(x_cloud_token: Optional[str] = Header(None)) -> dict[str, Any]:
    require_session(x_cloud_token)
    return {
        "ok": True,
        "apps": [
            {
                "key": key,
                "title": item["title"],
                "icon": item.get("icon", "app"),
                "aliases": item.get("aliases", []),
                "installed": bool(resolve_executable(item)),
                "found": bool(resolve_executable(item) or item.get("uri") or item.get("shell")),
            }
            for key, item in APPS.items()
        ],
        "folders": [
            {
                "key": key,
                "title": item["title"],
                "aliases": item["aliases"],
                "path": expand(item["path"]),
                "exists": Path(expand(item["path"])).is_dir(),
            }
            for key, item in FOLDERS.items()
        ],
    }


@app.post("/api/exec")
async def exec_action(body: ExecRequest, x_cloud_token: Optional[str] = Header(None)) -> dict[str, Any]:
    require_session(x_cloud_token)
    try:
        result = execute(body)
    except HTTPException as error:
        log(f"FAIL   {body.intent} → {error.detail}")
        raise
    log(f'EXEC   {body.intent} → {result["title"]} ({result["method"]}: {result["detail"]})')
    return {"ok": True, "intent": body.intent, **result}


# фронтенд отдаётся тем же сервером — никакого CORS в обычном режиме
app.mount("/", StaticFiles(directory=str(PUBLIC_DIR), html=True), name="static")

START_TIME = time.time()


if __name__ == "__main__":
    import uvicorn

    host = CONFIG.get("host", "127.0.0.1")
    port = int(CONFIG.get("port", 4477))
    address = f"http://{host}:{port}"

    print()
    print("   [CLOUD HDR AI v1.0] Запуск модуля...")
    print("   ☁  Cloud HDR Agent (Python) v" + VERSION)
    print("   ─────────────────────────────────────────────")
    print("   Интерфейс :  " + address)
    print("   Права     :  " + ", ".join(k for k, v in CONFIG.get("permissions", {}).items() if v))
    print("   Папки     :  " + "  |  ".join(expand(r) for r in CONFIG.get("allowedFolderRoots", [])))
    if PIN:
        print(f"   КОД ДОСТУПА: {PIN}   ← введите его в окне разрешений")
    print("   Ctrl+C — остановить агента")
    print()

    if CONFIG.get("openBrowserOnStart") and sys.platform == "win32":
        detach(["cmd.exe", "/c", "start", "", address])

    uvicorn.run(app, host=host, port=port, log_level="warning")
