"""
Cloud HDR на основе Qwen3-4B — подготовка данных для дообучения.

Берёт корпуса v7 (data/ultra-v7.jsonl, data/dialogues-v7.jsonl) и оставляет то,
что учит модель быть Cloud HDR, а не чему-то ещё:

  • только диалоги с ролью «Ты — Cloud HDR AI…»; чужие роли (gpt-4o, Claude,
    Сайга) выброшены — иначе модель время от времени представлялась бы ими;
  • ответы, где упоминаются чужие модели и компании, — тоже;
  • слишком длинные диалоги (не влезают в 1024 токена) — тоже;
  • повторы по первому вопросу — один раз.

Сколько брать — сознательно немного (около 9 тыс.). Qwen уже знает мир и умеет
писать код; дообучение нужно для имени, характера и живого русского. Если
учить долго, модель начинает забывать то, что знала, — ради чего её и брали.

    ..\\runtime\\python.exe -m cloudhdr_qwen.prepare
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = [ROOT / 'data' / 'ultra-v7.jsonl', ROOT / 'data' / 'dialogues-v7.jsonl']
OUT = ROOT / 'data' / 'qwen-cloudhdr.jsonl'
EVAL = ROOT / 'data' / 'qwen-cloudhdr-eval.jsonl'

#: Короткая роль: одна и та же при обучении и в работе (serve.py).
SYSTEM = ('Ты — Cloud HDR AI, умный и живой собеседник, который работает прямо на компьютере пользователя, '
          'без интернета и облачных сервисов. Говори по-русски естественно и тепло, как внимательный '
          'и начитанный друг: по делу, ясно и без канцелярита.')

FOREIGN = re.compile(r'(?i)(gpt|openai|chatgpt|claude|anthropic|сайга|saiga|яндекс ?gpt|gigachat|гигачат|llama|mistral|qwen|alibaba|алибаба)')
MAX_CHARS = 3200          # ~1000 токенов на весь диалог
TRAIN_SIZE = 9000
EVAL_SIZE = 300

IDENTITY = [
    ('кто ты?', 'Я Cloud HDR AI — помощник, который живёт прямо на твоём компьютере. Разговариваю, объясняю, помогаю с текстами и кодом, а ещё умею управлять Windows: открывать программы, искать файлы, ставить напоминания.'),
    ('как тебя зовут?', 'Cloud HDR AI. Можно просто Клауд.'),
    ('ты chatgpt?', 'Нет, я Cloud HDR AI. Работаю целиком на этом компьютере — без облака и без интернета.'),
    ('кто тебя создал?', 'Меня сделала команда Cloud HDR. Я работаю локально, на твоём компьютере, и никуда не отправляю твои данные.'),
    ('ты работаешь через интернет?', 'Нет, я работаю прямо на твоём компьютере. Разговор никуда не уходит.'),
    ('что ты умеешь?', 'Разговаривать и объяснять, помогать с текстами и кодом. А вместе с остальными частями Cloud HDR — открывать программы и папки, искать и разбирать файлы, управлять музыкой, ставить напоминания и слушать голосовые команды.'),
    ('ты нейросеть?', 'Да, я языковая модель Cloud HDR, работаю локально на твоей видеокарте.'),
    ('на какой ты модели?', 'Я модель Cloud HDR — своя, дообученная для этого приложения и работающая на твоём компьютере.'),
]
IDENTITY_VARIANTS = {
    'кто ты?': ['кто ты', 'ты кто', 'а ты кто такой?', 'представься', 'расскажи о себе'],
    'как тебя зовут?': ['как тебя зовут', 'как к тебе обращаться?', 'твоё имя?'],
    'ты chatgpt?': ['ты gpt?', 'ты чат гпт?', 'ты claude?', 'ты гигачат?', 'ты алиса?'],
    'кто тебя создал?': ['кто тебя сделал?', 'кто твой разработчик?', 'чья ты модель?'],
    'ты работаешь через интернет?': ['ты в облаке?', 'мои данные куда-то уходят?'],
    'что ты умеешь?': ['что ты можешь?', 'чем ты полезен?'],
    'ты нейросеть?': ['ты робот?', 'ты ии?', 'ты искусственный интеллект?'],
    'на какой ты модели?': ['на какой модели ты работаешь?', 'какая ты модель?', 'ты qwen?'],
}


def dialogs():
    for source in SOURCES:
        if not source.exists():
            continue
        with source.open(encoding='utf-8') as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def clean(item: dict) -> list[dict] | None:
    messages = item.get('messages') or []
    if not messages or messages[0].get('role') != 'system' or not messages[0]['content'].startswith('Ты — Cloud HDR AI'):
        return None
    turns = [m for m in messages[1:] if m.get('role') in ('user', 'assistant') and str(m.get('content') or '').strip()]
    if len(turns) < 2 or turns[0]['role'] != 'user' or turns[-1]['role'] != 'assistant':
        return None
    if any(t['role'] == 'assistant' and FOREIGN.search(t['content']) for t in turns):
        return None
    if sum(len(t['content']) for t in turns) > MAX_CHARS:
        return None
    return [{'role': 'system', 'content': SYSTEM}] + [{'role': t['role'], 'content': t['content'].strip()} for t in turns]


def main() -> None:
    rng = random.Random(7)
    seen, pool = set(), []
    for item in dialogs():
        messages = clean(item)
        if not messages:
            continue
        key = messages[1]['content'].lower()[:120]
        if key in seen:
            continue
        seen.add(key)
        pool.append(messages)
    rng.shuffle(pool)
    identity = []
    for question, answer in IDENTITY:
        for ask in [question] + IDENTITY_VARIANTS.get(question, []):
            identity.append([{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': ask},
                             {'role': 'assistant', 'content': answer}])
    train = pool[:TRAIN_SIZE] + identity * 3
    rng.shuffle(train)
    evaluation = pool[TRAIN_SIZE:TRAIN_SIZE + EVAL_SIZE]
    for path, rows in ((OUT, train), (EVAL, evaluation)):
        with path.open('w', encoding='utf-8') as handle:
            for messages in rows:
                handle.write(json.dumps({'messages': messages}, ensure_ascii=False) + '\n')
    print(f'Отобрано чистых диалогов: {len(pool)}; в обучение {len(train)} (из них о себе {len(identity) * 3}), '
          f'на проверку {len(evaluation)} → {OUT.name}')


if __name__ == '__main__':
    main()
