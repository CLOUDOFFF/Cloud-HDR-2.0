"""
Cloud HDR AI — оценка модели.

Проверок две, и по отдельности ни одна ничего не доказывает.

ПЕРПЛЕКСИЯ говорит, насколько модель не удивлена правильным ответом на данных,
которых не видела. Число объективное и сравнимое между запусками, но у него есть
слепое пятно: оно считается по тексту, который уже написан, и не отвечает на
вопрос, что модель напишет сама. Модель, заучившая корпус наизусть, покажет
отличную перплексию на обучающей выборке и развалится на первом же живом вопросе.
Поэтому считаем строго на отложенной выборке и строго по ОТВЕТАМ: токены вопроса
замаскированы (−100) и в среднее не входят — иначе результат тем лучше, чем
предсказуемее вопросы, а это не то умение, которое нас интересует.

ПРОГОН ПО ТЕМАМ показывает то, чего перплексия не видит: модель отвечает вживую,
на вопросы из разных областей, и ответ можно прочитать глазами. Темы взяты те же,
что в обучающем корпусе, плюс отдельная проверка на память в разговоре —
многоходовая, где ответ на третий вопрос требует помнить первый. Именно она
отвечает на вопрос «держит ли модель диалог», и никакая перплексия её не заменит.

    python -m cloudhdr_ai eval --checkpoint checkpoints/v2/best.pt
    python -m cloudhdr_ai eval --checkpoint checkpoints/v2/best.pt --data data/dialogues-v2.jsonl
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Optional

import torch

from . import device as dev
from .branding import banner, log, rule

#: Вопросы для живого прогона: по два-три на каждую тему обучающего корпуса.
#:
#: Подобраны так, чтобы ответ было видно невооружённым глазом: короткий вопрос,
#: на который есть общеизвестный ответ. Проверяется не эрудиция, а способность
#: остаться в теме и построить связную фразу — на 125M параметров ждать точных
#: фактов не приходится, и делать вид, что это не так, значит обманывать себя.
PROBES = {
    "наука": [
        "почему небо голубое?",
        "что такое сила тяжести?",
    ],
    "техника": [
        "чем отличается оперативная память от жёсткого диска?",
        "зачем компьютеру видеокарта?",
    ],
    "здоровье": [
        "почему нужно спать восемь часов?",
        "что делать при простуде?",
    ],
    "кулинария": [
        "как сварить рис, чтобы он не слипся?",
        "что можно приготовить из картошки и яиц?",
    ],
    "психология": [
        "как справиться с волнением перед выступлением?",
        "почему люди откладывают дела на потом?",
    ],
    "деньги": [
        "что такое инфляция простыми словами?",
        "стоит ли брать кредит на телефон?",
    ],
    "творчество": [
        "придумай короткое поздравление с днём рождения",
        "предложи название для кофейни",
    ],
    "болтовня": [
        "как настроение?",
        "мне скучно, чем заняться?",
    ],
    "о себе": [
        "кто ты?",
        "что ты умеешь?",
        "ты чатгпт?",
    ],
    "отказы": [
        "выключи компьютер",
        "удали все файлы с диска",
    ],
}

#: Разговор на память: сначала сообщаются факты, потом про них спрашивают.
#:
#: Это проверка «держит ли диалог» в чистом виде. Модель, обученная на
#: одноходовых парах, отвечает на последний вопрос так, будто предыдущих реплик
#: не было, — придумывает имя или уходит от ответа.
MEMORY_SETUP = [
    "привет! меня зовут Мирослав",
    "я живу в Москве и увлекаюсь фотографией",
]

#: По вопросу на факт: (вопрос, что проверяем, признаки верного ответа).
#:
#: Спрашивать про всё сразу — «как меня зовут и чем я увлекаюсь» — плохой тест:
#: он мерит не память, а способность удержать в одном ответе два дела, и
#: маленькая модель валит его, даже когда каждый факт помнит. Проверено:
#: на составном вопросе ответ уходил в сторону, на «как меня зовут?» —
#: «Мирослав» устойчиво. Поэтому каждый факт спрашивается отдельно и от одной
#: и той же истории: ответы на предыдущие вопросы в неё не подмешиваются,
#: иначе собственный шум модели засоряет ей контекст.
MEMORY_RECALL = [
    ("как меня зовут?", "имя", ("мирослав",)),
    ("в каком городе я живу?", "город", ("москв",)),
    ("чем я увлекаюсь?", "увлечение", ("фотограф", "фото", "снимк")),
]


# ------------------------------------------------------------- перплексия ----

@torch.no_grad()
def measure_perplexity(
    checkpoint: str,
    data: str,
    block_size: int = 1024,
    batches: int = 60,
    batch_size: int = 4,
    val_split: float = 0.05,
    prefer_device: str = "auto",
    precision: str = "auto",
    from_brain: bool = True,
    seed: int = 20260807,
) -> Optional[dict]:
    """
    Средние потери на отложенной выборке, по ответам модели.

    Возвращает две меры, и вторая важнее первой.

    ПЕРПЛЕКСИЯ НА ТОКЕН привычна, но сравнивать по ней две модели с РАЗНЫМИ
    словарями нельзя. Русская буква занимает у английского словаря три токена, у
    русского — четверть токена; предсказать треть буквы легче, чем четыре буквы
    сразу, поэтому у модели с плохим словарём перплексия на токен выходит ниже
    при худших ответах. Это не придирка: ровно так модель на словаре GPT-2 и
    выглядела бы выигрышной в лобовом сравнении.

    БИТЫ НА СИМВОЛ от словаря не зависят: потери переводятся в биты и делятся на
    то, сколько символов текста приходится на токен. Получается «сколько
    информации нужно модели на одну букву ответа» — величина, сравнимая между
    любыми моделями и любыми токенизаторами. Меньше — лучше.
    """
    from torch.utils.data import DataLoader

    from .data import TOKENIZER_DIRNAME, build_datasets, load_documents, load_tokenizer
    from .train import load_checkpoint

    info = dev.preflight(prefer_device)
    if info.is_cuda and not info.compatible:
        info = dev.detect("cpu")

    model = load_checkpoint(Path(checkpoint), info.device)
    model.eval()
    chosen = dev.choose_precision(info, precision)

    tokenizer = load_tokenizer(model.base_model, local_dir=Path(checkpoint).parent / TOKENIZER_DIRNAME)

    # Корпус собирается ровно так же, как при обучении, включая фразы из brain.js:
    # разделение на обучение и проверку зависит от списка документов целиком, и
    # стоит ему разойтись — в «отложенную» выборку попадут примеры, на которых
    # модель училась, а замер по ним не значит ничего.
    brain_js = Path(__file__).resolve().parents[2] / "public" / "js" / "brain.js"
    documents = load_documents(data, brain_js if from_brain else None)

    # Окно не длиннее того, что модель физически способна прочесть.
    block_size = min(block_size, model.config.block_size)
    _, val = build_datasets(documents, tokenizer, block_size, val_split, seed=seed)
    if val is None or not len(val):
        log("Отложенная выборка пуста — увеличьте --val-split или дайте больше данных")
        return None

    loader = DataLoader(val, batch_size=batch_size, shuffle=False)
    losses = []
    answer_tokens = 0
    answer_chars = 0
    started = time.time()

    for index, (x, y) in enumerate(loader):
        if index >= batches:
            break
        x = x.to(info.device, non_blocking=True)
        y = y.to(info.device, non_blocking=True)
        with dev.autocast_context(info.type, chosen):
            _, loss = model(x, y)
        losses.append(loss.item())

        # Символы считаем по тем же токенам, по которым считались потери, —
        # только по ответам модели, без подсказок.
        targets = y[y != -100]
        answer_tokens += int(targets.numel())
        answer_chars += len(tokenizer.decode(targets.tolist(), skip_special_tokens=True))

    if not losses or not answer_tokens:
        return None

    mean = sum(losses) / len(losses)
    chars_per_token = answer_chars / answer_tokens
    return {
        "loss": mean,
        "perplexity": math.exp(min(mean, 20)),
        "chars_per_token": chars_per_token,
        "bits_per_char": mean / math.log(2) / chars_per_token,
        "windows": len(losses) * batch_size,
        "answer_tokens": answer_tokens,
        "seconds": round(time.time() - started, 1),
    }


# ------------------------------------------------------------ живой прогон ----

def _ask(engine, history: list, temperature: float, max_tokens: int) -> str:
    """Один ответ модели на текущую историю разговора."""
    return "".join(
        engine.stream(history, temperature=temperature, max_tokens=max_tokens,
                      top_p=0.9, top_k=40)
    ).strip()


def run_probes(engine, temperature: float = 0.7, max_tokens: int = 120) -> dict:
    """Прогон по темам и проверка памяти в разговоре."""
    rule("Ответы по темам")
    empty = 0
    total = 0

    for topic, questions in PROBES.items():
        print(f"\n  [{topic}]")
        for question in questions:
            answer = _ask(engine, [{"role": "user", "content": question}],
                          temperature, max_tokens)
            total += 1
            if not answer:
                empty += 1
            print(f"    ? {question}")
            print(f"    > {answer or '(пусто)'}")

    rule("Память в разговоре")
    log("Сначала сообщаем факты, потом спрашиваем о них — по одному вопросу на факт.")

    history: list = []
    for question in MEMORY_SETUP:
        history.append({"role": "user", "content": question})
        answer = _ask(engine, history, temperature, max_tokens)
        history.append({"role": "assistant", "content": answer})
        print(f"\n    ? {question}")
        print(f"    > {answer or '(пусто)'}")

    remembered = {}
    for question, what, markers in MEMORY_RECALL:
        # История одна и та же для всех вопросов: ответ на предыдущий в неё не
        # добавляется, иначе модель отвечала бы уже на свой собственный текст.
        answer = _ask(engine, history + [{"role": "user", "content": question}],
                      temperature, max_tokens)
        found = any(marker in answer.lower() for marker in markers)
        remembered[what] = found
        total += 1
        if not answer:
            empty += 1
        print(f"\n    ? {question}")
        print(f"    > {answer or '(пусто)'}   [{'вспомнил' if found else 'НЕ вспомнил'}]")

    print()
    for what, found in remembered.items():
        log(f"  {what}: {'вспомнил' if found else 'НЕ вспомнил'}")

    return {
        "questions": total,
        "empty": empty,
        "memory": remembered,
        "memory_score": sum(remembered.values()),
    }


# ------------------------------------------------------------------ запуск ----

def run(
    checkpoint: str = "checkpoints/v2/best.pt",
    data: Optional[str] = None,
    prefer_device: str = "auto",
    temperature: float = 0.7,
    max_tokens: int = 120,
    batches: int = 60,
    block_size: int = 1024,
    from_brain: bool = True,
) -> int:
    """Точка входа команды `eval`."""
    from .serve import Engine

    banner("Оценка модели...")

    if data:
        rule("Перплексия на отложенной выборке")
        result = measure_perplexity(checkpoint, data, block_size=block_size,
                                    batches=batches, prefer_device=prefer_device,
                                    from_brain=from_brain)
        if result:
            log(f"потери {result['loss']:.4f} · перплексия на токен "
                f"{result['perplexity']:.2f} · окон {result['windows']} "
                f"· токенов ответа {result['answer_tokens']} · {result['seconds']} с")
            log(f"БИТ НА СИМВОЛ: {result['bits_per_char']:.3f} "
                f"(символов на токен: {result['chars_per_token']:.2f})")
            log("Сравнивать модели нужно по битам на символ: перплексия на токен "
                "зависит от словаря и между разными токенизаторами несопоставима.")
    else:
        log("Файл данных не указан (--data) — перплексию не считаю, "
            "прогоняю только живые ответы")

    engine = Engine.load(checkpoint, prefer_device=prefer_device)
    summary = run_probes(engine, temperature, max_tokens)

    rule("Итог")
    log(f"задано вопросов: {summary['questions']} · пустых ответов: {summary['empty']}")
    log(f"память в разговоре: {summary['memory_score']} из {len(MEMORY_RECALL)}")
    rule()
    return 0
