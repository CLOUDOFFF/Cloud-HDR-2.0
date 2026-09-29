"""
Cloud HDR на основе Qwen3-4B — движок для сервера модели (cloudhdr_ai/serve.py).

Интерфейс тот же, что у прежнего Engine: stream(), complete(), tokenizer,
model.config.block_size, wake(), sleep_if_idle(), busy, label. Поэтому сервер,
профили «Cloud HDR 3.2 / Ultra», банк проверенного кода и окно приложения не
знают, что внутри другая основа — наружу модель называется Cloud HDR, как и
раньше.

Память. Основа лежит на видеокарте в 4 битах (~2,6 ГБ вместе с LoRA). Сдвинуть
4-битную модель в оперативную память нельзя — bitsandbytes такого не умеет, —
поэтому в простое она выгружается целиком, а на следующий вопрос читается с
диска заново (несколько секунд: файлы уже в кэше Windows). Так видеокарта
свободна для голоса и распознавания речи, и всё приложение укладывается в
4 ГБ — ноутбук с 6 ГБ видеопамяти его тянет.
"""
from __future__ import annotations

import gc
import threading
import time
from pathlib import Path
from typing import Optional

import torch

from cloudhdr_ai import knowledge, profiles
from cloudhdr_ai.branding import log

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / 'models' / 'qwen3-4b'
ADAPTER = ROOT / 'checkpoints' / 'qwen-cloudhdr' / 'adapter'

# Та же роль, на которой шло дообучение (prepare.py): модель видела её в
# каждом диалоге и отвечает с ней лучше всего.
from cloudhdr_qwen.prepare import SYSTEM as CLOUD_SYSTEM  # noqa: E402

CONTEXT = 4096          # окно, которое отдаём разговору (сама Qwen3 умеет больше)
CODE_HINT = ('Если просят код — дай короткую вводную фразу, затем код в блоке ``` с указанием языка, '
             'затем одну-две строки о том, как им пользоваться.')


class _Config:
    block_size = CONTEXT


class _Shim:
    """То, что сервер спрашивает у model: только config.block_size."""
    config = _Config()


class Info:
    def __init__(self):
        self.is_cuda = torch.cuda.is_available()
        self.device = 'cuda:0' if self.is_cuda else 'cpu'
        self.type = 'cuda' if self.is_cuda else 'cpu'
        self.compatible = True
        self.name = torch.cuda.get_device_name(0) if self.is_cuda else 'CPU'


