"""
Cloud HDR — поиск файлов по имени, включая русские названия английских файлов.

Задача, которую решает модуль: «найди файл клоуд мап» должно находить
`Cloud map_1_1.0.apk`. Ни один из прежних способов этого не делал, и вот почему.

Обход дисков на каждый запрос — медленно: даже с бюджетом в девять секунд он
успевает заглянуть в малую часть диска и находит не то, что есть, а то, до чего
дошёл. Поэтому здесь СНАЧАЛА строится индекс, один раз, а поиск идёт по нему и
занимает миллисекунды.

Второе — сопоставление. «Клоуд мап» после транслитерации даёт `kloudmap`, а файл
называется `cloudmap`. Расстояние между ними единица, но порог по короткому
запросу такую разницу не пропускает, и совпадения не было. Причина не в опечатке:
латинская `c` в русском читается то как «к», то как «с», и обратно однозначно не
переводится. В форме для сравнения (`loose`) эти различия стираются — вместе с
десятком других, которые ведут себя так же (ph/f, x/ks, y/i, w/v, сдвоенные).

Индекс держит не всё подряд. Системные каталоги, кеши и служебные расширения
пропускаются: они занимают львиную долю файлов на диске и никогда не бывают тем,
что человек ищет по имени.
"""

from __future__ import annotations

import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_INDEX = Path("data/files.db")

# ------------------------------------------------------------ нормализация --

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}

_NOT_WORD = re.compile(r"[^\w\d]", re.UNICODE)
_DOUBLED = re.compile(r"(.)\1+")


def comparable(value: str) -> str:
    """Нижний регистр без разделителей: «Cloud map_1.apk» → «cloudmap1apk»."""
    return _NOT_WORD.sub("", (value or "").lower().replace("ё", "е"))


def translit(value: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in (value or "").lower())


def loose(value: str) -> str:
    """
    Форма для нечёткого сравнения: стирает различия в написании одного и того же.

    Каждая замена здесь закрывает конкретный способ записать один звук:

        c → k      «клоуд» даёт kloud, а файл называется cloud — это и был
                   исходный промах, из-за которого «найди клоуд мап» ничего
                   не находило;
        ph → f     photoshop / фотошоп;
        x → ks     max / макс;
        w → v      windows / виндовс;
        y, j → i   yandex / яндекс, major / мажор;
        z → s      zoom / зум;
        сдвоенные  Garry's → гарис.

    Показывать эту форму пользователю нельзя — она нечитаемая.
    """
    text = comparable(translit(value))
    if not text:
        return ""
    text = text.replace("ph", "f").replace("ck", "k").replace("x", "ks")
    text = text.replace("c", "k").replace("w", "v")
    text = text.replace("y", "i").replace("j", "i").replace("z", "s")
    return _DOUBLED.sub(r"\1", text)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = row
    return previous[len(b)]


# ----------------------------------------------------------------- индекс ---

#: Каталоги, которые в индекс не попадают. Это не экономия ради экономии:
#: системные и служебные деревья дают большую часть файлов на диске и никогда
#: не бывают тем, что человек ищет по имени.
SKIP_DIRS = {
    "windows", "$recycle.bin", "system volume information", "programdata",
    "node_modules", ".git", ".svn", "__pycache__", "appdata", "temp", "tmp",
    "cache", "cache2", "caches", ".cache", "packages", "winsxs", "installer",
    "assembly", "driverstore", "servicing", "softwaredistribution",
    ".vscode", ".idea", "venv", ".venv", "site-packages", "dist-info",
}

#: Расширения, которые ищут по имени. Всё остальное — библиотеки, объектники,
#: временные файлы — в индекс не идёт.
KEEP_EXT = {
    # документы
    ".txt", ".doc", ".docx", ".pdf", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf",
    ".odt", ".ods", ".md", ".csv", ".json", ".xml", ".html", ".htm",
    # изображения и медиа
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".ico", ".psd",
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".mp4", ".avi", ".mkv", ".mov", ".webm",
    # архивы и установщики
    ".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".apk", ".exe", ".msi",
    # код и проекты
    ".py", ".js", ".ts", ".java", ".cs", ".cpp", ".c", ".h", ".php", ".rb", ".go",
    ".ps1", ".bat", ".sh", ".sql", ".ipynb",
    # прочее полезное
    ".lnk", ".url", ".torrent", ".epub", ".fb2", ".save", ".sav",
}


