"""
Собственный компонент пайплайна: лемма вместо словоформы в признаке NORM.

Зачем он нужен, если лемматизатор в ru_core_news_md уже есть. Лемматизатор
кладёт результат в `token.lemma_`, а слой признаков MultiHashEmbed смотрит на
NORM, PREFIX, SUFFIX и SHAPE — леммы он не видит. Без этого компонента
«дискорду», «дискордом» и «дискорд» остались бы тремя разными хешами, то есть
ровно тем, чем они были в brain.js, и вся установка ради морфологии пропала бы
впустую.

Побочный, но важный эффект: поиск предобученного вектора у spaCy идёт через
NORM. На нашем корпусе векторы находились для 81% словоформ и для 86% лемм —
пять процентов токенов получают смысл вместо случайного хеша просто потому, что
записаны в начальной форме.

Компонент намеренно тривиальный. Вся морфология уже сделана выше по конвейеру,
здесь только перенос значения из одного поля в другое.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import spacy
from spacy.language import Language
from spacy.tokens import Doc

FACTORY = "cloudhdr_lemma_norm"

_MORPH = None


@lru_cache(maxsize=100000)
def _normal_form(word: str) -> str:
    """
    Начальная форма слова напрямую из словаря pymorphy3.

    Нужна для лёгкой сборки, где нейросетевого лемматизатора в пайплайне нет.
    Тот хорош тем, что снимает омонимию по контексту, но стоит дорого: на замере
    морфология съедала 2 мс из 3,4 — почти две трети всего разбора. Здесь берётся
    первый разбор слова без оглядки на контекст. Для команд этого достаточно:
    в «открой загрузки» слово «загрузки» ведёт себя одинаково в любом падеже, а
    редкие промахи омонимии на намерение не влияют.
    """
    global _MORPH
    if _MORPH is None:
        import pymorphy3

        _MORPH = pymorphy3.MorphAnalyzer()
    parsed = _MORPH.parse(word)
    return parsed[0].normal_form if parsed else word


@Language.factory(FACTORY, default_config={"pymorphy": False})
def make_lemma_norm(nlp: Language, name: str, pymorphy: bool):
    return LemmaNorm(pymorphy=pymorphy)


@spacy.registry.callbacks("cloudhdr_prune_vectors.v1")
def make_vector_pruner(rows: int = 8000):
    """
    Оставляет только `rows` самых частых векторов, остальные слова отображает
    на ближайший оставшийся.

    Полные 20 000 векторов — это 23 МБ, и они тянут за собой словарь лексем на
    77 МБ. Но корпус команд узкий: из двадцати тысяч слов в нём встречается
    хорошо если пара тысяч. Обрезка вызывается ПОСЛЕ инициализации и ДО обучения,
    поэтому модель учится ровно на тех векторах, с которыми потом и работает.
    """

    def prune(nlp: Language) -> Language:
        vectors = nlp.vocab.vectors
        if vectors.shape[0] > rows:
            nlp.vocab.prune_vectors(rows)
        return nlp

    return prune


class LemmaNorm:
    """Ставит `token.norm_` равным лемме в нижнем регистре."""

    def __init__(self, pymorphy: bool = False):
        #: True — брать начальную форму из словаря, не полагаясь на лемматизатор
        #: выше по конвейеру (его в лёгкой сборке нет).
        self.pymorphy = pymorphy

    def __call__(self, doc: Doc) -> Doc:
        for token in doc:
            lemma = token.lemma_
            if not lemma and self.pymorphy and token.is_alpha:
                lemma = _normal_form(token.text.lower())
            if lemma:
                token.norm_ = lemma.lower()
            else:
                # Лемматизатор молчит на пунктуации и латинице — там словоформа
                # и есть начальная, приводим только регистр.
                token.norm_ = token.text.lower()
        return doc

    # Компонент без обучаемых весов: spaCy всё равно вызывает эти методы при
    # сохранении и загрузке модели, и без заглушек пайплайн не соберётся.
    def to_disk(self, path, exclude=tuple()):
        return None

    def from_disk(self, path, exclude=tuple()):
        return self

    def to_bytes(self, exclude=tuple()):
        return b""

    def from_bytes(self, data, exclude=tuple()):
        return self
