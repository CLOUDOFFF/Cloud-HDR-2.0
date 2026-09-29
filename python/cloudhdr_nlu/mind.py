"""
Cloud HDR Mind — общий слой над тремя мозгами проекта.

В проекте три разных механизма понимания, и до сих пор каждый отвечал сам за
себя: перцептрон в браузере, модель на spaCy и языковая модель. Работали они
по очереди — кто первый подошёл, тот и ответил. Отсюда и брались провалы,
которые видно на измерениях: «не открывай хром» разбиралось как запуск с
уверенностью 99%, «открой хром и найди там котиков» теряло первую половину, а
«а теперь то же самое» не значило вообще ничего.

Ни одна из этих бед не лечится весами. Их причина в том, что разбор возвращал
ОДНО намерение для ВСЕЙ фразы — а фраза не всегда одна команда, иногда она
отмена команды, а иногда ссылка на предыдущую.

Здесь разбор возвращает ПЛАН: последовательность шагов и то, как с ней быть.
Порядок обработки — от дешёвого и однозначного к дорогому и приблизительному:

  1. отрицание      — «не открывай хром» отменяет действие, а не выполняет его;
  2. контекст       — «закрой его», «а теперь то же самое» получают цель из
                      предыдущей реплики ДО того, как их увидит модель;
  3. композиция     — фраза делится на части, и каждая разбирается отдельно;
     причём делится не по запятым «на глаз»: разбиение принимается, только если
     модель уверенно опознала команду в КАЖДОЙ части (см. `split`);
  4. модель         — то, ради чего всё и делалось;
  5. языковая модель — последняя надежда, когда модель не уверена: она не
                      выполняет команду, а лишь подсказывает, чем фраза была.

Каждый шаг может отказаться от работы и передать дальше. Ни один не притворяется,
что понял: уверенность откалибрована, и низкая уверенность здесь — рабочий
сигнал, а не повод угадывать.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .branding import log
from .parse import Parser

# --------------------------------------------------------------- отрицание --

#: «Не открывай», «не надо запускать», «отмени», «передумал».
#:
#: Отдельного намерения для отмены в проекте нет, и заводить его ради этого не
#: стоит: отмена — не команда Windows, а разговор о команде. Поэтому такая фраза
#: не выполняется вовсе, а получает вежливый ответ.
_NEGATION_RE = re.compile(
    r"^\s*(не\s+(надо|нужно|стоит|хочу)\s+\w+|не\s+(открыв|запуск|включ|закрыв|дела|пиш|ищ|стави)\w*"
    r"|отмени\w*|передумал\w*|забудь\b|отставить\b|ничего\s+не\s+делай)",
    re.IGNORECASE,
)

#: «Стоп» и «хватит» отменяют не всё подряд: «стоп запись» — это команда
#: остановки записи, а не отказ от действия. Такие фразы отрицанием не считаем.
_NOT_NEGATION_RE = re.compile(r"(запис|записыв|таймер|звук|громкост)", re.IGNORECASE)


# ---------------------------------------------------------------- контекст --

#: Местоимения, за которыми стоит цель из прошлой реплики.
#:
#: «Там» и «туда» сюда не входят намеренно, хотя тоже указывают на прошлое.
#: Они обозначают МЕСТО, а не предмет: в «открой хром и найди там котиков»
#: подстановка цели превратила бы поиск котиков в поиск хрома.
_PRONOUN_RE = re.compile(r"\b(его|её|ее|их|это|этот|эту|тем\s+же)\b", re.IGNORECASE)

#: Фраза целиком состоит из отсылки: «а теперь то же самое», «и там же».
_ONLY_REFERENCE_RE = re.compile(
    r"^\s*(а\s+)?(теперь\s+)?(то\s+же\s+самое|тоже\s+самое|так\s+же|там\s+же|ещё\s+раз|еще\s+раз|повтори)\s*[.!?]*\s*$",
    re.IGNORECASE,
)


# -------------------------------------------------------------- композиция --

#: Где фраза может разойтись на две команды.
_SPLIT_RE = re.compile(
    # Союз и следом необязательное «потом/затем/заодно». Уточнения записаны
    # вместе со своим пробелом: если вынести \s+ наружу, союз «и» уже съест
    # пробел, и на простом «открой хром И найди котиков» разбиение не сработает.
    r"\s+(?:и|а)\s+(?:потом\s+|затем\s+|заодно\s+|также\s+|ещё\s+|еще\s+|сразу\s+)?"
    r"|\s+(?:потом|затем|после\s+этого)\s+"
    r"|\s*[;,]\s+(?=открой|запусти|включи|закрой|найди|поищи|сделай|загугли|поставь|запиши|покажи|зайди|перейди)",
    re.IGNORECASE,
)

#: Ниже этого порога часть фразы командой не считается — значит, делить нельзя.
SPLIT_TRUSTED = 0.55

#: Ниже этого порога разбор считается неуверенным и зовёт языковую модель.
UNSURE = 0.35


class Mind:
    """Разбор фразы в план действий. Держит модель и мост к языковой модели."""

    def __init__(self, parser: Parser, llm_base: Optional[str] = None,
                 wiki_enabled: bool = True):
        self.parser = parser
        self.llm = LanguageModelBridge(llm_base)
        #: Единственное место в проекте, где происходит обращение в сеть.
        #: Выключается флагом — тогда всё работает как раньше, офлайн.
        self.wiki_enabled = wiki_enabled

    #: Намерения, которые отвечают сами и в Википедию не ходят: у них есть
    #: собственный точный ответ — расчёт, память, дата, список команд.
    _OWN_ANSWER = frozenset({
        "math", "convert", "datetime", "remember", "recall", "timer",
        "settings", "help", "identity", "greeting", "thanks", "denied",
        "analyze_chats",
    })

    def _is_world_question(self, text: str, step: dict) -> bool:
        """Стоит ли идти в Википедию за ответом на эту фразу."""
        try:
            import cloudhdr_wiki
        except ImportError:
            return False

        # Строгая проверка: вопросительный зачин и никакого «ты/я/как дела».
        # По одному «?» на конце сюда попадала светская болтовня, и на «как
        # дела?» приходил ответ про фильм «Как дома, как дела?».
        if not cloudhdr_wiki.is_world_question(text):
            return False
        if step["intent"] in self._OWN_ANSWER:
            # Исключение для счёта и дат: «сколько планет в солнечной системе»
            # разбирается как вычисление, но считать там нечего — это вопрос о
            # мире. Отличаем по цифрам: у настоящего расчёта они есть всегда.
            # datetime сюда не входит: «сколько времени» — это часы, а не вопрос
            # о мире, и отправив его в Википедию, мы сломали бы рабочую команду
            # (находилась «Машина времени»).
            if step["intent"] in ("math", "convert") and not re.search(r"\d", text):
                return True
            return False
        # Уверенно распознанную команду не трогаем: «открой хром?» с вопросом
        # на конце — всё равно команда, а не вопрос о мире.
        if step["intent"] in self.parser.lex.executable and step["confidence"] >= UNSURE:
            return False
        return True

    def _from_wiki(self, text: str) -> Optional[dict]:
        try:
            import cloudhdr_wiki

            if not cloudhdr_wiki.is_world_question(text):
                return None
            return cloudhdr_wiki.answer(text)
        except Exception:
            # Нет сети, нет модуля, ошибка разбора — не повод ронять разбор
            # команды. Просто отвечаем как раньше.
            return None

    # ------------------------------------------------------------ основное --

    def plan(self, text: str, context: Optional[Dict[str, Any]] = None) -> dict:
        raw = (text or "").strip()
        context = context or {}
        if not raw:
            return self._empty()

        trace: List[str] = []

        # 1. Отрицание. Раньше «не открывай хром» разбиралось как запуск хрома
        #    с уверенностью 99% — уверенно и ровно наоборот.
        negated = self._negation(raw)
        if negated:
            trace.append("это отмена действия, а не команда")
            return self._single(negated, trace, route="chat")

        # 2. Контекст. Местоимение надо развернуть ДО модели: «закрой его» без
        #    подстановки — фраза без цели, и никакая сеть цель из неё не достанет.
        resolved, note = self._resolve(raw, context)
        if note:
            trace.append(note)

        # 3. Композиция.
        parts = self.split(resolved)
        if len(parts) > 1:
            trace.append(f"в запросе {len(parts)} команды — выполню по очереди")
            steps = [self.parser.parse(part, context) for part in parts]
            return self._many(steps, trace)

        # 4. Модель.
        step = self.parser.parse(resolved, context)

        # 4a. Вопрос о мире. Своя языковая модель знаний не имеет и на «кто такой
        #     Гагарин» сочиняет правдоподобную неправду — а это хуже молчания.
        #     Википедия даёт факт.
        #
        #     Условие шире, чем «намерение похоже на болтовню», и вот почему.
        #     «Кто изобрёл лампочку» разбор относил к запуску программы с
        #     уверенностью в двадцать процентов — то есть не понял, но формально
        #     это была команда, и до Википедии дело не доходило. Поэтому идём
        #     туда во всех случаях, когда фраза выглядит вопросом и при этом
        #     НЕ является уверенно распознанной командой или навыком.
        if self.wiki_enabled and self._is_world_question(resolved, step):
            found = self._from_wiki(resolved)
            if found:
                trace.append(f"ответ из Википедии: «{found['title']}»")
                answer = dict(step)
                answer["intent"] = "chat"
                answer["reply"] = found["text"]
                answer["source_url"] = found["url"]
                answer["confidence"] = max(step["confidence"], 0.8)
                answer["engine"] = "wikipedia"
                return self._single(answer, trace, route="chat")

        # 5. Языковая модель — только при сомнении и только как подсказка.
        if step["confidence"] < UNSURE:
            hint = self.llm.classify(resolved, self.parser.lex.intents)
            if hint and hint != step["intent"]:
                trace.append(f"модель сомневалась ({step['confidence'] * 100:.0f}%), "
                             f"языковая модель предполагает {hint}")
                second = self.parser.parse(resolved, context)
                second["intent"] = hint
                second["confidence"] = max(step["confidence"], 0.4)
                second["engine"] = "spacy+llm"
                return self._single(second, trace, route="execute")
            # Подсказки нет. Тогда действие, в котором мы не уверены, НЕ
            # выполняется: цена ошибки несимметрична. Переспросить — секунда
            # неудобства, а запустить не то по догадке в 20% — уже последствия
            # на чужом компьютере. Ответ остаётся, но команда не уходит агенту.
            if step["intent"] in self.parser.lex.executable:
                trace.append(f"уверенность {step['confidence'] * 100:.0f}% — выполнять по догадке не буду")
                step = dict(step)
                step["reply"] = (
                    "Не уверен, что правильно понял. Переформулируйте, пожалуйста, — "
                    "или скажите «что ты умеешь», и я покажу список команд.")
                return self._single(step, trace, route="chat")
            trace.append(f"уверенность низкая ({step['confidence'] * 100:.0f}%) — уточню, если ошибусь")

        return self._single(step, trace)

    # ---------------------------------------------------------- отрицание ---

    def _negation(self, raw: str) -> Optional[dict]:
        if not _NEGATION_RE.search(raw) or _NOT_NEGATION_RE.search(raw):
            return None
        return {
            "intent": "chat",
            "confidence": 0.95,
            "ranked": [],
            "alternatives": [],
            "slots": {},
            "trace": [],
            "engine": "правило отрицания",
            "raw": raw,
            "target": None,
            "reply": "Хорошо, ничего не делаю.",
        }

    # ------------------------------------------------------------ контекст --

    def _resolve(self, raw: str, context: Dict[str, Any]):
        """Подставляет цель из прошлой реплики вместо местоимения."""
        last = context.get("lastTarget") or (context.get("lastApp") or {}).get("title")
        if not last:
            return raw, ""

        if _ONLY_REFERENCE_RE.match(raw):
            previous = context.get("lastPhrase")
            if previous:
                return previous, f"«{raw}» — повторяю прошлую команду: «{previous}»"
            return raw, ""

        if _PRONOUN_RE.search(raw):
            replaced = _PRONOUN_RE.sub(str(last), raw, count=1)
            if replaced != raw:
                return replaced, f"«{raw}» → цель из прошлой реплики: {last}"
        return raw, ""

    # ---------------------------------------------------------- композиция --

    def split(self, raw: str) -> List[str]:
        """
        Делит фразу на команды — но только если делить действительно есть что.

        Разбиение по союзу «и» само по себе ненадёжно: «найди фильм и сериал» —
        одна команда с двумя целями, а «открой хром и сделай скриншот» — две
        разные. Формальными признаками их не различить, зато у нас есть модель:
        разбиение принимается, ТОЛЬКО если в каждой части она уверенно видит
        исполнимую команду. Иначе фраза остаётся целой.
        """
        parts = [part.strip(" ,.;") for part in _SPLIT_RE.split(raw) if part and part.strip(" ,.;")]
        if len(parts) < 2:
            return [raw]

        executable = self.parser.lex.executable
        for part in parts:
            if len(part) < 3:
                return [raw]
            probe = self.parser.parse(part)
            if probe["intent"] not in executable or probe["confidence"] < SPLIT_TRUSTED:
                return [raw]
        return parts

    # ------------------------------------------------------------- ответы ---

    @staticmethod
    def _empty() -> dict:
        return {"steps": [], "route": "none", "trace": [], "confidence": 0.0, "engine": "mind"}

    def _single(self, step: dict, trace: List[str], route: str = "execute") -> dict:
        step = dict(step)
        step["trace"] = trace + list(step.get("trace", []))
        if route == "chat":
            route_final = "chat"
        else:
            route_final = "execute" if step["intent"] in self.parser.lex.executable else "answer"
        return {
            "steps": [step],
            "route": route_final,
            "trace": step["trace"],
            "confidence": step["confidence"],
            "engine": step.get("engine", "spacy"),
            **{k: v for k, v in step.items() if k not in ("trace",)},
        }

    def _many(self, steps: List[dict], trace: List[str]) -> dict:
        first = dict(steps[0])
        first["trace"] = trace + list(first.get("trace", []))
        return {
            "steps": steps,
            "route": "execute",
            "trace": first["trace"],
            "confidence": min(step["confidence"] for step in steps),
            "engine": "mind",
            **{k: v for k, v in first.items() if k not in ("trace",)},
        }


# ------------------------------------------------- мост к языковой модели ---

class LanguageModelBridge:
    """
    Связь с локальной языковой моделью — та же, что ищет llm.js.

    Зовём её только когда модель команд не уверена, и просим не выполнять
    команду, а всего лишь назвать намерение из списка. Причина простая: у
    языковой модели есть знание языка, которого нет у классификатора на
    семнадцати тысячах фраз, но нет никакой ответственности за последствия.
    Пусть подсказывает, а решает и выполняет по-прежнему разбор.

    Нет движка — метод молча возвращает None, и всё работает как раньше.
    """

    PORTS = (11434, 1234, 8080)

    def __init__(self, base: Optional[str] = None):
        self.base = base
        self.checked = base is not None
        self.model: Optional[str] = None

    def _discover(self) -> None:
        if self.checked:
            return
        self.checked = True
        for port in self.PORTS:
            base = f"http://127.0.0.1:{port}"
            try:
                with urllib.request.urlopen(base + "/v1/models", timeout=1.5) as response:
                    data = json.load(response)
            except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
                continue
            models = [item.get("id") for item in data.get("data", []) if item.get("id")]
            if models:
                self.base, self.model = base, models[0]
                log(f"Языковая модель найдена: {self.model} ({base})")
                return

    def classify(self, text: str, intents: List[str]) -> Optional[str]:
        self._discover()
        if not self.base:
            return None

        prompt = (
            "Определи, чего хочет пользователь. Ответь ОДНИМ словом из списка и ничем больше.\n"
            f"Список: {', '.join(intents)}\n"
            f"Запрос: {text}\n"
            "Ответ:"
        )
        body = json.dumps({
            "model": self.model or "local",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 12,
            "stream": False,
        }).encode("utf-8")

        try:
            request = urllib.request.Request(
                self.base + "/v1/chat/completions", body, {"Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=6) as response:
                data = json.load(response)
            answer = data["choices"][0]["message"]["content"].strip().lower()
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError, json.JSONDecodeError):
            return None

        # Модель почти наверняка добавит лишнего — берём первое известное слово.
        for word in re.findall(r"[a-z_]+", answer):
            if word in intents:
                return word
        return None
