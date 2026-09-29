"""
Cloud HDR AI — подготовка обучающих данных.

Поддерживаются три источника:

  • .txt          — сплошной текст, режется на окна длиной block_size;
  • .jsonl        — по объекту на строку: {"text": "..."} либо
                    {"messages": [{"role": "user", "content": "..."}, ...]};
  • brain.js      — размеченный корпус прежнего классификатора (около двух тысяч
                    фраз): извлекается регулярным выражением и разворачивается в
                    диалоги. Так языковая модель с самого начала знает, о чём
                    вообще разговаривает Cloud HDR.

Шаблон диалога намеренно обычный текст, без новых служебных токенов. Причина
практическая: добавление <|user|> и подобных потребовало бы расширить словарь и
матрицу эмбеддингов, а это ломает совместимость с предобученными весами и с
чекпоинтами. Обычные слова с двоеточием токенизируются штатным BPE и работают
не хуже.

Диалоги обрабатываются МНОГОХОДОВЫМИ. Это главное отличие от первой версии
модуля, и оно прямо отвечает за то, умеет ли модель поддержать разговор. Раньше
из беседы брался один последний ответ ассистента, а всё предыдущее сваливалось в
подсказку — то есть на обучении модель ни разу не видела, как разговор
продолжается после её собственной реплики. Теперь в функцию потерь входит каждый
ответ ассистента, реплики пользователя маскируются, а разговор длиннее окна
режется по границам реплик, а не обрывается на первом же ответе.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import torch
from torch.utils.data import Dataset

from .branding import BRAND, log

# ------------------------------------------------------------- шаблон диалога --

SYSTEM_PROMPT = (
    f"Ты — {BRAND} AI, умный и живой собеседник, который работает прямо на компьютере "
    "пользователя, без интернета и облачных сервисов. Команды к Windows выполняет "
    "другая часть системы; твоя работа — разговор, объяснения и ответы на вопросы. "
    "Говори по-русски естественно и тепло, как внимательный и начитанный друг: с интересом "
    "к собеседнику, ясной мыслью и уместной лёгкой шуткой. Не отделывайся одним словом — "
    "отвечай полными фразами, а сложное объясняй связно и понятно. "
    "Не выдумывай фактов о компьютере пользователя."
)

ROLE_LABELS = {"system": "Система", "user": "Пользователь", "assistant": BRAND}

#: Чем заканчивается реплика модели — по ней сервер обрывает генерацию.
STOP_SEQUENCES = ("\nПользователь:", "\nСистема:", f"\n{BRAND}:")

#: Как чужие наборы данных называют те же три роли.
#:
#: Единого стандарта нет: OpenAI пишет assistant, Saiga — bot, ShareGPT — gpt и
#: human, часть наборов — bot/human вперемешку. Роль, которую не удалось опознать,
#: считается пользовательской: это безопаснее, потому что реплика пользователя
#: маскируется и в худшем случае модель просто не поучится на ней, тогда как
#: ошибка в другую сторону научила бы её отвечать чужим текстом.
ROLE_ALIASES = {
    "system": "system", "система": "system", "instruction": "system",
    "user": "user", "human": "user", "пользователь": "user", "prompter": "user",
    "assistant": "assistant", "bot": "assistant", "gpt": "assistant",
    "ai": "assistant", "chatbot": "assistant", "model": "assistant",
}


def normalize_dialogue(messages: list) -> list:
    """
    Приводит чужой диалог к трём ролям Cloud HDR и чинит частые дефекты разметки.

    Что делается и зачем:
      • роли переводятся по ROLE_ALIASES;
      • пустые реплики выбрасываются — на них нечему учиться;
      • подряд идущие реплики одной роли склеиваются: без этого в тексте
        появлялось бы «Пользователь: ... \\n\\n Пользователь: ...», формат, которого
        при разговоре не бывает, и модель училась бы отвечать сама себе;
      • системные сообщения не с начала диалога переносятся в начало, потому что
        шаблон Cloud HDR держит системную подсказку только в первой позиции.
    """
    head: list = []
    body: list = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = ROLE_ALIASES.get(str(message.get("role", "user")).strip().lower(), "user")
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        if role == "system":
            head.append(content)
            continue
        if body and body[-1]["role"] == role:
            body[-1]["content"] += "\n\n" + content
        else:
            body.append({"role": role, "content": content})

    result = [{"role": "system", "content": "\n\n".join(head)}] if head else []
    return result + body


def format_messages(messages: list, add_generation_prompt: bool = True) -> str:
    """Превращает список ролей в плоский текст по шаблону Cloud HDR."""
    parts = []
    for message in messages:
        role = str(message.get("role", "user"))
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        parts.append(f"{ROLE_LABELS.get(role, 'Пользователь')}: {content}")

    text = "\n\n".join(parts)
    if add_generation_prompt:
        text += f"\n\n{BRAND}: "
    return text


# ---------------------------------------------------- корпус прежней нейросети --

#: Пары ['фраза', 'intent'] из public/js/brain.js.
_CORPUS_ENTRY = re.compile(r"\[\s*'((?:[^'\\]|\\.)*)'\s*,\s*'([a-z_]+)'\s*\]")

#: Как отвечать на каждое намерение — чтобы фразы стали диалогами, а не парами.
_INTENT_REPLY = {
    "open_app": "Открываю приложение.",
    "open_folder": "Открываю папку.",
    "open_url": "Открываю ссылку в браузере.",
    "web_search": "Ищу в интернете.",
    "close_app": "Закрываю приложение.",
    "volume_up": "Прибавляю громкость.",
    "volume_down": "Убавляю громкость.",
    "volume_mute": "Выключаю звук.",
    "screenshot": "Делаю снимок экрана.",
    "lock": "Блокирую экран.",
    "window_pin": "Закрепляю окно поверх остальных.",
    "window_unpin": "Снимаю закрепление окна.",
    "scan_files": "Сканирую диски и ищу файлы.",
    "analyze_chats": "Разбираю переписку.",
    "math": "Считаю выражение.",
    "datetime": "Смотрю дату и время.",
    "convert": "Перевожу величины.",
    "remember": "Запомнил.",
    "recall": "Вот что я запомнил раньше.",
    "timer": "Ставлю таймер.",
    "settings": "Показываю настройки и параметры модели.",
    "greeting": f"Здравствуйте! Я {BRAND} AI, помощник на вашем компьютере. Чем помочь?",
    "thanks": "Пожалуйста, обращайтесь.",
    "help": (
        "Я умею открывать программы и папки, искать файлы и игры на дисках, "
        "делать снимки экрана, управлять громкостью, считать и запоминать заметки."
    ),
    "identity": (
        f"Я {BRAND} AI — языковая модель, которая работает локально на этом компьютере. "
        "Команды к Windows выполняет отдельный классификатор намерений."
    ),
    "smalltalk": "Понимаю. Расскажите, что нужно сделать.",
    "denied": "Это я делать не буду.",
}


def harvest_brain_corpus(brain_js: Path) -> list:
    """
    Достаёт корпус из brain.js и разворачивает его в диалоги.

    Разбор регулярным выражением, а не парсером JavaScript, — сознательно:
    нужен ровно один литерал фиксированной формы, и тащить ради него зависимость
    было бы несоразмерно.
    """
    if not brain_js.exists():
        log(f"Файл {brain_js.name} не найден — корпус классификатора пропущен")
        return []

    source = brain_js.read_text(encoding="utf-8")
    pairs = _CORPUS_ENTRY.findall(source)
    if not pairs:
        log(f"В {brain_js.name} не нашлось пар вида ['фраза', 'intent']")
        return []

    dialogues = []
    seen = set()
    for phrase, intent in pairs:
        phrase = phrase.replace("\\'", "'").strip()
        if not phrase or (phrase, intent) in seen:
            continue
        seen.add((phrase, intent))
        dialogues.append([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": phrase},
            {"role": "assistant", "content": _INTENT_REPLY.get(intent, "Понял.")},
        ])

    log(f"Из корпуса классификатора получено диалогов: {len(dialogues)}")
    return dialogues


# ------------------------------------------------------------- чтение файлов ---

def _iter_files(source: Path) -> Iterator[Path]:
    if source.is_file():
        yield source
        return
    for pattern in ("*.txt", "*.jsonl", "*.json"):
        yield from sorted(source.rglob(pattern))


def load_documents(source: Optional[str], brain_js: Optional[Path] = None) -> list:
    """
    Собирает список документов. Документ — либо строка текста, либо диалог
    (список сообщений). Ниже они приводятся к единому виду.
    """
    documents: list = []

    if source:
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"Источник данных не найден: {path}")

        for file in _iter_files(path):
            if file.suffix == ".txt":
                text = file.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    documents.append(text)
            else:
                # split("\n"), а не splitlines(): последний рвёт строку ещё и на
                # U+2028, U+2029, \v, \f и \x85. Все они законно живут внутри
                # JSON-строки, и на настоящем корпусе это не теория — в текстах
                # диалогов нашлось десять U+2028, и каждый разрезал свою запись
                # пополам, превращая её в две неразбираемые. Записи молча
                # пропускались: данные терялись, а причина выглядела как «строка
                # не разобралась как JSON».
                for line_no, line in enumerate(
                    file.read_text(encoding="utf-8", errors="replace").split("\n"), 1
                ):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record: Any = json.loads(line)
                    except json.JSONDecodeError:
                        log(f"{file.name}:{line_no} — строка не разобралась как JSON, пропускаю")
                        continue
                    if isinstance(record, dict) and isinstance(record.get("messages"), list):
                        documents.append(record["messages"])
                    elif isinstance(record, dict) and record.get("text"):
                        documents.append(str(record["text"]))
        log(f"Прочитано документов из «{source}»: {len(documents)}")

    if brain_js is not None:
        documents.extend(harvest_brain_corpus(brain_js))

    if not documents:
        raise ValueError(
            "Обучающих данных нет. Укажите --data с файлом .txt/.jsonl "
            "или разрешите сбор корпуса из brain.js (--from-brain)."
        )
    return documents


def to_text(document: Any) -> str:
    """Диалог → плоский текст; текст остаётся собой."""
    if isinstance(document, list):
        return format_messages(document, add_generation_prompt=False)
    return str(document)


# ------------------------------------------------------------------ датасеты ---

class PackedDataset(Dataset):
    """
    Плотная упаковка: все документы склеиваются в один поток токенов,
    разделённые <|endoftext|>, и режутся на окна длиной block_size.

    Так в батче не остаётся ни одного padding-токена — на коротких фразах, как
    в корпусе Cloud HDR, паддинг съедал бы больше половины вычислений.
    """

    def __init__(self, token_stream: torch.Tensor, block_size: int):
        self.tokens = token_stream
        self.block_size = block_size
        # −1, потому что цель сдвинута на один токен вперёд
        self.length = max(0, (len(token_stream) - 1) // block_size)

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, index: int):
        start = index * self.block_size
        chunk = self.tokens[start: start + self.block_size + 1].long()
        return chunk[:-1], chunk[1:]


class PackedChatDataset(Dataset):
    """
    Диалоги, упакованные в плотный поток вместе со своими масками.

    Маскирование. Токены системной подсказки и реплик пользователя получают цель
    −100 и не попадают в функцию потерь: модель учится ОТВЕЧАТЬ, а не
    воспроизводить вопросы, которые ей и так дают на вход.

    Упаковка. Диалоги разной длины, и дополнение каждого заполнителем до полного
    окна — прямая потеря вычислений: при окне 1024 и средней длине разговора
    около 350 токенов две трети каждого батча были бы паддингом, на который
    честно тратятся матмулы. Здесь заполнителя нет ни одного токена, поэтому за
    те же часы на видеокарте модель видит втрое больше настоящего текста.

    Плата за это — граница окна может разрезать разговор посередине. Для
    языковой модели это норма (так устроено обучение всех GPT), а разделитель
    <|endoftext|> в конце каждого ответа не даёт склеить два разных диалога в
    один.
    """

    def __init__(self, examples: list, block_size: int):
        tokens: list = []
        labels: list = []
        for example_tokens, example_labels in examples:
            tokens.extend(example_tokens)
            labels.extend(example_labels)

        # int32 вместо int64: поток целиком лежит в оперативной памяти, и на
        # больших корпусах разница в два раза — это гигабайты. В long значения
        # переводятся поштучно при выдаче окна, это ничего не стоит.
        self.tokens = torch.tensor(tokens, dtype=torch.int32)
        self.labels = torch.tensor(labels, dtype=torch.int32)
        self.block_size = block_size
        self.length = max(0, (len(self.tokens) - 1) // block_size)

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, index: int):
        start = index * self.block_size
        end = start + self.block_size
        return self.tokens[start:end].long(), self.labels[start + 1: end + 1].long()


# ------------------------------------------------- разбор диалога на примеры --

def dialogue_turns(messages: list) -> list:
    """
    Диалог → пары «текст подсказки, текст ответа», по одной на реплику модели.

    Склеенные подряд, они дают ровно тот же текст, что format_messages, — это
    существенно: обучающая последовательность обязана совпадать с той, которую
    соберёт сервер при разговоре, вплоть до переводов строк.
    """
    turns: list = []
    pending = ""
    written = False

    for message in messages:
        role = message["role"]
        content = message["content"]
        separator = "\n\n" if written else ""

        if role == "assistant":
            pending += f"{separator}{ROLE_LABELS['assistant']}: "
            turns.append((pending, content))
            pending = ""
        else:
            pending += f"{separator}{ROLE_LABELS.get(role, 'Пользователь')}: {content}"

        written = True

    # Незакрытая реплика пользователя в конце отбрасывается: учиться не на чем.
    return turns


def build_chat_examples(messages: list, tokenizer, block_size: int, eos_id: int) -> list:
    """
    Диалог → список примеров (токены, цели), готовых к упаковке.

    Здесь два отличия от прежнего разбора, и оба меняют то, чему модель учится.

    ВСЕ ОТВЕТЫ, А НЕ ПОСЛЕДНИЙ. Раньше из диалога брался один финальный ответ
    ассистента, а всё, что было до него, уходило в подсказку. На таких примерах
    модель учится отвечать на первую реплику и никогда не видит, как разговор
    ПРОДОЛЖАЕТСЯ: что делать со второй репликой пользователя, как опираться на
    сказанное раньше, как не повторяться. Отсюда и берётся «не держит диалог».
    Теперь в функцию потерь входит каждый ответ ассистента.

    РЕЗ ПО ГРАНИЦАМ РЕПЛИК. Разговор длиннее окна раньше просто обрезался, и
    всё, что не поместилось, пропадало. Теперь он разбивается на несколько
    примеров по границам реплик, и каждый следующий кусок получает системную
    подсказку заново — иначе продолжение разговора начиналось бы без неё,
    в формате, которого при работе не бывает.
    """
    turns = dialogue_turns(messages)
    if not turns:
        return []

    def encode(text: str) -> list:
        return tokenizer(text, add_special_tokens=False)["input_ids"]

    system = next((m for m in messages if m["role"] == "system"), None)
    system_ids = encode(f"{ROLE_LABELS['system']}: {system['content']}") if system else []

    limit = block_size + 1
    examples: list = []
    tokens: list = []
    labels: list = []

    for index, (prompt, answer) in enumerate(turns):
        # Первая пара уже несёт системную подсказку внутри текста подсказки;
        # у продолжающих окон её нужно добавить самим.
        prompt_ids = ([] if index == 0 or tokens else system_ids) + encode(prompt)
        answer_ids = encode(answer) + [eos_id]

        if tokens and len(tokens) + len(prompt_ids) + len(answer_ids) > limit:
            if any(label != -100 for label in labels):
                examples.append((tokens, labels))
            tokens, labels = [], []
            prompt_ids = ([] if index == 0 else system_ids) + encode(prompt)

        # Реплика, не влезающая в пустое окно: режем ОТВЕТ с конца, подсказку
        # оставляем целой. Обрезанная подсказка сделала бы пример вредным —
        # модель училась бы отвечать на половину вопроса.
        room = limit - len(tokens) - len(prompt_ids)
        if room < 1:
            continue
        answer_ids = answer_ids[:room]

        tokens.extend(prompt_ids + answer_ids)
        labels.extend([-100] * len(prompt_ids) + answer_ids)

    if any(label != -100 for label in labels):
        examples.append((tokens, labels))
    return examples


def build_datasets(
    documents: list,
    tokenizer,
    block_size: int,
    val_split: float = 0.05,
    mode: str = "auto",
    seed: int = 0,
):
    """
    Собирает обучающую и отложенную выборки.

    :param mode: pack | chat | auto (chat, если больше половины документов —
                 диалоги: маскирование вопроса даёт заметно лучший результат
                 именно на диалоговых данных)
    """
    dialogues = [d for d in documents if isinstance(d, list)]
    if mode == "auto":
        mode = "chat" if len(dialogues) > len(documents) / 2 else "pack"

    eos_id = tokenizer.eos_token_id
    random.Random(seed).shuffle(documents)

    if mode == "chat":
        def collect(subset: list) -> list:
            examples: list = []
            for document in subset:
                messages = normalize_dialogue(document if isinstance(document, list) else [
                    {"role": "user", "content": str(document)}
                ])
                examples.extend(build_chat_examples(messages, tokenizer, block_size, eos_id))
            return examples

        # Делим по ДИАЛОГАМ, а не по готовым примерам. Длинный разговор даёт
        # несколько примеров, и раздели мы их — куски одной беседы попали бы и в
        # обучение, и в проверку. Проверка после такого показывает не способность
        # обобщать, а память, и по ней нельзя судить, когда остановиться.
        cut = max(1, int(len(documents) * (1 - val_split))) if val_split else len(documents)
        train_examples = collect(documents[:cut])
        val_examples = collect(documents[cut:])

        if not train_examples:
            raise ValueError("Ни один диалог не содержит ответа ассистента")

        train = PackedChatDataset(train_examples, block_size)
        val = PackedChatDataset(val_examples, block_size) if val_examples else None
        if val is not None and len(val) == 0:
            val = None

        answer_tokens = sum(
            int((labels != -100).sum()) for labels in [train.labels]
        )
        total_tokens = len(train.tokens)
        log(f"Режим «chat»: диалогов {len(documents)} → примеров {len(train_examples)} "
            f"(проверка {len(val_examples)})")
        spaced = lambda number: f"{number:,}".replace(",", " ")
        log(f"Токенов в обучении: {spaced(total_tokens)} · из них ответы модели "
            f"{spaced(answer_tokens)} ({100 * answer_tokens / max(total_tokens, 1):.0f}%) — "
            f"остальное подсказки, они замаскированы")
        log(f"Окон по {block_size}: обучение {len(train)}, проверка {len(val) if val else 0}")
        return train, val

    # --- режим plain LM -----------------------------------------------------
    stream: list = []
    for document in documents:
        ids = tokenizer(to_text(document), add_special_tokens=False)["input_ids"]
        stream.extend(ids)
        stream.append(eos_id)

    tensor = torch.tensor(stream, dtype=torch.long)
    cut = int(len(tensor) * (1 - val_split)) if val_split else len(tensor)
    train = PackedDataset(tensor[:cut], block_size)
    val = PackedDataset(tensor[cut:], block_size) if len(tensor) - cut > block_size else None

    log(f"Режим «pack»: токенов {len(tensor):,} → окон по {block_size}: "
        f"обучение {len(train)}, проверка {len(val) if val else 0}".replace(",", " "))
    if len(train) == 0:
        raise ValueError(
            f"Данных меньше одного окна в {block_size} токенов — "
            "добавьте текста или уменьшите --block-size"
        )
    return train, val


#: Куда кладётся собственная копия токенизатора рядом с чекпоинтами.
TOKENIZER_DIRNAME = "tokenizer"


def find_local_tokenizer(*candidates) -> Optional[Path]:
    """
    Ищет сохранённую копию токенизатора среди переданных путей.

    Нужно ради работы без интернета: по умолчанию transformers лезет на
    huggingface.co даже за тем, что уже лежит в кеше, — проверить обновления.
    Если файлы токенизатора есть рядом с чекпоинтом, берём их и в сеть не ходим
    вовсе. Это надёжнее переменной HF_HUB_OFFLINE, потому что не зависит от того,
    что кто-то не забыл её выставить.
    """
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_dir() and ((path / "tokenizer.json").exists() or (path / "vocab.json").exists()):
            return path
    return None


def load_tokenizer(name: Optional[str] = None, local_dir=None):
    """
    BPE-токенизатор болванки. Паддинг-токеном служит конец текста.

    :param name: имя болванки; по умолчанию — русская (см. config.DEFAULT_MODEL)
    :param local_dir: папка с сохранённой копией; если файлы там есть, они и
                      используются, и обращения к сети не происходит
    """
    from transformers import AutoTokenizer

    from .config import DEFAULT_MODEL, resolve_model_name

    local = find_local_tokenizer(local_dir)
    if local is not None:
        log(f"Токенизатор взят локально: {local} (сеть не нужна)")
        tokenizer = AutoTokenizer.from_pretrained(str(local), local_files_only=True)
    else:
        # Псевдоним разворачивается ЗДЕСЬ, а не у вызывающего. Модель это делала
        # у себя (CloudHDRGPT.from_pretrained), а токенизатор получал имя как
        # есть — и «rugpt3-medium» уходило на Hugging Face дословно, где такого
        # репозитория нет. Пока в командах писали полное имя, расхождение не
        # проявлялось; первый же псевдоним уронил обучение на загрузке словаря.
        tokenizer = AutoTokenizer.from_pretrained(resolve_model_name(name or DEFAULT_MODEL))

    # Конец текста обязателен: им отделяются документы при упаковке и им же
    # модель заканчивает свою реплику. Если в словаре его нет под привычным
    # именем, берём любой служебный токен — лишь бы он был единственным и
    # никогда не встречался в обычном тексте.
    if tokenizer.eos_token is None:
        tokenizer.eos_token = tokenizer.pad_token or tokenizer.unk_token or "<|endoftext|>"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    return tokenizer


def save_tokenizer(tokenizer, out_dir) -> Path:
    """Кладёт копию токенизатора рядом с чекпоинтами — для работы офлайн."""
    path = Path(out_dir) / TOKENIZER_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(str(path))
    log(f"Токенизатор сохранён локально: {path}")
    return path
