"""
Cloud HDR на основе Qwen3-4B — дообучение (QLoRA).

Основа грузится в 4 битах и не меняется; учится только LoRA — небольшие
добавки к слоям внимания и MLP (~30 МБ). Этого достаточно, чтобы модель
заговорила как Cloud HDR, и мало, чтобы она забыла то, что знала.

Ошибка считается только по ответам ассистента: вопросы и роль модель видит,
но учиться их писать ей незачем.

    ..\\runtime\\python.exe -m cloudhdr_qwen.train              обучение
    ..\\runtime\\python.exe -m cloudhdr_qwen.train --resume     продолжить с последнего сохранения

Сохраняется в checkpoints/qwen-cloudhdr/: adapter (LoRA) каждые SAVE_EVERY шагов
и в конце; state.json — где остановились.
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / 'models' / 'qwen3-4b'
DATA = ROOT / 'data' / 'qwen-cloudhdr.jsonl'
EVAL = ROOT / 'data' / 'qwen-cloudhdr-eval.jsonl'
OUT = ROOT / 'checkpoints' / 'qwen-cloudhdr'

MAX_LEN = 1024
ACCUM = 16                 # примеров на один шаг оптимизатора
LR = 1e-4
EPOCHS = 1
SAVE_EVERY = 100           # шагов
LORA_R, LORA_ALPHA = 16, 32


def log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line, flush=True)
    with (OUT / 'train.log').open('a', encoding='utf-8') as handle:
        handle.write(line + '\n')


def load_rows(path: Path) -> list[list[dict]]:
    with path.open(encoding='utf-8') as handle:
        return [json.loads(line)['messages'] for line in handle if line.strip()]


def encode(tok, messages: list[dict]):
    """input_ids и labels: -100 везде, кроме токенов ответов ассистента."""
    ids, labels = [], []
    for i in range(len(messages)):
        # Шаблон Qwen рендерится целиком, а границы реплик находятся по префиксам:
        # так разметка совпадает с тем, что модель увидит в работе.
        prefix = tok.apply_chat_template(messages[:i + 1], tokenize=False, add_generation_prompt=False, enable_thinking=False)
        piece = tok(prefix, add_special_tokens=False)['input_ids'][len(ids):]
        if messages[i]['role'] == 'assistant':
            # «<|im_start|>assistant\n» — не ответ; учим начиная с текста
            head = tok('<|im_start|>assistant\n', add_special_tokens=False)['input_ids']
            skip = len(head) if piece[:len(head)] == head else 0
            labels += [-100] * skip + piece[skip:]
        else:
            labels += [-100] * len(piece)
        ids += piece
    if len(ids) > MAX_LEN:
        return None
    return ids, labels


def main() -> None:
    from peft import LoraConfig, PeftModel, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    import bitsandbytes as bnb

    resume = '--resume' in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(7)
    tok = AutoTokenizer.from_pretrained(str(BASE))
    quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.bfloat16,
                               bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(str(BASE), quantization_config=quant, device_map='cuda:0', dtype=torch.bfloat16)
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    state = {'step': 0, 'seen': 0}
    adapter = OUT / 'adapter'
    if resume and (adapter / 'adapter_config.json').exists():
        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=True)
        state = json.loads((OUT / 'state.json').read_text(encoding='utf-8'))
        log(f'Продолжаю с шага {state["step"]}')
    else:
        model = get_peft_model(model, LoraConfig(r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=0.05, bias='none', task_type='CAUSAL_LM',
                                                 target_modules=['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']))
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    log(f'Обучаемых параметров: {trainable / 1e6:.1f} млн')

    rows = [r for r in (encode(tok, m) for m in load_rows(DATA)) if r]
    random.Random(7).shuffle(rows)
    total_steps = math.ceil(len(rows) * EPOCHS / ACCUM)
    log(f'Примеров: {len(rows)}; шагов: {total_steps}')

    optim = bnb.optim.PagedAdamW8bit([p for p in model.parameters() if p.requires_grad], lr=LR, weight_decay=0.0)
    def lr_at(step):
        warm = 20
        if step < warm:
            return LR * (step + 1) / warm
        return LR * 0.5 * (1 + math.cos(math.pi * (step - warm) / max(1, total_steps - warm)))

    model.train()
    started, running, count = time.time(), 0.0, 0
    order = rows * EPOCHS
    position = state['seen']
    while state['step'] < total_steps and position < len(order):
        for group in optim.param_groups:
            group['lr'] = lr_at(state['step'])
        for _ in range(ACCUM):
            if position >= len(order):
                break
            ids, labels = order[position]
            position += 1
            batch = torch.tensor([ids], device='cuda:0')
            target = torch.tensor([labels], device='cuda:0')
            loss = model(input_ids=batch, labels=target).loss / ACCUM
            loss.backward()
            running += loss.item() * ACCUM
            count += 1
        torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
        optim.step()
        optim.zero_grad(set_to_none=True)
        state['step'] += 1
        state['seen'] = position
        if state['step'] % 10 == 0:
            speed = (time.time() - started) / max(1, count) * ACCUM
            left = (total_steps - state['step']) * speed / 60
            log(f'шаг {state["step"]}/{total_steps} · ошибка {running / count:.3f} · {speed:.1f} с/шаг · осталось ~{left:.0f} мин · '
                f'видеопамять {torch.cuda.max_memory_allocated() / 1024 ** 3:.1f} ГБ')
            running, count, started = 0.0, 0, time.time()
        if state['step'] % SAVE_EVERY == 0 or state['step'] >= total_steps:
            model.save_pretrained(str(adapter))
            (OUT / 'state.json').write_text(json.dumps(state), encoding='utf-8')
            log(f'Сохранено: шаг {state["step"]}')

    # Проверочная ошибка на отложенных диалогах — число для сравнения версий
    model.eval()
    losses = []
    with torch.no_grad():
        for m in load_rows(EVAL)[:150]:
            r = encode(tok, m)
            if r:
                ids, labels = r
                losses.append(model(input_ids=torch.tensor([ids], device='cuda:0'),
                                    labels=torch.tensor([labels], device='cuda:0')).loss.item())
    log(f'Готово. Ошибка на проверке: {sum(losses) / max(1, len(losses)):.3f}')


if __name__ == '__main__':
    main()
