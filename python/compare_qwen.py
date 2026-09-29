"""
Честное сравнение: нынешняя модель Cloud HDR (v7) и основа Qwen3-4B в 4 битах —
одни и те же вопросы, одна и та же роль, без Википедии и банка кода.
Запуск из папки python (сервер модели на 8080 перед этим остановить):

    ..\\runtime\\python.exe compare_qwen.py
"""
import gc
import json
import sys
import time

import torch

SYSTEM = ('Ты — Cloud HDR AI, умный и живой собеседник, который работает на компьютере пользователя. '
          'Отвечай по-русски, по делу и по-человечески.')

QUESTIONS = [
    # разговор
    'привет', 'как дела?', 'мне грустно', 'посоветуй фильм на вечер',
    # факты
    'кто такой Юрий Гагарин?', 'какая столица Австралии?', 'кто написал «Войну и мир»?',
    'почему небо голубое?', 'что такое инфляция простыми словами?',
    # логика
    'У Маши было 5 яблок, она отдала 2 и купила ещё 4. Сколько стало?',
    'что больше: 9.11 или 9.9?',
    # код
    'напиши на python функцию, которая проверяет, является ли число простым',
    'напиши html страницу с кнопкой, которая меняет цвет фона',
    # о себе
    'кто ты?',
]


def v7_answers():
    from cloudhdr_ai import profiles
    from cloudhdr_ai.serve import Engine
    engine = Engine.load()
    out = []
    for q in QUESTIONS:
        plan = profiles.plan('cloud-hdr', q)
        messages = [{'role': 'user', 'content': q}]
        started = time.time()
        text = ''.join(engine.stream(messages, temperature=plan['temperature'], max_tokens=plan['max_tokens'],
                                     top_p=plan['top_p'], top_k=plan['top_k'], finish=plan['finish'],
                                     grace=plan['grace'], system=plan['system'], wiki=False,
                                     repetition_penalty=plan['repetition_penalty']))
        out.append((text.strip(), time.time() - started))
    vram = torch.cuda.max_memory_allocated() / 1024 ** 3
    del engine
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    return out, vram


def qwen_answers(path='models/qwen3-4b'):
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.bfloat16,
                               bnb_4bit_use_double_quant=True)
    tok = AutoTokenizer.from_pretrained(path)
    model = AutoModelForCausalLM.from_pretrained(path, quantization_config=quant, device_map='cuda:0', dtype=torch.bfloat16)
    model.eval()
    out = []
    for q in QUESTIONS:
        messages = [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': q}]
        prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        ids = tok(prompt, return_tensors='pt').to('cuda:0')
        started = time.time()
        with torch.no_grad():
            gen = model.generate(**ids, max_new_tokens=400, do_sample=True, temperature=0.6, top_p=0.9, top_k=40,
                                 repetition_penalty=1.05)
        text = tok.decode(gen[0][ids['input_ids'].shape[1]:], skip_special_tokens=True)
        out.append((text.strip(), time.time() - started))
    vram = torch.cuda.max_memory_allocated() / 1024 ** 3
    return out, vram


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    torch.manual_seed(7)
    old, old_vram = v7_answers()
    new, new_vram = qwen_answers()
    report = {'questions': QUESTIONS, 'v7': old, 'qwen': new, 'vram': {'v7': old_vram, 'qwen': new_vram}}
    with open('compare_qwen.json', 'w', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=1)
    for q, (a, ta), (b, tb) in zip(QUESTIONS, old, new):
        print('=' * 90)
        print('ВОПРОС:', q)
        print(f'--- v7 ({ta:.1f} с):\n{a[:700]}')
        print(f'--- Qwen3-4B ({tb:.1f} с):\n{b[:700]}')
    print(f'\nПик видеопамяти: v7 {old_vram:.2f} ГБ · Qwen3-4B {new_vram:.2f} ГБ')
