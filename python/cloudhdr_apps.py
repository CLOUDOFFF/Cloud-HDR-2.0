"""
Cloud HDR — группа 3 интеграции с ПК: программы, окна, автозагрузка.

Зовёт его служба умений (cloudhdr_hub.py, маршруты /hub/apps*). Здесь только
Windows: winget, окна через user32, записи автозапуска. Разговор, карточки и
кнопки — в окне приложения (public/js/features.js).

    программы     «установи телеграм» → карточка с найденным пакетом и кнопкой;
                  «что нужно обновить», «обнови все программы»;
                  «удали программу X» — только после кнопки «Удалить».
    окна          «какие окна открыты», «сверни все окна», «переключись на хром»,
                  «сверни/разверни хром», «хром слева, телеграм справа»,
                  «перенеси хром на второй монитор», «перезапусти браузер»,
                  «что зависло», «закрой зависшие».
    автозагрузка  «что в автозагрузке», «убери дискорд из автозагрузки»,
                  «верни дискорд в автозагрузку». Отключение — как в
                  Диспетчере задач (StartupApproved): запись остаётся на месте,
                  вернуть её можно одной фразой.

Установка, обновление и удаление идут фоновой задачей: winget работает минуты,
а служба за это время должна отвечать на всё остальное. Окно опрашивает ход
через /hub/apps/job.
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import threading
import time
import uuid
import winreg
from ctypes import wintypes
from pathlib import Path

import psutil

from cloudhdr_files import levenshtein, loose

NO_WINDOW = 0x08000000

# ================================================================ имена ===
#
# Как люди называют программы — и как они называются на самом деле. Нужен и
# для окон (имя процесса), и для winget (поисковый запрос). Всё, чего здесь нет,
# сравнивается транслитом: «телеграм» ≈ telegram, «зум» ≈ zoom.

ALIASES: dict[str, tuple[str, ...]] = {
    'chrome': ('хром', 'гугл хром', 'гугл', 'chrome', 'google chrome'),
    'msedge': ('эдж', 'едж', 'edge', 'майкрософт эдж'),
    'firefox': ('фаерфокс', 'файрфокс', 'мозилла', 'firefox'),
    'browser': ('яндекс браузер', 'яндекс', 'yandex browser'),
    'opera': ('опера', 'opera'),
    'telegram': ('телеграм', 'телега', 'тг', 'telegram'),
    'discord': ('дискорд', 'дс', 'discord'),
    'steam': ('стим', 'steam'),
    'spotify': ('спотифай', 'spotify'),
    'code': ('вскод', 'vs code', 'vscode', 'visual studio code', 'код'),
    'explorer': ('проводник', 'explorer'),
    'notepad': ('блокнот', 'notepad'),
    'winword': ('ворд', 'word', 'майкрософт ворд'),
    'excel': ('эксель', 'excel'),
    'powerpnt': ('поверпоинт', 'пауэрпоинт', 'powerpoint', 'презентации'),
    'obs64': ('обс', 'obs', 'obs studio'),
    'robloxplayerbeta': ('роблокс', 'roblox'),
    'robloxstudiobeta': ('роблокс студио', 'roblox studio', 'студио'),
    'vlc': ('влс', 'vlc', 'влц'),
    'mspaint': ('пейнт', 'паинт', 'paint', 'рисовалку'),
    'windowsterminal': ('терминал', 'terminal'),
    'taskmgr': ('диспетчер задач', 'диспетчер'),
    'yandexmusic': ('яндекс музыка', 'яндекс музыку', 'музыка яндекс'),
    'zoom': ('зум', 'zoom'),
    'whatsapp': ('ватсап', 'вотсап', 'whatsapp'),
    'viber': ('вайбер', 'viber'),
    'skype': ('скайп', 'skype'),
    'epicgameslauncher': ('эпик', 'epic games', 'эпик геймс'),
    'cloudhdr': ('cloud hdr', 'клауд', 'клоуд'),
}

# Слово «браузер» — любой из них: тот, что сейчас открыт.
BROWSERS = ('chrome', 'msedge', 'firefox', 'browser', 'opera', 'vivaldi', 'brave')

# Что искать в winget по русскому названию (когда оно не совпадает с английским).
WINGET_QUERY = {
    'хром': 'Google Chrome', 'гугл хром': 'Google Chrome', 'телеграм': 'Telegram Desktop', 'телега': 'Telegram Desktop',
    'дискорд': 'Discord', 'стим': 'Steam', 'спотифай': 'Spotify', 'вскод': 'Visual Studio Code', 'vs code': 'Visual Studio Code',
    'обс': 'OBS Studio', 'влс': 'VLC', 'зум': 'Zoom', 'ватсап': 'WhatsApp', 'вотсап': 'WhatsApp', 'вайбер': 'Viber',
    'скайп': 'Skype', 'фаерфокс': 'Mozilla Firefox', 'файрфокс': 'Mozilla Firefox', 'мозилла': 'Mozilla Firefox',
    'яндекс браузер': 'Yandex Browser', 'опера': 'Opera', 'винрар': 'WinRAR', '7зип': '7-Zip', 'семизип': '7-Zip',
    'нотпад': 'Notepad++', 'блокнот плюс плюс': 'Notepad++', 'гимп': 'GIMP', 'аудасити': 'Audacity',
    'эпик': 'Epic Games Launcher', 'эпик геймс': 'Epic Games Launcher', 'торрент': 'qBittorrent', 'кьюбиторрент': 'qBittorrent',
    'питон': 'Python 3.12', 'пайтон': 'Python 3.12', 'гит': 'Git', 'нода': 'Node.js', 'ноду': 'Node.js',
    'блендер': 'Blender', 'анидеск': 'AnyDesk', 'тимвьюер': 'TeamViewer', 'пайнт нет': 'Paint.NET',
    'роблокс': 'Roblox', 'майнкрафт': 'Minecraft Launcher', 'яндекс музыку': 'Яндекс Музыка', 'яндекс музыка': 'Яндекс Музыка',
}


def canon(query: str) -> str:
    """Каноническое имя процесса по тому, как его назвали: «хром» → chrome."""
    q = query.lower().strip().replace('ё', 'е')
    for name, aliases in ALIASES.items():
        if q == name or q in aliases:
            return name
    return ''


def name_score(query: str, *candidates: str) -> int:
    """0 — не похоже; больше — похоже сильнее. Сравнение транслитом."""
    q = loose(query)
    if not q:
        return 0
    best = 0
    for cand in candidates:
        c = loose(cand)
        if not c:
            continue
        if c == q:
            best = max(best, 100)
        elif c.startswith(q) or q.startswith(c) and len(c) >= 4:
            best = max(best, 80)
        elif q in c and len(q) >= 3:
            best = max(best, 60)
        elif len(q) >= 5 and levenshtein(q, c[:len(q) + 1]) <= max(1, len(q) // 5):
            best = max(best, 50)
    return best


# ================================================================ окна ===

user32 = ctypes.WinDLL('user32', use_last_error=True)
dwmapi = ctypes.WinDLL('dwmapi')

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
GW_OWNER = 4
SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE, SW_SHOWMINNOACTIVE = 6, 3, 9, 7
DWMWA_EXTENDED_FRAME_BOUNDS, DWMWA_CLOAKED = 9, 14
WM_CLOSE = 0x0010
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
MONITOR_DEFAULTTONEAREST = 2


class MONITORINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT), ('dwFlags', wintypes.DWORD)]


user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsHungAppWindow.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.c_void_p, MONITORENUMPROC, wintypes.LPARAM]
dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

# Окна, которые человек «окнами» не считает: панель задач, рабочий стол,
# оверлеи видеокарты, служебные окна самой Windows.
SKIP_EXE = {'textinputhost.exe', 'shellexperiencehost.exe', 'searchhost.exe', 'startmenuexperiencehost.exe',
            'applicationframehost.exe', 'systemsettings.exe', 'lockapp.exe', 'nvidia overlay.exe', 'widgets.exe',
            'gamebar.exe', 'phoneexperiencehost.exe'}
SKIP_TITLES = {'program manager', 'windows input experience', 'microsoft text input application'}


def _title(hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ''
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _cloaked(hwnd) -> bool:
    value = wintypes.DWORD()
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(value), ctypes.sizeof(value))
    return bool(value.value)


def windows() -> list[dict]:
    """Окна, которые видны человеку на панели задач."""
    found: list[dict] = []

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, GW_OWNER):
            return True
        if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW or _cloaked(hwnd):
            return True
        title = _title(hwnd)
        if not title or title.lower() in SKIP_TITLES:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            process = psutil.Process(pid.value)
            exe_name = process.name()
            try:
                exe_path = process.exe()
            except (psutil.AccessDenied, OSError):
                exe_path = ''
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return True
        if exe_name.lower() in SKIP_EXE:
            return True
        found.append({'hwnd': int(hwnd), 'title': title, 'pid': pid.value, 'exe': exe_name, 'path': exe_path,
                      'minimized': bool(user32.IsIconic(hwnd)), 'maximized': bool(user32.IsZoomed(hwnd)),
                      'hung': bool(user32.IsHungAppWindow(hwnd))})
        return True

    user32.EnumWindows(WNDENUMPROC(visit), 0)
    return found


# Имена процессов, как их знает человек, а не диспетчер задач. Общие для
# окон здесь и для «что грузит процессор» в Hub.
FRIENDLY = {
    'chrome': 'Google Chrome', 'msedge': 'Microsoft Edge', 'firefox': 'Firefox', 'opera': 'Opera', 'browser': 'Яндекс Браузер',
    'dwm': 'Оформление Windows', 'explorer': 'Проводник', 'system': 'Windows (ядро)', 'memcompression': 'Сжатие памяти Windows',
    'msmpeng': 'Защитник Windows', 'searchhost': 'Поиск Windows', 'svchost': 'Службы Windows', 'audiodg': 'Звук Windows',
    'python': 'Cloud HDR (модель)', 'pythonw': 'Cloud HDR (службы)', 'cloudhdr': 'Cloud HDR', 'powershell': 'PowerShell',
    'discord': 'Discord', 'telegram': 'Telegram', 'steam': 'Steam', 'steamwebhelper': 'Steam', 'spotify': 'Spotify',
    'robloxplayerbeta': 'Roblox', 'robloxstudiobeta': 'Roblox Studio', 'code': 'VS Code', 'obs64': 'OBS Studio',
    'яндекс музыка': 'Яндекс Музыка', 'msedgewebview2': 'Окна на WebView2', 'notepad': 'Блокнот', 'winword': 'Word',
    'excel': 'Excel', 'powerpnt': 'PowerPoint', 'windowsterminal': 'Терминал', 'taskmgr': 'Диспетчер задач',
    'vlc': 'VLC', 'claude': 'Claude', 'mspaint': 'Paint', 'securityhealthsystray': 'Безопасность Windows', 'onedrive': 'OneDrive',
    'epicgameslauncher': 'Epic Games', 'yandexmusic': 'Яндекс Музыка', 'whatsapp': 'WhatsApp', 'zoom': 'Zoom',
}


def friendly(exe: str) -> str:
    base = exe.removesuffix('.exe').removesuffix('.EXE')
    return FRIENDLY.get(base.lower(), base)


def app_label(win: dict) -> str:
    """Короткое имя окна для ответа: «Google Chrome», а не заголовок вкладки."""
    return friendly(win['exe'])


def find_windows(query: str, wins: list[dict] | None = None) -> list[dict]:
    """Окна программы по тому, как её назвали: «хром», «телеграм», «браузер»."""
    wins = windows() if wins is None else wins
    q = query.lower().strip().replace('ё', 'е')
    if not q:
        return []
    if re.fullmatch(r'браузер\w*', q):
        return [w for w in wins if w['exe'].lower().removesuffix('.exe') in BROWSERS]
    name = canon(q)
    if name:
        hits = [w for w in wins if w['exe'].lower().removesuffix('.exe') == name]
        if hits:
            return hits
    scored = []
    for w in wins:
        score = max(name_score(q, w['exe'].removesuffix('.exe'), Path(w['path']).stem if w['path'] else ''),
                    name_score(q, w['title']) - 20)
        if score >= 50:
            scored.append((score, w))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [w for s, w in scored if s == top]


def _press_alt() -> None:
    # Windows не даёт фоновой программе выводить чужие окна вперёд, пока
    # пользователь «ничего не нажимал». Короткое нажатие Alt снимает запрет —
    # тот же приём используют переключатели окон.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)


def focus(hwnd: int) -> bool:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    _press_alt()
    return bool(user32.SetForegroundWindow(hwnd))


def _frame_offsets(hwnd) -> tuple[int, int, int, int]:
    """Невидимые рамки окна (у Windows 10/11 это ~7 px слева, справа и снизу)."""
    outer = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    inner = wintypes.RECT()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(inner), ctypes.sizeof(inner)) != 0:
        return 0, 0, 0, 0
    return inner.left - outer.left, inner.top - outer.top, outer.right - inner.right, outer.bottom - inner.bottom


def monitors() -> list[dict]:
    """Мониторы слева направо: рабочая область (без панели задач)."""
    found = []

    def visit(handle, _hdc, _rect, _):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(handle, ctypes.byref(info))
        work = info.rcWork
        found.append({'handle': handle, 'work': (work.left, work.top, work.right, work.bottom), 'primary': bool(info.dwFlags & 1)})
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(visit), 0)
    found.sort(key=lambda m: (m['work'][0], m['work'][1]))
    return found


def monitor_of(hwnd: int) -> tuple[int, int, int, int]:
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST), ctypes.byref(info))
    w = info.rcWork
    return w.left, w.top, w.right, w.bottom


def place(hwnd: int, box: tuple[int, int, int, int]) -> None:
    """Поставить окно ровно в прямоугольник, с поправкой на невидимые рамки."""
    user32.ShowWindow(hwnd, SW_RESTORE)
    left, top, right, bottom = box
    dl, dt, dr, db = _frame_offsets(hwnd)
    user32.SetWindowPos(hwnd, None, left - dl, top - dt, (right - left) + dl + dr, (bottom - top) + dt + db, SWP_NOZORDER)


def snap(hwnd: int, side: str, work: tuple[int, int, int, int] | None = None) -> None:
    left, top, right, bottom = work or monitor_of(hwnd)
    middle = (left + right) // 2
    place(hwnd, (left, top, middle, bottom) if side == 'left' else (middle, top, right, bottom))


def move_to_monitor(hwnd: int, target: dict) -> None:
    """Перенести окно на другой монитор, сохранив положение и «на весь экран»."""
    was_max = bool(user32.IsZoomed(hwnd))
    sl, st, sr, sb = monitor_of(hwnd)
    tl, tt, tr, tb = target['work']
    rect = wintypes.RECT()
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    fx = (rect.left - sl) / max(1, sr - sl)
    fy = (rect.top - st) / max(1, sb - st)
    width = min(rect.right - rect.left, tr - tl)
    height = min(rect.bottom - rect.top, tb - tt)
    x = tl + int(fx * (tr - tl))
    y = tt + int(fy * (tb - tt))
    x = min(max(x, tl), tr - width)
    y = min(max(y, tt), tb - height)
    user32.SetWindowPos(hwnd, None, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE)
    if was_max:
        user32.ShowWindow(hwnd, SW_MAXIMIZE)


def own_window(win: dict) -> bool:
    """Окно самого Cloud HDR — его не сворачиваем вместе со всеми."""
    return win['exe'].lower() in ('cloudhdr.exe',) or win['title'].startswith('Cloud HDR')


def target_window(wins: list[dict] | None = None) -> dict | None:
    """«Это окно» — верхнее окно под Cloud HDR.

    EnumWindows отдаёт окна в порядке наложения, сверху вниз. Когда человек
    говорит «сверни это» голосом, сверху и есть то, на что он смотрит; когда
    пишет в окне Cloud HDR — сверху Cloud HDR, а под ним то, с чем он работал
    до этого. В обоих случаях нужное — первое не наше и не свёрнутое.
    """
    for win in (windows() if wins is None else wins):
        if own_window(win) or win['minimized']:
            continue
        return win
    return None


VK = {'ctrl': 0x11, 'shift': 0x10, 'alt': 0x12, 'tab': 0x09, 'f5': 0x74, 'left': 0x25, 'right': 0x27,
      'pgdn': 0x22, 'pgup': 0x21, 'w': 0x57, 't': 0x54, 'f': 0x46, 'home': 0x24, 'end': 0x23,
      **{str(n): 0x30 + n for n in range(1, 10)}}

# «вторая вкладка» → Ctrl+2; Ctrl+9 в браузерах — всегда последняя
TAB_NUMBERS = {'перв': 1, 'втор': 2, 'трет': 3, 'четверт': 4, 'пят': 5, 'шест': 6, 'седьм': 7, 'восьм': 8, 'последн': 9}
TAB_NUMBER_RE = (r'^(?:(?:переключи(?:сь)?|перейди|открой|покажи|давай)\s+)?(?:на\s+)?'
                 r'(?:(?P<word>перв|втор|трет|четверт|пят|шест|седьм|восьм|последн)\w*\s+вкладк\w*'
                 r'|вкладк\w*\s+(?:номер\s+)?(?P<num>[1-9]))$')


def press(*keys: str) -> None:
    """Сочетание клавиш: press('ctrl', 'w')."""
    codes = [VK[k] for k in keys]
    for code in codes:
        user32.keybd_event(code, 0, 0, 0)
    for code in reversed(codes):
        user32.keybd_event(code, 0, KEYEVENTF_KEYUP, 0)


# Что делаем с «этим» окном: (выражение, действие, клавиши или None, что сказать)
THIS_ACTIONS = [
    (r'^(?:сверни)(?:\s+(?:это|эту|этот|его|ее|её))?(?:\s+(?:окно|вкладку|программу|приложение))?$', 'min', None, 'Свернул {app}.'),
    (r'^(?:разверни)(?:\s+(?:это|эту|этот|его|ее|её))?(?:\s+окно)?(?:\s+на\s+весь\s+экран)?$|^(?:на\s+)?весь\s+экран$', 'max', None, 'Развернул {app} на весь экран.'),
    (r'^закрой\s+(?:это|этот|эту)\s+(?:окно|программу|приложение)$|^закрой\s+(?:это\s+)?окно$|^закрой\s+это$', 'close', None, 'Закрыл {app}.'),
    (r'^закрой\s+(?:эту\s+|текущую\s+|открытую\s+)?вкладку$', 'keys', ('ctrl', 'w'), 'Закрыл вкладку.'),
    (r'^(?:открой\s+|создай\s+)?нов(?:ую|ая)\s+вкладк[уа]$', 'keys', ('ctrl', 't'), 'Открыл новую вкладку.'),
    (r'^(?:(?:переключи(?:сь)?|перейди)\s+(?:на\s+)?)?(?:следующ\w+|другую)\s+вкладк\w*$', 'keys', ('ctrl', 'tab'), 'Следующая вкладка.'),
    (r'^(?:(?:переключи(?:сь)?|перейди)\s+(?:на\s+)?)?(?:предыдущ\w+|прошл\w+)\s+вкладк\w*$', 'keys', ('ctrl', 'shift', 'tab'), 'Предыдущая вкладка.'),
    (r'^(?:верни|открой|восстанови)\s+(?:закрытую\s+)?вкладку(?:\s+обратно)?$', 'keys', ('ctrl', 'shift', 't'), 'Вернул закрытую вкладку.'),
    (r'^(?:обнови|перезагрузи)\s+(?:эту\s+)?(?:страницу|вкладку)$', 'keys', ('f5',), 'Обновил страницу.'),
    (r'^(?:назад|вернись\s+назад|на\s+страницу\s+назад|предыдущая\s+страница)$', 'keys', ('alt', 'left'), 'Назад.'),
    (r'^(?:вперед|вперёд|на\s+страницу\s+вперед|следующая\s+страница)$', 'keys', ('alt', 'right'), 'Вперёд.'),
    (r'^(?:прокрути|листай|пролистай)(?:\s+страницу)?\s+(?:вниз|ниже)$', 'keys', ('pgdn',), 'Листаю вниз.'),
    (r'^(?:прокрути|листай|пролистай)(?:\s+страницу)?\s+(?:вверх|выше)$', 'keys', ('pgup',), 'Листаю вверх.'),
    (r'^(?:в\s+начало\s+страницы|наверх\s+страницы)$', 'keys', ('ctrl', 'home'), 'В начало страницы.'),
]


def match_this(text: str) -> tuple | None:
    low = re.sub(r'\s+', ' ', text.lower().strip().rstrip('.!?').replace('ё', 'е'))
    low = re.sub(r'^(?:пожалуйста|ну|давай)\s+', '', low)
    for pattern, action, keys, say in THIS_ACTIONS:
        if re.search(pattern.replace('ё', 'е'), low):
            return action, keys, say
    m = re.search(TAB_NUMBER_RE, low)
    if m:
        n = int(m.group('num')) if m.group('num') else TAB_NUMBERS[m.group('word')]
        return 'keys', ('ctrl', str(n)), 'Последняя вкладка.' if n == 9 and m.group('word') else f'Вкладка {n}.'
    return None


def this_answer(text: str) -> dict | None:
    found = match_this(text)
    if not found:
        return None
    action, keys, say = found
    win = target_window()
    if not win:
        return {'ok': False, 'known': True, 'say': 'Не вижу окна, с которым работать: кроме Cloud HDR, всё свёрнуто.'}
    label = app_label(win)
    hwnd = win['hwnd']
    if action == 'min':
        user32.ShowWindow(hwnd, SW_MINIMIZE)
    elif action == 'max':
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        focus(hwnd)
    elif action == 'close':
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    else:
        focus(hwnd)
        time.sleep(0.15)          # окно должно успеть стать активным, иначе клавиши уйдут не туда
        press(*keys)
    return {'ok': True, 'known': True, 'say': say.format(app=label)}


def restart(query: str) -> dict:
    """Закрыть программу по-хорошему и запустить заново."""
    wins = find_windows(query)
    if not wins:
        return {'ok': False, 'say': f'Не нашёл открытую программу «{query}». Скажите, как она называется в панели задач.'}
    exe_path = next((w['path'] for w in wins if w['path']), '')
    exe_name = wins[0]['exe']
    label = app_label(wins[0])
    if not exe_path:
        return {'ok': False, 'say': f'У {label} нет доступа к пути запуска — перезапустите вручную.'}
    for w in wins:
        user32.PostMessageW(w['hwnd'], WM_CLOSE, 0, 0)
    procs = [p for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower() == exe_name.lower()]
    gone, alive = psutil.wait_procs(procs, timeout=10)
    if alive:
        # Не закрылась сама (например, свёрнута в трей) — завершаем.
        for p in alive:
            try:
                p.terminate()
            except psutil.Error:
                pass
        psutil.wait_procs(alive, timeout=5)
    subprocess.Popen([exe_path], cwd=str(Path(exe_path).parent), creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_GROUP
    return {'ok': True, 'say': f'Перезапустил {label}.'}


# ============================================================= winget ===

def winget(args: list[str], timeout: int = 180) -> tuple[int, str]:
    try:
        done = subprocess.run(['winget', *args, '--accept-source-agreements', '--disable-interactivity'],
                              capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return done.returncode, done.stdout.decode('utf-8', 'replace')
    except FileNotFoundError:
        return -1, 'NO_WINGET'
    except subprocess.TimeoutExpired:
        return -2, ''


def _clean_lines(text: str) -> list[str]:
    """Убрать крутилки и полосы прогресса, которые winget печатает в поток."""
    lines = []
    for raw in text.replace('\r', '\n').split('\n'):
        line = raw.rstrip()
        if not line.strip() or re.fullmatch(r'\s*[-\\|/]\s*', line) or re.search(r'[█▒]', line):
            continue
        lines.append(line)
    return lines


def parse_table(text: str) -> list[dict]:
    """Таблица winget → список словарей по заголовкам (Name, Id, Version…)."""
    lines = _clean_lines(text)
    for i, line in enumerate(lines):
        if re.fullmatch(r'-{10,}', line.strip()) and i > 0:
            header = lines[i - 1]
            cols = [(m.group(), m.start()) for m in re.finditer(r'\S+', header)]
            rows = []
            for row in lines[i + 1:]:
                if re.match(r'^\d+ (upgrades|package|пакет)', row) or len(row) < cols[-1][1] // 2:
                    break
                item = {}
                for k, (name, start) in enumerate(cols):
                    end = cols[k + 1][1] if k + 1 < len(cols) else None
                    item[name.lower()] = row[start:end].strip() if start < len(row) else ''
                if item.get('id'):
                    rows.append(item)
            return rows
    return []


def winget_query(target: str) -> str:
    low = target.lower().strip().replace('ё', 'е')
    return WINGET_QUERY.get(low, target.strip())


def search_packages(target: str) -> list[dict]:
    query = winget_query(target)
    code, out = winget(['search', query, '--source', 'winget'], 90)
    if out == 'NO_WINGET':
        return []
    rows = parse_table(out)
    ql = query.lower()

    def rank(row):
        name = row.get('name', '').lower()
        match = row.get('match', '').lower()
        return (0 if name == ql else 1 if name.startswith(ql) else 2 if f'moniker: {ql}' in match or f'moniker: {target.lower()}' in match
                else 3 if ql in name else 4, len(name))

    rows.sort(key=rank)
    return rows[:4]


def installed(target: str = '') -> list[dict]:
    args = ['list'] + (['--name', winget_query(target)] if target else [])
    _, out = winget(args, 120)
    return [r for r in parse_table(out)]


def upgrades() -> list[dict]:
    _, out = winget(['upgrade'], 150)
    return [r for r in parse_table(out) if r.get('available')]


class Job:
    """Одна фоновая задача winget: установка, обновление или удаление."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state: dict = {'state': 'idle'}

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def start(self, kind: str, title: str, args: list[str]) -> dict:
        with self.lock:
            if self.state.get('state') == 'running':
                return {'ok': False, 'error': f'Уже идёт: {self.state.get("title")}. Дождитесь окончания.'}
            self.state = {'id': uuid.uuid4().hex[:8], 'state': 'running', 'kind': kind, 'title': title,
                          'progress': 0.0, 'stage': 'Готовлюсь…', 'started': time.time()}
            job = dict(self.state)
        threading.Thread(target=self._run, args=(kind, title, args), daemon=True).start()
        return {'ok': True, 'job': job}

    def _set(self, **values) -> None:
        with self.lock:
            self.state.update(values)

    def _run(self, kind: str, title: str, args: list[str]) -> None:
        cmd = ['winget', *args, '--accept-source-agreements', '--disable-interactivity']
        if kind in ('install', 'upgrade', 'upgrade_all'):
            cmd.append('--accept-package-agreements')
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
        except FileNotFoundError:
            self._set(state='error', say='На этом компьютере нет winget (Установщик приложений). Его ставит Microsoft Store.')
            return
        tail: list[str] = []
        buffer = b''
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buffer += chunk
            text = buffer.decode('utf-8', 'replace')
            parts = re.split(r'[\r\n]', text)
            buffer = parts[-1].encode('utf-8')
            for line in parts[:-1]:
                line = line.strip()
                if not line or re.fullmatch(r'[-\\|/]', line):
                    continue
                pct = re.search(r'(\d{1,3})%\s*$', line)
                size = re.search(r'([\d.]+)\s*(KB|MB|GB)\s*/\s*([\d.]+)\s*(KB|MB|GB)', line)
                if size:
                    done_mb = float(size.group(1)) * {'KB': 1 / 1024, 'MB': 1, 'GB': 1024}[size.group(2)]
                    total_mb = float(size.group(3)) * {'KB': 1 / 1024, 'MB': 1, 'GB': 1024}[size.group(4)]
                    self._set(progress=min(0.95, done_mb / max(total_mb, 0.01) * 0.8), stage=f'Скачиваю — {done_mb:.0f} из {total_mb:.0f} МБ')
                elif pct:
                    self._set(progress=min(0.95, int(pct.group(1)) / 100 * 0.8))
                elif re.search(r'Starting package install|Запуск установки', line, re.I):
                    self._set(progress=0.85, stage='Устанавливаю…')
                elif re.search(r'Starting package uninstall|Запуск удаления', line, re.I):
                    self._set(progress=0.5, stage='Удаляю…')
                elif re.search(r'Downloading|Скачивание', line, re.I):
                    self._set(stage='Скачиваю…')
                if not re.search(r'[█▒]', line):
                    tail.append(line)
                    tail = tail[-12:]
        code = proc.wait()
        text = '\n'.join(tail)
        self._set(state='done' if code == 0 else 'error', progress=1.0, code=code, finished=time.time(),
                  say=self._summary(kind, title, code, text), log=text)

    @staticmethod
    def _summary(kind: str, title: str, code: int, text: str) -> str:
        if code == 0:
            if kind == 'install':
                return f'Установил {title}. Найти её можно в меню «Пуск» — или скажите «открой {title}».'
            if kind == 'uninstall':
                return f'Удалил {title}.'
            if kind == 'upgrade_all':
                count = len(re.findall(r'Successfully installed|Успешно установлено', text))
                return f'Обновил программы{f" ({count})" if count else ""}.'
            return f'Обновил {title}.'
        low = text.lower()
        if 'already installed' in low or 'уже установлен' in low:
            return f'{title} уже установлена — обновлений для неё нет.'
        if 'no applicable upgrade' in low or 'no available upgrade' in low or 'нет доступных обновлений' in low:
            return f'У {title} уже последняя версия.'
        if 'cancelled' in low or 'отменен' in low or code in (1602, -1978335215):
            return 'Установку отменили — окно разрешения Windows закрыто без согласия.'
        if 'no package found' in low or 'не найден' in low:
            return f'Winget не нашёл «{title}».'
        return f'Не получилось: winget вернул код {code}. ' + (text.splitlines()[-1][:160] if text else '')


