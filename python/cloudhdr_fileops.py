"""
Cloud HDR — группа 4 интеграции с ПК: файлы по всему компьютеру.

Зовёт его служба умений (cloudhdr_hub.py, маршруты /hub/files/*). Правила:

  • удаляется ТОЛЬКО в корзину и ТОЛЬКО по кнопке «В корзину» — фраза лишь
    показывает, что именно будет удалено;
  • переименование, перенос, копирование, новая папка, архив выполняются
    сразу, но каждое можно вернуть кнопкой «Вернуть» (журнал OPS);
  • всё, что меняет файлы, проходит через cloudhdr_safety.forbidden_reason:
    системные папки, папки программ, корни дисков и чужие профили не трогаются;
  • окно присылает только номер действия или путь из предложенного списка, а
    служба перепроверяет его сама — пути из запроса на веру не берутся.

Поиск в два слоя: сначала живой обход своих папок (рабочий стол, загрузки,
документы… — там свежие файлы, которых нет в индексе), затем индекс имён
cloudhdr_files. Найденное всегда проверяется на существование.
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
import zipfile
from ctypes import wintypes
from pathlib import Path

from cloudhdr_files import levenshtein, loose
from cloudhdr_safety import forbidden_reason

NO_WINDOW = 0x08000000
HOME = Path(os.path.expanduser('~'))
INDEX = Path(__file__).resolve().parent / 'data' / 'files.db'


def _known(name: str) -> Path:
    """Путь известной папки с учётом переноса (Документы на D: и т. п.)."""
    guid = {'desktop': '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}', 'documents': '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}',
            'downloads': '{374DE290-123F-4565-9164-39C4925E467B}', 'pictures': '{33E28130-4E1E-4676-835A-98395C3BC3BB}',
            'music': '{4BD8D571-6D19-48D3-BE97-422220080E43}', 'videos': '{18989B1D-99B5-455B-841C-AB7C74E4DDFC}'}[name]
    try:
        ptr = ctypes.c_wchar_p()
        g = ctypes.create_string_buffer(uuid.UUID(guid).bytes_le)
        if ctypes.windll.shell32.SHGetKnownFolderPath(g, 0, None, ctypes.byref(ptr)) == 0:
            path = Path(ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(ptr)
            return path
    except (OSError, AttributeError):
        pass
    return HOME / name.capitalize()


FOLDERS = {name: _known(name) for name in ('desktop', 'documents', 'downloads', 'pictures', 'music', 'videos')}
FOLDER_WORDS = [
    (r'рабоч\w*\s+стол\w*|десктоп\w*', 'desktop'), (r'документ\w*', 'documents'), (r'загрузк\w*|скачанн\w*|downloads', 'downloads'),
    (r'изображени\w*|картинк\w*|фотографи\w*|pictures', 'pictures'), (r'музык\w*', 'music'), (r'видео\b', 'videos'),
]
FOLDER_LABEL = {'desktop': 'Рабочий стол', 'documents': 'Документы', 'downloads': 'Загрузки', 'pictures': 'Изображения',
                'music': 'Музыка', 'videos': 'Видео'}


def label_of(path: Path) -> str:
    for key, folder in FOLDERS.items():
        if path == folder:
            return FOLDER_LABEL[key]
    return path.name or str(path)


def human_size(n: float) -> str:
    for unit, size in (('ГБ', 1024 ** 3), ('МБ', 1024 ** 2), ('КБ', 1024)):
        if n >= size:
            value = n / size
            return (f'{value:.1f}' if value < 10 else f'{value:.0f}').replace('.', ',') + ' ' + unit
    return f'{int(n)} Б'


def plural(n: int, one: str, few: str, many: str) -> str:
    a, b = n % 10, n % 100
    if a == 1 and b != 11:
        return one
    if 2 <= a <= 4 and not 12 <= b <= 14:
        return few
    return many


def files_word(n: int) -> str:
    return f'{n} {plural(n, "файл", "файла", "файлов")}'


# ================================================================ поиск ===

def folder_in(text: str) -> Path | None:
    """«на рабочем столе», «в загрузках», «на диске D», «в D:\\Игры»."""
    low = text.lower()
    path = re.search(r'([a-z]:\\[^"<>|?*]*)', text, re.I)
    if path and Path(path.group(1).strip()).is_dir():
        return Path(path.group(1).strip())
    drive = re.search(r'(?:на\s+)?диск\w*\s+([a-zа-я])(?![а-яa-z])', low)
    if drive:
        letter = {'ц': 'c', 'д': 'd', 'е': 'e', 'ф': 'f'}.get(drive.group(1), drive.group(1))
        if Path(f'{letter.upper()}:\\').exists():
            return Path(f'{letter.upper()}:\\')
    for pattern, key in FOLDER_WORDS:
        if re.search(pattern, low):
            return FOLDERS[key]
    return None


def strip_place(text: str) -> str:
    """Убрать из имени указание места: «отчёт на рабочем столе» → «отчёт»."""
    value = re.sub(r'\s+(?:на|в|во|из|с|со)\s+(?:папк\w+\s+)?(?:рабоч\w*\s+стол\w*|десктоп\w*|документ\w*|загрузк\w*|'
                   r'изображени\w*|картинк\w*|музык\w*|видео|диск\w*\s+\w|[a-z]:\\\S*)\s*$', '', text, flags=re.I)
    return value.strip()


def clean_name(text: str) -> str:
    value = text.strip().strip('«»"\'').strip()
    value = re.sub(r'^(?:файл|файлы|папку|папка|документ|фото|фотографию|картинку|видео|архив|презентацию|таблицу)\s+', '', value, flags=re.I)
    value = re.sub(r'\s+(?:пожалуйста|плиз)$', '', value, flags=re.I)
    return value.strip().strip('«»"\'').strip()


def _score(query: str | tuple, path: Path) -> int:
    # Несколько прочтений запроса: «фото кота» — это и «кота» (фото как тип
    # файла), и имя «фото кота.png» целиком. Берём лучшее.
    if isinstance(query, tuple):
        return max((_score(q, path) for q in query), default=0)
    q = loose(query)
    if not q:
        return 0
    name, stem = loose(path.name), loose(path.stem)
    if q in (name, stem):
        return 100
    if stem.startswith(q) or name.startswith(q):
        return 85
    if len(q) >= 3 and q in name:
        return 70
    if len(q) >= 5 and levenshtein(q, stem[:len(q) + 1]) <= max(1, len(q) // 5):
        return 55
    return 0


def _walk(root: Path, depth: int, deadline: float):
    stack = [(root, 0)]
    while stack and time.time() < deadline:
        folder, level = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.startswith(('.', '$')) or entry.name.lower() in ('appdata', 'node_modules', '__pycache__'):
                        continue
                    yield entry
                    if level < depth and entry.is_dir(follow_symlinks=False):
                        stack.append((Path(entry.path), level + 1))
        except OSError:
            continue


def find(query: str, scope: Path | None = None, want: str = 'any', limit: int = 6) -> list[Path]:
    """Файлы и папки по имени. want: any | file | dir."""
    full = query.strip().strip('«»"\'').strip()
    query = clean_name(query)
    if not query:
        return []
    readings = tuple(dict.fromkeys((query, full)))
    explicit = re.match(r'^[a-z]:\\', query, re.I)
    if explicit:
        path = Path(query)
        return [path] if path.exists() else []
    scored: dict[Path, int] = {}
    deadline = time.time() + 2.5
    roots = [scope] if scope else [FOLDERS[k] for k in ('desktop', 'downloads', 'documents', 'pictures', 'videos', 'music')]
    for root in roots:
        if not root.exists():
            continue
        for entry in _walk(root, 3 if scope else 2, deadline):
            path = Path(entry.path)
            is_dir = entry.is_dir(follow_symlinks=False)
            if want == 'file' and is_dir or want == 'dir' and not is_dir:
                continue
            value = _score(readings, path)
            if value:
                scored[path] = max(scored.get(path, 0), value + 5)     # свои папки — первыми
    if not scope and len([v for v in scored.values() if v >= 85]) == 0 and INDEX.exists():
        try:
            import cloudhdr_files
            for item in cloudhdr_files.search(query, str(INDEX), limit=25):
                path = Path(item['path'])
                if not path.exists() or (want == 'file' and item['is_dir']) or (want == 'dir' and not item['is_dir']):
                    continue
                if forbidden_reason(path):          # в папках программ человек файлы не ищет
                    continue
                value = _score(readings, path)
                if value:
                    scored[path] = max(scored.get(path, 0), value)
        except Exception:  # noqa: BLE001 — индекс не обязателен
            pass
    if not scored:
        return []
    ranked = sorted(scored, key=lambda p: (-scored[p], len(str(p))))
    top = scored[ranked[0]]
    return [p for p in ranked if scored[p] >= top - 15][:limit]


# ============================================================= корзина ===

class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT), ('pFrom', wintypes.LPCWSTR), ('pTo', wintypes.LPCWSTR),
                ('fFlags', ctypes.c_uint16), ('fAnyOperationsAborted', wintypes.BOOL), ('hNameMappings', ctypes.c_void_p),
                ('lpszProgressTitle', wintypes.LPCWSTR)]


class SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [('cbSize', wintypes.DWORD), ('i64Size', ctypes.c_longlong), ('i64NumItems', ctypes.c_longlong)]


FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 0x4, 0x10, 0x40, 0x400


def to_bin(paths: list[Path]) -> tuple[bool, str]:
    """В корзину — именно в корзину: FOF_ALLOWUNDO, без безвозвратного удаления."""
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = '\0'.join(str(p) for p in paths) + '\0\0'
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if code or op.fAnyOperationsAborted:
        return False, f'Windows не дала убрать файл в корзину (код {code}) — возможно, он открыт в программе.'
    still = [p for p in paths if p.exists()]
    if still:
        return False, f'Не удалось убрать: {", ".join(p.name for p in still)} — файл занят.'
    return True, ''


def bin_info() -> tuple[int, int]:
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    ctypes.windll.shell32.SHQueryRecycleBinW(None, ctypes.byref(info))
    return int(info.i64NumItems), int(info.i64Size)


def empty_bin() -> bool:
    return ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4) in (0, -2147418113)


def _ps(script: str, timeout: int = 40) -> str:
    full = '[Console]::OutputEncoding=[Text.Encoding]::UTF8;$ProgressPreference="SilentlyContinue";' + script
    try:
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', full],
                              capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return done.stdout.decode('utf-8', 'replace').strip()
    except (OSError, subprocess.SubprocessError):
        return ''


def bin_items() -> list[dict]:
    out = _ps("$b=(New-Object -ComObject Shell.Application).Namespace(10); foreach($i in $b.Items()){ "
              "'{0}|{1}|{2}' -f $i.Name, $i.ExtendedProperty('System.Recycle.DeletedFrom'), $i.Path }")
    items = []
    for line in out.splitlines():
        parts = line.split('|')
        if len(parts) == 3:
            items.append({'name': parts[0], 'from': parts[1], 'bin': parts[2]})
    return items


def restore(query: str) -> tuple[list[str], str]:
    """Вернуть из корзины по имени. Возвращает (что вернули, куда)."""
    q = loose(clean_name(query))
    if not q:
        return [], ''
    wanted = [i for i in bin_items() if loose(i['name']) == q or loose(Path(i['name']).stem) == q
              or (len(q) >= 3 and q in loose(i['name']))]
    if not wanted:
        return [], ''
    names = []
    for item in wanted[:10]:
        esc = item['bin'].replace("'", "''")
        _ps("$b=(New-Object -ComObject Shell.Application).Namespace(10); "
            f"$i=@($b.Items()) | Where-Object {{ $_.Path -eq '{esc}' }} | Select-Object -First 1; if($i){{ $i.InvokeVerb('undelete') }}")
        names.append(item['name'])
    return names, wanted[0]['from']


# ======================================================== журнал действий ===
#
# Каждое переименование, перенос, копирование, архив и новая папка ложатся
# сюда. Кнопка «Вернуть» присылает только номер — что именно откатывать,
# служба знает сама.

OPS: dict[str, dict] = {}
OPS_LOCK = threading.Lock()


def remember(op: dict) -> str:
    op_id = uuid.uuid4().hex[:10]
    with OPS_LOCK:
        OPS[op_id] = {**op, 'ts': time.time()}
        if len(OPS) > 60:
            for old in sorted(OPS, key=lambda k: OPS[k]['ts'])[:-60]:
                OPS.pop(old, None)
    return op_id


def undo(op_id: str) -> tuple[bool, str]:
    with OPS_LOCK:
        op = OPS.pop(op_id, None)
    if not op:
        return False, 'Это действие уже не вернуть — журнал живёт до перезапуска Cloud HDR.'
    kind = op['kind']
    try:
        if kind in ('rename', 'move'):
            src, dst = Path(op['to']), Path(op['from'])
            if dst.exists():
                return False, f'На старом месте уже есть «{dst.name}» — не перезаписываю.'
            shutil.move(str(src), str(dst))
            return True, f'Вернул «{dst.name}» на место.'
        if kind in ('copy', 'mkdir', 'zip', 'unzip'):
            made = Path(op['to'])
            if made.exists():
                ok, why = to_bin([made])
                if not ok:
                    return False, why
            return True, f'Убрал «{made.name}» в корзину.'
    except OSError as error:
        return False, f'Не получилось вернуть: {error}'
    return False, 'Не знаю, как это вернуть.'


def free_name(path: Path) -> Path:
    if not path.exists():
        return path
    stem, ext = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f'{stem} ({n}){ext}')
        if not candidate.exists():
            return candidate
        n += 1


def guard(*paths: Path) -> str | None:
    """Почему нельзя трогать; плюс сами известные папки целиком."""
    for path in paths:
        reason = forbidden_reason(path)
        if reason:
            return reason
        if path in FOLDERS.values():
            return f'«{label_of(path)}» — системная папка Windows, её саму не переношу и не удаляю. Назовите файл внутри неё.'
    return None


def do_rename(path: Path, new: str) -> dict:
    new = new.strip().strip('«»"\'').strip()
    if not new or re.search(r'[\\/:*?"<>|]', new):
        return {'ok': False, 'say': 'В имени файла не может быть символов \\ / : * ? " < > |.'}
    if not Path(new).suffix and path.is_file():
        new += path.suffix                      # «переименуй отчёт в итоги» — расширение оставляем
    target = path.with_name(new)
    why = guard(path, target)
    if why:
        return {'ok': False, 'say': why}
    if target.exists():
        return {'ok': False, 'say': f'Рядом уже есть «{target.name}» — выберите другое имя.'}
    path.rename(target)
    return {'ok': True, 'say': f'Переименовал «{path.name}» в «{target.name}».', 'undo': remember({'kind': 'rename', 'from': str(path), 'to': str(target)})}


def do_move(path: Path, dest: Path, copy: bool = False) -> dict:
    # копировать можно откуда угодно — меняется только то место, куда кладём
    why = (None if copy else guard(path)) or forbidden_reason(dest)
    if why:
        return {'ok': False, 'say': why}
    if not dest.is_dir():
        return {'ok': False, 'say': f'Папки «{dest}» нет.'}
    target = free_name(dest / path.name)
    if copy:
        if path.is_dir():
            shutil.copytree(path, target)
        else:
            shutil.copy2(path, target)
        verb = 'Скопировал'
    else:
        shutil.move(str(path), str(target))
        verb = 'Перенёс'
    return {'ok': True, 'say': f'{verb} «{path.name}» в «{label_of(dest)}».',
            'undo': remember({'kind': 'copy' if copy else 'move', 'from': str(path), 'to': str(target)}), 'path': str(target)}


def do_mkdir(name: str, where: Path) -> dict:
    name = name.strip().strip('«»"\'').strip() or 'Новая папка'
    if re.search(r'[\\/:*?"<>|]', name):
        return {'ok': False, 'say': 'В имени папки не может быть символов \\ / : * ? " < > |.'}
    why = forbidden_reason(where)
    if why:
        return {'ok': False, 'say': why}
    target = free_name(where / name)
    target.mkdir(parents=False)
    return {'ok': True, 'say': f'Создал папку «{target.name}» в «{label_of(where)}».', 'path': str(target),
            'undo': remember({'kind': 'mkdir', 'to': str(target)})}


def do_zip(path: Path) -> dict:
    why = forbidden_reason(path.parent)
    if why:
        return {'ok': False, 'say': why}
    target = free_name(path.with_suffix('.zip') if path.is_file() else path.parent / (path.name + '.zip'))
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        if path.is_file():
            archive.write(path, path.name)
        else:
            for file in path.rglob('*'):
                if file.is_file():
                    archive.write(file, file.relative_to(path.parent))
    return {'ok': True, 'say': f'Упаковал в «{target.name}» — {human_size(target.stat().st_size)}.', 'path': str(target),
            'undo': remember({'kind': 'zip', 'to': str(target)})}


def do_unzip(path: Path) -> dict:
    why = forbidden_reason(path.parent)
    if why:
        return {'ok': False, 'say': why}
    target = free_name(path.parent / path.stem)
    if path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            root = target.resolve()
            for member in archive.namelist():
                # «../../Windows/…» внутри архива — классическая ловушка
                if not (root / member).resolve().is_relative_to(root):
                    return {'ok': False, 'say': 'В архиве пути, ведущие за пределы папки, — не распаковываю: это похоже на ловушку.'}
            archive.extractall(target)
    else:
        seven = next((p for p in (Path(os.environ.get('ProgramFiles', '')) / '7-Zip' / '7z.exe',
                                  Path(os.environ.get('ProgramFiles(x86)', '')) / '7-Zip' / '7z.exe') if p.exists()), None)
        if not seven:
            return {'ok': False, 'say': f'Архивы {path.suffix} распаковываю через 7-Zip, а его нет. Скажите «установи 7-zip».'}
        done = subprocess.run([str(seven), 'x', str(path), f'-o{target}', '-y'], capture_output=True, creationflags=NO_WINDOW)
        if done.returncode != 0:
            return {'ok': False, 'say': '7-Zip не смог распаковать архив — возможно, он повреждён или с паролем.'}
    return {'ok': True, 'say': f'Распаковал в папку «{target.name}».', 'path': str(target),
            'undo': remember({'kind': 'unzip', 'to': str(target)})}


# ============================================================ обзоры ===

def recent_downloads(limit: int = 6) -> list[Path]:
    folder = FOLDERS['downloads']
    try:
        entries = [Path(e.path) for e in os.scandir(folder) if e.is_file() and not e.name.lower().endswith(('.crdownload', '.part', '.tmp', 'desktop.ini'))]
    except OSError:
        return []
    return sorted(entries, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def size_of(path: Path, budget: float = 8.0) -> tuple[int, int, bool]:
    """(байт, файлов, досчитано ли) — с пределом по времени."""
    if path.is_file():
        return path.stat().st_size, 1, True
    total, count, deadline = 0, 0, time.time() + budget
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
                count += 1
            except OSError:
                pass
        if time.time() > deadline:
            return total, count, False
    return total, count, True


def space_report(budget: float = 12.0) -> dict:
    """Что занимает место в профиле: крупные папки и самые большие файлы."""
    deadline = time.time() + budget
    folders: dict[Path, int] = {}
    big: list[tuple[int, Path]] = []
    for top in sorted(p for p in HOME.iterdir() if p.is_dir() and not p.name.startswith(('.', '$')) and p.name.lower() != 'appdata'):
        size = 0
        for root, dirs, files in os.walk(top, onerror=lambda e: None):
            dirs[:] = [d for d in dirs if not d.startswith(('.', '$')) and d.lower() not in ('node_modules', '__pycache__')]
            for name in files:
                full = os.path.join(root, name)
                try:
                    s = os.path.getsize(full)
                except OSError:
                    continue
                size += s
                if s >= 100 * 1024 ** 2:
                    big.append((s, Path(full)))
            if time.time() > deadline:
                break
        folders[top] = size
        if time.time() > deadline:
            break
    big.sort(reverse=True)
    count, bin_size = bin_info()
    return {'folders': sorted(folders.items(), key=lambda kv: -kv[1])[:6], 'big': big[:8],
            'bin': (count, bin_size), 'complete': time.time() <= deadline}


def duplicates(folder: Path, budget: float = 20.0) -> list[list[Path]]:
    """Одинаковые файлы: сначала по размеру, потом по содержимому."""
    by_size: dict[int, list[Path]] = {}
    deadline = time.time() + budget
    for root, _dirs, files in os.walk(folder, onerror=lambda e: None):
        for name in files:
            full = Path(root) / name
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size >= 4096:
                by_size.setdefault(size, []).append(full)
        if time.time() > deadline:
            break
    groups = []
    for size, paths in by_size.items():
        if len(paths) < 2 or size > 4 * 1024 ** 3:
            continue
        by_hash: dict[str, list[Path]] = {}
        for path in paths:
            h = hashlib.blake2b(digest_size=16)
            try:
                with open(path, 'rb') as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        h.update(chunk)
            except OSError:
                continue
            by_hash.setdefault(h.hexdigest(), []).append(path)
        for same in by_hash.values():
            if len(same) > 1:
                # оригинал — самый старый; остальные предлагаем убрать
                groups.append(sorted(same, key=lambda p: p.stat().st_mtime))
        if time.time() > deadline:
            break
    groups.sort(key=lambda g: -g[0].stat().st_size * (len(g) - 1))
    return groups


# ============================================================ разговор ===

N = r'(?:файл\w*\s+|папк\w+\s+|документ\s+|фото\w*\s+|картинк\w+\s+|видео\s+|архив\s+)?'
DELETE_RE = r'^(?:пожалуйста\s+)?(?:удали|сотри|выкинь|выброси|(?:убери|отправь|перемести|кинь)\s+в\s+корзину)\s+(' + N + r'.+?)(?:\s+в\s+корзину)?[.!?]*$'
RESTORE_RE = r'(?:верни|восстанови|достань)\s+(?:из\s+корзины\s+)?(.+?)\s+из\s+корзины|^(?:восстанови|верни)\s+(?:удал\w+\s+)(?:файл\w*\s+|папку\s+)?(.+?)[.!?]*$'
BIN_EMPTY_RE = r'(?:очисти|опустоши|почисти|освободи)\s+корзину'
BIN_INFO_RE = r'(?:что|сколько|много\s+ли)\s+(?:\w+\s+)?(?:лежит\s+|в\s+)?корзин'
RENAME_RE = r'^(?:пожалуйста\s+)?переименуй\s+(' + N + r'.+?)\s+в\s+(.+?)[.!?]*$'
MOVE_RE = r'^(?:пожалуйста\s+)?(перемести|перенеси|переложи|скопируй|копируй)\s+(' + N + r'.+?)\s+(?:в|на)\s+(.+?)[.!?]*$'
MKDIR_RE = r'^(?:пожалуйста\s+)?(?:создай|сделай)\s+(?:новую\s+)?папку(?:\s+(?:с\s+названием|под\s+названием|с\s+именем))?\s*(.*?)[.!?]*$'
RECENT_RE = r'(?:что|какие\s+файлы)\s+(?:я\s+)?(?:недавно|последн\w*|сегодня|только\s+что)\s+скача\w*|последни\w+\s+(?:загрузк\w*|скачанн\w*)|недавн\w+\s+(?:файлы|загрузки)|что\s+нового\s+в\s+загрузках'
SPACE_RE = r'(?:что|какие\s+файлы|какие\s+папки)\s+(?:\w+\s+)?занима\w*\s+(?:больше\s+всего\s+|много\s+)?мест|самые\s+(?:большие|тяж[её]лые)\s+(?:файлы|папки)|(?:куда|на\s+что)\s+(?:\w+\s+)?(?:делось|ушло|уходит)\s+место|чем\s+(?:забит|занят)\s+(?:диск|компьютер)'
SIZE_RE = r'сколько\s+(?:весит|занимает)\s+(?:места\s+)?(' + N + r'.+?)[.!?]*$|(?:размер|вес)\s+(?:папки|файла)\s+(.+?)[.!?]*$'
DUP_RE = r'(?:найди|покажи|есть\s+ли|поищи|убери)\s+(?:\w+\s+)?(?:дубликат\w*|дубл\w*|повтор\w*|одинаков\w+\s+файл\w*|копии\s+файлов)(?:\s+(?:в|на)\s+(.+?))?[.!?]*$'
ZIP_RE = r'^(?:пожалуйста\s+)?(?:сожми|заархивируй|упакуй|запакуй)\s+(' + N + r'.+?)(?:\s+в\s+(?:zip|зип|архив))?[.!?]*$'
UNZIP_RE = r'^(?:пожалуйста\s+)?(?:распакуй|разархивируй|извлеки)\s+(?:архив\s+)?(.+?)[.!?]*$'
OPEN_RE = r'^(?:пожалуйста\s+)?(?:открой|запусти|покажи)\s+(?:мне\s+)?(файл|документ|фото\w*|картинк\w+|таблиц\w+|презентаци\w+|pdf|пдф)\s+(.+?)[.!?]*$'
REVEAL_RE = r'(?:где\s+(?:лежит|находится|хранится|сохранился)|покажи\s+(?:в\s+папке|где\s+лежит))\s+(?:мой\s+|моя\s+|мое\s+|мои\s+)?(' + N + r'.+?)[.!?]*$'
FOLDER_OPEN_RE = r'^(?:пожалуйста\s+)?открой\s+(?:мне\s+)?папку\s+(.+?)[.!?]*$'


def match_files(text: str) -> tuple[str, list[str]]:
    low = re.sub(r'\s+', ' ', text.strip().lower().replace('ё', 'е'))
    for action, pattern in (('bin_empty', BIN_EMPTY_RE), ('bin_info', BIN_INFO_RE), ('restore', RESTORE_RE),
                            ('recent', RECENT_RE), ('space', SPACE_RE), ('dups', DUP_RE), ('size', SIZE_RE),
                            ('rename', RENAME_RE), ('mkdir', MKDIR_RE), ('zip', ZIP_RE), ('unzip', UNZIP_RE),
                            ('move', MOVE_RE), ('delete', DELETE_RE), ('open', OPEN_RE), ('reveal', REVEAL_RE),
                            ('folder', FOLDER_OPEN_RE)):
        m = re.search(pattern, low)
        if not m:
            continue
        # имена берём из исходного текста: регистр важен для «Отчёт.docx»
        span_text = lambda i: text.strip()[m.start(i):m.end(i)] if m.group(i) else ''  # noqa: E731
        if action == 'move':
            return action, [m.group(1), span_text(2), span_text(3)]
        if action == 'rename':
            return action, [span_text(1), span_text(2)]
        if action == 'open':
            return action, [span_text(2)]
        groups = [span_text(i) for i in range(1, (m.lastindex or 0) + 1) if m.group(i)]
        return action, groups
    return '', []


# «удали напоминание», «удали программу» — не файлы; эти фразы разбирают
# соседние службы раньше, но и здесь не стоит на них реагировать.
NOT_FILES = re.compile(r'^(напоминани|таймер|программ|приложени|игру|сообщени|чат|истори|переписк|из\s+автозагрузк|'
                       r'последн\w+\s+сообщени|это|его|ее|её|их|все)', re.I)


def where_of(folder: Path) -> str:
    """«Документы › Проекты › 2026» — так два одноимённых файла различимы."""
    for key, root in FOLDERS.items():
        try:
            rest = folder.relative_to(root).parts
        except ValueError:
            continue
        parts = [FOLDER_LABEL[key], *rest]
        break
    else:
        parts = list(folder.parts)
    if len(parts) > 4:
        parts = [parts[0], '…', *parts[-2:]]
    return ' › '.join(p.rstrip('\\') for p in parts)


def _choices(paths: list[Path]) -> list[dict]:
    out = []
    for p in paths:
        try:
            size = p.stat().st_size if p.is_file() else None
        except OSError:
            size = None
        out.append({'path': str(p), 'name': p.name, 'where': where_of(p.parent), 'dir': p.is_dir(),
                    'size': human_size(size) if size is not None else ''})
    return out


def dest_folder(text: str) -> Path | None:
    place = folder_in(text)
    if place:
        return place
    name = clean_name(re.sub(r'^(?:папку|папка)\s+', '', text.strip(), flags=re.I))
    hits = find(name, want='dir', limit=1)
    return hits[0] if hits else None


def files_answer(text: str) -> dict:
    action, args = match_files(text)
    if not action:
        return {'ok': False, 'known': False}

    if action == 'bin_info':
        count, size = bin_info()
        if not count:
            return {'ok': True, 'known': True, 'say': 'Корзина пуста.'}
        return {'ok': True, 'known': True, 'say': f'В корзине {files_word(count)}, {human_size(size)}. '
                                                  'Вернуть файл — «верни <имя> из корзины», очистить — «очисти корзину».'}
    if action == 'bin_empty':
        count, size = bin_info()
        if not count:
            return {'ok': True, 'known': True, 'say': 'Корзина и так пуста.'}
        return {'ok': True, 'known': True, 'say': f'Очистить корзину? Там {files_word(count)}, {human_size(size)}. '
                                                  'Это уже насовсем — вернуть их потом будет нельзя.', 'confirm': 'bin_empty'}
    if action == 'restore':
        names, where = restore(args[0] if args else '')
        if not names:
            return {'ok': False, 'known': True, 'say': f'В корзине нет «{clean_name(args[0]) if args else ""}». Что там лежит — «что в корзине».'}
        return {'ok': True, 'known': True, 'say': f'Вернул из корзины: {", ".join(names)} — лежит снова в «{label_of(Path(where)) if where else "прежней папке"}».'}

    if action == 'recent':
        items = recent_downloads()
        if not items:
            return {'ok': True, 'known': True, 'say': 'В загрузках пусто.'}
        return {'ok': True, 'known': True, 'say': 'Последнее скачанное:', 'files': _choices(items), 'offer': 'open'}

    if action == 'space':
        report = space_report()
        lines = [f'{label_of(p)} — {human_size(s)}' for p, s in report['folders'] if s >= 1024 ** 2]
        # убрать в корзину предлагаем только то, что вообще можно трогать
        report['big'] = [(s, p) for s, p in report['big'] if not guard(p)]
        count, bin_size = report['bin']
        if bin_size > 500 * 1024 ** 2:
            lines.append(f'Корзина — {human_size(bin_size)} ({files_word(count)})')
        say = 'Больше всего места занимают:' + ('' if report['complete'] else ' (считал быстро, по основным папкам)')
        return {'ok': True, 'known': True, 'say': say, 'text': '\n'.join(lines),
                'files': _choices([p for _, p in report['big']]), 'offer': 'bin',
                'note': 'Самые большие файлы — любой можно убрать в корзину:' if report['big'] else ''}

    if action == 'size':
        target = args[0] if args else ''
        place = folder_in(target)
        paths = [place] if place and not strip_place(target).strip() else find(strip_place(target), scope=None)
        if not paths:
            return {'ok': False, 'known': True, 'say': f'Не нашёл «{clean_name(target)}».'}
        total, count, complete = size_of(paths[0])
        more = '' if complete else ' (не меньше — считать дальше слишком долго)'
        what = f'{files_word(count)}, ' if paths[0].is_dir() else ''
        return {'ok': True, 'known': True, 'say': f'«{label_of(paths[0])}»: {what}{human_size(total)}{more}.'}

    if action == 'dups':
        folder = folder_in(args[0]) if args else None
        folder = folder or FOLDERS['downloads']
        groups = duplicates(folder)
        if not groups:
            return {'ok': True, 'known': True, 'say': f'В «{label_of(folder)}» одинаковых файлов нет.'}
        extra = [p for g in groups for p in g[1:]]
        saved = sum(p.stat().st_size for p in extra)
        lines = [f'{g[0].name} — {len(g)} {plural(len(g), "копия", "копии", "копий")}' for g in groups[:12]]
        return {'ok': True, 'known': True, 'say': f'В «{label_of(folder)}» нашёл {len(groups)} {plural(len(groups), "файл", "файла", "файлов")} '
                                                  f'с копиями. Лишние копии занимают {human_size(saved)}; оригиналы (самые старые) останутся.',
                'text': '\n'.join(lines), 'confirm': 'bin_many', 'paths': [str(p) for p in extra], 'count': len(extra)}

    if action == 'mkdir':
        raw = args[0] if args else ''
        where = folder_in(raw) or FOLDERS['desktop']
        name = strip_place(raw) or 'Новая папка'
        return {'known': True, **do_mkdir(clean_name(name) or 'Новая папка', where)}

    if action == 'folder':
        target = args[0]
        place = folder_in(target)
        paths = [place] if place and not strip_place(target) else find(strip_place(target), scope=place, want='dir')
        if not paths:
            return {'ok': False, 'known': False}          # «открой папку игры» — пусть решает агент
        os.startfile(str(paths[0]))  # noqa: S606
        return {'ok': True, 'known': True, 'say': f'Открыл «{label_of(paths[0])}».'}

    # остальное — над конкретным файлом: найти его
    raw = args[1] if action == 'move' else args[0]
    if action != 'unzip' and NOT_FILES.match(clean_name(raw)):
        return {'ok': False, 'known': False}
    scope = folder_in(raw)
    name = strip_place(raw)
    # «удали папку документы», «перенеси загрузки на диск D» — сама известная
    # папка, а не что-то в ней: её не трогаем и говорим почему.
    if scope in FOLDERS.values() and action in ('delete', 'move', 'rename') and \
            re.fullmatch(r'(?:папк\w+\s+)?' + '|'.join(p for p, _ in FOLDER_WORDS).join('()'), clean_name(raw).lower()):
        return {'ok': False, 'known': True, 'say': guard(scope)}
    paths = find(name, scope=scope, want='file' if action in ('open', 'unzip') else 'any')
    explicit = bool(re.match(r'^(?:файл|папк|документ|фото|картинк|видео|архив)', raw.lower()))
    if not paths:
        if explicit or action in ('rename', 'move', 'zip', 'unzip', 'reveal', 'open'):
            return {'ok': False, 'known': True, 'say': f'Не нашёл «{clean_name(name)}». Скажите, где он лежит, — например, «на рабочем столе».'}
        return {'ok': False, 'known': False}

    if action == 'delete':
        paths = [p for p in paths if not guard(p)]
        if not paths:
            return {'ok': False, 'known': True, 'say': 'Такое я не удаляю: это системная папка или файл программы.'}
        one = len(paths) == 1
        return {'ok': True, 'known': True, 'say': f'Убрать в корзину «{paths[0].name}» из «{label_of(paths[0].parent)}»?' if one
                else 'Нашёл несколько — что убрать в корзину?', 'files': _choices(paths), 'offer': 'bin',
                'note': 'Из корзины можно вернуть: «верни <имя> из корзины».'}

    if len(paths) > 1 and action in ('rename', 'move', 'zip', 'unzip'):
        # Действие меняет файл — по догадке не выполняем: пусть выберут.
        return {'ok': True, 'known': True, 'say': 'Нашёл несколько похожих — с каким работать?', 'files': _choices(paths),
                'offer': action, 'arg': args[2] if action == 'move' else args[1] if action == 'rename' else '',
                'copy': action == 'move' and args[0].startswith(('скопир', 'копир'))}

    return run_on(action, paths[0], args)


def run_on(action: str, path: Path, args: list[str]) -> dict:
    try:
        if action == 'open':
            os.startfile(str(path))  # noqa: S606
            return {'ok': True, 'known': True, 'say': f'Открыл «{path.name}».'}
        if action == 'reveal':
            subprocess.Popen(['explorer.exe', '/select,', str(path)])
            return {'ok': True, 'known': True, 'say': f'«{path.name}» лежит в «{label_of(path.parent)}»: {path.parent}. Показал в Проводнике.'}
        if action == 'rename':
            return {'known': True, **do_rename(path, args[-1])}
        if action == 'move':
            dest = dest_folder(args[-1])
            if not dest:
                return {'ok': False, 'known': True, 'say': f'Не нашёл папку «{args[-1]}».'}
            return {'known': True, **do_move(path, dest, copy=args[0].startswith(('скопир', 'копир')))}
        if action == 'zip':
            return {'known': True, **do_zip(path)}
        if action == 'unzip':
            return {'known': True, **do_unzip(path)}
    except OSError as error:
        return {'ok': False, 'known': True, 'say': f'Windows не дала: {error.strerror or error}. Возможно, файл открыт в программе.'}
    return {'ok': False, 'known': False}


def act(body: dict) -> dict:
    """Нажата кнопка в карточке. Путь перепроверяется здесь, а не берётся на веру."""
    kind = str(body.get('kind') or '')
    if kind == 'undo':
        ok, say = undo(str(body.get('id') or ''))
        return {'ok': ok, 'say': say}
    if kind == 'bin_empty':
        count, size = bin_info()
        ok = empty_bin()
        return {'ok': ok, 'say': f'Очистил корзину — освободилось {human_size(size)}.' if ok else 'Windows не дала очистить корзину.'}
    raw = body.get('paths') if isinstance(body.get('paths'), list) else [body.get('path')]
    paths = [Path(str(p)) for p in raw if p]
    if not paths or any(not p.is_absolute() or not p.exists() for p in paths):
        return {'ok': False, 'say': 'Файла уже нет на месте.'}
    if kind == 'bin':
        for p in paths:
            why = guard(p)
            if why:
                return {'ok': False, 'say': why}
        ok, why = to_bin(paths)
        if not ok:
            return {'ok': False, 'say': why}
        names = paths[0].name if len(paths) == 1 else files_word(len(paths))
        return {'ok': True, 'say': f'Убрал в корзину: {names}. Вернуть — «верни {paths[0].stem} из корзины».'}
    if kind in ('open', 'reveal', 'rename', 'move', 'zip', 'unzip'):
        arg = str(body.get('arg') or '')
        args = (['скопируй' if body.get('copy') else 'перемести', '', arg] if kind == 'move' else ['', arg])
        return run_on(kind, paths[0], args)
    return {'ok': False, 'say': 'Не понял, что сделать.'}
