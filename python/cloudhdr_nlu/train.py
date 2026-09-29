"""
Обучение: .jsonl → DocBin → spaCy train.

Обучение запускается программно, а не командой `python -m spacy train`, ровно
по одной причине: в пайплайне есть наш компонент `cloudhdr_lemma_norm`, и
подхватить его spaCy может только внутри процесса, где пакет уже импортирован.
Отдельный процесс о нём не знает и падает на сборке конфигурации.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

from . import SPACY_MODEL
from .branding import log, rule
from .corpus import Example, SLOT_LABELS, read
from .lexicon import load as load_lexicon
from . import pipeline as _pipeline  # noqa: F401 — импорт регистрирует фабрику

CONFIG = Path(__file__).with_name("config.cfg")
LIGHT_CONFIG = Path(__file__).with_name("config-light.cfg")
DEFAULT_OUT = "models/nlu"


# ------------------------------------------------------------- подготовка ---

def to_docbin(examples: List[Example], intents: List[str], nlp, out: Path) -> Tuple[int, int]:
    """
    Складывает примеры в DocBin. Возвращает (документов, потерянных границ).

    Границы приходят в символах, а модель работает с токенами, и не всякий
    отрезок ложится на границы токенов: «открой D:\\Games» токенизируется иначе,
    чем «открой хром». Такие спаны spaCy сжимает до ближайших целых токенов
    (alignment_mode="contract"), а совсем безнадёжные отбрасываются — молча
    обучаться на кривой разметке хуже, чем недосчитаться примеров.
    """
    from spacy.tokens import DocBin

    doc_bin = DocBin(store_user_data=False)
    lost = 0

    for example in examples:
        doc = nlp.make_doc(example.text)
        doc.cats = {intent: 0.0 for intent in intents}
        doc.cats[example.intent] = 1.0

        if example.ner_missing:
            # Границы неизвестны: намерению фраза учит, разметчику границ —
            # ничего не сообщает. Без default="missing" пустой список означал бы
            # «цели в этой фразе нет», и 603 живые фразы учили бы разметчик
            # обратному тому, чему учат шаблоны.
            doc.set_ents([], default="missing")
            doc_bin.add(doc)
            continue

        spans = []
        for start, end, label in example.spans:
            span = doc.char_span(start, end, label=label, alignment_mode="contract")
            if span is None or not len(span):
                lost += 1
                continue
            spans.append(span)

        try:
            doc.ents = spans
        except ValueError:
            # пересекающиеся отрезки — берём только первый
            doc.ents = spans[:1]
            lost += max(0, len(spans) - 1)

        doc_bin.add(doc)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc_bin.to_disk(out)
    return len(examples), lost


def prepare(data_dir: str = "data/nlu") -> Tuple[Path, Path, List[str]]:
    """Читает .jsonl и пишет рядом train.spacy / dev.spacy."""
    import spacy

    path = Path(data_dir)
    train_jsonl, dev_jsonl = path / "train.jsonl", path / "dev.jsonl"
    if not train_jsonl.exists():
        raise FileNotFoundError(
            f"Нет {train_jsonl}. Сначала соберите корпус: python -m cloudhdr_nlu build"
        )

    intents = load_lexicon().intents
    nlp = spacy.blank("ru")

    train_path, dev_path = path / "train.spacy", path / "dev.spacy"
    train_count, train_lost = to_docbin(read(train_jsonl), intents, nlp, train_path)
    dev_count, dev_lost = to_docbin(read(dev_jsonl), intents, nlp, dev_path)

    log(f"Обучение: {train_count} документов (границ потеряно: {train_lost})")
    log(f"Проверка: {dev_count} документов (границ потеряно: {dev_lost})")
    return train_path, dev_path, intents


# ---------------------------------------------------------------- обучение --

def run(
    data_dir: str = "data/nlu",
    out_dir: str = DEFAULT_OUT,
    max_epochs: Optional[int] = None,
    dropout: Optional[float] = None,
    seed: Optional[int] = None,
    light: bool = False,
) -> Path:
    from spacy.cli.train import train as spacy_train

    rule("Подготовка данных")
    train_path, dev_path, intents = prepare(data_dir)

    overrides = {
        "paths.train": str(train_path),
        "paths.dev": str(dev_path),
        "paths.vectors": SPACY_MODEL,
    }
    if max_epochs is not None:
        overrides["training.max_epochs"] = max_epochs
    if dropout is not None:
        overrides["training.dropout"] = dropout
    if seed is not None:
        overrides["system.seed"] = seed

    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)

    config = LIGHT_CONFIG if light else CONFIG
    rule("Обучение" + (" — лёгкая сборка" if light else ""))
    log(f"Намерений: {len(intents)} · меток цели: {len(SLOT_LABELS)} · база: {SPACY_MODEL}")
    if light:
        log("Без нейросетевой морфологии · векторы обрезаны до 8000 · мешок слов 16384")
    spacy_train(config, output, use_gpu=-1, overrides=overrides)

    best = output / "model-best"
    (best / "cloudhdr-nlu.json").write_text(
        json.dumps({"intents": intents, "slots": list(SLOT_LABELS), "base": SPACY_MODEL},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"Модель сохранена: {best}")

    if light:
        compact(str(best))
    calibrate(str(best), data_dir)
    return best


# --------------------------------------------------------------- ужатие -----

def compact(model_dir: str) -> dict:
    """
    Выбрасывает из словаря строк всё, что модели не нужно.

    Откуда берётся лишнее. Чтобы получить предобученные векторы, spaCy
    загружает словарь целиком — вместе с ним в модель попадают все лексемы
    ru_core_news_md, почти миллион строк на 48 МБ. Нашей модели из них нужны
    только те, у которых есть вектор; остальные приехали за компанию и просто
    занимают место.

    Почему это безопасно. Поиск вектора идёт по ХЕШУ слова, а хеш считается на
    лету и в словаре строк не нуждается. Словарь нужен для обратного
    преобразования — показать текст по хешу, — и там участвуют метки
    компонентов, которые мы сохраняем. Проверено сравнением: точность на
    отложенной выборке и сквозная проверка не изменились ни на десятую.
    """
    import spacy

    path = Path(model_dir)
    strings_path = path / "vocab" / "strings.json"
    if not strings_path.exists():
        return {"skipped": True}

    nlp = spacy.load(str(path))
    key2row = nlp.vocab.vectors.key2row

    keep = {text for text in nlp.vocab.strings if nlp.vocab.strings[text] in key2row}
    total = sum(1 for _ in nlp.vocab.strings)

    # Служебные строки компонентов: имена и метки. Их немного, но без них
    # модель не соберётся обратно.
    for name, pipe in nlp.pipeline:
        keep.add(name)
        keep.update(str(label) for label in (getattr(pipe, "labels", None) or ()))
    keep.update({"", " ", "\n", "\t"})

    before = strings_path.stat().st_size / 1024 / 1024
    strings_path.write_text(json.dumps(sorted(keep), ensure_ascii=False), encoding="utf-8")
    after = strings_path.stat().st_size / 1024 / 1024
    size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024

    rule("Ужатие словаря")
    log(f"строк: {total} → {len(keep)} · strings.json {before:.0f} → {after:.1f} МБ")
    log(f"модель целиком: {size:.0f} МБ")
    return {"strings_before": total, "strings_after": len(keep), "mb": round(size, 1)}


# ------------------------------------------------------------ калибровка ----

def calibrate(model_dir: str, data_dir: str = "data/nlu") -> float:
    """
    Подбирает температуру, с которой уверенность начинает означать вероятность.

    Зачем это нужно. После двенадцати эпох потеря на обучении уходит к нулю, и
    textcat начинает отвечать «100%» — в том числе когда ошибается. В разборе
    ошибок это видно прямо: `заскринь` → greeting с p=1.0. Уверенность в таком
    виде бесполезна, а на ней стоят три решения сразу: доверять модели или
    словарю, показывать ли подсказку «Не угадал?», и что писать в панели
    размышления. Ровно эту болезнь в проекте уже лечили — у brain.js есть
    калибровка температурой, и по той же причине.

    Метод простой и стандартный: одно число T, вероятности пересчитываются как
    p^(1/T) с перенормировкой, T подбирается перебором по минимуму
    правдоподобия на отложенной выборке. Обучать заново ничего не надо, и
    предсказанный класс не меняется — меняется только его уверенность.
    """
    import math

    import spacy

    nlp = spacy.load(model_dir)
    examples = read(Path(data_dir) / "dev.jsonl")
    if not examples:
        return 1.0

    # (вероятности по классам, индекс верного класса)
    rows = []
    labels = None
    for example in examples:
        doc = nlp(example.text)
        if labels is None:
            labels = list(doc.cats)
        rows.append(([max(doc.cats[name], 1e-12) for name in labels], labels.index(example.intent)))

    def nll(temperature: float) -> float:
        total = 0.0
        for probabilities, gold in rows:
            powered = [p ** (1.0 / temperature) for p in probabilities]
            norm = sum(powered)
            total -= math.log(max(powered[gold] / norm, 1e-12))
        return total / len(rows)

    grid = [0.5 + 0.05 * step for step in range(80)]     # 0.50 … 4.45
    best = min(grid, key=nll)

    before, after = nll(1.0), nll(best)
    rule("Калибровка уверенности")
    log(f"температура {best:.2f} · правдоподобие {before:.3f} → {after:.3f}")

    meta_path = Path(model_dir) / "cloudhdr-nlu.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    meta["temperature"] = round(best, 3)
    meta["nll_before"] = round(before, 4)
    meta["nll_after"] = round(after, 4)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return best


# ------------------------------------------------------------------ оценка --

def evaluate(model_dir: str = DEFAULT_OUT + "/model-best", data_dir: str = "data/nlu") -> dict:
    """
    Точность на отложенной выборке, отдельно по намерениям и по границам цели.

    Считается своим кодом, а не `spacy evaluate`, ради разбора ошибок: важно не
    само число, а на каких намерениях модель путается — это подсказывает, чего
    не хватает в корпусе.
    """
    import spacy
    from collections import Counter

    from .parse import Parser

    nlp = spacy.load(model_dir)
    examples = read(Path(data_dir) / "dev.jsonl")

    # Пользователю достаётся не голая модель, а разбор целиком: модель плюс
    # несколько жёстких правил на однозначных признаках. Мерить только модель
    # значило бы отчитываться о том, чего никто не запускает, поэтому считаются
    # оба числа — и видно, сколько именно добавляют правила.
    parser = Parser(nlp, Path(model_dir))
    full_correct = 0

    correct = 0
    confusion: Counter = Counter()
    by_intent: Counter = Counter()
    hits_by_intent: Counter = Counter()

    span_expected = span_found = span_exact = 0

    for example in examples:
        doc = nlp(example.text)
        predicted = max(doc.cats, key=doc.cats.get) if doc.cats else "unknown"
        by_intent[example.intent] += 1
        if predicted == example.intent:
            correct += 1
            hits_by_intent[example.intent] += 1
        else:
            confusion[(example.intent, predicted)] += 1

        if parser.parse(example.text)["intent"] == example.intent:
            full_correct += 1

        gold = {(s, e, label) for s, e, label in example.spans}
        got = {(ent.start_char, ent.end_char, ent.label_) for ent in doc.ents}
        span_expected += len(gold)
        span_found += len(got)
        span_exact += len(gold & got)

    accuracy = correct / len(examples) if examples else 0.0
    full_accuracy = full_correct / len(examples) if examples else 0.0
    precision = span_exact / span_found if span_found else 0.0
    recall = span_exact / span_expected if span_expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    rule("Отложенная выборка")
    log(f"Намерение, только модель:  {accuracy * 100:.1f}% ({correct} из {len(examples)})")
    log(f"Намерение, разбор целиком: {full_accuracy * 100:.1f}% ({full_correct} из {len(examples)})")
    log(f"Границы цели: точность {precision * 100:.1f}%, полнота {recall * 100:.1f}%, F1 {f1 * 100:.1f}%")

    weak = sorted(
        ((intent, hits_by_intent[intent] / total, total) for intent, total in by_intent.items()),
        key=lambda row: row[1],
    )[:5]
    rule("Слабые намерения")
    for intent, share, total in weak:
        log(f"{intent:<16} {share * 100:5.1f}%  ({total} фраз)")

    if confusion:
        rule("Частые путаницы")
        for (gold_intent, predicted), count in confusion.most_common(6):
            log(f"{gold_intent} → {predicted}: {count}")

    return {
        "intent_accuracy": accuracy,
        "intent_accuracy_full": full_accuracy,
        "slot_precision": precision,
        "slot_recall": recall,
        "slot_f1": f1,
        "examples": len(examples),
    }
