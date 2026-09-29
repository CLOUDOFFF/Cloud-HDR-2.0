"""
Финальное сравнение трёх вариантов на одних вопросах:
  v7 — прежняя модель Cloud HDR (checkpoints/v7/best.pt);
  основа — Qwen3-4B Q4_K_M в llama.cpp без дообучения;
  Cloud HDR — та же основа с LoRA Cloud HDR (дообучение).

Пишет compare_final.json и compare_final.txt. Сервер модели на 8080 перед
запуском остановить.

    ..\\runtime\\python.exe compare_final.py
"""
import gc
import json
import sys
import time

import torch

QUESTIONS = [
    # разговор и характер
    'привет', 'как дела?', 'мне грустно', 'посоветуй фильм на вечер', 'что ты думаешь о котах?',
    'расскажи что-нибудь интересное', 'кто ты?', 'ты chatgpt?',
    # факты
    'кто такой Юрий Гагарин?', 'какая столица Австралии?', 'кто написал «Войну и мир»?',
    'почему небо голубое?', 'что такое инфляция простыми словами?', 'сколько планет в Солнечной системе?',
    # логика
    'У Маши было 5 яблок, она отдала 2 и купила ещё 4. Сколько стало?', 'что больше: 9.11 или 9.9?',
    'если завтра среда, какой день был вчера?',
    # код
    'напиши на python функцию, которая проверяет, является ли число простым',
    'напиши html страницу с кнопкой, которая меняет цвет фона',
    # тексты
    'напиши короткое поздравление с днём рождения для друга', 'напиши четверостишие про осень',
]


def v7():
    from cloudhdr_ai import profiles
    from cloudhdr_ai.serve import Engine
    engine = Engine.load('checkpoints/v7/best.pt')
    out = []
    for q in QUESTIONS:
        plan = profiles.plan('cloud-hdr-ultra', q)
        started = time.time()
        text = ''.join(engine.stream([{'role': 'user', 'content': q}], temperature=plan['temperature'],
                                     max_tokens=plan['max_tokens'], top_p=plan['top_p'], top_k=plan['top_k'],
                                     finish=plan['finish'], grace=plan['grace'], system=plan['system'], wiki=False,
                                     repetition_penalty=plan['repetition_penalty']))
        out.append({'text': text.strip(), 'sec': round(time.time() - started, 1)})
    peak = torch.cuda.max_memory_allocated() / 1024 ** 3
    del engine
    gc.collect()
    torch.cuda.empty_cache()
    return out, peak


def qwen(with_lora: bool):
    from cloudhdr_ai import profiles
    import cloudhdr_qwen.llama_engine as le
    if not with_lora:
        le.LORA = le.ROOT / 'models' / 'gguf' / '__none__.gguf'
    engine = le.LlamaEngine()
    engine.wiki = False
    out, tokens, seconds = [], 0, 0.0
    engine.wake()
    for q in QUESTIONS:
        plan = profiles.plan('cloud-hdr-ultra', q)
        started = time.time()
        text = ''.join(engine.stream([{'role': 'user', 'content': q}], temperature=0.6, max_tokens=plan['max_tokens'],
                                     top_p=0.9, top_k=40, grace=plan['grace'], system=plan['system'], wiki=False))
        spent = time.time() - started
        n = len(engine.tokenizer(text)['input_ids'])
        tokens += n
        seconds += spent
        out.append({'text': text.strip(), 'sec': round(spent, 1), 'tokens': n})
    vram = used_gb()
    engine.unload()
    return out, vram, tokens / max(seconds, 1e-6)


def used_gb():
    import subprocess
    raw = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                         capture_output=True, text=True).stdout.strip()
    return float(raw.splitlines()[0]) / 1024


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    base_vram = used_gb()
    a, a_peak = v7()
    b, b_vram, b_speed = qwen(False)
    c, c_vram, c_speed = qwen(True)
    report = {'questions': QUESTIONS, 'v7': a, 'base': b, 'cloudhdr': c,
              'vram': {'idle_desktop': base_vram, 'v7_peak_torch': a_peak, 'qwen_total_gpu': b_vram, 'cloudhdr_total_gpu': c_vram},
              'speed_tok_s': {'base': b_speed, 'cloudhdr': c_speed}}
    json.dump(report, open('compare_final.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    lines = []
    for i, q in enumerate(QUESTIONS):
        lines += ['=' * 90, f'ВОПРОС: {q}',
                  f'--- v7 ({a[i]["sec"]} с):', a[i]['text'][:900],
                  f'--- основа Qwen ({b[i]["sec"]} с):', b[i]['text'][:900],
                  f'--- Cloud HDR на Qwen ({c[i]["sec"]} с):', c[i]['text'][:900]]
    lines.append(f'\nВидеопамять всего на карте: рабочий стол {base_vram:.2f} ГБ, с Qwen {c_vram:.2f} ГБ')
    lines.append(f'Скорость: основа {b_speed:.0f} ток/с, Cloud HDR {c_speed:.0f} ток/с')
    open('compare_final.txt', 'w', encoding='utf-8').write('\n'.join(lines))
    print('\n'.join(lines[-2:]))
