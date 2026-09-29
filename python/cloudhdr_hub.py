"""
Cloud HDR Hub — умения второй версии, которым не место в однопоточном агенте.

    python cloudhdr_hub.py            слушает 127.0.0.1:4480 (только этот компьютер)

Что внутри:
  • перевод EN→RU  — своя офлайновая модель Helsinki-NLP opus-mt-en-ru
                     (python/models/translate-en-ru); её зовёт Ctrl+Alt+T;
  • защита         — проверка компьютера по шести категориям угроз и
                     карантин, из которого всё можно вернуть;
  • файлы          — разбор папки по типам: сначала план, потом действие,
                     и отмена одним запросом;
  • медиа, буфер, напоминания — группа 2 интеграции с ПК (cloudhdr_desk.py):
                     «пауза», «следующий трек», «что играет»; «переведи то,
                     что я скопировал»; «напомни через 20 минут…» с
                     уведомлением Windows даже при закрытом окне.

Почему отдельный процесс. Агент (server/agent.ps1) обслуживает запросы по
одному: проверка идёт полминуты и дольше, первая загрузка переводчика —
несколько секунд. Всё это время окно приложения не получало бы ответов.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import winreg
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from cloudhdr_gpu import make_room
from cloudhdr_safety import caller_allowed, forbidden_reason, is_program_folder, project_files
import cloudhdr_apps as apps
import cloudhdr_control as control
import cloudhdr_fileops as fileops
from cloudhdr_apps import friendly


class Utf8Json(JSONResponse):
    # Явный charset обязателен: PowerShell 5.1 (им пользуется Ctrl+Alt+T) без
    # него читает ответ как Latin-1, и русский текст превращается в кракозябры.
    media_type = 'application/json; charset=utf-8'

ROOT = Path(__file__).resolve().parent
HOST = '127.0.0.1'
PORT = int(os.environ.get('CLOUDHDR_HUB_PORT', '4480'))
DATA = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'Cloud HDR'
DATA.mkdir(parents=True, exist_ok=True)
QUARANTINE = DATA / 'quarantine'
STATE_FILE = DATA / 'hub.json'
LOG_FILE = DATA / 'hub.log'
UNDO_FILE = DATA / 'sort-undo.json'
NO_WINDOW = 0x08000000

app = FastAPI(title='Cloud HDR Hub', docs_url=None, redoc_url=None, openapi_url=None,
              default_response_class=Utf8Json)


def log(message: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}"
    print(line, flush=True)
    try:
        with LOG_FILE.open('a', encoding='utf-8') as handle:
            handle.write(line + '\n')
    except OSError:
        pass


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding='utf-8')


STATE = load_state()


def powershell(script: str, timeout: int = 120) -> str:
    """Короткий сценарий PowerShell с выводом в UTF-8."""
    full = '[Console]::OutputEncoding=[Text.Encoding]::UTF8;$ProgressPreference="SilentlyContinue";' + script
    try:
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                               '-Command', full], capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return done.stdout.decode('utf-8', 'replace')
    except (OSError, subprocess.TimeoutExpired) as error:
        log(f'powershell: {error}')
        return ''


@app.middleware('http')
async def cors(request: Request, call_next):
    # Страница приложения живёт на 127.0.0.1:4477 — это другой источник, и без
    # этих заголовков браузер не отдал бы ей ответы. Но только ей: чужой сайт
    # получает отказ ещё до обработчика (см. cloudhdr_safety.caller_allowed).
    allowed, origin = caller_allowed(request.headers, PORT)
    if not allowed:
        log(f'отказ чужому источнику: {request.method} {request.url.path} ({request.headers.get("origin")})')
        return Utf8Json({'ok': False, 'error': 'Запрос не со страницы Cloud HDR'}, status_code=403)
    response = Response(status_code=204) if request.method == 'OPTIONS' else await call_next(request)
    if origin:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Vary'] = 'Origin'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    response.headers['Cache-Control'] = 'no-store'
    return response


async def body_of(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


@app.get('/hub/health')
def health():
    return {'ok': True, 'name': 'Cloud HDR Hub', 'translate': TRANSLATOR.state, 'scan': SCAN.state}


# ================================================================ перевод ===

IMPORT_LOCK = threading.Lock()


class Listener:
    """
    Распознавание речи: Whisper large-v3-turbo, целиком на этом компьютере.

    Зачем, если у ушей (server/ears.ps1) есть свой движок. Тот сравнивает звук
    с эталонами сотни заготовленных фраз — быстро и без модели, но свободную
    речь не понимает в принципе: «перемести отчёт в документы» среди эталонов
    нет и быть не может. Отсюда — всё, что сказано после «Клауд, …», и кнопка
    микрофона в окне приложения (раньше она отдавала звук онлайн-сервису
    браузера, а во встроенном окне WebView2 не работала вовсе).

    Модель поднимается при первой фразе и уходит из памяти после десяти минут
    тишины: полтора гигабайта видеопамяти не должны лежать занятыми весь день.
    """

    # Живёт на видеокарте постоянно, в 8 битах (0,83 ГБ вместо 1,52): рядом с
    # моделью разговора (~2,9 ГБ) это около 3,7 ГБ, в пределах 4 ГБ, и никто
    # никого не выгружает. Раньше распознавание на каждую фразу заезжало на
    # карту и выгоняло модель, а та потом 2-4 с грузилась обратно — голосовые
    # команды шли с задержкой. Точность в 8 битах та же (замер на командах),
    # фраза — около 0,5 с. Без видеокарты или без bitsandbytes — прежний путь:
    # fp16 в ОЗУ и на карту только на время фразы.
    IDLE = 1800
    resident = False

    def __init__(self) -> None:
        self.path = ROOT / 'models' / 'whisper-turbo'
        self.model = None
        self.processor = None
        self.device = 'cpu'
        self.dtype = None
        self.used = 0.0
        self.lock = threading.Lock()

    @property
    def state(self) -> str:
        if not (self.path / 'config.json').exists():
            return 'нет модели'
        return 'готова' if self.model is not None else 'не загружена'

    def load(self) -> None:
        if self.model is not None:
            return
        if not (self.path / 'config.json').exists():
            raise RuntimeError('Модель распознавания речи не найдена в python/models/whisper-turbo')
        import torch
        with IMPORT_LOCK:   # ленивый импорт transformers не терпит двух потоков сразу
            from transformers import WhisperForConditionalGeneration, WhisperProcessor
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.dtype = torch.float16 if self.device == 'cuda' else torch.float32
        started = time.time()
        # Загрузка — под общим замком с переводчиком: from_pretrained на время
        # работы глобально подменяет создание параметров (пустые веса на
        # устройстве meta), и параллельная загрузка в соседнем потоке получала
        # «Cannot copy out of meta tensor».
        with IMPORT_LOCK:
            self.processor = WhisperProcessor.from_pretrained(str(self.path))
            model = None
            if self.device == 'cuda':
                try:
                    from transformers import BitsAndBytesConfig
                    model = WhisperForConditionalGeneration.from_pretrained(
                        str(self.path), quantization_config=BitsAndBytesConfig(load_in_8bit=True),
                        device_map='cuda:0', dtype=torch.float16)
                    self.resident = True
                except Exception as error:  # noqa: BLE001 — нет bitsandbytes: прежний путь
                    log(f'речь: 8 бит не вышло ({error!r}) — fp16 с переездом на карту')
                    model = None
            if model is None:
                model = WhisperForConditionalGeneration.from_pretrained(str(self.path), dtype=self.dtype)
                model.tie_weights()     # proj_out делит веса с эмбеддингами — связать до переноса на карту
                self.resident = False
        self.model = model.eval()
        where = 'на видеокарте, 8 бит' if self.resident else f'{self.device}, с переездом'
        log(f'речь: модель загружена ({where}) за {time.time() - started:.1f} с')

    def warm_on_start(self) -> None:
        """Прослушивание включено — поднять распознавание сразу, а не на первой
        фразе: первая команда голосом иначе ждала бы загрузку."""
        try:
            config = json.loads((ROOT.parent / 'server' / 'config.json').read_text(encoding='utf-8'))
            if not (config.get('listen') or {}).get('enabled', True):
                return
        except (OSError, ValueError):
            pass
        time.sleep(20)          # пусть сначала поднимутся агент и модель
        try:
            with self.lock:
                self.load()
                self.used = time.time()
        except Exception as error:  # noqa: BLE001
            log(f'речь: прогрев при старте не удался — {error!r}')

    def _run(self, features, device: str):
        import torch
        with torch.no_grad():
            ids = self.model.generate(features.to(device, dtype=self.model.dtype), language='ru', task='transcribe',
                                      max_new_tokens=120)
        return self.processor.batch_decode(ids, skip_special_tokens=True)[0].strip()

    def transcribe(self, samples, rate: int = 16000) -> str:
        import numpy as np
        import torch
        audio = np.asarray(samples, dtype=np.float32)
        if rate != 16000:
            import librosa
            audio = librosa.resample(audio, orig_sr=rate, target_sr=16000)
        peak = float(np.abs(audio).max()) if audio.size else 0.0
        if peak < 1e-4:
            return ''
        audio = audio / max(peak, 1e-3) * 0.9           # тихий микрофон — не повод не расслышать
        with self.lock:
            self.load()
            self.used = time.time()
            features = self.processor(audio, sampling_rate=16000, return_tensors='pt').input_features
            if self.resident:
                text = self._run(features, 'cuda')
            elif self.device == 'cuda':
                make_room(1.9, 'asr')
                try:
                    self.model.to('cuda')
                    text = self._run(features, 'cuda')
                except torch.cuda.OutOfMemoryError:
                    # карту так и не освободили (игра, обучение) — считаем на процессоре
                    self.model.to('cpu').float()
                    text = self._run(features, 'cpu')
                    self.model.half()
                finally:
                    self.model.to('cpu')
                    torch.cuda.empty_cache()
            else:
                text = self._run(features, 'cpu')
            self.used = time.time()
        # Whisper на тишине и шуме любит «Продолжение следует…» и «Субтитры…»
        if re.fullmatch(r'(?i)\W*(продолжение следует|субтитры.*|спасибо за просмотр|редактор субтитров.*|\.+)\W*', text):
            return ''
        return text

    def sleep_if_idle(self) -> None:
        if self.model is None or time.time() - self.used < self.IDLE:
            return
        with self.lock:
            if self.model is None or time.time() - self.used < self.IDLE:
                return
            self.model = None
            self.processor = None
            import gc
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            log('речь: модель выгружена из памяти — десять минут тишины')


LISTENER = Listener()
if LISTENER.state != 'нет модели':
    threading.Thread(target=LISTENER.warm_on_start, daemon=True).start()


def _listener_watch() -> None:
    while True:
        time.sleep(60)
        try:
            LISTENER.sleep_if_idle()
        except Exception:  # noqa: BLE001
            pass


threading.Thread(target=_listener_watch, daemon=True).start()


class Translator:
    """opus-mt-en-ru: 300 МБ весов; на видеокарте — доли секунды на абзац."""

    def __init__(self) -> None:
        self.path = ROOT / 'models' / 'translate-en-ru'
        self.model = None
        self.tokenizer = None
        self.device = 'cpu'
        self.state = 'не загружена' if (self.path / 'config.json').exists() else 'нет модели'
        self.lock = threading.Lock()

    def load(self) -> None:
        with self.lock:
            if self.model is not None:
                return
            if not (self.path / 'config.json').exists():
                raise RuntimeError('Модель перевода не найдена в python/models/translate-en-ru')
            self.state = 'загружается'
            import torch
            with IMPORT_LOCK:
                from transformers import MarianMTModel, MarianTokenizer
            # Всегда на процессоре. На видеокарте переводчик держал ~0,5 ГБ
            # (веса плюс контекст CUDA) весь день ради выигрыша в полсекунды,
            # а видеопамять нужнее модели разговора, речи и голосу.
            self.device = 'cpu'
            with IMPORT_LOCK:
                self.tokenizer = MarianTokenizer.from_pretrained(str(self.path))
                self.model = MarianMTModel.from_pretrained(str(self.path)).to(self.device).eval()
            self.state = 'готова'
            log(f'перевод: модель загружена ({self.device})')

    @staticmethod
    def paragraphs(text: str) -> list[str]:
        # Распознанный с экрана текст приходит строками, разорванными по ширине
        # колонки. Склеиваем их в абзацы, иначе модель переводит обрывки фраз.
        result = []
        for block in re.split(r'\n\s*\n', text.strip()):
            joined = re.sub(r'-\n(?=\w)', '', block)
            joined = re.sub(r'\s*\n\s*', ' ', joined).strip()
            if joined:
                result.append(joined)
        return result

    def translate(self, text: str) -> str:
        self.load()
        import torch
        out = []
        for paragraph in self.paragraphs(text):
            # Каждое предложение — отдельный элемент пакета. Склеенные в один
            # кусок, они переводились не целиком: opus-mt часто обрывает вывод
            # после первой точки, и второе предложение просто пропадало.
            chunks = [s.strip() for s in re.split(r'(?<=[.!?])\s+(?=[A-Z0-9"“(])', paragraph) if s.strip()]
            with self.lock, torch.no_grad():
                batch = self.tokenizer(chunks, return_tensors='pt', padding=True, truncation=True,
                                       max_length=512).to(self.device)
                generated = self.model.generate(**batch, max_new_tokens=512, num_beams=4)
            out.append(' '.join(self.tokenizer.batch_decode(generated, skip_special_tokens=True)))
        return '\n\n'.join(out)


TRANSLATOR = Translator()


@app.post('/hub/translate')
def translate(body: dict = Depends(body_of)):
    text = str(body.get('text') or '').strip()[:6000]
    if not text:
        return {'ok': False, 'error': 'Нет текста для перевода'}
    started = time.perf_counter()
    try:
        result = TRANSLATOR.translate(text)
    except Exception as error:  # noqa: BLE001 — показываем человеку как есть
        log(f'перевод: {error}')
        return {'ok': False, 'error': str(error)}
    return {'ok': True, 'text': result, 'ms': int((time.perf_counter() - started) * 1000),
            'device': TRANSLATOR.device}


@app.post('/hub/translate/warm')
def translate_warm():
    threading.Thread(target=lambda: TRANSLATOR.translate('Hello.'), daemon=True).start()
    return {'ok': True}


# ================================================================== файлы ===

SORT_GROUPS = {
    'Фото': {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.tif', '.tiff', '.svg', '.raw', '.cr2', '.nef'},
    'Документы': {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.rtf', '.odt', '.ods', '.csv',
                  '.md', '.epub', '.djvu', '.fb2'},
    'Музыка': {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus'},
    'Видео': {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm', '.flv', '.m4v'},
    'Архивы': {'.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso'},
    'Программы': {'.exe', '.msi', '.msix', '.appx'},
}
SORT_FOLDERS = {'загрузк': 'Downloads', 'download': 'Downloads', 'рабоч': 'Desktop', 'desktop': 'Desktop',
                'документ': 'Documents', 'картин': 'Pictures', 'изображ': 'Pictures'}
KNOWN_FOLDERS = {'Downloads': '{374DE290-123F-4565-9164-39C4925E467B}',
                 'Desktop': '{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}',
                 'Documents': '{FDD39AD0-238F-46AF-ADB4-6C85480369C7}',
                 'Pictures': '{33E28130-4E1E-4676-835A-98395C3BC3BB}'}


def known_folder(name: str) -> Path:
    """Настоящий путь «Загрузок» и прочих — их нередко переносят на другой диск."""
    try:
        guid = (ctypes.c_byte * 16)()
        ctypes.oledll.ole32.CLSIDFromString(KNOWN_FOLDERS[name], ctypes.byref(guid))
        buffer = ctypes.c_wchar_p()
        ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(buffer))
        path = Path(buffer.value)
        ctypes.windll.ole32.CoTaskMemFree(buffer)
        return path
    except Exception:  # noqa: BLE001
        return Path.home() / name


def resolve_sort_target(text: str) -> Path | None:
    lower = text.lower()
    for stem, name in SORT_FOLDERS.items():
        if stem in lower:
            return known_folder(name)
    match = re.search(r'([a-zA-Z]:\\[^"<>|?*]*)', text)
    if match and Path(match.group(1).strip()).is_dir():
        return Path(match.group(1).strip())
    return None


def sort_plan(folder: Path) -> list[dict]:
    """Только файлы верхнего уровня: вложенные папки человек уже разложил сам.

    Файлы git-проекта пропускаются: рабочий стол бывает сам проектом, и его
    README.md с Установить.cmd должны остаться на местах.
    """
    plan = []
    keep = project_files(folder)
    for entry in os.scandir(folder):
        if not entry.is_file(follow_symlinks=False):
            continue
        name = entry.name
        if name in keep or name.startswith('.'):
            continue
        if name.lower() in ('desktop.ini', 'thumbs.db') or name.lower().endswith(('.crdownload', '.part', '.tmp', '.lnk', '.url')):
            continue
        ext = os.path.splitext(name)[1].lower()
        group = next((g for g, exts in SORT_GROUPS.items() if ext in exts), None)
        if group:
            plan.append({'name': name, 'group': group, 'from': entry.path, 'to': str(folder / group / name)})
    return plan


def count_groups(items: list[dict]) -> dict[str, int]:
    groups: dict[str, int] = {}
    for item in items:
        groups[item['group']] = groups.get(item['group'], 0) + 1
    return groups


@app.post('/hub/files/sort')
def files_sort(body: dict = Depends(body_of)):
    folder = resolve_sort_target(str(body.get('text') or 'загрузки'))
    if not folder or not folder.is_dir():
        return {'ok': False, 'error': 'Не понял, какую папку разобрать. Скажите «разбери загрузки» или «разбери рабочий стол».'}
    reason = forbidden_reason(folder)
    if reason:
        return {'ok': False, 'error': reason}
    if is_program_folder(folder):
        return {'ok': False, 'error': f'«{folder.name}» похожа на папку программы или игры: рядом с exe лежат её библиотеки. '
                                      'Если разложить эти файлы, программа перестанет запускаться — не трогаю.'}
    plan = sort_plan(folder)
    if not body.get('apply'):
        return {'ok': True, 'folder': str(folder), 'count': len(plan), 'groups': count_groups(plan),
                'sample': [p['name'] for p in plan[:8]]}

    moved, failed = [], 0
    for item in plan:
        target = Path(item['to'])
        target.parent.mkdir(exist_ok=True)
        stem, ext = os.path.splitext(target.name)
        n = 2
        while target.exists():
            target = target.with_name(f'{stem} ({n}){ext}')
            n += 1
        try:
            shutil.move(item['from'], target)
            moved.append({'from': item['from'], 'to': str(target), 'group': item['group']})
        except OSError:
            failed += 1
    UNDO_FILE.write_text(json.dumps({'folder': str(folder), 'moved': moved, 'ts': time.time()},
                                    ensure_ascii=False), encoding='utf-8')
    log(f'файлы: разложено {len(moved)} в {folder}')
    return {'ok': True, 'folder': str(folder), 'moved': len(moved), 'failed': failed, 'groups': count_groups(moved)}


@app.post('/hub/files/undo')
def files_undo():
    try:
        undo = json.loads(UNDO_FILE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'ok': False, 'error': 'Отменять нечего — последний разбор уже отменён или его не было.'}
    back = 0
    for item in reversed(undo['moved']):
        try:
            if Path(item['to']).exists() and not Path(item['from']).exists():
                shutil.move(item['to'], item['from'])
                back += 1
        except OSError:
            pass
    for group in {Path(i['to']).parent for i in undo['moved']}:
        try:
            group.rmdir()            # только пустые: чужие файлы не трогаем
        except OSError:
            pass
    UNDO_FILE.unlink(missing_ok=True)
    return {'ok': True, 'restored': back, 'folder': undo['folder']}


# ================================================================== видео ===
#
# «Включи фильм про Бэтмена»: ищем на Rutube, выбираем лучший вариант и
# открываем. Если там пусто — открываем поиск VK Видео с тем же запросом.
#
# «Лучший» — это не первый в выдаче. Сначала отбрасываем не то: ролики без
# темы в названии, трейлеры и обзоры (для фильма нужно от часа), платные,
# скрытые и трансляции. Из оставшихся у пяти самых популярных смотрим
# настоящее разрешение — Rutube отдаёт его в адресе потока, — и побеждает
# самое высокое; при равенстве — официальный канал и число просмотров.

import math  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

VIDEO_KINDS = {
    'фильм': ('фильм', 60), 'кино': ('фильм', 60), 'сериал': ('сериал', 15),
    'мультфильм': ('мультфильм', 20), 'мультик': ('мультфильм', 5),
    'видео': ('', 0), 'ролик': ('', 0), 'клип': ('клип', 0),
}
VIDEO_PHRASE = re.compile(
    r'^(?:пожалуйста\s+)?(?:открой\s+и\s+)?(?:включи|поставь|запусти|найди|покажи|открой)\s+(?:мне\s+)?'
    r'(?:какой-нибудь\s+|какое-нибудь\s+)?(фильм|кино|сериал|мультфильм|мультик|видео|ролик|клип)\w*\s*(?:про\s+|о\s+|с\s+)?(.*)$',
    re.I)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Cloud HDR'}


def open_url(url: str) -> None:
    """Открыть адрес в браузере. CLOUDHDR_HUB_NO_OPEN=1 — режим проверки: ничего не открывать."""
    if os.environ.get('CLOUDHDR_HUB_NO_OPEN') == '1':
        log(f'(проверка) открыл бы: {url}')
        return
    os.startfile(url)  # noqa: S606 — адрес собран здесь же, только Rutube и VK


def http_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def stems(text: str) -> list[str]:
    """Основы слов темы: «бэтмена» → «бэтме», чтобы находить любой падеж."""
    words = [w for w in re.findall(r'[\wё]+', text.lower()) if len(w) > 2 and w not in ('про', 'для', 'что', 'как')]
    return [w[:max(4, len(w) - 2)] for w in words]


def max_height(video_id: str) -> int:
    """Настоящее разрешение ролика — из адреса его потока (…_1920x800_…)."""
    try:
        options = http_json(f'https://rutube.ru/api/play/options/{video_id}/?no_404=true', 8)
        url = (options.get('video_balancer') or {}).get('m3u8') or ''
        sizes = [(int(w), int(h)) for w, h in re.findall(r'_(\d{3,4})x(\d{3,4})_', url)]
        # Высоту считаем по ширине: у широкоформатного кино «1920x800» — это
        # Full HD, хотя строк и 800.
        return max((round(w * 9 / 16) for w, _ in sizes), default=0)
    except Exception:  # noqa: BLE001
        return 0


def quality_label(height: int) -> str:
    return '4K' if height >= 2000 else '1440p' if height >= 1400 else '1080p' if height >= 1000 else \
           '720p' if height >= 700 else f'{height}p' if height else 'качество неизвестно'


def find_video(text: str) -> dict:
    match = VIDEO_PHRASE.match(text.strip())
    kind_word, topic = (match.group(1).lower(), match.group(2).strip(' .!?')) if match else ('видео', text)
    kind, min_minutes = VIDEO_KINDS.get(kind_word, ('', 0))
    topic = re.sub(r'\s+(на|в)\s+(рутуб\w*|вк\w*|вконтакте)$', '', topic, flags=re.I).strip()
    if not topic:
        return {'ok': False, 'error': 'Скажите, что включить: «включи фильм про Бэтмена», «включи сериал Друзья».'}
    query = f'{kind} {topic}'.strip()
    keys = stems(topic)

    candidates = []
    series_start = kind == 'сериал' and not re.search(r'сезон|сери|\d', topic, re.I)
    # Для сериала «с начала» ищем и отдельно первую серию: в общей выдаче её
    # часто нет — там самые популярные, а не первые.
    queries = [query] + ([f'{topic} 1 сезон 1 серия'] if series_start else [])
    try:
        found = {'results': []}
        for q in queries:
            found['results'] += http_json('https://rutube.ru/api/search/video/?query=' + urllib.parse.quote(q)).get('results') or []
        seen = set()
        for item in found.get('results') or []:
            if item.get('id') in seen:
                continue
            seen.add(item.get('id'))
            if item.get('is_hidden') or item.get('is_deleted') or item.get('is_paid') or item.get('is_livestream') \
                    or item.get('is_audio') or item.get('is_adult'):
                continue
            minutes = (item.get('duration') or 0) / 60
            title = str(item.get('title') or '')
            if minutes < min_minutes:
                continue
            if keys and not all(key in title.lower() for key in keys):
                continue
            if kind == 'фильм' and re.search(r'трейлер|обзор|разбор|тизер|реакция|нарезк|все смерти|пересказ', title, re.I):
                continue
            candidates.append({
                'id': item['id'], 'title': title, 'minutes': int(minutes), 'hits': item.get('hits') or 0,
                'author': (item.get('author') or {}).get('name') or '', 'official': bool(item.get('is_official')),
                'url': item.get('video_url') or f"https://rutube.ru/video/{item['id']}/",
                'thumb': item.get('thumbnail_url') or '',
            })
    except Exception as error:  # noqa: BLE001
        log(f'видео: Rutube не ответил: {error}')

    vk_url = 'https://vkvideo.ru/?q=' + urllib.parse.quote(query)
    if not candidates:
        open_url(vk_url)
        return {'ok': True, 'source': 'vk', 'url': vk_url, 'query': query,
                'title': f'Поиск VK Видео: «{query}»'}

    # Сериал без номера сезона и серии начинаем с начала: «включи Друзей»
    # значит первую серию, а не ту, что популярнее. Названный номер —
    # «3 сезон 5 серия» — уже часть темы и проверяется наравне с остальным.
    def first_episode(c: dict) -> bool:
        # Сезон и серию разбираем по отдельности: «2 сезон 1 серия» содержит
        # «1 серия», и простой поиск подстроки принимал её за начало сериала.
        title = c['title'].lower()
        for pattern in (r'(\d+)\s*сезон\W+(\d+)\s*сери', r'сезон\s*(\d+)\W+сери[яи]\s*(\d+)', r's(\d+)\s*e(\d+)'):
            found = re.search(pattern, title)
            if found:
                return found.group(1) == '1' and found.group(2) == '1'
        return False

    candidates.sort(key=lambda c: (series_start and first_episode(c), c['official'], c['hits']), reverse=True)
    top = candidates[:5]
    for c in top:
        c['height'] = max_height(c['id'])
        c['quality'] = quality_label(c['height'])
    top.sort(key=lambda c: (series_start and first_episode(c), c['height'] >= 1000, c['height'],
                            c['official'], math.log10(c['hits'] + 10)), reverse=True)
    best = top[0]
    open_url(best['url'])
    log(f"видео: «{query}» → {best['title']} ({best['quality']}, {best['minutes']} мин)")
    return {'ok': True, 'source': 'rutube', 'query': query, **best, 'vk': vk_url,
            'alternatives': [{k: c[k] for k in ('title', 'url', 'quality', 'minutes', 'author')} for c in top[1:4]]}


@app.post('/hub/video/play')
def video_play(body: dict = Depends(body_of)):
    text = str(body.get('text') or '')
    try:
        return find_video(text)
    except Exception as error:  # noqa: BLE001
        return {'ok': False, 'error': f'Не получилось найти видео: {error}'}


@app.post('/hub/video/open')
def video_open(body: dict = Depends(body_of)):
    """Открыть одну из альтернатив — только адреса Rutube и VK Видео."""
    url = str(body.get('url') or '')
    if not re.match(r'^https://(rutube\.ru|vkvideo\.ru|vk\.com)/', url):
        return {'ok': False, 'error': 'Открываю только Rutube и VK Видео.'}
    open_url(url)
    return {'ok': True}


# ================================================================ система ===
#
# Железо и настройки Windows по просьбе: радио (Wi-Fi, Bluetooth), тема,
# режим питания, яркость (где Windows её даёт), сведения о компьютере и
# питание. Сон, перезагрузка и выключение сами по себе не выполняются:
# окно приложения сначала показывает карточку с подтверждением и шлёт сюда
# confirm=true. Выключение и перезагрузка идут с отсчётом 30 с — отменяются
# фразой «отмена выключения» (shutdown /a).

RADIO_PS = r'''
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); [void]$t.Wait(8000); $t.Result }
[void][Windows.Devices.Radios.Radio, Windows.System.Devices, ContentType = WindowsRuntime]
[void](Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]))
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$radio = $radios | Where-Object { "$($_.Kind)" -eq '__KIND__' } | Select-Object -First 1
if (-not $radio) { 'NONE'; exit }
if ('__STATE__' -ne 'Query') { [void](Await ($radio.SetStateAsync([Windows.Devices.Radios.RadioState]::__STATE__)) ([Windows.Devices.Radios.RadioAccessStatus])) }
"$($radio.State)"
'''

POWER_SCHEMES = {'экономи': 'a1841308-3541-4fab-bc81-f71556f20b4a', 'сбаланс': '381b4222-f694-41f0-9685-ff5bb260df2e',
                 'обычн': '381b4222-f694-41f0-9685-ff5bb260df2e', 'производ': '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c',
                 'максимал': '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c', 'мощн': '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c'}


def radio(kind: str, state: str) -> str:
    """Переключить радио (On/Off) и вернуть его состояние: On, Off или NONE — адаптера нет."""
    lines = powershell(RADIO_PS.replace('__KIND__', kind).replace('__STATE__', state), 30).strip().splitlines()
    return lines[-1].strip() if lines else ''


def set_theme(dark: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
                        0, winreg.KEY_SET_VALUE) as key:
        for name in ('AppsUseLightTheme', 'SystemUsesLightTheme'):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 0 if dark else 1)


def gb(n: float) -> str:
    return f'{n / 1024 ** 3:.0f} ГБ' if n >= 10 * 1024 ** 3 else f'{n / 1024 ** 3:.1f} ГБ'.replace('.', ',')


def system_info(topic: str) -> str:
    if topic == 'disk':
        lines = []
        for part in psutil.disk_partitions():
            if 'cdrom' in part.opts or not part.fstype:
                continue
            try:
                use = psutil.disk_usage(part.mountpoint)
            except OSError:
                continue
            lines.append(f'{part.mountpoint.rstrip(chr(92))} — свободно {gb(use.free)} из {gb(use.total)} ({100 - use.percent:.0f}%)')
        return 'Место на дисках:\n' + '\n'.join(lines)
    if topic == 'cpu':
        procs = list(psutil.process_iter(['name']))
        for p in procs:
            try:
                p.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(1.2)
        cores = psutil.cpu_count() or 1
        # По имени, а не по процессу: у Chrome их десятки, и список из пяти
        # строк «chrome — 2%» ничего не объяснял.
        by_name: dict[str, float] = {}
        for p in procs:
            try:
                value = p.cpu_percent(None) / cores
                if p.info['name'] not in ('System Idle Process', 'Idle'):
                    name = friendly(p.info['name'] or '')
                    by_name[name] = by_name.get(name, 0.0) + value
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        load = sorted(((v, n) for n, v in by_name.items() if v >= 0.5), reverse=True)
        total = psutil.cpu_percent(0.3)
        top = '\n'.join(f'{name} — {value:.0f}%' for value, name in load[:5]) or 'ничего заметного'
        return f'Процессор загружен на {total:.0f}%. Больше всего сейчас:\n{top}'
    if topic == 'ram':
        mem = psutil.virtual_memory()
        by_name: dict[str, int] = {}
        for p in psutil.process_iter(['name', 'memory_info']):
            if p.info.get('memory_info'):
                name = friendly(p.info['name'] or '')
                by_name[name] = by_name.get(name, 0) + p.info['memory_info'].rss
        top = sorted(((rss, name) for name, rss in by_name.items()), reverse=True)[:5]
        names = '\n'.join(f'{n} — {gb(r)}' for r, n in top)
        return f'Память: занято {gb(mem.used)} из {gb(mem.total)} ({mem.percent:.0f}%). Больше всего берут:\n{names}'
    if topic == 'temp':
        raw = subprocess.run(['nvidia-smi', '--query-gpu=name,temperature.gpu,utilization.gpu', '--format=csv,noheader'],
                             capture_output=True, text=True, creationflags=NO_WINDOW).stdout.strip()
        if raw:
            name, temp, util = [x.strip() for x in raw.split(',')]
            gpu = f'Видеокарта {name}: {temp} °C, загрузка {util}.'
        else:
            gpu = 'Температуру видеокарты узнать не удалось.'
        return gpu + ' Температуру процессора Windows обычным программам не отдаёт — её показывает GIGABYTE Control Center.'
    if topic == 'overview':
        return overview()
    if topic == 'battery':
        battery = psutil.sensors_battery()
        if not battery:
            return 'Батареи нет — это настольный компьютер, он работает от сети.'
        left = '' if battery.power_plugged or battery.secsleft < 0 else f', хватит примерно на {battery.secsleft // 3600} ч {battery.secsleft % 3600 // 60} мин'
        return f'Заряд {battery.percent:.0f}%' + (' — на зарядке' if battery.power_plugged else left) + '.'
    return ''


def overview() -> str:
    """«Как там пк?» — сводка одним абзацем: процессор, память, видеокарта, диск C.
    Раньше такой вопрос не узнавал ни один шаблон, и он доставался модели,
    которая в компьютер заглянуть не может — отвечала «не знаю» или выдумывала."""
    cpu = psutil.cpu_percent(0.5)
    mem = psutil.virtual_memory()
    parts = [f'процессор — {cpu:.0f}%', f'память — {gb(mem.used)} из {gb(mem.total)} ({mem.percent:.0f}%)']
    hot = cpu > 85 or mem.percent > 90
    try:
        raw = subprocess.run(['nvidia-smi', '--query-gpu=name,temperature.gpu,utilization.gpu', '--format=csv,noheader'],
                             capture_output=True, text=True, creationflags=NO_WINDOW, timeout=5).stdout.strip()
        name, temp, util = [x.strip() for x in raw.splitlines()[0].split(',')]
        parts.append(f'видеокарта {name.replace("NVIDIA GeForce ", "")} — {temp} °C, загрузка {util.replace(" ", "")}')
        hot = hot or int(temp) >= 83
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        pass
    try:
        system_drive = os.environ.get('SystemDrive', 'C:') + '\\'
        free = psutil.disk_usage(system_drive).free
        parts.append(f'на диске {system_drive[0]} свободно {gb(free)}')
        hot = hot or free < 10 * 1024 ** 3
    except OSError:
        pass
    head = 'Компьютер под нагрузкой' if hot else 'Всё спокойно'
    return f'{head}: ' + ', '.join(parts) + '.'


# Слова, по которым понятно, что речь о самом компьютере, а не о погоде,
# телефоне или игре: «температура» бывает и на улице, «заряд» — у телефона.
PC = r'(компьютер|комп\b|пк\b|систем|windows|виндовс|ноутбук|ноут\b)'
CPU = r'(процессор|цп\b|cpu)'
PCW = r'(компьютер\w*|комп\w*|пк|ноутбук\w*|ноут\w*|систем\w*|железо|железк\w*)'
DISK = r'(диск|ssd|hdd|накопител|\b[a-z]:)'

SYS_PATTERNS = [
    # (регулярное выражение, действие) — порядок важен: сначала отмена.
    (r'отмен\w*\s+(выключени|перезагрузк)|не\s+(выключай|перезагружай)', 'abort'),
    (r'(выключи|отключи)\s+(мне\s+)?' + PC + r'|заверши\s+работу\s*(windows|' + PC + r')?\s*$', 'shutdown'),
    # «перезагрузи браузер/страницу» — не компьютер: без слова о ПК только голое «перезагрузись».
    (r'перезагрузи\s+(мне\s+)?' + PC + r'|^перезагрузи(сь)?$|^перезагрузка$', 'restart'),
    (r'(отправь|переведи|усыпи)\s+' + PC + r'?.*\b(в\s+)?(сон|спящ)|^спящий\s+режим$|(включи|перейди\s+в)\s+спящий\s+режим|^усни$', 'sleep'),
    (r'(включи|отключи|выключи)\s+(вай[\s-]?фай|wi[\s-]?fi|беспроводн)', 'wifi'),
    (r'(включи|отключи|выключи)\s+(блютуз|bluetooth)', 'bluetooth'),
    (r'(включи|сделай|поставь)\s+(тёмн|темн)\w*\s+тем', 'dark'),
    (r'(включи|сделай|поставь)\s+светл\w*\s+тем', 'light'),
    (r'ночн\w*\s+(свет|режим)', 'nightlight'),
    # яркость экрана, но не «яркость фото в фотошопе»
    (r'^(?!.*(фото|картинк|изображени|видео|фотошоп|photoshop)).*яркост', 'brightness'),
    # «производительность» сама по себе не повод менять режим питания
    (r'(режим|схем)\w*\s+(питани|производительност|энергосбережени|экономи)|энергосбережени|экономи\w*\s+энерги'
     r'|(включи|поставь|сделай)\s+(\w+\s+)?(максимальн|высок)\w*\s+производительн', 'power'),
    (r'(мест\w*|свободн\w*|занят\w*)\s+(\w+\s+)?(на|в)\s+(\w+\s+)?' + DISK + r'|' + DISK + r'\w*\s+(\w+\s+)?(заполнен|забит|переполнен)'
     r'|(сколько|много ли)\s+(\w+\s+)?(свободн\w*\s+)?мест\w*\s+(на|в)\s+(\w+\s+)?(' + DISK[1:-1] + r'|компьютер|пк\b|ноутбук)'
     r'|мест\w*\s+на\s+' + PC, 'disk'),
    # общее «как там пк» — целиком фраза, чтобы «как там пк открыть папку» сюда не попало
    (r'^(ну\s+|а\s+)*(как\s+(там\s+|дела\s+(у|с)\s+|поживает\s+|себя\s+чувствует\s+|сейчас\s+|ты\s+там\s+)?(мой\s+|наш\s+)?' + PCW +
     r'|что\s+(там\s+)?(с|у)\s+(моим\s+|моего\s+)?' + PCW + r'|(в\s+каком\s+)?состояни\w*\s+(моего\s+)?' + PCW +
     r'|(все|всё)\s+(ли\s+)?(нормально|хорошо|ок|окей|в\s+порядке)\s+(с|у)\s+(моим\s+|моего\s+)?' + PCW + r')(\s+(сейчас|сегодня|вообще))?$',
     'overview'),
    (r'(загруж\w*|загрузк\w*|нагрузк\w*|нагружен\w*)\s+(на\s+)?' + CPU + r'|' + CPU + r'\w*\s+(\w+\s+)?(загруж|нагруж|занят)'
     r'|(что|кто)\s+(\w+\s+)?(грузит|нагружает|жр[её]т|ест|тормозит)\s+(мой\s+)?(' + CPU[1:-1] + r'|' + PC[1:-1] + r')'
     r'|почему\s+(\w+\s+)?(тормозит|тупит|лагает|виснет)\s+(мой\s+)?' + PC + r'|' + PC + r'\w*\s+(тормозит|тупит|лагает)', 'cpu'),
    (r'оперативк|\bозу\b|\bram\b|(сколько|много ли)\s+(\w+\s+)?памят\w*\s*(свободн|занят|осталось|$)'
     r'|памят\w*\s+(занят|свободн|осталось)|(свободн|занят)\w*\s+памят|(кто|что)\s+(\w+\s+)?(ест|жр[её]т|занимает)\s+(\w+\s+)?памят', 'ram'),
    (r'температур\w*\s+(\w+\s+)?(видеокарт|процессор|цп\b|gpu|cpu|железа|' + PC[1:-1] + r')'
     r'|(греется|перегрева\w*|нагрелс\w*|горячий)\s+(ли\s+)?(мой\s+)?(' + PC[1:-1] + r'|видеокарт|процессор)'
     r'|(' + PC[1:-1] + r'|видеокарт\w*|процессор\w*)\s+(не\s+)?(греется|перегрева)', 'temp'),
    (r'(сколько|какой|уровень|процент)\s+(\w+\s+)?заряд\w*\s*(батаре|аккумулятор|ноутбук|осталось|$)'
     r'|заряд\w*\s+(батаре|аккумулятор|ноутбук)|^(заряд|батарея)\??$|(батаре|аккумулятор)\w*\s+(\w+\s+)?(хватит|осталось|сел)', 'battery'),
]


def match_system(text: str) -> str:
    low = text.lower().strip().rstrip('.!?').replace('ё', 'е')
    for pattern, action in SYS_PATTERNS:
        if re.search(pattern.replace('ё', 'е'), low):
            return action
    return ''


@app.post('/hub/sys')
def sys_action(body: dict = Depends(body_of)):
    text = str(body.get('text') or '')
    action = str(body.get('action') or match_system(text))
    low = text.lower()
    off = bool(re.search(r'\b(отключи|выключи)\b', low))
    if not action:
        return {'ok': False, 'known': False}

    # --- питание: только с подтверждением ---------------------------------
    if action in ('shutdown', 'restart', 'sleep'):
        if not body.get('confirm'):
            titles = {'shutdown': 'Выключить компьютер?', 'restart': 'Перезагрузить компьютер?', 'sleep': 'Отправить компьютер в сон?'}
            notes = {'shutdown': 'Через 30 секунд после подтверждения. Сохраните открытые файлы. Передумаете — скажите «отмена выключения».',
                     'restart': 'Через 30 секунд после подтверждения. Сохраните открытые файлы. Передумаете — скажите «отмена перезагрузки».',
                     'sleep': 'Сразу после подтверждения. Разбудить — любой клавишей или мышью.'}
            return {'ok': True, 'known': True, 'confirm': True, 'action': action, 'title': titles[action], 'say': notes[action]}
        if action == 'sleep':
            threading.Timer(1.5, lambda: subprocess.run(
                ['powershell', '-NoProfile', '-Command', 'Add-Type -AssemblyName System.Windows.Forms;'
                 '[System.Windows.Forms.Application]::SetSuspendState("Suspend", $false, $false)'],
                creationflags=NO_WINDOW)).start()
            return {'ok': True, 'known': True, 'say': 'Засыпаю. До встречи!'}
        flag = '/s' if action == 'shutdown' else '/r'
        subprocess.run(['shutdown', flag, '/t', '30', '/c', 'Cloud HDR: по вашей просьбе'], creationflags=NO_WINDOW)
        log(f'система: {action} через 30 с')
        word = 'Выключаю' if action == 'shutdown' else 'Перезагружаю'
        return {'ok': True, 'known': True, 'say': f'{word} компьютер через 30 секунд. Передумали — скажите «отмена».'}
    if action == 'abort':
        done = subprocess.run(['shutdown', '/a'], capture_output=True, creationflags=NO_WINDOW)
        return {'ok': True, 'known': True, 'say': 'Отменил — компьютер продолжает работать.' if done.returncode == 0
                else 'Отменять нечего: выключение не было запланировано.'}

    # --- радио -------------------------------------------------------------
    if action in ('wifi', 'bluetooth'):
        kind, label = ('WiFi', 'Wi‑Fi') if action == 'wifi' else ('Bluetooth', 'Bluetooth')
        state = radio(kind, 'Off' if off else 'On')
        if state == 'NONE':
            return {'ok': True, 'known': True, 'say': f'{label}-адаптера в этом компьютере нет.'}
        now_on = state == 'On'
        if now_on == (not off):
            return {'ok': True, 'known': True, 'say': f'{label} {"включён" if now_on else "выключен"}.'}
        return {'ok': False, 'known': True, 'say': f'Windows не дала переключить {label}. Попробуйте в центре уведомлений (Win+A).'}

    # --- тема, ночной свет, яркость, питание -------------------------------
    if action in ('dark', 'light'):
        set_theme(action == 'dark')
        return {'ok': True, 'known': True, 'say': 'Включил тёмную тему Windows.' if action == 'dark' else 'Включил светлую тему Windows.'}
    if action == 'nightlight':
        os.startfile('ms-settings:nightlight')  # noqa: S606
        return {'ok': True, 'known': True, 'say': 'Открыл настройки ночного света — переключатель вверху. Сам Windows его менять не даёт.'}
    if action == 'brightness':
        level = re.search(r'(\d{1,3})\s*%?', low)
        if level is None:
            level_value = 30 if re.search(r'темн|меньш|убав|пониз', low) else 80 if re.search(r'ярч|больш|приба|повыс', low) else None
        else:
            level_value = max(0, min(100, int(level.group(1))))
        if level_value is not None:
            out = powershell(f'try {{ (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop)'
                             f' | Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{{Timeout=0;Brightness={level_value}}} | Out-Null; "ok" }} catch {{ "no" }}', 20).strip()
            if out.endswith('ok'):
                return {'ok': True, 'known': True, 'say': f'Яркость — {level_value}%.'}
        os.startfile('ms-settings:display')  # noqa: S606
        return {'ok': True, 'known': True, 'say': 'У внешнего монитора Windows не даёт менять яркость программно — открыл настройки экрана; '
                                                    'у самого монитора она меняется кнопками на корпусе.'}
    if action == 'power':
        scheme = next((guid for key, guid in POWER_SCHEMES.items() if key in low), None)
        if not scheme:
            # powercfg отвечает в кодировке консоли (cp866), а не в UTF-8.
            active = subprocess.run(['powercfg', '/getactivescheme'], capture_output=True,
                                    creationflags=NO_WINDOW).stdout.decode('cp866', 'replace')
            name = re.search(r'\((.+)\)', active)
            return {'ok': True, 'known': True, 'say': f'Сейчас режим питания «{name.group(1) if name else "неизвестный"}». '
                                                        'Скажите «режим питания экономия», «сбалансированный» или «максимальная производительность».'}
        done = subprocess.run(['powercfg', '/setactive', scheme], capture_output=True, creationflags=NO_WINDOW)
        names = {'a1841308-3541-4fab-bc81-f71556f20b4a': 'экономия энергии', '381b4222-f694-41f0-9685-ff5bb260df2e': 'сбалансированный',
                 '8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c': 'высокая производительность'}
        return {'ok': done.returncode == 0, 'known': True,
                'say': f'Режим питания — {names[scheme]}.' if done.returncode == 0 else 'Windows не дала сменить режим питания.'}

    # --- сведения ------------------------------------------------------------
    answer = system_info(action)
    if answer:
        return {'ok': True, 'known': True, 'say': answer}
    return {'ok': False, 'known': False}


# ================================================================= защита ===

import psutil  # noqa: E402 — нужен только проверке

MINER_NAMES = {'xmrig', 'xmr-stak', 'nicehash', 'nhm', 'ethminer', 't-rex', 'trex', 'nbminer', 'lolminer',
               'phoenixminer', 'gminer', 'cpuminer', 'minerd', 'ccminer', 'teamredminer', 'srbminer', 'bminer',
               'excavator'}
MINER_ARGS = re.compile(r'stratum\+(tcp|ssl)|--donate-level|nanopool|2miners|minexmr|supportxmr|hashvault', re.I)
MINER_PORTS = {3333, 4444, 5555, 7777, 14444, 14433, 45700, 3357}
SPY_NAMES = re.compile(r'keylog|stealer|remcos|njrat|darkcomet|quasar|asyncrat|nanocore|agenttesla|redline|lumma|raccoon|vidar', re.I)
DOUBLE_EXT = re.compile(r'\.(pdf|docx?|xlsx?|pptx?|jpe?g|png|txt|mp3|mp4|avi|zip|rar)\s*\.(exe|scr|com|pif|bat|cmd|vbs|js|jse|hta|wsf)$', re.I)
RISKY_EXT = {'.exe', '.scr', '.com', '.pif', '.bat', '.cmd', '.vbs', '.vbe', '.js', '.jse', '.hta', '.wsf', '.ps1', '.msi', '.jar'}
BIG_DOMAINS = ('google', 'yandex', 'vk.com', 'mail.ru', 'microsoft', 'windowsupdate', 'apple', 'sber', 'tinkoff',
               'tbank', 'gosuslugi', 'telegram', 'steam', 'paypal', 'ozon', 'wildberries', 'avito', 'kaspersky',
               'drweb', 'eset', 'virustotal', 'youtube', 'github')
CATEGORIES = [('virus', 'Вирусы и трояны'), ('spy', 'Шпионские программы'), ('miner', 'Майнеры и скрытые процессы'),
              ('autorun', 'Автозагрузка'), ('phish', 'Фишинговые ссылки'), ('net', 'Сетевые подключения')]


def user_writable(path: str) -> bool:
    low = path.lower()
    return any(part in low for part in ('\\appdata\\', '\\temp\\', '\\downloads\\', '\\desktop\\',
                                        '\\users\\public\\', '\\$recycle.bin\\'))


def in_temp(path: str) -> bool:
    return '\\temp\\' in path.lower()


class Scanner:
    """
    Проверка по шести категориям из презентации. Находки двух уровней:
    threat — почти наверняка вред; warn — подозрительно, решает человек.
    Ничего не удаляется и не перемещается само: только по кнопке.
    """

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.state = 'idle'
        self.progress = 0.0
        self.files = 0
        self.current = ''
        self.stage = ''
        self.defender: dict = {}
        self.summary = ''
        self.started = self.finished = 0.0
        self.signatures: dict[str, str] = {}
        self.categories = {key: {'id': key, 'title': title, 'state': 'wait', 'note': '', 'items': []}
                           for key, title in CATEGORIES}

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps({
                'state': self.state, 'progress': round(self.progress, 3), 'files': self.files,
                'current': self.current, 'stage': self.stage, 'defender': self.defender,
                'categories': [self.categories[k] for k, _ in CATEGORIES], 'summary': self.summary,
                'started': self.started, 'finished': self.finished}, default=str))

    def start(self) -> bool:
        with self.lock:
            if self.state == 'running':
                return False
        self.reset()
        with self.lock:
            self.state = 'running'
            self.started = time.time()
        threading.Thread(target=self.run, daemon=True).start()
        return True

    # --- помощники -----------------------------------------------------------

    def begin(self, key: str) -> None:
        with self.lock:
            self.categories[key]['state'] = 'run'
            self.stage = self.categories[key]['title']

    def finish(self, key: str, note: str) -> None:
        with self.lock:
            category = self.categories[key]
            levels = {item['level'] for item in category['items']}
            category['state'] = 'bad' if 'threat' in levels else ('warn' if 'warn' in levels else 'ok')
            category['note'] = note

    def add(self, key: str, level: str, title: str, detail: str, path: str = '', kind: str = '', **extra) -> None:
        item = {'id': hashlib.sha1(f'{key}|{title}|{path}'.encode()).hexdigest()[:12], 'level': level,
                'title': title, 'detail': detail, 'path': path, 'kind': kind, **extra}
        with self.lock:
            if not any(i['id'] == item['id'] for i in self.categories[key]['items']):
                self.categories[key]['items'].append(item)

    def step(self, value: float) -> None:
        with self.lock:
            self.progress = max(self.progress, min(1.0, value))

    def check_signatures(self, paths: list[str]) -> None:
        todo = [p for p in dict.fromkeys(paths) if p and p not in self.signatures and os.path.isfile(p)][:500]
        if not todo:
            return
        listing = DATA / 'sig-list.txt'
        listing.write_text('\n'.join(todo), encoding='utf-8')
        out = powershell(
            f"Get-Content -LiteralPath '{listing}' -Encoding UTF8 | ForEach-Object {{ "
            "try { $s = Get-AuthenticodeSignature -LiteralPath $_ -ErrorAction Stop; '{0}|{1}' -f $s.Status, $_ } "
            "catch { 'Error|' + $_ } }", timeout=300)
        for line in out.splitlines():
            status, _, path = line.strip().partition('|')
            if path:
                self.signatures[path] = status

    def signed(self, path: str) -> bool:
        return self.signatures.get(path) == 'Valid'

    # --- ход проверки --------------------------------------------------------

    def run(self) -> None:
        try:
            self.scan_virus()
            self.scan_processes()
            self.scan_autorun()
            self.scan_phish()
            self.scan_net()
            self.summarize()
        except Exception as error:  # noqa: BLE001
            log(f'защита: сбой {error!r}')
            with self.lock:
                self.summary = f'Проверка прервалась: {error}'
        finally:
            with self.lock:
                self.state = 'done'
                self.progress = 1.0
                self.finished = time.time()
                self.current = ''
                self.stage = ''
            STATE['lastScan'] = {'ts': time.time(), 'summary': self.summary}
            save_state(STATE)

    def roots(self) -> list[tuple[Path, int]]:
        home = Path.home()
        roots = [(known_folder('Downloads'), 3), (known_folder('Desktop'), 2), (known_folder('Documents'), 3),
                 (home / 'AppData' / 'Roaming', 3), (home / 'AppData' / 'Local' / 'Temp', 2),
                 (Path('C:/Users/Public'), 3), (Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Temp', 2)]
        return [(root, depth) for root, depth in roots if root.exists()]

    def scan_virus(self) -> None:
        self.begin('virus')
        raw = powershell('try { Get-MpComputerStatus | Select-Object AMServiceEnabled,AntivirusEnabled,'
                         'RealTimeProtectionEnabled,AntivirusSignatureLastUpdated | ConvertTo-Json -Compress } catch { "{}" }', 60)
        try:
            defender = json.loads(raw or '{}')
        except ValueError:
            defender = {}
        with self.lock:
            self.defender = defender
        if defender and not defender.get('RealTimeProtectionEnabled'):
            self.add('virus', 'warn', 'Защита Windows в реальном времени выключена',
                     'Защитник не проверяет файлы при открытии. Включить: Пуск → «Защитник Windows - включить».',
                     kind='info')
        raw = powershell('try { Get-MpThreatDetection | Select-Object ThreatID,Resources,ActionSuccess | '
                         'ConvertTo-Json -Compress } catch { "" }', 60)
        try:
            found = json.loads(raw) if raw.strip() else []
            found = found if isinstance(found, list) else [found]
        except ValueError:
            found = []
        for threat in found:
            resources = threat.get('Resources') or []
            resources = resources if isinstance(resources, list) else [resources]
            for resource in resources:
                if isinstance(resource, str) and resource.lower().startswith('file:_'):
                    path = resource.split('_', 1)[1]
                    if os.path.exists(path):
                        self.add('virus', 'threat', f'Угроза, найденная Защитником: {os.path.basename(path)}',
                                 'Защитник Windows пометил этот файл, но он всё ещё на диске.', path, 'file')
        self.step(0.05)

        risky: list[str] = []
        roots = self.roots()
        skip = {'node_modules', '.git', 'cloud hdr', 'packages', 'windows defender', '__pycache__'}
        for index, (root, depth) in enumerate(roots):
            base = len(root.parts)
            for folder, dirs, names in os.walk(root, onerror=lambda e: None):
                if len(Path(folder).parts) - base >= depth:
                    dirs[:] = []
                dirs[:] = [d for d in dirs if d.lower() not in skip]
                for name in names:
                    full = os.path.join(folder, name)
                    with self.lock:
                        self.files += 1
                        if self.files % 50 == 0:
                            self.current = full
                    if DOUBLE_EXT.search(name):
                        self.add('virus', 'threat', f'Файл притворяется документом: {name}',
                                 'Двойное расширение — классическая маскировка вируса: выглядит как документ, '
                                 'а запускается как программа.', full, 'file')
                    elif os.path.splitext(name)[1].lower() in RISKY_EXT:
                        risky.append(full)
            self.step(0.05 + 0.35 * (index + 1) / len(roots))
        with self.lock:
            self.stage = 'Проверяю цифровые подписи'
        exes = [p for p in risky if p.lower().endswith(('.exe', '.scr', '.com', '.pif'))]
        self.check_signatures([p for p in exes if in_temp(p) or '\\appdata\\roaming\\' in p.lower()])
        for path in exes:
            low = path.lower()
            if low.endswith(('.scr', '.pif', '.com')) and ('\\downloads\\' in low or in_temp(path)):
                self.add('virus', 'warn', f'Подозрительный тип файла: {os.path.basename(path)}',
                         'Такие файлы почти не встречаются в обычной работе и часто оказываются вредоносными.',
                         path, 'file')
            elif (in_temp(path) or os.path.dirname(low).endswith('\\appdata\\roaming')) and path in self.signatures \
                    and not self.signed(path):
                self.add('virus', 'warn', f'Неподписанная программа: {os.path.basename(path)}',
                         'Лежит там, где обычно прячутся вредоносные файлы, и не имеет цифровой подписи. '
                         'Если вы её не устанавливали — отправьте в карантин.', path, 'file')
        self.step(0.45)
        self.finish('virus', 'Защитник Windows: ' + ('включён' if defender.get('RealTimeProtectionEnabled')
                                                      else 'выключен или недоступен'))

    def scan_processes(self) -> None:
        self.begin('spy')
        procs = {}
        for proc in psutil.process_iter(['pid', 'name', 'exe', 'cmdline']):
            try:
                proc.cpu_percent(None)
                procs[proc.pid] = proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        self.check_signatures([p.info['exe'] for p in procs.values() if p.info.get('exe') and user_writable(p.info['exe'])])
        for proc in procs.values():
            exe, name = proc.info.get('exe') or '', proc.info.get('name') or ''
            if not exe:
                continue
            with self.lock:
                self.current = exe
            if SPY_NAMES.search(name) and not self.signed(exe):
                self.add('spy', 'threat', f'Похоже на программу-шпиона: {name}',
                         'Имя процесса совпадает с известными шпионскими программами, подписи нет.',
                         exe, 'process', pid=proc.pid)
            elif in_temp(exe) and exe in self.signatures and not self.signed(exe):
                self.add('spy', 'warn', f'Неподписанный процесс из временной папки: {name}',
                         'Программы редко запускаются из Temp. Если вы её не узнаёте — завершите и отправьте в карантин.',
                         exe, 'process', pid=proc.pid)
        self.step(0.55)
        self.finish('spy', f'Процессов проверено: {len(procs)}')

        self.begin('miner')
        time.sleep(2.0)
        cores = psutil.cpu_count() or 1
        for proc in procs.values():
            try:
                load = proc.cpu_percent(None) / cores
                name = (proc.info.get('name') or '').lower().removesuffix('.exe')
                cmd = ' '.join(proc.info.get('cmdline') or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            exe = proc.info.get('exe') or ''
            if name in MINER_NAMES or MINER_ARGS.search(cmd):
                self.add('miner', 'threat', f'{proc.info["name"]} — скрытый майнер',
                         f'Тайком добывает криптовалюту на вашем железе (нагрузка {load:.0f}%).',
                         exe, 'process', pid=proc.pid)
            elif load > 20 and exe and user_writable(exe) and exe in self.signatures and not self.signed(exe):
                self.add('miner', 'warn', f'{proc.info["name"]} сильно грузит процессор ({load:.0f}%)',
                         'Неподписанная программа из папки пользователя — так выглядят скрытые майнеры.',
                         exe, 'process', pid=proc.pid)
        self.step(0.65)
        self.finish('miner', f'Загрузка процессора сейчас: {psutil.cpu_percent(0.3):.0f}%')

    @staticmethod
    def command_exe(command: str) -> str:
        command = os.path.expandvars(command.strip())
        if command.startswith('"'):
            return command[1:].split('"', 1)[0]
        match = re.match(r'(.+?\.(exe|bat|cmd|vbs|js|ps1|scr|com))(\s|$)', command, re.I)
        return match.group(1) if match else command.split(' ')[0]

    def autoruns(self) -> list[dict]:
        entries = []
        keys = [(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Run', 'HKCU'),
                (winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\RunOnce', 'HKCU'),
                (winreg.HKEY_LOCAL_MACHINE, r'Software\Microsoft\Windows\CurrentVersion\Run', 'HKLM'),
                (winreg.HKEY_LOCAL_MACHINE, r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run', 'HKLM')]
        for hive, sub, label in keys:
            try:
                with winreg.OpenKey(hive, sub) as key:
                    index = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, index)
                        except OSError:
                            break
                        entries.append({'hive': label, 'sub': sub, 'name': name, 'command': str(value)})
                        index += 1
            except OSError:
                pass
        for folder in (Path(os.environ.get('APPDATA', '')) / r'Microsoft\Windows\Start Menu\Programs\Startup',
                       Path(os.environ.get('ProgramData', '')) / r'Microsoft\Windows\Start Menu\Programs\Startup'):
            if folder.is_dir():
                for item in folder.iterdir():
                    if item.name.lower() != 'desktop.ini':
                        entries.append({'name': item.name, 'command': str(item), 'file': str(item)})
        return entries

    def scan_autorun(self) -> None:
        self.begin('autorun')
        entries = self.autoruns()
        exes = [self.command_exe(e['command']) for e in entries]
        self.check_signatures([x for x in exes if x.lower().endswith('.exe')])
        for entry, exe in zip(entries, exes):
            with self.lock:
                self.current = entry['command']
            cmd, name = entry['command'].lower(), entry['name']
            if re.search(r'powershell.*(-enc\b|-e\s|frombase64|downloadstring|iex\b)', cmd) or re.search(r'mshta\s+https?:', cmd):
                self.add('autorun', 'threat', f'Скрытая команда в автозагрузке: {name}',
                         'При каждом входе в Windows запускается зашифрованная команда — типичный приём вирусов.',
                         exe, 'autorun', entry=entry)
            elif exe.lower().endswith(('.vbs', '.js', '.jse', '.wsf', '.hta')):
                self.add('autorun', 'warn', f'Сценарий в автозагрузке: {name}',
                         'Скрипты в автозагрузке — частый способ закрепиться в системе. Если вы его не добавляли — отключите.',
                         exe, 'autorun', entry=entry)
            elif ':\\' in exe and not entry.get('file') and not os.path.exists(exe):
                self.add('autorun', 'warn', f'Автозапуск ведёт в пустоту: {name}',
                         'Программы уже нет, а запись осталась. Её безопасно отключить.', exe, 'autorun', entry=entry)
            elif exe.lower().endswith('.exe') and user_writable(exe) and exe in self.signatures and not self.signed(exe) \
                    and 'cloud hdr' not in exe.lower():
                self.add('autorun', 'warn', f'Неподписанная программа в автозагрузке: {name}',
                         f'Запускается при входе в Windows из {os.path.dirname(exe)}. Проверьте, знаете ли вы её.',
                         exe, 'autorun', entry=entry)
        self.step(0.8)
        self.finish('autorun', f'Записей автозапуска: {len(entries)}')

    def scan_phish(self) -> None:
        self.begin('phish')
        hosts = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'drivers' / 'etc' / 'hosts'
        with self.lock:
            self.current = str(hosts)
        try:
            lines = hosts.read_text(encoding='utf-8', errors='replace').splitlines()
        except OSError:
            lines = []
        for raw_line in lines:
            line = raw_line.split('#', 1)[0].strip()
            parts = line.split()
            if len(parts) < 2 or parts[0] in ('127.0.0.1', '0.0.0.0', '::1', '::'):
                continue
            hit = [n for n in parts[1:] if any(d in n.lower() for d in BIG_DOMAINS)]
            if hit:
                self.add('phish', 'threat', f'Подмена адреса: {hit[0]} → {parts[0]}',
                         'Файл hosts отправляет известный сайт на чужой сервер — так крадут пароли через поддельные страницы.',
                         str(hosts), 'hosts', line=raw_line.strip())
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
                def value(name):
                    try:
                        return winreg.QueryValueEx(key, name)[0]
                    except OSError:
                        return None
                pac, enabled, proxy = value('AutoConfigURL'), value('ProxyEnable'), value('ProxyServer')
            local = r'127\.0\.0\.1|localhost|::1'
            if pac and not re.search(local, str(pac)):
                self.add('phish', 'warn', 'Браузер настроен через сторонний сценарий прокси',
                         f'Адрес автонастройки: {pac}. Если вы его не ставили, так можно подменять сайты.', kind='info')
            if enabled and proxy and not re.search(local, str(proxy)):
                self.add('phish', 'warn', f'Включён внешний прокси: {proxy}',
                         'Трафик браузера идёт через этот сервер. Если это не ваш VPN — отключите в «Параметры → Сеть → Прокси».',
                         kind='info')
        except OSError:
            pass
        self.step(0.9)
        self.finish('phish', 'Файл hosts и настройки прокси проверены')

    def scan_net(self) -> None:
        self.begin('net')
        count, doubtful = 0, []
        try:
            connections = psutil.net_connections(kind='inet')
        except psutil.AccessDenied:
            connections = []
        for conn in connections:
            if conn.status != psutil.CONN_ESTABLISHED or not conn.raddr or not conn.pid:
                continue
            count += 1
            try:
                proc = psutil.Process(conn.pid)
                exe, name = proc.exe(), proc.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            with self.lock:
                self.current = f'{name} → {conn.raddr.ip}:{conn.raddr.port}'
            if conn.raddr.port in MINER_PORTS or in_temp(exe):
                doubtful.append((exe, name, conn))
        self.check_signatures([exe for exe, _, _ in doubtful])
        for exe, name, conn in doubtful:
            if self.signed(exe):
                continue
            if conn.raddr.port in MINER_PORTS:
                self.add('net', 'threat', f'{name} подключён к порту майнинг-пула {conn.raddr.port}',
                         f'Соединение с {conn.raddr.ip}:{conn.raddr.port} — такие порты используют пулы скрытой добычи.',
                         exe, 'process', pid=conn.pid)
            else:
                self.add('net', 'warn', f'{name} из временной папки выходит в интернет',
                         f'Соединение с {conn.raddr.ip}:{conn.raddr.port}, подписи у программы нет.',
                         exe, 'process', pid=conn.pid)
        self.step(0.98)
        self.finish('net', f'Активных соединений: {count}')

    def summarize(self) -> None:
        with self.lock:
            items = [i for c in self.categories.values() for i in c['items']]
        bad = [i for i in items if i['level'] == 'threat']
        warn = [i for i in items if i['level'] == 'warn']
        files = f'{self.files:,}'.replace(',', ' ')
        if bad:
            text = f'Нашёл угроз: {len(bad)}. Нажмите «В карантин» — файлы переедут в безопасное место, их можно будет вернуть.'
        elif warn:
            text = f'Угроз не нашёл, но есть {len(warn)} {"место" if len(warn) == 1 else "места" if len(warn) < 5 else "мест"}, на которые стоит взглянуть. Проверено файлов: {files}.'
        else:
            text = f'Компьютер чист — угроз не найдено. Проверено файлов: {files}.'
        with self.lock:
            self.summary = text
        log('защита: ' + text)


SCAN = Scanner()


@app.post('/hub/scan/start')
def scan_start():
    started = SCAN.start()
    return {'ok': True, 'started': started, **SCAN.snapshot()}


@app.get('/hub/scan/status')
def scan_status():
    return {'ok': True, **SCAN.snapshot(), 'last': STATE.get('lastScan')}


def find_item(item_id: str) -> dict | None:
    with SCAN.lock:
        for category in SCAN.categories.values():
            for item in category['items']:
                if item['id'] == item_id:
                    return item
    return None


def read_manifest() -> list:
    try:
        return json.loads((QUARANTINE / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []


def write_manifest(manifest: list) -> None:
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    (QUARANTINE / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')


@app.post('/hub/scan/fix')
def scan_fix(body: dict = Depends(body_of)):
    """Обезвредить находку — только по нажатию человека и только обратимо."""
    item = find_item(str(body.get('id')))
    if not item:
        return {'ok': False, 'error': 'Находка не найдена — запустите проверку заново.'}
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    record = {'id': item['id'], 'title': item['title'], 'ts': time.time()}
    done = []

    if item.get('pid'):
        try:
            psutil.Process(item['pid']).kill()
            done.append('процесс остановлен')
            time.sleep(0.5)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    entry = item.get('entry') or {}
    if item['kind'] == 'autorun' and not entry.get('file'):
        hive = winreg.HKEY_CURRENT_USER if entry.get('hive') == 'HKCU' else winreg.HKEY_LOCAL_MACHINE
        try:
            with winreg.OpenKey(hive, entry['sub'], 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, entry['name'])
            record['registry'] = entry
            done.append('запись автозапуска отключена')
        except OSError as error:
            return {'ok': False, 'error': f'Не удалось отключить автозапуск: {error}'}
    elif item['kind'] == 'hosts':
        hosts = Path(item['path'])
        backup = QUARANTINE / f'hosts-{int(time.time())}.bak'
        shutil.copy2(hosts, backup)
        lines = hosts.read_text(encoding='utf-8', errors='replace').splitlines()
        hosts.write_text('\n'.join('# ' + l if l.strip() == item.get('line') else l for l in lines) + '\n', encoding='utf-8')
        record['hostsBackup'] = str(backup)
        done.append('подмена в hosts отключена')
    else:
        source = Path(entry.get('file') or item.get('path') or '')
        if source.is_file():
            target = QUARANTINE / f"{item['id']}_{source.name}.quar"
            for _ in range(6):
                try:
                    shutil.move(str(source), target)
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                return {'ok': False, 'error': 'Файл занят и не перемещается. Перезагрузите компьютер и повторите.'}
            record.update({'from': str(source), 'to': str(target)})
            done.append('файл перемещён в карантин')

    if not done:
        return {'ok': False, 'error': 'Здесь нечего обезвреживать автоматически — следуйте совету в описании.'}
    manifest = read_manifest()
    manifest.append(record)
    write_manifest(manifest)
    with SCAN.lock:
        item['fixed'] = True
    log(f"защита: {item['title']} — {', '.join(done)}")
    return {'ok': True, 'done': ', '.join(done).capitalize() + '.'}


@app.get('/hub/scan/quarantine')
def quarantine_list():
    return {'ok': True, 'items': read_manifest(), 'folder': str(QUARANTINE)}


@app.post('/hub/scan/restore')
def quarantine_restore(body: dict = Depends(body_of)):
    wanted = body.get('id')
    manifest = read_manifest()
    for record in manifest:
        if record['id'] != wanted:
            continue
        if record.get('to') and Path(record['to']).exists():
            shutil.move(record['to'], record['from'])
        if record.get('registry'):
            entry = record['registry']
            hive = winreg.HKEY_CURRENT_USER if entry['hive'] == 'HKCU' else winreg.HKEY_LOCAL_MACHINE
            with winreg.CreateKey(hive, entry['sub']) as key:
                winreg.SetValueEx(key, entry['name'], 0, winreg.REG_SZ, entry['command'])
        if record.get('hostsBackup'):
            shutil.copy2(record['hostsBackup'], Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'drivers' / 'etc' / 'hosts')
        manifest.remove(record)
        write_manifest(manifest)
        return {'ok': True}
    return {'ok': False, 'error': 'Такой записи в карантине нет.'}


# ============================================= медиа, буфер, напоминания ===
#
# Группа 2 интеграции с ПК. Сама логика — в cloudhdr_desk.py; здесь только
# маршруты. Напоминания живут в этой службе, а не в окне: хаб работает и при
# закрытом окне, поэтому уведомление придёт в любом случае.

import cloudhdr_desk as desk  # noqa: E402 — после объявления журнала и данных

desk.register_toast_app(ROOT.parent / 'public' / 'icons' / 'icon-256.png')
REMINDERS = desk.Reminders(DATA / 'reminders.json', log)


@app.post('/hub/media')
def media_action(body: dict = Depends(body_of)):
    action = str(body.get('action') or desk.match_media(str(body.get('text') or '')))
    if not action:
        return {'ok': False, 'known': False}
    return desk.media_answer(action, str(body.get('text') or ''))


@app.post('/hub/clip')
def clip_action(body: dict = Depends(body_of)):
    if 'set' in body:
        return {'ok': desk.clip_set(str(body.get('set') or ''))}
    action = str(body.get('action') or desk.match_clip(str(body.get('text') or '')))
    if not action:
        return {'ok': False, 'known': False}
    try:
        return desk.clip_answer(action, TRANSLATOR.translate if TRANSLATOR.state != 'нет модели' else None)
    except Exception as error:  # noqa: BLE001 — переводчик может упасть на памяти
        log(f'буфер: {error}')
        return {'ok': False, 'known': True, 'say': f'Не получилось: {error}'}


@app.post('/hub/remind')
def remind_action(body: dict = Depends(body_of)):
    return desk.remind_answer(str(body.get('text') or ''), REMINDERS)


@app.get('/hub/remind/fired')
def remind_fired():
    # Окно забирает сработавшие, чтобы показать их в чате и произнести вслух
    return {'ok': True, 'items': REMINDERS.take_fired()}


# ============================================ программы, окна, автозагрузка ===
# Группа 3 (cloudhdr_apps.py). Обычные функции, не async: winget и окна —
# блокирующая работа, FastAPI выполняет такие обработчики в пуле потоков.

@app.post('/hub/apps')
def apps_action(body: dict = Depends(body_of)):
    try:
        return apps.apps_answer(str(body.get('text') or ''))
    except Exception as error:  # noqa: BLE001 — окно не должно видеть трассировку
        log(f'программы: {error!r}')
        return {'ok': False, 'known': True, 'say': f'Не получилось: {error}'}


@app.post('/hub/ctl')
def control_action(body: dict = Depends(body_of)):
    """Группа 5: настройки Windows, звук, не беспокоить, режимы, сеть, очистка, файлы, печать."""
    try:
        answer = control.control_answer(str(body.get('text') or ''))
        if answer.get('open'):
            os.startfile(answer.pop('open'))  # noqa: S606 — только ms-availablenetworks:
        return answer
    except Exception as error:  # noqa: BLE001 — окно не должно видеть трассировку
        log(f'управление: {error!r}')
        return {'ok': False, 'known': True, 'say': f'Не получилось: {error}'}


@app.post('/hub/ctl/run')
def control_run(body: dict = Depends(body_of)):
    """Нажата кнопка: выбрать устройство звука, удалить мусор, отправить на печать."""
    try:
        result = control.run_confirmed(str(body.get('kind') or ''), body)
        log(f"управление: {body.get('kind')} — {result.get('say', '')}")
        return result
    except Exception as error:  # noqa: BLE001
        log(f'управление: {error!r}')
        return {'ok': False, 'say': f'Не получилось: {error}'}


@app.post('/hub/ctl/reveal')
def control_reveal(body: dict = Depends(body_of)):
    """Показать готовый файл (pdf, сжатые фото) в Проводнике."""
    path = Path(str(body.get('path') or ''))
    if not path.is_file():
        return {'ok': False}
    subprocess.Popen(['explorer.exe', '/select,', str(path)], creationflags=NO_WINDOW)
    return {'ok': True}


threading.Thread(target=control.restore_quiet_on_start, daemon=True).start()


@app.post('/hub/apps/run')
def apps_run(body: dict = Depends(body_of)):
    """Нажата кнопка в карточке: установить, обновить всё, удалить, закрыть зависшие."""
    package = body.get('package') if isinstance(body.get('package'), dict) else None
    if package and not re.fullmatch(r'[\w.\-+]{2,120}', str(package.get('id') or '')):
        return {'ok': False, 'error': 'Непонятный идентификатор пакета.'}
    result = apps.run_confirmed(str(body.get('kind') or ''), package)
    if result.get('ok'):
        log(f"программы: {body.get('kind')} {package.get('id') if package else ''}".strip())
    return result


# ================================================================== речь ===

# Обращение по имени. Whisper записывает «Клауд» по-разному, и все варианты
# равноправны: человек не обязан говорить так, чтобы понравиться модели.
#
# Живой журнал показал, как это звучит на деле: хозяин зовёт его «Cloud HDR»,
# а Whisper пишет «HDR», «Клау Тождяр», «Клодастяр», «ждер». Всё это — одно
# и то же обращение, в том числе вторая половина («HDR») сама по себе.
_HDR = r'(?:hdr|эйч\W*ди\W*ар|эйчдиар|аш\W*ди\W*эр|\w*дяр|\w*дар|\w*стяр|ждер|эйчдер)'
WAKE_RE = re.compile(r'^\W*(?:эй\W*|окей\W*|слушай\W*)?(?:(?:клау\w*|клаут\w*|клод\w*|клоуд\w*|cloud\w*|claude|облачко)(?:\W*' + _HDR + r')?|'
                     + _HDR + r')(?![а-яёa-z])[\s,.!?:—-]*', re.I)

# Что Whisper слышит вместо повелительного наклонения: «Открою Chrome»,
# «от Крою хром». Команда «открой», конечно.
MISHEARD = [(re.compile(r'^(?:от\s*крою|открою|откроем|открыв)(?![а-яё])', re.I), 'открой'),
            (re.compile(r'^(?:за\s*крою|закрою|закроем)(?![а-яё])', re.I), 'закрой'),
            (re.compile(r'^(?:включу|включим)(?![а-яё])', re.I), 'включи'),
            (re.compile(r'^(?:выключу|выключим)(?![а-яё])', re.I), 'выключи')]

# Начало команды. Такую фразу, сказанную близко к микрофону, уши выполняют и
# без обращения по имени: «открой папку Секрет» — явно просьба к компьютеру.
IMPERATIVE_RE = re.compile(
    r'^(?:пожалуйста\s+)?(?:открой|закрой|запусти|включи|выключи|найди|покажи|сверни|разверни|перемести|перенеси|скопируй|'
    r'переименуй|создай|установи|обнови|удали|поставь|сделай|переключись|перезапусти|перезагрузи|проверь|разбери|напомни|'
    r'засеки|переведи|громче|тише|пауза|продолжи|следующ\w*|предыдущ\w*|заблокируй|очисти|верни|распакуй|сожми)(?![а-яё])', re.I)


def normalize_command(text: str) -> str:
    value = text.strip().strip(' ,.!?«»"')
    for pattern, word in MISHEARD:
        value = pattern.sub(word, value, count=1)
    return value


@app.post('/hub/asr')
def asr(body: dict = Depends(body_of)):
    """{pcm: base64 int16 LE, rate} → {text, wake, command}."""
    import base64
    import numpy as np
    try:
        raw = base64.b64decode(str(body.get('pcm') or ''))
    except ValueError:
        return {'ok': False, 'error': 'Звук не разобрался.'}
    if len(raw) < 3200:                     # меньше десятой доли секунды
        return {'ok': True, 'text': '', 'wake': False, 'command': ''}
    if len(raw) > 16000 * 2 * 30:
        return {'ok': False, 'error': 'Фраза длиннее 30 секунд — скажите короче.'}
    # Сверка озвучки (cloudhdr_voice): только если распознавание уже живёт на
    # карте. Иначе загрузка заняла бы секунды, а переезд на карту выгнал бы
    # голос и модель разговора — фраза пусть звучит без сверки.
    if body.get('check') and not (LISTENER.model is not None and LISTENER.resident):
        return {'ok': False, 'skipped': True}
    samples = np.frombuffer(raw[: len(raw) // 2 * 2], dtype='<i2').astype(np.float32) / 32768.0
    try:
        text = LISTENER.transcribe(samples, int(body.get('rate') or 16000))
    except Exception as error:  # noqa: BLE001
        log(f'речь: {error!r}')
        return {'ok': False, 'error': str(error)}
    wake = WAKE_RE.match(text)
    command = normalize_command(text[wake.end():] if wake else text)
    # «HDR» следом за «Клауд» — всё ещё обращение, а не команда
    if wake and WAKE_RE.fullmatch(command):
        command = ''
    return {'ok': True, 'text': text, 'wake': bool(wake), 'command': command if wake else '',
            'plain': command, 'imperative': bool(command and IMPERATIVE_RE.match(command))}


@app.post('/hub/asr/warm')
def asr_warm():
    """Начать загрузку заранее: первая загрузка — секунд двадцать, и пусть
    они пройдут, пока человек ещё договаривает, а не после."""
    def warm():
        try:
            with LISTENER.lock:
                LISTENER.load()
                LISTENER.used = time.time()
        except Exception as error:  # noqa: BLE001
            log(f'речь: прогрев не удался — {error!r}')
    if LISTENER.model is None and LISTENER.state != 'нет модели':
        threading.Thread(target=warm, daemon=True).start()
    return {'ok': True, 'state': LISTENER.state}


@app.post('/hub/asr/park')
def asr_park():
    """Очередь видеопамяти: распознавание и так уезжает в ОЗУ после каждой
    фразы, поэтому здесь нечего освобождать — только подтвердить."""
    return {'ok': True}


@app.get('/hub/asr/state')
def asr_state():
    return {'ok': True, 'state': LISTENER.state, 'device': LISTENER.device}


# ======================================================= файлы (группа 4) ===
# cloudhdr_fileops.py. Удаление — только в корзину и только кнопкой (/hub/fs/act).

@app.post('/hub/fs')
def fs_action(body: dict = Depends(body_of)):
    try:
        return fileops.files_answer(str(body.get('text') or ''))
    except Exception as error:  # noqa: BLE001
        log(f'файлы: {error!r}')
        return {'ok': False, 'known': True, 'say': f'Не получилось: {error}'}


@app.post('/hub/fs/act')
def fs_act(body: dict = Depends(body_of)):
    try:
        result = fileops.act(body)
    except OSError as error:
        result = {'ok': False, 'say': f'Windows не дала: {error.strerror or error}'}
    if result.get('ok'):
        log(f"файлы: {body.get('kind')} {body.get('path') or len(body.get('paths') or [])}")
    return result


@app.get('/hub/apps/job')
def apps_job():
    return {'ok': True, **apps.JOB.snapshot()}


if __name__ == '__main__':
    import sys
    import uvicorn
    # Под pythonw.exe консоли нет и sys.stdout/stderr равны None — журнал
    # uvicorn споткнулся бы о первую же запись. Пишем в файл рядом с hub.log.
    if sys.stdout is None or sys.stderr is None:
        sink = open(DATA / 'hub-console.log', 'a', encoding='utf-8', buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or sink
        sys.stderr = sys.stderr or sink
    log(f'Cloud HDR Hub: http://{HOST}:{PORT}')
    # Переводчик грузится сразу, в фоне: иначе первое Ctrl+Alt+T ждало бы
    # загрузку весов — до полуминуты вместо долей секунды.
    threading.Thread(target=lambda: TRANSLATOR.translate('Ready.'), daemon=True).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level='warning', access_log=False)
