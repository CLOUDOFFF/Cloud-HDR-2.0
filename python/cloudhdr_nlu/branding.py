"""Единый вывод в консоль — как в cloudhdr_ai, чтобы окна выглядели одинаково."""

from __future__ import annotations

import sys

from . import PRODUCT, VERSION

WIDTH = 78


def _write(text: str) -> None:
    # Консоль Windows живёт в cp866/cp1251 и на «☁» падает с UnicodeEncodeError.
    # Ронять обучение из-за символа в логе — нелепо, поэтому пишем побайтово с
    # заменой того, что кодировка терминала не вытягивает.
    stream = sys.stdout
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.write(text.encode(encoding, errors="replace").decode(encoding) + "\n")
    stream.flush()


def log(message: str) -> None:
    _write(f"  {message}")


def rule(title: str = "") -> None:
    if title:
        _write("  " + title)
    _write("  " + "-" * (WIDTH - 4))


def banner(subtitle: str = "") -> None:
    _write("")
    _write("  " + "=" * (WIDTH - 4))
    _write(f"  {PRODUCT} v{VERSION}" + (f" — {subtitle}" if subtitle else ""))
    _write("  " + "=" * (WIDTH - 4))