def default_roots() -> List[str]:
    """Профиль пользователя и несистемные диски — там живут файлы человека."""
    roots: List[str] = []
    profile = os.environ.get("USERPROFILE")
    if profile and Path(profile).exists():
        roots.append(profile)

    for letter in "DEFGH":
        drive = f"{letter}:\\"
        if Path(drive).exists():
            roots.append(drive)

    for guess in (r"C:\Games", r"C:\Program Files", r"C:\Program Files (x86)"):
        if Path(guess).exists():
            roots.append(guess)
    return roots


def _walk(roots: Iterable[str], max_files: int, on_progress=None):
    """Обход дисков с пропуском системных деревьев."""
    seen = 0
    for root in roots:
        for current, dirs, files in os.walk(root, topdown=True, onerror=lambda e: None):
            # Правка dirs НА МЕСТЕ — так os.walk не заходит внутрь пропущенных.
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith("$")]

            name = os.path.basename(current)
            if name:
                yield name, current, True, 0
                seen += 1

            for file in files:
                extension = os.path.splitext(file)[1].lower()
                if extension not in KEEP_EXT:
                    continue
                path = os.path.join(current, file)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                yield file, path, False, size
                seen += 1

                if seen >= max_files:
                    return
            if on_progress and seen % 5000 < len(files) + 1:
                on_progress(seen)


def build_index(out: str = str(DEFAULT_INDEX), roots: Optional[List[str]] = None,
                max_files: int = 400000, quiet: bool = False) -> dict:
    """Строит индекс имён. Один раз — дальше поиск идёт по нему."""
    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()

    connection = sqlite3.connect(str(path))
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute(
        "CREATE TABLE files (name TEXT, path TEXT, is_dir INTEGER, size INTEGER, loose TEXT)")

    started = time.time()
    batch: List[Tuple[str, str, int, int, str]] = []
    total = 0

    def progress(seen):
        if not quiet:
            print(f"  просмотрено: {seen}", flush=True)

    for name, full, is_dir, size in _walk(roots or default_roots(), max_files, progress):
        batch.append((name, full, 1 if is_dir else 0, size, loose(name)))
        total += 1
        if len(batch) >= 5000:
            connection.executemany("INSERT INTO files VALUES (?,?,?,?,?)", batch)
            batch.clear()

    if batch:
        connection.executemany("INSERT INTO files VALUES (?,?,?,?,?)", batch)
    connection.commit()
    connection.close()

    elapsed = time.time() - started
    return {"files": total, "seconds": round(elapsed, 1),
            "mb": round(path.stat().st_size / 1024 / 1024, 1), "path": str(path)}


# ------------------------------------------------------------------ поиск ---

_CACHE: Dict[str, list] = {}


def _load(index: str) -> list:
    """Полный список в память — нужен только для нечёткого прохода."""
    if index in _CACHE:
        return _CACHE[index]
    connection = sqlite3.connect(index)
    rows = connection.execute("SELECT name, path, is_dir, size, loose FROM files").fetchall()
    connection.close()
    _CACHE[index] = rows
    return rows


