"""
Cloud HDR — библиотека игр Steam: поиск по названию и запуск через appid.

Зачем отдельный модуль. Игру мало найти на диске — её надо запустить ПРАВИЛЬНО.
Запуск exe напрямую у многих игр либо не работает, либо ломает то, что
рассчитывает на Steam: античит, облачные сохранения, оверлей, права DRM.
Штатный путь один — steam://rungameid/<appid>.

appid лежит рядом с игрой: на каждую установленную Steam держит файл
steamapps/appmanifest_<appid>.acf, и внутри есть и appid, и настоящее название,
и состояние установки. Обход папок steamapps/common, которым занимается поиск
файлов, appid не даёт — там только имена каталогов.

Логика повторяет server/steam.js и server/agent.ps1 намеренно: три бэкенда
проекта равноправны, и запуск игры не должен зависеть от того, какой из них
подняли. Таблица псевдонимов и «мягкая» нормализация — те же.
"""

from __future__ import annotations

import re
import time
import winreg
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Служебные пакеты Steam: формально приложения, играть в них нельзя.
_JUNK = re.compile(r"(redistributable|proton|steam linux runtime|steamworks|dedicated server|sdk)", re.IGNORECASE)

#: Русские названия и сленг, которые не берутся ни транслитом, ни расстоянием.
#: «Среди нас» и «Among Us» — разные слова, их не сближает никакой алгоритм.
ALIASES: List[Tuple[str, List[str]]] = [
    ("Counter-Strike", ["контра", "кс го", "ксго", "кс 2", "кс2", "counter strike", "csgo", "cs2"]),
    ("Among Us", ["амонг ас", "амонгас", "амонг", "среди нас", "among us"]),
    ("Garry's Mod", ["гарис мод", "гаррис мод", "гмод", "garrys mod", "gmod"]),
    ("BeamNG.drive", ["бимка", "бимэнджи", "бим эн джи", "beamng"]),
    ("Black Myth: Wukong", ["вуконг", "укун", "чёрный миф", "черный миф", "wukong"]),
    ("Resident Evil", ["резидент ивел", "резик", "обитель зла", "resident evil"]),
    ("Slay the Spire", ["слей зе спайр", "спайр", "slay the spire"]),
    ("Tabletop Simulator", ["тейблтоп", "настольный симулятор", "tabletop simulator"]),
    ("Buckshot Roulette", ["бакшот", "buckshot roulette"]),
    ("DCS World", ["дцс", "dcs world"]),
    ("Geometry Dash", ["геометри даш", "geometry dash"]),
    ("Cyberpunk", ["киберпанк", "сайберпанк", "cyberpunk"]),
    ("Subnautica", ["субнавтика", "субнатика", "subnautica"]),
    ("Minecraft", ["майнкрафт", "майн", "minecraft"]),
    ("Dota", ["дота", "дота 2", "dota"]),
    ("Grand Theft Auto", ["гта", "гта 5", "gta"]),
    ("The Witcher", ["ведьмак", "witcher"]),
    ("Terraria", ["террария", "terraria"]),
    ("Rust", ["раст", "rust"]),
]

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
    "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
    "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

_NOT_WORD = re.compile(r"[^\w\d]", re.UNICODE)
_DOUBLED = re.compile(r"(.)\1+")


def comparable(value: str) -> str:
    return _NOT_WORD.sub("", (value or "").lower().replace("ё", "е"))


def translit(value: str) -> str:
    return "".join(_TRANSLIT.get(char, char) for char in (value or "").lower())


def loose(value: str) -> str:
    """
    «Мягкая» форма — только для сравнения.

    Транслитерация неоднозначна в обе стороны: «гарис мод» даёт garismod, а
    Garry's Mod — garrysmod, и расстояние 2 не дотягивает до порога. Дело не в
    опечатке, а в том, что одну букву принято писать по-разному: и/й/ы → i или y,
    w или v, ck или k, з → z или s, сдвоенные согласные то есть, то нет. Здесь
    эти различия стираются, и обе стороны совпадают точно.
    """
    text = comparable(value)
    if not text:
        return ""
    text = text.replace("ck", "k").replace("w", "v")
    text = text.replace("y", "i").replace("j", "i").replace("z", "s")
    return _DOUBLED.sub(r"\1", text)


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a or not b:
        return max(len(a), len(b))
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        row = [i]
        for j, char_b in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = row
    return previous[len(b)]


# ------------------------------------------------------------- библиотеки ----

