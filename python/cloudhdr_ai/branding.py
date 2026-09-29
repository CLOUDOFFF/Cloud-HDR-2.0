"""
Cloud HDR AI — единый источник бренда.

Заголовок печатается ровно один раз за процесс: модули зовут banner() свободно,
не согласовывая между собой, кто стартовал первым, — иначе при `serve`, который
внутри поднимает и модель, и токенизатор, шапка вылезала бы трижды.
"""

from __future__ import annotations

import os
import sys

PRODUCT = "CLOUD HDR AI"
VERSION = "1.0"
TAG = f"[{PRODUCT} v{VERSION}]"

# Полное имя продукта для веб-интерфейса и заголовков окон.
BRAND = "Cloud HDR"
BRAND_AI = "Cloud HDR AI"

# Имя и версия самой языковой модели — то, что видит пользователь.
#
# Отдельно от VERSION выше: та обозначает версию пакета и командной строки, а
# эта — поколение обученных весов, и растут они независимо. GPT-2 в имени
# больше нет: конфигурация слоёв действительно повторяет её, но это внутренняя
# подробность реализации, а не название продукта.
MODEL_VERSION = "3.2"
MODEL_LABEL = f"{BRAND} {MODEL_VERSION}"
#: Идентификатор в ответе /v1/models — по нему интерфейс подписывает модель.
MODEL_ID = "cloud-hdr-3.2"

# Вторая роль тех же весов — Cloud HDR Ultra.
#
# Это НЕ вторая модель: на диске одни веса, в видеопамяти одна сеть. Разными у
# двух имён являются системная подсказка, параметры генерации и правило
# остановки. Держать в памяти два экземпляра по 760 млн параметров ради этого
# было бы расточительством, а разница в поведении даётся целиком настройкой.
#
# Почему тогда два имени в интерфейсе, а не переключатель «режим кода».
# Потому что для человека это и есть разный собеседник: у 3.2 ответы короткие и
# разговорные, у Ultra — длинные, с кодом и разметкой, и токенов они стоят
# по-разному. Выбор модели в списке говорит об этом честнее, чем галочка.
MODEL_VERSION_ULTRA = "Ultra"
MODEL_LABEL_ULTRA = f"{BRAND} {MODEL_VERSION_ULTRA}"
MODEL_ID_ULTRA = "cloud-hdr-ultra"

_banner_shown = False


def _color_ok() -> bool:
    """ANSI-цвет уместен только в настоящем терминале и без NO_COLOR."""
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def tag(text: str = "") -> str:
    """Строка, снабжённая брендированным префиксом."""
    return f"{TAG} {text}".rstrip()


def log(text: str) -> None:
    """Брендированная строка лога — единый формат для всех модулей пакета."""
    print(tag(text), flush=True)


def banner(subtitle: str = "Запуск модуля...", force: bool = False) -> None:
    """
    Печатает приветственный заголовок:

        [CLOUD HDR AI v1.0] Запуск модуля...
    """
    global _banner_shown
    if _banner_shown and not force:
        return
    _banner_shown = True

    if _color_ok():
        # 96 — голубой: цвет акцента интерфейса Cloud HDR.
        print(f"\033[96m\033[1m{TAG}\033[0m {subtitle}", flush=True)
    else:
        print(f"{TAG} {subtitle}", flush=True)


def rule(title: str = "") -> None:
    """Разделитель для длинных отчётов обучения."""
    line = "-" * 64
    print(f"{line}\n{tag(title)}\n{line}" if title else line, flush=True)
