"""
Сравнение ответов v6 и v7 на одних и тех же вопросах — с теми же настройками
разговора, что у сервера (profiles.plan). Запуск из папки python:

    ..\\runtime\\python.exe compare_v6_v7.py
"""
import gc
import sys

import torch

from cloudhdr_ai import profiles
from cloudhdr_ai.serve import Engine

QUESTIONS = [
    'привет',
    'как дела?',
    'кто ты?',
    'мне грустно',
    'почему небо голубое?',
    'объясни простыми словами, что такое инфляция',
    'посоветуй, чем заняться вечером',
    'расскажи что-нибудь интересное',
]
DIALOG = [  # память в разговоре
    'привет, меня зовут Мирослав',
    'я живу в Москве и увлекаюсь фотографией',
    'как меня зовут и чем я увлекаюсь?',
]


def ask(engine, messages):
    question = messages[-1]['content']
    plan = profiles.plan('cloud-hdr', question)
    text = ''
    for piece in engine.stream(messages, temperature=plan['temperature'], max_tokens=plan['max_tokens'],
                               top_p=plan['top_p'], top_k=plan['top_k'], finish=plan['finish'],
                               grace=plan['grace'], system=plan['system'], wiki=False,
                               repetition_penalty=plan['repetition_penalty']):
        text += piece
    return text.strip()


def run(checkpoint):
    torch.manual_seed(7)
    engine = Engine.load(checkpoint, 'ai-forever/rugpt3large_based_on_gpt2', 'auto')
    answers = [ask(engine, [{'role': 'user', 'content': q}]) for q in QUESTIONS]
    history = []
    for line in DIALOG:
        history.append({'role': 'user', 'content': line})
        history.append({'role': 'assistant', 'content': ask(engine, history)})
    del engine
    gc.collect()
    torch.cuda.empty_cache()
    return answers, history


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    results = {name: run(f'checkpoints/{name}/best.pt') for name in ('v6', 'v7')}
    for i, q in enumerate(QUESTIONS):
        print(f'\n### {q}')
        for name in ('v6', 'v7'):
            print(f'[{name}] {results[name][0][i]}')
    print('\n### Память в разговоре')
    for name in ('v6', 'v7'):
        print(f'\n[{name}]')
        for m in results[name][1]:
            print(('  Вы: ' if m['role'] == 'user' else '  AI: ') + m['content'])