def _registry_roots() -> List[str]:
    roots: List[str] = []
    hives = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam"),
    ]
    for hive, subkey in hives:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                install = winreg.QueryValueEx(key, "InstallPath")[0]
            if install and Path(install).exists():
                roots.append(install)
        except OSError:
            continue          # Steam не установлен — не ошибка
    return roots


def libraries() -> List[str]:
    roots = list(_registry_roots())
    for guess in (r"C:\Program Files (x86)\Steam", r"D:\Steam", r"D:\SteamLibrary", r"E:\SteamLibrary"):
        if Path(guess).exists():
            roots.append(guess)

    found: List[str] = []
    for root in roots:
        if root not in found:
            found.append(root)
        vdf = Path(root) / "steamapps" / "libraryfolders.vdf"
        if not vdf.exists():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s*"([^"]+)"', text):
            library = match.group(1).replace("\\\\", "\\")
            if Path(library).exists() and library not in found:
                found.append(library)
    return found


_CACHE: Optional[List[Dict[str, Any]]] = None
_CACHED_AT = 0.0


def index(force: bool = False) -> List[Dict[str, Any]]:
    """Установленные игры: appid, название, состояние. Кеш на 10 минут."""
    global _CACHE, _CACHED_AT
    if _CACHE is not None and not force and time.time() - _CACHED_AT < 600:
        return _CACHE

    games: List[Dict[str, Any]] = []
    seen = set()
    for library in libraries():
        steamapps = Path(library) / "steamapps"
        if not steamapps.is_dir():
            continue
        for manifest in steamapps.glob("appmanifest_*.acf"):
            try:
                text = manifest.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            appid = (re.search(r'"appid"\s*"(\d+)"', text) or [None, None])[1]
            name = (re.search(r'"name"\s*"([^"]+)"', text) or [None, None])[1]
            state = (re.search(r'"StateFlags"\s*"(\d+)"', text) or [None, None])[1]
            if not appid or not name or _JUNK.search(name) or appid in seen:
                continue
            seen.add(appid)

            # StateFlags — битовая маска Steam; бит 4 означает «установлено
            # полностью». Со снятым битом Steam начнёт докачивать игру, и до
            # запуска дело не дойдёт.
            installed = bool(int(state) & 4) if state else False
            games.append({"appid": appid, "name": name, "installed": installed, "library": library})

    _CACHE, _CACHED_AT = games, time.time()
    return games


# ------------------------------------------------------------------ поиск ----

def _score(needle: str, name: str) -> int:
    if not needle or not name or len(needle) < 2:
        return 0
    if name == needle:
        return 100
    if name.startswith(needle):
        return 90 - min(len(name) - len(needle), 20)
    if needle in name and len(needle) >= 3:
        return 76
    if name in needle and len(name) >= 4:
        return 70
    if len(needle) >= 4:
        distance = levenshtein(needle, name)
        if distance <= max(1, len(needle) // 4):
            return 66 - distance * 6
    return 0


def resolve_alias(query: str) -> Optional[str]:
    needle = comparable(query)
    if not needle:
        return None
    for name, words in ALIASES:
        for word in words:
            key = comparable(word)
            if key and (needle == key or key in needle or needle in key):
                return name
    return None


def find(query: str) -> Dict[str, Any]:
    """:returns: {"best": игра или None, "suggestions": [названия]}"""
    games = index()
    if not games:
        return {"best": None, "suggestions": []}

    variants = [comparable(query), comparable(translit(query))]
    alias = resolve_alias(query)
    if alias:
        variants.append(comparable(alias))

    scored = []
    for game in games:
        name = comparable(game["name"])
        if not name:
            continue
        name_loose = loose(game["name"])

        best = 0
        for needle in variants:
            best = max(best, _score(needle, name))
            # Второй проход по «мягкой» форме — со штрафом, чтобы точное
            # совпадение написания всегда выигрывало у приблизительного.
            soft = _score(loose(needle), name_loose)
            if soft > 0:
                best = max(best, soft - 6)
        if best > 0 and not game["installed"]:
            best -= 20
        if best > 0:
            scored.append((best, game))

    scored.sort(key=lambda row: row[0], reverse=True)
    return {
        "best": scored[0][1] if scored and scored[0][0] >= 60 else None,
        "suggestions": [game["name"] for _, game in scored[:3]],
    }


def run_uri(game: Dict[str, Any]) -> str:
    return f"steam://rungameid/{game['appid']}"
