"""
Cloud HDR AI — HTTP-сервер модели, совместимый с диалектом OpenAI.

Почему именно этот протокол. В проекте уже есть public/js/llm.js — мост к
локальной языковой модели. Он опрашивает 127.0.0.1 на портах 11434, 1234 и 8080,
спрашивает /v1/models и шлёт запросы в /v1/chat/completions с потоковой отдачей.
Написан он был под Ollama, LM Studio и llama.cpp, но протокол у них общий, и
если говорить на нём же, фронтенд не нужно править ВООБЩЕ: страница сама найдёт
модель на 8080, покажет её в настройках и начнёт с ней разговаривать.

Разделение ролей в приложении остаётся прежним и осознанным:
    • brain.js решает, ЧТО сделать с Windows, за миллисекунды;
    • эта модель подключается там, где команды нет, — на свободный разговор.

CORS открыт для локальных адресов: страница живёт на 127.0.0.1:4477, а модель —
на другом порту, и без заголовков доступа браузер запрос не выпустит.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import torch

from . import codebank
from . import device as dev
from . import knowledge
from . import profiles
from .branding import (BRAND_AI, MODEL_ID, MODEL_ID_ULTRA, MODEL_LABEL,
                       MODEL_LABEL_ULTRA, VERSION, banner, log)
from .config import DEFAULT_MODEL, resolve_model_name
from .data import STOP_SEQUENCES, SYSTEM_PROMPT, format_messages

# Запас токенов сверх запрошенного — на то, чтобы договорить начатое.
# Шестидесяти хватает на длинную русскую фразу; коду нужно на порядок больше, и
# сколько именно — решает профиль (см. profiles.GRACE). Здесь только значение по
# умолчанию для вызовов без профиля: разговор в терминале, старые клиенты.
GRACE_TOKENS = 60

# Чем кончается законченная мысль. Правила остановки для разговора, стихов и
# кода лежат в profiles.can_finish: у кода предложений нет, и ждать точку там —
# ровно та ошибка, из-за которой программа обрывалась на середине.
SENTENCE_ENDS = profiles.SENTENCE_ENDS

# FastAPI импортируется здесь, на уровне модуля, а не внутри build_app — и это не
# вопрос стиля, а условие работоспособности.
#
# В файле стоит `from __future__ import annotations`, поэтому все аннотации
# остаются СТРОКАМИ и вычисляются позже. FastAPI разрешает их через
# get_type_hints, а тот смотрит в глобальные имена модуля — то есть в
# __globals__ функции. Импорт внутри build_app кладёт Request в ЛОКАЛЬНУЮ область
# этой функции, и снаружи имя не видно: строка "Request" не разрешается ни во
# что, FastAPI не узнаёт свой служебный тип и считает параметр обычным — то есть
# ждёт его в строке запроса.
#
# Наружу это выходило так: /health и /v1/models отвечают как ни в чём не бывало,
# а на каждый запрос к /v1/chat/completions приходит 422 «Field required» —
# то есть интерфейс находит модель, показывает её в шапке и не может получить
# ни одного ответа.
try:
    from fastapi import FastAPI, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, StreamingResponse
except ImportError:      # без fastapi живут train, chat и eval — но не serve
    FastAPI = Request = CORSMiddleware = JSONResponse = StreamingResponse = None

#: Порт по умолчанию — из списка, который llm.js проверяет сам.
DEFAULT_PORT = 8080

#: Где искать веса, если по указанному пути их не оказалось, — от новых к старым.
#:
#: Нужно, чтобы обновление модели не требовало править .bat-файлы и ярлык на
#: рабочем столе: обучение кладёт новые веса в checkpoints/v2, старые остаются на
#: месте, и приложение само берёт те, что есть. Порядок здесь — приоритет, а не
#: перебор наугад: подставленную модель сервер называет в логе, чтобы не гадать,
#: какие именно веса сейчас отвечают.
#:   v6 — 760M, дообучена на коде и стихах (профиль Ultra), основная
#:   v5 — 760M, ночное обучение на расширенном разговорном корпусе
#:   v4 — 760M, обучена через LoRA
#:   v3 — 356M, полное обучение, запасная
#:   v2 — 125M на русской болванке
#:   .  — 124M на английской болванке, самая первая
#:
#: Самая свежая папка стоит первой, даже если её ещё нет, и это сделано
#: намеренно: пока весов нет, поиск молча спускается на предыдущие и всё
#: работает как прежде, а как только обучение их создаст — приложение возьмёт
#: новые само, без правки кода и без ручного перезапуска чего бы то ни было.
CHECKPOINT_FALLBACKS = (
    "checkpoints/v6/best.pt",
    "checkpoints/v5/best.pt",
    "checkpoints/v4/best.pt",
    "checkpoints/v3/best.pt",
    "checkpoints/v2/best.pt",
    "checkpoints/best.pt",
)


def resolve_checkpoint(checkpoint: Optional[str]) -> Optional[str]:
    """Путь к весам: заданный, если он есть, иначе первый существующий запасной."""
    if checkpoint and Path(checkpoint).exists():
        return checkpoint

    for candidate in CHECKPOINT_FALLBACKS:
        if Path(candidate).exists():
            if checkpoint:
                log(f"Чекпоинт {checkpoint} не найден — беру {candidate}")
            return candidate
    return checkpoint


#: Строка для замера того, насколько словарь дружелюбен к русскому языку.
_CYRILLIC_PROBE = "Расскажи, пожалуйста, почему небо голубое, а закат красный."


def choose_repetition_penalty(tokenizer) -> float:
    """
    Подбирает штраф за повтор под словарь модели.

    Величина не универсальная, и раньше она была прибита к 1.05 — значение,
    подобранное под байтовый словарь GPT-2. Причина там была такая: русская
    буква занимала два-три байтовых токена, эти токены неизбежно повторяются в
    любом русском тексте (они и есть алфавит), и наказывать повтор значило
    наказывать сам язык — на 1.1 рвались последовательности UTF-8 и в ответе
    появлялись битые знаки.

    С русским словарём этой беды нет: токен — это кусок слова, и его повтор
    действительно означает, что модель зациклилась. Поэтому штраф можно вернуть
    к обычным 1.1, что заметно помогает маленькой модели не уходить по кругу.

    Тип словаря определяется замером, а не именем болванки: сколько символов
    русского текста приходится на токен. Меньше двух — словарь байтовый.
    """
    tokens = len(tokenizer(_CYRILLIC_PROBE, add_special_tokens=False)["input_ids"])
    chars_per_token = len(_CYRILLIC_PROBE) / max(tokens, 1)
    return 1.05 if chars_per_token < 2.0 else 1.1


class Engine:
    """Модель, токенизатор и всё, что нужно для генерации."""

    def __init__(self, model, tokenizer, info: dev.DeviceInfo, precision: str, label: str):
        self.model = model
        self.tokenizer = tokenizer
        self.info = info
        self.precision = precision
        self.label = label
        self.busy = False
        self.repetition_penalty = choose_repetition_penalty(tokenizer)
        #: Подставлять ли справку из Википедии перед ответом.
        self.wiki = True
        #: Статья, использованная в последнем ответе, — для /health и отладки.
        self.last_source: Optional[dict] = None
        #: Выгрузка в простое: где сейчас веса и когда модель спрашивали.
        self.on_gpu = info.is_cuda
        self.last_used = time.time()
        self.placement = threading.Lock()

    # ---------------------------------------------------- выгрузка в простое --

    #: Сколько секунд без вопросов, прежде чем освободить видеокарту.
    IDLE_SECONDS = 180

    def wake(self) -> None:
        """Перед ответом: вернуть веса на видеокарту, если они были выгружены."""
        self.last_used = time.time()
        if not self.info.is_cuda or self.on_gpu:
            return
        with self.placement:
            if self.on_gpu:
                return
            started = time.time()
            self.model.to(self.info.device)
            self.on_gpu = True
            log(f"Модель вернулась на видеокарту за {time.time() - started:.2f} с")

    def sleep_if_idle(self) -> None:
        """
        Модель долго не спрашивали — веса уезжают в оперативную память.

        Видеокарта свободна для игр и всего остального, а вернуть 1.5 ГБ из
        оперативной памяти — доли секунды, а не 10-30 секунд чтения с диска.
        Во время ответа не выгружаем никогда.
        """
        if not self.info.is_cuda or not self.on_gpu or self.busy:
            return
        if time.time() - self.last_used < self.IDLE_SECONDS:
            return
        with self.placement:
            if not self.on_gpu or self.busy:
                return
            self.model.to("cpu")
            self.on_gpu = False
            torch.cuda.empty_cache()
            log(f"Простой {self.IDLE_SECONDS // 60} мин — видеокарта освобождена, веса в оперативной памяти")

    @classmethod
    def load(
        cls,
        checkpoint: Optional[str] = None,
        model_name: str = DEFAULT_MODEL,
        prefer_device: str = "auto",
        precision: str = "auto",
    ) -> "Engine":
        from .data import TOKENIZER_DIRNAME, load_tokenizer
        from .model import CloudHDRGPT
        from .train import load_checkpoint

        info = dev.preflight(prefer_device)
        if info.is_cuda and not info.compatible:
            log("Переключаюсь на CPU: сборка PyTorch не умеет работать с этой картой")
            info = dev.detect("cpu")

        resolved = resolve_model_name(model_name)

        # Модель загружаем ПЕРВОЙ, и это не косметика. Чекпоинт хранит имя
        # болванки, с которой начинались его веса, а словарь у русской и
        # английской болванок разный при почти одинаковом размере. Возьми мы
        # токенизатор раньше — пришлось бы гадать по флагу --model, и старый
        # чекпоинт с новым значением по умолчанию читался бы чужим словарём:
        # модель работала бы, но выдавала бы бессмыслицу.
        checkpoint = resolve_checkpoint(checkpoint)

        if checkpoint and Path(checkpoint).exists():
            model = load_checkpoint(Path(checkpoint), info.device)
            label = f"дообученная · {Path(checkpoint).parent.name}/{Path(checkpoint).name}"
        else:
            if checkpoint:
                log(f"Обученных весов нет — беру исходную болванку")
            model = CloudHDRGPT.from_pretrained(resolved).to(info.device)
            label = f"болванка · {resolved}"

        # Токенизатор ищем рядом с чекпоинтом: при работе без интернета копия
        # в checkpoints/tokenizer/ — единственный источник, откуда его взять.
        local_tokenizer = None
        if checkpoint:
            local_tokenizer = Path(checkpoint).parent / TOKENIZER_DIRNAME
        tokenizer = load_tokenizer(model.base_model or resolved, local_dir=local_tokenizer)

        # Последний рубеж: словарь токенизатора обязан совпадать с матрицей
        # эмбеддингов. Если не совпал — дальше идти нельзя, наружу пойдёт мусор.
        if len(tokenizer) > model.config.vocab_size:
            raise SystemExit(
                f"Токенизатор ({len(tokenizer)} токенов) не подходит к модели "
                f"({model.config.vocab_size}). Укажите верную болванку через --model "
                f"или положите её копию в {local_tokenizer}"
            )

        model.eval()
        chosen = dev.choose_precision(info, precision)
        dev.apply_speedups(info)

        # Веса — сразу в bf16. Раньше они лежали на карте в fp32 (2.9 ГБ), а на
        # время ответа autocast заводил рядом их bf16-копию (ещё 1.4 ГБ). Считала
        # модель всё равно в bf16, так что ответы те же, а памяти вдвое меньше и
        # никаких временных копий. На fp16-картах так не делаем: у fp16 узкий
        # диапазон, и хранить в нём веса рискованнее, чем считать.
        if info.is_cuda and chosen == "bf16":
            model.to(torch.bfloat16)
            torch.cuda.empty_cache()

        engine = cls(model, tokenizer, info, chosen, label)
        engine.release()
        log(f"Модель готова: {model.describe()} · {chosen} · {label}")
        log(f"Штраф за повтор: {engine.repetition_penalty} (подобран под словарь)")
        log(f"Видеопамять: {dev.memory_report(info)}")
        return engine

    def release(self) -> None:
        """
        Возвращает драйверу видеопамять, которую аллокатор придержал про запас.

        Нужно вот из-за чего. Аллокатор PyTorch кэширующий: освобождённые блоки
        он драйверу не отдаёт, а держит у себя под следующий запрос — так быстрее
        и так правильно для обучения, где размеры от шага к шагу одни и те же.
        Но у нас между запросами модель просто стоит и ждёт человека, а место на
        карте всё это время числится занятым.

        Держится при этом не мелочь. На время генерации autocast заводит рядом с
        весами их копии в bf16 — считает-то он в bf16, а лежат веса в fp32, — и
        это ещё 1.4 ГБ поверх 2.9 ГБ самих весов. Копии живут, пока не закрыт
        контекст autocast, то есть всю генерацию целиком; после неё они не нужны
        никому, но без этого вызова остались бы на карте до перезапуска.

        На ответы не влияет ВООБЩЕ: очищается только кэш свободных блоков, а всё,
        на что есть живая ссылка — веса, токенизатор, — остаётся на месте.
        Следующий запрос просто заново попросит память у драйвера, это доли
        секунды на фоне генерации.
        """
        if self.info.is_cuda:
            torch.cuda.empty_cache()

    # ------------------------------------------------------------ генерация --

    def stream(self, messages: list, temperature: float, max_tokens: int,
               top_p: float, top_k: int, finish: str = "sentence",
               grace: int = GRACE_TOKENS, system: Optional[str] = None,
               wiki: Optional[bool] = None,
               repetition_penalty: Optional[float] = None):
        """
        Отдаёт куски текста по мере генерации, обрывая ответ на стоп-фразе.

        Системная подсказка подставляется ЗДЕСЬ, а не только в HTTP-обработчике.
        Раньше её добавлял лишь эндпоинт, и разговор в терминале («chat») уходил
        в модель без неё — то есть в формате, которого при обучении не было: там
        системная подсказка стоит в каждом диалоге. Вызов идемпотентный, так что
        двойного применения можно не бояться.

        `finish` и `grace` задают, чем ответ имеет право закончиться и сколько
        токенов сверх бюджета на это отводится. Значения по умолчанию — те же,
        что были до появления профилей: разговорная фраза и шестьдесят токенов.

        `wiki` перекрывает общую настройку движка на один запрос: у профиля кода
        справка выключена всегда, потому что на «напиши сортировку» статья
        «Сортировка» подставит определение вместо программы.
        """
        prepared = apply_system(messages, system) if system else normalize_system(messages)
        use_wiki = self.wiki if wiki is None else (self.wiki and wiki)

        # Справка из Википедии подставляется перед генерацией: модель на 356M
        # фактов не помнит и на их месте сочиняет правдоподобное. Со справкой её
        # работа сводится к пересказу того, что уже лежит в контексте.
        if use_wiki:
            question = next(
                (str(m.get("content", "")) for m in reversed(prepared)
                 if isinstance(m, dict) and m.get("role") == "user"),
                "",
            )
            found = knowledge.lookup(question)
            if found:
                # Факт отдаётся ДОСЛОВНО, а не пересказывается моделью.
                # Почему пересказ пришлось отменить — подробно в
                # knowledge.direct_answer: на 356M модель переписывает даты и
                # имена своими, и гладкий ответ выходит убедительнее правды.
                log(f"Справка: «{found['title']}» — отвечаю по ней дословно")
                self.last_source = found
                self.busy = True
                try:
                    yield knowledge.direct_answer(found)
                finally:
                    self.busy = False
                return

        prompt = format_messages(prepared, add_generation_prompt=True)
        ids = self.tokenizer(prompt, add_special_tokens=False)["input_ids"]

        # Оставляем место под ответ: контекст общий на вопрос и ответ.
        #
        # Запас берётся из профиля, а не из константы: у кода он в семь раз
        # больше разговорного, и посчитать место по разговорному значению значило
        # бы отдать под ответ меньше, чем ему разрешено сгенерировать.
        room = self.model.config.block_size - max_tokens - grace
        if room < 1:
            # Просьба не помещается в окно вместе с ответом. Режем ОТВЕТ, а не
            # вопрос: обрезанный вопрос модель поймёт неверно и ответит не на то.
            room = max(1, self.model.config.block_size // 4)
            max_tokens = max(1, self.model.config.block_size - room - grace)
        if len(ids) > room:
            # Обрезаем с ХВОСТА истории, а системную подсказку сохраняем целиком.
            #
            # Раньше здесь стояло ids[-room:] — срез с начала. При длинном
            # разговоре первым делом отваливалась именно системная подсказка, то
            # есть единственное место, где написано «будь кратким», «не выдумывай
            # про компьютер пользователя» и «не притворяйся, что выполнил
            # действие». Модель теряла все правила разом и оставалась наедине с
            # собственными прошлыми ответами — отсюда и жалобы, что она отвечает
            # сама себе. Причём чем дольше шёл разговор, тем хуже: ровно наоборот
            # к ожиданию.
            head = self.tokenizer(
                format_messages(prepared[:1], add_generation_prompt=False),
                add_special_tokens=False,
            )["input_ids"] if prepared and prepared[0].get("role") == "system" else []

            keep = max(0, room - len(head))
            ids = (head + ids[-keep:]) if keep else ids[-room:]

        self.busy = True
        self.wake()
        idx = torch.tensor([ids], dtype=torch.long, device=self.info.device)
        generated: list = []
        text = ""
        overflow = ""          # сгенерировано сверх бюджета и ещё не отдано

        try:
            with dev.autocast_context(self.info.type, self.precision), torch.no_grad():
                for token in self.model.generate(
                    idx,
                    max_new_tokens=max_tokens + grace,
                    temperature=temperature,
                    top_k=top_k or None,
                    top_p=top_p,
                    eos_token_id=self.tokenizer.eos_token_id,
                    repetition_penalty=(self.repetition_penalty
                                        if repetition_penalty is None
                                        else float(repetition_penalty)),
                ):
                    # Декодируем ВСЮ накопленную последовательность, а не отдельный
                    # токен. BPE у GPT-2 работает на байтах, и одна русская буква
                    # занимает два-три токена: декодированный поодиночке токен —
                    # это обрывок UTF-8, который превращается в «<?>». Поэтому
                    # берём разницу между полным текстом до и после.
                    generated.append(int(token[0].item()))
                    full = self.tokenizer.decode(generated, skip_special_tokens=True)

                    # Ждём, пока последовательность байт станет полной.
                    #
                    # Без этой проверки терялась первая буква почти каждого
                    # русского слова, и ответ выглядел как «Кто ты» → «то ты».
                    # Механика такая: пока байты буквы пришли не все, decode
                    # ставит на её место U+FFFD. Прежний код принимал такой
                    # текст за готовый и запоминал его. На следующем токене
                    # буква достраивалась, но занимала В СТРОКЕ ТУ ЖЕ ОДНУ
                    # позицию, что и заменитель, — и срез full[len(text):]
                    # перешагивал её. Наружу уходил битый знак, правильный
                    # не выдавался никогда.
                    if full.endswith("�"):
                        continue

                    piece = full[len(text):]
                    if not piece:
                        continue
                    text = full

                    # Стоп-фраза может разорваться между токенами, поэтому ищем
                    # её в накопленном тексте, а не в очередном куске.
                    cut = min(
                        (text.find(stop) for stop in STOP_SEQUENCES if stop in text),
                        default=-1,
                    )
                    if cut >= 0:
                        tail = text[:cut]
                        if len(tail) > len(text) - len(piece):
                            yield tail[len(text) - len(piece):]
                        return

                    # Зацикливание. Проверяем ДО бюджета, а не после: именно в
                    # запасе оно и разворачивалось во всю ширь, потому что
                    # правило остановки при повторе «</div>» никогда не считает
                    # ответ дописанным — незакрытых тегов с каждым повтором
                    # только больше.
                    #
                    # Останавливаемся молча. Дописать «(модель зациклилась)»
                    # заманчиво, но это сообщение о внутренней кухне в том
                    # месте, где человек ждёт ответ; а повтор он и так видит.
                    if profiles.looping(text):
                        log("Повтор в ответе — останавливаю генерацию")
                        return

                    # Пока бюджет не исчерпан — отдаём как есть.
                    if len(generated) <= max_tokens:
                        yield piece
                        continue

                    # Бюджет кончился. Дальше идёт запас на то, чтобы ДОГОВОРИТЬ
                    # начатое: предложение, строку стиха или блок кода.
                    #
                    # Раньше генерация просто останавливалась на нужном токене, и
                    # ответ обрывался посреди слова: «оперативная память нужна для
                    # хране». Выглядит как поломка, хотя это ровно то, о чём
                    # попросили. Теперь лишние токены копятся в стороне: как
                    # только начатое дописано — отдаём разом и заканчиваем.
                    #
                    # Чем именно «дописано», решает profiles.can_finish, и для
                    # кода это не точка, а закрытая скобка, закрытый тег и
                    # закрытый блок ```. Ровно из-за того, что здесь ждали точку,
                    # программа и обрывалась на половине: точек в ней нет, запас
                    # уходил впустую, а хвост выбрасывался.
                    overflow += piece
                    if profiles.can_finish(text, finish):
                        yield overflow
                        return

            # Запас израсходован, а начатое так и не дописано.
            #
            # У разговора хвост выбрасывается: лучше ответ на фразу короче, чем
            # повисшее на середине слово. У кода и стихов наоборот — там
            # выброшенный хвост означает потерю половины ответа, которую человек
            # уже видел в чате по мере генерации. Отдаём как есть.
            if overflow and finish != "sentence":
                yield overflow
        finally:
            self.busy = False
            self.last_used = time.time()
            self.release()

    def complete(self, messages: list, **kwargs) -> str:
        return "".join(self.stream(messages, **kwargs)).strip()


# ------------------------------------------------------------- HTTP-интерфейс --

#: Длиннее этого системный промпт клиента заменяется на тот, с которым обучались.
#:
#: Порог подняли с 400 символов вместе с переходом на русский словарь: раньше
#: 400 символов стоили около 400 токенов из 1024, теперь — около 95 из 2048,
#: и жадничать больше незачем.
SYSTEM_LIMIT = 600


def normalize_system(messages: list) -> list:
    """
    Приводит системную часть к тому виду, на котором модель обучалась.

    У фронтенда (llm.js) свой системный промпт — он писался под движки с моделями
    на 7-8 млрд параметров и занимает под сотню строк. Для модели на 125M он плох
    не столько длиной, сколько тем, что не совпадает с форматом обучения: модель
    видела в этой позиции одну конкретную подсказку и на незнакомую реагирует
    хуже. Короткий промпт клиента уважаем как есть, слишком длинный — заменяем
    своим.
    """
    head = next((m for m in messages if isinstance(m, dict) and m.get("role") == "system"), None)
    if head is None:
        return [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    if len(str(head.get("content", ""))) <= SYSTEM_LIMIT:
        return messages

    rest = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
    return [{"role": "system", "content": SYSTEM_PROMPT}] + rest


def apply_system(messages: list, system: str) -> list:
    """
    Ставит в начало заданную системную подсказку, убирая все прочие.

    Отличие от normalize_system: та уважает короткий промпт клиента, а эта —
    нет, и так и задумано. Подсказку выбирает профиль по существу вопроса
    («сейчас пишем код», «сейчас сочиняем стихи»), и клиент про это ничего не
    знает: он отправил запрос до того, как кто-либо разобрал, о чём просят.
    """
    rest = [m for m in messages if not (isinstance(m, dict) and m.get("role") == "system")]
    return [{"role": "system", "content": system}] + rest


def build_app(engine: Engine):
    """Собирает приложение FastAPI с эндпоинтами в стиле OpenAI."""
    if FastAPI is None:
        raise SystemExit(
            "Не установлен fastapi — сервер модели поднять не выйдет.\n"
            "Из папки python/:  pip install -r requirements.txt"
        )

    app = FastAPI(title=f"{BRAND_AI} API", version=VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _chunk(delta: dict, finish: Optional[str], created: int, ident: str,
               model: str = MODEL_ID) -> str:
        payload = {
            "id": ident,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @app.post("/v1/route")
    async def route(request: Request):
        """Просьба → команды приложения (cloudhdr_qwen/router.py).

        {"text": "...", "history": [{role, content}, …]} → {"commands": [...],
        "ask": "...", "say": "..."}. У прежней модели (v7) такого умения нет —
        она отвечает пустым списком, и фраза идёт в обычный чат, как раньше."""
        try:
            body = await request.json()
        except Exception:                                           # noqa: BLE001
            body = {}
        text = str(body.get("text") or "").strip()
        route_fn = getattr(engine, "route", None)
        if not text or route_fn is None:
            return {"commands": [], "ask": "", "say": ""}
        history = body.get("history") if isinstance(body.get("history"), list) else None
        import asyncio
        return await asyncio.to_thread(route_fn, text, history)

    @app.post("/v1/act")
    async def act(request: Request):
        """Руки: следующее действие курсора (cloudhdr_qwen/hands.py).

        {"goal": "...", "scene": "экран текстом", "history": ["1. …", …]} →
        {"why", "action", "id", "text", "say"}. Без llama-движка — «fail», и
        руки разбирают поручение прежними правилами."""
        try:
            body = await request.json()
        except Exception:                                           # noqa: BLE001
            body = {}
        goal = str(body.get("goal") or "").strip()
        act_fn = getattr(engine, "act", None)
        if not goal or act_fn is None:
            return {"why": "", "action": "fail", "id": 0, "text": "", "say": ""}
        history = body.get("history") if isinstance(body.get("history"), list) else None
        import asyncio
        return await asyncio.to_thread(act_fn, goal, str(body.get("scene") or ""), history,
                                       bool(body.get("plan")), str(body.get("context") or ""))

    @app.post("/internal/release")
    def release_gpu():
        """Очередь видеопамяти: другая служба (голос) просит освободить карту.

        Во время ответа не уступаем — генерация важнее: голос подождёт или
        поработает на процессоре. Отвечает только этому компьютеру: сервер и
        так слушает 127.0.0.1."""
        if engine.busy:
            return {"ok": False, "busy": True}
        unload = getattr(engine, "unload", None)
        if unload is None:
            # прежняя модель: веса в ОЗУ вместо выгрузки целиком
            engine.last_used = 0
            engine.sleep_if_idle()
        else:
            with engine.placement:
                if not engine.busy:
                    unload()
        return {"ok": True}

    @app.get("/health")
    def health():
        return {
            "brand": f"{BRAND_AI} v{VERSION}",
            "model": MODEL_ID,
            "models": [MODEL_ID, MODEL_ID_ULTRA],
            "variant": engine.label,
            "device": engine.info.name,
            "precision": engine.precision,
            "busy": engine.busy,
            "wiki": engine.wiki,
            "source": (engine.last_source or {}).get("title"),
        }

    @app.get("/v1/models")
    def models():
        """
        Этот ответ ищет llm.js, обходя порты, — по нему модель и находится.

        Имён два, весов по-прежнему одни. Подробно, почему так, — в
        branding.MODEL_ID_ULTRA; коротко: разными у имён являются подсказка,
        параметры генерации и правило остановки, а не сеть.

        Порядок важен: клиент, который про выбор ничего не знает, берёт первое
        имя из списка. Первым стоит разговорный профиль — он дешевле и отвечает
        быстрее, и получить его по умолчанию правильнее.
        """
        created = int(time.time())
        return {
            "object": "list",
            "data": [
                {
                    "id": name,
                    "object": "model",
                    "created": created,
                    "owned_by": "cloud-hdr",
                    "title": profile["title"],
                    "about": profile["about"],
                    # размер и окно — от той основы, что сейчас отвечает:
                    # окно приложения показывает их в настройках как есть
                    "params": getattr(engine, "params_label", "760 млн"),
                    "context": int(engine.model.config.block_size),
                }
                for name, profile in profiles.PROFILES.items()
            ],
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        try:
            body: dict[str, Any] = await request.json()
        except Exception:
            return JSONResponse({"error": {"message": "Тело запроса не разобралось как JSON"}}, 400)

        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return JSONResponse({"error": {"message": "Поле messages пустое"}}, 400)

        # Что спросили — нужно ДО генерации: от рода вопроса зависят и подсказка,
        # и температура, и бюджет, и правило остановки.
        question = next(
            (str(m.get("content", "")) for m in reversed(messages)
             if isinstance(m, dict) and m.get("role") == "user"),
            "",
        )
        plan = profiles.plan(body.get("model"), question, body.get("max_tokens"))

        # Верхний предел на ответ — от размера окна, а не от круглого числа.
        # Прежняя тысяча была взята с потолка и резала ровно те ответы, ради
        # которых профиль Ultra и появился.
        ceiling = max(64, engine.model.config.block_size - plan["grace"] - 256)
        params = dict(
            temperature=float(plan["temperature"]),
            max_tokens=max(1, min(int(plan["max_tokens"]), ceiling)),
            top_p=float(plan["top_p"]),
            top_k=int(plan["top_k"]),
            finish=plan["finish"],
            grace=int(plan["grace"]),
            system=plan["system"],
            wiki=plan["wiki"],
            repetition_penalty=plan["repetition_penalty"],
        )
        model_id = plan["profile"]
        log(f"{plan['label']} · {plan['kind']} · бюджет {params['max_tokens']}"
            f"+{params['grace']} · t={params['temperature']}")

        ident = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())

        # Просьба почти дословно совпадает с задачей из банка — отдаём
        # проверенный код вместо сгенерированного (см. codebank.recall).
        # Только для последней реплики: «а теперь сделай её рекурсивной» —
        # уже разговор о коде, и там банку делать нечего.
        # Род берём по самому вопросу, а не по профилю: у 3.2 просьба о коде
        # обслуживается разговорным профилем, но проверенный код нужен и там.
        banked = codebank.recall(question) if profiles.kind_of(question) == profiles.KIND_CODE else None
        if banked:
            log(f"{plan['label']} · код из банка проверенных решений")
            if body.get("stream"):
                def banked_events():
                    yield _chunk({"role": "assistant", "content": ""}, None, created, ident, model_id)
                    # кусками по строке — окно показывает ответ так же, как живой
                    for line in banked.splitlines(keepends=True):
                        yield _chunk({"content": line}, None, created, ident, model_id)
                    yield _chunk({}, "stop", created, ident, model_id)
                    yield "data: [DONE]\n\n"

                return StreamingResponse(banked_events(), media_type="text/event-stream",
                                         headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            return {
                "id": ident, "object": "chat.completion", "created": created, "model": model_id,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": banked}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

        if body.get("stream"):
            def events():
                yield _chunk({"role": "assistant", "content": ""}, None, created, ident, model_id)
                try:
                    for piece in engine.stream(messages, **params):
                        yield _chunk({"content": piece}, None, created, ident, model_id)
                except Exception as error:                      # noqa: BLE001
                    log(f"Ошибка генерации: {error}")
                    yield _chunk({"content": f"\n[ошибка генерации: {error}]"}, "stop", created, ident, model_id)
                    yield "data: [DONE]\n\n"
                    return
                yield _chunk({}, "stop", created, ident, model_id)
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        text = engine.complete(messages, **params)
        prompt_tokens = len(engine.tokenizer(
            format_messages(apply_system(messages, plan["system"]), add_generation_prompt=True),
            add_special_tokens=False,
        )["input_ids"])
        completion_tokens = len(engine.tokenizer(text, add_special_tokens=False)["input_ids"])

        return {
            "id": ident,
            "object": "chat.completion",
            "created": created,
            "model": model_id,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }

    return app


def run(
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    checkpoint: Optional[str] = None,
    model_name: str = DEFAULT_MODEL,
    prefer_device: str = "auto",
    precision: str = "auto",
    wiki: bool = True,
) -> None:
    """Точка входа команды `serve`."""
    import uvicorn

    banner("Запуск модуля генерации...")
    engine = None
    # Какая основа отвечает — решает checkpoints/active.json: {"engine": "qwen"}
    # или {"engine": "v7"}. Откат на прежнюю модель — одна строчка в этом файле,
    # без переустановки. Наружу модель в любом случае называется Cloud HDR.
    active = Path(__file__).resolve().parent.parent / "checkpoints" / "active.json"
    choice = "v7"
    try:
        choice = json.loads(active.read_text(encoding="utf-8")).get("engine", "v7")
    except (OSError, ValueError):
        pass
    # --checkpoint у команды serve есть всегда (по умолчанию v7/best.pt), поэтому
    # решает только active.json: он и есть явный выбор хозяина.
    if choice == "qwen":
        # Быстрый путь — llama.cpp (GGUF); запасной — transformers в 4 битах.
        for make in ("llama", "transformers"):
            try:
                if make == "llama":
                    from cloudhdr_qwen.llama_engine import LlamaEngine
                    engine = LlamaEngine()
                else:
                    from cloudhdr_qwen.engine import QwenEngine
                    engine = QwenEngine()
                log(f"Модель: {engine.label} ({make}, грузится на первый вопрос)")
                break
            except Exception as error:                              # noqa: BLE001
                log(f"Движок {make} не поднялся ({error})")
                engine = None
        if engine is None:
            log("Новая основа недоступна — отвечает прежняя модель")
    if engine is None:
        engine = Engine.load(checkpoint, model_name, prefer_device, precision)
    engine.wiki = wiki
    log("Справка из Википедии: " + ("подключена — факты берутся из статьи, "
                                    "а не из памяти модели"
                                    if wiki else "выключена (--no-wiki)"))
    app = build_app(engine)

    # Сторож простоя: раз в полминуты смотрит, не пора ли освободить видеокарту.
    def idle_watch():
        while True:
            time.sleep(30)
            try:
                engine.sleep_if_idle()
            except Exception as error:                          # noqa: BLE001
                log(f"Выгрузка в простое не удалась: {error}")
    threading.Thread(target=idle_watch, daemon=True).start()
    if engine.info.is_cuda:
        log(f"Выгрузка в простое: через {engine.IDLE_SECONDS // 60} мин без вопросов видеокарта освобождается")

    log(f"Профили: {MODEL_LABEL} (разговор, Википедия) · "
        f"{MODEL_LABEL_ULTRA} (плюс код, стихи и длинные тексты)")
    log(f"Слушаю http://{host}:{port}  (/v1/models, /v1/chat/completions)")
    if port in (11434, 1234, 8080):
        log("Порт из списка автопоиска llm.js — интерфейс Cloud HDR найдёт модель сам")
    else:
        log(f"Порт нестандартный: укажите в настройках Cloud HDR адрес {host}:{port}")

    uvicorn.run(app, host=host, port=port, log_level="warning")
