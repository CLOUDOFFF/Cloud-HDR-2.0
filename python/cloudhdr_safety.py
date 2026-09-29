"""
Cloud HDR — какие папки и файлы нельзя трогать.

Одно место для всех, кто что-то перемещает, переименовывает или удаляет:
разбор папок в Hub, действия с файлами по всему компьютеру. Правило простое —
лучше отказать и объяснить, чем сломать Windows или чужую программу.

Раньше такой проверки не было вовсе: «разбери C:\\Windows» строил план
разложить explorer.exe, notepad.exe и regedit.exe по папкам «Программы», и
одна кнопка отделяла человека от неработающей системы.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

NO_WINDOW = 0x08000000


# ============================================================ источники ===
#
# Службы Cloud HDR слушают 127.0.0.1, но это не защищает от браузера: любой
# сайт, открытый на этом компьютере, может слать запросы на 127.0.0.1. Раньше
# все службы отвечали с «Access-Control-Allow-Origin: *», и страница с
# example.com читала буфер обмена, получала ключ телефона и токен агента.
# Теперь браузерные запросы принимаются только со страницы самого приложения.

APP_PORT = int(os.environ.get('CLOUDHDR_AGENT_PORT', '4477'))
APP_ORIGINS = {f'http://127.0.0.1:{APP_PORT}', f'http://localhost:{APP_PORT}'}


def caller_allowed(headers, own_port: int, extra_origins: set[str] | None = None) -> tuple[bool, str | None]:
    """(можно ли, Origin для ответа).

    Без Origin — программа, а не браузер (PowerShell, быстрая строка): можно.
    Host проверяется всегда: подмена DNS (чужое имя, которое смотрит на
    127.0.0.1) даёт странице «свой» источник, но Host у неё чужой.
    """
    host = (headers.get('host') or '').lower()
    if host and host not in (f'127.0.0.1:{own_port}', f'localhost:{own_port}') and not (extra_origins and f'http://{host}' in extra_origins):
        return False, None
    origin = headers.get('origin')
    if not origin:
        return True, None
    # своя страница службы (окно записи голоса на 4482) — тоже своя
    own = {f'http://127.0.0.1:{own_port}', f'http://localhost:{own_port}'}
    if origin.lower() in APP_ORIGINS | own or (extra_origins and origin.lower() in extra_origins):
        return True, origin
    return False, None


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).resolve() if value else None


def _system_roots() -> list[Path]:
    roots = [_env_path('SystemRoot') or Path('C:/Windows'),
             _env_path('ProgramFiles'), _env_path('ProgramFiles(x86)'), _env_path('ProgramW6432'),
             _env_path('ProgramData'), _env_path('APPDATA'), _env_path('LOCALAPPDATA')]
    drive = (_env_path('SystemDrive') or Path('C:/'))
    roots += [Path(str(drive) + '\\') / name for name in ('$Recycle.Bin', 'System Volume Information', 'Recovery',
                                                          'Boot', 'EFI', 'PerfLogs', 'Config.Msi')]
    return [r for r in roots if r]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


_APP_ROOT = Path(__file__).resolve().parent.parent
OWN_PARTS = [(_APP_ROOT / part).resolve() for part in ('python', 'runtime', 'desktop', 'server', 'public', 'setup')
             if (_APP_ROOT / part).is_dir()]


def _user_home() -> Path:
    return Path(os.path.expanduser('~')).resolve()


def forbidden_reason(path: str | Path) -> str | None:
    """Почему с этим путём ничего нельзя делать; None — можно.

    Проверяется и сам путь, и всё, что выше него: папка внутри Program Files
    так же неприкосновенна, как Program Files.
    """
    try:
        target = Path(path).resolve()
    except OSError:
        return 'Не удалось разобрать путь.'
    if target == Path(target.anchor):
        return f'Это корень диска {target.anchor} — его целиком я не трогаю.'
    for root in _system_roots():
        if _is_within(target, root.resolve()):
            return f'«{target}» — системная папка или папка программ. Если её менять, Windows или программы перестанут работать.'
    # Сам Cloud HDR: веса модели, своя копия Python, окно. «Что занимает место»
    # честно находит best.pt на 2,8 ГБ — и кнопка «В корзину» рядом с ним
    # убивала бы модель одним нажатием.
    for own in OWN_PARTS:
        if _is_within(target, own):
            return f'«{target.name}» — часть самого Cloud HDR (модель, службы или окно). Её я не трогаю.'
    home = _user_home()
    users = home.parent
    if target == users or (_is_within(target, users) and not _is_within(target, home)):
        return 'Это папка другого пользователя компьютера — её я не трогаю.'
    if target == home:
        return 'Это корень вашего профиля: там лежат системные файлы Windows. Назовите папку внутри — «Загрузки», «Документы»…'
    return None


def is_program_folder(folder: str | Path) -> bool:
    """Папка установленной программы или игры: рядом с exe лежат её библиотеки."""
    try:
        with os.scandir(folder) as entries:
            names = [e.name.lower() for e in entries if e.is_file(follow_symlinks=False)]
    except OSError:
        return False
    has_exe = any(n.endswith('.exe') for n in names)
    dlls = sum(n.endswith('.dll') for n in names)
    return has_exe and dlls >= 2 or any(n in ('unins000.exe', 'steam_api.dll', 'steam_api64.dll', 'unityplayer.dll') for n in names)


def project_files(folder: str | Path) -> set[str]:
    """Имена файлов верхнего уровня, которые принадлежат git-проекту в этой папке.

    Рабочий стол бывает сам проектом (у хозяина Cloud HDR так и есть): разбор
    «рабочего стола» не должен растаскивать README.md и Установить.cmd по
    папкам «Документы» и «Программы».
    """
    folder = Path(folder)
    if not (folder / '.git').exists():
        return set()
    try:
        out = subprocess.run(['git', '-C', str(folder), 'ls-files', '-z'], capture_output=True, timeout=10,
                             creationflags=NO_WINDOW).stdout.decode('utf-8', 'replace')
        tracked = {p for p in out.split('\0') if p and '/' not in p}
        if tracked:
            return tracked
    except (OSError, subprocess.SubprocessError):
        pass
    # git не нашёлся — берём то, что похоже на файлы проекта
    names = {e.name for e in os.scandir(folder) if e.is_file()}
    markers = ('readme', 'license', 'changelog', '.git', 'package.json', 'requirements', 'setup.', 'makefile')
    return {n for n in names if n.lower().startswith(markers) or n.lower().endswith(('.cmd', '.bat', '.ps1', '.py', '.js', '.md'))}