class QwenEngine:
    IDLE_SECONDS = 180

    def __init__(self, base: Path = BASE, adapter: Optional[Path] = ADAPTER):
        from transformers import AutoTokenizer
        self.base = base
        self.adapter = adapter if adapter and (adapter / 'adapter_config.json').exists() else None
        self.tokenizer = AutoTokenizer.from_pretrained(str(base))
        self.info = Info()
        self.precision = 'nf4'
        self.params_label = '4 млрд'
        self.label = 'Cloud HDR · дообученная' if self.adapter else 'Cloud HDR · основа без дообучения'
        self.model = _Shim()          # для сервера: config.block_size
        self.net = None               # сама сеть; None — выгружена
        self.busy = False
        self.wiki = True
        self.last_source = None
        self.last_used = time.time()
        self.placement = threading.Lock()
        self.repetition_penalty = 1.05
        self.on_gpu = False

    # ------------------------------------------------------------ память --
    def wake(self) -> None:
        self.last_used = time.time()
        if self.net is not None:
            return
        with self.placement:
            if self.net is not None:
                return
            from transformers import AutoModelForCausalLM, BitsAndBytesConfig
            started = time.time()
            quant = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type='nf4',
                                       bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
            net = AutoModelForCausalLM.from_pretrained(str(self.base), quantization_config=quant,
                                                       device_map=self.info.device, dtype=torch.bfloat16)
            if self.adapter:
                from peft import PeftModel
                net = PeftModel.from_pretrained(net, str(self.adapter))
            net.eval()
            self.net = net
            self.on_gpu = self.info.is_cuda
            log(f'Модель на видеокарте за {time.time() - started:.1f} с · '
                f'{torch.cuda.memory_allocated() / 1024 ** 3:.2f} ГБ' if self.info.is_cuda else 'Модель загружена')

    def sleep_if_idle(self) -> None:
        if self.net is None or self.busy or time.time() - self.last_used < self.IDLE_SECONDS:
            return
        with self.placement:
            if self.net is None or self.busy:
                return
            self.unload()
            log(f'Простой {self.IDLE_SECONDS // 60} мин — модель выгружена, видеокарта свободна')

    def unload(self) -> None:
        self.net = None
        self.on_gpu = False
        gc.collect()
        if self.info.is_cuda:
            torch.cuda.empty_cache()

    def release(self) -> None:
        if self.info.is_cuda:
            torch.cuda.empty_cache()

    # --------------------------------------------------------- генерация --
    def _prompt(self, messages: list, system: Optional[str], found: Optional[dict] = None) -> str:
        rest = [m for m in messages if isinstance(m, dict) and m.get('role') in ('user', 'assistant')]
        role = CLOUD_SYSTEM
        if system and ('```' in system or 'код' in system.lower()):
            role = CLOUD_SYSTEM + ' ' + CODE_HINT
        if found:
            role = knowledge.augment(role, found)
        chat = [{'role': 'system', 'content': role}] + [{'role': m['role'], 'content': str(m.get('content', ''))} for m in rest]
        return self.tokenizer.apply_chat_template(chat, tokenize=False, add_generation_prompt=True, enable_thinking=False)

    def stream(self, messages: list, temperature: float, max_tokens: int, top_p: float, top_k: int,
               finish: str = 'sentence', grace: int = 80, system: Optional[str] = None,
               wiki: Optional[bool] = None, repetition_penalty: Optional[float] = None):
        from transformers import TextIteratorStreamer

        use_wiki = self.wiki if wiki is None else (self.wiki and wiki)
        found = None
        if use_wiki:
            question = next((str(m.get('content', '')) for m in reversed(messages)
                             if isinstance(m, dict) and m.get('role') == 'user'), '')
            found = knowledge.lookup(question)
            if found:
                # Эта модель пересказывает справку, не путая имён и дат (у
                # прежней на 760M пересказ был ненадёжен, и факт шёл дословно).
                self.last_source = found
                log(f"Справка: «{found['title']}» — отвечаю по ней")
        prompt = self._prompt(messages, system, found)
        ids = self.tokenizer(prompt, return_tensors='pt', add_special_tokens=False)
        room = CONTEXT - max_tokens - grace
        if ids['input_ids'].shape[1] > room:
            ids = {k: v[:, -room:] for k, v in ids.items()}

        self.busy = True
        try:
            self.wake()
            ids = {k: v.to(self.info.device) for k, v in ids.items()}
            streamer = TextIteratorStreamer(self.tokenizer, skip_prompt=True, skip_special_tokens=True)
            stop = threading.Event()
            from transformers import StoppingCriteria, StoppingCriteriaList

            class _Stop(StoppingCriteria):
                def __call__(self, *_a, **_k):
                    return stop.is_set()

            args = dict(**ids, streamer=streamer, stopping_criteria=StoppingCriteriaList([_Stop()]), max_new_tokens=int(max_tokens + grace), do_sample=temperature > 0,
                        temperature=max(0.05, float(temperature)), top_p=float(top_p), top_k=int(top_k or 0) or None,
                        repetition_penalty=float(repetition_penalty or self.repetition_penalty))
            worker = threading.Thread(target=lambda: self.net.generate(**args), daemon=True)
            worker.start()
            text = ''
            for piece in streamer:
                if not piece:
                    continue
                text += piece
                if profiles.looping(text):
                    log('Повтор в ответе — останавливаю генерацию')
                    break
                yield piece
        finally:
            # и при зацикливании, и если клиент ушёл посреди ответа — генерацию
            # останавливаем, иначе поток крутил бы видеокарту до лимита токенов
            try:
                stop.set()
                worker.join(timeout=30)
            except NameError:
                pass
            self.busy = False
            self.last_used = time.time()
            self.release()

    def complete(self, messages: list, **kwargs) -> str:
        return ''.join(self.stream(messages, **kwargs)).strip()