JOB = Job()

# ======================================================== автозагрузка ===

RUN_KEYS = [
    (winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run', 'HKCU',
     r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'),
    (winreg.HKEY_LOCAL_MACHINE, r'Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM',
     r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'),
    (winreg.HKEY_LOCAL_MACHINE, r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run', 'HKLM32',
     r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32'),
]
STARTUP_FOLDERS = [
    (Path(os.environ.get('APPDATA', '')) / r'Microsoft\Windows\Start Menu\Programs\Startup', winreg.HKEY_CURRENT_USER),
    (Path(os.environ.get('ProgramData', '')) / r'Microsoft\Windows\Start Menu\Programs\Startup', winreg.HKEY_LOCAL_MACHINE),
]
APPROVED_FOLDER = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'


def _approved(hive, sub: str, name: str) -> bool:
    try:
        with winreg.OpenKey(hive, sub) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return not (isinstance(value, bytes) and value and value[0] & 1)
    except OSError:
        return True


def autostart() -> list[dict]:
    items = []
    for hive, sub, label, approved in RUN_KEYS:
        try:
            with winreg.OpenKey(hive, sub) as key:
                i = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, i)
                    except OSError:
                        break
                    i += 1
                    items.append({'name': name, 'command': str(value), 'where': label, 'hive': hive,
                                  'approved': approved, 'enabled': _approved(hive, approved, name)})
        except OSError:
            pass
    for folder, hive in STARTUP_FOLDERS:
        if folder.is_dir():
            for entry in folder.iterdir():
                if entry.name.lower() == 'desktop.ini':
                    continue
                items.append({'name': entry.stem, 'file': entry.name, 'command': str(entry), 'where': 'папка',
                              'hive': hive, 'approved': APPROVED_FOLDER, 'enabled': _approved(hive, APPROVED_FOLDER, entry.name)})
    return items


def set_autostart(item: dict, enabled: bool) -> tuple[bool, str]:
    """Включить/выключить запись так, как это делает Диспетчер задач."""
    value_name = item.get('file') or item['name']
    stamp = int((time.time() + 11644473600) * 10_000_000).to_bytes(8, 'little')
    data = (b'\x02' if enabled else b'\x03') + b'\x00' * 3 + (b'\x00' * 8 if enabled else stamp)
    try:
        with winreg.CreateKeyEx(item['hive'], item['approved'], 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_BINARY, data)
        return True, ''
    except PermissionError:
        return False, 'admin'
    except OSError as error:
        return False, str(error)


def autostart_label(item: dict) -> str:
    """«Microsoft Edge», а не «MicrosoftEdgeAutoLaunch_29EBC4579851B72EE312C449CF839B1A»."""
    cmd = item['command']
    exe = re.match(r'"([^"]+)"|(\S+?\.exe)', cmd, re.I)
    stem = Path((exe.group(1) or exe.group(2)) if exe else '').stem
    if stem and stem.lower() in FRIENDLY:
        return FRIENDLY[stem.lower()]
    name = re.sub(r'[_\-]?[0-9A-F]{12,}$', '', item['name'] or '', flags=re.I)
    if not name or re.fullmatch(r'[{(].*[})]', name):
        return stem or item['name']
    return name


def find_autostart(query: str) -> list[dict]:
    items = autostart()
    name = canon(query)
    scored = []
    for item in items:
        score = max(name_score(query, item['name'], autostart_label(item), Path(re.sub(r'"', '', item['command']).split(' -')[0]).stem),
                    100 if name and name in item['command'].lower() else 0)
        if score >= 50:
            scored.append((score, item))
    if not scored:
        return []
    top = max(s for s, _ in scored)
    return [i for s, i in scored if s == top]


# ============================================================ разговор ===

INSTALL_RE = r'^(?:пожалуйста\s+)?(?:установи|инсталлируй|скачай\s+и\s+установи|поставь\s+программу|поставь\s+приложение|скачай\s+программу)\s+(?:мне\s+)?(?:программу\s+|приложение\s+|игру\s+)?(.+?)[.!?]*$'
UPGRADE_ALL_RE = r'(обнови|обновить)\s+(все|всё)\s*(программы|приложения|софт)?$|^(обнови|обновить)\s+(программы|приложения|софт)$|(какие|что)\s+(\w+\s+)?(программы\s+|приложения\s+)?(нужно\s+|надо\s+|можно\s+)?обновить|есть\s+ли\s+обновлени\w*\s+(программ|приложений)|проверь\s+обновлени\w*\s+программ'
UPGRADE_ONE_RE = r'^(?:пожалуйста\s+)?обнови\s+(?:программу\s+|приложение\s+)?(.+?)[.!?]*$'
UNINSTALL_RE = r'^(?:пожалуйста\s+)?(?:удали|деинсталлируй|снеси)\s+(?:программу|приложение|игру)\s+(.+?)[.!?]*$'
WINDOWS_RE = r'(какие|что за)\s+(окна|программы|приложения)\s+(сейчас\s+)?(открыты|запущены|открыто)|^что\s+(сейчас\s+)?(открыто|запущено)\??$|^(список|покажи)\s+(открытых\s+)?окон|^открытые\s+окна$'
MIN_ALL_RE = r'^(?:пожалуйста\s+)?(сверни\s+(все|всё)(\s+окна)?|покажи\s+рабочий\s+стол)[.!?]*$'
RESTORE_ALL_RE = r'^(?:пожалуйста\s+)?(разверни|верни)\s+(все|всё)(\s+окна)?(\s+обратно)?[.!?]*$'
FOCUS_RE = r'^(?:пожалуйста\s+)?(?:переключись|перейди|переключи)\s+(?:на|в)\s+(.+?)[.!?]*$|^(?:покажи|открой)\s+окно\s+(.+?)[.!?]*$|^(?:выведи|подними)\s+(.+?)\s+(?:наверх|вперед|вперёд|на\s+передний\s+план)[.!?]*$'
MINIMIZE_RE = r'^(?:пожалуйста\s+)?сверни\s+(?!все\b|всё\b)(.+?)[.!?]*$'
MAXIMIZE_RE = r'^(?:пожалуйста\s+)?(?:разверни|раскрой)\s+(?!все\b|всё\b)(.+?)(?:\s+на\s+весь\s+экран)?[.!?]*$|^(?:сделай\s+)?(.+?)\s+на\s+весь\s+экран[.!?]*$'
SIDE = r'(слева|налево|влево|в\s+левую\s+(?:половину|часть|сторону)|справа|направо|вправо|в\s+правую\s+(?:половину|часть|сторону))'
SNAP_TWO_RE = r'^(?:пожалуйста\s+)?(?:поставь\s+|размести\s+|открой\s+)?(.+?)\s+' + SIDE + r'\s*,?\s*(?:а\s+|и\s+)?(.+?)\s+' + SIDE + r'[.!?]*$'
SPLIT_RE = r'^(?:пожалуйста\s+)?(?:раздели|подели)\s+экран\s+(?:между\s+|на\s+)?(.+?)\s+и\s+(.+?)[.!?]*$'
SNAP_ONE_RE = r'^(?:пожалуйста\s+)?(?:поставь|перемести|прижми|сдвинь|подвинь|размести)\s+(.+?)\s+' + SIDE + r'[.!?]*$'
MONITOR_RE = r'^(?:пожалуйста\s+)?(?:перенеси|перемести|отправь|перекинь)\s+(.+?)\s+на\s+(другой|второй|первый|соседний|основной|главный|левый|правый|третий)\s+(?:монитор|экран|дисплей)[.!?]*$'
RESTART_RE = r'^(?:пожалуйста\s+)?(?:перезапусти|перезагрузи|перезапуск)\s+(?!компьютер|комп\b|пк\b|систем|windows|виндовс|ноутбук)(.+?)[.!?]*$'
HUNG_LIST_RE = r'(что|какая|какие|кто)\s+(\w+\s+)?завис|(программа|окно|игра|приложение|браузер|\w+)\s+(завис\w*|не\s+отвечает)$|^что\s+не\s+отвечает'
HUNG_KILL_RE = r'(закрой|убей|сними|заверши)\s+(все\s+)?(завис\w*|не\s*отвечающ\w*)'
AUTO_LIST_RE = r'(что|какие|покажи|список)\s+(\w+\s+)?(в\s+)?автозагрузк|(что|какие\s+программы)\s+(\w+\s+)?запуска\w*\s+(сама\s+|сами\s+)?(вместе\s+)?с\s+(windows|виндовс|компьютером|системой)|^автозагрузка\??$|^автозапуск\??$'
AUTO_OFF_RE = r'(?:убери|отключи|выключи|удали|исключи)\s+(.+?)\s+из\s+автозагрузки|(?:отключи|выключи|убери)\s+автозапуск\s+(.+?)[.!?]*$|(?:пусть|чтобы)\s+(.+?)\s+не\s+запускал\w*'
AUTO_ON_RE = r'(?:верни|включи|добавь)\s+(.+?)\s+в\s+автозагрузку|(?:включи|верни)\s+автозапуск\s+(.+?)[.!?]*$'

SIDE_LEFT = re.compile(r'слева|налево|влево|левую')


def _side(word: str) -> str:
    return 'left' if SIDE_LEFT.search(word) else 'right'


def _group(match: re.Match) -> str:
    return next((g for g in match.groups() if g), '').strip()


def match_apps(text: str) -> tuple[str, list[str]]:
    """Что просят и о чём: ('install', ['телеграм']) или ('', [])."""
    low = text.lower().strip().replace('ё', 'е')
    low = re.sub(r'\s+', ' ', low)
    checks = [
        ('hung_kill', HUNG_KILL_RE), ('auto_off', AUTO_OFF_RE), ('auto_on', AUTO_ON_RE), ('auto_list', AUTO_LIST_RE),
        ('upgrade_all', UPGRADE_ALL_RE), ('install', INSTALL_RE), ('uninstall', UNINSTALL_RE),
        ('windows', WINDOWS_RE), ('min_all', MIN_ALL_RE), ('restore_all', RESTORE_ALL_RE),
        ('monitor', MONITOR_RE), ('split', SPLIT_RE), ('snap_two', SNAP_TWO_RE), ('snap_one', SNAP_ONE_RE),
        ('restart', RESTART_RE), ('focus', FOCUS_RE), ('minimize', MINIMIZE_RE), ('maximize', MAXIMIZE_RE),
        ('hung_list', HUNG_LIST_RE), ('upgrade_one', UPGRADE_ONE_RE),
    ]
    for action, pattern in checks:
        m = re.search(pattern.replace('ё', 'е'), low)
        if not m:
            continue
        if action in ('snap_two',):
            return action, [m.group(1).strip(), m.group(2), m.group(3).strip(), m.group(4)]
        if action == 'split':
            return action, [m.group(1).strip(), m.group(2).strip()]
        if action == 'snap_one':
            return action, [m.group(1).strip(), m.group(2)]
        if action == 'monitor':
            return action, [m.group(1).strip(), m.group(2)]
        if action in ('windows', 'min_all', 'restore_all', 'upgrade_all', 'hung_list', 'hung_kill', 'auto_list'):
            return action, []
        target = _group(m)
        # «обнови страницу», «сверни разговор» — не программы: цель должна
        # быть похожа на имя программы или открытого окна (проверит apps_answer)
        return action, [target]
    return '', []


def _names(wins: list[dict]) -> str:
    labels = []
    for w in wins:
        label = app_label(w)
        if label not in labels:
            labels.append(label)
    return ', '.join(labels)


def apps_answer(text: str) -> dict:
    # «сверни это», «закрой вкладку» — раньше именованных окон: «это» в
    # MINIMIZE_RE иначе искалось бы как программа с таким названием
    this = this_answer(text)
    if this:
        return this
    action, args = match_apps(text)
    if not action:
        return {'ok': False, 'known': False}

    # ------------------------------------------------------------- окна --
    if action == 'windows':
        wins = [w for w in windows() if not own_window(w)]
        if not wins:
            return {'ok': True, 'known': True, 'say': 'Кроме Cloud HDR, ничего не открыто.'}
        groups: dict[str, list[dict]] = {}
        for w in wins:
            groups.setdefault(app_label(w), []).append(w)
        lines = []
        for label, items in groups.items():
            extra = ' (свёрнуто)' if all(i['minimized'] for i in items) else ''
            hung = ' — не отвечает' if any(i['hung'] for i in items) else ''
            count = f' · {len(items)} окна' if len(items) > 1 else ''
            lines.append(f'{label}{count}{extra}{hung}')
        return {'ok': True, 'known': True, 'say': f'Открыто программ: {len(groups)}.', 'text': '\n'.join(lines)}

    if action == 'min_all':
        wins = [w for w in windows() if not own_window(w) and not w['minimized']]
        for w in wins:
            user32.ShowWindow(w['hwnd'], SW_SHOWMINNOACTIVE)
        STATE['minimized'] = [w['hwnd'] for w in wins]
        if not wins:
            return {'ok': True, 'known': True, 'say': 'Сворачивать нечего — всё уже свёрнуто.'}
        return {'ok': True, 'known': True, 'say': f'Свернул {len(wins)} {plural(len(wins), "окно", "окна", "окон")}. '
                                                  'Cloud HDR оставил на виду. Вернуть — «разверни всё обратно».'}

    if action == 'restore_all':
        handles = STATE.get('minimized') or [w['hwnd'] for w in windows() if w['minimized'] and not own_window(w)]
        for hwnd in handles:
            user32.ShowWindow(hwnd, SW_RESTORE)
        STATE['minimized'] = []
        return {'ok': True, 'known': True, 'say': f'Вернул {len(handles)} {plural(len(handles), "окно", "окна", "окон")}.' if handles
                else 'Свёрнутых окон нет.'}

    if action in ('focus', 'minimize', 'maximize'):
        wins = find_windows(args[0])
        if not wins:
            # «перейди на сайт…», «сверни разговор» — не окно программы: пусть
            # фразу разбирает агент или модель
            return {'ok': False, 'known': False}
        target = sorted(wins, key=lambda w: w['minimized'])[0]
        label = app_label(target)
        if action == 'focus':
            focus(target['hwnd'])
            return {'ok': True, 'known': True, 'say': f'Переключился на {label}.'}
        if action == 'minimize':
            for w in wins:
                user32.ShowWindow(w['hwnd'], SW_MINIMIZE)
            return {'ok': True, 'known': True, 'say': f'Свернул {label}.'}
        user32.ShowWindow(target['hwnd'], SW_MAXIMIZE)
        focus(target['hwnd'])
        return {'ok': True, 'known': True, 'say': f'Развернул {label} на весь экран.'}

    if action in ('snap_two', 'split'):
        if action == 'split':
            first, second, side_a = args[0], args[1], 'left'
        else:
            first, side_a, second = args[0], _side(args[1]), args[2]
        a, b = find_windows(first), find_windows(second)
        missing = [q for q, w in ((first, a), (second, b)) if not w]
        if missing:
            return {'ok': False, 'known': True, 'say': f'Не нашёл открытое окно: {", ".join(missing)}. Сначала откройте программу.'}
        wa, wb = a[0], b[0]
        work = monitor_of(wa['hwnd'])
        snap(wa['hwnd'], side_a, work)
        snap(wb['hwnd'], 'right' if side_a == 'left' else 'left', work)
        focus(wb['hwnd'])
        focus(wa['hwnd'])
        left, right = (wa, wb) if side_a == 'left' else (wb, wa)
        return {'ok': True, 'known': True, 'say': f'Готово: {app_label(left)} слева, {app_label(right)} справа.'}

    if action == 'snap_one':
        wins = find_windows(args[0])
        if not wins:
            return {'ok': False, 'known': False}
        side = _side(args[1])
        snap(wins[0]['hwnd'], side)
        focus(wins[0]['hwnd'])
        return {'ok': True, 'known': True, 'say': f'Поставил {app_label(wins[0])} {"в левую" if side == "left" else "в правую"} половину экрана.'}

    if action == 'monitor':
        wins = find_windows(args[0])
        if not wins:
            return {'ok': False, 'known': True, 'say': f'Не нашёл открытое окно «{args[0]}».'}
        by_x = monitors()
        if len(by_x) < 2:
            return {'ok': False, 'known': True, 'say': 'Монитор у компьютера один — переносить некуда.'}
        # Нумерация как у человека: «первый» — основной (где панель задач и
        # «Пуск»), «второй» — следующий. Слева направо считают только «левый»
        # и «правый»: вертикальный монитор слева «первым» никто не называет.
        mons = sorted(by_x, key=lambda m: not m['primary'])
        hwnd = wins[0]['hwnd']
        current = next((i for i, m in enumerate(mons) if m['work'] == monitor_of(hwnd)), 0)
        word = args[1]
        if word == 'левый':
            target = by_x[0]
        elif word == 'правый':
            target = by_x[-1]
        else:
            index = {'первый': 0, 'основной': 0, 'главный': 0, 'второй': 1, 'третий': 2}.get(word)
            if index is None or index == current or index >= len(mons):
                index = (current + 1) % len(mons)
            target = mons[index]
        if target['work'] == monitor_of(hwnd):
            return {'ok': True, 'known': True, 'say': f'{app_label(wins[0])} уже на этом мониторе.'}
        move_to_monitor(hwnd, target)
        focus(hwnd)
        where = 'основной монитор' if target['primary'] else 'второй монитор' if len(mons) == 2 else f'монитор {mons.index(target) + 1}'
        return {'ok': True, 'known': True, 'say': f'Перенёс {app_label(wins[0])} на {where}.'}

    if action == 'restart':
        if not find_windows(args[0]):
            return {'ok': False, 'known': False}
        answer = restart(args[0])
        return {'known': True, **answer}

    if action == 'hung_list':
        hung = [w for w in windows() if w['hung']]
        if not hung:
            return {'ok': True, 'known': True, 'say': 'Зависших программ нет — все окна отвечают.'}
        return {'ok': True, 'known': True, 'say': f'Не отвечает: {_names(hung)}. Закрыть их?', 'hung': _names(hung),
                'confirm': 'hung_kill'}

    if action == 'hung_kill':
        hung = [w for w in windows() if w['hung']]
        if not hung:
            return {'ok': True, 'known': True, 'say': 'Зависших программ нет — закрывать нечего.'}
        killed = set()
        for w in hung:
            try:
                psutil.Process(w['pid']).kill()
                killed.add(app_label(w))
            except psutil.Error:
                pass
        return {'ok': bool(killed), 'known': True, 'say': f'Закрыл зависшие: {", ".join(sorted(killed))}.' if killed
                else 'Не получилось закрыть — у программы больше прав, чем у Cloud HDR.'}

    # -------------------------------------------------------- автозагрузка --
    if action == 'auto_list':
        items = autostart()
        if not items:
            return {'ok': True, 'known': True, 'say': 'В автозагрузке пусто.'}
        on = [i for i in items if i['enabled']]
        lines = [f'{"●" if i["enabled"] else "○"} {autostart_label(i)}' for i in sorted(items, key=lambda i: (not i['enabled'], autostart_label(i).lower()))]
        return {'ok': True, 'known': True, 'say': f'Вместе с Windows запускается {len(on)} из {len(items)}. '
                                                  'Убрать — «убери <программу> из автозагрузки».', 'text': '\n'.join(lines)}

    if action in ('auto_off', 'auto_on'):
        found = find_autostart(args[0])
        if not found:
            return {'ok': False, 'known': True, 'say': f'В автозагрузке нет «{args[0]}». Список — «что в автозагрузке».'}
        enable = action == 'auto_on'
        done, admin = [], False
        for item in found:
            ok, why = set_autostart(item, enable)
            if ok:
                done.append(autostart_label(item))
            elif why == 'admin':
                admin = True
        if admin and not done:
            subprocess.Popen(['taskmgr', '/7', '/startup'], creationflags=NO_WINDOW)
            return {'ok': False, 'known': True, 'say': 'Эту запись Windows разрешает менять только администратору — открыл «Автозагрузку» '
                                                       'в Диспетчере задач: там переключатель в правом столбце.'}
        name = ', '.join(dict.fromkeys(done))
        if enable:
            return {'ok': True, 'known': True, 'say': f'Вернул {name} в автозагрузку.'}
        return {'ok': True, 'known': True, 'say': f'Убрал {name} из автозагрузки — со следующего входа в Windows не запустится. '
                                                  f'Передумаете — «верни {args[0]} в автозагрузку».'}

    # ------------------------------------------------------------ winget --
    if action == 'upgrade_all':
        items = upgrades()
        if not items:
            return {'ok': True, 'known': True, 'say': 'Все программы, которые знает winget, свежие — обновлять нечего.'}
        lines = [f'{i["name"]} — {i["version"]} → {i["available"]}' for i in items]
        return {'ok': True, 'known': True, 'say': f'Можно обновить {len(items)} {plural(len(items), "программу", "программы", "программ")}.',
                'text': '\n'.join(lines), 'confirm': 'upgrade_all', 'count': len(items)}

    if action == 'install':
        target = args[0]
        if re.fullmatch(r'(таймер|будильник|напоминани\w*|пароль|обои|тему|яркость)', target):
            return {'ok': False, 'known': False}
        packages = search_packages(target)
        if not packages:
            return {'ok': False, 'known': True, 'say': f'В каталоге winget не нашлось «{target}». Скажите название иначе — например, как на сайте программы.'}
        return {'ok': True, 'known': True, 'say': f'Нашёл в каталоге winget. Что ставим?',
                'packages': [{'name': p.get('name'), 'id': p.get('id'), 'version': p.get('version')} for p in packages[:3]],
                'confirm': 'install'}

    if action == 'upgrade_one':
        target = args[0]
        if re.fullmatch(r'(страниц\w*|список|данные|чат|все|всё)', target):
            return {'ok': False, 'known': False}
        items = [i for i in upgrades() if name_score(winget_query(target), i['name'], i['id'].split('.')[-1]) >= 60]
        if not items:
            return {'ok': False, 'known': False}
        item = items[0]
        started = JOB.start('upgrade', item['name'], ['upgrade', '--id', item['id'], '-e', '--silent'])
        return {'ok': started['ok'], 'known': True, 'say': f'Обновляю {item["name"]} до {item["available"]}…' if started['ok'] else started['error'],
                'job': started.get('job')}

    if action == 'uninstall':
        target = args[0]
        found = installed(target)
        if not found:
            return {'ok': False, 'known': True, 'say': f'Не нашёл установленную программу «{target}».'}
        item = found[0]
        return {'ok': True, 'known': True, 'say': f'Удалить {item["name"]}? Её данные в профиле (настройки, кэш) программа может оставить.',
                'package': {'name': item['name'], 'id': item['id']}, 'confirm': 'uninstall'}

    return {'ok': False, 'known': False}


def run_confirmed(kind: str, package: dict | None = None) -> dict:
    """Нажата кнопка в карточке: установить, обновить всё, удалить, закрыть зависшие."""
    if kind == 'install' and package:
        return JOB.start('install', package['name'], ['install', '--id', package['id'], '-e', '--source', 'winget', '--silent'])
    if kind == 'uninstall' and package:
        return JOB.start('uninstall', package['name'], ['uninstall', '--id', package['id'], '-e', '--silent'])
    if kind == 'upgrade_all':
        return JOB.start('upgrade_all', 'все программы', ['upgrade', '--all', '--silent', '--include-unknown'])
    if kind == 'hung_kill':
        answer = apps_answer('закрой зависшие')
        return {'ok': answer.get('ok', False), 'say': answer.get('say')}
    return {'ok': False, 'error': 'Не понял, что сделать.'}


STATE: dict = {}


def plural(n: int, one: str, few: str, many: str) -> str:
    a, b = n % 10, n % 100
    if a == 1 and b != 11:
        return one
    if 2 <= a <= 4 and not 12 <= b <= 14:
        return few
    return many