def _score(needle: str, name: str) -> int:
    if not needle or not name:
        return 0
    if name == needle:
        return 100
    if name.startswith(needle):
        return 92 - min(len(name) - len(needle), 20)
    if needle in name:
        # Совпадение внутри имени тем ценнее, чем меньше вокруг лишнего.
        return 80 - min((len(name) - len(needle)) // 3, 20)
    return 0


def _fuzzy_score(needle: str, name: str) -> int:
    """
    Нечёткое совпадение по ОКНУ имени, а не по имени целиком.

    Сравнивать запрос с полным именем бессмысленно: «клауд мап» (8 знаков) против
    «cloudmap_1_10apk» (16) даёт расстояние 8 при любой близости начал. Поэтому
    берётся кусок имени длиной с запрос, и расстояние считается с ним. Так
    «клауд мап» находит «Cloud map», отличаясь одной буквой, — а именно так
    название и слышат на слух.
    """
    if len(needle) < 4 or not name:
        return 0
    span = len(needle)
    allowed = max(1, span // 4)
    best = 99
    # Шаг в два знака: точное выравнивание тут не нужно, а проходов вдвое меньше.
    for start in range(0, max(1, len(name) - span + 1), 2):
        distance = levenshtein(needle, name[start:start + span])
        if distance < best:
            best = distance
            if best == 0:
                break
    if best <= allowed:
        return 58 - best * 8 - min(start // 4, 8)
    return 0


def search(query: str, index: str = str(DEFAULT_INDEX), limit: int = 25,
           fuzzy_budget: float = 0.6) -> List[dict]:
    """
    Ищет файлы и папки по имени: русское название находит английский файл.

    Два прохода, и порядок между ними не косметический. Сначала точное вхождение
    отбирается запросом SQL — это делает SQLite на своей скорости, а не Python в
    цикле по шестидесяти тысячам строк. Нечёткий проход дороже на порядок и
    запускается только если первый не набрал результатов, то есть почти никогда.
    """
    if not Path(index).exists():
        raise FileNotFoundError(
            f"Индекс не построен: {index}. Соберите его: python -m cloudhdr_files index")

    needle = loose(query)
    if len(needle) < 2:
        return []

    connection = sqlite3.connect(index)
    rows = connection.execute(
        "SELECT name, path, is_dir, size, loose FROM files WHERE loose LIKE ? LIMIT 4000",
        (f"%{needle}%",)).fetchall()
    connection.close()

    scored = []
    for name, path, is_dir, size, name_loose in rows:
        value = _score(needle, name_loose)
        if value > 0:
            scored.append((value + (3 if is_dir else 0), name, path, bool(is_dir), size))

    if len(scored) < 3:
        # Нечёткий проход дорог: расстояние Левенштейна по окнам для каждого из
        # шестидесяти тысяч имён занимало три с половиной секунды. Спасают два
        # ограничения. Предфильтр отсекает имена, у которых нет ни одной общей с
        # запросом первой буквы и близкой длины, — а таких большинство. Бюджет
        # времени не даёт поиску подвиснуть на большом индексе: лучше отдать
        # найденное за полсекунды, чем полное за пять.
        deadline = time.time() + fuzzy_budget
        head = needle[0]
        low, high = len(needle) - 4, len(needle) + 20

        for name, path, is_dir, size, name_loose in _load(index):
            if not (low <= len(name_loose) <= high) or head not in name_loose:
                continue
            value = _fuzzy_score(needle, name_loose)
            if value > 0:
                scored.append((value + (3 if is_dir else 0), name, path, bool(is_dir), size))
            if time.time() > deadline:
                break

    # Папка при равном совпадении интереснее файла: чаще ищут именно её.
    scored.sort(key=lambda row: (-row[0], len(row[1])))

    seen = set()
    result = []
    for value, name, path, is_dir, size in scored:
        if path in seen:
            continue
        seen.add(path)
        result.append({"name": name, "path": path, "is_dir": is_dir,
                       "size": size, "score": value})
        if len(result) >= limit:
            break
    return result


def index_info(index: str = str(DEFAULT_INDEX)) -> dict:
    path = Path(index)
    if not path.exists():
        return {"exists": False}
    connection = sqlite3.connect(str(path))
    count = connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    connection.close()
    return {"exists": True, "files": count,
            "mb": round(path.stat().st_size / 1024 / 1024, 1),
            "built": time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))}


# -------------------------------------------------------------------- CLI ---

if __name__ == "__main__":
    import sys

    command = sys.argv[1] if len(sys.argv) > 1 else "info"

    if command == "index":
        print("Строю индекс файлов (это разовая операция)...")
        result = build_index()
        print(f"Готово: {result['files']} записей · {result['mb']} МБ · {result['seconds']} с")
    elif command == "search":
        for item in search(" ".join(sys.argv[2:])):
            kind = "папка" if item["is_dir"] else "файл "
            print(f"  {item['score']:3d}  {kind}  {item['path']}")
    else:
        print(index_info())
