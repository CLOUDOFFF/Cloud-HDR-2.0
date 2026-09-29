# -*- coding: utf-8 -*-
"""
Cloud HDR — что человек смотрит в браузере.

Зачем. Помощник должен знать привычки хозяина, иначе «открой что я обычно
смотрю» и «мне скучно» отвечать нечем. Список установленных программ на этот
вопрос не отвечает: браузер один, а жизни в нём десяток разных.

Откуда берётся. У всех браузеров на движке Chromium история лежит в файле
History — это обычная база SQLite с таблицей urls, где для каждой ссылки есть
счётчик посещений. Модуль читает её напрямую: sqlite3 входит в стандартную
поставку Python, ставить ради этого нечего.

Куда уходит. Никуда. Данные читаются локально, отдаются локальному агенту и
показываются хозяину этих же данных. Это то же правило, что и во всём проекте:
наружу не уходит ничего, кроме вопроса к Википедии.

Две детали, без которых не работает.

БАЗУ НАДО КОПИРОВАТЬ. Пока браузер запущен, он держит History заблокированным,
и открыть файл на чтение нельзя — SQLite ответит «database is locked». Поэтому
файл копируется во временную папку вместе с журналом -wal, и читается копия.

СВОЁ ЛЕТОИСЧИСЛЕНИЕ. Время в Chromium считается в микросекундах от 1 января
1601 года — наследство от Windows FILETIME. Без пересчёта «последний визит»
оказывается где-то в XVII веке.

    python cloudhdr_browser.py top --limit 10 --days 60
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

#: Браузеры и путь к профилю относительно %LOCALAPPDATA% / %APPDATA%.
#:
#: Профиль по умолчанию называется Default, но у людей часто заведено
#: несколько — «Profile 1», «Profile 2». Поэтому берутся все каталоги профилей,
#: какие найдутся, а не только Default.
BROWSERS = [
    ("Google Chrome", "LOCALAPPDATA", r"Google\Chrome\User Data"),
    ("Microsoft Edge", "LOCALAPPDATA", r"Microsoft\Edge\User Data"),
    ("Яндекс.Браузер", "LOCALAPPDATA", r"Yandex\YandexBrowser\User Data"),
    ("Brave", "LOCALAPPDATA", r"BraveSoftware\Brave-Browser\User Data"),
    ("Vivaldi", "LOCALAPPDATA", r"Vivaldi\User Data"),
    ("Opera", "APPDATA", r"Opera Software\Opera Stable"),
    ("Opera GX", "APPDATA", r"Opera Software\Opera GX Stable"),
    ("Chromium", "LOCALAPPDATA", r"Chromium\User Data"),
]

#: Служебные адреса: они есть в истории, но человек их не «смотрит».
SKIP_HOSTS = {"newtab", "localhost", "127.0.0.1", "", "chrome", "edge"}

#: Страницы входа и прочая служебная механика.
#:
#: Без этого в «часто посещаемом» вторым номером идёт accounts.google.com: через
#: него проходит каждый вход в почту и на любой сайт с кнопкой «войти через
#: Google». Счётчик у него честный, но предложить хозяину «открыть страницу
#: входа» — не то, чего он ждёт от помощника.
SKIP_PREFIXES = ("accounts.", "login.", "auth.", "id.", "oauth.", "sso.")

#: Начало времён по-хромиумовски.
CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


def _profiles(root: Path) -> List[Path]:
    """Файлы History во всех профилях браузера."""
    found = []
    if not root.exists():
        return found
    # Opera хранит History прямо в корне профиля, Chrome — в подкаталогах.
    direct = root / "History"
    if direct.exists():
        found.append(direct)
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name != "Default" and not entry.name.startswith("Profile"):
            continue
        history = entry / "History"
        if history.exists():
            found.append(history)
    return found


def _read(history: Path, days: Optional[int]) -> List[dict]:
    """
    Строки истории из копии базы. Пустой список — значит не прочиталось.

    Молчаливый отказ здесь намеренный: браузер мог обновиться и сменить схему,
    файл мог оказаться битым. Ни одна такая беда не стоит того, чтобы уронить
    ответ помощника — просто этого браузера не будет в статистике.
    """
    temp = Path(tempfile.mkdtemp(prefix="cloudhdr-hist-"))
    try:
        copy = temp / "History"
        shutil.copy2(history, copy)
        for extra in ("-wal", "-shm"):          # журнал: без него часть записей свежее базы
            side = history.with_name(history.name + extra)
            if side.exists():
                shutil.copy2(side, copy.with_name(copy.name + extra))

        since = 0
        if days:
            moment = datetime.now(timezone.utc) - timedelta(days=days)
            since = int((moment - CHROME_EPOCH).total_seconds() * 1_000_000)

        connection = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT url, title, visit_count, last_visit_time FROM urls "
                "WHERE visit_count > 0 AND last_visit_time > ? "
                "ORDER BY visit_count DESC LIMIT 4000",
                (since,),
            ).fetchall()
        finally:
            connection.close()

        return [{"url": r[0], "title": r[1] or "", "visits": int(r[2] or 0),
                 "time": int(r[3] or 0)} for r in rows]
    except Exception:                            # noqa: BLE001
        return []
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def _domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _pretty(domain: str, titles: Dict[str, int]) -> str:
    """
    Человеческое имя сайта.

    Берётся самый частый заголовок вкладки, из него — кусок до разделителя:
    «Смешные видео — YouTube» → «YouTube». Если заголовков нет, остаётся домен,
    и это не беда: «youtube.com» тоже читается.
    """
    if not titles:
        return domain
    best = max(titles.items(), key=lambda pair: pair[1])[0]
    for separator in (" — ", " – ", " - ", " | ", " · "):
        if separator in best:
            best = best.split(separator)[-1]
    # «(1) YouTube» — счётчик непрочитанного из заголовка вкладки.
    best = re.sub(r"^\s*\(\d+\)\s*", "", best).strip()
    return best if 2 <= len(best) <= 40 else domain


def top_sites(limit: int = 10, days: Optional[int] = 90) -> dict:
    """Самые посещаемые сайты по всем найденным браузерам."""
    sites: Dict[str, dict] = {}
    browsers: List[str] = []

    for name, env, relative in BROWSERS:
        base = os.environ.get(env)
        if not base:
            continue
        histories = _profiles(Path(base) / relative)
        if not histories:
            continue
        rows: List[dict] = []
        for history in histories:
            rows += _read(history, days)
        if not rows:
            continue
        browsers.append(name)

        for row in rows:
            domain = _domain(row["url"])
            if not domain or domain in SKIP_HOSTS or "." not in domain:
                continue
            if domain.startswith(SKIP_PREFIXES):
                continue
            site = sites.setdefault(domain, {
                "domain": domain, "visits": 0, "time": 0, "titles": {}, "browser": name,
            })
            site["visits"] += row["visits"]
            site["time"] = max(site["time"], row["time"])
            if row["title"]:
                site["titles"][row["title"]] = site["titles"].get(row["title"], 0) + row["visits"]

    ranked = sorted(sites.values(), key=lambda s: s["visits"], reverse=True)[:limit]
    result = []
    for site in ranked:
        last = ""
        if site["time"]:
            last = (CHROME_EPOCH + timedelta(microseconds=site["time"])).astimezone().isoformat(timespec="seconds")
        result.append({
            "domain": site["domain"],
            "title": _pretty(site["domain"], site["titles"]),
            "visits": site["visits"],
            "url": "https://" + site["domain"],
            "browser": site["browser"],
            "last": last,
        })

    return {"ok": True, "browsers": browsers, "sites": result}


def main() -> int:
    parser = argparse.ArgumentParser(description="История браузеров Cloud HDR")
    parser.add_argument("command", nargs="?", default="top", choices=["top"])
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--days", type=int, default=90, help="0 — за всё время")
    args = parser.parse_args()

    data = top_sites(limit=max(1, args.limit), days=args.days or None)
    sys.stdout.write(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
