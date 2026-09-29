"""
Cloud HDR на основе Qwen3-4B — быстрый движок: llama.cpp (llama-server).

Почему не transformers + bitsandbytes (engine.py). Замер: 4-битная модель через
bitsandbytes даёт около 15 токенов в секунду — длинный ответ шёл 20-27 секунд.
llama.cpp на той же видеокарте и том же объёме памяти в разы быстрее: у него
свои ядра под 4-битные веса.

Как устроено. Сервер модели Cloud HDR (serve.py) остаётся тем же и на том же
порту 8080 — окно приложения ничего не замечает. Внутри он держит дочерний
llama-server на 127.0.0.1:8091 и пересылает ему разговор. Основа — GGUF Q4_K_M
(~2,5 ГБ), поверх неё LoRA Cloud HDR (дообучение) — отдельным файлом, как и при
обучении. В простое llama-server закрывается целиком: видеокарта освобождается
полностью, а на следующий вопрос он поднимается за пару секунд (файл уже в кэше
Windows). Так всё приложение укладывается в 4 ГБ видеопамяти.
"""
from __future__ import annotations

import json
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from cloudhdr_ai import knowledge, profiles
from cloudhdr_ai.branding import log
from cloudhdr_qwen.prepare import SYSTEM as CLOUD_SYSTEM

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT.parent
SERVER = APP / 'runtime' / 'llama' / 'llama-server.exe'
MODEL = ROOT / 'models' / 'gguf' / 'qwen3-4b-q4km.gguf'
LORA = ROOT / 'models' / 'gguf' / 'cloudhdr-lora.gguf'
PORT = 8091
CONTEXT = 4096
NO_WINDOW = 0x08000000
# Сила дообучения на каждый запрос (подобрана на живых ответах 2026-09-27).
# В корпусе v7 у кода испорчены отступы, и с полной силой LoRA модель писала
# Python с одним пробелом вместо четырёх — такой код не запускается. А полная
# сила в разговоре делала ответы сухими («мне грустно» → «я тоже»). На 0,4
# остаются имя и характер Cloud HDR, а теплота и знания — от основы; код пишет
# основа без добавки.
#
# 2026-09-29: 0,4 → 0,2. Замер на пяти многоходовых диалогах (по 4 реплики,
# четыре настройки): на 0,4 модель заполняла ответ списками и заголовками даже
# на «как дела», повторяла дежурное «я здесь, чтобы поддержать тебя» и
# выдумывала себе прошлое. На 0,2 вместе со штрафами ниже — живой разговор:
# держит нить, спрашивает в ответ, отвечает по делу. Без дообучения (0) снова
# списки, эмодзи и выдуманные характеристики железа.
LORA_TALK = 0.2
LORA_CODE = 0.0
CODE_HINT = ('Если просят код — дай короткую вводную фразу, затем код в блоке ``` с указанием языка, '
             'затем одну-две строки о том, как им пользоваться.')
TALK_HINT = ('Веди живой разговор: отвечай на то, что спросили сейчас, опираясь на сказанное раньше; '
             'можешь задать встречный вопрос. Не представляйся и не перечисляй свои умения, если об этом '
             'не спросили. Не повторяй свои прошлые ответы и не используй дежурные фразы вроде «спасибо, '
             'что поделились мнением» или «я стараюсь быть полезным». О себе говори в мужском роде.')
# Против повторов в разговоре (в коде повтор — норма, там их нет):
# DRY штрафует продолжение фразы, которая уже была в разговоре, — именно так
# модель копировала свои прошлые ответы; presence — возврат к тем же словам.
TALK_SAMPLING = {'repeat_penalty': 1.1, 'presence_penalty': 0.3, 'dry_multiplier': 0.8, 'dry_base': 1.75,
                 'dry_allowed_length': 3, 'dry_penalty_last_n': CONTEXT}
TALK_TEMPERATURE = 0.4


class _Config:
    block_size = CONTEXT


class _Shim:
    config = _Config()


class Info:
    is_cuda = True
    device = 'cuda:0'
    type = 'cuda'
    compatible = True

    def __init__(self):
        try:
            out = subprocess.run(['nvidia-smi', '--query-gpu=name', '--format=csv,noheader'], capture_output=True,
                                 text=True, creationflags=NO_WINDOW, timeout=10).stdout.strip()
            self.name = out.splitlines()[0] if out else 'GPU'
        except (OSError, subprocess.SubprocessError):
            self.name = 'GPU'


