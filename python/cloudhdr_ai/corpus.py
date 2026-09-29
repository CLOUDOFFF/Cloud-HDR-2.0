"""
Cloud HDR AI — загрузка открытого текстового корпуса.

Зачем отдельный модуль. Корпус проекта — 603 фразы из brain.js, около 30 КБ.
Для дообучения языковой модели этого мало на несколько порядков: на таком объёме
сеть за считаные сотни шагов заучивает примеры наизусть и перестаёт обобщать.
Нужны десятки мегабайт связного русского текста, и брать их лучше из открытого
источника, а не собирать вручную.

Ключевое решение — ПОТОКОВОЕ чтение (streaming=True). Русская Википедия целиком
весит около 2 ГБ в parquet; качать её ради 50 тысяч статей бессмысленно. В
потоковом режиме datasets тянет файлы по частям и отдаёт записи по одной, поэтому
на диск попадает ровно столько текста, сколько мы попросили, и остановиться можно
в любой момент.

    python -m cloudhdr_ai corpus --docs 20000        →  data/corpus.txt
    python -m cloudhdr_ai train --data data/corpus.txt --from-brain
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .branding import log

#: Готовые источники: (датасет, конфигурация, поле с текстом, описание).
SOURCES = {
    "wikipedia-ru": (
        "wikimedia/wikipedia", "20231101.ru", "text",
        "Русская Википедия — связный литературный текст на широкую тему",
    ),
    "wikipedia-en": (
        "wikimedia/wikipedia", "20231101.en", "text",
        "Английская Википедия — родной язык GPT-2, полезна для сверки",
    ),
}

DEFAULT_SOURCE = "wikipedia-ru"

#: Разметка и служебные хвосты, которые незачем показывать модели.
_SECTION = re.compile(r"^(См\.\s*также|Примечания|Литература|Ссылки|References|See also)\s*$",
                      re.MULTILINE)
_SPACES = re.compile(r"[ \t]+")
_BLANKS = re.compile(r"\n{3,}")


def clean(text: str) -> str:
    """Убирает служебные разделы и лишние пробелы, сохраняя абзацы."""
    cut = _SECTION.search(text)
    if cut:
        text = text[: cut.start()]
    text = _SPACES.sub(" ", text)
    text = _BLANKS.sub("\n\n", text)
    return text.strip()


def download(
    source: str = DEFAULT_SOURCE,
    docs: int = 20000,
    out: str = "data/corpus.txt",
    min_chars: int = 400,
    max_mb: Optional[float] = None,
    dataset: Optional[str] = None,
    config: Optional[str] = None,
    field: Optional[str] = None,
) -> Path:
    """
    Скачивает корпус и складывает в один текстовый файл.

    :param docs: сколько документов взять
    :param min_chars: короче этого документы пропускаются — обрубки статей
                      («Иванов — русский футболист.») учат модель обрывать мысль
    :param max_mb: жёсткий предел по размеру файла
    """
    try:
        from datasets import load_dataset
    except ImportError:
        raise SystemExit(
            "Не установлен пакет datasets. Из папки python/:\n"
            "    pip install datasets"
        )

    if dataset:
        name, conf, key = dataset, config, field or "text"
        description = f"{dataset}" + (f" / {config}" if config else "")
    else:
        if source not in SOURCES:
            raise ValueError(
                f"Неизвестный источник «{source}». Доступны: {', '.join(SOURCES)}"
            )
        name, conf, key, description = SOURCES[source]

    path = Path(out)
    path.parent.mkdir(parents=True, exist_ok=True)

    log(f"Источник: {description}")
    log(f"Потоковое чтение {name}" + (f" ({conf})" if conf else "") + " — весь датасет не качается")

    limit_bytes = int(max_mb * 1024 * 1024) if max_mb else None
    written = kept = skipped = 0

    try:
        stream = load_dataset(name, conf, split="train", streaming=True)
    except Exception as error:                                   # noqa: BLE001
        raise SystemExit(
            f"Не удалось открыть датасет: {error}\n"
            "Проверьте интернет — датасеты Hugging Face качаются с huggingface.co."
        )

    with path.open("w", encoding="utf-8") as handle:
        for record in stream:
            if kept >= docs:
                break

            text = clean(str(record.get(key, "")))
            if len(text) < min_chars:
                skipped += 1
                continue

            # Пустая строка между документами: при упаковке в окна она мешает
            # модели склеивать конец одной статьи с началом другой.
            block = text + "\n\n"
            handle.write(block)
            written += len(block.encode("utf-8"))
            kept += 1

            if kept % 2000 == 0:
                log(f"  собрано {kept:>6} документов · {written / 1024 / 1024:.1f} МБ")

            if limit_bytes and written >= limit_bytes:
                log(f"Достигнут предел {max_mb} МБ — останавливаюсь")
                break

    size_mb = written / 1024 / 1024
    log(f"Готово: {kept} документов, {size_mb:.1f} МБ → {path.resolve()}")
    if skipped:
        log(f"Пропущено слишком коротких: {skipped}")

    if size_mb < 5:
        log("ВНИМАНИЕ: корпус меньше 5 МБ — для дообучения этого мало, "
            "возьмите больше документов (--docs)")

    return path
