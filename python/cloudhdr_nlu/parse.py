"""
Разбор фразы: намерение от textcat, цель от ner, канон от словаря.

Модель отвечает на два вопроса — «что за команда» и «какой кусок фразы её
цель». Ответ на третий вопрос — «а какому приложению эта цель соответствует» —
берётся из словаря, и это не отступление от идеи «пусть решает сеть», а
разделение обязанностей: «дискордик» и «дискорт» сеть уверенно выделит как
цель, но что запускать надо именно Discord — знание о системе, а не о языке, и
его место в таблице, где его можно поправить одной строкой.

Поверх модели работают несколько жёстких правил. Их немного, и каждое стоит на
однозначном признаке: путь вида D:\\Games, домен, приказ выключить компьютер.
Там, где формулировка не оставляет разночтений, спорить с регулярным выражением
нейросети незачем — а вот наоборот, полагаться на регулярные выражения в
свободной речи, и не работало.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from .branding import log
from .lexicon import load as load_lexicon
from . import pipeline as _pipeline  # noqa: F401 — регистрирует фабрику

DEFAULT_MODEL_DIR = "models/nlu/model-best"

_URL_RE = re.compile(
    # Кириллические домены: «рф» тут был с самого начала, а «ру» и «сайт» —
    # нет, и «открывай хабр.ру» уходило в запуск программы вместо браузера.
    # Нашлось это не метрикой, а свежими фразами: в корпусе такие домены не
    # порождались, и на отложенной выборке ошибка была невидима.
    r"(https?://\S+|(?:[a-zа-я0-9-]+\.)+"
    r"(?:ru|com|net|org|io|dev|me|tv|gg|app|xyz|info|biz|ua|by|kz|su|рф|ру|сайт|орг|дети)"
    r"(?:/\S*)?)",
    re.IGNORECASE,
)
_PATH_RE = re.compile(r"([a-zA-Z]:[\\/][^\s,]*|\\\\[^\s,]+)")

#: Упоминание Steam в запросе: «запусти В СТИМЕ кс 2», «включи кс 2 ЧЕРЕЗ СТИМ».
_VIA_STEAM_RE = re.compile(r"\b(в\s+стиме|через\s+стим|из\s+стима|стим[еа]?\b|steam)\b", re.IGNORECASE)

#: Словесные уровни громкости. «Половину» и «треть» человек говорит не реже,
#: чем «пятьдесят», а числа в них нет — регулярное выражение их не возьмёт.
_VOLUME_WORDS = {
    "максимум": 100, "максималку": 100, "полную": 100, "полностью": 100,
    "минимум": 0, "ноль": 0, "нуля": 0, "тишину": 0,
    "половину": 50, "половина": 50, "середину": 50, "треть": 33, "четверть": 25,
}

_NUMBER_RE = re.compile(r"(\d{1,3})\s*(?:%|процент\w*)?")

#: Вопрос о содержимом памяти: «что ты помнишь», «что я просил запомнить».
_RECALL_RE = re.compile(
    r"(что\s+(ты\s+)?помн\w+|помнишь\s+ли|что\s+(я\s+)?(просил|говорил|рассказыв\w*)\s+запомн\w*"
    r"|что\s+(у\s+тебя\s+)?(записано|сохранено|в\s+памяти)|покажи\s+(мои\s+)?заметк\w*)",
    re.IGNORECASE)

#: Слова относительного изменения: «громче», «тише», «прибавь».
#:
#: Отличают «сделай громче НА 20» (шаг) от «поставь громкость НА 20» (уровень).
#: Число есть в обеих фразах, и по нему их не различить — различает глагол.
_VOLUME_RELATIVE_RE = re.compile(
    r"(громче|тише|погромче|потише|прибав|убав|подним|опусти|увелич|уменьш"
    r"|добав|сбав|накрут|прикрут|скрут|выкрут)", re.IGNORECASE)

#: Кандидат, который целиком состоит из слова «стим» — указание, а не цель.
_STEAM_ONLY_RE = re.compile(r"^(в\s+|через\s+|из\s+)?(стим\w*|steam)$", re.IGNORECASE)

#: Служебные начала, которые целью не бывают: «запусти В СТИМЕ ИГРУ амонг ас».
_LEAD_NOISE = (
    "в стиме ", "через стим ", "из стима ", "в стим ", "стиме ", "стим ", "steam ",
    "поиграть в ", "сыграть в ", "играть в ", "игру ", "игра ", "игрушку ",
    "программу ", "приложение ", "прогу ", "мне ", "сайт ", "страницу ", "на сайт ",
    "в браузере ", "ка ",
)

#: Приказы, которые Cloud HDR не выполняет. Совпадение здесь важнее мнения
#: сети: «вырубай машину совсем» и «вырубай звук» отличаются одним словом, а
#: последствия ошибки несопоставимы, и цена ложного срабатывания — вежливый
#: отказ, а не выключенный компьютер.
#:
#: Список объектов намеренно перечислен, а не заменён на «что угодно после
#: глагола». «Выключи компьютер» — запрещено, «выключи звук» и «выключи
#: телеграм» — обычные команды, и глагол у них один и тот же. По той же причине
#: здесь нет «всё»: «выключи все звуки» — это громкость, а не выключение ПК, и
#: ловить такую фразу правилом значило бы сломать рабочий сценарий ради
#: воображаемого. Где формулировка размыта, решает модель.
_DENIED_RE = re.compile(
    # «Гаси» и «туши» добавлены по разбору свежих фраз: «гаси комп немедленно»
    # разбиралось как блокировка экрана. Корень «погас» их не покрывал —
    # приставки в повелительном наклонении часто нет.
    r"(выключ|выруб|отключ|заверш|обесточ|погас|гас|туш|глуш)\w*\s+(комп\w*|пк\b|ноут\w*|машин\w*"
    r"|систем\w*|питани\w*|винд\w*|windows|сеанс\w*|шарманк\w*|железк\w*)"
    r"|(перезагруз|перезапуст|ребут)\w*\s+(комп\w*|пк\b|систем\w*|ноут\w*|машин\w*|винд\w*|windows)"
    r"|(отформат|формат(ир|н))\w*"
    r"|удали\w*\s+(вс[её]\b|все\s+\w+|систем\w*|windows|винд\w*|реестр|диск|мои\s+\w+|папку\s+с\s+)"
    r"|(снеси|сломай)\s+(винд|систем|операцион)"
    # Между глаголом и целью бывают вставки: «сотри С ДИСКА всё подчистую».
    # Допускаем до трёх слов, но только для «стереть» — там объектом стоит «всё»
    # или «диск», и спутать с безобидной командой нечем. Для глаголов выключения
    # такая вольность опасна: «выключи звук на компьютере» — это громкость,
    # а по свободному промежутку правило приняло бы её за выключение ПК.
    r"|(сотри|затри)\w*\s+(?:\w+\s+){0,3}(вс[её]\b|диск)"
    r"|почисти\s+(диск|вс[её]\b|реестр)"
    r"|убей\s+(вс[её]\b|систем)"
    r"|(заблокируй|закрой)\s+мне\s+доступ"
    r"|shutdown\s|format\s+[a-z]:|rm\s+-rf",
    re.IGNORECASE,
)

#: Слова, которые не могут быть целью сами по себе — остатки формулировки.
_NOISE = re.compile(
    r"^(мне|пожалуйста|плиз|пж|давай|ка|быстро|срочно|это|тут|там|же|бы|ну|эй|"
    r"игру|игра|игры|файл|файлы|программу|программа|приложение|папку|папка)$",
    re.IGNORECASE,
)


# --------------------------------------------------------------- словарное --

def _first(entities: Dict[str, List[str]], *labels: str) -> str:
    """Самый длинный отрезок по первой найденной метке из перечисленных."""
    for label in labels:
        values = entities.get(label)
        if values:
            return values[0]
    return ""


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


def _tolerance(word: str) -> int:
    """Порог опечаток — тот же, что в nlu.js, чтобы разбор не расходился."""
    if len(word) <= 4:
        return 0
    if len(word) <= 7:
        return 1
    return 2


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\wа-я0-9:\\/.\- ]", " ", text, flags=re.IGNORECASE)).strip()


def match_dictionary(phrase: str, dictionary: Dict[str, List[str]]):
    """Точное совпадение → вхождение словом → подстрока → опечатка."""
    text = normalize(phrase)
    if not text:
        return None

    best = None
    for key, aliases in dictionary.items():
        for alias in [key] + list(aliases):
            if text == alias:
                return {"key": key, "score": 1.0}
            if text.startswith(alias + " ") or text.endswith(" " + alias) or f" {alias} " in text:
                if not best or best["score"] < 0.9:
                    best = {"key": key, "score": 0.9}
            if len(alias) > 3 and alias in text and (not best or best["score"] < 0.8):
                best = {"key": key, "score": 0.8}
    if best:
        return best

    for word in text.split(" "):
        if len(word) < 3:
            continue
        for key, aliases in dictionary.items():
            for alias in [key] + list(aliases):
                if " " in alias:
                    continue
                distance = levenshtein(word, alias)
                if distance <= _tolerance(alias):
                    score = 0.75 - distance * 0.1
                    if not best or score > best["score"]:
                        best = {"key": key, "score": score}
    return best


# ------------------------------------------------------------------ разбор --

class Parser:
    """Обёртка над обученной моделью: фраза на входе, разбор на выходе."""

    def __init__(self, nlp, model_dir: Path):
        self.nlp = nlp
        self.model_dir = model_dir
        self.lex = load_lexicon()
        self.temperature = self._read_temperature(model_dir)

    @staticmethod
    def _read_temperature(model_dir: Path) -> float:
        """Температура калибровки, подобранная при обучении. 1.0 — без правки."""
        meta = Path(model_dir) / "cloudhdr-nlu.json"
        if not meta.exists():
            return 1.0
        try:
            value = float(json.loads(meta.read_text(encoding="utf-8")).get("temperature", 1.0))
        except (ValueError, json.JSONDecodeError):
            return 1.0
        return value if value > 0 else 1.0

    def _calibrated(self, cats: Dict[str, float]) -> Dict[str, float]:
        """
        Пересчитывает вероятности с калибровочной температурой.

        Порядок классов не меняется — предсказание остаётся тем же. Меняется
        только уверенность, и лишь после этого её можно сравнивать с порогами:
        на ней стоит выбор между моделью и словарём и появление подсказки
        «Не угадал?».
        """
        if abs(self.temperature - 1.0) < 1e-6:
            return dict(cats)
        power = 1.0 / self.temperature
        powered = {name: max(value, 1e-12) ** power for name, value in cats.items()}
        total = sum(powered.values()) or 1.0
        return {name: value / total for name, value in powered.items()}

    @classmethod
    def load(cls, model_dir: str = DEFAULT_MODEL_DIR) -> "Parser":
        import spacy

        path = Path(model_dir)
        if not path.exists():
            raise FileNotFoundError(
                f"Модель не найдена: {path}. Обучите её: python -m cloudhdr_nlu train"
            )
        nlp = spacy.load(str(path))
        parser = cls(nlp, path)
        log(f"Модель загружена: {path} · компоненты: {', '.join(nlp.pipe_names)}")
        log(f"Температура калибровки: {parser.temperature:.2f}")
        return parser

    # ----------------------------------------------------------- основное --

    def parse(self, text: str, context: Optional[dict] = None) -> dict:
        raw = (text or "").strip()
        if not raw:
            return {"intent": "none", "confidence": 0.0, "ranked": [], "slots": {},
                    "trace": [], "engine": "spacy"}

        doc = self.nlp(raw)
        cats = self._calibrated(doc.cats)
        ranked = sorted(cats.items(), key=lambda item: item[1], reverse=True)
        intent, confidence = ranked[0]
        trace = [f"spaCy: {intent} ({confidence * 100:.0f}%)"]
        if len(ranked) > 1 and ranked[1][1] > 0.12:
            trace[0] += f", затем {ranked[1][0]} ({ranked[1][1] * 100:.0f}%)"

        # Метка может встретиться НЕСКОЛЬКО раз, и хранить по одной было ошибкой:
        # во «включи кс 2 через стим» разметчик выделяет два APP — «кс 2» и
        # «стим», — и словарь «метка → текст» оставлял последний. Целью
        # становился Steam вместо игры. Держим все и разбираемся ниже.
        entities: Dict[str, List[str]] = {}
        for ent in doc.ents:
            entities.setdefault(ent.label_, []).append(ent.text)
        for values in entities.values():
            values.sort(key=len, reverse=True)

        if entities:
            trace.append("выделено: " + ", ".join(
                f"{label} «{'», «'.join(values)}»" for label, values in entities.items()))

        intent, confidence, trace = self._guards(raw, intent, confidence, trace)
        slots = self._slots(raw, intent, entities, trace)

        result = {
            "intent": intent,
            "confidence": round(float(confidence), 4),
            "ranked": [{"intent": name, "p": round(float(p), 4)} for name, p in ranked[:4]],
            "alternatives": [name for name, p in ranked[1:4] if p > 0.1],
            "slots": slots,
            "trace": trace,
            "engine": "spacy",
            "raw": raw,
        }
        result.update(self._presentation(intent, slots))
        return result

    # ---------------------------------------------------------- правила ----

    def _guards(self, raw: str, intent: str, confidence: float, trace: List[str]):
        """Немногочисленные жёсткие правила поверх модели."""
        if _DENIED_RE.search(raw):
            if intent != "denied":
                trace.append(f"правило безопасности: {intent} → denied")
            return "denied", max(confidence, 0.95), trace

        # Названный уровень громкости без слова сравнения — это установка, а не
        # шаг, и угадывать тут нечего. «Громкость на максимум» модель относила к
        # volume_up: слова «максимум» в её обучении почти нет, а «громкость»
        # тянет к увеличению. Признак однозначный, поэтому решает правило.
        if intent in ("volume_up", "volume_down", "volume_set"):
            level = self._volume_level(raw)
            relative = bool(_VOLUME_RELATIVE_RE.search(raw))
            if level is not None and not relative and intent != "volume_set":
                trace.append(f"правило: назван уровень {level}%, {intent} → volume_set")
                return "volume_set", max(confidence, 0.9), trace
            if relative and intent == "volume_set":
                trace.append("правило: это шаг, а не уровень — volume_set → volume_up/down")
                return ("volume_down" if re.search(r"(тише|потише|убав|уменьш|опусти|сбав|прикрут|скрут)",
                                                   raw, re.IGNORECASE) else "volume_up"), confidence, trace

        # «Что ты помнишь» и «что ты умеешь» отличаются одним словом, и модель
        # их путает: обе — короткий вопрос о самом себе. Но глагол здесь
        # однозначен, и различить их можно без сети. Повелительное «запомни»
        # исключено намеренно: это запись, а не чтение памяти.
        if _RECALL_RE.search(raw) and not re.search(r"\bзапомни\b", raw, re.IGNORECASE):
            if intent != "recall":
                trace.append(f"правило: спрашивают о памяти, {intent} → recall")
            return "recall", max(confidence, 0.9), trace

        lowered = raw.lower()
        has_url = bool(_URL_RE.search(raw))
        has_path = bool(_PATH_RE.search(raw))

        if has_url and intent in ("open_app", "open_folder", "smalltalk", "web_search"):
            # «зайди на habr.com» — домен в тексте не оставляет разночтений
            if not re.search(r"(загугли|погугли|найди|поищи|search|google)", lowered):
                trace.append(f"правило: домен в запросе, {intent} → open_url")
                return "open_url", max(confidence, 0.9), trace

        if has_path and intent in ("open_app", "smalltalk", "scan_files"):
            trace.append(f"правило: путь в запросе, {intent} → open_folder")
            return "open_folder", max(confidence, 0.9), trace

        return intent, confidence, trace

    # ------------------------------------------------------------ слоты ----

    def _slots(self, raw: str, intent: str, entities: Dict[str, str], trace: List[str]) -> dict:
        slots = {"app": None, "folder": None, "path": None, "url": None,
                 "site": None, "query": None, "text": None, "via": None,
                 "level": None, "amount": None}

        if intent == "volume_set":
            slots["level"] = self._volume_level(raw)
            if slots["level"] is None:
                trace.append("уровень не назван — переспрошу")
            else:
                trace.append(f"уровень громкости: {slots['level']}%")

        elif intent in ("volume_up", "volume_down"):
            # «Сделай громче НА 20» — шаг назван явно. Без числа шаг остаётся
            # значением по умолчанию, и это честно: пользователь его не задавал.
            step = self._volume_level(raw)
            if step is not None:
                slots["amount"] = step
                trace.append(f"шаг громкости: {step}%")

        if intent in ("open_app", "close_app"):
            candidate = self._app_target(raw, entities, trace)
            if candidate:
                slots["text"] = candidate
                match = match_dictionary(candidate, self.lex.app_aliases)
                if match and match["score"] >= 0.6:
                    slots["app"] = match["key"]
                    trace.append(f"цель: «{candidate}» → {self.lex.title_of('app', match['key'])}")
                else:
                    trace.append(f"цель: «{candidate}» — в словаре нет, ищу по компьютеру")

                # Steam упомянут прямо: искать надо в библиотеке игр, а не среди
                # программ, иначе «запусти в стиме кс 2» рискует открыть что-то
                # похожее по названию вместо самой игры.
                #
                # Проверка идёт ПОСЛЕ словаря и не срабатывает на самом Steam:
                # «запусти стим» — это запуск клиента, а не игры с названием
                # «стим», и подсказка агенту здесь была бы вредна.
                if (intent == "open_app" and slots["app"] != "steam"
                        and _VIA_STEAM_RE.search(raw)):
                    slots["via"] = "steam"
                    trace.append("указан Steam — запуск через библиотеку игр")

        elif intent == "open_folder":
            path = _PATH_RE.search(raw)
            if path:
                slots["path"] = path.group(1)
                slots["text"] = path.group(1)
                trace.append(f"путь: {slots['path']}")
            else:
                candidate = self._clean(_first(entities, "FOLDER", "PATH"))
                if candidate:
                    slots["text"] = candidate
                    match = match_dictionary(candidate, self.lex.folder_aliases)
                    if match and match["score"] >= 0.6:
                        slots["folder"] = match["key"]
                        trace.append(f"папка: «{candidate}» → {self.lex.title_of('folder', match['key'])}")
                    else:
                        trace.append(f"папка: «{candidate}» — по имени")

        elif intent == "open_url":
            found = _URL_RE.search(_first(entities, "URL")) or _URL_RE.search(raw)
            if found:
                slots["url"] = found.group(1)
                slots["text"] = slots["url"]
                trace.append(f"адрес: {slots['url']}")
            else:
                # Домена нет — значит сайт назван по имени, и адрес надо найти.
                named = self._clean(_first(entities, "SITE"))
                site = match_dictionary(named or raw, self.lex.site_aliases)
                if site and site["score"] >= 0.7:
                    slots["site"] = site["key"]
                    slots["url"] = self.lex.site_urls.get(site["key"])
                    slots["text"] = slots["url"]
                    trace.append(f"сайт по имени: {self.lex.site_titles.get(site['key'], site['key'])}"
                                 f" → {slots['url']}")

        elif intent in ("web_search", "scan_files", "timer", "remember", "math", "convert"):
            candidate = _first(entities, "QUERY")
            if candidate:
                candidate = self._clean(candidate)
                if candidate:
                    slots["query"] = candidate
                    slots["text"] = candidate
                    trace.append(f"аргумент: «{candidate}»")

        return slots

    def _app_target(self, raw: str, entities: Dict[str, List[str]], trace: List[str]) -> str:
        """
        Что именно запускать. Кандидаты берутся по очереди убывающей надёжности.

        Порядок сложился из разбора ошибок, и каждый шаг закрывает свой случай:

          1. отрезки, помеченные APP, — обычный путь;
          2. отрезки с другой меткой: «запусти ИГРУ амонг ас» разметчик иногда
             относит к QUERY, а команда всё та же, и терять из-за метки цель,
             которую он верно нашёл, незачем;
          3. снятие глагола и служебных слов — когда разметчик промолчал вовсе;
          4. словарь по всей фразе — последняя попытка.

        Отдельно отбрасываются кандидаты, состоящие ИЗ ОДНОГО слова «стим»:
        в «запусти в стиме кс 2» это не цель, а указание, где искать. Но если
        других кандидатов нет, слово остаётся: «запусти стим» — это запуск
        клиента Steam, и здесь оно как раз цель.
        """
        candidates: List[str] = []
        candidates.extend(entities.get("APP", []))
        for label, values in entities.items():
            if label not in ("APP", "URL", "PATH", "SITE"):
                candidates.extend(values)
        candidates.append(self._strip_lead(raw))
        candidates.append(self._fallback_app(raw) or "")

        cleaned = []
        for candidate in candidates:
            value = self._clean(candidate)
            if value and value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            return ""

        without_steam = [value for value in cleaned if not _STEAM_ONLY_RE.match(value)]
        pool = without_steam or cleaned
        if without_steam != cleaned and without_steam:
            trace.append("слово «Steam» — это указание, где искать, а не цель")
        return pool[0]

    def _strip_lead(self, raw: str) -> str:
        """
        Снимает глагол в начале и служебные слова: «запусти в стиме кс 2» → «кс 2».

        Только в начале — по той же причине, что и при сборке корпуса: порядок
        слов свободный, и угадывание в середине фразы дало бы неверную цель.
        """
        text = (raw or "").strip()
        verbs = sorted(set(self.lex.open_verbs + self.lex.close_verbs +
                           ["покажи", "хочу", "надо", "нужно", "можешь", "давай"]),
                       key=len, reverse=True)
        lowered = text.lower()
        for verb in verbs:
            if lowered.startswith(verb + " "):
                text = text[len(verb):].strip()
                break

        changed = True
        while changed:
            changed = False
            lowered = text.lower()
            for noise in _LEAD_NOISE:
                if lowered.startswith(noise):
                    text = text[len(noise):].strip()
                    changed = True
        return text.strip(" .,!?;:")

    @staticmethod
    def _volume_level(raw: str) -> Optional[int]:
        """
        Уровень громкости из фразы: число, процент или слово.

        Нужен именно уровень, а не «громче/тише». «Поставь громкость на 30» при
        текущих восьмидесяти — это УМЕНЬШЕНИЕ, и без числа команду не выполнить,
        сколько ни жми клавишу: агент бил бы наугад и попадал случайно.
        """
        lowered = (raw or "").lower()
        for word, value in _VOLUME_WORDS.items():
            if word in lowered:
                return value
        match = _NUMBER_RE.search(lowered)
        if match:
            return min(100, max(0, int(match.group(1))))
        return None

    def _fallback_app(self, raw: str) -> Optional[str]:
        """
        Если разметчик цели промолчал, пробуем словарь по всей фразе.

        Такое бывает на коротких репликах вроде «дискорд» — там нет глагола, за
        который мог бы зацепиться ner, но приложение названо прямо.
        """
        match = match_dictionary(raw, self.lex.app_aliases)
        if match and match["score"] >= 0.75:
            for alias in [match["key"]] + list(self.lex.app_aliases[match["key"]]):
                position = normalize(raw).find(alias)
                if position >= 0:
                    return alias
        return None

    @staticmethod
    def _clean(value: str) -> str:
        words = [word for word in value.strip().split() if not _NOISE.match(word)]
        return " ".join(words).strip(" .,!?;:")

    # ------------------------------------------------------ представление --

    def _presentation(self, intent: str, slots: dict) -> dict:
        """Заголовок, иконка и каноническая цель — то, что покажет интерфейс."""
        if intent in ("open_app", "close_app"):
            if slots["app"]:
                return {"target": slots["app"],
                        "title": self.lex.title_of("app", slots["app"]),
                        "icon": self.lex.icon_of(slots["app"])}
            if slots["text"]:
                return {"target": slots["text"], "title": slots["text"],
                        "icon": "game" if slots["via"] == "steam" else "app",
                        "via": slots["via"]}

        if intent == "volume_set":
            level = slots["level"]
            return {"target": None, "icon": "volume", "level": level,
                    "title": f"Громкость {level}%" if level is not None else "Громкость"}

        if intent in ("volume_up", "volume_down"):
            return {"target": None, "icon": "volume", "amount": slots["amount"],
                    "title": "Громкость +" if intent == "volume_up" else "Громкость −"}

        if intent in ("record_screen", "record_stop"):
            return {"target": None, "icon": "video",
                    "title": "Запись экрана" if intent == "record_screen" else "Запись остановлена"}

        if intent == "open_folder":
            if slots["path"]:
                return {"target": slots["path"], "title": slots["path"], "icon": "folder"}
            if slots["folder"]:
                return {"target": slots["folder"],
                        "title": self.lex.title_of("folder", slots["folder"]), "icon": "folder"}
            if slots["text"]:
                return {"target": slots["text"], "title": slots["text"], "icon": "folder"}

        if intent == "open_url" and slots["url"]:
            title = self.lex.site_titles.get(slots["site"]) if slots["site"] else None
            return {"target": slots["url"], "title": title or slots["url"], "icon": "globe"}

        if intent == "web_search" and slots["query"]:
            return {"target": slots["query"], "query": slots["query"],
                    "title": "Поиск: " + slots["query"], "icon": "globe"}

        if intent == "scan_files":
            query = slots["query"]
            return {"target": query, "query": query,
                    "title": ("Поиск: " + query) if query else "Сканирование файлов",
                    "icon": "search"}

        return {"target": slots.get("text")}


@lru_cache(maxsize=4)
def get_parser(model_dir: str = DEFAULT_MODEL_DIR) -> Parser:
    """Один разбор на процесс — загрузка модели занимает секунду с лишним."""
    return Parser.load(model_dir)