class LlamaEngine:
    # Полчаса, а не три минуты: голосовые команды идут с паузами, и каждая
    # выгрузка стоила следующей команде 2-4 с на загрузку. Вместе с
    # распознаванием речи (8 бит) это около 3,7 ГБ — в пределах 4 ГБ.
    IDLE_SECONDS = 1800

    def __init__(self):
        from transformers import AutoTokenizer
        if not SERVER.exists() or not MODEL.exists():
            raise FileNotFoundError('нет llama-server или модели GGUF')
        # токенизатор нужен серверу только для подсчёта токенов в ответе API
        self.tokenizer = AutoTokenizer.from_pretrained(str(ROOT / 'models' / 'qwen3-4b'))
        self.info = Info()
        self.precision = 'q4_k_m'
        self.params_label = '4 млрд'
        self.lora = LORA if LORA.exists() else None
        self.label = 'Cloud HDR · дообученная' if self.lora else 'Cloud HDR · основа без дообучения'
        self.model = _Shim()
        self.proc: Optional[subprocess.Popen] = None
        self.busy = False
        self.wiki = True
        self.last_source = None
        self.last_used = time.time()
        self.placement = threading.Lock()
        self.repetition_penalty = 1.05
        self.on_gpu = False
        # Ключ на каждый запуск: llama-server по умолчанию отвечает любому
        # источнику (CORS *), и без ключа любой сайт в браузере мог бы гонять
        # видеокарту. Ключ знает только этот процесс.
        self.key = secrets.token_urlsafe(24)

    # ------------------------------------------------------------ память --
    def _alive(self) -> bool:
        if not self.proc or self.proc.poll() is not None:
            return False
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{PORT}/health', headers={'Authorization': f'Bearer {self.key}'})
            with urllib.request.urlopen(request, timeout=2) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def wake(self) -> None:
        self.last_used = time.time()
        if self._alive():
            return
        with self.placement:
            if self._alive():
                return
            started = time.time()
            # очередь видеопамяти: голос и распознавание уезжают в ОЗУ
            from cloudhdr_gpu import make_room
            make_room(3.0, 'llm')
            args = [str(SERVER), '-m', str(MODEL), '--host', '127.0.0.1', '--port', str(PORT), '-c', str(CONTEXT),
                    '-ngl', '99', '--jinja', '--no-webui', '-fa', 'on', '--cache-type-k', 'q8_0', '--cache-type-v', 'q8_0',
                    '--parallel', '1', '--api-key', self.key]
            if self.lora:
                args += ['--lora', str(self.lora)]
            logfile = open(ROOT.parent / 'runtime' / 'llama' / 'server.log', 'ab')  # noqa: SIM115
            self.proc = subprocess.Popen(args, stdout=logfile, stderr=logfile, creationflags=NO_WINDOW,
                                         cwd=str(SERVER.parent))
            for _ in range(240):
                if self._alive():
                    break
                if self.proc.poll() is not None:
                    raise RuntimeError('llama-server не поднялся — см. runtime/llama/server.log')
                time.sleep(0.25)
            self.on_gpu = True
            log(f'Модель на видеокарте за {time.time() - started:.1f} с (llama.cpp)')

    def sleep_if_idle(self) -> None:
        if not self.proc or self.busy or time.time() - self.last_used < self.IDLE_SECONDS:
            return
        with self.placement:
            if not self.proc or self.busy:
                return
            self.unload()
            log(f'Простой {self.IDLE_SECONDS // 60} мин — модель выгружена, видеокарта свободна')

    def unload(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        self.on_gpu = False

    def release(self) -> None:
        pass

    # --------------------------------------------------------- генерация --
    def _chat(self, messages: list, system: Optional[str], found: Optional[dict], code: bool = False) -> list:
        role = CLOUD_SYSTEM + ' ' + (CODE_HINT if code else TALK_HINT)
        if not code and system and ('```' in system or 'код' in system.lower()):
            role = CLOUD_SYSTEM + ' ' + CODE_HINT
        if found:
            role = knowledge.augment(role, found)
        rest = [{'role': m['role'], 'content': str(m.get('content', ''))} for m in messages
                if isinstance(m, dict) and m.get('role') in ('user', 'assistant')]
        return [{'role': 'system', 'content': role}] + rest

    def stream(self, messages: list, temperature: float, max_tokens: int, top_p: float, top_k: int,
               finish: str = 'sentence', grace: int = 80, system: Optional[str] = None,
               wiki: Optional[bool] = None, repetition_penalty: Optional[float] = None):
        use_wiki = self.wiki if wiki is None else (self.wiki and wiki)
        found = None
        if use_wiki:
            question = next((str(m.get('content', '')) for m in reversed(messages)
                             if isinstance(m, dict) and m.get('role') == 'user'), '')
            found = knowledge.lookup(question)
            if found:
                self.last_source = found
                log(f"Справка: «{found['title']}» — отвечаю по ней")
        question = next((str(m.get('content', '')) for m in reversed(messages)
                         if isinstance(m, dict) and m.get('role') == 'user'), '')
        code = profiles.kind_of(question) == profiles.KIND_CODE
        body = {
            'messages': self._chat(messages, system, found, code), 'stream': True,
            'max_tokens': int(max_tokens + grace),
            # не выше 0,5: у дообученной модели на 0,55 изредка проскакивали
            # странные ответы из корпуса («Плохо, сижу в ужасном положении»);
            # в разговоре — 0,4, на ней и подбирались настройки ниже
            'temperature': min(float(temperature), 0.5 if code else TALK_TEMPERATURE), 'top_p': float(top_p),
            'top_k': int(top_k or 40), 'repeat_penalty': float(repetition_penalty or self.repetition_penalty),
            'chat_template_kwargs': {'enable_thinking': False},
        }
        if not code:
            body.update(TALK_SAMPLING)
        if self.lora:
            body['lora'] = [{'id': 0, 'scale': LORA_CODE if code else LORA_TALK}]
        self.busy = True
        response = None
        try:
            self.wake()
            request = urllib.request.Request(f'http://127.0.0.1:{PORT}/v1/chat/completions',
                                             data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                             headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.key}'})
            response = urllib.request.urlopen(request, timeout=300)
            text = ''
            for raw in response:
                line = raw.decode('utf-8', 'replace').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    delta = json.loads(data)['choices'][0].get('delta', {}).get('content') or ''
                except (ValueError, KeyError, IndexError):
                    continue
                if not delta:
                    continue
                text += delta
                if profiles.looping(text):
                    log('Повтор в ответе — останавливаю генерацию')
                    break
                yield delta
        finally:
            if response is not None:
                response.close()          # закрытое соединение — llama-server прекращает генерацию
            self.busy = False
            self.last_used = time.time()

    def complete(self, messages: list, **kwargs) -> str:
        return ''.join(self.stream(messages, **kwargs)).strip()

    # ------------------------------------------------------ просьба → команды --
    def route(self, text: str, history: list | None = None) -> dict:
        """Переписать просьбу в команды приложения (cloudhdr_qwen/router.py)."""
        from cloudhdr_qwen import router
        body = {
            'messages': router.build_messages(text, history), 'stream': False, 'max_tokens': 300,
            'temperature': 0.1, 'top_p': 0.9, 'top_k': 20,
            'response_format': {'type': 'json_schema', 'json_schema': {'name': 'route', 'schema': router.SCHEMA}},
            'chat_template_kwargs': {'enable_thinking': False},
        }
        # Основа следует инструкции точнее, чем с добавкой характера: здесь
        # нужен не стиль, а аккуратный JSON.
        if self.lora:
            body['lora'] = [{'id': 0, 'scale': 0.0}]
        self.busy = True
        try:
            self.wake()
            request = urllib.request.Request(f'http://127.0.0.1:{PORT}/v1/chat/completions',
                                             data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                             headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.key}'})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode('utf-8'))
            content = data['choices'][0]['message']['content']
            result = json.loads(content)
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as error:
            log(f'Разбор просьбы не удался: {error}')
            return {'commands': [], 'ask': '', 'say': ''}
        finally:
            self.busy = False
            self.last_used = time.time()
        commands = [c.strip() for c in result.get('commands', []) if isinstance(c, str) and c.strip()][:5]
        return router.clean({'commands': commands, 'ask': str(result.get('ask') or '').strip(),
                             'say': str(result.get('say') or '').strip()}, text)

    # ------------------------------------------------------ руки: шаг за шагом --
    def act(self, goal: str, scene: str, history: list | None = None, plan: bool = False,
            context: str = '') -> dict:
        """Руки (cloudhdr_qwen/hands.py): план подзадач или следующее действие."""
        from cloudhdr_qwen import hands
        if plan:
            messages, schema, name = hands.build_plan_messages(goal, scene), hands.PLAN_SCHEMA, 'plan'
        else:
            messages, schema, name = hands.build_messages(goal, scene, history, context), hands.SCHEMA, 'act'
        body = {
            'messages': messages, 'stream': False, 'max_tokens': 320,
            'temperature': 0.1, 'top_p': 0.9, 'top_k': 20,
            'response_format': {'type': 'json_schema', 'json_schema': {'name': name, 'schema': schema}},
            'chat_template_kwargs': {'enable_thinking': False},
        }
        if self.lora:
            body['lora'] = [{'id': 0, 'scale': 0.0}]
        self.busy = True
        try:
            self.wake()
            request = urllib.request.Request(f'http://127.0.0.1:{PORT}/v1/chat/completions',
                                             data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
                                             headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.key}'})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = json.loads(response.read().decode('utf-8'))
            result = json.loads(data['choices'][0]['message']['content'])
        except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError) as error:
            log(f'Шаг рук не получился: {error}')
            if plan:
                return {'steps': [], 'ask': '', 'say': 'Модель не ответила.', 'failed': True}
            return {'why': '', 'action': 'fail', 'id': 0, 'text': '', 'say': 'Модель не ответила.'}
        finally:
            self.busy = False
            self.last_used = time.time()
        return hands.clean_plan(result) if plan else hands.clean(result)
