"""
Словари проекта: приложения, папки, глаголы, список намерений.

Единственный источник правды — сам фронтенд: `public/js/nlu.js` и
`public/js/brain.js`. Продублировать их здесь было бы проще всего и хуже всего:
стоит кому-то добавить в nlu.js новый псевдоним — обученная модель об этом не
узнает, и расхождение вылезет не при сборке, а в разговоре с пользователем.

Поэтому литералы читаются регулярными выражениями прямо из .js. Разбор наивный,
и это осознанно: нужны объектные литералы фиксированной формы — ключ и строка
либо ключ и список строк, — а тянуть в проект парсер JavaScript ради восьми
словарей несоразмерно. Если форма literal'а всё же изменится, сборка корпуса
упадёт с внятным сообщением, а не тихо обучится на пустом словаре: за этим
следит `require()`.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List

#: Корень репозитория: python/cloudhdr_nlu/lexicon.py → cloud-hdr/
ROOT = Path(__file__).resolve().parents[2]
NLU_JS = ROOT / "public" / "js" / "nlu.js"
BRAIN_JS = ROOT / "public" / "js" / "brain.js"


# --------------------------------------------------------------- чтение .js --

def _source(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Не найден {path}. Пакет cloudhdr_nlu берёт словари из фронтенда "
            f"и должен лежать внутри папки проекта cloud-hdr."
        )
    return path.read_text(encoding="utf-8")


def _block(source: str, name: str, opening: str) -> str:
    """Тело литерала `const NAME = { ... }` без вложенных структур."""
    start = source.find(f"const {name} = {opening}")
    if start < 0:
        raise ValueError(f"В исходнике нет литерала «const {name} = {opening}»")
    start += len(f"const {name} = {opening}")
    closing = "}" if opening == "{" else "]"
    depth = 1
    for index in range(start, len(source)):
        char = source[index]
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return source[start:index]
    raise ValueError(f"Литерал «{name}» не закрыт")


_STRING = re.compile(r"'((?:[^'\\]|\\.)*)'")


def _strings(text: str) -> List[str]:
    return [match.group(1).replace("\\'", "'").replace("\\\\", "\\") for match in _STRING.finditer(text)]


def _dict_of_lists(source: str, name: str) -> Dict[str, List[str]]:
    body = _block(source, name, "{")
    result: Dict[str, List[str]] = {}
    for key, values in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\[([^\]]*)\]", body):
        result[key] = _strings(values)
    return result


def _dict_of_strings(source: str, name: str) -> Dict[str, str]:
    body = _block(source, name, "{")
    result: Dict[str, str] = {}
    for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*'((?:[^'\\]|\\.)*)'", body):
        result[key] = value.replace("\\'", "'")
    return result


def _list(source: str, name: str) -> List[str]:
    return _strings(_block(source, name, "["))


def _new_set(source: str, name: str) -> List[str]:
    """`const NAME = new Set([...])` — литерал внутри вызова конструктора."""
    marker = f"const {name} = new Set("
    start = source.find(marker)
    if start < 0:
        raise ValueError(f"В исходнике нет литерала «{marker}...)»")
    tail = source[start + len(marker):]
    end = tail.find("])")
    if end < 0:
        raise ValueError(f"Литерал «{name}» не закрыт")
    return _strings(tail[:end])


# ------------------------------------------------------------------ словари --

class Lexicon:
    """Собранные словари. Загружается один раз, дальше берётся из кеша."""

    def __init__(self) -> None:
        nlu = _source(NLU_JS)
        brain = _source(BRAIN_JS)

        self.app_aliases = _dict_of_lists(nlu, "APP_ALIASES")
        self.app_titles = _dict_of_strings(nlu, "APP_TITLES")
        self.app_icons = _dict_of_strings(nlu, "APP_ICONS")
        self.folder_aliases = _dict_of_lists(nlu, "FOLDER_ALIASES")
        self.folder_titles = _dict_of_strings(nlu, "FOLDER_TITLES")

        self.site_aliases = _dict_of_lists(nlu, "SITE_ALIASES")
        self.site_urls = _dict_of_strings(nlu, "SITE_URLS")
        self.site_titles = _dict_of_strings(nlu, "SITE_TITLES")

        self.open_verbs = _list(nlu, "OPEN_VERBS")
        self.close_verbs = _list(nlu, "CLOSE_VERBS")
        self.search_verbs = _list(nlu, "SEARCH_VERBS")
        self.scan_verbs = _list(nlu, "SCAN_VERBS")

        self.intents = _list(brain, "INTENTS")
        self.executable = set(_new_set(nlu, "EXECUTABLE"))

        self.require()

    # ----------------------------------------------------------- проверка ---

    def require(self) -> None:
        """
        Проверяет, что разбор действительно что-то нашёл.

        Без этой проверки изменение формы литерала в nlu.js дало бы пустые
        словари, обучение прошло бы «успешно», а модель молча перестала бы
        узнавать приложения.
        """
        checks = {
            "APP_ALIASES": self.app_aliases,
            "APP_TITLES": self.app_titles,
            "FOLDER_ALIASES": self.folder_aliases,
            "FOLDER_TITLES": self.folder_titles,
            "SITE_ALIASES": self.site_aliases,
            "SITE_URLS": self.site_urls,
            "OPEN_VERBS": self.open_verbs,
            "INTENTS": self.intents,
            "EXECUTABLE": self.executable,
        }
        empty = [name for name, value in checks.items() if not value]
        if empty:
            raise ValueError(
                "Из фронтенда не разобрались словари: " + ", ".join(empty) +
                ". Похоже, форма литералов в public/js/ изменилась."
            )

        # Числа намерений тут раньше стояло жёстко (27), и на добавлении
        # record_screen сборка падала на ровном месте. Проверять надо не
        # количество, а то, что у каждого сайта есть адрес: без него разбор
        # вернёт ключ, который фронтенду некуда подставить.
        if len(self.intents) < 20:
            raise ValueError(f"Намерений подозрительно мало: {len(self.intents)}")
        without_url = sorted(set(self.site_aliases) - set(self.site_urls))
        if without_url:
            raise ValueError("У сайтов нет адреса в SITE_URLS: " + ", ".join(without_url))

    # ------------------------------------------------------- удобные виды ---

    @property
    def app_keys(self) -> List[str]:
        return list(self.app_aliases)

    @property
    def folder_keys(self) -> List[str]:
        return list(self.folder_aliases)

    def alias_to_app(self) -> Dict[str, str]:
        """Псевдоним → ключ приложения. Длинные псевдонимы имеют приоритет."""
        table: Dict[str, str] = {}
        for key, aliases in self.app_aliases.items():
            for alias in aliases:
                table.setdefault(alias.lower(), key)
        return table

    def alias_to_folder(self) -> Dict[str, str]:
        table: Dict[str, str] = {}
        for key, aliases in self.folder_aliases.items():
            for alias in aliases:
                table.setdefault(alias.lower(), key)
        return table

    def alias_to_site(self) -> Dict[str, str]:
        table: Dict[str, str] = {}
        for key, aliases in self.site_aliases.items():
            for alias in aliases:
                table.setdefault(alias.lower(), key)
        return table

    def title_of(self, kind: str, key: str) -> str:
        source = self.app_titles if kind == "app" else self.folder_titles
        return source.get(key, key)

    def icon_of(self, key: str) -> str:
        return self.app_icons.get(key, "app")


@lru_cache(maxsize=1)
def load() -> Lexicon:
    return Lexicon()
